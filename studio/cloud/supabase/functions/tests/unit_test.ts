// deno test --allow-env supabase/functions/tests/unit_test.ts   (no network, no database)
import { assert, assertEquals } from "jsr:@std/assert@1.0.13";
import { corsHeaders, HttpError, wrap } from "../_shared/http.ts";
import { costOf } from "../_shared/claude.ts";
import {
  boardText,
  developPrompt,
  ideaFields,
  planOrganise,
  planShots,
  shotFields,
  shotState,
} from "../_shared/plan.ts";
import {
  DEVELOP_SCHEMA,
  DEVELOP_SYSTEM,
  ORGANISE_NO_SHOTS_SCHEMA,
  ORGANISE_SCHEMA,
  renderSystem,
} from "../_shared/prompts.ts";

Deno.test("CORS preflight and errors carry CORS headers", async () => {
  const h = wrap(["POST"], () => {
    throw new HttpError(409, "busy", { job_id: 3 });
  });
  const pre = await h(new Request("http://x/", { method: "OPTIONS" }));
  assertEquals(pre.status, 200);
  assertEquals(pre.headers.get("Access-Control-Allow-Origin"), "*");
  assert(pre.headers.get("Access-Control-Allow-Headers")!.includes("x-client-info"));
  const res = await h(new Request("http://x/", { method: "POST" }));
  assertEquals(res.status, 409);
  assertEquals(await res.json(), { error: "busy", job_id: 3 });
  assertEquals(res.headers.get("Access-Control-Allow-Headers"), corsHeaders["Access-Control-Allow-Headers"]);
  assertEquals((await h(new Request("http://x/", { method: "GET" }))).status, 405);
});

Deno.test("system prompt placeholders are filled, $ sequences survive", () => {
  const s = renderSystem(DEVELOP_SYSTEM, "Urban Makers: $$ reno", "Cut on $& action");
  assert(!s.includes("{brands}") && !s.includes("{playbook}") && !s.includes("{cameras}"));
  assert(s.includes("Urban Makers: $$ reno") && s.includes("Cut on $& action"));
  assert(s.includes("films it (iphone = iPhone 16 Pro: talking to camera"));
});

Deno.test("schemas: organise stage 1 has no shots; develop keeps them", () => {
  // deno-lint-ignore no-explicit-any
  const ideaOf = (s: any) => s.properties.ideas.items;
  assert(!("shots" in ideaOf(ORGANISE_NO_SHOTS_SCHEMA).properties));
  assert(!ideaOf(ORGANISE_NO_SHOTS_SCHEMA).required.includes("shots"));
  assert("shots" in ideaOf(ORGANISE_SCHEMA).properties);
  // deno-lint-ignore no-explicit-any
  assert((DEVELOP_SCHEMA as any).properties.idea.required.includes("shots"));
});

Deno.test("shot state like plan.shot_state", () => {
  assertEquals(shotState({}, []), "needed");
  assertEquals(shotState({ filmed_at: "2026-01-01" }, []), "filmed");
  assertEquals(shotState({}, ["retake"]), "retake");
  assertEquals(shotState({}, ["retake", "good"]), "got");
  assertEquals(shotState({}, [null]), "got");
});

Deno.test("model output is clamped", () => {
  const f = ideaFields({ title: "  x ".repeat(200), brand: "nike", format: "vlog", est_seconds: 9999, music: {} }, {
    brand: "personal",
  });
  assertEquals(f.brand, "personal");
  assertEquals(f.format, "talking_head");
  assertEquals(f.est_seconds, 600);
  assert(f.title.length <= 300);
  assertEquals(ideaFields({}, { brand: "urban_makers" }).title, "Untitled idea");
  const s = shotFields({ kind: "drone", framing: "close", camera: "red", description: "  ", seconds: "5" });
  assertEquals([s.kind, s.framing, s.camera, s.description, s.seconds], [null, "close", null, "New shot", null]);
});

Deno.test("organise: existing keys reused, unknown keys Unsorted, existing arcs not re-added", () => {
  const plan = planOrganise(
    {
      arcs: [{ key: "arc:5", title: "dup", purpose: "" }, { key: "new:a", title: "A", purpose: "p" }, {
        key: "new:a",
        title: "A again",
        purpose: "",
      }],
      ideas: [{ arc: "arc:5", title: "one" }, { arc: "new:a", title: "two" }, { arc: "arc:77", title: "three" }],
      notes: "n",
    },
    [5],
    "urban_makers",
  );
  assertEquals(plan.arcs.map((a) => a.key), ["new:a"]);
  assertEquals(plan.ideas.map((i) => i.arc), [{ existing: 5 }, { key: "new:a" }, null]);
  assertEquals(boardText([], []), "(empty so far)");
  assertEquals(
    boardText([{ id: 5, title: "W", purpose: "" }], [{ arc_id: 5, title: "one" }, { arc_id: null, title: "u" }]),
    '- arc:5 "W" (no purpose yet); ideas: one\n- (no arc yet): u',
  );
});

Deno.test("develop: keep_id only within the idea, filmed shots never lost, positions 1..n", () => {
  const current = [
    { id: 1, state: "needed" as const },
    { id: 2, state: "filmed" as const },
    { id: 3, state: "got" as const },
    { id: 4, state: "needed" as const },
  ];
  const plan = planShots(current, [
    { keep_id: 4, description: "reuse 4" },
    { keep_id: 999, description: "foreign id -> new" },
    { keep_id: 4, description: "second keep of 4 -> new" },
    { keep_id: 2, description: "keep filmed" },
  ]);
  assertEquals(plan.update.map((u) => [u.id, u.position]), [[4, 1], [2, 4]]);
  assertEquals(plan.insert.map((i) => i.position), [2, 3]);
  assertEquals(plan.move, [{ id: 3, position: 5 }]);
  assertEquals(plan.delete, [1]);
});

Deno.test("develop prompt like plan.develop", () => {
  const p = developPrompt(
    { name: "Kensington", brief: "" },
    {
      arc_id: null,
      title: "T",
      brand: "urban_makers",
      format: "talking_head",
      est_seconds: 32.4,
      hook: "",
      purpose: "",
      cta: "",
      caption: "",
      music: { mood: "warm" },
    },
    [{ id: 7, state: "filmed", camera: "zfc", framing: null, kind: "detail", description: "D", line: "L" }],
    [{ author_name: "Wei", body: "nice" }],
    "",
  );
  assert(p.includes("(arc key: arc:0)") && p.includes("~32s") && p.includes("music: warm"));
  assert(p.includes("  id 7 [filmed] zfc  detail: D | says: L"));
  assert(p.includes("Team comments (newest first):\n  Wei: nice\n\n"));
  assert(p.endsWith("The team's request: (none: make the plan complete and filmable)"));
});

Deno.test("cost: Opus 5.5 rates, cache tokens, fallback iterations", () => {
  assertEquals(costOf({ input_tokens: 1_000_000, output_tokens: 100_000 }).cost_usd, 6);
  const c = costOf({ input_tokens: 0, output_tokens: 0, cache_read_input_tokens: 1_000_000 });
  assertEquals([c.cost_usd, c.cache_read_input_tokens], [0.2, 1_000_000]);
  const f = costOf({
    input_tokens: 1,
    output_tokens: 1,
    iterations: [
      { model: "claude-opus-5-5", input_tokens: 1_000_000, output_tokens: 0 },
      { model: "claude-opus-5", input_tokens: 1_000_000, output_tokens: 0 },
    ],
  }, "claude-opus-5");
  assertEquals([f.cost_usd, f.input_tokens, f.model], [9, 2_000_000, "claude-opus-5"]);
});
