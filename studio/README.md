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

**Edit words** (a wrong word in the captions, on a Reel waiting for review; you or the team): open **Transcript**,
press **Edit words**, and correct the words in place: a word replaced ("SIDs" to "as IDs"), one word split in two,
two joined into one, a word Whisper heard that you didn't say. **Fix captions** puts them in the captions and makes the
next version with the same cut: no new edit, no Claude, no cost; it comes back to To review in a few minutes and
Studio's read of the version before stays (only the captions changed). Adding or cutting a sentence is an edit, not a
correction: it says so, and a note does that instead. Chinese captions are corrected character by character. Tick
**Remember this word** and the corrected word goes into the trade glossary (`Studio\glossary.txt`, under "Added from
Review"), so Whisper hears it next time and the spell-check knows it. **Cancel** leaves the transcript as it was.

**Edit it again** (on a draft waiting for review, you only): the same idea made again in today's look, as its next
version: a Reel gets a fresh edit (titles, captions, music taste), a carousel's slides are rewritten in the current
style guide and laid out again. **Edit all N waiting again** does every draft in To review. Notes and earlier versions
stay. When Studio's look changes (`restyle.RELEASE`), it does this by itself once, on its first start after the
update, and says so on Telegram (2026-10-22, the Site Notes graphics: every Reel waiting for review is made again in
the new look; carousels, whose slides didn't change, are left as they are).

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
   **jump cuts**: inside what's kept, a pause between sentences or clauses (after a full stop or a comma) longer
   than 0.7 s, or any pause over a second, is cut down to a natural breath (about a quarter of a second); a lone
   "um"/"uh" or 嗯/呃 goes ("ah"/啊 only when alone between long pauses). A short pause inside a sentence stays: it's
   how you talk (your note of 2026-10-21: cutting every pause had made it sound like words were cut mid-speech). Never
   inside a word; B-roll is untouched. Complete sentences come first: the pacing numbers in the playbook and the
   house style are for shot changes and B-roll, never for cutting into speech.
3. **Framing**: horizontal footage (Z fc, GoPro) is cropped around your face instead of down
   the middle (OpenCV's face finder; if you walk across a long shot, it splits into a few
   still crops, never a pan). No face = the centre. Vertical footage is as filmed. On talking
   shots the picture **punches in** to 112% and back every 4 to 7 seconds, at a jump cut or the
   end of a sentence, so a long shot never sits still (never on B-roll or under the hook).
   ffmpeg then cuts every piece from your **original** files in one pass at 1080x1920.
   Before that, Studio checks the ends of the draft by code: it never ends on a stray word that
   starts a new sentence (an "and" after the last full stop), never stops partway through the last
   sentence (the rest of it comes back when it's short), never opens on the tail of a sentence, and
   trims dead air before the first word. A shot that stops (or starts) partway through a sentence
   where it meets another shot gets the rest of that sentence back the same way.
   **The close**: every Reel ends on a complete sentence, then a short beat (about 0.8 s) on the last
   shot (its own footage after your last word; when the clip ends there, its last frame is held), then
   the Urban Makers end card for 1.8 s: the mark, the post's ask (the plan's call to action, short, never
   a price or an offer it doesn't have) and @urbanmakers_sg (or the connected account's handle); in Site
   Notes over the last frame under a dark shade, the ask big with one word in brass and the handle on a brass
   label, in the gallery look on the account's green with the ask in the serif. The picture dissolves into it, captions are off while it's up, your shot's sound fades and
   the music fades out over the last 1.2 s. No card, callout or drawn animation runs into the close (one
   that would is shortened, moved earlier or left out). `STUDIO_END_CARD=0` ends after the beat with no
   card; personal posts get no card. The Resolve project gets it too (the card on the graphics layer).
4. HyperFrames renders the captions (1 to 3 words in sentence case, timed to each word) and the
   hook as a transparent layer over the footage, in fonts bundled in `studio/render/captions/fonts`
   (Archivo in Site Notes; in the gallery and classic looks `STUDIO_CAPTION_FONT`: `inter`, the default,
   `figtree`, `nunito`, `dm_sans` or `outfit`; nothing depends on the machine's fonts or the network).
   Cards stay above the caption line, and a caption that repeats a card's words is hidden while the card is up.
   **The look** (`STUDIO_TITLE_STYLE`): `site`, **Site Notes**, the default (your pick of 2026-10-22, "go
   with A for now", from the proposal https://claude.ai/artifact/TwCheZsi7ywNKNEAEJ3FcM): one font, Archivo
   (bold, bundled with its licence), for every word in the Reel: captions, hook, callouts, cards, the drawn
   animations, the Concept label on a picture and the end card. Captions are 74 px bold white in sentence
   case with a soft shadow (beside a voice-over's window a little smaller, 60 px, so a long word fits); a key
   word now and then is in **brass** instead of italic (the same rules as below). The hook is stacked on tight
   dark labels from the top left (104 px, its key word in brass), with no shade over the top of the frame; a
   callout is a brass label (and its line on a dark one), pointing at the thing with a brass line and dot
   when the plan says where it is. Cards never fill the screen: a dark panel over the top of the footage (a
   headline and its points; points with one number each, "Older HDB 30A", as big digits and bars; "Now: ..."
   / "The plan: ..." as labelled pairs). Drawn animations are the same drawings in white on a dark panel over
   the footage (a "cutaway" dims the footage under it instead of covering it); a picture stays full screen
   with one small "Concept · not a photo" label. Brass is the only accent colour on screen. The only
   full-screen card is the end card. Going back is one setting: `STUDIO_TITLE_STYLE=gallery`.
   `gallery`, the look before (your pick of 2026-10-20: "A with C's italic key word in captions"): the hook, callouts and cards are a large serif title (Instrument Serif) in
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
   Where a real-looking thing (a glass panel, a finish, a finished look) explains it better than a drawing, the
   editor shows an AI picture instead (**Pictures instead of drawings**, below).
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
   **Studio listens back and watches it through** (`final_check.py`, free: no Claude), on the finished file the way
   a viewer does, while Claude reads it. Listening (what Whisper hears again, against the words the cut meant to keep,
   and the voice's own sound at every cut): a word cut short or missing at a join or the end, a word or phrase heard
   twice in a row that you didn't say twice (a stutter left in, two takes meeting at a join), real words cut from the
   middle of a sentence or a shot stopping partway through one, and the voice ending mid-sentence. Watching (ten small
   frames a second, against the draft's hook, cards and drawings): the hook, a callout or a panel over your face for
   more than 0.3 s (OpenCV's face finder; full-screen cards and drawings replace the picture on purpose), a graphic up
   under half a second, words gone before they can be read (0.3 s plus 0.06 s a letter, at least 1.2 s), the same
   animation twice in a row, and a card or drawing still on in the last second before the close (or the end card
   still moving as the video stops). Each one becomes a fix in Studio's read with its moment and the words around it,
   and Studio's own rounds (below) make it like any other: a cut point moved, words brought back or cut, a title moved
   clear of your face or kept up longer, a repeat or a late card taken off or moved. A 40-second Reel took about 10
   seconds in testing, while the read was running, so a draft waits about 3 seconds longer. `STUDIO_FINAL_CHECK=0`
   turns it off.
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
**Restart Studio**): `STUDIO_EDIT_JUMP_CUTS`, `STUDIO_EDIT_MAX_PAUSE` (seconds, default 0.7; pauses between
sentences only), `STUDIO_END_CARD` (default on),
`STUDIO_EDIT_REFRAME`, `STUDIO_EDIT_PUNCH_INS`, `STUDIO_EDIT_PUNCH_ZOOM` (default 1.12), `STUDIO_EDIT_SCENES`,
`STUDIO_EDIT_SCENES_MAX` (default 3; `0` = none), `STUDIO_REEL_PICTURES` (AI pictures, below; default on),
`STUDIO_REEL_PICTURES_MAX` (default 4), `STUDIO_EDIT_BEHIND` (words behind you, below; default on),
`STUDIO_EDIT_BEHIND_MAX` (default 2), `STUDIO_SFX` (sound effects, below; default on), `STUDIO_SFX_LEVEL_DB`
(default 24), `STUDIO_SFX_MAX` (default 5). The fix rounds,
learning, caption font and music gap are too: `STUDIO_SELF_FIX_ROUNDS`, `STUDIO_SELF_FIX_MAX_USD`, `STUDIO_FINAL_CHECK`,
`STUDIO_SPELL_CHECK`, `STUDIO_SPELL_MODEL`, `STUDIO_LEARN_RULES`,
`STUDIO_LEARNED_RULES_MAX`, `STUDIO_CAPTION_FONT`, `STUDIO_TITLE_STYLE` (`site`, the default, `gallery` or `classic`),
`STUDIO_MUSIC_GAP_DB`. Older drafts keep their look until they're revised. A draft
remembers its pieces and framing, so a re-render (after a subtitle fix, say) and the Resolve
project match it.

**Edit it again** (a draft waiting for review: this one, or all of them) makes a new version in today's look,
working from the last cut. **Start over** makes a brand-new edit from your footage as if it were the first draft:
the last cut, its cards and Studio's read of it are set aside (cutting a cut again and again can compound), your
open notes still go in, and the version number carries on. A carousel's Start over rewrites the slides from the
plan. When Studio's editing changes enough (2026-10-21: gentler cuts and the close), the drafts waiting for review
are rejected on the next start (kept in Done > Archived, noted "Rejected for a fresh edit after the endings fix")
and each idea starts over as a new edit, once; Telegram says so. When only the look changes (2026-10-22: the Site
Notes graphics), the Reels waiting are made again in it instead (Edit it again), once.

Drafts are saved in `Studio\Renders`. If an edit fails, the draft shows the reason and a
**Retry edit** button. To edit one production by hand: `worker.py --edit <id>`. To run the
worker without the editor: `--no-editor`. `python music.py` saves Epidemic Sound's full tool
list to `Studio\logs\epidemic_tools.json`, which is useful when the music step misbehaves.

Each draft costs one Claude call to plan the cut plus a few short calls to pick music,
typically tens of cents.

### Pictures instead of drawings

Some things a drawing can't show: what a reeded glass panel looks like, a fluted timber wall, a terrazzo floor, a
joinery detail, a finished look. Where a real-looking thing explains what you're saying better than a diagram, the
editor shows an **AI picture** of it over the footage instead of a drawn animation (your call of 2026-10-20: "there
are many instances where an animation just doesn't cut it", and "Gemini OK" on 2026-10-22). Processes, steps,
layers, measurements, comparisons and numbers stay drawings.

- **How it looks**: full screen (or a card over the top of the picture when that keeps your face clear), a slow
  push-in, a soft dissolve in and out, and a small **Concept** mark top left, as on the carousels' concept slides,
  so nobody takes it for a finished project. Your voice and the captions carry on over it. Each is on screen 2.5 to
  6 seconds, timed to your words; never in the first 2 seconds (the hook stays on you), never within a second of the
  close, and together with the drawings about one every 10 seconds.
- **Asking for one**: a note like "**use an image of a decorative glass panel here instead of the animation**" (or
  "a photo of fluted glass here") makes that moment a picture of what you named when you send it back, whatever the
  editor plans. Studio's own read can do the same before you see a draft when a drawing shows a real thing badly.
- **How it's made**: the editor writes a short picture brief (the thing, its material and light, the Urban Makers
  look); Google's Gemini draws **one** picture (9:16), the same way as the carousels' concept pictures; Claude checks
  it (a warped panel, lettering, a face, not the thing asked for) and it's drawn once more with the problem named
  only when the check finds it wrong. Never words, logos, people, faces or real places; never a client's home or
  real work, and never in a **Finished homes** post.
- **Cost**: about US$0.03 to 0.04 a picture (`STUDIO_IMAGE_COST_USD`), at most `STUDIO_REEL_PICTURES_MAX` (default
  4) new pictures a draft, Studio's own fix rounds included. Every picture is kept (`Studio\Concepts`), so a
  re-render, a fix round or a new version that keeps it pays nothing. The draft's cost line says how many were shown
  and drawn, and what they cost; they count in the month's pictures with the carousels'.
- **When there's no picture**: with no image key (see "AI concept pictures" in section 12), `STUDIO_REEL_PICTURES=0`,
  or when Gemini is down or the picture comes out wrong twice, the insert is drawn instead (the editor's own drawing
  for it, or its words as a headline), and "What the editor did" says so once. A draft is never held up by a picture.
- Studio's read and its watch-through treat a picture like a drawing (long enough to see, clear of your face, gone
  before the close). The DaVinci Resolve project has the pictures on the drawn animations' track.

### Sound effects

A Reel used to have your voice and the music only. Now a few **soft sound effects** come with the graphics (your
"yes ok" of 2026-10-26: "a gentle whoosh as a label slides in, a tick on the numbers panel, a soft chime on the end
card. Calm, never jarring"):

- **Where**: a soft whoosh as a label, card or callout slides in; a soft tick as each bar of a numbers panel grows
  (at most 3); a very soft air whoosh as a drawing or a picture comes in; one soft chime on the end card as the mark
  rises. Sparse on purpose: never two within 1.5 seconds, none in the first second, none within 0.4 seconds of a cut
  in your speech, at most one every 6 seconds (`STUDIO_SFX_MAX`, default 5, a draft). Never riding on the loud part of a word
  you say: it moves into the nearest gap (at most 0.3 seconds); with no gap near, it dips well under that moment, or
  it's left out when it would mostly sit on your words. One that touches a word at all plays a little quieter.
- **How loud**: well under your voice: each effect's loudest moment `STUDIO_SFX_LEVEL_DB` (default 24) dB under the
  loud parts of your levelled voice, with a short fade in and out, mixed in the same one encode as the voice and the
  music. Studio measures them after and turns them down if one came out louder.
- **Where they come from**: Epidemic Sound's own sound effects (your subscription) when its connection offers them;
  Studio asks it what it offers each time it needs a new one. Otherwise Openverse (openverse.org, free, no account),
  and only effects marked **CC0** or public domain, never anything that needs credit or bars commercial use. Never
  made up by a generator. Each effect is fetched once and kept in `Studio\SoundEffects` (with `index.json`: where it
  came from, its id, title, maker and licence), so every draft after uses the same ones. The draft's **Checks** list
  each effect and where it came from; so does its plan (`sfx` in the edit).
- **Your say**: a note like "**no sound effects**", "the whoosh is too loud" or "no chime" turns them off, down or
  that one off for that post from then on ("bring back the sound effects" puts them back). Studio's read knows they're
  deliberate and may flag one as too loud or jarring; its own round then takes that one off or turns them all down.
- **Turning them off**: `STUDIO_SFX=0` (then a draft is made exactly as before). Slideshow Reels get none. Free: Epidemic's
  are in your subscription, Openverse's are CC0. The DaVinci Resolve project doesn't carry them yet.

### Words behind you

Big words can pop up in the background **behind your head**, so you stand in front of them (your ask of 2026-10-28:
"Have words pop up behind my head together in the background"). The first of the layered looks.

- **When**: the editor may choose it for the hook or one key line, on a shot of you talking where you're clearly in
  the picture, alone, with a still camera and some wall around your head. At most `STUDIO_EDIT_BEHIND_MAX` (default
  2, never more) a Reel, usually one or none. Each is up 1.5 to 4 seconds, timed to your words, never with a card, a
  drawing or a picture at the same time, never in your small window in a voice-over, never in the last second.
- **How it looks**: the words in the look's own font (Archivo, bold, in Site Notes), as big as fits, in one to three
  lines, the key word in brass. They pop up together (a quick grow), hold, and fade. White with a soft shadow over a
  dark or busy wall; dark over a light one. Placed behind and around your head inside the frame's safe area (never cut
  off at the edge, never under the captions), with every word mostly readable. Your face is never covered: Studio cuts
  your outline out of the words, and keeps the face it finds clear again on top of that.
- **How it's made**: Studio finds you in every frame of that stretch (Google's MediaPipe person finder, on the Studio
  machine's own processor, no cost), smooths your outline over time so it doesn't flicker, fits it to the picture's edges
  and softens it, then lays the words with your shape cut out over the footage, under the captions, in the same one
  encode. About 7 to 8 seconds of the machine's work for each second of the effect (a 2.5 s stretch: about 11 s).
- **When it doesn't**: if Studio isn't sure of you there (you're small in the picture or missing from some frames,
  your outline keeps changing, there's more than one person, the camera moves, it can't see your face, or you fill
  so much of the picture that the words wouldn't read), the words go on top the usual way instead (the hook as the
  hook, a key line as a callout) and the draft's checks say why. The watch-through also looks for words over your face
  in the finished file, and Studio's own round takes them off if it finds any.
- **Your say**: pause where you want them and write "**words behind me here**" (or "**put 'Screed first' behind
  me**"); "**no words behind me**" takes them off for that post. Studio's read can add or take off the same way.
- **The first time**: the person finder's model (16 MB, from Google's model store) is fetched once into
  `Studio\Models`; if it can't be fetched, the words go on top and it's tried again an hour later.
  `STUDIO_EDIT_BEHIND=0` turns it off. The DaVinci Resolve project has the words on the animations track.

### Voice-overs: film on site, talk in the studio

Filming speech on site is hard work (noise, light, people). So you can film the site as **B-roll only** (the work,
the details, no talking needed) and talk to camera later in your studio. Studio tells the footage apart by itself,
with no extra cost (`voiceover.py`):

- **A talking head**: a face looking at the camera in most of the clip's stills and someone talking most of the
  time. **B-roll**: hardly anything said (a few words in the background are fine). Talking with no face to camera (a
  walk-through) counts as site footage too.
- **Studio or site**: a talking head is from the studio when enough of these agree: clean sound (the quality check's
  gap between your voice and the room), a still camera (Studio measures how much the picture moves), the setting
  Claude saw when it tagged the clip (a studio, an office; or a site), and when it was filmed (another day than the
  B-roll, or hours later). The sound and the camera have the final say: a noisy or hand-held talking head is on site.
  What Studio decided is kept with each clip (`footage.json`) and in the draft.

When a post's footage is **a studio talking head plus site B-roll**, the draft is a **voice-over**:

1. Your words are cut exactly as for any talking head (whole sentences, cuts that follow your voice, the
   clipped-word check). The editor puts each B-roll shot before the words it shows; Studio lays it over your voice
   instead of stopping it, changing shots at the ends of your sentences or clauses.
2. You're **full screen for your first sentence** (the hook) and **your last** (before the end card). In between,
   the site footage fills the screen and you're in a **small window** (a third of the width, rounded corners, a
   hairline and a soft shadow) in a bottom corner, above Instagram's caption area. The captions move beside the
   window. The window goes on the side where the footage is calmest (usually the left).
3. The site's own sound plays well under your voice (about 18 dB under it), under the music too.

A talking head filmed **on site** is edited the old way (the B-roll cut in between your sentences), and so is a
talking head with no B-roll. `STUDIO_EDIT_VOICEOVER=0` turns voice-overs off.

**Notes that change the picture**: pause where you want it and write "**full screen here**" (that sentence shows you
full screen), "**show the site here**" (the footage and your window come back), "**move me to the left**" or
"**... right**" (the window's corner, the whole draft), or "**no window**" (full screen all through). Studio makes
these itself when you send it back, and keeps them in later versions. Studio's own read can make the same changes
before you see a draft. Not yet: a bigger or smaller window, the window in a top corner, or a shot swapped for
another by a note (the editor re-plans for that, as for any note).

**Planning for it**: in the shot list, mark talking shots for the studio and keep the site list to B-roll (the
planner suggests this). Film the B-roll first, then talk in the studio with the plan in front of you.

**Cost**: nothing extra. The footage check uses what Studio already measured (stills, transcript, the quality check)
and OpenCV on the server. The edit is still one plan; its prompt is a few hundred words longer for a voice-over
(about a cent).

**DaVinci Resolve**: the project has the site shots on V2 as clips from your originals (their sound low), your
window as a layer above them, and the rest as before.

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

**Slideshow Reels (a carousel with music).** Instagram doesn't let Studio add music to a carousel, so a carousel
can also go out as a Reel with one of your Epidemic Sound tracks in it. On a carousel waiting for review (or
approved), **Make a slideshow Reel** (you only, under the notes) makes a new draft of the same idea; it comes
back to **To review** as a draft of its own, and the carousel stays exactly as it is (you can still post it from
the Instagram app with its own music). Studio (`slideshow.py`) lays the slides out again for the Reel, the same
words and pictures without the carousel's **Swipe** cue and slide numbers (a slide whose picture can't be had again
keeps the carousel's own, and the draft's checks say so), and puts each in a 9:16 frame on the Urban Makers
green, kept clear of Instagram's buttons and words at the top, bottom and right, each up long enough to read its
words (about 0.3 s plus 0.06 s a letter, 2.5 to 6 seconds, the cover a little longer), with short crossfades and
a very slow push-in that never crops a slide (`STUDIO_SLIDESHOW_PUSH_IN=0` keeps them still). Then the usual
close: a short beat, the end card with the post's ask and @urbanmakers_sg, the music fading out. The music is
picked as for any Reel (your taste; a track link pasted in a note, on the carousel or this draft, picks exactly
that track) and set for a post with no voice; there are no captions. It's reviewed like any Reel: Studio's read,
its watch-through (each slide up long enough to read; there's no voice to listen back to) and its own fixes
(the music level, a slide's time or the order). **Remember for future edits** isn't offered on a slideshow's
notes: they're about its slides and music, not the Reels' editing playbook. A note can change how long a slide is up, the order (or leave a
slide out) or the music; then **Send back**, **Approve**, **Schedule** and it posts as a Reel. The slides' words
and pictures are the carousel's: change them there and make a new slideshow. Pressing **Make a slideshow Reel**
again opens the one already made. Cost: the same calls as a Reel's draft without its edit plan, so a little less
than a Reel: the music pick, Studio's read and any of Studio's own fixes (shown on the draft as usual); a
send-back with notes adds one short call (about a cent); a note that's only a track link adds none. Studio can't line slide changes up with the music's beats yet
(Epidemic Sound's tools don't give Studio a track's beats), so slides change on their reading times.

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

**Ideas from the wiki.** When there isn't footage of you explaining something, Studio also suggests posts from
Urban Makers' own wiki (the trade articles in the quote engine: tiling, carpentry, electrical, plumbing, permits and
so on). Mostly carousels, each built on one article's facts: sizes, the order of works, checks, mounting heights,
rules of thumb. Such a suggestion's line on Plan says **From the wiki:** and the article's title, and its purpose
carries the article's key facts, so **Write slides** has the detail. Before anything from the wiki reaches Claude,
Studio takes out everything that mustn't go public: prices and anything about money, quotes or margins, internal
notes, addresses, postcodes and unit numbers, estates and towns, clients' and projects' names, phone numbers and
emails, supplier and brand names, case studies, image placeholders and unchecked facts; directories (supplier lists,
showrooms) are left out whole. Articles used least so far go first, and an article already behind a suggestion
waiting for you isn't offered again. Studio reads the wiki at most once a day (read-only) and keeps a copy, so it
still works when the wiki can't be reached. It adds a few pages of reading to each run's one call (at most about 3,500 tokens, about
US$0.015 a run); there's no extra call. Settings: `STUDIO_WIKI_URL`, `STUDIO_WIKI_JWT`, `STUDIO_WIKI_ANON_KEY`
(the CTO sets them on the server); blank = no wiki.

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
  retried a day later (one Instagram refused: once Instagram lets the server ask again, below);
- once nothing is waiting, if the references differ from the ones the style guide was built
  from, Claude drafts a new **style guide**: the house style, a short report, and **what
  changed** (and why, tied to the references added or retired and their notes). **Update style
  guide** in the planner drafts one on demand (after editing notes, say). A rejected draft isn't
  drafted again until the references change;
- when Bryan approves a draft, the laptop writes it into `playbook.md` (replacing the previous
  house style) on its next sync.

How Studio "watches" a reel (since 2026-10-26): before concluding anything it lines up the stills
and the timed transcript as one timeline of beats (each still, what is said until the next one, the
cuts in between), then reads the structure from it: how the reel opens, holds attention, turns and
closes. Each finding says whether a still showed it, the words said it, or it is Studio's inference;
what the stills could have missed (shots no still landed in) is listed as a gap; and it ends with the
three strongest observations and their times. The style guide and the taste report count the patterns
across references ("8 of 12 open with the result first"). Same single Claude look per reel (about
US$0.065 instead of 0.05: ~650 more output tokens, ~500 more input). References analysed before keep
their old read.

YouTube links (watch, youtu.be, Shorts) aren't downloaded (YouTube blocks servers): Gemini watches the
video from its link and answers in the same shape, with its numbers marked as estimates (kept out of
the averages) and YouTube's thumbnail as the still. It uses the Gemini key (`setup.sh key gemini`),
costs about US$0.05 each (`STUDIO_REFERENCE_VIDEO_COST_USD`, counted in the reference budget), watches
at most the first 3 minutes (`STUDIO_REFERENCE_VIDEO_MAX_SECONDS`); the model is
`STUDIO_REFERENCE_VIDEO_MODEL`. A video Gemini refuses or can't open fails with the reason and isn't
tried again by itself.

Retire a reference rather than deleting it when it no longer represents the style.

**When Instagram won't let the server download a post** (since 2026-10-27). References added by hand
always go first: a new one is tried once straight away, even while the library is paused. If
Instagram refuses, the card says "Instagram is refusing the server right now" and Studio tries again
by itself once Instagram lets it (never in a loop). Meanwhile:

- **Upload the file instead**, on the failed card or the reference's page: the post's video, or its
  pictures (up to 20, in name order). Studio analyses them exactly as a download and keeps the link
  for likes and views. Phones: pick from Photos.
- Or drop it in OneDrive: `Studio Inbox > References > Inspiration` (or `Ours`), with the post's code
  in the file or folder name (the part after `/p/` or `/reel/`, e.g.
  `markynv-editing-details-DeKIm8tsyWj.mp4`). It attaches to that reference, notes and all, instead of
  making a new one. A **folder of pictures** is one carousel (slides in name order), taken once nothing
  has changed in it for 5 minutes; a folder without a code becomes a new carousel reference. A loose
  picture in `References\Inspiration` is left alone, as before. A reference made from such a file
  before (a duplicate) is merged into its link reference and retired, once.
- **The sign-in account needs attention** shows at the top of References (and pauses the library) when
  every download with the sign-in has failed for 2 hours or more, or Instagram answers with a check
  (challenge, login page): open Instagram with the spare account on a phone, clear any check, then
  upload a fresh cookies.txt in "Instagram sign-in for downloads". A temporary limit only pauses (6 hours).
  Studio keeps a small record of how Instagram answered (`Studio/Style/ig_outcomes.json`: time, tool,
  status, signed in or not; never a cookie).

### Many references at once, and the taste report

When the team has a long list of posts it likes (say an export of 800 saved Instagram posts),
import them in one go: **References** > **Import many…**, then paste the links (one per line) or
choose the CSV file (a `url` column; an `account` and `type` column are kept too), and say whether
they're **Inspiration** or **Ours**. The same post twice (a `/reel/` and a `/p/` link to one post,
or one already in References) is skipped. Imported posts go in the **Library** on the References
page: a progress line ("312 of 814 analysed · 40 couldn't be downloaded"), a paged grid you can
filter by kind of post and account, and each post's own page.

**Nothing is downloaded or spent until Bryan presses Analyse.** The Library shows the cost first:
**Analyse all** (about US$0.06 a post: about US$49 and about 8 days for 814) or **Analyse a sample of
150 first** (about US$7.50 and 1-2 days, spread across kinds of post and accounts, at most two per
account). Studio then works through them on the server slowly and steadily, at most
`STUDIO_REFERENCE_LIBRARY_PER_HOUR` (default 4) an hour, spaced at random, so Instagram keeps
trusting the server (814 posts take about a week; references added by hand always go first; while
a pause is on, the library asks Instagram nothing): when Instagram refuses, it waits longer each time (10 minutes, then 20, 40 …), and
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
2. `.\venv\Scripts\python worker.py --style-study reels.txt` (on the server: `python worker.py --style-study
   reels.txt` as user studio). It runs while Studio runs; a second study waits until the first is done.
3. Read `Studio\Style\<date>\report.md`, and `house_style.md` (the proposed playbook section).
4. Happy with it: `.\venv\Scripts\python worker.py --style-apply`. Run another study later and
   apply it again; it replaces the earlier house style instead of adding a second one.

A reel Instagram won't hand over (yt-dlp, then gallery-dl, both with the sign-in when there is one, then
yt-dlp signed out) is skipped with the reason ("Instagram is refusing the server right now ... Or upload the
file instead"; the downloaders' own errors in the log); it never goes on to Claude without its video. A study where nothing could be measured
leaves no folder behind, and each study starts by removing empty dated folders in `Studio\Style`.

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

**Insights: what kinds of posts work** (Plan > More > **Insights**, or **What works: Insights** on Results). Each post
is scored a week after it goes out against your typical post of the same format (the middle of the 20 before it): 100
is typical, 150 half as good again. The score counts saves, shares, follows, profile visits and comments per 1,000
people reached, and for Reels how much of each Reel people watch. Likes are shown but never judged. Studio then
compares what it knows about each post because it made it (kind of post, format, hook formula, length or slides, look,
voice-over, picture and drawn inserts, sound effects, music mood, first cut, cards, caption length, day and time, where
the topic came from, your notes and rounds) with the rest. Small groups are pulled toward typical before anything is
shown, every line says how many posts it rests on, and a finding is **proven** only with 8 or more posts on each side,
very unlikely to be luck and at least 15% apart; otherwise it's an **early signal** (a hunch to test). One post is
never a pattern. Posts from your phone count by format and time only.

Every Monday at 7 (before the suggested topics) Studio first forecasts the week's scheduled posts, then grades past
forecasts against their real numbers and against the plain guess "every post is typical". The grade sits at the top of
the page and the memo: "beats average by X%", or "not better than average yet: treat suggestions as hunches" (the
target is 25% better within 12 weeks). Then one Claude call (about US$0.10) writes the memo: Do more, Do less, Try
next, each with its numbers and n, and what the numbers can't tell yet. Memos are kept (`Studio/insights/<date>.json`)
and earlier ones are one tap away. Its top lines go to the suggested topics, and its Do more / Do less sit at the end of
Studio's own playbook section as soft guidance, never rules: your playbook and your calls always win. Nothing on the
page changes a post. Settings: `STUDIO_INSIGHTS=0` (off), `STUDIO_INSIGHTS_DAY` / `_HOUR` (monday, 7),
`STUDIO_INSIGHTS_MODEL`.

**The grid** (Plan > More > **Grid**, or **See it as the grid** on the posting calendar) shows the whole profile as a
phone does, before anything goes out: the header (name, handle, bio, counts, read from the account each morning), then
three posts to a row, newest at the top: pinned posts first, then what's planned (ideas with a posting date, drafts in
Review, approved posts), what's scheduled, and what's already posted (phone posts too). A Reel kept out of the grid
(Reels tab only) doesn't show; **Show them** dims them in place.

- **Change the order**: drag a post (on the phone, hold it a moment first; on a keyboard, Space picks it up, the arrows
  move it, Space drops it). The days stay where they are and the posts move through them; **Swap two** (or Alt while
  dropping) makes two posts trade days instead. Posted and scheduled posts don't move. Nothing changes until **Save this
  order**; **Undo** goes back to the last saved order (and the toast after saving has its own Undo). A fine line marks
  each row, and a note says when the posts coming up don't fill whole rows.
- **Spread these…**: pick a project's posts in the order they should go out (tick them, or type their numbers like
  24-44), a start day and the weekdays (Mon, Wed, Fri), and each gets the next day in turn. Then Save this order.
- **Tap a post**: its slides (swipe), caption, status and Studio's read. For a post that isn't made yet you can change
  the caption, add many photos at once, drag them into order, make one the cover, remove one, and **Confirm photos**.
  Changing the photos after confirming asks again. The posting date changes there too.
- **Pins**: Instagram doesn't let Studio pin, so **Pin to the top** pins it in the grid and Plan reminds you to pin it in
  the Instagram app once it's out (hold the post, then Pin to your profile). Three at most, as on Instagram.
- **Show it in the profile grid** (a Reel): unticked, it goes out to the Reels tab only. Scheduling starts from it.
- **Needs a fact from Bryan**: a post flagged this way (its Details), or whose words have a `[check]` where a figure goes,
  shows an orange **?**. The **Key** button on every page with marks says what each one means: Posted, Scheduled,
  Approved, Draft in Review, Idea, Needs a fact.

**Photos only**: on a carousel, **Photos fill the slide** makes each photo the whole slide (4:5, never cropped or made
bigger; 2160x2700 photos stay as they are), with an optional short **cover line** on slide 1 only. **Add photos** takes
many at once (pick several from Photos on the phone, or drop a folder on a computer), in file-name order, up to 10, as
Instagram allows; past 10 it says which were left out. Before the database update, the grid page says it needs
`cloud_setup.py --update`; until Instagram is connected the header comes from `STUDIO_INSTAGRAM_HANDLE`,
`STUDIO_INSTAGRAM_NAME` and `STUDIO_INSTAGRAM_BIO`. The cover line is set small, in the serif, at the bottom.

**Photos from OneDrive, many carousels at once** (`showcase.py`, `showcase_batch.py`): a batch file in `batches/` lists
ideas with their posting date, title, caption, grid comment and, for those to make now, the photos by name from a
OneDrive folder and the cover line. On the laptop, `.\venv\Scripts\python showcase_batch.py batches\<file>.json
--folder "<the folder's full path from OneDrive's top>"` shows what it would change and checks every photo in OneDrive;
add `--apply` to change the ideas and send the carousels to Studio (like Make this). A photo's name is its file name
without the extension: "11" at the top of the folder, "Other 12" for 12 in the subfolder starting "Other" ("Other
photos/12.jpg"). Studio fetches the photos in that order; if one is missing or a name fits two pictures, that carousel
isn't made and says which. Nothing is posted or scheduled.

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

## 19. Brand kit: fonts, colours and logo

**More > Brand kit** (in Review; the planner's More links to it) holds the fonts, colours and logo every Reel,
carousel and slideshow Reel is drawn in, like a brand kit in Canva. Until it's changed it is exactly today's look
(Site Notes for Reels, the green and amber slides), so nothing changes by itself.

- **Start from**: Site Notes (one bold font, words on dark labels), Gallery (a serif title, captions that glow) or
  Classic (boxed titles and captions). Picking one puts its colours and fonts back.
- **Colours**, by what they colour. Reels: **Words** (captions and the words on labels and cards), **Labels**
  (behind the hook, callouts and cards), **Key word** (the key word, callout labels, the handle), **Words on key
  colour**, **End card** (the shade over the last frame). Carousels: **Background**, **Headings**, **Body**,
  **Accent** (the bar, tags, Swipe, the ask) and **Last slide**. Pick a colour or type its #code.
- **Fonts**: Reels' **Titles** and **Captions**, carousels' **Headings** and **Body**. Pick one of Studio's fonts,
  or add your own: drop a .ttf, .otf or .woff2 (up to 3 MB) on **Add a font**. Studio reads the file itself (never
  trusting its name) and refuses anything that isn't a font with Latin letters. Add each weight you have; titles
  need a bold one.
- **Logo**: a PNG (a see-through background works best) in place of the Urban Makers mark on the end card and the
  slides. **Use the Urban Makers mark** goes back.
- **Kit for** a kind of post (Know-how, Ideas, On site, Finished homes, Work with us): change a colour or font for
  that kind only; everything else follows the main kit. **Use the main kit's** undoes one.
- The **preview** is the real page each render uses, in the kit as you edit it: a Reel frame (**Title**,
  **Callout**, **Card**, **End**; on a phone one button steps through them) and a slide (**Cover**, **Point**,
  **Last**). On a phone it stays at the top while you scroll.
- **Check these** says when words are hard to read (under 4.5 to 1 on what they sit on, as WCAG measures it) or a
  font has no bold the look needs (Studio then uses the look's own font, and says which).
- Nothing changes until **Save**. New drafts use it. A draft keeps the kit it was made with: **Edit words** and
  Studio's own fixes keep its look; a send-back, a new edit and **Edit it again** use the kit of the day (Edit it
  again remakes a waiting draft in it). Removing an uploaded font keeps its file, so drafts made with it still render.
- Bryan and Michelle can change it (`STUDIO_BRAND_KIT_TEAM=0` leaves it to Bryan alone). Kept on the Studio machine:
  `Studio\brand_kit.json`, fonts and logos in `Studio\BrandKit\` (`brand_kit.py`, `brand_kit_routes.py`). The
  planner's small slide previews take the saved slide colours from Studio's heartbeat. Drawn animations, the
  picture's Concept label and the DaVinci Resolve export's drawings keep Site Notes' own look for now.

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
plus the transcript. AI concept pictures for carousels, and the pictures a Reel shows instead of a drawing, are Google
Gemini's, about US$0.03 each (`STUDIO_IMAGE_COST_USD`; a Reel at most 4 new ones, `STUDIO_REEL_PICTURES_MAX`); the
dashboard shows them next to the Claude cost. Sound effects on the Reels' graphics cost nothing (Epidemic Sound's are
in the subscription, Openverse's are CC0, each fetched once). The Insights memo is one Claude call a week (about 10 cents). Suggested topics are one Claude
call a week (plus any when the month falls behind or someone presses Suggest topics), a few cents each. Check the first week's usage in the Anthropic console before you
send it a backlog of hundreds of clips. The words filter (section 17) adds about a cent per draft or idea run when a
rewrite is needed, and the voice file about 10 cents each time it's written. To spend less, set `STUDIO_CLAUDE_EFFORT=low` or
lower `STUDIO_MAX_FRAMES`.
