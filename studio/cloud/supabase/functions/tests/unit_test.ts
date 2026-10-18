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
  contentPlanText,
  DEVELOP_SCHEMA,
  DEVELOP_SYSTEM,
  MAX_SLIDES,
  ORGANISE_NO_SHOTS_SCHEMA,
  ORGANISE_SCHEMA,
  ORGANISE_SYSTEM,
  renderSystem,
  RULES,
  SLIDES_SCHEMA,
  SLIDES_SYSTEM,
} from "../_shared/prompts.ts";
import { cleanImage, planSlides, slidesPrompt, storedSlides } from "../_shared/slides.ts";

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

Deno.test("content plan: kinds, this month's gaps and only the listed offers reach the prompts", () => {
  const text = contentPlanText(
    { posts_per_week: 3, offers: [{ name: "Free consultation", details: "30 min", ask: "Book one" }, { name: " " }] },
    {
      month: "2026-10",
      target: 13,
      planned: 8,
      kinds: {
        knowhow: { target: 4, planned: 2 },
        ideas: { target: 2, planned: 0 },
        onsite: { target: 3, planned: 4 },
      },
    },
  );
  for (const k of ["knowhow (Know-how", "ideas (Ideas", "onsite (On site", "finished (Finished homes", "offer (Work"]) {
    assert(text.includes(k), k);
  }
  assert(text.includes("October 2026: 8 of 13 posts planned (3 a week)."));
  assert(text.includes("Know-how 2 of 4 (2 short); Ideas 0 of 2 (2 short); On site 4 of 3; Finished homes 0 of 0"));
  assert(text.includes("Short this month: Know-how, Ideas."));
  assert(text.includes('- Free consultation: 30 min (ask: "Book one")'));
  assert(!text.includes("Site visit") && text.includes("Never invent a price"));
  // No plan row yet: the default offers; no counts: no month line.
  const plain = contentPlanText();
  assert(plain.includes("- Site visit") && plain.includes("- Floor plan review") && !plain.includes("short"));
  assert(contentPlanText({ offers: [] }).includes("(none listed yet"));
  // The placeholder is filled in both systems, with the defaults when nothing was loaded.
  for (const t of [ORGANISE_SYSTEM, DEVELOP_SYSTEM]) {
    assert(t.includes("{content_plan}"));
    const filled = renderSystem(t, "b", "p", text);
    assert(!filled.includes("{content_plan}") && filled.includes("Short this month"));
    assert(renderSystem(t, "b", "p").includes("- Free consultation"));
  }
});

Deno.test("ideas carry a kind of post; unknown kinds are dropped", () => {
  // deno-lint-ignore no-explicit-any
  const idea = (ORGANISE_NO_SHOTS_SCHEMA as any).properties.ideas.items;
  assert(idea.required.includes("post_type"));
  assertEquals(idea.properties.post_type.enum, ["knowhow", "ideas", "onsite", "finished", "offer"]);
  assertEquals(ideaFields({ post_type: "onsite" }, { brand: "urban_makers" }).post_type, "onsite");
  assertEquals(ideaFields({ post_type: "promo" }, { brand: "urban_makers" }).post_type, null);
  const p = developPrompt(
    null,
    {
      arc_id: 1,
      title: "T",
      brand: "urban_makers",
      format: "talking_head",
      post_type: "knowhow",
      est_seconds: 30,
      hook: "",
      purpose: "",
      cta: "",
      caption: "",
      music: null,
    },
    [],
    [],
    "",
  );
  assert(p.includes("kind: knowhow"));
});

// ── Carousels ────────────────────────────────────────────────────────────────

Deno.test("carousel: format allowed for new ideas, slides schema and system carry the rules", () => {
  // deno-lint-ignore no-explicit-any
  const idea = (ORGANISE_NO_SHOTS_SCHEMA as any).properties.ideas.items;
  assert(idea.properties.format.enum.includes("carousel"));
  assertEquals(ideaFields({ format: "carousel" }, { brand: "urban_makers" }).format, "carousel");
  // deno-lint-ignore no-explicit-any
  const slide = (SLIDES_SCHEMA as any).properties.slides.items;
  assertEquals(slide.properties.role.enum, ["cover", "point", "ask"]);
  for (const k of ["keep_image", "clip_id", "at", "image_idea"]) assert(slide.required.includes(k), k);
  const filled = renderSystem(
    SLIDES_SYSTEM,
    "BRANDS-TEXT",
    "PLAYBOOK-TEXT",
    contentPlanText({ offers: [{ name: "Site visit", ask: "Book a site visit" }] }),
  );
  assert(filled.includes("BRANDS-TEXT") && filled.includes("PLAYBOOK-TEXT"));
  assert(filled.includes("Bryan's voice") && filled.includes("4 to 8 point slides"));
  assert(filled.includes("uses ONLY") && filled.includes('- Site visit (ask: "Book a site visit")'));
  assert(filled.includes("never anyone else's images") && filled.includes("Never name a place"));
  assert(filled.includes("Ideas (to get found)") || filled.includes("ideas (Ideas"));
  assert(RULES.includes("carousel for a"));
});

Deno.test("carousel: planSlides keeps one cover first, the ask last, only real pictures", () => {
  const current = storedSlides([
    { key: "a", role: "cover", heading: "Old cover", image: { source: "text" } },
    {
      key: "b",
      role: "point",
      heading: "Old point",
      image: { source: "photo", photo_id: "0123456789abcdef0123456789abcdef", thumb_path: "photos/x.jpg" },
    },
  ]);
  const clips = [{ id: 12, title: "Window seat", duration: 20, thumb_path: "clips/12.jpg" }];
  let n = 0;
  const plan = planSlides(
    {
      title: "  Quiet corner  ",
      post_type: "ideas",
      purpose: "p",
      caption: "c #reno",
      changes: "Wrote 5",
      slides: [
        { role: "point", heading: "Point A", body: "a", keep_image: 2, clip_id: 0, at: 0, image_idea: "" },
        { role: "ask", heading: "First ask", body: "", keep_image: 0, clip_id: 0, at: 0, image_idea: "" },
        { role: "cover", heading: "The hook", body: "", keep_image: 0, clip_id: 12, at: 99, image_idea: "seat" },
        { role: "point", heading: "", body: "", keep_image: 0, clip_id: 0, at: 0, image_idea: "" },
        { role: "point", heading: "Point B", body: "b", keep_image: 9, clip_id: 555, at: 3, image_idea: "lamp" },
        {
          role: "ask",
          heading: "Planning a reno?",
          body: "Send us your floor plan.",
          keep_image: 1,
          clip_id: 0,
          at: 0,
          image_idea: "",
        },
      ],
    },
    current,
    clips,
    "Fallback",
    () => `k${++n}`,
  );
  assertEquals(plan.slides.map((s) => s.role), ["cover", "point", "point", "point", "ask"]);
  assertEquals(plan.slides.map((s) => s.heading), ["The hook", "Point A", "First ask", "Point B", "Planning a reno?"]);
  assertEquals(plan.slides[0].image, { source: "clip", clip_id: 12, at: 20, thumb_path: "clips/12.jpg", note: "seat" });
  assertEquals(plan.slides[1].image.source, "photo"); // kept the picture someone picked
  assertEquals(plan.slides[1].image.photo_id, "0123456789abcdef0123456789abcdef");
  assertEquals(plan.slides[3].image, { source: "text", note: "lamp" }); // unknown clip, bad keep: text only
  assertEquals(plan.slides[4].image, { source: "text" }); // keep_image of a text slide: nothing to keep
  assertEquals(plan.idea.title, "Quiet corner");
  assertEquals(plan.idea.hook, "The hook");
  assertEquals(plan.idea.cta, "Planning a reno? Send us your floor plan.");
  assertEquals(plan.idea.post_type, "ideas");
  assertEquals(planSlides({ post_type: "promo", slides: [] }, [], [], "F").idea.post_type, null);
  // At most 12, the ask kept.
  const many = planSlides(
    {
      slides: Array.from({ length: 20 }, (_, i) => ({ role: i === 19 ? "ask" : "point", heading: `S${i}`, body: "" })),
    },
    [],
    [],
    "F",
  );
  assertEquals(many.slides.length, MAX_SLIDES);
  assertEquals(many.slides[0].role, "cover");
  assertEquals(many.slides[MAX_SLIDES - 1].heading, "S19");
});

Deno.test("carousel: stored slides and pictures are cleaned like valid_slides()", () => {
  assertEquals(cleanImage({ source: "clip", clip_id: 3, at: 2.345 }), { source: "clip", clip_id: 3, at: 2.3 });
  assertEquals(cleanImage({ source: "clip" }), { source: "text" });
  assertEquals(cleanImage({ source: "photo", photo_id: "../x" }), { source: "text" });
  assertEquals(cleanImage({ source: "nope" }), { source: "text" });
  assertEquals(cleanImage({ source: "text", thumb_path: "../../etc/passwd" }), { source: "text" });
  assertEquals(cleanImage({ source: "concept", note: "a sketch" }), { source: "concept", note: "a sketch" });
  const s = storedSlides([{ role: "bogus", heading: 5, image: null }, "x"]);
  assertEquals(s[0].role, "point");
  assertEquals(s[0].heading, "");
  assertEquals(s[1].image, { source: "text" });
});

Deno.test("carousel: the prompt lists slides, pictures, clips and the request", () => {
  const p = slidesPrompt(
    { name: "Topics", brief: "know-how", standing: "topics" },
    { title: "Quiet corner", post_type: "ideas", purpose: "", caption: "" },
    storedSlides([{ role: "cover", heading: "Hook", image: { source: "clip", clip_id: 7, at: 4 } }]),
    [{ id: 7, title: "Lamp corner", duration: 65, thumb_path: null, project_id: 2 }],
    [{ author_name: "Wei", body: "More on lighting" }],
    "Make it shorter",
    1,
  );
  assert(p.includes("(not one client's renovation)"));
  assert(p.includes('1. [cover] Hook | picture: a still from clip 7 ("Lamp corner") at 4s'));
  assert(p.includes('clip 7: "Lamp corner", 1:05 (another project)'));
  assert(p.includes("kind: ideas") && p.includes("Wei: More on lighting") && p.includes("request: Make it shorter"));
  assert(
    slidesPrompt(null, { title: "T", purpose: "", caption: "", hook: "H" }, [], [], [], "").includes(
      "(none yet: use text-only slides)",
    ),
  );
});
