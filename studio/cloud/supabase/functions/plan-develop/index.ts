// POST {idea_id, request?} -> {job_id}            creates a develop job and runs it
// POST {job_id}            -> {job_id} | {job_id, already: true}   runs a queued develop job
// Claude refines one idea's plan and storyboard (plan.develop): filmed shots and shots with
// footage are kept, unfilmed shots it didn't keep are deleted, new ones inserted, positions 1..n.
// A carousel (format 'carousel') gets its slides written instead ("Write slides"): a cover,
// the points and the ask, each with a picture the team picked or a still from a clip Studio has.
import { ask } from "../_shared/claude.ts";
import { check, db, requireMember } from "../_shared/db.ts";
import { background, HttpError, idParam, json, readJson, serve } from "../_shared/http.ts";
import { expireStale, JobError, type JobRow, loadContext, runJob, WORK_BUDGET_MS } from "../_shared/jobs.ts";
import {
  type DevelopIdea,
  developPrompt,
  ideaFields,
  planShots,
  type ShotRow,
  type ShotState,
  shotState,
  text,
} from "../_shared/plan.ts";
import { DEVELOP_SCHEMA, DEVELOP_SYSTEM, renderSystem, SLIDES_SCHEMA, SLIDES_SYSTEM } from "../_shared/prompts.ts";
import { type ClipRow, planSlides, slidesPrompt, storedSlides } from "../_shared/slides.ts";

// Clips a carousel may take stills from: the project's own, then recent ones from other projects.
const PROJECT_CLIPS = 30, OTHER_CLIPS = 20;

const MAX_REQUEST = 2000;
const OPEN_STATES = ["queued", "running"];

serve(["POST"], async (req) => {
  const started = Date.now();
  const caller = await requireMember(req);
  const body = await readJson(req);
  let job: JobRow;

  if (body.job_id !== undefined && body.job_id !== null) {
    const jobId = idParam(body.job_id, "job_id");
    const found = check(
      await db().from("jobs").select("id, kind, runner, idea_id, state").eq("id", jobId).maybeSingle(),
      "read job",
    ) as Pick<JobRow, "id" | "kind" | "runner" | "idea_id" | "state"> | null;
    if (!found || found.kind !== "develop" || found.runner !== "cloud" || !found.idea_id) {
      throw new HttpError(404, "No such storyboard job");
    }
    await expireStale({ ideaId: found.idea_id });
    const claimed = check(
      await db().from("jobs").update({ state: "running", started_at: new Date().toISOString(), error: null })
        .eq("id", jobId).eq("state", "queued").select("*"),
      "claim job",
    ) as JobRow[];
    if (!claimed.length) return json({ job_id: jobId, already: true });
    job = claimed[0];
  } else {
    const ideaId = idParam(body.idea_id, "idea_id");
    const request = typeof body.request === "string" ? body.request.trim() : "";
    if (request.length > MAX_REQUEST) throw new HttpError(400, `Keep the request under ${MAX_REQUEST} characters`);
    const idea = check(
      await db().from("ideas").select("id, project_id").eq("id", ideaId).maybeSingle(),
      "read idea",
    ) as { id: number; project_id: number } | null;
    if (!idea) throw new HttpError(404, "Idea not found");
    await expireStale({ ideaId });
    const open = check(
      await db().from("jobs").select("id").eq("idea_id", ideaId).eq("kind", "develop").eq("runner", "cloud")
        .in("state", OPEN_STATES).limit(1),
      "read jobs",
    ) as { id: number }[];
    if (open.length) {
      throw new HttpError(409, "Claude is already working on this idea", { job_id: open[0].id });
    }
    job = check(
      await db().from("jobs").insert({
        kind: "develop",
        runner: "cloud",
        project_id: idea.project_id,
        idea_id: ideaId,
        payload: { request },
        state: "running",
        started_at: new Date().toISOString(),
        requested_by: caller.id,
      }).select("*").single(),
      "queue job",
    ) as JobRow;
  }

  const request = typeof job.payload?.request === "string" ? job.payload.request : "";
  const ideaId = job.idea_id!;
  background(runJob(job.id, () => develop(ideaId, request, started + WORK_BUDGET_MS)));
  return json({ job_id: job.id });
});

type StatefulShot = ShotRow & { state: ShotState; position: number };

/** The idea's shots in order, each with its state (needed / filmed / got / retake). */
async function shotsWithState(ideaId: number): Promise<StatefulShot[]> {
  const shots = check(
    await db().from("shots").select("id, position, kind, framing, camera, description, line, on_screen, filmed_at")
      .eq("idea_id", ideaId).order("position").order("id"),
    "read shots",
  ) as (ShotRow & { position: number })[];
  if (!shots.length) return [];
  const takes = check(
    await db().from("takes").select("shot_id, clips(verdict)").in("shot_id", shots.map((s) => s.id)),
    "read takes",
  ) as unknown as { shot_id: number; clips: { verdict: string | null } | null }[];
  return shots.map((s) => ({
    ...s,
    state: shotState(s, takes.filter((t) => t.shot_id === s.id).map((t) => t.clips?.verdict ?? null)),
  }));
}

async function develop(ideaId: number, request: string, deadline: number) {
  const idea = check(
    await db().from("ideas").select(
      "id, project_id, arc_id, title, brand, format, post_type, est_seconds, hook, purpose, cta, caption, music",
    ).eq("id", ideaId).maybeSingle(),
    "read idea",
  ) as (DevelopIdea & { id: number; project_id: number }) | null;
  if (!idea) throw new JobError("The idea was deleted");
  if (idea.format === "carousel") return writeSlides(idea, request, deadline);
  const [projectRes, commentsRes, shots, context] = await Promise.all([
    db().from("projects").select("name, brief").eq("id", idea.project_id).maybeSingle(),
    db().from("comments").select("author_name, body").eq("idea_id", ideaId).order("id", { ascending: false })
      .limit(20),
    shotsWithState(ideaId),
    loadContext(),
  ]);
  const project = check(projectRes, "read project") as { name: string; brief: string } | null;
  const comments = check(commentsRes, "read comments") as { author_name: string; body: string }[];

  const { result, usage } = await ask<{ idea?: unknown; changes?: unknown }>({
    system: renderSystem(DEVELOP_SYSTEM, context.brands, context.playbook, context.contentPlan),
    user: developPrompt(project, idea, shots, comments, request),
    schema: DEVELOP_SCHEMA,
    effort: "high",
    maxTokens: 32000,
    cutOffMessage: "Claude's answer was cut off; try a shorter request",
    deadline,
  });

  const fields = ideaFields(result.idea, { brand: idea.brand, format: idea.format, title: idea.title });
  // A Reel stays a Reel (its shots are planned): a carousel is made as one from the start.
  if (fields.format === "carousel") fields.format = idea.format as typeof fields.format;
  const { music: newMusic, post_type: newType, ...ideaUpdate } = fields;
  // The kind is filled in when it's empty; one already set stays (it may be someone's choice).
  const kind = !idea.post_type && newType ? { post_type: newType } : {};
  // Keep the music shortlist / chosen track; only the plan (mood, tempo, search) changes.
  const music = { ...(idea.music ?? {}), ...newMusic };
  const updated = check(
    await db().from("ideas").update({ ...ideaUpdate, ...kind, music }).eq("id", ideaId).select("id"),
    "update idea",
  ) as { id: number }[];
  if (!updated.length) throw new JobError("The idea was deleted");

  // Shots may have been ticked off while Claude worked: plan against their state now.
  const current = await shotsWithState(ideaId);
  const proposed = (result.idea as { shots?: unknown } | undefined)?.shots;
  const plan = planShots(current, proposed);
  if (plan.delete.length) check(await db().from("shots").delete().in("id", plan.delete), "remove shots");
  for (const u of plan.update) {
    check(
      await db().from("shots").update({ ...u.fields, position: u.position }).eq("id", u.id).eq("idea_id", ideaId),
      "update shot",
    );
  }
  for (const m of plan.move) {
    check(await db().from("shots").update({ position: m.position }).eq("id", m.id), "move shot");
  }
  if (plan.insert.length) {
    check(
      await db().from("shots").insert(plan.insert.map((s) => ({ idea_id: ideaId, ...s.fields, position: s.position }))),
      "add shots",
    );
  }
  return {
    changes: text(result.changes, 1000),
    shots: plan.update.length + plan.insert.length + plan.move.length,
    kept: plan.update.map((u) => u.id),
    usage,
  };
}

/** The usable clips a carousel may take stills from (no retakes). */
async function carouselClips(projectId: number): Promise<ClipRow[]> {
  const cols = "id, title, duration, thumb_path, project_id, verdict, usable";
  const [own, other] = await Promise.all([
    db().from("clips").select(cols).eq("project_id", projectId).order("recorded_at", {
      ascending: false,
      nullsFirst: false,
    })
      .limit(PROJECT_CLIPS * 2),
    db().from("clips").select(cols).or(`project_id.is.null,project_id.neq.${projectId}`)
      .order("recorded_at", { ascending: false, nullsFirst: false }).limit(OTHER_CLIPS * 2),
  ]);
  const fine = (rows: ClipRow[]) => rows.filter((c) => c.verdict !== "retake" && c.usable !== false);
  return [
    ...fine(check(own, "read clips") as ClipRow[]).slice(0, PROJECT_CLIPS),
    ...fine(check(other, "read clips") as ClipRow[]).slice(0, OTHER_CLIPS),
  ];
}

/** "Write slides": a carousel's cover, points and ask (plus its caption), in Bryan's voice. */
async function writeSlides(
  idea: DevelopIdea & { id: number; project_id: number },
  request: string,
  deadline: number,
) {
  const [projectRes, commentsRes, slidesRes, clips, context, studioRes] = await Promise.all([
    db().from("projects").select("name, brief, standing").eq("id", idea.project_id).maybeSingle(),
    db().from("comments").select("author_name, body").eq("idea_id", idea.id).order("id", { ascending: false })
      .limit(20),
    db().from("ideas").select("slides, status, production").eq("id", idea.id).maybeSingle(),
    carouselClips(idea.project_id),
    loadContext(),
    db().from("laptop_status").select("info").eq("id", 1).maybeSingle(),
  ]);
  // Concept pictures: only when the Studio machine has the image key (laptop_status.info.concepts).
  const studio = (studioRes.data ?? null) as { info?: { concepts?: { on?: boolean } } } | null;
  const concepts = studio?.info?.concepts?.on === true;
  const project = check(projectRes, "read project") as { name: string; brief: string; standing: string | null } | null;
  const comments = check(commentsRes, "read comments") as { author_name: string; body: string }[];
  const row = check(slidesRes, "read slides") as
    | { slides: unknown; status: string; production: unknown }
    | null;
  // Once made, the draft's slides change in Review (Send back with notes), not here.
  if (row && (row.status === "made" || row.production)) {
    throw new JobError("This carousel is in Review now: send the draft back with notes there to change its slides");
  }
  const current = storedSlides(row?.slides);

  const { result, usage } = await ask({
    system: renderSystem(SLIDES_SYSTEM, context.brands, context.playbook, context.contentPlan),
    user: slidesPrompt(project, idea, current, clips, comments, request, idea.project_id, concepts),
    schema: SLIDES_SCHEMA,
    effort: "high",
    maxTokens: 24000,
    cutOffMessage: "Claude's answer was cut off; try a shorter request",
    deadline,
  });
  const plan = planSlides(result, current, clips, idea.title, undefined, { concepts, postType: idea.post_type });
  if (plan.slides.length < 2) throw new JobError("Claude didn't write any slides; try again");
  const { post_type: newType, ...fields } = plan.idea;
  // The kind is filled in when it's empty; one already set stays (it may be someone's choice).
  const kind = !idea.post_type && newType ? { post_type: newType } : {};
  const updated = check(
    await db().from("ideas").update({ ...fields, ...kind, slides: plan.slides }).eq("id", idea.id).select("id"),
    "save slides",
  ) as { id: number }[];
  if (!updated.length) throw new JobError("The idea was deleted");
  return { changes: plan.changes, slides: plan.slides.length, usage };
}
