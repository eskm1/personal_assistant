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
      plan-develop/index.ts   one idea's full plan + storyboard (also "Refine with Claude")
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
`shell.css`) lives in the planner's folder and is served to the dashboard as `/static/shell.*`.

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
kind). Studio never posts, so the month tracks what's planned and what's approved ("ready").

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
- `jobs.kind` also allows `carousel` and `topics` (later steps); there are no insert policies for them yet.
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

**Planner.** A "This month" strip at the top of Plan (month, "N of target planned · M ready", a meter per
kind, "2 short" in red), each meter opening `#/month/<kind>` (the posts counting this month and the ones
waiting for a decision). `#/content-plan` (More on phones, "Targets and offers" on the strip): the targets
per kind with the total and per-week equivalent, the offers, and the five kinds explained; the owner edits,
the team reads. Cards show a coloured kind chip. Until the database update the strip stays away and ideas
load without `post_type`.
