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
