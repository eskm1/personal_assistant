// HTTP plumbing shared by the functions: CORS, JSON in/out, errors, background work.

export const corsHeaders: Record<string, string> = {
  "Access-Control-Allow-Origin": Deno.env.get("ALLOWED_ORIGIN") || "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Max-Age": "86400",
};

/** An error with an HTTP status, answered as {error: message}. */
export class HttpError extends Error {
  constructor(public status: number, message: string, public extra: Record<string, unknown> = {}) {
    super(message);
  }
}

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...corsHeaders, "Content-Type": "application/json" },
  });
}

export async function readJson(req: Request): Promise<Record<string, unknown>> {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    throw new HttpError(400, "Send a JSON body");
  }
  if (!body || typeof body !== "object" || Array.isArray(body)) throw new HttpError(400, "Send a JSON object");
  return body as Record<string, unknown>;
}

/** A positive integer id from the request, or a 400. */
export function idParam(value: unknown, name: string): number {
  const n = typeof value === "string" && /^\d+$/.test(value) ? Number(value) : value;
  if (typeof n !== "number" || !Number.isSafeInteger(n) || n <= 0) throw new HttpError(400, `${name} is required`);
  return n;
}

/** Wraps a handler with CORS preflight, method check and error answers. */
export function wrap(methods: string[], handler: (req: Request) => Promise<Response>) {
  return async (req: Request): Promise<Response> => {
    if (req.method === "OPTIONS") return new Response("ok", { headers: corsHeaders });
    if (!methods.includes(req.method)) return json({ error: "Method not allowed" }, 405);
    try {
      return await handler(req);
    } catch (e) {
      if (e instanceof HttpError) return json({ error: e.message, ...e.extra }, e.status);
      console.error(e);
      return json({ error: "Something went wrong" }, 500);
    }
  };
}

export function serve(methods: string[], handler: (req: Request) => Promise<Response>): void {
  Deno.serve(wrap(methods, handler));
}

declare const EdgeRuntime: { waitUntil(promise: Promise<unknown>): void } | undefined;

/** Keep working after the response is sent (Supabase EdgeRuntime.waitUntil). */
export function background(promise: Promise<unknown>): void {
  const guarded = promise.catch((e) => console.error("background work failed", e));
  if (typeof EdgeRuntime !== "undefined" && EdgeRuntime?.waitUntil) EdgeRuntime.waitUntil(guarded);
}
