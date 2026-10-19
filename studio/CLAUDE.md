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
- Bryan decides: approving/rejecting drafts, Go/Park/Dismiss, spending (Make this), style guides.
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

## Testing

Local Supabase stack via the Supabase CLI + Docker (`cloud/supabase`), Playwright for the planner,
stubs for Claude/Epidemic (the sandbox can't reach them). Check `python -c "import worker, dashboard, ..."`
(`updater._MODULES`) before pushing.

## Open items (as of 2026-10-19)

- Bryan to run once on the laptop: `git pull` then `.\venv\Scripts\python cloud_setup.py --update`. It applies every
  migration not yet online: 20261018000000_shoot_plan, 20261019000000_footage_ideas (the `ideas` job,
  `clips.usable`/`flagged`), 20261020000000_content_plan (+ the updated plan-organise / plan-develop functions),
  20261021000000_carousels (+ plan-develop's slide writing) and 20261022000000_topics (`client_questions`,
  `ideas.suggestion`, the `topics` job), 20261023000000_reference_library (imports, held analyses, taste
  reports, the `refs`/`taste` jobs), 20261024000000_cleanup (`clips.archived_at`) and 20261025000000_posting (posts,
  results, the month counting posted posts). Re-run after any new migration.
  Until then the planner hides the month on Plan, carousels, suggested topics and Client questions, the server sends
  no `post_type`, and archived clips still show in the planner's footage pickers.
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
- Editor upgrades built (not yet proven on real footage): jump cuts (`editor.tighten`), face-tracked
  reframing of horizontal footage and punch-ins (`framing.py`, OpenCV), each a `STUDIO_EDIT_*` setting.
  Still offered: numbered chapter labels, screenshots floating above the speaker.
- Built 2026-10-19 (not yet proven with real Claude or footage): Studio fixes its own read before Bryan sees a
  draft (`self_fix.py`, `STUDIO_SELF_FIX_ROUNDS`, reels and carousels; the read marks each fix `editor_can_fix`),
  learns editing rules into its own playbook section (`learning.py`, Studio/learned_rules.json; Bryan's send-back
  notes sorted editing/taste; Review's "Rules Studio taught itself", playbook editor, trend), code checks
  (`editor.sentence_guard`, `clear_of_cards`, caption timing, the voice/music gap guard, loudness), a new mix
  (voice cleaned and levelled, music `STUDIO_MUSIC_GAP_DB` under it) and bundled caption fonts
  (`render/captions/fonts`, `STUDIO_CAPTION_FONT`; Bryan picks the final one after studying his reference reels).
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
