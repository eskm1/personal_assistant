// Cloud job bookkeeping in the jobs table, and the context the prompts need.
import { check, db } from "./db.ts";
import { ClaudeError, type Usage } from "./claude.ts";

/** A failure whose message is meant for the team (shown on the job as is). */
export class JobError extends Error {}

/** A cloud job still running this long after started_at died with its function. */
export const STALE_MS = 4 * 60 * 1000;
export const STALE_MESSAGE = "took too long, try again";
/** Supabase free plan: 150 s wall clock per invocation. Stop Claude before that. */
export const WORK_BUDGET_MS = 135 * 1000;

export interface JobRow {
  id: number;
  kind: string;
  runner: string;
  project_id: number | null;
  idea_id: number | null;
  payload: Record<string, unknown>;
  state: string;
  requested_by: string | null;
  started_at: string | null;
}

/** Mark cloud jobs of this project/idea that died while running as failed. */
export async function expireStale(scope: { projectId?: number; ideaId?: number }): Promise<void> {
  let q = db().from("jobs").update({ state: "failed", error: STALE_MESSAGE })
    .eq("runner", "cloud").eq("state", "running")
    .lt("started_at", new Date(Date.now() - STALE_MS).toISOString());
  if (scope.projectId) q = q.eq("project_id", scope.projectId);
  if (scope.ideaId) q = q.eq("idea_id", scope.ideaId);
  check(await q, "expire stale jobs");
}

export async function finishJob(id: number, result: Record<string, unknown>): Promise<void> {
  check(await db().from("jobs").update({ state: "done", result, error: null }).eq("id", id), "finish job");
}

export async function failJob(id: number, e: unknown): Promise<void> {
  const message = e instanceof ClaudeError || e instanceof JobError
    ? e.message
    : e instanceof Error
    ? `Something went wrong: ${e.message}`
    : "Something went wrong";
  const usage: Usage | undefined = e instanceof ClaudeError ? e.usage : undefined;
  if (!(e instanceof ClaudeError || e instanceof JobError)) console.error(`job ${id} failed`, e);
  const { error } = await db().from("jobs")
    .update({ state: "failed", error: message.slice(0, 1000), ...(usage ? { result: { usage } } : {}) })
    .eq("id", id);
  if (error) console.error(`couldn't mark job ${id} failed`, error);
}

/** Run a job's work, recording done or failed (never leaves it running on an error). */
export async function runJob(id: number, work: () => Promise<Record<string, unknown>>): Promise<void> {
  try {
    await finishJob(id, await work());
  } catch (e) {
    await failJob(id, e);
  }
}

/** brands.md and the playbook, pushed by the laptop ("" when missing). */
export async function loadContext(): Promise<{ brands: string; playbook: string }> {
  const rows = check(await db().from("context").select("key, value"), "read context") as {
    key: string;
    value: string;
  }[];
  const get = (k: string) => rows.find((r) => r.key === k)?.value ?? "";
  return { brands: get("brands"), playbook: get("playbook") };
}
