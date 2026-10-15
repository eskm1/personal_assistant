// One structured-output call to Claude (streamed), like plan._ask, with its cost.
import Anthropic from "npm:@anthropic-ai/sdk@0.133.0";

export const MODEL = "claude-opus-5-5";

// $ per million tokens. Opus 5.5 is the requested model; server-side fallbacks may serve an
// attempt on another Opus, billed at that model's rates.
const PRICES: Record<string, { input: number; output: number; cacheRead: number; cacheWrite: number }> = {
  "claude-opus-5-5": { input: 4, output: 20, cacheRead: 0.2, cacheWrite: 5 },
  "claude-opus-5": { input: 5, output: 25, cacheRead: 0.5, cacheWrite: 6.25 },
  "claude-opus-4-8": { input: 5, output: 25, cacheRead: 0.5, cacheWrite: 6.25 },
};

export interface Usage {
  input_tokens: number;
  output_tokens: number;
  cache_read_input_tokens?: number;
  cache_creation_input_tokens?: number;
  cost_usd: number;
  model?: string;
}

/** A Claude call that ended without a usable answer; message is shown to the team. */
export class ClaudeError extends Error {
  constructor(message: string, public usage?: Usage) {
    super(message);
  }
}

interface TokenCounts {
  input_tokens?: number | null;
  output_tokens?: number | null;
  cache_read_input_tokens?: number | null;
  cache_creation_input_tokens?: number | null;
  model?: string | null;
}

export function costOf(
  usage: TokenCounts & { iterations?: TokenCounts[] | null },
  model: string = MODEL,
): Usage {
  // usage.iterations (when present) lists every attempt, including refused ones that fell back;
  // top-level usage only covers the attempt that produced the message.
  const parts: TokenCounts[] = usage.iterations?.length ? usage.iterations : [usage];
  const out: Usage = { input_tokens: 0, output_tokens: 0, cost_usd: 0 };
  let read = 0, write = 0, cost = 0;
  for (const p of parts) {
    const price = PRICES[p.model || model] ?? PRICES[MODEL];
    const i = p.input_tokens ?? 0, o = p.output_tokens ?? 0;
    const r = p.cache_read_input_tokens ?? 0, w = p.cache_creation_input_tokens ?? 0;
    out.input_tokens += i;
    out.output_tokens += o;
    read += r;
    write += w;
    cost += (i * price.input + o * price.output + r * price.cacheRead + w * price.cacheWrite) / 1e6;
  }
  if (read) out.cache_read_input_tokens = read;
  if (write) out.cache_creation_input_tokens = write;
  out.cost_usd = Math.round(cost * 1e6) / 1e6;
  if (model !== MODEL) out.model = model;
  return out;
}

export interface AskOptions {
  system: string;
  user: string;
  schema: Record<string, unknown>;
  effort: "low" | "medium" | "high" | "xhigh" | "max";
  maxTokens: number;
  /** Shown when the answer hits max_tokens. */
  cutOffMessage: string;
  /** Give up (and fail the job) at this time, before the platform kills the function. */
  deadline: number;
}

let anthropic: Anthropic | null = null;
function client(): Anthropic {
  if (!anthropic) {
    const apiKey = Deno.env.get("ANTHROPIC_API_KEY");
    if (!apiKey) throw new ClaudeError("ANTHROPIC_API_KEY isn't set on the server");
    anthropic = new Anthropic({
      apiKey,
      baseURL: Deno.env.get("ANTHROPIC_BASE_URL") || undefined,
      maxRetries: 1,
    });
  }
  return anthropic;
}

export async function ask<T>(opts: AskOptions): Promise<{ result: T; usage: Usage }> {
  const abort = new AbortController();
  const timer = setTimeout(() => abort.abort(), Math.max(1000, opts.deadline - Date.now()));
  let message: Anthropic.Beta.Messages.BetaMessage;
  try {
    const stream = client().beta.messages.stream({
      model: MODEL,
      max_tokens: opts.maxTokens,
      betas: ["server-side-fallback-2026-07-01"],
      fallbacks: "default",
      output_config: { effort: opts.effort, format: { type: "json_schema", schema: opts.schema } },
      system: opts.system,
      messages: [{ role: "user", content: opts.user }],
    }, { signal: abort.signal });
    message = await stream.finalMessage();
  } catch (e) {
    if (abort.signal.aborted) throw new ClaudeError("took too long, try again");
    if (e instanceof Anthropic.APIError) {
      const why = e instanceof Anthropic.RateLimitError
        ? "Claude is busy (rate limit); try again in a minute"
        : e instanceof Anthropic.AuthenticationError
        ? "the server's Anthropic API key was rejected"
        : e.status && e.status >= 500
        ? "Claude is unavailable right now; try again shortly"
        : `Claude API error ${e.status ?? ""}: ${e.message}`.trim();
      throw new ClaudeError(why);
    }
    throw e;
  } finally {
    clearTimeout(timer);
  }

  const usage = costOf(message.usage as TokenCounts & { iterations?: TokenCounts[] }, message.model);
  if (message.stop_reason === "refusal") throw new ClaudeError("Claude declined this request", usage);
  if (message.stop_reason === "max_tokens") throw new ClaudeError(opts.cutOffMessage, usage);
  // After a mid-stream fallback the content holds the declined partial, a `fallback` block,
  // then the answer: read only the text after the last fallback block.
  const blocks = message.content;
  let start = 0;
  blocks.forEach((b, i) => {
    if ((b.type as string) === "fallback") start = i + 1;
  });
  const text = blocks.slice(start).filter((b) => b.type === "text").map((b) => (b as { text: string }).text)
    .join("");
  if (!text.trim()) throw new ClaudeError("Claude returned no answer; try again", usage);
  try {
    return { result: JSON.parse(text) as T, usage };
  } catch {
    throw new ClaudeError("Claude's answer wasn't valid JSON; try again", usage);
  }
}
