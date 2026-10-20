# Studio worker and dashboard

Film on your phone and upload the video to a OneDrive folder. Your Windows laptop at
home picks it up, transcribes it, and has Claude tag it as Personal or Urban Makers.
It also labels the format (talking head, day in the life or compilation footage) and the
best moments, then files it in a searchable library. Every video shows up on the
**dashboard**, a Reels-style review site the laptop serves. Ava sends one Telegram summary per batch
("20 new videos processed, see …"), not a message per video.

This covers phase 1 of the Content Studio plan (capture and understand) plus the review
dashboard. Ideas and auto-editing come next and feed drafts into the dashboard's To review tab.

```
Phone ──upload──► OneDrive "Studio Inbox" ──sync──► laptop
                                                       │  ffmpeg: audio + frames
                                                       │  Whisper: transcript (CPU; NVIDIA GPU if present)
                                                       │  Claude: brand, format, moments
                                                       ▼
                         %USERPROFILE%\Studio\Library\2026-10\2026-10-14_1532_IMG_1234\
                             IMG_1234.MOV  transcript.json  analysis.json  frames\
                         %USERPROFILE%\Studio\catalog.db   (one row per clip)
                                                       │
                                                       ▼
                      Dashboard (http://127.0.0.1:8765): watch, note, approve
                      Telegram: "Studio: 20 new videos processed. See …" (one per batch)
```

It runs on its own machine and shares nothing with the bot on the server except the
Telegram bot token, which it uses to send messages only. The server's auto-deploy
ignores this folder.

**Studio can also run on a rented server** (a Hetzner CPX42 at about €30 a month) instead of the
laptop, so it works around the clock with the laptop off. Footage still comes in through OneDrive and
the dashboard is at https://studio.theurbanmakers.com. See section 15 and
[server/SERVER.md](server/SERVER.md). The steps below set up the laptop.

## 1. Keep the laptop awake at home

The laptop is the server now. When you take it out, uploads queue in OneDrive and are
processed once it's back home and awake.

1. **Settings → System → Power**, when plugged in: Screen off after 10 minutes,
   **Sleep: Never**.
2. **Control Panel → Power Options → Choose what closing the lid does**, plugged in:
   **Do nothing**, so it keeps working with the lid shut.
3. Turn on your manufacturer's **battery charge limit** (around 80%) in Lenovo Vantage,
   MyASUS, Dell Power Manager, HP or similar. Sitting at 100% on the charger all day
   wears the battery out.
4. **Settings → Windows Update → Advanced options → Active hours**: set them to cover
   the hours you're out, so updates don't restart it mid-job. After an update restart,
   sign in once; the worker starts at sign-in.
5. **GPU:** Whisper's GPU mode needs an NVIDIA card. On your laptop's Intel Arc, or with
   the AMD RX 7600 XT eGPU, it runs on the CPU, which handles phone clips of a few
   minutes fine. Leave the eGPU plugged in at home anyway: its hardware video encoder
   speeds up rendering in the editing phase. Its 16 GB of video memory also meets the
   requirement for DaVinci Resolve's AI features if you add Resolve later. (With an NVIDIA
   card, Whisper would run on it automatically and fall back to the CPU when unplugged.)

## 2. Install the tools (one time)

In **PowerShell**:

```powershell
winget install Python.Python.3.12
winget install Gyan.FFmpeg
winget install Git.Git
```

Close and reopen PowerShell so the new commands are on PATH, then confirm with
`python --version` and `ffmpeg -version`.

## 3. Get the code and its dependencies

```powershell
cd $env:USERPROFILE
git clone https://github.com/eskm1/personal_assistant.git
cd personal_assistant
git checkout claude/sleepy-thompson-iewk09   # until this branch is merged into main
cd studio
python -m venv venv
.\venv\Scripts\pip install -r requirements.txt
# Only with an NVIDIA card (skip for Intel Arc / AMD):
# .\venv\Scripts\pip install -r requirements-gpu.txt
```

The first clip downloads the Whisper model (about 0.5 GB for the CPU model; 1.6 GB for
the GPU one). Later clips reuse it. If transcripts miss words, especially with accents or
mixed languages, set `WHISPER_MODEL=large-v3-turbo` in `.env`. It's more accurate but
noticeably slower on the CPU, which is usually fine for a background worker.

## 4. Set up the inbox

OneDrive is built into Windows, so there's nothing to install.

1. In File Explorer, open **OneDrive** (signed in with the account that has your 1 TB) and
   create a folder called `Studio Inbox`. It's usually `C:\Users\<you>\OneDrive\Studio Inbox`,
   or `OneDrive - <company>` for a work account.
2. Right-click the folder → **Always keep on this device**, so new uploads download as soon
   as they arrive instead of staying cloud-only.
3. On your **iPhone**, either:
   - In the OneDrive app, open `Studio Inbox`, tap **+ → Upload** and pick videos, or
   - Make a one-tap share action. In **Shortcuts → +**, turn on **Show in Share Sheet**
     and set it to receive **Media**. Add **Save File** with the destination
     `OneDrive › Studio Inbox` and **Ask Where to Save** off, and name it
     "Send to Studio". From Photos, select clips, tap **Share → Send to Studio**.
   - On Android, share to OneDrive and choose the `Studio Inbox` folder.

Upload the original files, not compressed copies. Processed clips are moved out of
OneDrive into the library on the laptop, so OneDrive only holds the queue.

**Don't point the inbox at OneDrive's camera-backup folder** (Pictures › Camera Roll).
The worker moves every video it processes out of the inbox, which would take them out of
your phone's backup. Keep `Studio Inbox` for clips you send on purpose.

Google Drive for desktop works the same way if you ever switch: point `STUDIO_INBOX` at
`G:\My Drive\Studio Inbox`.

## 5. Configure

```powershell
copy .env.example .env
notepad .env
```

Fill in `STUDIO_INBOX`, `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN` and
`TELEGRAM_CHAT_ID`. Then edit **`brands.md`**: it tells Claude what belongs on each
account. The defaults are a good start; sharpen them whenever a clip lands on the wrong account.

## 6. Check, test, and leave it running

```powershell
.\venv\Scripts\python worker.py --check     # ffmpeg, GPU, API key; sends a Telegram test
.\venv\Scripts\python worker.py --once      # process what's in the inbox now, with logs on screen
powershell -ExecutionPolicy Bypass -File .\install_task.ps1   # start at sign-in, from now on
```

Drop one short clip in the inbox before `--once`. It's picked up after it has been
unchanged for 20 seconds. `--once` sends its Telegram summary as soon as it finishes; the
background worker waits until a batch of uploads and edits has settled, then sends one summary.

## 7. The Studio desktop icon

```powershell
powershell -ExecutionPolicy Bypass -File .\install_shortcut.ps1
```

This puts a **Studio** icon on your desktop. Double-click it any time: if the worker isn't
running it starts it in the background (no window), waits for the dashboard, and opens it in
your browser. If it's already running, it just opens the browser. With `install_task.ps1`
also set up, the worker is always running after you sign in and the icon is simply the way
in. To stop the worker: Task Scheduler → Studio Worker → End, or end `pythonw.exe` in Task
Manager.

## 8. The dashboard (where you review)

While the worker runs, it serves a review site at **http://127.0.0.1:8765** on the laptop.
It's laid out like Reels: a grid of tiles you can filter by account, with tabs for each stage. On a phone the row
shows **To review**, **Done** and **Library**; **Being edited**, **Ingesting** and **In the cloud** are under its
**More** (the stage you're on always shows).

| Tab | What's in it |
| --- | --- |
| Plan | Projects, content arcs, storyboards and shot lists, planned before filming (section 12) |
| To review | Edited drafts waiting for you |
| Ideas | Posts Claude planned from your library, waiting for Make this or Dismiss (only without online planning: with it, ideas live in the planner, section 12) |
| Being edited | Drafts in progress, including ones you sent back |
| Library | Raw clips, tagged by Claude, each with a quality verdict (section 13); clean-up below |
| Ingesting | Clips being processed, or that failed and will be retried |
| In the cloud | Uploads still in the OneDrive inbox |
| Done | Approved, posted and rejected edits of the last 30 days, older ones folded under **Older** (**Back to review** undoes an approve or reject); clean-up below |

Open a draft and **Studio's read** comes first: a one-line verdict ("Ready to post", "Nearly
ready: 2 things to fix", "Needs work: ..."), a good / fix mark for hook, pace, sound, captions
and picture, then **Fix before posting**: the few changes that matter most, each with the moment
it's about (tap it to play from there) and **Add as note**, which turns it into a note for the
editor. Things that need new footage say "Next shoot" and, for a planned idea, **Add to shot
list** puts a shot on its storyboard. Below: what works, your notes, and folded rows for the
caption, what the editor did, music, transcript, checks, brief and DaVinci Resolve (they
remember whether you left them open). The video is a small tile; the decision bar at the bottom
has **Reject**, **Send back with N fixes** and **Approve**. Raw clips in the Library open the
same way: the quality verdict with "Next time" tips, the best moments, then folded rows (the tag
check is under **Tags**). Michelle sees the same draft page; her bar has **Add a note**, then **Send back** with her
notes (Bryan approves or rejects).

Studio's read is one Claude call after each draft renders (a few cents). If it fails, the draft
is still ready to review and the owner gets a **Get Studio's read** button. When the worker
starts it writes the read for up to 10 drafts waiting for review that don't have one.

A draft has one note box, right under the video ("Note at 0:12"). Typing pauses the video, Enter adds the note
pinned to that moment (it shows in the list at once, marked Saving…, and goes back in the box with why if it
couldn't be saved; **Undo** under the box takes it back), and the cursor stays for the next one. On a phone, Play
grows the video at the top of the page (not the system's full screen) and it stays there with that box while you
scroll; **Close video** shrinks it back. Under your notes, **Add a note at a moment** and the bar's **Add a note**
put the cursor in that box; on a computer **N** does too. The video isn't downloaded until you press Play.

Tick **Remember for future edits** (under the box, Bryan only) and the note is also added to `Studio\playbook.md`,
the rules the editor follows on every edit. A carousel has its note box under its notes, with which slide it's
about; a raw clip keeps both its box under the video and the one in its Notes fold.
**×** beside a note takes it back (yours, or anyone's for the owner) until the editor has acted on it. To pick
the music, paste an Epidemic Sound track link (epidemicsound.com/music/tracks/...) as a note: the next draft
uses exactly that track, and so do later ones until you paste another.
After a decision the next draft opens straight away while the editor revises the last one in the
background. You also write or approve the post caption here. Keyboard: J / K for next and
previous, Space to play, Esc to go back, Enter to add a note.

**Edit it again** (on a draft waiting for review, you only): the same idea made again in today's look, as its next
version: a Reel gets a fresh edit (titles, captions, music taste), a carousel's slides are rewritten in the current
style guide and laid out again. **Edit all N waiting again** does every draft in To review. Notes and earlier versions
stay. When Studio's look changes (`restyle.RELEASE`), it does this by itself once, on its first start after the
update, and says so on Telegram.

**Ideas** (this tab is for when planning isn't online; with the online planner, ideas live only
there and come from new footage by themselves, section 12): on the Ideas tab, press **Suggest posts** (optionally with a focus such as "kitchen
projects this month"). Claude reads your tagged clips, their timestamped transcripts and best
moments, `brands.md` and the playbook, and proposes posts built only from footage you have:
a hook, why it works, the structure, the exact clip segments, and a draft caption. Open one
to play each segment, edit the caption, then **Make this** (it moves to Being edited for the
editor) or **Dismiss**. One run costs one Claude call over your library, roughly tens of
cents for a few dozen clips.

**The playbook** lives at `Studio\playbook.md`. It starts with editing rules adapted from
Nate Herk's Edit Kit, and notes you mark **Remember for future edits** are added at the
bottom. Ideas and the editor both follow it; edit it freely.

To try it with sample videos, run `.\venv\Scripts\python demo.py`, which uses a separate
throwaway folder, and open the address it prints (sign in as `demo@studio.local` /
`demo-password`).

**Signing in.** Everyone signs in, you included. The first time you open the dashboard on the
laptop it asks you to make your owner account (this page only works on the laptop itself). Your
browser stays signed in for 30 days. To reach it from your phone and let the team in, see
section 11.

**Clean-up (you only).** Review keeps Studio's disk tidy without ever touching OneDrive:

- **Done**: **Remove** on a rejected draft deletes its videos (it says how much space that frees) and keeps a
  small record, so the planner and the history still make sense; it can't come back to review, and it moves to
  **Archived**. **Clear N rejected** does them all at once, after one question with the count and the space. **Archive** hides an
  approved or posted draft from Done (it stays in OneDrive's **Studio Ready to post**); the **Archived** view
  lists them, each with **Bring back**.
- **Library**: **Archive** hides a clip from the Library, footage ideas, suggested topics and the planner's
  pickers; **Bring back** undoes it. **Free up space** deletes Studio's own copy of a clip's original video once
  OneDrive's **Studio Archive** holds it. A clip OneDrive never had (uploaded from the planner, or from the laptop
  before the move) is copied there first, into `Studio Archive/<project>/<post or Uploads>/`, never over another
  file, and checked (exact size and OneDrive's fingerprint) before anything is deleted. It runs in the background
  ("Copying 4 of 11 to OneDrive…", then "Freed 2.3 GB from 11 clips."), can be stopped, and carries on after a
  restart. The light preview, stills, transcript and tags stay, and if an edit needs the clip again Studio fetches
  the original back from OneDrive by itself. **Select** picks several clips at once (Shift-click picks a run,
  Ctrl/Cmd-click one more; on a phone, press and hold a clip, then tap); **Suggested to tidy** lists retakes,
  clips unused for 60 days and clips of archived projects, each with what it would free. The bar at the top
  shows the disk's free space.
- Every delete asks once, in plain words, and is logged with the space it freed (`Studio\logs\cleanup.jsonl`).
  Michelle doesn't see these buttons.

**Watching far from the server.** Review plays a light copy of every draft and clip: 720p at about 2 Mbps,
made as a draft goes to review and as a clip comes in (older ones get theirs in the background, while Studio is
idle, or the first time they're played). It starts at once and doesn't stop to buffer on wifi from Singapore.
The **720p** button on the video switches to the full-quality file (**Full quality** for a draft, **Original**
for a clip); **Download video** and OneDrive's Ready to post always get the full-quality file.

Telegram is just for notifications: one summary per batch of uploads or edits, with a link
to the right dashboard tab (set `STUDIO_DASHBOARD_URL` so the link works from your phone).
The dashboard is where you give feedback.

## 9. Editing prerequisites (phase 5)

The editor cuts with ffmpeg and renders captions and graphics with HyperFrames, which
turns HTML pages into video frames with a headless Chrome. It needs Node.js, HyperFrames
itself, and its Chrome, plus your Epidemic Sound key for music. One-time setup:

```powershell
winget install OpenJS.NodeJS.LTS
```

Close and reopen PowerShell, then from the `studio` folder:

```powershell
cd render
npm ci
npx hyperframes browser ensure
cd ..
```

`npm ci` installs the pinned HyperFrames and GSAP versions into `studio\render`.
`browser ensure` downloads the Chrome that HyperFrames renders with.

Then add to `.env`:

```
EPIDEMIC_SOUND_API_KEY=<key from epidemicsound.com/account/api-keys>
STUDIO_MUSIC_ACCOUNTS=urban_makers
```

Your Epidemic Sound plan covers one channel per platform, so only the accounts listed in
`STUDIO_MUSIC_ACCOUNTS` get Epidemic tracks. Personal posts are edited without Epidemic music.

Run `.\venv\Scripts\python worker.py --check`. Besides the earlier lines, it now shows
Node.js, HyperFrames, Chrome, a 2-second test render, and the Epidemic Sound connection with
the tools it offers.

## 10. The editor (phase 5)

Once the prerequisites in section 9 pass, the worker also runs the editor. Any idea you
**Make** (and any draft you send back with **Request changes**) is edited in the
background, one at a time, and lands in **To review**, usually a few minutes later:

1. Claude plans the cut from the idea. It sees every word with its timestamp, so it cuts
   between phrases, drops dead air and false starts, and writes the hook text and a music
   brief. On a revision it also gets your notes and the previous cut list.
2. Cut points follow your voice, not just the transcript's timings: Studio measures each clip's sound once (`voice_edges.py`, kept as `voice.npz`) and only cuts after a word has really died away, and before it starts. Cuts keep whole sentences: only um, uh, 嗯, 呃 and an immediate repeat ("the the") are dropped by themselves, and words cut from inside a kept sentence come back (short gaps by joining the cuts again, longer ones after one cheap Claude check); the draft's checks say what was kept and why. Then
   **jump cuts**: inside what's kept, any pause longer than 0.35 s (silence, a breath, a lone
   "um"/"uh" or 嗯/呃; "ah"/啊 only when alone between long pauses) is cut down to a short breath. Never inside a word; B-roll is untouched.
3. **Framing**: horizontal footage (Z fc, GoPro) is cropped around your face instead of down
   the middle (OpenCV's face finder; if you walk across a long shot, it splits into a few
   still crops, never a pan). No face = the centre. Vertical footage is as filmed. On talking
   shots the picture **punches in** to 112% and back every 4 to 7 seconds, at a jump cut or the
   end of a sentence, so a long shot never sits still (never on B-roll or under the hook).
   ffmpeg then cuts every piece from your **original** files in one pass at 1080x1920.
   Before that, Studio checks the ends of the draft by code: it never ends on a stray word that
   starts a new sentence (an "and" after the last full stop), never opens on the tail of a
   sentence, and trims dead air before the first word and after the last.
4. HyperFrames renders the captions (1 to 3 words in sentence case, timed to each word) and the
   hook as a transparent layer over the footage, in fonts bundled in `studio/render/captions/fonts`
   (`STUDIO_CAPTION_FONT`: `inter`, the default, `figtree`, `nunito`, `dm_sans` or `outfit`; nothing
   depends on the machine's fonts or the network). Cards stay above the caption line, and a caption that
   repeats a card's words is hidden while the card is up.
   **The look** (`STUDIO_TITLE_STYLE`, your pick of 2026-10-20: "A with C's italic key word in captions"):
   `gallery`, the default: the hook, callouts and cards are a large serif title (Instrument Serif) in
   sentence case with one word in italic ("Floors *stripped*"), a short hairline and a quiet line under
   it; no box, no side bar, no orange. A soft shade comes down from the top while a title is up; the
   words rise into place one after another, the hairline draws, the line under it fades up. Captions
   are Inter with a soft glow instead of a box, over a soft shade at the bottom; over bright footage
   (a white wall, sunlit rubble: Studio measures it) the glow is stronger and a soft shade hugs the
   words; over a drawn cutaway's paper they turn ink on paper. Now and then a key word in a caption is in the
   serif italic ("down to the *screed*"): only real key words (the material, the thing being
   explained, a number), at most one per caption and about one every 4 seconds or fewer, never in
   Chinese captions. The editor picks the title's italic word and the caption key words when it plans
   the cut (a title with nothing marked: its last word when it's two or three words long). `classic`
   is the first look (boxed titles with the amber bar, captions on a rounded box). A draft keeps the
   look it was made in: a re-render or its Resolve project doesn't change it, and drafts from before
   this stay classic until they're revised.
   **Drawn animations** (`STUDIO_EDIT_SCENES`, on by default): where you explain something the footage
   doesn't show (layers in a floor, a measurement, steps, a comparison, a layout), or a shot is missing
   or weak, the editor may lay a short animation from the motion library over you (the same paper-and-ink
   look as the explainers) while your voice and the captions carry on. Each lasts 2.5 to 6 seconds,
   timed to your words; none in the first 2 seconds (unless the hook itself is a drawing); about one per
   15 seconds and at most `STUDIO_EDIT_SCENES_MAX` (default 3) per draft. It fills the screen (a
   cutaway); a card over the top of the picture (a panel) only when it wouldn't cover your face. Never a
   price, a promotion or a place name (the motion library's own checks), and an animation that doesn't
   fit the screen is left out. The draft's summary and Studio's read say what was drawn, not filmed.
   Each is drawn once and kept (`Studio\Renders\inserts`, cleared after 45 days unused), so a fix
   round or a re-render that keeps it doesn't draw it again. Notes like "too many animations" or "more
   diagrams" change how freely the editor draws from then on (a line in Studio's playbook section).
5. Sound: your voice is cleaned up (a gentle high-pass and noise reduction) and brought to a steady
   level first, then the music is set against it: about 9 dB under your voice where nobody talks,
   and `STUDIO_MUSIC_GAP_DB` (default 20) dB under it while you talk, with smooth dips timed to your
   words. Studio measures the gap and turns the music down further if it's short, then sets the
   whole mix to Instagram's -14 LUFS in two passes (no pumping). Urban Makers posts get an Epidemic
   Sound track, searched with your taste in mind (`music.TASTE`: soft, warm, unhurried; tracks you like and
   ones to avoid; `Studio\music_taste.json` adds to it); personal posts get no music, plus a note on what to
   add in the Instagram app.
6. Every join is checked against the source sound for a clipped word; one is extended and that cut rendered again before delivery. The draft is re-transcribed to check no word was clipped, and its loudness is measured
   (target -14 LUFS, corrected if it's off). Both show under **Checks and measurements**.
   **Captions spelt right**: Whisper is given the trade glossary (`Studio\glossary.txt`: MCST, HDB, BTO, screed, grout,
   brand names...; add your own, one per line, and `wrong -> right` for a mis-hearing it keeps making), and before each
   render one short check (`spelling.py`) corrects caption words it mis-heard, against the glossary and the brief. It
   never changes a number, a name or what you meant. Only the captions change, not your words, and the fix holds through
   re-renders, revisions and the Resolve project. `STUDIO_SPELL_CHECK`, `STUDIO_SPELL_MODEL`.
7. Studio's read (`draft_review.py`): Claude looks at stills of the finished draft, its
   captions, words, cut list and checks, and writes the verdict and fix list. Each fix says
   whether the editor can make it itself (trims, the opening, hook text, captions, a card's
   place or words, the music level) or it's yours (new footage, spending, judgement calls).
   When a shot you didn't film could be drawn instead (a build-up, a dimension, a process), the read
   adds "Draw it for now: ..." beside the "film next time" fix: Studio's own round draws it in this
   draft, and the "film next time" fix stays for you, marked as drawn for now.
8. **Studio fixes its own list** (`self_fix.py`, `fix_actions.py`): each fix Studio's read gives the editor comes with
   the exact change: a caption word (MCSC to MCST), a stutter or repeat to cut, words to bring back at a join or at the
   end, a pause to shorten, a cut point to move, a card to move, retime, reword or remove, the hook's words, the music
   level. Studio makes those changes directly on the draft's own edit (no new plan, so nothing else moves) and renders
   it again with every check (voice edges, the clipped-word check, loudness). Only what no small change can do
   (re-ordering, a different take, a new drawing), or a small change that didn't work, goes back to the editor for a
   new plan, all together, once. Studio then reads the draft again and keeps the round when the fixes it made are gone
   and nothing got worse; partial progress is kept. Up to `STUDIO_SELF_FIX_ROUNDS` rounds (default 3; `0` = off), and
   never past `STUDIO_SELF_FIX_MAX_USD` (default US$1.50) for the draft's read, rounds and spell-checks together; when
   it stops at the limit, the draft says so. Only then does the draft come to **To review** (and Telegram tells you),
   with "Studio fixed N things before showing you this" folded at the top. Every fix left for you says why: it needs
   new footage, it's your call (taste, the message, spending), or what Studio tried. The draft's cost line shows what
   the read and fixes cost (typically US$0.15 to 0.40).
9. **Studio learns** (`learning.py`): the rule behind each problem a round fixed, and behind each
   of your send-back notes the editor marks as an editing problem (not taste), is kept in its own
   playbook section, "Editing rules Studio learned" (how often it was caught, when last), so the
   next first draft avoids it. Near-duplicates merge, at most `STUDIO_LEARNED_RULES_MAX` (20) per
   kind of post, rules unseen for four months retire, and **Forget** drops one for good. Review
   shows them under "Rules Studio taught itself", with the code checks and a trend ("Studio caught
   4 editing problems itself on this draft · 30-day average 2.1, down from 3.4"); **Edit the
   playbook** there edits your own rules (Studio never touches them). A note that became a rule
   says so under it. `STUDIO_LEARN_RULES=0` turns learning off.

The jump cuts, reframing and punch-ins are settings in `.env` (all on by default; `0` turns one off, then press
**Restart Studio**): `STUDIO_EDIT_JUMP_CUTS`, `STUDIO_EDIT_MAX_PAUSE` (seconds, default 0.35),
`STUDIO_EDIT_REFRAME`, `STUDIO_EDIT_PUNCH_INS`, `STUDIO_EDIT_PUNCH_ZOOM` (default 1.12), `STUDIO_EDIT_SCENES`,
`STUDIO_EDIT_SCENES_MAX` (default 3; `0` = none). The fix rounds,
learning, caption font and music gap are too: `STUDIO_SELF_FIX_ROUNDS`, `STUDIO_SELF_FIX_MAX_USD`,
`STUDIO_SPELL_CHECK`, `STUDIO_SPELL_MODEL`, `STUDIO_LEARN_RULES`,
`STUDIO_LEARNED_RULES_MAX`, `STUDIO_CAPTION_FONT`, `STUDIO_TITLE_STYLE` (`gallery` or `classic`),
`STUDIO_MUSIC_GAP_DB`. Older drafts keep their look until they're revised. A draft
remembers its pieces and framing, so a re-render (after a subtitle fix, say) and the Resolve
project match it.

Drafts are saved in `Studio\Renders`. If an edit fails, the draft shows the reason and a
**Retry edit** button. To edit one production by hand: `worker.py --edit <id>`. To run the
worker without the editor: `--no-editor`. `python music.py` saves Epidemic Sound's full tool
list to `Studio\logs\epidemic_tools.json`, which is useful when the music step misbehaves.

Each draft costs one Claude call to plan the cut plus a few short calls to pick music,
typically tens of cents.

### Finishing a draft by hand in DaVinci Resolve

Any draft can be opened in the free [DaVinci Resolve](https://www.blackmagicdesign.com/products/davinciresolve)
for finishing touches. On the draft's review screen press **Make Resolve project** (a minute
or two; no API cost), then **Show folder** (on the laptop) or **Download (.zip)** (anywhere else;
the zip carries the original clips in a Footage folder to relink to). Or from PowerShell: `worker.py --resolve <id>`.
The folder, `Studio\Renders\production_<id>_v<n>_resolve`, holds:

- `timeline.fcpxml`: the cut, pointing at your **original** clips (full quality, and you can
  drag any cut's edge to get back footage the draft trimmed)
- `graphics.mov` and `captions.mov`: the hook, B-roll cards and captions as transparent
  layers on V2 and V3 (ProRes 4444); a draft with drawn animations has them on a track of their
  own, `animations.mov` on V2, and the cards and captions move up to V3 and V4
- `captions.srt`: the caption text, if you'd rather retype captions as a subtitle track
- `music.mp3`: the Epidemic Sound track on A2 (Urban Makers only)
- `HOW TO OPEN.txt`: the steps below and what to do about black bars or offline media

In Resolve: **File > Import > Timeline...**, pick `timeline.fcpxml`, keep "Automatically
import source clips into media pool" ticked, press OK. Export from the **Deliver** page at
1080x1920.

## 11. Team logins and reaching Studio from anywhere

The dashboard runs on the laptop. To open it from your phone on site, or to let a colleague in, the
laptop needs a private way to be reached. Either way **only Studio** is reachable (not the laptop's
files or anything else), nothing is opened on your home router, everyone signs in with their own
Studio account, and it works only while the laptop is on and awake (section 1).

- **Tailscale** (below): free for a few people, about 10 minutes, no changes to your domain. Each
  person installs the Tailscale app once. Best for you plus one or two colleagues.
- **Cloudflare Tunnel at studio.theurbanmakers.com** (further down): free, nothing to install for
  the team, but it moves the domain's DNS to Cloudflare first. Worth it when the team grows.

Team accounts must use an **@theurbanmakers.com** email (invites, `--add-user` and sign-in refuse
others; set `STUDIO_ALLOWED_EMAIL_DOMAINS` to change it). Owner accounts may use any address.

**Who can do what**

| | Bryan (owner) | Team |
| --- | --- | --- |
| Plan: projects, arcs, ideas, storyboards, organise or refine with Claude, music shortlists, votes, comments, tick shots, attach footage | yes | yes |
| Mark ideas **Go** / **Park** (Bryan, 2026-10-20) | yes | yes |
| **Dismiss** an idea or a suggestion | yes | no |
| **Make this** (sends to the editor; costs an edit), Suggest posts | yes | no |
| **Send back** a draft with notes (Bryan, 2026-10-20) | yes | yes (with at least one note) |
| Approve, Reject, Back to review, Retry edit, Schedule, Resolve export | yes | no (they can leave notes) |
| Notes on clips and drafts, fix tags, edit captions | yes | yes |
| API costs, invites, removing people | yes | no |

**1. Your account.** Open http://127.0.0.1:8765 on the laptop and make the owner account (or
`.\venv\Scripts\python worker.py --add-user you@theurbanmakers.com "Bryan" --owner`).

**2. Tailscale (about 10 minutes).**

1. On the laptop: install Tailscale (`winget install tailscale.tailscale`, or tailscale.com/download)
   and sign in (a Google or Microsoft account is fine). Do the same on your phone.
2. In PowerShell on the laptop: `tailscale serve --bg 8765`. The first time, it gives you a link to
   switch on HTTPS for your Tailscale network: open it, allow it, and run the command again. It
   prints Studio's private address, like `https://your-laptop.tail1234.ts.net`. `--bg` keeps it
   running in the background, also after restarts.
3. In `.env`, set `STUDIO_DASHBOARD_URL=` that address (invite and Telegram links use it), then
   account menu > **Restart Studio**.
4. On your phone, open the address and sign in.
5. Your colleague: in the Tailscale admin console (login.tailscale.com) > **Machines** > the
   laptop > **Share**, and send them the link. They install Tailscale on their phone and computer,
   sign in with their own account and accept; they can reach this one machine and nothing else.
   Then invite them to Studio (step 3 below).

**3. Invite your colleague.** Account menu (top right) > **Team** > **Invite someone**. Send the link
(WhatsApp is fine); it works once and expires after 7 days. Each person picks their own password.
Remove someone there and they're signed out everywhere at once.

The laptop has to be on and awake for anyone to reach Studio (section 1). If the team outgrows
that, the planning board is the part to move to a server first.


**Later, for a bigger team: Cloudflare Tunnel at studio.theurbanmakers.com.** Instead of Tailscale
(turn `tailscale serve` off with `tailscale serve --bg off`, or keep both).

*Move the DNS to Cloudflare (about 20 minutes, then up to a day of waiting):*

1. **Save what's there now.** GoDaddy: *My Products > theurbanmakers.com > DNS*. Export the zone
   file, or screenshot every record. Typical ones here:
   - the website: `@` (an **A** record; for Netlify usually `75.2.60.5`) and `www` (a **CNAME** to
     `<something>.netlify.app`);
   - the designer app: `app` (a **CNAME** to Netlify, Vercel, or similar);
   - email: **MX** records, **TXT** records (SPF `v=spf1 ...`, `_dmarc`, verification codes such as
     Google or Microsoft), and email-signing **CNAME**s such as `selector1._domainkey`;
   - anything with Supabase or Netlify in its value, and any `_acme-challenge` or other
     underscore records.
2. **DNSSEC:** in GoDaddy (*DNS > DNSSEC*), if it's on, turn it off and wait a day before step 4.
   Switching nameservers with DNSSEC on can take the whole domain offline.
3. **Cloudflare:** sign up (free), **Add a domain** > `theurbanmakers.com` > **Free** plan. It scans
   and copies the records. Check its list against step 1 line by line and add anything missing
   (scans often miss underscore records like `_dmarc` or `_domainkey`). Then set **every** record
   to **DNS only** (grey cloud, not orange). That keeps Netlify, the app and email working exactly
   as today; Netlify in particular should not sit behind Cloudflare's proxy.
4. **GoDaddy:** *Domain > DNS > Nameservers > Change nameservers > I'll use my own*, and enter the
   two nameservers Cloudflare shows. Cloudflare emails when the domain is active (usually within an
   hour). Then check: the website loads, `app.theurbanmakers.com` loads and signs in, and an email
   sent to you from outside arrives. If anything's wrong, compare the records again; switching the
   nameservers back to GoDaddy's undoes everything.

*The tunnel (about 10 minutes, on the laptop):*

1. PowerShell: `winget install --id Cloudflare.cloudflared`
2. Cloudflare dashboard: **Zero Trust > Networks > Tunnels > Create a tunnel** (the first time,
   Zero Trust asks you to pick a team name and the free plan), type *Cloudflared*, name it
   `studio`. Choose Windows; it shows a command like `cloudflared.exe service install <long token>`.
   Run that in an **administrator** PowerShell. The tunnel now runs as a Windows service and starts
   with the laptop.
3. Add the route (called **Published application** or **Public hostname** depending on the
   dashboard version): subdomain `studio`, domain `theurbanmakers.com`, service **HTTP**
   `localhost:8765`. Cloudflare creates the `studio` DNS record itself (orange cloud, which is right
   for this one).
4. In `.env` set `STUDIO_DASHBOARD_URL=https://studio.theurbanmakers.com` (invite links and
   Telegram links use it) and restart the worker. Leave `STUDIO_DASHBOARD_HOST` at `127.0.0.1`.
5. Open https://studio.theurbanmakers.com on your phone (off Wi-Fi, to be sure) and sign in.

Optional extra lock, also free: **Zero Trust > Access > Applications** can put Cloudflare's own
email check in front of Studio, so only addresses you list (or all `@theurbanmakers.com`) even reach
the sign-in page. It means signing in twice, so it's up to you.

## 12. Planning: projects, arcs and shot lists

The planner and Review share one bar: **Plan · Ideas · Review · More** (on a phone, the same four at the
bottom). **More** has References, the style guide, Targets and offers, Client questions, Today on site and the
Library.

**Plan** opens on what needs you, across every project: the date, "N things need you" and one orange **+ New
idea**. **Needs your call** lists drafts ready to review, posts Studio suggests (**Go**, **Not now** (parks it) or
**Dismiss**; after Go the toast's **Open** opens the idea), "N post ideas from new footage" (**Look through**) and
anything else waiting, such as a style guide draft. Michelle's Plan opens on **Your jobs** instead: the next site
days with shots to film (**Shot list**), Go posts with shots still to film, Studio's suggestions (**Go** / **Not
now**), new ideas to Go or Park per project (**Decide**) and drafts to watch and leave notes on; Bryan's own list
(failed edits or posts, a style guide or taste report to approve) is folded under it as "N things waiting for
Bryan". On a phone a line under the date sums up the month ("11 of 13 planned · still to plan: ..."). **Coming up** has the next site days with shots to film (**Shot list** opens Today on site), posts in the
edit and posts being filmed. On the side: the month as slots, one per post you want (filled for planned, outlined
for still to plan, coloured by kind), "Still to plan" with **+ Add** beside each gap (New idea with that kind
chosen), **Targets and offers**, your projects (each opens its board) and Client questions. Archived projects
drop out.

**New idea** asks only for the essentials: the project (from Plan), the idea and its kind (from Plan it starts on
the kind the month is shortest of; "Let Studio decide" is still there). **More** has the rest: Reel or carousel,
a link (kept as its first comment), its series, and adding several ideas at once (a list or a messy brain-dump,
added as written or organised by Claude). After **Add** the toast's **Open** opens the new idea.

**Ideas** is one project at a time: tap the project's name to change project (client projects first, Everyday
posts and Topics last); a device's first visit opens on the project with the next site day, else the one most
recently worked on. The line under it says how many shots are filmed and the next site day. Ideas are grouped by
what to do next: **Ready to make** (every shot filmed), **Film these** (Go, shots still to film), **Waiting for
your call** (Michelle: **Waiting for Go or Park**), **Parked** and **Made** (both
folded on a phone; columns on a computer). Each card is a title and one status line. The one orange button is
**+ New idea**. The project's tools are in its **⋯** menu: Shoot plan (phases and site days), Brief and inbox
folder, Series, Find ideas in new footage, Today on site, New project, Archive.

An idea opens to its title and one line (kind · format · length · series; tap it for **Details**: the title,
series, account, kind, format, length, Move to another project, Dismiss), **Your call** (Go / Park for anyone on
the team: tap the one that's on to undo, and the line beside it says so for a few seconds; the votes),
the hook, the shot list (tap a shot for its full editor) and folds for Caption and ask, Music, Comments and
Change it with Claude. The orange button at the bottom is the next step: **Film shot N** (Upload on a computer),
then **Make this**. A shot with an upload on its way says "Uploading…", then "Uploaded · Studio is checking it"
until the clip is on it, and the button moves on to the next shot to film.

- **Project**: a job, say *Kensington*, with a short brief (who the client is, what's being done,
  what to show). Making one also makes an inbox subfolder, `Studio Inbox\Kensington`. Uploads
  dropped there are filed to the project, and matched against its shots first.
- **Throw ideas in.** Type or paste ideas, one per line or as a messy brain-dump. **Add as ideas**
  just files them under Unsorted. **Organise with Claude** (a few cents) groups them into
  **arcs** (content series such as *Waterproofing* or *The client's story*) and gives every idea:
  a hook, who it's for, a call to action, a draft caption, a music direction, and a **storyboard**,
  one frame per shot to film, with framing, what's said and any on-screen text. It follows the
  playbook, so the structure and B-roll it plans are what the editor needs.
- **Decide.** The team votes 👍/👎 and comments. Refine any idea with Claude ("make the hook
  punchier", "add a before/after"). Filmed shots are kept. **Shortlist music** finds a few Epidemic
  Sound tracks to play and pick (Urban Makers), or writes the brief for Instagram's music (personal).
  You mark each idea **Go** or **Park**.
- **On site**: open the **On-site checklist** on a phone and tick shots as you film them. Go ideas
  come first.
- **Footage comes back**: each new clip is matched to the shots it films, as a "take", with its
  quality verdict. A shot shows *Needed*, *Filmed* (ticked, upload pending), *Got it* or *Retake*.
  Anyone can attach or remove footage by hand.
- **Make this**: when the shots have footage, you send the idea to the editor. It cuts from the
  takes in storyboard order, uses the on-screen text and the call to action, and uses the music
  track the team picked.

Ideas Claude suggests from existing footage (Ideas tab) can be moved onto a project with **Add to
plan**: their footage becomes shots that are already filmed, and the shots the editor wished it
had become shots to film.

**Ideas from new footage (automatic).** After a shoot day's uploads, once no new clip has arrived
for 20 minutes (`STUDIO_IDEAS_AFTER_MINUTES`; `0` turns it off), Studio has Claude read the new clips
of each project (title, summary, best moments, transcript, quality, length) with the project's brief
and the posts already planned for it, once per project (roughly 10 to 30 cents). It then:

- adds the new footage to those posts: a take for the shot it films, or a new shot of that post when
  nothing in the storyboard covers it (note *Found in new footage*);
- suggests up to 3 new posts built from the footage, with the shots it already has and the shots
  still to film, in the project's **From your footage** arc as *proposed* ideas for you to Go / Park.

Clips outside any project folder go to a standing project, **Everyday posts** (its own inbox folder
too). Clips that aren't usable are skipped, a clip is read once, and if Claude fails it tries again
at the next quiet spell (three times at most). Telegram says what it did ("3 new post ideas and 4
clips added to existing ideas from today's footage"). In the planner, a project's **Ideas from
footage** button asks for it now, over the project's footage of the last 14 days. With online
planning, the library ideas still waiting on Review's Ideas tab move to the planner once, into
**From your footage** (their footage as shots with takes, their wish list as shots to film), and
Review no longer has an Ideas tab.

**Kinds of post and the month's mix.** Every post has a job, its kind: **Know-how** and **Ideas** (to
get found), **On site** and **Finished homes** (to build trust) and **Work with us** (to get enquiries). On
Plan, the month shows how it's going against your targets: a slot per post, "6 of 13 posts planned" and what's
still to plan; tap a kind to see its posts and the ones waiting for your Go.
A post counts once it's Go (until it's approved), or when it's approved that month; once it's scheduled on
Instagram it counts in the month it goes out, and the slots tick off what was posted (section 16). **Targets and offers** (on Plan, or under More) is where you set how many
of each kind you want a month (it shows the total and about how many a week) and the offers people can take
you up on (name, what's included, how a post words the ask). Michelle sees them but can't change them.
Claude gives every idea it writes a kind, leans toward the kinds that are short this month, and uses only
your offers: it never makes up a price, discount or promotion. You can change any idea's kind on the idea
(or pick one in New idea); ideas without one are sorted by Studio a few at a time (a cent or two each
batch). Posts that aren't about one client's renovation go in the standing **Topics** project, like
**Everyday posts** for loose footage. After this update, run `cloud_setup.py --update` once (below).

**Carousels.** A post can be a carousel instead of a Reel: 6 to 10 slides people swipe through and save,
with no filming. In **New idea** pick **A carousel** (Claude's Organise also suggests one now and then, for
Know-how, Ideas or Work with us posts). On the idea, **Write slides** has Claude write a cover (the hook), 4 to 8
points and the ask, plus the caption, in your voice; the ask only ever uses your offers. Edit any line, move,
add or delete slides, and give each slide a picture: **From footage** (pick a clip, tap the moment; Studio takes
the sharpest frame there), **Photo** (from your phone; iPhone photos are fine), **AI concept** (below) or **Text
only**. **Make this** (you only) has Studio lay the slides out in the Urban Makers
look (1080x1350, big text for phones, the slide number and a small Urban Makers mark; the cover's words stay
inside what the profile grid shows). The draft arrives in Review like a Reel: Studio's read first (is every
slide easy to read, spelling, sensible claims, the ask only uses your offers, no place names), the slides small
and swipeable. A note can be about one slide. **Send back** and Claude rewrites the slides from your notes and
lays them out again as the next version; **Approve** and they're copied to OneDrive, **Studio Ready to post**,
in a folder named after the post (01.png, 02.png ... and caption.txt), and **Download (.zip)** has them too.
Writing slides costs a few cents; laying them out is free.

**AI concept pictures.** For a design idea you haven't built yet (Ideas and Know-how posts mostly), a slide can
show an AI picture of it instead of a photo. In the slide's picture buttons press **AI concept** and say in a few
words what it should show ("a reading corner by the window, light oak and linen"); Claude's Write slides may
suggest one too. Nothing is drawn (or paid) until you press **Make this**. Then Studio writes a picture brief for
each concept slide (Singapore flat sizes: HDB ceilings, windows and room sizes, and the Urban Makers look from
your style guide), has Google's Gemini draw **two options** of each, and has Claude check every picture: a
warped chair, a room that couldn't be built, lettering, a face or the wrong room is drawn once more with the
problem named; if it's still wrong, that slide is laid out as text and Studio's read tells you. In Review, under
**Concept pictures**, tap the option you prefer (the slide is laid out again, free), or **Redraw** a slide (two
new pictures, optionally with a change like "warmer light"). A note when you send it back, like "redraw slide 3,
warmer", redraws only slide 3; everything already drawn is kept, so a new version never pays twice. The rules:
every concept slide carries a small **Concept** mark; a concept is never shown as a home you built; it never goes
in a **Finished homes** post (the planner doesn't offer it there, and Studio lays such a slide out as text and
flags it); where you have a real photo of the idea, use it. Instagram may add its own "AI info" label to these
posts; that's fine. Each picture costs about US$0.03 (a slide with its two options about US$0.07); Review shows
the pictures' cost next to Claude's, per draft and for the month.

*Turning it on (once, about 5 minutes):*

1. Go to **aistudio.google.com**, sign in with a Google account, press **Get API key** > **Create API key**.
   Turn on billing for it (Google AI Studio > **Billing**): the image model Studio uses needs it.
   This key is Studio's own; it's not the quote tool's key.
2. Copy the key (one long line, usually starting with `AIza`).
3. In PowerShell on the laptop run this one command, and paste the key when it asks (it stays hidden while you
   paste; press Enter after):

   ```powershell
   ssh -t root@46.62.223.220 bash /home/studio/personal_assistant/studio/server/setup.sh key gemini
   ```

   It saves the key in Studio's settings on the server and restarts Studio. Within a minute the planner shows
   **AI concept** on carousel slides (before that it says "needs the image key"). Run the same command again to
   change the key. (On the laptop instead of the server: put `GEMINI_API_KEY=<key>` in `studio\.env` and
   restart Studio.)

**Suggested topics (automatic).** Once a week, on Monday morning (Singapore time), Studio suggests a few posts
(4) for the month's gaps, the way it suggests ideas from new footage. It also does it when the month falls behind:
past the middle of the month, when a kind is still short and nothing waiting for you covers it (at most once a
day). It waits while Studio is busy with footage or edits, and it doesn't pile up: if 4 suggestions are already
waiting for your call, it suggests nothing new. Anyone can ask for some now with **Suggest topics** next to the month on Plan. Where the topics come from, best first:

- **Things you said on site.** Studio has the transcripts of your clips. When you explain why or how something is
  done, that's a ready Know-how post in your own words; the suggestion says which clip and when.
- **Questions clients ask.** **Client questions** (on Plan, or under More) is a list you and Michelle
  add to whenever someone asks something: type the question, optionally who asked and a note, **Add question**.
  Studio answers open ones first; the list shows which ones became posts, and **Suggest again** puts back one
  whose post you dismissed. Questions only: no clients' names or addresses.
- **Your saved references and the style guide**, for format and tone.
- **The shoot calendar**: a Know-how post timed for the week you film that work.
- **Singapore renovation basics** (HDB rules, permits, the order of works), stated carefully. Studio never puts a
  price or figure in a post unless you said it on camera or wrote it in a question's note (anything else becomes
  **[check]**), only uses your offers, never names a place, and says nothing about quotes.

Each suggestion is a row under **Needs your call** on Plan: its kind, Carousel or Reel, the title and a short "why now" ("Fills
October's Ideas gap", "You explained this on site last Tuesday", "Michelle logged this client question"). Tap it
to open the idea, or press **Go**, **Not now** (Park) or **Dismiss** right there (Michelle: Go and Not now; Dismiss is yours).
Most are carousels with the slide outline written, in the **Topics** project (arc **Studio suggests**): Go, then
**Write slides** when you want them finished. Where you already explained it on camera it's a Reel in that job's
project (arc **From your footage**) with your clip already in the storyboard. Studio never suggests something
already planned, made or dismissed, even reworded, so Dismiss is how you say "not this one". **Clear N dismissed suggestions** (on Plan, you only) deletes the dismissed ones from the planner; Studio
still remembers them and won't suggest them again. Each run is one Claude
call, a few cents (shown with the rest of the month's cost); Telegram says when there are new ones. Settings:
`STUDIO_TOPICS_DAY` (`off` = only the button), `STUDIO_TOPICS_HOUR`, `STUDIO_TOPICS_COUNT`, `STUDIO_TOPICS_BEHIND`.
After this update, run `cloud_setup.py --update` once; until then the planner doesn't show the row.

### Planning online

Planning can live on the online planner instead (a website on Netlify with its data in Supabase),
so the team plans, votes and attaches footage while this laptop is off. **Setting it up (once, about
30 minutes): follow [`cloud/SETUP.md`](cloud/SETUP.md)**; its script `cloud_setup.py` fills in
`STUDIO_SUPABASE_URL`, `STUDIO_SUPABASE_SERVICE_KEY` and `STUDIO_PLANNER_URL` in `studio\.env`.
While the worker runs, it syncs about every 30 seconds:

- it pulls the plan (projects, arcs, ideas, shots, takes) into this laptop, so new footage in a
  project's inbox folder is still matched to its shots; the online plan always wins;
- it pushes each library clip (title, quality verdict and tips, a still) and every matched take
  (with a still), so the planner shows what's filmed;
- it runs what only this laptop can do: **Shortlist music** (Epidemic Sound) and **Make this**
  (the editor; for a carousel, the slide layout), and keeps each made idea's status (draft ready,
  approved, posted) up to date;
- it analyses the reference reels on the planner's **References** tab and drafts the style guide
  (section 14).

- it gives each **Go** idea its own **post folder** inside the project's inbox folder
  (`Studio Inbox\Kensington\Why we tank the shower`, named once from the title and never renamed;
  on a server it's made in the OneDrive inbox too). Clips dropped there are matched only against
  that post's shots. Folders deeper than project > post are folded into the file name;
- it runs **Place by phase** (the planner's shoot plan): Claude puts each unplaced shot of the
  project's Go and proposed ideas into the renovation phase whose work it shows;
- it suggests **ideas from new footage** once uploads go quiet (above), writing them to the
  planner, and runs the planner's **Ideas from footage** button.

**One address, one login, one app.** The dashboard also serves the planner at **/plan/**, behind the
dashboard's sign-in, and opens it signed in; https://studio.theurbanmakers.com/ opens on **Plan**
(links to a draft, `/#/production/12` or `/#/tab/library`, still open it in Review). Both halves have
the same top bar (`cloud/planner/shell.js` and `shell.css`, which the dashboard serves as
`/static/shell.*`): **Plan**, **Review** (with the number of drafts waiting), **References**, **Style
guide**, Studio on/offline and the account menu. On a phone it's a bottom tab bar: **Plan**, **Today**
(on site, for the current project), **Review** and **More** (References, Style guide, Library and the
account). Review's stages are a second row under it. The sign-in page
takes the planner's email and password too, and the planner role (owner or team) carries over.
Only **@theurbanmakers.com** addresses can sign in as team (`STUDIO_ALLOWED_EMAIL_DOMAINS`; `*` =
anyone); owner accounts can use any address. /plan/ needs the planner's public key: Studio reads it
once from the Netlify planner (`STUDIO_PLANNER_URL/config.js`), or set `STUDIO_SUPABASE_ANON_KEY`.
The Netlify planner keeps working as before.

**Uploading to a shot.** In the planner, a shot's **Film** / **From library** / **Choose file**
sends the video straight to the Studio machine in 8 MB pieces (a dropped connection carries on
where it stopped; up to 8 GB). It lands in the idea's post folder, is processed like any other
clip, and becomes a take of that shot; the shot is marked filmed. Uploads need 5 GB of free disk
afterwards, and unfinished ones are removed after 3 days. A planner on another address (say a
Netlify preview) may upload once it's listed in `STUDIO_UPLOAD_ORIGINS`.

The dashboard's Plan tab then opens the planner and shows when the laptop last synced.
**After an update that adds online features** (such as References), bring the online database
and functions up to date once: `.\venv\Scripts\python cloud_setup.py --update`. Until then the
sync keeps working and the Plan tab says "Online database needs an update".
`python worker.py --cloud-check` tests the connection and shows what's waiting (including
references waiting for analysis and the style guide in force);
`python worker.py --cloud-import` (with the worker stopped) moves a plan made here before the
switch to the online planner, once (votes and comments stay here).

## 13. Quality checks

Every clip gets a verdict as it's ingested: **Good**, **Check** (usable, with issues) or
**Retake**, with what's wrong and a tip for next time. Only a **Retake** (or a clip that isn't
usable) is flagged on tiles and thumbnails; a usable clip isn't, and its tips stay in the clip's
panel. Telegram summaries count the retakes.

- Measured, no API cost: voice level, background noise between words, distortion, Whisper's
  confidence in the speech; exposure (too dark, blown windows, hazy); resolution, frame rate,
  orientation, HDR, length.
- Judged by Claude while tagging: focus, stability, framing and light on the subject.

Clips ingested before this existed: `.\venv\Scripts\python worker.py --check-quality`
(add `--all` to redo every clip). That rerun only measures; the focus/stability/framing checks
come with the next tagging (`--retry-failed` doesn't retag finished clips).

**Mixing cameras** (iPhone, Z fc, GoPro): give each a job: iPhone for talking and anything
vertical, Z fc for detail B-roll in 4K (subject centred, so the vertical crop works), GoPro for
POV and hands-on, never for talking. Match settings on all of them: 4K 30 fps, 60 fps only for
slow-motion; Z fc shutter 1/50 or 1/100 and GoPro anti-flicker 50 Hz under site lights; HDR off on
the iPhone; fixed white balance; neutral colour (Z fc *Standard*/*Neutral*, GoPro *Natural*), no
log; one wireless mic for speech.


## 14. Style study: measure reels you admire

Give Studio your best reels and some you admire; it measures how they're edited and proposes a
house style for the playbook (the rules the idea generator and editor follow).

### Reference guides (online planner)

With the online planner (section 12), the team keeps the reference library there: **References**
tab > add a link, say whether it's **ours** (one of our best) or **inspiration** (another
account), and a few words on why it works. Or drop the video file itself in
`Studio Inbox\References\Ours` or `Studio Inbox\References\Inspiration` (made by the worker);
it becomes a reference and moves to `Studio\References`. Then, on the laptop, automatically:

- each new reference is analysed (two per sync, so footage isn't held up): the numbers below plus
  a strip of stills the planner shows; a failure says why (e.g. Instagram wants a login) and is
  retried a day later;
- once nothing is waiting, if the references differ from the ones the style guide was built
  from, Claude drafts a new **style guide**: the house style, a short report, and **what
  changed** (and why, tied to the references added or retired and their notes). **Update style
  guide** in the planner drafts one on demand (after editing notes, say). A rejected draft isn't
  drafted again until the references change;
- when Bryan approves a draft, the laptop writes it into `playbook.md` (replacing the previous
  house style) on its next sync.

Retire a reference rather than deleting it when it no longer represents the style.

### Many references at once, and the taste report

When the team has a long list of posts it likes (say an export of 800 saved Instagram posts),
import them in one go: **References** > **Import many…**, then paste the links (one per line) or
choose the CSV file (a `url` column; an `account` and `type` column are kept too), and say whether
they're **Inspiration** or **Ours**. The same post twice (a `/reel/` and a `/p/` link to one post,
or one already in References) is skipped. Imported posts go in the **Library** on the References
page: a progress line ("312 of 814 analysed · 40 couldn't be downloaded"), a paged grid you can
filter by kind of post and account, and each post's own page.

**Nothing is downloaded or spent until Bryan presses Analyse.** The Library shows the cost first:
**Analyse all** (about US$0.05 a post: US$41 and about 18 hours for 814) or **Analyse a sample of
150 first** (about US$7.50 and 3 hours, spread across kinds of post and accounts, at most two per
account). Studio then works through them on the server one every minute or so, so Instagram
doesn't block it: when Instagram refuses, it waits longer each time (10 minutes, then 20, 40 …), and
if most downloads want a login it pauses and says so. **Pause**, **Resume**, **Stop** (the rest go
back to waiting, nothing more is spent) and **Try again** (the ones that failed but weren't
deleted) are on the same card. A monthly cap, `STUDIO_REFERENCE_BUDGET_USD` (default US$60),
covers this and the taste report; past it, analysing waits for next month.

Reels are measured as before. Carousels and photos are analysed from their pictures (up to ten
slides): Claude reads the layout, type (with the nearest free fonts), colours, how many words a
slide carries, the slide order, the cover's hook and the call to action. Whatever Instagram shows
is kept as engagement: likes, comments, views, the date, and (with the sign-in below) the account's
followers. Downloads are deleted after analysis; only small stills stay.

**Instagram sign-in for downloads.** Instagram often refuses downloads to a server. On the
References page (Bryan only) there's a box with four steps: pick a **spare** Instagram account (not
Urban Makers' own); sign in to it at instagram.com on a laptop in Chrome or Edge; with the browser
extension **"Get cookies.txt LOCALLY"** export `cookies.txt` while on instagram.com; upload the file
in that box. It goes straight to the Studio machine (`Studio/cookies.txt`, readable by Studio only;
only the instagram.com lines are kept, nothing goes online). The box then says "Signed in for
downloads · added <date>". When downloads start failing with "login required" again, the sign-in
has expired: export a fresh file and upload it (**Replace**); a paused batch carries on by itself.

**The taste report** (Style guide > Taste report, or the Library's **Taste report** link): once
posts are analysed, Bryan presses **Write the taste report** (about US$4 for 800 posts, US$1.20 for
150). Claude reads every analysis and writes: (1) **Design**: colours, materials, light, camera,
framing, kinds of shot, on-screen type and the closest free fonts Studio could bundle (for a
softer caption font), caption styling, carousel layouts; (2) **Content**: formats, hooks (quoted),
structures, topics, length and pace, calls to action, voice; (3) **Engagement**: what the
better-performing posts share against the rest, with the numbers and their caveats; then ranked
**recommendations** for Urban Makers by kind of post and for Reels or carousels, the editor and
carousel **settings** worth changing, which posts stand out and why (Studio's own inference: the
list has no tags), and a **proposed style guide** and short **taste section** for the playbook.
Example posts show as thumbnails that open the post on Instagram. The page works on a phone; the
same report is kept as `Studio/Style/taste-report-v<n>.md`. **Nothing changes until Bryan
approves it**: then the proposed guide becomes the style guide in force and the taste section goes
into the playbook (the idea generator and editor follow both). Settings it suggests are only
suggestions: changing one is a separate decision.

### Study from the command line

1. Make a text file, say `reels.txt`, with links under two headings, and a few words on why:

   ```
   Ours:
   https://www.instagram.com/reel/AAA/  most saves we've had, the kitchen reveal
   Inspiration:
   https://www.instagram.com/reel/BBB/  love the pace and the before/after
   ```

   (Or put video files in a folder; files named `ours...` count as yours.)
2. `.\venv\Scripts\python worker.py --style-study reels.txt`
3. Read `Studio\Style\<date>\report.md`, and `house_style.md` (the proposed playbook section).
4. Happy with it: `.\venv\Scripts\python worker.py --style-apply`. Run another study later and
   apply it again; it replaces the earlier house style instead of adding a second one.

Measured on the laptop, no API cost: cuts (including jump cuts and punch-ins), shot lengths, first
cut, speaking pace, pauses, loudness, and how far the music sits under the voice. Claude then looks
at about a dozen stills per reel for captions, layouts, B-roll, hook and call to action (about
US$0.05 a reel) and writes the report (cents). Public posts only; downloads are deleted after
measuring. If Instagram asks for a login, set `STUDIO_YTDLP_COOKIES_BROWSER=chrome` (or `edge`) in
`.env` so the download uses your browser's Instagram sign-in (on the server: the References page's
"Instagram sign-in for downloads", above).
## 15. Moving Studio to a server

[server/SERVER.md](server/SERVER.md) has the whole move, about 45 minutes once: rent a Hetzner
CPX42 (8 vCPU, 16 GB, EU location, with backups ticked), add one GoDaddy record (`studio` > the server's
address), then run one command on the laptop:

```powershell
.\venv\Scripts\python server_move.py <server address>
```

It installs everything on the server (`server/setup.sh`), connects GitHub (for updates) and
OneDrive (for footage), copies your settings, library, drafts and catalogue across, starts Studio
there and retires the laptop's copy. After that:

- footage still goes in the OneDrive **Studio Inbox**; the server collects it (`onedrive.py`) and
  moves the originals to **Studio Archive**; approved reels land in **Studio Ready to post**;
- the dashboard is https://studio.theurbanmakers.com (log in as before, or with your planner
  email and password), with **Download video** and, for Resolve, **Download (.zip)**; the planner
  is at https://studio.theurbanmakers.com/plan/, and footage uploaded to a shot from the planner
  goes straight to the server;
- updates and **Restart Studio** work as before (systemd restarts the worker);
- checking on it: `ssh root@<server address> bash /root/studio-server/setup.sh status`.

## 16. Posting to Instagram

Studio can post to **@urbanmakers_sg** for you, but only what you approved and scheduled: it never posts on its own.
Meta Business Suite stays for DMs and anything else.

**Connect it once (about 20 minutes, on a computer).**

1. In Meta's App Dashboard (developers.facebook.com), open your app > **Instagram** > **API setup with Instagram
   login**. Under **Generate access tokens**, add @urbanmakers_sg (it must be a Business account and accepted as
   an Instagram Tester while the app is in Development mode) and press **Generate token**. Copy the token.
2. On the same page, copy the **Instagram app ID** and the **Instagram app secret** (Show).
3. In Studio: account menu (top right) > **Instagram…** (or Plan > More > **Instagram**). Paste the three and press
   **Connect**. It says "Connected as @urbanmakers_sg · token renews itself · added <date>".

The three are kept only on the Studio server, never online and never shown again. The token lasts 60 days; Studio
renews it by itself about every 10 days, and sends you a Telegram if it can't. **Replace** pastes a new one;
**Disconnect** stops all posting until you connect again. The box also shows how many posts went out through Studio
in the last 24 hours (Instagram allows 100).

**Schedule a post.** Open an approved Reel or carousel in Review (Done, or the draft's page) and press **Schedule…**
(or **Schedule** on its idea in Plan). Pick the day and time (Singapore time): it suggests the next free slot that
keeps about 3 posts a week, spread out (the content plan's posts a week). Edit the caption (it counts characters and
hashtags against Instagram's limits: 2,200 and 30), pick a Reel's cover (the first frame or a moment), keep "Also
show it in the grid" on, add up to 3 collaborators, then **Schedule**, or **Post now** (it asks once). Until it's out
you can change the time, the caption or cancel it (**Change or cancel**). A scheduled draft can't go back to review:
cancel the post first.

**At the time,** Studio prepares the files (a Reel as Instagram wants it; the slides as JPEGs), lets Instagram fetch
them from a private link that works for two hours only while it posts, waits for Instagram to process them and
publishes. Telegram: "Posted: <title> <link>", or "Couldn't post <title>: <why>". Passing problems (Instagram busy, a
slow upload) are retried by itself; a refused file, a run-out connection or Instagram's daily limit fail with the
reason, and **Try again** sends it again. Studio never posts the same draft twice, even after a restart mid-post.

**The posting calendar** (Plan > More > **Posting calendar**) shows this month and next: posted in the kind's colour,
scheduled in its tint, failed in red. Tap one to see it, its numbers, and to change it. Plan's home lists the next
posts under Coming up, and posts that couldn't go out under Needs your call.

**Results** (Plan > More > **Results**): every morning at 9 Studio reads every post of the last 90 days (also the
ones you posted from your phone) and the account; a new post is read again about an hour after it goes out. You see
followers, views, reach, saves and shares, then by kind of post, by format (Reel / carousel / photo), by month, and
every post. A posted draft in Review shows its own numbers. These are what the next phases (learning, the weekly
review) work from.

Settings in `.env` (then **Restart Studio**): `STUDIO_POSTING=0` stops all posting; `STUDIO_POSTS_PER_WEEK` (blank =
the content plan's); `STUDIO_POST_TIME` (19:30); `STUDIO_RESULTS_HOUR` (9); `STUDIO_PUBLIC_URL` (blank = the
dashboard address; Instagram fetches the files from it, so it must be the https address).

## 17. Words that sound like you (de-AI filter, hooks, caption preview, your voice)

Parts of the MIT-licensed [instagram-agent-skill](https://github.com/Jakeschincariol/instagram-agent-skill) (Jake
Schincariol) are built in (`studio/igkit`, `wording.py`, `voice_file.py`; Bryan's OK 2026-10-20):

- **Everything Studio writes that viewers read** (post captions, hooks and on-screen titles, cards, drawn animations'
  words, carousel slides, suggested topics, ideas from footage, Studio's own fixes) gets the safe fixes by code:
  invisible characters out, em dashes to commas, curly quotes straight. Links, hashtags, @handles, ranges like "3-4"
  and Chinese punctuation stay as they are. Words are never swapped by code.
- Claude is told the **never-write list** (stock AI words like "delve", "elevate", "seamless", and Instagram bait like
  "stop scrolling") and, when one still slips in, Studio asks Claude once to rewrite just that field and keeps the
  better version (a cent or two; counted with the draft or idea). A rewrite that changes a number, a hashtag or a
  link, adds a name, or switches language is thrown away. `STUDIO_HUMANIZE=0` = only the safe fixes. Your own trade
  words go in `Studio\humanize_allow.txt`, one per line (it starts with hack, seamless, showcase and "follow for more").
- **Hooks**: where Claude writes an idea's hook it writes three, each from one of 26 hook formulas; Studio scores
  them (0-100: length, something concrete, something at stake, the point up front, spoken to the viewer) and keeps
  the strongest. The planner's idea page shows the score under the Hook, and the other two one tap away (**Use
  this**). A hook you typed yourself is never replaced. On a draft, **Checks and measurements** says how long the
  spoken hook takes and flags one past 3 seconds.
- **Post caption** in Review shows what the feed shows before "… more", and up to two things worth fixing (a first
  line that gets cut off, too many hashtags, a link, two asks...).
- **Your voice** (Review > Rules Studio taught itself > **Your voice**, or the Playbook's **Your voice**): how you
  sound on camera, which every caption, hook and slide is written in. **Write the voice file from my videos** reads
  about 20 of your talking-head videos (one Claude call, about 10 cents); Studio does it once by itself when 10 of
  them are in. Edit it freely; it's `Studio\voice.md`.

## 18. Help, feedback and what gets used

**Help** is on the bar on every screen (the **?** on a phone, top right; **Help** on a computer, and inside a draft
or an idea's page). It opens a panel with:

- **This page**: what the page is for and its main action, in a few lines, and **Show me**, which points at the
  page's main controls one at a time (**Next**, **Done**; Esc stops).
- **Ask Studio**: type a how-to question ("How do I send a draft back?"). Claude answers in a few steps from this
  README and the page you're on, never from what's on screen. Up to 30 questions a person a day (`STUDIO_HELP_DAILY`;
  usage kind `help`, `STUDIO_HELP_MODEL` for a cheaper model). Without Claude it says so and shows the page's help.
- **Something not working?**: tap the part that's wrong (or **Skip**), say what happened if you like, **Send**. It
  sends the page, the part, the screen size and Studio's version; never a screenshot.

**A grey button says why.** Tap (or hover, or tab to) a button that can't act yet, such as Make this before the
footage is in, Schedule before Instagram is connected or Next on the last draft, and it says why and what to do.

**What gets used** (never content): Review and the planner note page views, presses on controls (by their name in
the code, never the words on a card, title, caption or note), presses that changed nothing, quick repeated presses,
dialogs closed without their action, errors, and the golden paths' steps. Batched, sent about every 15 seconds and
when a page is left, kept 180 days (`STUDIO_USAGE_DAYS`); `STUDIO_USAGE=0` turns it off. A test walk sets
`localStorage studio.qa = 1` in its browser: its events are marked and left out of the report.

**Bryan only, under More**: **Feedback** (everything flagged, newest first, **Mark done**) and **Weekly report
access**: the key Studio's own weekly review uses to read `GET /api/report/weekly` (`?start=YYYY-MM-DD`,
`&format=md`; the last full Monday-Sunday week by default): who was active, the golden paths G1-G5 (started,
finished, median time, where people stopped), dead and rage clicks, cancelled dialogs, errors, flags, questions to
Ask Studio and "did anyone use what we built last week?" (`usage_report.FEATURES`). **New key** stops the old one at once.

## Day to day

**Updates install themselves.** Every 10 minutes, when Studio is idle (nothing uploading,
ingesting, editing, or running a Claude job), the worker checks GitHub for a new version. If there
is one, it pulls it, installs any new packages, checks the new code starts, and restarts itself in
the background. Open dashboards refresh on their own once nobody is typing. If an update would
break something, it goes back to the previous version and Telegram tells you once. To update right
away: account menu (top right) > **Update now**. The menu also shows which version is running.
`STUDIO_AUTO_UPDATE_MINUTES=0` in `.env` turns automatic updates off. Edits you make on the laptop
to tracked files (such as `brands.md`) are kept; if one clashes with an update, the update waits
and Telegram says so.

| Task | Command (from `studio`, with `.\venv\Scripts\python`) |
| --- | --- |
| See what it's doing | open `%USERPROFILE%\Studio\logs\worker.log` |
| Retry clips that failed (no internet, API down) | automatic when the worker starts, or `worker.py --retry-failed` |
| Process one specific file | `worker.py --file "D:\footage\clip.mov"` |
| Transcribe and catalogue without Claude | add `--no-claude` |
| Stop / start the background worker | Task Scheduler → **Studio Worker** → End / Run |
| Update now instead of waiting | account menu > **Update now** |
| Run the worker without the dashboard | add `--no-dashboard` |

Files the worker can't use are moved out of the inbox, not deleted. They go to
`Studio\Duplicates` (already in the library) or `Studio\Unreadable` (not a video).

## Cost

Whisper runs locally for free. Each clip is one Claude call with up to 20 small frames
plus the transcript. AI concept pictures for carousels are Google Gemini's, about US$0.03 each
(`STUDIO_IMAGE_COST_USD`); the dashboard shows them next to the Claude cost. Suggested topics are one Claude
call a week (plus any when the month falls behind or someone presses Suggest topics), a few cents each. Check the first week's usage in the Anthropic console before you
send it a backlog of hundreds of clips. The words filter (section 17) adds about a cent per draft or idea run when a
rewrite is needed, and the voice file about 10 cents each time it's written. To spend less, set `STUDIO_CLAUDE_EFFORT=low` or
lower `STUDIO_MAX_FRAMES`.
