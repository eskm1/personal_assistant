// Prompts and JSON schemas ported from studio/plan.py (tuned there; keep the two in step).
import { CAPTION_RULE, HOOK_RULE, NEVER_WRITE, VOICE_HEAD } from "./wording.ts";

export const SHOT_KINDS = ["talking", "broll", "detail", "before_after", "reaction", "screen"] as const;
export const FRAMINGS = ["wide", "medium", "close", "detail", "pov", "overhead"] as const;
// The team's kit and what each camera is for, so footage from three cameras cuts together.
export const CAMERAS: Record<string, string> = {
  iphone: "iPhone 16 Pro: talking to camera, walk-and-talks, anything that must be vertical",
  zfc: "Nikon Z fc: detail and beauty B-roll (materials, finishes, reveals), 4K horizontal, subject centred",
  gopro: "GoPro: POV and hands-on work, tight spaces, timelapses; never for talking",
  any: "whichever camera is at hand",
};
export const BRANDS = ["personal", "urban_makers"] as const;
// The Reel formats the team films, and carousel (slides; no filming).
export const FORMATS = ["talking_head", "day_in_the_life", "compilation", "carousel"] as const;
export const REEL_FORMATS = ["talking_head", "day_in_the_life", "compilation"] as const;

// The five kinds of post (ideas.post_type), as in studio/content_plan.py (keep the two in step).
export const POST_TYPES = ["knowhow", "ideas", "onsite", "finished", "offer"] as const;
export const KINDS: Record<string, { label: string; stage: string; job: string; ask: string }> = {
  knowhow: {
    label: "Know-how",
    stage: "Get found",
    job: "teach something practical people save and share (HDB rules, hidden costs, materials that last in " +
      "Singapore humidity)",
    ask: "save this / follow for more",
  },
  ideas: {
    label: "Ideas",
    stage: "Get found",
    job: 'design thinking: layouts, zoning, light, colour ("3 ways to give an open-plan flat a quiet corner")',
    ask: "save this / which would you pick?",
  },
  onsite: {
    label: "On site",
    stage: "Build trust",
    job: "show the craft and care while the work happens (waterproofing done right, a carpentry detail, a " +
      "problem solved)",
    ask: "follow this project",
  },
  finished: {
    label: "Finished homes",
    stage: "Build trust",
    job: "proof: the portfolio, before and after, the owners' reaction",
    ask: "see the whole project / talk to us about yours",
  },
  offer: {
    label: "Work with us",
    stage: "Get enquiries",
    job: "turn followers into enquiries: how we work, what's included, one of the offers below",
    ask: "one of the offers below",
  },
};
export interface Offer {
  name: string;
  details?: string;
  ask?: string;
}
export const DEFAULT_OFFERS: Offer[] = [
  { name: "Free consultation", details: "", ask: "Book a free consultation" },
  { name: "Site visit", details: "", ask: "Book a site visit" },
  { name: "Floor plan review", details: "", ask: "Send us your floor plan" },
];

type Schema = Record<string, unknown>;

export const SHOT_SCHEMA: Schema = {
  type: "object",
  properties: {
    keep_id: { type: "integer" },
    kind: { type: "string", enum: [...SHOT_KINDS] },
    framing: { type: "string", enum: [...FRAMINGS] },
    camera: { type: "string", enum: Object.keys(CAMERAS) },
    description: { type: "string" },
    line: { type: "string" },
    on_screen: { type: "string" },
    seconds: { type: "number" },
    why: { type: "string" },
  },
  required: ["keep_id", "kind", "framing", "camera", "description", "line", "on_screen", "seconds", "why"],
  additionalProperties: false,
};

const MUSIC_SCHEMA: Schema = {
  type: "object",
  properties: { mood: { type: "string" }, tempo_bpm: { type: "number" }, search: { type: "string" } },
  required: ["mood", "tempo_bpm", "search"],
  additionalProperties: false,
};

// Three hooks from the 26 formulas (studio/wording.py HOOK_RULE); the Studio machine scores them
// (hookscore) and keeps the strongest as the hook (cloud_sync.score_hooks).
export const HOOK_OPTION_SCHEMA: Schema = {
  type: "object",
  properties: { text: { type: "string" }, formula: { type: "string" } },
  required: ["text", "formula"],
  additionalProperties: false,
};

const IDEA_FIELDS: Schema = {
  arc: { type: "string" },
  title: { type: "string" },
  brand: { type: "string", enum: [...BRANDS] },
  post_type: { type: "string", enum: [...POST_TYPES] },
  format: { type: "string", enum: [...FORMATS] },
  hook: { type: "string" },
  hook_options: { type: "array", items: HOOK_OPTION_SCHEMA },
  purpose: { type: "string" },
  cta: { type: "string" },
  caption: { type: "string" },
  est_seconds: { type: "number" },
  music: MUSIC_SCHEMA,
};
const IDEA_REQUIRED = [
  "arc",
  "title",
  "brand",
  "post_type",
  "format",
  "hook",
  "hook_options",
  "purpose",
  "cta",
  "caption",
  "est_seconds",
  "music",
];

/** plan.py _IDEA: an idea with its storyboard. */
export const IDEA_SCHEMA: Schema = {
  type: "object",
  properties: { ...IDEA_FIELDS, shots: { type: "array", items: SHOT_SCHEMA } },
  required: [...IDEA_REQUIRED, "shots"],
  additionalProperties: false,
};

/** Organise stage 1 in the cloud: ideas without shots (each storyboard is a develop job). */
export const IDEA_NO_SHOTS_SCHEMA: Schema = {
  type: "object",
  properties: { ...IDEA_FIELDS },
  required: IDEA_REQUIRED,
  additionalProperties: false,
};

function organiseSchema(idea: Schema): Schema {
  return {
    type: "object",
    properties: {
      arcs: {
        type: "array",
        items: {
          type: "object",
          properties: { key: { type: "string" }, title: { type: "string" }, purpose: { type: "string" } },
          required: ["key", "title", "purpose"],
          additionalProperties: false,
        },
      },
      ideas: { type: "array", items: idea },
      notes: { type: "string" },
    },
    required: ["arcs", "ideas", "notes"],
    additionalProperties: false,
  };
}

export const ORGANISE_SCHEMA: Schema = organiseSchema(IDEA_SCHEMA);
export const ORGANISE_NO_SHOTS_SCHEMA: Schema = organiseSchema(IDEA_NO_SHOTS_SCHEMA);

export const DEVELOP_SCHEMA: Schema = {
  type: "object",
  properties: { idea: IDEA_SCHEMA, changes: { type: "string" } },
  required: ["idea", "changes"],
  additionalProperties: false,
};

export const RULES = `How to plan a post:
- One clear point per post, 15 to 60 seconds. The hook (first 3 seconds) states the tension or \
payoff; spoken hooks should sound like Bryan talking, not an ad.
- purpose: who it's for and what they should think, feel or do after watching (one or two sentences).
- post_type: the kind of post, by the job it does (the content plan below lists the five).
- format: talking_head, day_in_the_life or compilation for a Reel the team films; carousel for a \
post of 6 to 10 slides people swipe through and save (no filming: Studio writes the slides and lays \
them out with stills from the team's own footage, their photos, or text). A carousel suits Know-how, \
Ideas and Work with us posts whose point is a list of tips, steps, options or what's included. Most \
posts stay Reels: propose a carousel only where it clearly suits, never for a post that needs \
footage of the work happening. A carousel has no shots (its slides are written next).
- cta: the call to action at the end, fitting the kind's ask ("Save this for your reno", "Follow \
this project", ...). An offer only from the listed offers, worded as listed.
- caption: a ready-to-edit Instagram caption in Bryan's voice with 3 to 5 hashtags (the caption rule below).
- hook_options: three hooks from the hook formulas below (hook = the one you'd pick).
- shots: the storyboard, one entry per shot, in playback order, each something the team can \
actually film on site. kind: talking (to camera), broll (work happening), detail (close-up of a \
material, fixing, finish), before_after, reaction, screen (a drawing, render or phone screen). \
framing: wide / medium / close / detail / pov / overhead. camera: which of the team's cameras \
films it ({cameras}). description: what the camera sees, \
specific enough to film without asking ("Close-up: trowel spreading grey membrane into the \
wall-floor corner, slow pan left"). line: what's said over it ("" if nothing). on_screen: text on \
screen ("" if none). seconds: rough length in the final cut. why: what this shot does for the story. \
Plan B-roll and detail shots to cover talking, as the playbook says. Usually 4 to 10 shots. Filming speech \
on site is hard (noise, light, people): plan talking shots to be filmed in Bryan's studio after the site day \
(say "in the studio" in the description) and keep the site shots to B-roll and details with no lines, so \
the team films the site without anyone talking and Bryan talks over it later (Studio puts the site footage \
full screen with him in a small window). Plan a talking shot on site only when it must be there: a \
walk-through, a reveal, a reaction.
- music: the mood, tempo and a short search phrase for the music bed. For personal posts this \
becomes a note on what to pick from Instagram's library.
- Never invent facts, prices, timelines, promotions, offers or client details. Write \
[placeholder] where the team must fill something in.
- keep_id: 0 for a new shot.`;

export const ORGANISE_SYSTEM = `You are the content producer for Bryan's Instagram accounts. The team is \
planning posts before filming. Turn their ideas into organised, filmable plans.

{brands}

Bryan's playbook (his editing and story rules; plan shots and structure that let the editor follow it):

<playbook>
{playbook}
</playbook>

The content plan (the kinds of post, this month's mix and the offers):

<content_plan>
{content_plan}
</content_plan>

` + RULES + `

How to organise:
- Group ideas into arcs: content series a viewer would follow (a topic, a phase of the job, a \
recurring format). Reuse an existing arc by giving its key exactly ("arc:12"); make a new one \
with a short key of your own ("new:waterproofing") and list it in arcs. Don't list existing arcs.
- Keep every idea the team wrote (merge only true duplicates), sharpen it, and add a few strong \
ones the project obviously supports if the team asked for more. Don't repeat existing ideas. \
Give each the kind that fits it; when a new idea could go either way, prefer a kind that's \
short this month.
- notes: anything the team should know (merged duplicates, ideas that need a fact checked, gaps \
in the series).`;

/** Cloud organise is two-stage: this step plans the ideas only, each storyboard comes next. */
export const ORGANISE_NO_SHOTS_NOTE = `
- This step plans the ideas only, without shots: each idea's storyboard is planned separately \
right after, from what you write here. Make the hook, purpose and format specific enough to \
storyboard from.`;

export const DEVELOP_SYSTEM = `You are the content producer for Bryan's Instagram accounts, refining one \
planned post with the team before it's filmed.

{brands}

Bryan's playbook:

<playbook>
{playbook}
</playbook>

The content plan (the kinds of post, this month's mix and the offers):

<content_plan>
{content_plan}
</content_plan>

` + RULES + `

Refining:
- Apply the team's request. If there's none, make the plan complete and filmable: a sharp hook, \
a clear structure, B-roll covering the talking, a strong end.
- Shots that are already filmed or have footage are marked; keep those (keep_id = their id) \
unless the request says otherwise, and plan around them. Reusing an unfilmed shot keeps its \
comments and history, so give its id in keep_id too; new shots get keep_id 0.
- arc: return the idea's current arc key unchanged. post_type: its current kind when it has \
one (the team changes kinds themselves); otherwise the kind that fits it.
- changes: one or two sentences on what you changed, for the team.`;

export function camerasText(): string {
  return Object.entries(CAMERAS).map(([k, v]) => `${k} = ${v}`).join("; ");
}

export interface MonthCounts {
  month?: string;
  target?: number;
  planned?: number;
  kinds?: Record<string, { target?: number; planned?: number }>;
}

const MONTHS = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];
function monthName(m?: string): string {
  const match = String(m || "").match(/^(\d{4})-(\d{2})$/);
  return match ? `${MONTHS[Number(match[2]) - 1]} ${match[1]}` : "This month";
}

/** content_plan.prompt_text: the kinds, this month's gaps (when known) and the offers. */
export function contentPlanText(
  plan?: { posts_per_week?: number; offers?: Offer[] } | null,
  month?: MonthCounts | null,
): string {
  const lines = ["Kinds of post. Every post has exactly one kind (post_type), by the job it does:"];
  for (const key of POST_TYPES) {
    const k = KINDS[key];
    const ask = k.ask.endsWith("?") ? k.ask : `${k.ask}.`;
    lines.push(`- ${key} (${k.label}, to ${k.stage.toLowerCase()}): ${k.job}. Ask: ${ask}`);
  }
  if (month?.kinds && typeof month.kinds === "object") {
    const parts: string[] = [], short: string[] = [];
    for (const key of POST_TYPES) {
      const c = month.kinds[key] || {};
      const target = Number(c.target) || 0, planned = Number(c.planned) || 0;
      const gap = Math.max(target - planned, 0);
      parts.push(`${KINDS[key].label} ${planned} of ${target}` + (gap ? ` (${gap} short)` : ""));
      if (gap) short.push(KINDS[key].label);
    }
    lines.push(
      "",
      `${monthName(month.month)}: ${month.planned ?? 0} of ${month.target ?? 0} posts planned (${
        plan?.posts_per_week ?? 3
      } a week). ` + parts.join("; ") + ".",
    );
    lines.push(
      short.length
        ? `Short this month: ${short.join(", ")}. When the footage or the team's ideas allow, lean toward ` +
          "these kinds; never force a post into a kind it isn't."
        : "Every kind is on target this month: pick whatever kind fits each post best.",
    );
  }
  const offers = (Array.isArray(plan?.offers) ? plan!.offers : DEFAULT_OFFERS)
    .filter((o) => o && typeof o.name === "string" && o.name.trim());
  lines.push(
    "",
    "Offers: the ONLY things a Work with us post, or any call to action, may offer. Never invent a price, " +
      "discount, promotion, deadline or offer; no figures unless they're written here.",
  );
  if (offers.length) {
    for (const o of offers) {
      lines.push(
        `- ${o.name.trim()}` + (o.details?.trim() ? `: ${o.details.trim()}` : "") +
          (o.ask?.trim() ? ` (ask: "${o.ask.trim()}")` : ""),
      );
    }
  } else {
    lines.push("- (none listed yet: a call to action asks people to follow, save or DM, and offers nothing)");
  }
  return lines.join("\n");
}

/** Fill {brands}, {playbook}, {content_plan} and {cameras} like plan._ask (brands/playbook from the
 * context table, the content plan from content_plan and content_month()); `wording` (wordingBlock)
 * goes at the end, as studio/wording.guide does in the Studio machine's prompts. */
export function renderSystem(
  template: string,
  brands: string,
  playbook: string,
  contentPlan = "",
  wording = "",
): string {
  // Function replacers: the texts may contain "$" sequences that string replacements would expand.
  const text = template.replaceAll("{brands}", () => brands.trim()).replaceAll("{playbook}", () => playbook.trim())
    .replaceAll("{content_plan}", () => contentPlan.trim() || contentPlanText())
    .replaceAll("{cameras}", () => camerasText());
  return wording.trim() ? `${text}\n\n${wording.trim()}` : text;
}

/** studio/wording.guide: Bryan's voice file and the "never write these" list (both pushed by the
 * Studio machine into the context table; the generated fallbacks until then), and as asked the
 * caption rule and the hook formulas. */
export function wordingBlock(
  voice: string,
  never: string,
  opts: { caption?: boolean; hooks?: boolean } = {},
): string {
  const parts: string[] = [];
  if (voice.trim()) parts.push(`${VOICE_HEAD}\n<voice>\n${voice.trim()}\n</voice>`);
  parts.push(never.trim() || NEVER_WRITE);
  if (opts.caption) parts.push(CAPTION_RULE);
  if (opts.hooks) parts.push(HOOK_RULE);
  return parts.join("\n\n");
}

// ── Carousels: the slides of one post (plan-develop on an idea whose format is carousel) ──
// Keep in step with studio/carousel.py (_SLIDE, _REVISE_SYSTEM), which revises them from Bryan's notes.

export const SLIDE_ROLES = ["cover", "point", "ask"] as const;
export const MAX_SLIDES = 12;

export const SLIDE_SCHEMA: Schema = {
  type: "object",
  properties: {
    role: { type: "string", enum: [...SLIDE_ROLES] },
    heading: { type: "string" },
    body: { type: "string" },
    keep_image: { type: "integer" },
    clip_id: { type: "integer" },
    at: { type: "number" },
    image_idea: { type: "string" },
    concept: { type: "boolean" },
  },
  required: ["role", "heading", "body", "keep_image", "clip_id", "at", "image_idea", "concept"],
  additionalProperties: false,
};

export const SLIDES_SCHEMA: Schema = {
  type: "object",
  properties: {
    title: { type: "string" },
    post_type: { type: "string", enum: [...POST_TYPES] },
    purpose: { type: "string" },
    caption: { type: "string" },
    slides: { type: "array", items: SLIDE_SCHEMA },
    changes: { type: "string" },
  },
  required: ["title", "post_type", "purpose", "caption", "slides", "changes"],
  additionalProperties: false,
};

export const SLIDE_RULES = `How to write a carousel:
- Bryan's voice: plain, practical and warm, like he explains things on site; "we" for Urban Makers. \
No hype, no emoji on the slides, no trade jargon a homeowner wouldn't use. English only.
- Slide 1 (role cover) is the hook: a heading of at most 9 words that makes someone stop and swipe \
(a promise, a tension or a question); body empty or one short line.
- Then 4 to 8 point slides (role point), one idea each: a heading of at most 8 words and a body of \
at most 30 words, specific and useful (what to do and why). Each slide makes sense on its own, so \
the post is worth saving. Use a number only when it's a well-known fact or Bryan said it.
- The last slide (role ask) is the call to action, fitting the post's kind (the content plan lists \
the ask for each kind). An ask that offers something (a Work with us post, or any other) uses ONLY \
an offer listed in the content plan, worded as listed; otherwise ask people to save, share, follow \
or comment. Never invent a price, discount, promotion, deadline, freebie or offer.
- Never name a place (estate, street, condo, block, town) or a client: where clients live stays \
private. Never invent facts about a project; write [placeholder] where the team must fill something in.
- Pictures come only from Urban Makers' own footage and photos, or an AI concept picture drawn for \
the slide; never anyone else's images. For each slide: keep_image is the number of a current slide \
whose picture this slide keeps (someone picked it; keep it with the point it illustrates when you \
rewrite), else 0. clip_id and at: a still from one of the clips listed (at = the moment in seconds) \
that shows exactly what the slide says, else 0 and 0. Never use a clip just to fill a slide: \
text-only slides read well. image_idea: one short line on what picture would suit, for the team \
("close-up of the window seat's drawer"), or "".
- concept: true to illustrate a slide with a new AI concept picture instead, only when the prompt \
says concept pictures are available (otherwise always false). Studio draws it when Bryan presses \
Make this and marks it "Concept"; image_idea then says what it shows (the room, the idea, the \
materials), and keep_image, clip_id and at are 0. Use concepts mainly in Ideas and Know-how posts, \
for a design idea no clip shows. A real still or photo always beats a concept: where the footage has \
a real example, use it (a concept can sit beside it). Never in a Finished homes post, which shows \
only real work. A concept slide's words never say it's a home Urban Makers built or a client's home.
- caption: a ready-to-edit Instagram caption in Bryan's voice that adds to the slides rather than \
repeating them, ending with the same ask, with 3 to 5 hashtags (the caption rule below).`;

export const SLIDES_SYSTEM = `You are the content producer for Bryan's Instagram account (Urban Makers), \
writing one carousel post with the team: a set of slides people swipe through.

{brands}

Bryan's playbook (his voice and story rules):

<playbook>
{playbook}
</playbook>

The content plan (the kinds of post, this month's mix and the offers):

<content_plan>
{content_plan}
</content_plan>

` + SLIDE_RULES + `

Writing:
- Apply the team's request and read their comments. With no request, write the whole carousel \
from the idea (or make the current slides complete and sharp).
- title: the post's working title. post_type: its current kind when it has one (the team changes \
kinds themselves); otherwise the kind that fits. purpose: who it's for and what they should do \
after swiping (one or two sentences).
- changes: one or two sentences on what you wrote or changed, for the team.`;
