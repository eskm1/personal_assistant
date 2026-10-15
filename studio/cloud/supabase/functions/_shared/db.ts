// Service-role Supabase client (bypasses RLS) and the calling user's identity.
import { createClient, type SupabaseClient } from "npm:@supabase/supabase-js@2.117.3";
import { HttpError } from "./http.ts";

let client: SupabaseClient | null = null;

/** SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are injected by Supabase into every function. */
export function serviceKey(): string {
  const key = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || Deno.env.get("SUPABASE_SECRET_KEY");
  if (!key) throw new Error("SUPABASE_SERVICE_ROLE_KEY is not set");
  return key;
}

export function db(): SupabaseClient {
  if (!client) {
    const url = Deno.env.get("SUPABASE_URL");
    if (!url) throw new Error("SUPABASE_URL is not set");
    client = createClient(url, serviceKey(), {
      auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false },
    });
  }
  return client;
}

export type Role = "owner" | "team";
export interface Caller {
  id: string;
  name: string;
  email: string;
  role: Role;
}

/** The member calling, from the request's user JWT (verified by the Auth server), or a 401/403. */
export async function requireMember(req: Request): Promise<Caller> {
  const header = req.headers.get("Authorization") ?? "";
  const token = header.match(/^Bearer\s+(.+)$/i)?.[1]?.trim();
  if (!token) throw new HttpError(401, "Sign in first");
  const { data, error } = await db().auth.getUser(token);
  if (error || !data?.user) throw new HttpError(401, "Your session has expired; sign in again");
  const { data: profile, error: pErr } = await db().from("profiles").select("id, name, email, role")
    .eq("id", data.user.id).maybeSingle();
  if (pErr) throw pErr;
  if (!profile) throw new HttpError(403, "You're not on the team");
  return profile as Caller;
}

export async function requireOwner(req: Request): Promise<Caller> {
  const caller = await requireMember(req);
  if (caller.role !== "owner") throw new HttpError(403, "Only the owner can do that");
  return caller;
}

/** Throws a PostgREST error as a plain Error (so job failure messages are readable). */
export function check<T>(res: { data: T; error: { message: string } | null }, what: string): T {
  if (res.error) throw new Error(`${what}: ${res.error.message}`);
  return res.data;
}
