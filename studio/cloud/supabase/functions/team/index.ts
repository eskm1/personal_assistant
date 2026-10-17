// Owner only.
// GET                                          -> {members: [{id, name, email, role, last_sign_in_at, invited_at, created_at}]}
// POST {action: 'invite', email, name, redirect_to?} -> {member: {...}}  (sends the Supabase invite email)
// POST {action: 'remove', user_id}             -> {removed: user_id}
import { check, db, requireOwner } from "../_shared/db.ts";
import { HttpError, json, readJson, serve } from "../_shared/http.ts";

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
// Only work addresses join the team (ALLOWED_EMAIL_DOMAINS secret, comma-separated; "*" = any).
const DOMAINS = (Deno.env.get("ALLOWED_EMAIL_DOMAINS") || "theurbanmakers.com")
  .split(",").map((d) => d.trim().toLowerCase().replace(/^@/, "")).filter(Boolean);
export function allowedEmail(email: string): boolean {
  return DOMAINS.includes("*") || DOMAINS.includes(email.split("@").pop() || "");
}
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

interface Profile {
  id: string;
  name: string;
  email: string;
  role: string;
  created_at: string;
}

serve(["GET", "POST"], async (req) => {
  const caller = await requireOwner(req);
  if (req.method === "GET") return json({ members: await members() });

  const body = await readJson(req);
  if (body.action === "invite") return json({ member: await invite(body) });
  if (body.action === "remove") {
    const userId = typeof body.user_id === "string" ? body.user_id : "";
    if (!UUID.test(userId)) throw new HttpError(400, "user_id is required");
    if (userId === caller.id) throw new HttpError(400, "You can't remove yourself");
    const profile = check(
      await db().from("profiles").select("role").eq("id", userId).maybeSingle(),
      "read profile",
    ) as { role: string } | null;
    if (!profile) throw new HttpError(404, "Not on the team");
    if (profile.role === "owner") throw new HttpError(400, "The owner can't be removed");
    const { error } = await db().auth.admin.deleteUser(userId);
    if (error) throw new HttpError(502, `Couldn't remove: ${error.message}`);
    return json({ removed: userId });
  }
  throw new HttpError(400, "action must be 'invite' or 'remove'");
});

async function authUsers() {
  const users = new Map<string, { last_sign_in_at: string | null; invited_at: string | null; email: string }>();
  for (let page = 1; page <= 20; page++) {
    const { data, error } = await db().auth.admin.listUsers({ page, perPage: 200 });
    if (error) throw new Error(`list users: ${error.message}`);
    for (const u of data.users) {
      users.set(u.id, {
        last_sign_in_at: u.last_sign_in_at ?? null,
        invited_at: u.invited_at ?? null,
        email: u.email ?? "",
      });
    }
    if (data.users.length < 200) break;
  }
  return users;
}

async function members() {
  const [profilesRes, users] = await Promise.all([
    db().from("profiles").select("id, name, email, role, created_at").order("created_at"),
    authUsers(),
  ]);
  const profiles = check(profilesRes, "read profiles") as Profile[];
  return profiles.map((p) => {
    const u = users.get(p.id);
    return {
      id: p.id,
      name: p.name,
      email: u?.email || p.email,
      role: p.role,
      last_sign_in_at: u?.last_sign_in_at ?? null,
      invited_at: u?.invited_at ?? null,
      created_at: p.created_at,
    };
  });
}

async function invite(body: Record<string, unknown>) {
  const email = typeof body.email === "string" ? body.email.trim().toLowerCase() : "";
  if (!EMAIL.test(email) || email.length > 320) throw new HttpError(400, "Enter a valid email address");
  if (!allowedEmail(email)) {
    throw new HttpError(400, `Invite their work email: only ${DOMAINS.map((d) => "@" + d).join(", ")} addresses can join`);
  }
  const name = typeof body.name === "string" ? body.name.trim().slice(0, 100) : "";
  const redirect = typeof body.redirect_to === "string" && body.redirect_to
    ? body.redirect_to
    : Deno.env.get("PLANNER_URL") || "";
  if (redirect && !/^https?:\/\/[^\s]+$/i.test(redirect)) throw new HttpError(400, "redirect_to must be a URL");

  const existing = check(
    await db().from("profiles").select("id").eq("email", email).limit(1),
    "read profiles",
  ) as { id: string }[];
  if (existing.length) throw new HttpError(409, "That person is already on the team");

  const { data, error } = await db().auth.admin.inviteUserByEmail(email, {
    data: { name },
    ...(redirect ? { redirectTo: redirect } : {}),
  });
  if (error) {
    const status = (error as { status?: number }).status;
    if (status === 422 || /already/i.test(error.message)) throw new HttpError(409, "That email already has an account");
    if (status === 429) throw new HttpError(429, "Too many emails sent; try again in a while");
    throw new HttpError(502, `Couldn't send the invite: ${error.message}`);
  }
  const user = data.user;
  const profile = check(
    await db().from("profiles").select("id, name, email, role, created_at").eq("id", user.id).maybeSingle(),
    "read profile",
  ) as Profile | null;
  return {
    id: user.id,
    name: profile?.name ?? name,
    email: user.email ?? email,
    role: profile?.role ?? "team",
    last_sign_in_at: null,
    invited_at: user.invited_at ?? null,
    created_at: profile?.created_at ?? user.created_at,
  };
}
