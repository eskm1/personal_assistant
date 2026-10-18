// Carousels (CONTRACT "Carousels"): the slides of one post, as pure functions over rows so
// they're testable without a database. Claude's answer is never trusted: roles, lengths and
// pictures are checked here (a picture is only ever one the team picked, or a still from a
// clip Studio has).
import { KINDS, MAX_SLIDES, POST_TYPES, SLIDE_ROLES } from "./prompts.ts";
import { text } from "./plan.ts";

export type SlideRole = typeof SLIDE_ROLES[number];
export const IMAGE_SOURCES = ["text", "clip", "photo", "concept"] as const;
export type ImageSource = typeof IMAGE_SOURCES[number];

/** A slide's picture: text only, a still from a library clip, a photo uploaded to the Studio
 * machine, or (a later update) an AI concept image. thumb_path: a preview in "thumbs". */
export interface SlideImage {
  source: ImageSource;
  clip_id?: number;
  at?: number;
  photo_id?: string;
  thumb_path?: string | null;
  note?: string;
}

export interface Slide {
  key: string;
  role: SlideRole;
  heading: string;
  body: string;
  image: SlideImage;
}

export interface ClipRow {
  id: number;
  title: string | null;
  duration: number | null;
  thumb_path: string | null;
  project_id?: number | null;
  verdict?: string | null;
  usable?: boolean | null;
}

const asObject = (v: unknown): Record<string, unknown> =>
  v && typeof v === "object" && !Array.isArray(v) ? v as Record<string, unknown> : {};

export const newKey = (): string => crypto.randomUUID().replaceAll("-", "").slice(0, 10);

const THUMB = /^(clips|takes|stills|photos)\/[A-Za-z0-9_.-]+$/;

/** A stored picture, cleaned (what the database's valid_slides() accepts). */
export function cleanImage(v: unknown): SlideImage {
  const i = asObject(v);
  const source = (IMAGE_SOURCES as readonly string[]).includes(i.source as string) ? i.source as ImageSource : "text";
  const out: SlideImage = { source };
  if (source === "clip" && typeof i.clip_id === "number" && Number.isInteger(i.clip_id)) {
    out.clip_id = i.clip_id;
    out.at = typeof i.at === "number" && Number.isFinite(i.at) ? Math.max(0, Math.round(i.at * 10) / 10) : 0;
  } else if (source === "photo" && typeof i.photo_id === "string" && /^[A-Za-z0-9-]{1,64}$/.test(i.photo_id)) {
    out.photo_id = i.photo_id;
  } else if (source === "clip" || source === "photo") {
    return { source: "text", ...(text(i.note, 300) ? { note: text(i.note, 300) } : {}) };
  }
  if (typeof i.thumb_path === "string" && THUMB.test(i.thumb_path) && i.thumb_path.length <= 200) {
    out.thumb_path = i.thumb_path;
  }
  const note = text(i.note, 300);
  if (note) out.note = note;
  return out;
}

/** The slides stored on an idea, cleaned (anything unreadable is left out). */
export function storedSlides(v: unknown): Slide[] {
  if (!Array.isArray(v)) return [];
  return v.slice(0, MAX_SLIDES).map((raw) => {
    const s = asObject(raw);
    return {
      key: text(s.key, 40) || newKey(),
      role: (SLIDE_ROLES as readonly string[]).includes(s.role as string) ? s.role as SlideRole : "point",
      heading: text(s.heading, 200),
      body: text(s.body, 800),
      image: cleanImage(s.image),
    };
  });
}

const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;

/** How a slide's picture reads in the prompt. */
export function imageText(img: SlideImage, clips: Map<number, ClipRow>): string {
  if (img.source === "clip" && img.clip_id != null) {
    const c = clips.get(img.clip_id);
    return `a still from clip ${img.clip_id}${c?.title ? ` ("${c.title}")` : ""} at ${img.at ?? 0}s`;
  }
  if (img.source === "photo") return "a photo the team uploaded";
  if (img.source === "concept") return "an AI concept image";
  return "text only";
}

export interface CarouselIdea {
  title: string;
  post_type?: string | null;
  purpose: string;
  caption: string;
  hook?: string;
  cta?: string;
}

export function slidesPrompt(
  project: { name: string; brief: string; standing?: string | null } | null,
  idea: CarouselIdea,
  slides: Slide[],
  clips: ClipRow[],
  comments: { author_name: string; body: string }[],
  request: string,
  projectId?: number,
): string {
  const byId = new Map(clips.map((c) => [c.id, c]));
  const slideLines = slides.map((s, i) =>
    `  ${i + 1}. [${s.role}] ${s.heading || "(no heading)"}` + (s.body ? ` | ${s.body}` : "") +
    ` | picture: ${imageText(s.image, byId)}` + (s.image.note ? ` (idea: ${s.image.note})` : "")
  );
  const clipLine = (c: ClipRow) =>
    `  clip ${c.id}: "${c.title || "untitled"}", ${c.duration ? mmss(c.duration) : "?"}` +
    (projectId && c.project_id !== projectId ? " (another project)" : "");
  return `Project: ${project?.name ?? "-"}${project?.standing ? " (not one client's renovation)" : ""}. ` +
    `Brief: ${project ? project.brief || "-" : "-"}\n\n` +
    "The carousel now:\n" +
    `  title: ${idea.title}\n  kind: ${idea.post_type && KINDS[idea.post_type] ? idea.post_type : "(none yet)"}\n` +
    `  purpose: ${idea.purpose || "-"}\n  caption: ${idea.caption || "-"}\n` +
    (!slides.length && (idea.hook || idea.cta) ? `  hook: ${idea.hook || "-"}\n  ask: ${idea.cta || "-"}\n` : "") +
    "  slides:\n" + (slideLines.join("\n") || "  (none yet)") + "\n\n" +
    "Clips from Urban Makers' own footage you may take stills from (id, title, length):\n" +
    (clips.map(clipLine).join("\n") || "  (none yet: use text-only slides)") + "\n\n" +
    (comments.length
      ? "Team comments (newest first):\n" +
        comments.map((c) => `  ${c.author_name || "someone"}: ${c.body}`).join("\n") + "\n\n"
      : "") +
    `The team's request: ${request.trim() || "(none: write the whole carousel)"}`;
}

export interface SlidesPlan {
  slides: Slide[];
  idea: { title: string; post_type: string | null; purpose: string; caption: string; hook: string; cta: string };
  changes: string;
}

/** Claude's slides as stored slides: one cover first, the ask last, pictures only the team's
 * own (a kept picture, or a still from a listed clip), at most MAX_SLIDES. */
export function planSlides(
  result: unknown,
  current: Slide[],
  clips: ClipRow[],
  fallbackTitle: string,
  makeKey: () => string = newKey,
): SlidesPlan {
  const r = asObject(result);
  const byId = new Map(clips.map((c) => [c.id, c]));
  let slides: Slide[] = [];
  for (const raw of Array.isArray(r.slides) ? r.slides.slice(0, MAX_SLIDES * 2) : []) {
    const s = asObject(raw);
    const heading = text(s.heading, 200), body = text(s.body, 800);
    if (!heading && !body) continue;
    const note = text(s.image_idea, 300);
    const keep = typeof s.keep_image === "number" ? Math.round(s.keep_image) : 0;
    const clip = typeof s.clip_id === "number" ? byId.get(s.clip_id) : undefined;
    let image: SlideImage;
    if (keep >= 1 && keep <= current.length && current[keep - 1].image.source !== "text") {
      image = { ...current[keep - 1].image };
      if (note && !image.note) image.note = note;
    } else if (clip) {
      const at = typeof s.at === "number" && Number.isFinite(s.at) ? s.at : 0;
      image = {
        source: "clip",
        clip_id: clip.id,
        at: Math.round(Math.min(Math.max(at, 0), clip.duration || at) * 10) / 10,
      };
      if (clip.thumb_path) image.thumb_path = clip.thumb_path; // its middle frame, until Studio grabs the moment
      if (note) image.note = note;
    } else {
      image = note ? { source: "text", note } : { source: "text" };
    }
    const role = (SLIDE_ROLES as readonly string[]).includes(s.role as string) ? s.role as SlideRole : "point";
    slides.push({ key: makeKey(), role, heading, body, image });
  }
  if (slides.length) {
    // One cover, first.
    const c = slides.findIndex((s) => s.role === "cover");
    const cover = c >= 0 ? slides.splice(c, 1)[0] : slides.shift()!;
    cover.role = "cover";
    // One ask, last (when there is one).
    let ask: Slide | null = null;
    for (let i = slides.length - 1; i >= 0; i--) {
      if (slides[i].role === "ask") {
        ask = slides.splice(i, 1)[0];
        break;
      }
    }
    for (const s of slides) s.role = "point";
    const points = slides.slice(0, MAX_SLIDES - 1 - (ask ? 1 : 0));
    slides = [cover, ...points, ...(ask ? [ask] : [])];
  }
  const ask = slides.length > 1 && slides[slides.length - 1].role === "ask" ? slides[slides.length - 1] : null;
  const postType = (POST_TYPES as readonly string[]).includes(r.post_type as string) ? r.post_type as string : null;
  return {
    slides,
    idea: {
      title: text(r.title, 300) || fallbackTitle || "Untitled carousel",
      post_type: postType,
      purpose: text(r.purpose, 2000),
      caption: text(r.caption, 4000),
      hook: slides[0]?.heading ?? "",
      cta: ask ? text(`${ask.heading}${ask.body ? ` ${ask.body}` : ""}`, 500) : "",
    },
    changes: text(r.changes, 1000),
  };
}
