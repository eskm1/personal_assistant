# Studio online planning: the contract

The planning board moves online so the team can plan while the Studio laptop is off.
Three parts share one Supabase project; this file is the agreement between them.
The database schema is `supabase/migrations/20261015000000_planning.sql` (read it first:
it is the source of truth for tables, columns, RLS and triggers).

```
studio/cloud/
  CONTRACT.md                 this file
  supabase/
    config.toml               Supabase CLI project (local stack for tests; `supabase link` for prod)
    migrations/*.sql          schema, RLS, realtime, storage bucket
    functions/
      _shared/                prompts, schemas, Claude + DB helpers shared by the functions
      plan-organise/index.ts  brain-dump -> arcs + ideas, then one develop job per new idea
      plan-develop/index.ts   one idea's full plan + storyboard (also "Refine with Claude"), or a carousel's slides
      team/index.ts           owner: list members, invite by email, remove
  planner/                    the static web app Netlify serves (no framework, no bundler)
    index.html, app.js, style.css
    vendor/supabase.js        supabase-js UMD bundle, pinned and committed (no CDN at runtime)
    config.js                 GENERATED: window.STUDIO_CONFIG = {supabaseUrl, supabaseAnonKey, studioUrl}
    build-config.mjs          writes config.js from env (Netlify build command)
    netlify.toml              build + headers
studio/cloud_sync.py          the laptop side (Python, runs inside worker.py)
```

## Who writes what

| Data | Written by | Notes |
|---|---|---|
| projects, arcs, ideas, shots, votes, comments | team in the planner (RLS: members) | ideas.decision/status/production: owner or service role only (trigger) |
| ideas + shots from Claude | Edge Functions (service role) | |
| clips | laptop (service role) | summary of each library clip + `thumb_path` |
| takes | laptop (`source='auto'`, matched while tagging) and team (`source='manual'`, attach in the planner) | team may delete any take; laptop uploads `thumb_path` for every take |
| jobs | Edge Functions (organise/develop), team (`music`), owner (`make`) | see Jobs |
| context (`brands`, `playbook`) | laptop | read by the Edge Functions' prompts |
| laptop_status | laptop heartbeat every sync | planner shows "Studio laptop on / off since …" |
| ideas.music (shortlist) | laptop, as the result of a `music` job | team picks with `music.chosen` (plain update) |
| ideas.production, ideas.status='made' | laptop, after a `make` job | `{id, status, version, updated_at}` of the laptop production |

Storage bucket `thumbs` (private): `clips/<clip_id>.jpg`, `takes/<take_id>.jpg`. The planner
shows them with `supabase.storage.from('thumbs').createSignedUrls(paths, 3600)`.

## Jobs

Row in `jobs`: `kind`, `runner`, `project_id`/`idea_id`, `payload`, `state` queued -> running ->
done | failed, `result`, `error`, timestamps. The planner subscribes to `jobs` (realtime) to show
progress.

| kind | runner | queued by | payload | result |
|---|---|---|---|---|
| organise | cloud | `plan-organise` function | `{text}` | `{ideas, arcs, notes, idea_ids}` |
| develop | cloud | `plan-develop` function, or `plan-organise` (one per new idea) | `{request}` | `{changes}` |
| music | laptop | any member (insert) | `{}` | the idea's `music` (also written to ideas.music) |
| make | laptop | owner (insert) | `{caption?}` | `{production_id}` |
| carousel | laptop | owner (insert), carousel ideas only | `{caption?}` | `{production_id}` (see "Carousels") |
| ideas | laptop | any member (insert) | `{}` (project_id set) | see "Ideas from new footage" |

A `cloud` job still `running` 4 minutes after `started_at` died with its function (Supabase
free plan: 150 s wall clock) and is shown as failed ("took too long, try again").
A `laptop` job stays `queued` while the laptop is off; the planner says so using `laptop_status`.

## Edge Functions (HTTP)

All take/return JSON, require `Authorization: Bearer <user JWT>` of a member (the planner's
`supabase.functions.invoke` does this), answer fast and finish the work in
`EdgeRuntime.waitUntil`, writing results to the database.

- `POST plan-organise {project_id, text}` -> `{job_id}`. Claude (effort medium, streamed) sorts
  the dump into arcs (reusing existing arcs by key `arc:<id>`) and ideas (title, brand, format,
  hook, purpose, cta, caption, est_seconds, music {mood, tempo_bpm, search}) **without shots**,
  inserts them, then queues a `develop` job (state `queued`, `requested_by` the caller) per new
  idea. It does NOT call other functions (Supabase's new secret keys aren't JWTs, so
  function-to-function auth is fragile). The planner starts queued develop jobs: whenever it sees
  `develop` jobs in state `queued` for the open project (realtime or on load), it calls
  `plan-develop {job_id}` for each, at most 4 at a time. Each storyboard runs in its own 150 s
  window; jobs left queued resume the next time anyone opens the board.
- `POST plan-develop {idea_id, request?}` -> `{job_id}` (creates the job), or `{job_id}` -> runs an
  existing queued develop job. Any member may call either. The function claims a queued job
  atomically (`update ... set state='running' where id=$1 and state='queued' returning *`); if
  nothing was claimed (already running/done) it answers `{job_id, already: true}`.
  Claude (effort high, streamed) returns the full idea + storyboard; shots that are filmed or have
  takes are kept (`keep_id`), unfilmed shots not kept are deleted, new ones inserted, positions 1..n.
- `GET team` (owner) -> `{members: [{id, name, email, role, last_sign_in_at}]}`;
  `POST team {action: 'invite', email, name}` -> sends a Supabase invite email whose link lands on
  the planner (`redirectTo` = planner URL), where the person sets a password;
  `POST team {action: 'remove', user_id}` (not yourself).

Prompts and JSON schemas are ported from `studio/plan.py` (`_RULES`, `_ORGANISE_SYSTEM`,
`_DEVELOP_SYSTEM`, `_SHOT`, `_IDEA`, `CAMERAS`), with `{brands}` and `{playbook}` read from the
`context` table. Model `claude-opus-5-5`, `betas: ["server-side-fallback-2026-07-01"]`,
`fallbacks: "default"`, `output_config.format` json_schema, streamed (`messages.stream` +
`finalMessage()`). Secrets: `ANTHROPIC_API_KEY` (and optional `ANTHROPIC_BASE_URL` for tests).
Cost of every call is logged into `jobs.result.usage` `{input_tokens, output_tokens, cost_usd}`
(Opus 5.5: $4 / $20 per million).

## The laptop (cloud_sync.py)

Enabled when `.env` has `STUDIO_SUPABASE_URL`, `STUDIO_SUPABASE_SERVICE_KEY` (and
`STUDIO_PLANNER_URL` for links). Talks to PostgREST (`/rest/v1`) and Storage (`/storage/v1`) with
the service key; no extra Python packages. Key headers: a new-style secret key (`sb_secret_...`,
not a JWT) goes ONLY in the `apikey` header; a legacy `service_role` JWT (`eyJ...`) goes in both
`apikey` and `Authorization: Bearer`. Runs from the worker loop about every 30 s:

1. heartbeat `laptop_status` (last_seen, version);
2. push `context` (brands.md, playbook.md) when changed;
3. pull projects, arcs, ideas, shots, takes into the local SQLite planning tables, matched by a
   `remote_id` column (remote wins for plan fields; rows gone remotely are deleted locally), so the
   existing tagger shot matching (`plan.open_shots_text`, `plan.save_matches`) and `plan.make` keep
   working on local ids;
4. push clips (summary + `clips/<id>.jpg`) for tagged clips not yet pushed or changed, and push new
   local auto takes (then `takes/<id>.jpg`), storing their `remote_id`;
5. claim `laptop` jobs (`update ... set state='running' where state='queued'`), run `music`
   (`plan.shortlist_music`) and `make` (`plan.make`), write results; push production status of made
   ideas (draft ready, approved…).

`worker.py --cloud-import` pushes an existing local plan (projects, arcs, ideas, shots, takes) up
once. When the cloud is enabled, the local dashboard's Plan tab becomes a link to the planner.

## Reference guides and the style guide (migration 20261017000000_references.sql)

The team keeps a library of reference reels in the planner (**References** tab): their own
best-performing reels (`side='ours'`) and other accounts' (`side='inspiration'`), each with a
plain-language note on why it works (`why`), for a brand (default `urban_makers`). References
are retired (status `retired`), not deleted, when they stop representing the style.

| Data | Written by | Notes |
|---|---|---|
| reference_videos | members in the planner (links); the laptop for files dropped in `Studio Inbox\References\Ours` / `...\Inspiration` (`file_name`, `url` null) | url must be https |
| reference_analysis | laptop only | a `pending` row is created by trigger for each reference; laptop sets running -> done/failed with `measured`, `look`, `stills` (thumbs bucket `refs/<id>/<n>.jpg`); `analyser` version bump = re-analyse |
| style_guides | laptop inserts drafts; owner approves (status -> active, previous active -> archived by trigger), rejects, or edits `content` before approving | `reference_ids` = the active, analysed references it was built from; `added_ids`/`removed_ids` vs the previous active guide |
| jobs kind `style` | any member queues "Update style guide" (runner laptop) | result `{guide_id, version}` |

**Laptop (cloud_sync, each tick):** analyse pending references (download -> `style_study.measure`
-> stills -> `style_study.look`, upload stills, delete the download), at most 2 per tick so ingest
isn't starved; then, if the set of active analysed references of a brand differs from the latest
draft-or-active guide's `reference_ids` and nothing is pending, draft a new guide automatically
(also on a `style` job): Claude gets the previous active guide, every active reference's numbers,
look and why-note, and which were added/removed (with their notes), and writes `content`,
`report`, `changes`. When a guide becomes active, the laptop writes its `content` into
`playbook.md` between the style-study markers (replacing an earlier one) and remembers which
guide id it applied.

**Planner:** References tab: add link (+ side, why, brand), edit why, retire/restore, see
analysis state and a strip of stills plus key numbers (shot length, first cut, words/s, music
under voice). Style guide view: the active guide (Markdown rendered safely as text), its report,
version history; a banner when references changed since it ("2 added, 1 retired"); the newest
draft with its "What changed" and Approve / Reject / Edit (owner); "Update style guide" queues a
`style` job (shows waiting-for-laptop when it's off).

## Shoot plan, uploads to a shot, post folders (migration 20261018000000_shoot_plan.sql)

### Shoot plan (planner only; no Studio-machine work except the `place` job)

- `phases`: a project's renovation phases (name, `starts_on`/`ends_on` dates, `position`). Members edit.
- `site_days`: the days someone is on site (`day`, `phase_id`, `who` free text, `note`), one per project per date.
- `shots.phase_id`: the phase whose work the shot shows (Claude places them with the `place` job;
  the team can move them). `shots.shoot_on`: optional, pins a shot to one site day.
- A shot is **due** on a site day when `shoot_on = day`, or `shoot_on` is null and its `phase_id` is the
  day's `phase_id`. Only shots of ideas with `decision = 'go'` and `status <> 'dismissed'` are planned.
- **Filmed** = `filmed_at` set, or the shot has a take.
- **Last chance**: a site day that is the last (by date) site day of its phase while that phase still has
  unfilmed due shots.
- **Not on a site day yet**: Go shots with neither `phase_id` nor `shoot_on`, or whose phase has no site day.

### Jobs

| kind | runner | queued by | payload | result |
|---|---|---|---|---|
| place | laptop | any member (insert) | `{}` (project_id set) | `{placed, unplaced}`; writes `shots.phase_id` for the project's unplaced shots of Go and proposed ideas (Claude picks the phase each shot's work happens in, or none) |

### Uploading footage to a shot (planner -> Studio machine, HTTP)

Base URL: the Studio dashboard (`config.js` `studioUrl`, default `https://studio.theurbanmakers.com`;
also in `laptop_status.info.dashboard_url`). Every request carries the planner session:
`Authorization: Bearer <supabase access_token>`. The Studio machine checks it with Supabase
(`GET /auth/v1/user`) and that the user has a profile (a member). CORS: the planner's origin
(`STUDIO_PLANNER_URL`, plus any in `STUDIO_UPLOAD_ORIGINS`, comma-separated) may call these, with the
`Authorization` and `Content-Type` headers, methods GET/POST/PUT/DELETE.

| Request | Body | Answer |
|---|---|---|
| `POST /api/uploads` | JSON `{shot_id, name, size, type}` | `201 {id, offset, chunk}`; the same user + shot + name + size with an unfinished upload gets that one back (resume). 401 bad session, 403 not a member, 404 no such shot, 413 over 8 GB, 415 not a video, 507 disk full |
| `PUT /api/uploads/<id>?offset=<n>` | raw bytes (`application/octet-stream`), at most 16 MB | `{offset}`; 409 `{offset}` when n isn't where the server is; at `offset = size` the server finishes: `{offset, state: "received"}` |
| `GET /api/uploads/<id>` | | `{id, offset, size, state}`, plus `clip_id` / `error` once ingested or failed |
| `DELETE /api/uploads/<id>` | | `{ok: true}`; cancels an unfinished upload (409 once finished) |

Errors are `{error}`; besides those above: 429 rate limited, 502/503 Supabase unreachable or the
migration missing, 409 `{offset, state}` for a PUT after the upload finished. Only the uploader or an
owner can see or act on an upload (others get 404). Unfinished uploads fail after 3 days.

`chunk` is the size the planner should send per PUT (8 MB). The Studio machine mirrors each upload in
`shot_uploads` (it inserts the row at `POST`, state `uploading`; `received` when the file is complete and
in the inbox; `ingested` with `clip_id` once tagged; `failed` with `error`). A received file goes to
`Inbox/<project folder>/<idea folder>/<name>` and is pinned to its shot: when ingested, Studio makes a
`manual` take for that shot (note "Uploaded to this shot") and sets `shots.filmed_at` if empty.

### Post folders

For each Go idea (not dismissed) of an active project, the Studio machine makes an inbox folder
`<project folder>/<idea folder>/` (locally, and in OneDrive on a server) and writes its name to
`ideas.folder`. It never renames or deletes one: a renamed idea keeps its folder. A clip ingested from a
post folder is matched only against that idea's shots, and `clips.idea_id` is set. Clips of a project
with no take are the planner's "Needs a home" tray; attaching one is a `manual` take (insert), moving one
is delete + insert.

### Signing in, and the planner inside the dashboard

The Studio dashboard also accepts the planner's email and password (checked with Supabase), so the team
has one login. The planner role (`owner`/`team`) carries over.

Only emails at `STUDIO_ALLOWED_EMAIL_DOMAINS` (default `theurbanmakers.com`; `*` = anyone) may sign in,
accept an invite or upload; owner accounts always may. The `team` Edge Function invites only those
domains (secret `ALLOWED_EMAIL_DOMAINS`, same default).

The Studio machine also serves this planner at `<dashboard>/plan/` (behind the dashboard login), with a
generated `/plan/config.js` (`studioUrl: ""` = same origin; `problem` set when it can't find the public
key). `GET /api/planner-session` (dashboard cookie, same origin) returns
`{access_token, refresh_token, expires_at, expires_in, token_type}` for the signed-in user, which the
planner passes to `supabase.auth.setSession`; 401 signed out, 403 `{error}` when the user isn't a planner
member or hasn't proven the planner email (a team account must have signed in once with the planner's
password; owners always may). `GET /api/nav` (dashboard cookie) returns `{review: <drafts ready for
review>, studio: {on, last_sync}}` for the shared top bar's Review badge; the planner polls it about
once a minute under /plan/ (401 there signs the planner out too). The top bar itself (`shell.js`,
`shell.css`) lives in the planner's folder and is served to the dashboard as `/static/shell.*`; so are the
shared look, `theme.css` (the Urban Makers tokens and base controls, from the quote tool's palette), and its
fonts (`fonts/*.woff2`, IBM Plex Sans / Mono and Barlow, OFL, served as `/static/fonts/<name>`; CSP `font-src
'self'`). The bar is **Plan · Ideas · Review · More**: Plan = `#/` (Plan's home), Ideas = `#/ideas` (the current
project's board; `#/project/<id>`, `#/project/<id>/shoot`, `#/project/<id>/footage`), More = References, Style
guide, Targets and offers, Client questions, Today on site (`#/today/<id>`) and Review's Library.

**Plan's home** reads everything from Supabase (ideas across active projects, arcs, site days, phases, shots,
style guide / taste drafts, the month) and, under /plan/ only, `GET /api/home` (dashboard cookie) =
`{review: [{id, title, format, duration, version, thumb, slides_count, fixed, updated}], failed: [{id, title,
error}]}` (not archived). On Netlify, or when that call fails, drafts come from `ideas.production` (`status
'ready'` / `'failed'`) and their rows link to the Studio address.

## Ideas from new footage (migration 20261019000000_footage_ideas.sql)

Ideas live in ONE place, the planner. The Studio machine (`footage_ideas.py`, inside the sync, after
clips are pushed) writes them there with the service key; the local mirror picks them up on the next pull.

**Automatic.** When new tagged clips have arrived and then no clip has for `STUDIO_IDEAS_AFTER_MINUTES`
(default 20; 0 = off), nothing is processing and the inbox is empty, the new clips are grouped by project
(the clip's project; clips outside any project go to the standing project named **Everyday posts**, folder
`Everyday posts`, made if missing). One Claude call per project (effort medium) gets the brief, its open
ideas (`status = 'new'`, `decision in (proposed, go)`) with their shots and takes, the new clips (title,
summary, moments, transcript excerpt, quality, duration) and up to 30 earlier clips of the project. It writes:

| What | Rows |
|---|---|
| new footage for a planned shot | `takes` (`source 'auto'`, `note 'Found in new footage'`, start/end inside the clip) |
| new footage that needs a new shot of a planned idea | `shots` (appended, next position) + its take |
| up to 3 new ideas | `arcs` titled **From your footage** (made once per project, `position` before the others), `ideas` in it (`decision 'proposed'`, `status 'new'`, `created_by` null), their `shots` (positions 1..n), and a take for each shot that has footage; shots with no take are still to film |

Unusable clips are skipped. Each run is recorded on the Studio machine (catalog `idea_runs`, `clips.ideas_run`),
so a clip is read once; a failed run is retried at the next quiet period, at most 3 times per clip.
`laptop_status.info.footage_ideas` = `{after_minutes, waiting_clips, last_run: {at, state, project, trigger,
new_ideas, attached, error} | null}`.

**Library ideas move once.** With planning online, Review has no ideas any more: `GET /api/board` returns no
`kind: "idea"` items and no `ideas_job` (`ideas_online: true`); `POST /api/ideas/generate` and
`POST /api/idea/<id>/adopt` answer `410 {"error": "Ideas live in the planner now"}`. Library ideas still
`status 'new'` are written to the planner (project of their first clip, else Everyday posts; arc From your
footage; segments -> shots with takes, `note 'From Suggest posts'`; wish list -> shots to film) and marked
the planner's locally (`origin 'plan'`, `remote_id`).

### Jobs

| kind | runner | queued by | payload | result |
|---|---|---|---|---|
| ideas | laptop | any member (insert), `project_id` set | `{}` | `{new_ideas, attached, clips, idea_ids, notes}`: the project's usable clips of the last 14 days not yet read (all of them when none are new); fails with "No usable footage from the last 14 days in this project yet" |

### Clip quality on cards

`clips.verdict` stays `good | check | retake` and `clips.flags` `[{level, text, tip}]`. New: `clips.usable`
(boolean, written by the Studio machine) and `clips.flagged` (generated: `verdict = 'retake'` or
`usable = false`). Cards and thumbnails warn only when `flagged`; `check` and the tips belong in the
detail view. The dashboard's board items carry the same `flagged`.

## Content plan: kinds of post and the month's mix (migration 20261020000000_content_plan.sql)

Every post has a job, its **kind** (`ideas.post_type`), and the month has a mix Bryan sets (how many of each
kind). The month tracks what's planned and what's approved ("ready"); since the posting update, a post scheduled or
posted on Instagram counts in the month it goes out ("Posting to Instagram", below).

| value | label | stage | job | ask |
|---|---|---|---|---|
| `knowhow` | Know-how | Get found | teach something practical people save and share | save this / follow for more |
| `ideas` | Ideas | Get found | design thinking: layouts, zoning, light, colour | save this / which would you pick? |
| `onsite` | On site | Build trust | the craft and care while the work happens | follow this project |
| `finished` | Finished homes | Build trust | proof: portfolio, before/after, the owners | see the whole project / talk to us |
| `offer` | Work with us | Get enquiries | turn followers into enquiries | one of the offers |

The list lives in three places, kept in step: `studio/content_plan.py`, `functions/_shared/prompts.ts`,
`planner/js/core.js`.

**Schema.**
- `ideas.post_type` text null, one of the five. Not an owner-only field: anyone who can edit ideas sets it.
- `ideas.format` also allows `carousel` (carousels are built in a later step).
- `jobs.kind` also allows `carousel` and `topics` (their insert policies come with their own migrations:
  "Carousels", "Suggested topics").
- `projects.standing` text null unique: `everyday` (the Everyday posts project, footage_ideas.py) or `topics`
  (the standing **Topics** project for posts not tied to a client's renovation; the migration makes it, and
  marks an existing Everyday posts project).
- `content_plan`, one row (`id = 1`): `posts_per_week` (default 3), `mix` jsonb `{kind: monthly target}`
  (default `{"knowhow":4,"ideas":2,"onsite":3,"finished":2,"offer":2}`, integers 0..60, known kinds only),
  `offers` jsonb `[{name, details, ask}]` (at most 10; default Free consultation, Site visit, Floor plan
  review), `updated_by`, `updated_at` (stamped by trigger). RLS: members read; only the owner updates; nobody
  inserts or deletes. In the realtime publication.
- `content_month(as_of timestamptz default now()) -> jsonb` (members and the service role; runs with the
  caller's rights): `{month: "2026-10", starts_on, ends_on, posts_per_week, target, planned, ready, untyped,
  kinds: {<kind>: {target, planned, ready, short, ids}}}`. Months are Singapore time. A post counts for the
  month when it's **on its way** (decision Go, or made / in the edit, and not dismissed, not rejected, not
  approved yet, in an active project) or was **approved (or posted) this month** (`ideas.production.status`
  and `production.updated_at`, which the Studio machine writes). `ready` = approved this month. `untyped` =
  counted posts with no kind yet. The planner, the Edge Functions and the Studio machine all use this one
  function.

**Who writes the kind.**
- Claude: `plan-organise` (every new idea), `plan-develop` (only when the idea has none; a kind already set
  is kept), footage_ideas.py (new ideas from footage), plan.py (planning on the Studio machine). An unknown
  value is dropped (null).
- People: the planner's kind picker on the idea page and in New idea (optional).
- The Studio machine fills in the rest (`content_plan.backfill`, inside the sync): ideas with no kind, not
  dismissed, at least 10 minutes old, 20 per Claude call (effort low), one call per tick, written with
  `post_type=is.null` so a kind someone picked meanwhile is never overwritten. An idea Claude answers for
  without sorting is tried twice, then left for a person (`cloud_state.json` `post_type_tried`); a failed call
  waits an hour.

**What Claude is told.** Every planning prompt gets a content-plan section (`{content_plan}` in the
organise/develop systems; the user message in footage ideas): the five kinds with their job and ask, this
month's numbers per kind and which are short ("lean toward these; never force a post into a kind it isn't"),
and the offers: the ONLY things a Work with us post or any call to action may offer, never an invented
price, discount or promotion. Without the table (before the update) the defaults are used and no month.

**The local mirror** keeps `post_type` (catalog `ideas.post_type`). Before the migration is online the Studio
machine sends no `post_type` and doesn't backfill (`cloud_sync.check_content_plan`, rechecked every 10 min).

**Planner.** On Plan's home, the month as slots, one per targeted post (filled = planned, tinted outline = still
to plan, coloured by kind), "N of target posts planned" and "Still to plan: ..." with a link per kind to
`#/month/<kind>` (the posts counting this month and the ones waiting for a decision). `#/content-plan` (More,
"Targets and offers" on Plan): the targets
per kind with the total and per-week equivalent, the offers, and the five kinds explained; the owner edits,
the team reads. Cards show a coloured kind chip. Until the database update the month stays away and ideas
load without `post_type`.

## Carousels (migration 20261021000000_carousels.sql)

An idea is a Reel or a **carousel** (`ideas.format = 'carousel'`): 6 to 10 slides of 1080x1350 people swipe
through, with no shoot. English only; offers only from `content_plan`; never a place name; pictures only from
Urban Makers' own footage and photos, or AI concept pictures drawn for the slide (below).

**Schema.**
- `ideas.slides` jsonb null (null for a Reel), checked by `valid_slides()`: at most 12 of
  `{key, role: cover|point|ask, heading (<= 200), body (<= 800), image}`, with
  `image = {source: text|clip|photo|concept, clip_id, at, photo_id, thumb_path, note}`. `clip` = a still from a
  library clip at `at` seconds; `photo` = a photo uploaded to the Studio machine (`photo_id`, 32 hex); `concept` =
  an AI concept picture (see "Concept pictures"); `thumb_path` = a preview in the `thumbs` bucket
  (`clips/`, `takes/`, `stills/<clip>_<ms>.jpg`, `stills/concept_<key>_<a|b>.jpg`, `photos/<photo_id>.jpg`);
  `note` = what picture would suit (for a concept: what it should show).
  Anyone who can edit ideas edits slides (the planner writes the whole array).
- Policy "owner queues carousels": jobs insert, owner only, `kind = 'carousel'`, `runner = 'laptop'`,
  `state = 'queued'`, and `idea_id` must be a carousel.

| kind | runner | queued by | payload | result |
|---|---|---|---|---|
| develop | cloud | `plan-develop`, as for Reels | `{request}` | for a carousel: `{changes, slides, usage}` |
| carousel | laptop | owner (insert; "Make this" on a carousel) | `{caption?}` | `{production_id}` |

**Write slides = plan-develop.** On a carousel idea, `plan-develop` (and the develop job `plan-organise` queues
for a new carousel idea) writes slides instead of shots: Claude (effort high, `SLIDES_SYSTEM` with brands,
playbook and the content plan) gets the idea, its current slides with their pictures, the team's comments and
request, and up to 30 usable clips of the project plus 20 recent ones from elsewhere (no retakes). It returns
title, kind, purpose, caption and slides (`role, heading, body, keep_image, clip_id, at, image_idea`);
`planSlides()` (`_shared/slides.ts`) keeps one cover first and the ask last, keeps a picture someone picked
(`keep_image`), accepts a clip only from the list (its `thumb_path` = the clip's still until Studio takes the
exact one) and writes `ideas.slides`, `hook` (the cover), `cta` (the ask), `caption`, `purpose`, `post_type`
(only when empty). A made carousel (status made / production set) is refused: its slides change in Review.
A Reel never turns into a carousel through a refine. Organise may propose carousels (rarely, Know-how / Ideas /
Work with us); ideas from footage may too (cloud only), with their footage as the first slides' pictures and a
queued develop job to write the slides.

**Pictures (Studio machine, uploads.py; planner session, CORS as for uploads).**
- `POST /api/uploads {idea_id, name, size, type}` (JPEG, PNG or HEIC/HEIF, at most 60 MB) then the same PUTs as
  a video: once complete the photo is kept as `Photos/<photo_id>.jpg` (HEIC converted with pillow-heif, turned
  upright, at most 2400 px), its 540 px preview is uploaded to `photos/<photo_id>.jpg`, and the PUT (and GET)
  answer carries `photo: {id, thumb_path, width, height}`. 415 not a photo (or unreadable), 413 too big, 404 no
  such idea. Nothing is written to `shot_uploads`. The planner then sets the slide's image.
- `POST /api/uploads/stills {clip_id, at}` -> `{clip_id, at, taken, thumb_path}`: the sharpest of 7 frames within
  0.5 s of `at` in the original video (OpenCV), cached full size in `Stills/`, preview to `stills/<clip>_<ms>.jpg`.
- `GET /api/uploads/clips/<id>/moments` -> `{id, title, duration, moments, frames: [{at, src: data:image/jpeg}]}`.

**Making it (cloud_sync + carousel.py).** The Studio machine claims the `carousel` job, reads the idea's slides
fresh and files a production (`format 'carousel'`, `status 'planned'`, `plan.slides`), marks the idea made and
writes `ideas.production`. The editing queue (worker) lays it out: each slide's picture
(`slide_images.picture()`), one HTML page in the Urban Makers look (fonts bundled in `render/carousel/fonts`,
green #22302c, amber #f5a524), shot slide by slide to PNG by `render/carousel/shoot.mjs` (puppeteer-core with
HyperFrames' headless Chrome; every non-file request refused). The cover's words stay inside x 34..1046 (the
profile grid's 3:4 crop). Files: `Renders/carousel_<id>_v<n>/01.png ...`, `thumb.jpg`; `productions.slides` =
`{version, slides, files, layout, pictures, summary, notes_addressed}`. Then Studio's read (draft_review.py,
vision on every slide: cover, legibility, spelling, claims, the ask; fixes name a slide).

**Review.** Feedback first, the slides small (a swipeable tile on a computer, a slide section on a phone);
notes may name a slide (`feedback.slide`). Send back: Claude revises the slides from the open notes
(`carousel.revise`, keeps untouched slides and their pictures, changes the caption only when a note asks) and
lays out version n+1. After each version the Studio machine writes the slides (and caption) to the idea, so the
planner shows the draft's. `GET /api/production/<id>/download` = the zip (01.png ... caption.txt).
`/media/production/<id>/slide/<n>` = one slide. Approved: OneDrive `Studio Ready to post/<title> (<id>)/`
with 01.png ... and caption.txt (copied again when the slides or caption change). The month counts it like a
Reel (`ideas.production`).

## Concept pictures (no migration: `valid_slides()` already accepts `source 'concept'` and extra image keys)

A carousel slide can show an **AI concept picture** of a design idea, drawn by Google's Gemini image model
(`STUDIO_IMAGE_MODEL`, default `gemini-nano-banana-2.1`) over its REST API (`POST {STUDIO_GEMINI_URL}/models/
<model>:generateContent`, header `x-goog-api-key`, `generationConfig {responseModalities: ["IMAGE"], imageConfig
{aspectRatio: "4:5"}}`, the picture in `candidates[].content.parts[].inlineData`) with Studio's own key
`GEMINI_API_KEY` (Studio machine `.env` only; never logged, never online). Rules, in code and in the prompts:
every concept slide carries the **Concept** mark; a concept is never shown as Urban Makers' built work; never
in a **Finished homes** post (`post_type 'finished'`); a real still or photo is preferred where one exists;
Claude proposes concepts mainly for Ideas and Know-how; no words, logos, faces, real places or other brands in
a picture.

**A concept slide's image:** `{source: 'concept', note, brief, concept, options, chosen, round, thumb_path}` plus,
while waiting, `previous`/`change` (a redraw with a change) and `why` (why it's text for now). `brief` = the
picture's prompt (Claude); `concept` = its cache key (24 hex: sha256 of model, aspect, round, brief); `options` =
the options that passed Studio's check (`["a","b"]`, or one); `chosen` = the one on the slide. The planner and
the cloud only ever write `{source: 'concept', note}`; the rest is the Studio machine's (it writes the draft's
slides back to the idea, as for every carousel).

**Who can draw:** `laptop_status.info.concepts = {on, model, per_image_usd, options}` (the heartbeat; `on` =
the key is set). The planner offers "AI concept" (with "What should the concept show?") only when `on`, and
never on a Finished homes idea; otherwise "AI concept · needs the image key". `plan-develop` tells Claude whether
concepts are available (`conceptsLine`) and `planSlides(..., {concepts, postType})` accepts a new concept
(`concept: true` in SLIDE_SCHEMA) only then and never for Finished homes (a kept one becomes text there).

**Drawing (Studio machine, `concepts.prepare`, inside `carousel.run`: so only after Make this, Send back or
Redraw, all Bryan's):** one Claude call writes the briefs (slide words, note, kind, Singapore flat proportions,
the house style from the playbook), Gemini draws two options per concept slide (at most 6 slides), one Claude
vision call checks them all (warped or impossible furniture and rooms, lettering, faces or limbs, the wrong
room); a rejected one is drawn once more with the problem named and checked again; still wrong, that option
goes. No option left = the slide is laid out as text, the summary and Studio's read say why (a fix on that
slide). Gemini busy/down (429, 5xx, no picture, unreadable): one retry after a pause (Retry-After / RetryInfo,
else 5 s), then text for now and the next version tries again; a safety block isn't retried or redrawn. Files:
`Studio/Concepts/<key>_<a|b>.jpg` (passed) and `<key>.json` (brief, states, problems, previews); a layout or a
new version with the same brief never draws again. Every picture is logged in `usage` (`kind 'image'`,
`STUDIO_IMAGE_COST_USD`, default 0.034), shown next to Claude per draft and per month.

**Review (dashboard, owner):** `GET /api/production/<id>` slides carry `concept: {options: [{option, image}],
chosen, note, why, redraw}`; `GET /media/production/<id>/concept/<n>/<a|b>` = an option's JPEG;
`POST /api/production/<id>/slide/<n>/choose {option}` = lay the same version out again with that option (free);
`POST /api/production/<id>/slide/<n>/redraw {change?}` = two new pictures for that slide as the next version
(with a change, Claude rewrites the brief from the old one; without, the same brief, `round + 1`). A note on
Send back like "redraw slide 3, warmer" does the same through the revision (`redraw` in REVISE_SCHEMA's
slides). Both are owner-only and only for a draft waiting for review.

## Suggested topics (migration 20261022000000_topics.sql)

Studio suggests a few posts for the month's gaps by itself (`topics.py`, on the Studio machine, inside the sync,
after ideas from footage): one Claude call per run (effort medium, `usage` kind `topics`, ref `topics:<run id>`).

**When.** Weekly at `STUDIO_TOPICS_DAY` / `STUDIO_TOPICS_HOUR`, Singapore time (default Monday 8; `off` = no
weekly run), when no run has finished since 12 hours before that slot (so a run right after an update counts as that week's); or "the month falls behind" (`STUDIO_TOPICS_BEHIND`, on):
past the 14th (Singapore), a kind topics can fill (`knowhow`, `ideas`, `offer`) is short in `content_month()` and
no open suggestion has that kind (checked every 10 minutes at most). At most one automatic run per Singapore day
(a failed one counts; Supabase unreachable doesn't); none while open suggestions >= `STUDIO_TOPICS_COUNT`
(default 4, the most per run); none while Studio is busy (footage in the inbox or arriving from OneDrive, a clip
being tagged, an edit planned / revising / producing, new clips not yet read for ideas from footage). Runs are
recorded in the catalog's `idea_runs` (`trigger` `topics:weekly` | `topics:behind` | `topics:job`, `result` =
`{new_ideas, suggestions: [{id, title, hook, ...}], skipped: [{title, why}], gaps, notes}`); footage ideas ignore
them. `laptop_status.info.topics` = `{day, hour, count, behind, last_run: {at, state, trigger, new_ideas, error},
last_suggested}`.

**What Claude reads.** The content plan and this month's gaps (per fillable kind: short, open suggestions aiming
at it, what's left), the open suggestions; Bryan's site talk: tagged, usable, pushed clips of the last 120 days
with at least 160 characters of speech, most explanatory first (timestamped transcript, moments, project name
marked internal, where the clip is already used), about 60k characters; open `client_questions` (who added it,
who asked, the note); active references' titles and why-notes; the shoot calendar (site days and phases starting
in the next 28 days, active projects); posts already planned or made, and dismissed ones (status `dismissed`,
plus every earlier suggestion from `idea_runs` whose idea is dismissed or gone). The system prompt has brands,
the playbook (with the style guide), the sources best first, and the rules: offers only from `content_plan`; no
price, fee, duration or other figure unless Bryan said it or the team wrote it (`[check]` instead); no place
names in public text; nothing about quotes; never `finished`.

**What it writes.** Per suggestion (at most the count): repeats are dropped (title or hook shares at least three
content words and most of the shorter one's with a planned, made, open, dismissed or remembered post, or one
earlier in the same answer; or the cited moment overlaps a take of a live post by more than half); an On site
suggestion needs footage; a Reel without usable footage becomes a carousel (when it has an outline). Public text
(hook, caption, cta, slides, shot lines) loses project names/folders (`this home`) and money figures not in the
transcripts, question notes or offers (`[check]`).

| Kind of suggestion | Where | Rows |
|---|---|---|
| carousel (most Know-how, Ideas, Work with us) | the standing Topics project (`standing = 'topics'`, made if missing), arc **Studio suggests** (made once, first) | `ideas` (`decision 'proposed'`, `status 'new'`, `created_by` null, `post_type`, `format 'carousel'`, `slides` = the outline: cover first, ask last, text slides with `image.note`; the cover shows the cited clip's still when there is one) |
| Reel where Bryan explains it on camera | the clip's project if active, else Everyday posts; arc **From your footage** | `ideas` + `shots` (the clip's moment first) + `takes` (`source 'auto'`, note `Bryan explains it here`) |

Every suggestion has `ideas.suggestion` = `{why, source: transcript|question|calendar|reference|basics|plan,
clip_id, at, question_id, run, suggested_at}` (checked by `valid_suggestion()`: an object, `why` <= 300). A cited
question is set `status 'suggested'`, `idea_id` = the suggestion (only while it's `open`).

**client_questions** (`id, question (3..500), note (<= 2000), asked_by (<= 120, free text), status open |
suggested | used | passed, idea_id -> ideas on delete set null, created_by (stamped: the signed-in member),
created_at, updated_at`). RLS: members read, add and edit; the owner or the author deletes. Realtime on. Trigger
on ideas (`decision`, `status`, `production`): a linked question becomes `used` (Go, made, produced), `passed`
(dismissed) or `suggested` (otherwise); a linked idea deleted while `suggested` = `open` again. The planner's
**Suggest again** sets `open` and `idea_id` null.

### Jobs

| kind | runner | queued by | payload | result |
|---|---|---|---|---|
| topics | laptop | any member (insert; refused while another `topics` job is queued or running) | `{}` | `{new_ideas, idea_ids, skipped, notes}` |

**Planner.** On Plan's home under **Needs your call**: the open suggestions (`suggestion` not null,
`status 'new'`, `decision 'proposed'`, newest 12) as rows (kind, Carousel / Reel from footage / Reel, title,
`suggestion.why`), Go / Not now (= Park) / Dismiss inline for the owner (plain updates; the team sees "Bryan decides"), tapping
a card opens `#/idea/<id>` (which shows "Studio suggested this" with the why and the source); **Suggest topics**
(queues the job; the line under the cards follows it), **Client questions** with the open count, and "Last
suggested <date>" from `laptop_status.info.topics`, and for the owner "Clear N dismissed suggestions" (deletes
`status 'dismissed'` ideas with a `suggestion` and no `production`; `topics.py` still remembers them in `idea_runs`).
`#/questions` (Plan's home, More, the Content plan page): a fast add box (question; optional who asked and a note), the questions by status with the post each
became, Edit, Delete (owner or author), Suggest again. Until the update the row and the page say nothing / ask for
`cloud_setup.py --update`.


## Reference library and the taste report (migration 20261023000000_reference_library.sql)

Many references imported at once (a pasted list or a CSV), held until the owner pays for them, analysed slowly on
the Studio machine (`ref_library.py`), and a taste report from all of them (`taste.py`) that the owner approves.

| Data | Written by | Notes |
|---|---|---|
| reference_imports | members, through `import_references()` | one row per import: name, side, totals (added, duplicates, invalid), who |
| reference_videos (new columns) | `import_references()` / members | `account`, `post_type` (reel, video, carousel, image, series), `purpose` (design, content, engagement: only when the list has such a column), `import_id` (set = the library), `shortcode` (trigger: the Instagram post a /reel/, /reels/, /p/ or /tv/ link points at; unique, so the same post is never in twice) |
| reference_analysis (new) | laptop | state `held` (imported, waiting for Analyse); `kind` video \| images; `engagement` `{likes, comments, views, followers, uploaded, account, source}` (null = Instagram didn't say); `problem` login \| rate \| gone \| other |
| taste_reports | laptop inserts drafts; owner approves / rejects | `report` (Markdown, also `Studio/Style/taste-report-v<n>.md`), `sections` (structured, for the page), `taste_section`, `guide_id` (its proposed style guide, a `style_guides` draft), `reference_ids`, `usage` |
| jobs kind `refs`, `taste` | owner only | see below |

**Importing.** `rpc/import_references(p_side, p_brand, p_name, p_rows, p_invalid)` (members; at most 5000 rows):
`p_rows` = `[{url, account, post_type, purpose, why}]` (the planner normalises Instagram links to
`https://www.instagram.com/reel|p|tv/<code>/` and drops repeats first); a row whose post or link is already there
counts as a duplicate, a non-https link as invalid. Returns `{import_id, added, duplicates, invalid}`. Every new
reference's analysis starts `held` (the queue trigger), so nothing is downloaded or spent. Hand-added references
(no `import_id`) are analysed straight away as before.

**Jobs.**

| kind | runner | queued by | payload | result |
|---|---|---|---|---|
| refs | laptop | owner | `{action: analyse, mode: all \| sample, n, ids?, estimate_usd}` (held -> pending; a sample is stratified by `post_type` and account, at most 2 per account, seeded by the job id; only what fits in the month's budget) or `{action: pause \| resume \| stop \| retry}` (stop: pending -> held; retry: failed but not `gone` -> pending) | `{queued, estimate_usd, hours, note}` |
| taste | laptop | owner | `{brand}` | `{report_id, version, guide_id}` (fails readably under 10 analysed references or past the budget) |

**Laptop (each tick, after the hand-added references).** At most one library analysis, when the pacing allows:
45-90 s (random) between downloads; on `rate`/`login` the post goes back to `pending` (behind the rest) and the
next try waits 10, 20, 40 min … (≤4 h); four refusals in a row pause the batch (rate: resumes after 6 h; login:
resumes when a new `cookies.txt` arrives or on Resume); six of the last ten wanting a login pause it with the
sign-in hint; `gone` fails for good (never retried by itself); no internet waits 10 min. Before each: the month's
spend (`usage` kinds `style` + `taste`) against `STUDIO_REFERENCE_BUDGET_USD` (default 60) and 3 GB of free disk.
Reels: `style_study.analyse` as before plus the caption and engagement from yt-dlp's info. Carousels and photos
(`post_type` carousel/image, or a /p/ link): up to 10 pictures (yt-dlp's metadata without a sign-in; gallery-dl with
`cookies.txt` when yt-dlp can't), stills ≤540 px, Claude's slide read (`SLIDES_SCHEMA`); a /p/ post that is one
video is analysed as a reel. Follower counts: gallery-dl's profile info, once per account a month (only with the
sign-in), cached in `Studio/Style/accounts.json`. Downloads are deleted after each analysis. Library references
never count toward the automatic style-guide drafts (`_guide_inputs` is the hand-added set); they speak through the
taste report. The heartbeat carries `laptop_status.info.references`: `{paused: {kind, reason, at} | null, next_at,
backing_off, budget: {cap_usd, spent_usd, left_usd}, estimates: {per_post_usd, seconds_per_post, sample, report:
{base_usd, per_chunk_usd, chunk}}, signin: {present, added_at}}` (never the sign-in itself).

**Taste report.** One Claude read per 80 analysed references (notes, quoted hooks, notable posts with why:
design | content | engagement, inferred), then one report call with the measured stats (`taste.stats`: medians by
kind of post; engagement per view for Reels, per follower for carousels; top quarter vs the rest; coverage) and
the current settings, kinds of post and playbook. It inserts a `style_guides` draft (the proposed house style;
`reference_ids` = the hand-added set, so no automatic redraft follows) and the `taste_reports` draft. **Approving
the report** (owner; trigger) makes its guide active (the previous one archived) and archives the previous report;
rejecting it rejects its guide. A guide approved or rejected on the Style guide page carries its report along
(next sync). The laptop then writes the approved guide (as before) and the report's `taste_section` into
`playbook.md` between `<!-- taste:start -->` / `<!-- taste:end -->` (before the house style), once per report.

**Instagram sign-in for downloads (Studio machine, HTTP).** `GET | PUT | DELETE /api/uploads/instagram-signin`
with the planner session (uploads.guard: Bearer token, the planner's own address), owner only. PUT takes the
cookies.txt text (≤256 KB; must hold an instagram.com `sessionid`), keeps only the instagram.com lines and saves
`ROOT/cookies.txt` with mode 0600, replacing the old one; answers `{present, added_at, expires, signed_in}`. Never
stored online, never logged.

**Planner.** References: **Import many…** (paste or CSV; preview with counts by kind, accounts, duplicates, lines
that aren't links); the **Library** card (progress line and bar, what's on now and the time left or why it's paused,
the owner's Analyse all / Analyse a sample of 150 first with cost and time, Pause/Resume, Stop, Try again, the
month's budget; the team sees that it waits for the owner); the owner's **Instagram sign-in for downloads** box
(four steps, upload / Replace / Remove, a warning when downloads fail with "login required"); a paged grid (60 a
page) filtered by kind, state and account. Ours/Inspiration cards and the style-guide banner leave the library
out. A library post's page adds its account, kind, engagement, Claude's slide read and "Analyse just this one".
`#/taste`: Write the taste report (owner, with its cost), the latest report (sections, fonts, ranked
recommendations with kinds of post, settings, why posts stand out, proposed guide and taste section; thumbnails
open the posts on Instagram), Approve / Reject (owner); the Style guide page links to it. List queries select only
the light parts of each analysis (a few numbers through `measured->key`) so 800 posts stay quick.

## Clean-up (migration 20261024000000_cleanup.sql)

**Database.** `clips.archived_at timestamptz` (null = in the Library). The Studio machine sends it with each clip
(`cloud_sync.check_cleanup`, rechecked every 10 min; before the migration it sends nothing new). The planner's clip
pickers, the "Needs a home" tray and the slide picture picker leave archived clips out (`core.liveClips`, which
falls back to all clips while the column is missing). Archived projects already drop out of Plan.

**Studio machine (catalog).** `clips`: `archived_at`, `onedrive_path`, `onedrive_size` (where `onedrive.pull`
moved the original in `Studio Archive`), `freed_at`, `freed_bytes`. `productions`: `archived_at`, `removed_at`,
`removed_bytes`. Footage ideas and suggested topics skip archived clips. A freed clip's original is fetched back
from `Studio Archive` (`onedrive.ensure_originals`) when the editor, Resolve export or a carousel still needs it.

**Review (dashboard, owner only; `cleanup.py`).**

| Call | Answer |
|---|---|
| `GET /api/cleanup` | `{disk: {total, used, free}, onedrive, rejected: {count, bytes, each}, originals: {bytes, each}, tidy: [{id, why, bytes, known}], freed_total}` (`why`: retake, unused 60+ days, archived project) |
| `POST /api/production/<id>/remove` | deletes a rejected draft's videos and renders, keeps the row and its still: `{removed, bytes}`; 409 unless rejected |
| `POST /api/productions/clear-rejected` | the same for every rejected draft: `{removed, bytes}` |
| `POST /api/production/<id>/archive {archived}` | hides (or brings back) an approved or posted draft: `{ok, archived}`; OneDrive's copy is untouched |
| `POST /api/clips/archive {ids, archived}` | `{ok, count, archived}` |
| `POST /api/clips/free {ids}` | deletes Studio's copy of each original only when the same file (recorded path, else same name and size) is listed in OneDrive `Studio Archive`: `{freed, bytes, skipped: [{id, why}]}` |

Every delete is logged with the space freed (`logs/cleanup.jsonl`). Nothing in OneDrive is ever deleted or moved.
`GET /api/board` items carry `archived`, `removed` (drafts) and `freed` (clips); a removed draft can't go back to
review (409).


## Posting to Instagram (migration 20261025000000_posting.sql)

The Studio Director plan, phase 1 (Bryan approved 2026-10-19). **Studio posts only what Bryan approved and
scheduled**, through "Instagram API with Instagram Login" (graph.instagram.com, a Business account, no Facebook
Page). Account @urbanmakers_sg; the Meta app is in Development mode with the account as Instagram Tester.

**The connection (Studio machine only; `instagram.py`).** The owner pastes the access token, the Instagram app ID
and the app secret (Review: account menu > Instagram; the planner: More > Instagram). They live in
`ROOT/instagram.json` (0600), never in Supabase, never logged (urllib3's request lines are redacted), never sent to a
browser after saving. Connect: `GET /access_token?grant_type=ig_exchange_token&client_secret=..` swaps a short-lived
token for a long-lived one (a long-lived token from Meta's dashboard is used as it is), `GET /<v>/me?fields=user_id,
username,account_type` says who it is (Business or Creator only). The long-lived token (60 days) is renewed with
`GET /refresh_access_token?grant_type=ig_refresh_token` once fewer than 50 days are left (so about every 10 days;
Instagram needs it to be 24 h old); a failed renewal shows in the Instagram box and Telegrams Bryan (once a day; at
once when it is a token problem or 7 days or less are left). `laptop_status.info.instagram` = `{connected, username,
expired, problem, days_left, posting}` (no token). API version `STUDIO_INSTAGRAM_API_VERSION` (v25.0), base
`STUDIO_INSTAGRAM_GRAPH_URL` (tests point it at a local mock).

**Posts (catalog `posts` = source of truth; mirrored to Supabase `posts`, id = the Studio machine's id).**

| Column | |
|---|---|
| production_id, idea_id | the draft; the planner idea it was made from (`ideas.production.id`) |
| kind | `reel` \| `carousel` |
| scheduled_at | UTC; Post now = the moment it was asked |
| state | `scheduled` → `publishing` → `posted`; `failed` (with `error`, `problem`: token, limit, spec, media, transient, offline, other); `cancelled` |
| stage | while publishing: preparing, uploading, processing, publishing |
| next_try_at | after a passing problem (backoff 2, 5, 15, 30, 60 min; 5 tries; the posting limit waits an hour) |
| caption, cover_ms, share_to_feed, collaborators | what goes out (Reels: the cover moment in ms, 0 = first frame) |
| media_id, permalink, posted_at | once it is out |
| thumb_path | `posts/<id>.jpg` in the thumbs bucket |

One live post (scheduled, publishing or posted) per draft. RLS: members read; the owner may write (but the planner
schedules through the Studio machine, below: a row changed online directly is overwritten when the post next changes
there). Realtime on `posts`, `post_results`, `account_results`. Only `approved` drafts can be scheduled; a scheduled
draft can't go Back to review (cancel the post first). Once posted, the production's status is `posted` (so
`ideas.production.status` too).

**The posting API (Studio machine; `posting_routes.py`).** Review calls it with the dashboard cookie; the planner with
its Bearer token, like the upload API (same CORS). Reads: members; changes: the owner. Errors are `{error}`.

| Request | Answer |
|---|---|
| `GET /api/instagram` | the connection's status (owner: `username, added_at, expires_at, days_left, next_refresh, problem, limit: {used, total, hours}` from `content_publishing_limit`; team: `connected, username`) |
| `POST /api/instagram/connect {access_token, app_id, app_secret}` | the status; 400 `{error}` in plain words (the old connection stays) |
| `POST /api/instagram/disconnect` | `{connected: false}` |
| `GET /api/instagram/plan/<production_id>` | the Schedule dialog: `{production, why_not, post, suggested, suggested_text, per_week, taken, connected, posting_on, limits}` |
| `POST /api/instagram/posts {production_id, when, caption, cover_ms, share_to_feed, collaborators}` | 201 `{post}`; `when` = ISO time (Singapore if it has no zone) or `"now"` |
| `POST /api/instagram/posts/<id> {when, caption, ...}` | change or try again (scheduled or failed only; 409 while it goes out) |
| `POST /api/instagram/posts/<id>/cancel` | `{post}` (409 once it's out) |
| `GET /api/instagram/posts`, `GET /api/instagram/results` | the last 62 days of posts; results grouped (by kind, format, month) |
| `GET /public/media/<token>/<file>` | no sign-in: the files of a post being published, for Instagram to fetch (below) |

**Scheduling.** The suggested slot (`posting.suggest`): the next day that keeps about `posts_per_week` posts a week
(`STUDIO_POSTS_PER_WEEK`, else the content plan's, else 3): no other post within `7 // n` days (2 for 3 a week), the
week (Mon–Sun) not full, at `STUDIO_POST_TIME` (19:30 Singapore). Caption limits: 2,200 characters, 30 hashtags,
20 @mentions; at most 3 collaborators; Reels 3 s to 15 min; carousels 2 to `STUDIO_INSTAGRAM_CAROUSEL_MAX` (10) slides.

**Publishing (a worker thread, `posting.run_forever`, every 15 s).** For a due post: the files are made in
`ROOT/Posting/post_<id>/` (a Reel: the full-quality draft copied with the moov atom first when it already fits
Instagram's spec, else transcoded to H.264 high / yuv420p / 23–60 fps / AAC 48 kHz / ≤ 25 Mbps / ≤ 300 MB; a carousel:
each PNG slide as a JPEG, ≤ 1440 wide, ≤ 8 MB, 4:5 to 1.91:1). Instagram fetches them from
`STUDIO_PUBLIC_URL` (default `STUDIO_DASHBOARD_URL`, https) `/public/media/<signed token>/<file>`: signed with the
session key, two hours, only the files of that post and only while its state is `publishing`, range requests and
HEAD served. Then `POST /<v>/<ig-user-id>/media` (Reels: `media_type=REELS, video_url, caption, share_to_feed,
thumb_offset, collaborators`; carousels: one `image_url` + `is_carousel_item=true` container per slide, then
`media_type=CAROUSEL, children, caption, collaborators`), poll `GET /<v>/<container>?fields=status_code,status`
(5–30 s apart, up to 20 min) for `FINISHED`, `POST /<v>/<ig-user-id>/media_publish {creation_id}`, `GET /<v>/<media>
?fields=permalink,timestamp,...`. **Never twice:** `publish_at` is saved before media_publish; a post with
`publish_at` and no media id (a crash, a lost answer) is first looked for on the account (its caption among the posts
since 15 minutes before; its container `PUBLISHED`) and only published if it isn't there. Telegram: "Posted: <title>
<permalink>" or "Couldn't post <title>: <reason>" (not for a passing problem that will be retried). `STUDIO_POSTING=0`
stops everything going out.

**Results (`post_results.py`, from the publisher thread).** Once a day at `STUDIO_RESULTS_HOUR` (9, Singapore) every
post of the last 90 days (also posts made from the phone) and the account; each Studio post again ~1 h after it went
out. `GET /<v>/<media>/insights?metric=...`: Reels `views, reach, likes, comments, saved, shares, total_interactions,
ig_reels_avg_watch_time, ig_reels_video_view_total_time`; carousels and photos `views, reach, likes, comments, saved,
shares, total_interactions, follows, profile_visits` (a metric Instagram refuses is dropped and the rest asked again).
Account: `GET /<v>/<ig-user-id>/insights?metric=reach,views,total_interactions&period=day&metric_type=total_value
&since&until` for yesterday, and `followers_count`. Catalog: `ig_media` (every post seen), `post_results` (one row per
post per Singapore day, `raw` keeps Instagram's own names), `account_results` (per day). Supabase `post_results`: the
latest numbers per post (`media_id`, `post_id`, `idea_id`, `format` reel/carousel/image, `post_type`, `title`,
`permalink`, `posted_at`, `day`, views, reach, likes, comments, saves, shares, interactions, avg_watch_ms,
total_watch_ms, follows, profile_visits); `account_results` (day, followers, reach, views, interactions). Members read;
only the Studio machine writes.

**content_month()** now counts an idea that has a post (not cancelled) in the month the post went (`posted_at`) or
goes (`scheduled_at`) out, and returns `posted` and `scheduled` (in total and per kind). Ideas without a post count as
before.

**Planner.** More > Posting calendar (`#/calendar`: this month and next, posted = the kind's colour, scheduled = its
tint, failed = red; tap a post for what, when, its numbers and (owner) Change; Coming up / Posted lists; phones show
one month at a time), More > Results (`#/results`), More > Instagram (`#/instagram`). Plan's home: Coming up starts
with the next scheduled posts, Needs your call lists posts that couldn't go out (Try again), the month's slots tick off
what was posted. An approved idea shows its post and Schedule / Change. The dialogs (Schedule, the Instagram box) are
`cloud/planner/post-ui.js` + `post-ui.css`, shared with Review (served as `/static/post-ui.*`).

## Hook options and Bryan's voice (migration 20261026000000_hook_options.sql)

From instagram-agent-skill (MIT; `studio/igkit`, `studio/wording.py`, `studio/voice_file.py`).

**Schema.** `ideas.hook_options` jsonb null, checked by `valid_hook_options()`: at most 6 of `{text (<= 1000),
formula (<= 80), score (number or null)}`, best first. `context.key` also allows `voice` (Bryan's voice file,
compacted) and `wording` (the "never write these" list with his allowlist). Members edit `hook_options` like any idea
field.

**Who writes it.**
- Claude writes three options from the 26 hook formulas wherever it writes an idea's hook: `plan-organise`,
  `plan-develop` (Reels; `IDEA_FIELDS.hook_options`), `footage_ideas.py`, `topics.py` (and `plan.py` offline).
  The Edge Functions store them unscored (`{text, formula}`); before the update they insert/update without the
  column (`missingColumn`).
- The Studio machine scores (`wording.pick_hook`, igkit/hookscore.py; Chinese = null): its own writers store
  scored options and the strongest as the hook; options written online are scored by the sync
  (`cloud_sync.score_hooks`, needs `check_hook_options`): the hook becomes the strongest only while it is still
  one of Claude's (PATCH filtered on the old hook), so a hook someone typed is never replaced.
- The planner's idea page (Reels): "Score N" under the Hook when the hook is one of the options, "N other hooks"
  (or "N hook ideas" for a hand-written hook) opens them with Use this, which sets `hook` (a hand-written hook is
  kept in the list with `score: null`).

**Prompts.** Every prompt that writes viewer-facing words ends with `wording.guide` / TS `wordingBlock`: the voice
file (context `voice`), the never list (context `wording`, else `_shared/wording.ts`'s generated `NEVER_WRITE`), the
caption rule and (ideas) the hook formulas. `_shared/wording.ts` is generated: `python -c "import wording;
wording.write_ts()"`. The Studio machine pushes `voice` and `wording` with brands/playbook (`_push_context`), once
the update is online. Text written online gets the safe fixes (`wording.tidy`) when Studio uses it (`make`,
carousel slides, drawn scenes).

