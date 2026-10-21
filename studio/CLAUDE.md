# Studio: context for Claude sessions

Studio turns Urban Makers' raw footage into Instagram Reels and carousels, and posts the ones Bryan approved and scheduled. Owner: Bryan (Urban Makers, a
Singapore renovation company). Team member: Michelle (marketing). Read README.md first, then
`server/SERVER.md` (the server), `cloud/CONTRACT.md` (planner <-> Studio machine contract).

## Where things run

- **Studio machine = Hetzner server** (CPX42, Ubuntu 24.04, IP 46.62.223.220), not the laptop any more.
  systemd `studio.service` runs `worker.py` (worker loop + Flask/waitress dashboard on 127.0.0.1:8765)
  as user `studio`, repo at `/home/studio/personal_assistant`, data in `/home/studio/Studio`, behind
  Caddy at **https://studio.theurbanmakers.com**. It self-updates from the branch below every ~10 min
  when idle (`updater.py`; systemd restarts it). Status: `ssh root@46.62.223.220 bash /root/studio-server/setup.sh status`.
- **Footage** arrives in OneDrive `Studio Inbox/<project>/<post>/`; `onedrive.py` (rclone) pulls it and
  moves originals to `Studio Archive`; approved reels are copied to `Studio Ready to post`.
- **Planner**: static web app (`cloud/planner`) on Supabase (DB, auth, realtime, storage, Edge
  Functions). Served by the Studio machine at `/plan/` (single sign-on from the dashboard) and by Netlify
  (fallback). The Studio machine syncs with Supabase every 30 s (`cloud_sync.py`).
- **The Windows laptop** is retired from Studio (`Studio\MOVED_TO_SERVER.txt`); its repo does NOT
  auto-update any more (`git pull` first). It's still where `cloud_setup.py` runs (Supabase CLI), e.g.
  `cloud_setup.py --update` after a new migration or Edge Function change.

## Rules

- Branch `claude/sleepy-thompson-iewk09`. The server (and Netlify) deploy whatever is pushed, so never
  push half-built or untested code. No PRs unless asked. Never commit `.env` or secrets; never log keys.
- Bryan decides: approving/rejecting drafts, Dismiss, spending (Make this), style guides. Michelle (team) may also
  Go/Park ideas and send drafts back with notes (Bryan, 2026-10-20: "Michelle can Go/Park and send back").
  Captions and posting stay his call: Studio posts only what Bryan approved and scheduled (never on its own,
  never twice; `posting.py`), and the Instagram token never leaves the Studio machine (`Studio/instagram.json`).
- All content is Urban Makers for now (`STUDIO_BRANDS`, personal on hold). Epidemic Sound music is
  licensed for the Urban Makers account only.
- Subtitles: English or Simplified Chinese only (`WHISPER_LANGUAGES=en,zh`, zhconv).
- Only @theurbanmakers.com accounts may sign in (owners always may).
- Place names (clients' addresses, estates) stay internal: never in captions or posts.
- Anything touching the quote tool (`tools/umcpm.py`, the urban-makers-quote-engine repo) is the
  UMC Lead agent's: consult and defer to it.
- Plain English for Bryan (not technical); match the code's style (plain docstrings, short comments).

## CTO

The CTO session directs Studio and the UMC lead and reads this lead's transcript (Bryan, 2026-10-20: "yes that's
mine"). Studio lead: session `session_01SY9MzeiyvFPCXX85UaKQ4V`, shipping from branch `claude/sleepy-thompson-iewk09`.

## The usability standard (gates every push)

Bryan, 2026-10-20: "ALL apps we build have the ULTIMATE aim of being as usable as possible with the lowest learning
curve for new users. We will dedicate resources to iterate, capture feedback, iterate some more. Put the user before
capabilities ... use design, UI, hierarchy and all the other best practices to onboard ... AI also has to be on hand to
help at any moment the user may need assistance. Otherwise no adoption will cause our efforts to be futile, erode our
competitive edge, and cause the business to be mediocre or fail." And: apps must be "AS OPTIMISED AS POSSIBLE for speed
and performance, responsive as web apps ... periodically run ALL workflows to catch these optimisations and solve them
autonomously ... by any means necessary, but any destructive or reductive decisions still must be green lit by me."

1. **Golden paths first.** The jobs Bryan and Michelle do every week get the full treatment (measured, walked as a
   first-time user at 390 and 1360 wide, polished); everything else lives behind a menu, not on the default screen:
   - **G1 Review a draft** (Bryan, phone, daily): open it from Review or the Telegram link, watch, note at a moment,
     approve or send back.
   - **G2 Decide on ideas** (Bryan, weekly): Plan's "Needs your call" or Ideas > Waiting for your call, Go / Park /
     Dismiss, then Make this.
   - **G3 Film or upload to a shot** (Bryan or Michelle, on site, phone): Ideas > Film these, the idea, Film shot N.
   - **G4 Plan the month** (Michelle, weekly): Plan's month, + New idea into a project, its kind, Go.
   - **G5 Schedule an approved post** (Bryan, weekly; once Instagram is connected): the draft, Schedule, a time.
2. **New-user target.** Michelle, first time, unaided, on her phone: G1 (a note pinned to a moment, then send back or
   approve) in 3 minutes, and G4 (a new idea in a project with its kind) in 2 minutes. Measured by Bryan watching her
   once, never by us.
3. **Help one press away on every screen**: a visible place to ask (Ask Studio, an AI that knows Studio), "Show me" in
   place, and a control that can't act says why and what to do instead (never a silent grey button).
4. **Feedback where friction happens**: one press to flag friction (page, control, optional words) in Review and the
   planner, plus usage capture (page views, clicks, dead clicks, cancelled dialogs, errors) per signed-in user, never
   content. One weekly triage in `docs/usage/<date>.md`; Bryan never relays feedback.
5. **Nothing new ships over known friction on a golden path.** Each weekly report answers "did anyone use what we
   built last week?"; when no, the next week simplifies instead of adding. Hide and simplify by usage data, not taste.
6. **Speed is usability.** Mid-range phone over 4G: main screens paint within 2.5 s, every press answers within
   100 ms, no layout shift after load, page weight budgeted. A weekly pass walks every golden path under those
   conditions with timings per step, fixes regressions, writes `docs/perf/<date>.md` with before and after; a budget
   that improves is tightened.
7. **The green-light rule.** Destructive or reductive changes wait for Bryan's explicit yes, brought as a short list
   with evidence: removing a feature or page, hiding a module from everyone, deleting or rewriting data, dropping a
   library, changing a server or vendor plan, cutting what a client sees. Everything else ships under the normal gate.

**The gate, before any push that reaches a golden path:** walk it as a first-time user at 390 and 1360 with
screenshots and look at every one; the help, feedback and usage pieces still work on the pages touched; no control
on the path is silent about why it can't act; the speed budgets hold (the perf harness, numbers in the commit). The
older rules stand: Bryan decides approvals, spending, captions and posting; never push half-built code.

## Testing

Local Supabase stack via the Supabase CLI + Docker (`cloud/supabase`), Playwright for the planner,
stubs for Claude/Epidemic (the sandbox can't reach them). Check `python -c "import worker, dashboard, ..."`
(`updater._MODULES`) before pushing.

## Open items (as of 2026-10-19)

- Help, feedback and usage capture built 2026-10-20 (not yet proven on the server or with real Claude): the Help
  button on the bar and in the desktop viewers (`shell.js`; `help.js`/`help.css` load on the first press): each page's
  lines and Show me (`help.js` PAGES), Ask Studio (`help_desk.py`, README sections picked by keyword, `usage` kind
  `help`, `STUDIO_HELP_DAILY` 30, `STUDIO_HELP_MODEL`), Something not working? (catalog `friction_flags`, More >
  Feedback); `data-why` on a control that can't act (aria-disabled, shell.js shows why); usage capture
  (`capture.js` -> `POST /api/usage`, catalog `usage_events`, 180 days, `STUDIO_USAGE=0` off, test walks tagged by
  `localStorage studio.qa = 1`); the weekly report `GET /api/report/weekly` (`usage_report.py`: G1-G5 funnels,
  FEATURES, Bearer `Studio/report_token` or the owner; More > Weekly report access). When a feature ships, add it to
  `usage_report.FEATURES` and its golden-path steps to `StudioUsage.mark`; a new page gets its lines in help.js PAGES.
- Supabase is up to date through migration 20261026000000_hook_options and the current plan-organise / plan-develop /
  team functions (the CTO session ran `cloud_setup.py --update` on the laptop, 2026-10-20 ~22:10 SGT, project
  nbkhigzlsdlwdaqbhibn). After any new migration or Edge Function change, ask for it with a `NEEDS CTO: cloud_setup
  --update` line (laptop, personal_assistant\studio: `git pull` on this branch, then `.\venv\Scripts\python
  cloud_setup.py --update`; needs Node.js and studio\.env's STUDIO_SUPABASE_URL; no Docker) saying what it changes.
- Content plan (Bryan approved the proposal 2026-10-18: https://claude.ai/artifact/UyGPCh6YJv3y334g6531Ke), built in
  four steps, all four now built; none yet proven on the server or with real Claude/Gemini. Step 1: kinds of post
  (`ideas.post_type`, `content_plan.py`), the month's mix (`content_plan` table, `content_month()`), the month (now
  slots on Plan's home) and Content plan page, the standing Topics project. Step 2: carousels (`ideas.slides`, Write slides
  in `plan-develop`, pictures from footage stills / uploaded photos incl. HEIC (`slide_images.py`, `uploads.py`),
  layout to PNG with puppeteer-core + HyperFrames' Chrome (`carousel.py`, `render/carousel/`), Review with a
  carousel read, send back = Claude revises the slides, OneDrive folder + zip); needs the updater's pip install
  (pillow, pillow-heif) and npm ci (puppeteer-core) on the server's next update. Step 3: AI concept pictures
  (`concepts.py`: Gemini `gemini-nano-banana-2.1` over REST with Studio's own `GEMINI_API_KEY`, Claude briefs and
  vision checks, two options, cache in `Studio/Concepts`, Review's Concept pictures picker and Redraw, `usage` kind
  `image`; always marked "Concept", never in Finished homes). Step 4: suggested topics (`topics.py`: weekly
  Monday 8am SGT, when the month falls behind (at most daily), or the planner's "Suggest topics" (job `topics`);
  one Claude call, `usage` kind `topics`; sources: transcripts, `client_questions`, references/style guide, shoot
  calendar, SG basics; carousels in Topics arc "Studio suggests", Reels from footage in the clip's project; runs in
  catalog `idea_runs` as `topics:*`; suggestions under "Needs your call" on Plan's home, `#/questions` page). What Bryan must do:
  (1) `cloud_setup.py --update` on the laptop (above); (2) for concept pictures, a Google AI Studio key (billing
  on), then `ssh -t root@46.62.223.220 bash /home/studio/personal_assistant/studio/server/setup.sh key gemini` (the
  `/root/studio-server/` copy is the move-day copy and has no `key`); (3) start the Client questions list (he and
  Michelle). Offers: only the ones in the planner; never invent prices or promotions; nothing about quotes (UMC Lead).
- Built 2026-10-19 (not yet proven on the server or with Bryan's real OneDrive): the Urban Makers look from the quote
  tool (`cloud/planner/theme.css` + bundled fonts, shared with Review), Plan's home "What needs you" (`js/home.js`,
  `GET /api/home`; nav Plan · Ideas · Review · More, the board moved to Ideas, Today on site under More), and clean-up
  (`cleanup.py`: Done's Remove / Clear rejected / Archive, the Library's Archive / Free up space / Suggested to tidy,
  Plan's Clear dismissed suggestions). Free up space (a background job, `cleanup.start_free`, progress in the
  Library's bar, resumes after a restart) deletes the server's original only once OneDrive's Studio Archive holds it
  (path recorded at pull time, else same name and size, checked by quickxor); originals OneDrive never had (planner
  uploads, laptop-era clips) are first copied to `Studio Archive/<project>/<post or Uploads>/` (`onedrive.upload_original`,
  `--immutable`, never over another file). Originals come back on demand. Library: Shift-click ranges, Ctrl/Cmd-click,
  long-press on phones.
- Speed for Bryan's phone (2026-10-20; Singapore, the server ~200 ms away, so round trips matter, not bytes): every
  page names its files with `?v=<hash of the file and its imports>` and they're kept a year (`webassets.py`; pages
  always checked; a new planner module goes in index.html's `modulepreload` list too); scripts, styles, pages and JSON
  gzipped by the app (no Caddy change); the session cookie is renewed on page opens only and files/media carry no
  `Vary: Cookie` (that had made every file re-asked); Review's board answers 304 when unchanged; drafts' stills served
  720 wide (`_small_still`); a service worker (`web/sw.js`, registered by shell.js) opens `/` and `/plan/` from their
  last copy and reloads into a new one after an update if untouched (never data, sign-in or media;
  `STUDIO_SERVICE_WORKER=0` removes it, `?sw=off` one visit); `/` jumps to Plan before Review loads (`web/go.js`; the manifest now starts at `/plan/`, an icon added earlier keeps `/`); the
  planner asks independent things at once (profile + projects, posts, month + suggestions, board jobs) and skips the
  full refresh on unchanged heartbeats; signed still URLs kept 4 h across visits. Measured in the sandbox (phone
  emulation, perf scripts in the scratchpad): opening a view ~2.4-3.3 s -> ~0.8-1.3 s; not yet proven on the iPhone.
- Built 2026-10-19 (not yet proven on the server): Review plays light copies (`previews.py`: 720p H.264 ~2 Mbps capped
  2.5, faststart; drafts `production_<id>_v<n>.light.mp4` made in `self_fix.deliver`, clips `preview-720.mp4` at
  ingest; older ones in the background when idle or when first played, never all at boot); "720p" / "Full quality" /
  "Original" toggle; downloads and Ready to post stay full quality. Range requests are handed to waitress's file
  wrapper (`dashboard._stream_ranges`) so a playing video no longer holds a thread; waitress threads 8 -> 16. Caddy
  unchanged.
- Studio Director phase 1 built 2026-10-19 (plan approved by Bryan: posting, calendar, results; not yet proven against
  the real Instagram API, only a local mock in Meta's documented shapes): `instagram.py` (connection: token + app ID +
  secret pasted in Review's account menu > Instagram or the planner's More > Instagram, kept in `Studio/instagram.json`
  0600; ig_exchange_token, renewal with ig_refresh_token every ~10 days, Telegram when it fails), `posting.py`
  (catalog `posts`; Schedule / Post now / change / cancel on approved Reels and carousels; the publisher thread:
  Reels checked or transcoded, slides to JPEG, `/public/media/<signed token>/<file>` for Instagram to fetch, two hours,
  only while that post goes out; containers, FINISHED, media_publish, permalink; retries; never twice after a crash),
  `post_results.py` (daily 9am SGT and ~1 h after a post: per-post insights for 90 days incl. phone posts, the
  account), `posting_routes.py` (`/api/instagram...`, cookie or the planner's Bearer), migration
  20261025000000_posting (`posts`, `post_results`, `account_results`, content_month() counts posted / scheduled in the
  month they go out), the planner's Posting calendar / Results / Instagram pages (`js/posting.js`), the dialogs shared
  with Review (`cloud/planner/post-ui.*`). What Bryan must do: (1) `cloud_setup.py --update` on the laptop (the
  migration); (2) connect Instagram once (README section 16: Meta App Dashboard > Instagram > API setup with Instagram
  login: generate the token for @urbanmakers_sg as Instagram Tester, copy app ID and secret, paste in Review's account
  menu > Instagram); (3) schedule a first post and watch for the Telegram. Uncertain until the first real post: the
  carousel limit (10, `STUDIO_INSTAGRAM_CAROUSEL_MAX`), insights metric availability, API version v25.0.
- Ideas live only in the planner (footage_ideas.py proposes ideas from new footage into each project's
  "From your footage" arc after uploads go quiet; "Everyday posts" project for loose clips).
  Usable clips are never flagged on cards; only retakes.
- If /plan/ can't find the planner's public key, the server needs `STUDIO_PLANNER_URL` (Netlify address)
  or `STUDIO_SUPABASE_ANON_KEY` in `/home/studio/personal_assistant/studio/.env`.
- Not yet proven live: the `place` job and tagging with real Claude after this update; iPhone "Film"
  upload quality from Safari.
- Drawn inserts built 2026-10-20 (animated explainers stage 2(b); not yet proven with real Claude or footage): the
  editor's plan may lay motion-library scenes over the footage (`editor.place_scenes` / `render_scenes`, graphics
  style `scene`; cutaway by default, panel only clear of the face; cached in `Renders/inserts` by spec hash;
  composited under the captions in `mix_final`'s one encode; Resolve gets `animations.mov` on its own track); Studio's
  read adds "Draw it for now" beside a next-shoot fix (`illustrate`) so a fix round draws it; Bryan's "too many
  animations" / "more diagrams" notes move `learning.insert_lean`. `STUDIO_EDIT_SCENES`, `STUDIO_EDIT_SCENES_MAX` (3).
  The explainers' end card carries the handle under the mark (`STUDIO_INSTAGRAM_HANDLE`, default urbanmakers_sg, or
  the connected account's). Stage 2(a) (explainer ideas in the planner) and 2(c) (topics) not built yet.
- Editor upgrades built (not yet proven on real footage): jump cuts (`editor.tighten`), face-tracked
  reframing of horizontal footage and punch-ins (`framing.py`, OpenCV), each a `STUDIO_EDIT_*` setting.
  Still offered: numbered chapter labels, screenshots floating above the speaker.
- Built 2026-10-19 (not yet proven with real Claude or footage): Studio fixes its own read before Bryan sees a
  draft (`self_fix.py`, `STUDIO_SELF_FIX_ROUNDS`, reels and carousels; the read marks each fix `editor_can_fix`),
  learns editing rules into its own playbook section (`learning.py`, Studio/learned_rules.json; Bryan's send-back
  notes sorted editing/taste; Review's "Rules Studio taught itself", playbook editor, trend), code checks
  (`editor.sentence_guard`, `clear_of_cards`, caption timing, the voice/music gap guard, loudness), a new mix
  (voice cleaned and levelled, music `STUDIO_MUSIC_GAP_DB` under it) and bundled caption fonts
  (`render/captions/fonts`, `STUDIO_CAPTION_FONT`).
- Titles and captions, Bryan's pick 2026-10-20 ("A with C's italic key word in captions"), built 2026-10-20, not yet
  seen on a real draft: `STUDIO_TITLE_STYLE=gallery` (default; `classic` = the first look) in `editor.py`
  (`_GALLERY_CSS`, `_gallery_title`): Instrument Serif titles with one italic word (Claude marks it `*word*`;
  `tidy_plan` strips every asterisk and keeps the word as `hook_italic` / a graphic's `italic`), hairline, top shade;
  captions Inter (now the `STUDIO_CAPTION_FONT` default) with a glow, a stronger glow + hugging shade over bright
  footage (`bright_spans`, kept as `edl["bright"]`), ink on paper over a cutaway, and the plan's `key_words` in serif
  italic (`key_word_captions`, at most one per caption and per ~4 s, none in Chinese). A draft keeps its look
  (`edl["look"]`, `draft_look`; drafts from before stay classic/Figtree until revised); Resolve uses the same. The
  caption font is now settled (Inter); the taste report may still suggest changes.
- Restyle (Bryan, 2026-10-20: "as soon as the style guide is re-released, just refresh all of the work in progress
  posts"): `restyle.py` runs once per `RELEASE` on the editor's start (Studio/style_release.txt): every draft in To
  review goes back to `revising` with `restyle = 1` (a Reel gets a fresh edit; a carousel is rewritten from
  `CAROUSEL_NOTE`, not learned as a rule), Telegram says so. Review's **Edit it again** (one / all waiting) does the
  same. Bump `RELEASE` when the look changes enough to redo what's waiting.
- Cuts and endings, built 2026-10-21 (Bryan: "cuts out words mid-speech", "ends ... abruptly"; not yet proven on real
  footage): jump cuts only shorten pauses between sentences/clauses (`editor.pause_to_cut`, `STUDIO_EDIT_MAX_PAUSE`
  0.35 -> 0.7, a pause over `LONG_PAUSE` 1 s anywhere; ~0.26 s breath left, `BREATH_AFTER/BEFORE`); `whole_ends`
  (a shot meeting another mid-sentence gets its sentence back) and sentence_guard finishing the last sentence;
  patches refuse a trim_pause inside a sentence and a move_cut that stops mid-sentence (`fix_actions._whole`). The
  close (`editor.close_ending`, `end_card`, `clear_of_end`, `_END_CSS`): ~0.8 s beat on the last shot (or its last
  frame held), the gallery end card 1.8 s (mark, the plan's CTA via `end_ask`, the handle; `STUDIO_END_CARD`, Urban
  Makers only), music fade 1.2 s, captions off, nothing drawn within 1 s of it; `edl["close"]`; Resolve gets a gap +
  the card on graphics.mov. Prompt: whole sentences beat pacing targets. Playbook: two seed lines replaced
  (`playbook.SEED_CHANGES`, `migrate()` once per start, exact lines only). Start over (`restyle = 2`, Review's Start
  over, `editor._fresh_block`); RELEASE "2026-10-21 gentle cuts, a proper ending, started over" with
  `RELEASE_FRESH`: on the next start each draft in To review is rejected + archived (note `restyle.REJECT_NOTE`) and
  its idea restarts as a new production (`restyle.restart`, open notes moved, version carries on).
- Fix rounds that fix, built 2026-10-20 (not yet proven with real Claude): Studio's read gives each editor-fixable fix
  an `action` (caption_word, drop_words, restore_words, trim_pause, move_cut, graphic, hook_text, music_level, replan;
  carousels slide_text) and a `for_bryan` reason; `self_fix.py` applies them as patches (`fix_actions.py`,
  `editor.self_patch`, `carousel.patch`), re-plans only the rest once, judges per fix, up to 3 rounds,
  `STUDIO_SELF_FIX_MAX_USD` 1.50; fixes left carry `left_because` (Review shows it), the summary a `cost_line`.
  Caption corrections in `edl["corrections"]`; the trade glossary `Studio/glossary.txt` (`glossary.py`) goes to
  Whisper as hotwords and to the pre-render spell-check (`spelling.py`). Cuts follow the voice (`voice_edges.py`,
  `join_guard`, the clipped-word check; commit bde7803).
- Words that sound like Bryan, built 2026-10-20 from instagram-agent-skill (MIT, Bryan approved; not yet proven with
  real Claude): `igkit/` (vendored, see its README), `wording.py` (safe fixes on all viewer-facing text, the never list
  + one rewrite pass `STUDIO_HUMANIZE`, allowlist `Studio/humanize_allow.txt`, hook options scored with hookscore,
  beats' 3 s check in a draft's Checks, the caption preview in Review), `voice_file.py` (`Studio/voice.md`, drafted
  from ~20 talking-head transcripts, Review > Your voice, in every writing prompt). Migration
  20261026000000_hook_options (`ideas.hook_options`, context `voice`/`wording`) + updated plan-organise/plan-develop:
  Bryan runs `cloud_setup.py --update`; until then hooks are still picked on the Studio machine but no scores reach
  the planner and the Edge Functions use the built-in never list without the voice file.
- Reference library and taste report built 2026-10-19 (not yet proven against Instagram or real Claude):
  Import many (paste or CSV, dedupe by shortcode, `import_references()`), held until Bryan presses Analyse
  (all, or a stratified sample of 150), paced downloads with backoff and pauses (`ref_library.py`), carousels and
  photos from their pictures (yt-dlp metadata, gallery-dl 1.32.15 with the sign-in), engagement captured, the
  monthly cap `STUDIO_REFERENCE_BUDGET_USD` (60), the taste report (`taste.py`, map-reduce, `#/taste`, approval puts
  its guide in force and its taste section in the playbook). Bryan's 814 saved posts (no tags: the report infers why
  each stands out) are to be imported once the migration is online. What Bryan must do: (1) `cloud_setup.py
  --update`; (2) upload a spare Instagram account's cookies.txt in References > "Instagram sign-in for downloads";
  (3) Import many > the CSV, then Analyse all (about US$41, about 18 hours); (4) Write the taste report (about
  US$4) and approve it. The editor settings it suggests (caption font etc.) are his separate call.
