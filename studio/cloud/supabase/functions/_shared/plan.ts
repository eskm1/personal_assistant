// The board logic of studio/plan.py (organise / develop), as pure functions over rows,
// so it's testable without a database. Model output is never trusted: everything is
// clamped to the schema's enums and sensible lengths here.
import { BRANDS, CAMERAS, FORMATS, FRAMINGS, KINDS, POST_TYPES, SHOT_KINDS } from "./prompts.ts";

export type Brand = typeof BRANDS[number];
export type Format = typeof FORMATS[number];
export type PostType = typeof POST_TYPES[number];

export interface MusicPlan {
  mood?: string;
  tempo_bpm?: number;
  search?: string;
}

export interface HookOption {
  text: string;
  formula: string;
}

export interface IdeaFields {
  title: string;
  brand: Brand;
  post_type: PostType | null;
  format: Format;
  hook: string;
  /** Claude's three hooks (unscored: the Studio machine scores them and may swap the hook). */
  hook_options: HookOption[];
  purpose: string;
  cta: string;
  caption: string;
  est_seconds: number;
  music: MusicPlan;
}

export interface ShotFields {
  kind: string | null;
  framing: string | null;
  camera: string | null;
  description: string;
  line: string;
  on_screen: string;
  seconds: number | null;
  why: string;
}

export interface ShotRow extends Partial<ShotFields> {
  id: number;
  filmed_at?: string | null;
}

export type ShotState = "needed" | "filmed" | "got" | "retake";

const asObject = (v: unknown): Record<string, unknown> =>
  v && typeof v === "object" && !Array.isArray(v) ? v as Record<string, unknown> : {};

/** A trimmed string of at most `max` characters ("" for anything that isn't a string). */
export function text(v: unknown, max: number): string {
  return typeof v === "string" ? v.trim().slice(0, max).trim() : "";
}

function num(v: unknown, min: number, max: number): number | null {
  return typeof v === "number" && Number.isFinite(v) ? Math.min(max, Math.max(min, v)) : null;
}

function oneOf<T extends string>(v: unknown, options: readonly T[]): T | null {
  return typeof v === "string" && (options as readonly string[]).includes(v) ? v as T : null;
}

export function musicPlan(v: unknown): MusicPlan {
  const m = asObject(v);
  const out: MusicPlan = { mood: text(m.mood, 200), search: text(m.search, 200) };
  const bpm = num(m.tempo_bpm, 40, 220);
  if (bpm !== null) out.tempo_bpm = Math.round(bpm);
  return out;
}

/** Like plan.add_idea: defaults from the project (or the idea as it is, when refining). */
export function ideaFields(v: unknown, defaults: { brand: string; format?: string; title?: string }): IdeaFields {
  const i = asObject(v);
  return {
    title: text(i.title, 300) || defaults.title || "Untitled idea",
    brand: oneOf(i.brand, BRANDS) ?? oneOf(defaults.brand, BRANDS) ?? "urban_makers",
    post_type: oneOf(i.post_type, POST_TYPES),
    format: oneOf(i.format, FORMATS) ?? oneOf(defaults.format, FORMATS) ?? "talking_head",
    hook: text(i.hook, 1000),
    hook_options: hookOptions(i.hook_options),
    purpose: text(i.purpose, 2000),
    cta: text(i.cta, 500),
    caption: text(i.caption, 4000),
    est_seconds: Math.round(num(i.est_seconds, 5, 600) ?? 30),
    music: musicPlan(i.music),
  };
}

/** At most three hooks with words, each {text, formula} (like wording.pick_hook, before scoring). */
export function hookOptions(v: unknown): HookOption[] {
  const out: HookOption[] = [];
  for (const raw of Array.isArray(v) ? v : []) {
    const o = asObject(raw);
    const t = text(o.text, 300);
    if (!t || out.some((x) => x.text.toLowerCase() === t.toLowerCase())) continue;
    out.push({ text: t, formula: text(o.formula, 80) });
    if (out.length === 3) break;
  }
  return out;
}

/** A write that names a column the database doesn't have yet (before `cloud_setup.py --update`). */
export function missingColumn(error: unknown, column: string): boolean {
  const e = asObject(error);
  const msg = `${e.message ?? ""} ${e.details ?? ""} ${e.hint ?? ""}`;
  return (e.code === "PGRST204" || e.code === "42703") && msg.includes(column);
}

/** Like plan.add_shot. */
export function shotFields(v: unknown): ShotFields {
  const s = asObject(v);
  const seconds = num(s.seconds, 0, 600);
  return {
    kind: oneOf(s.kind, SHOT_KINDS),
    framing: oneOf(s.framing, FRAMINGS),
    camera: oneOf(s.camera, Object.keys(CAMERAS)),
    description: text(s.description, 1000) || "New shot",
    line: text(s.line, 1000),
    on_screen: text(s.on_screen, 300),
    seconds: seconds === null ? null : Math.round(seconds * 10) / 10,
    why: text(s.why, 500),
  };
}

/** plan.shot_state: needed | filmed (ticked, footage not in yet) | got (a usable take) | retake. */
export function shotState(shot: { filmed_at?: string | null }, takeVerdicts: (string | null)[]): ShotState {
  if (takeVerdicts.some((v) => v !== "retake")) return "got";
  if (takeVerdicts.length) return "retake";
  return shot.filmed_at ? "filmed" : "needed";
}

// ── Organise ──────────────────────────────────────────────────────────────────

export interface BoardArc {
  id: number;
  title: string;
  purpose: string;
}
export interface BoardIdea {
  arc_id: number | null;
  title: string;
}

/** plan._board_text: existing arcs (by key) with their ideas, then the unsorted ones. */
export function boardText(arcs: BoardArc[], ideas: BoardIdea[]): string {
  const lines = arcs.map((a) => {
    const titles = ideas.filter((i) => i.arc_id === a.id).map((i) => i.title);
    return `- arc:${a.id} "${a.title}" (${a.purpose || "no purpose yet"}); ideas: ` + (titles.join("; ") || "none");
  });
  const known = new Set(arcs.map((a) => a.id));
  const unsorted = ideas.filter((i) => i.arc_id === null || !known.has(i.arc_id)).map((i) => i.title);
  if (unsorted.length) lines.push("- (no arc yet): " + unsorted.join("; "));
  return lines.join("\n") || "(empty so far)";
}

export function organisePrompt(
  project: { name: string; brand: string; brief: string },
  board: string,
  dump: string,
): string {
  return `Project: ${project.name} (mostly for ${project.brand || "urban_makers"})\n` +
    `Brief: ${project.brief || "(none yet)"}\n\nThe board so far:\n${board}\n\n` +
    `The team's new ideas:\n<ideas>\n${dump.trim()}\n</ideas>`;
}

export interface OrganisePlan {
  /** New arcs to insert, in order, with the key ideas use to refer to them. */
  arcs: { key: string; title: string; purpose: string }[];
  /** Ideas to insert; arc is an existing arc id, a new arc key, or null (Unsorted). */
  ideas: { arc: { existing: number } | { key: string } | null; fields: IdeaFields }[];
  notes: string;
}

/** plan.organise's bookkeeping: reuse arc:<id> keys of this project, new keys become arcs. */
export function planOrganise(result: unknown, existingArcIds: number[], projectBrand: string): OrganisePlan {
  const r = asObject(result);
  const existing = new Map(existingArcIds.map((id) => [`arc:${id}`, id]));
  const arcs: OrganisePlan["arcs"] = [];
  const newKeys = new Set<string>();
  for (const raw of Array.isArray(r.arcs) ? r.arcs.slice(0, 30) : []) {
    const a = asObject(raw);
    const key = text(a.key, 100);
    const title = text(a.title, 200);
    if (!key || !title || existing.has(key) || newKeys.has(key)) continue;
    newKeys.add(key);
    arcs.push({ key, title, purpose: text(a.purpose, 1000) });
  }
  const ideas: OrganisePlan["ideas"] = [];
  for (const raw of Array.isArray(r.ideas) ? r.ideas.slice(0, 60) : []) {
    const key = text(asObject(raw).arc, 100);
    const arc = existing.has(key) ? { existing: existing.get(key)! } : newKeys.has(key) ? { key } : null;
    ideas.push({ arc, fields: ideaFields(raw, { brand: projectBrand }) });
  }
  return { arcs, ideas, notes: text(r.notes, 4000) };
}

// ── Develop ───────────────────────────────────────────────────────────────────

export interface DevelopIdea {
  arc_id: number | null;
  title: string;
  brand: string;
  format: string;
  post_type?: string | null;
  est_seconds: number | null;
  hook: string;
  purpose: string;
  cta: string;
  caption: string;
  music: Record<string, unknown> | null;
}

export function developPrompt(
  project: { name: string; brief: string } | null,
  idea: DevelopIdea,
  shots: (ShotRow & { state: ShotState })[],
  comments: { author_name: string; body: string }[],
  request: string,
): string {
  const music = asObject(idea.music);
  const shotLines = shots.map((s) =>
    `  id ${s.id} [${s.state}] ${s.camera || ""} ${s.framing || ""} ${s.kind || ""}: ${s.description}` +
    (s.line ? ` | says: ${s.line}` : "") + (s.on_screen ? ` | on screen: ${s.on_screen}` : "")
  );
  return `Project: ${project?.name ?? "-"}. Brief: ${project ? project.brief || "-" : "-"}\n\n` +
    `The idea now (arc key: arc:${idea.arc_id || 0}):\n` +
    `  title: ${idea.title}\n  account: ${idea.brand}, kind: ${
      idea.post_type && KINDS[idea.post_type] ? idea.post_type : "(none yet)"
    }, format: ${idea.format}, ~${Math.round(idea.est_seconds || 30)}s\n` +
    `  hook: ${idea.hook || "-"}\n  purpose: ${idea.purpose || "-"}\n  cta: ${idea.cta || "-"}\n` +
    `  caption: ${idea.caption || "-"}\n  music: ${music.mood || "-"}\n` +
    "  shots:\n" + (shotLines.join("\n") || "  (none yet)") + "\n\n" +
    (comments.length
      ? "Team comments (newest first):\n" +
        comments.map((c) => `  ${c.author_name || "someone"}: ${c.body}`).join("\n") + "\n\n"
      : "") +
    `The team's request: ${request.trim() || "(none: make the plan complete and filmable)"}`;
}

export interface ShotPlan {
  update: { id: number; position: number; fields: ShotFields }[];
  insert: { position: number; fields: ShotFields }[];
  /** Not kept by Claude but filmed or with footage: never lost, moved to the end. */
  move: { id: number; position: number }[];
  delete: number[];
}

/** plan.develop's storyboard merge: keep_id reuses one of this idea's shots (once), the rest
 * are new; unkept shots are deleted unless filmed or with takes; positions run 1..n. */
export function planShots(current: (ShotRow & { state: ShotState })[], proposed: unknown): ShotPlan {
  const byId = new Map(current.map((s) => [s.id, s]));
  const kept = new Set<number>();
  const plan: ShotPlan = { update: [], insert: [], move: [], delete: [] };
  let position = 0;
  for (const raw of Array.isArray(proposed) ? proposed.slice(0, 40) : []) {
    position += 1;
    const keepId = asObject(raw).keep_id;
    const fields = shotFields(raw);
    if (typeof keepId === "number" && byId.has(keepId) && !kept.has(keepId)) {
      kept.add(keepId);
      plan.update.push({ id: keepId, position, fields });
    } else {
      plan.insert.push({ position, fields });
    }
  }
  for (const s of current) {
    if (kept.has(s.id)) continue;
    if (s.state !== "needed") {
      position += 1;
      plan.move.push({ id: s.id, position });
    } else {
      plan.delete.push(s.id);
    }
  }
  return plan;
}
