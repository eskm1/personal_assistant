// POST {project_id, text} -> {job_id}
// Claude sorts the team's brain-dump into arcs and ideas (without shots), which are inserted on
// the board; then one queued `develop` job per new idea, which the planner starts (plan-develop
// {job_id}) so each storyboard gets its own function run.
import { ask } from "../_shared/claude.ts";
import { type Caller, check, db, requireMember } from "../_shared/db.ts";
import { background, HttpError, idParam, json, readJson, serve } from "../_shared/http.ts";
import { expireStale, loadContext, runJob, WORK_BUDGET_MS } from "../_shared/jobs.ts";
import { boardText, organisePrompt, planOrganise } from "../_shared/plan.ts";
import { ORGANISE_NO_SHOTS_NOTE, ORGANISE_NO_SHOTS_SCHEMA, ORGANISE_SYSTEM, renderSystem } from "../_shared/prompts.ts";

const MAX_TEXT = 20000;

interface Project {
  id: number;
  name: string;
  brand: string;
  brief: string;
}

serve(["POST"], async (req) => {
  const started = Date.now();
  const caller = await requireMember(req);
  const body = await readJson(req);
  const projectId = idParam(body.project_id, "project_id");
  const dump = typeof body.text === "string" ? body.text.trim() : "";
  if (!dump) throw new HttpError(400, "Write some ideas first");
  if (dump.length > MAX_TEXT) throw new HttpError(400, `That's too long (${MAX_TEXT} characters at most)`);

  const project = check(
    await db().from("projects").select("id, name, brand, brief").eq("id", projectId).maybeSingle(),
    "read project",
  ) as Project | null;
  if (!project) throw new HttpError(404, "Project not found");

  await expireStale({ projectId });
  const job = check(
    await db().from("jobs").insert({
      kind: "organise",
      runner: "cloud",
      project_id: projectId,
      payload: { text: dump },
      state: "running",
      started_at: new Date().toISOString(),
      requested_by: caller.id,
    }).select("id").single(),
    "queue job",
  ) as { id: number };

  background(runJob(job.id, () => organise(project, dump, caller, started + WORK_BUDGET_MS)));
  return json({ job_id: job.id });
});

async function organise(project: Project, dump: string, caller: Caller, deadline: number) {
  const [arcsRes, ideasRes, context] = await Promise.all([
    db().from("arcs").select("id, title, purpose, position").eq("project_id", project.id)
      .order("position").order("id"),
    db().from("ideas").select("arc_id, title, position, status").eq("project_id", project.id)
      .order("position").order("id"),
    loadContext(),
  ]);
  const arcs = check(arcsRes, "read arcs") as { id: number; title: string; purpose: string; position: number }[];
  const ideas = check(ideasRes, "read ideas") as {
    arc_id: number | null;
    title: string;
    position: number;
    status: string;
  }[];

  const board = boardText(arcs, ideas.filter((i) => i.status !== "dismissed"));
  const { result, usage } = await ask({
    system: renderSystem(ORGANISE_SYSTEM + ORGANISE_NO_SHOTS_NOTE, context.brands, context.playbook),
    user: organisePrompt(project, board, dump),
    schema: ORGANISE_NO_SHOTS_SCHEMA,
    effort: "medium",
    maxTokens: 32000,
    cutOffMessage: "Claude's answer was cut off; try fewer ideas at once",
    deadline,
  });
  const plan = planOrganise(result, arcs.map((a) => a.id), project.brand);

  // New arcs go after the existing ones.
  const arcIds = new Map<string, number>();
  if (plan.arcs.length) {
    const base = Math.max(0, ...arcs.map((a) => a.position));
    const rows = check(
      await db().from("arcs").insert(plan.arcs.map((a, i) => ({
        project_id: project.id,
        title: a.title,
        purpose: a.purpose,
        position: base + i + 1,
      }))).select("id, position"),
      "add arcs",
    ) as { id: number; position: number }[];
    const byPosition = new Map(rows.map((r) => [r.position, r.id]));
    plan.arcs.forEach((a, i) => arcIds.set(a.key, byPosition.get(base + i + 1)!));
  }

  // Ideas go at the end of their arc (or of Unsorted).
  const nextPos = new Map<number | null, number>();
  for (const i of ideas) nextPos.set(i.arc_id, Math.max(nextPos.get(i.arc_id) ?? 0, i.position));
  const ideaRows = plan.ideas.map(({ arc, fields }) => {
    const arcId = arc === null ? null : "existing" in arc ? arc.existing : arcIds.get(arc.key) ?? null;
    const position = (nextPos.get(arcId) ?? 0) + 1;
    nextPos.set(arcId, position);
    return {
      project_id: project.id,
      arc_id: arcId,
      ...fields,
      // The service role bypasses the owner-guard trigger, so set what it would.
      decision: "proposed",
      status: "new",
      production: null,
      created_by: caller.id,
      position,
    };
  });
  let ideaIds: number[] = [];
  let developJobIds: number[] = [];
  if (ideaRows.length) {
    const inserted = check(await db().from("ideas").insert(ideaRows).select("id"), "add ideas") as { id: number }[];
    ideaIds = inserted.map((r) => r.id);
    const jobs = check(
      await db().from("jobs").insert(ideaIds.map((ideaId) => ({
        kind: "develop",
        runner: "cloud",
        project_id: project.id,
        idea_id: ideaId,
        payload: { request: "" },
        state: "queued",
        requested_by: caller.id,
      }))).select("id"),
      "queue storyboards",
    ) as { id: number }[];
    developJobIds = jobs.map((j) => j.id);
  }
  return {
    ideas: ideaIds.length,
    arcs: plan.arcs.length,
    notes: plan.notes,
    idea_ids: ideaIds,
    develop_job_ids: developJobIds,
    usage,
  };
}
