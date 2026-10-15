// Prompts and JSON schemas ported from studio/plan.py (tuned there; keep the two in step).

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
export const FORMATS = ["talking_head", "day_in_the_life", "compilation"] as const;

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

const IDEA_FIELDS: Schema = {
  arc: { type: "string" },
  title: { type: "string" },
  brand: { type: "string", enum: [...BRANDS] },
  format: { type: "string", enum: [...FORMATS] },
  hook: { type: "string" },
  purpose: { type: "string" },
  cta: { type: "string" },
  caption: { type: "string" },
  est_seconds: { type: "number" },
  music: MUSIC_SCHEMA,
};
const IDEA_REQUIRED = ["arc", "title", "brand", "format", "hook", "purpose", "cta", "caption", "est_seconds", "music"];

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
- cta: the call to action at the end ("Save this for your reno", "DM us KENSINGTON", ...).
- caption: a ready-to-edit Instagram caption in Bryan's voice with 3 to 6 hashtags.
- shots: the storyboard, one entry per shot, in playback order, each something the team can \
actually film on site. kind: talking (to camera), broll (work happening), detail (close-up of a \
material, fixing, finish), before_after, reaction, screen (a drawing, render or phone screen). \
framing: wide / medium / close / detail / pov / overhead. camera: which of the team's cameras \
films it ({cameras}). description: what the camera sees, \
specific enough to film without asking ("Close-up: trowel spreading grey membrane into the \
wall-floor corner, slow pan left"). line: what's said over it ("" if nothing). on_screen: text on \
screen ("" if none). seconds: rough length in the final cut. why: what this shot does for the story. \
Plan B-roll and detail shots to cover talking, as the playbook says. Usually 4 to 10 shots.
- music: the mood, tempo and a short search phrase for the music bed. For personal posts this \
becomes a note on what to pick from Instagram's library.
- Never invent facts, prices, timelines or client details. Write [placeholder] where the team \
must fill something in.
- keep_id: 0 for a new shot.`;

export const ORGANISE_SYSTEM = `You are the content producer for Bryan's Instagram accounts. The team is \
planning posts before filming. Turn their ideas into organised, filmable plans.

{brands}

Bryan's playbook (his editing and story rules; plan shots and structure that let the editor follow it):

<playbook>
{playbook}
</playbook>

` + RULES + `

How to organise:
- Group ideas into arcs: content series a viewer would follow (a topic, a phase of the job, a \
recurring format). Reuse an existing arc by giving its key exactly ("arc:12"); make a new one \
with a short key of your own ("new:waterproofing") and list it in arcs. Don't list existing arcs.
- Keep every idea the team wrote (merge only true duplicates), sharpen it, and add a few strong \
ones the project obviously supports if the team asked for more. Don't repeat existing ideas.
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

` + RULES + `

Refining:
- Apply the team's request. If there's none, make the plan complete and filmable: a sharp hook, \
a clear structure, B-roll covering the talking, a strong end.
- Shots that are already filmed or have footage are marked; keep those (keep_id = their id) \
unless the request says otherwise, and plan around them. Reusing an unfilmed shot keeps its \
comments and history, so give its id in keep_id too; new shots get keep_id 0.
- arc: return the idea's current arc key unchanged.
- changes: one or two sentences on what you changed, for the team.`;

export function camerasText(): string {
  return Object.entries(CAMERAS).map(([k, v]) => `${k} = ${v}`).join("; ");
}

/** Fill {brands}, {playbook} and {cameras} like plan._ask (brands/playbook from the context table). */
export function renderSystem(template: string, brands: string, playbook: string): string {
  // Function replacers: the texts may contain "$" sequences that string replacements would expand.
  return template.replaceAll("{brands}", () => brands.trim()).replaceAll("{playbook}", () => playbook.trim())
    .replaceAll("{cameras}", () => camerasText());
}
