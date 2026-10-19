# Studio: context for Claude sessions

Studio turns Urban Makers' raw footage into Instagram Reels. Owner: Bryan (Urban Makers, a
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
  Captions and posting stay his call; Studio never posts.
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

## Open items (as of 2026-10-18)

- Bryan to run once on the laptop: `git pull` then `.\venv\Scripts\python cloud_setup.py --update`. It applies every
  migration not yet online: 20261018000000_shoot_plan, 20261019000000_footage_ideas (the `ideas` job,
  `clips.usable`/`flagged`), 20261020000000_content_plan (+ the updated plan-organise / plan-develop functions),
  20261021000000_carousels (+ plan-develop's slide writing) and 20261022000000_topics (`client_questions`,
  `ideas.suggestion`, the `topics` job) and 20261023000000_reference_library (imports, held analyses, taste
  reports, the `refs`/`taste` jobs). Re-run after any new migration. Until then the planner hides the "This
  month" strip, carousels, suggested topics and Client questions, and the server sends no `post_type`.
- Content plan (Bryan approved the proposal 2026-10-18: https://claude.ai/artifact/UyGPCh6YJv3y334g6531Ke), built in
  four steps, all four now built; none yet proven on the server or with real Claude/Gemini. Step 1: kinds of post
  (`ideas.post_type`, `content_plan.py`), the month's mix (`content_plan` table, `content_month()`), the "This
  month" strip and Content plan page, the standing Topics project. Step 2: carousels (`ideas.slides`, Write slides
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
  catalog `idea_runs` as `topics:*`; "Studio suggests" cards on the strip, `#/questions` page). What Bryan must do:
  (1) `cloud_setup.py --update` on the laptop (above); (2) for concept pictures, a Google AI Studio key (billing
  on), then `ssh -t root@46.62.223.220 bash /home/studio/personal_assistant/studio/server/setup.sh key gemini` (the
  `/root/studio-server/` copy is the move-day copy and has no `key`); (3) start the Client questions list (he and
  Michelle). Offers: only the ones in the planner; never invent prices or promotions; nothing about quotes (UMC Lead).
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
