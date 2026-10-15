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
It's laid out like Reels: a grid of tiles you can filter by account, with tabs for each stage.

| Tab | What's in it |
| --- | --- |
| To review | Edited drafts waiting for you |
| Ideas | Posts Claude planned from your library, waiting for Make this or Dismiss |
| Being edited | Drafts in progress, including ones you sent back |
| Library | Raw clips, tagged by Claude |
| Ingesting | Clips being processed, or that failed and will be retried |
| In the cloud | Uploads still in the OneDrive inbox |
| Done | Approved, posted and rejected edits |

Open a tile to watch it. Pause on a moment, type a note, and it's pinned to that time. Tick
**Remember for future edits** and the note is also added to `Studio\playbook.md`, the rules
the editor follows on every edit. Then **Request changes**, **Approve** or **Reject**: the next
draft opens straight away while the editor revises the last one in the background. You also
write or approve the post caption here. Keyboard: J / K for next and previous, Space to play,
Esc to close, Ctrl+Enter to add a note.

**Ideas:** on the Ideas tab, press **Suggest posts** (optionally with a focus such as "kitchen
projects this month"). Claude reads your tagged clips, their timestamped transcripts and best
moments, `brands.md` and the playbook, and proposes posts built only from footage you have:
a hook, why it works, the structure, the exact clip segments, and a draft caption. Open one
to play each segment, edit the caption, then **Make this** (it moves to Being edited for the
editor) or **Dismiss**. One run costs one Claude call over your library, roughly tens of
cents for a few dozen clips.

**The playbook** lives at `Studio\playbook.md`. It starts with editing rules adapted from
Nate Herk's Edit Kit, and notes you mark **Remember for future edits** are added at the
bottom. Ideas and the editor both follow it; edit it freely.

The editor isn't built yet (phase 5), so ideas you make wait in Being edited and **To review**
stays empty for now. To try the full review flow with sample videos, run
`.\venv\Scripts\python demo.py`, which uses a separate throwaway folder, and open the
address it prints.

**From your phone or desktop when you're out:** install [Tailscale](https://tailscale.com) on
the laptop and on your phone or desktop, signed in to the same account. Then set in `.env`:

```
STUDIO_DASHBOARD_HOST=0.0.0.0
STUDIO_DASHBOARD_PASSWORD=pick-a-long-password
STUDIO_DASHBOARD_URL=http://<laptop's Tailscale name>:8765
```

Restart the worker and open that address; any username works, with that password. The
dashboard won't listen beyond the laptop without a password. With `STUDIO_DASHBOARD_URL` set,
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
2. Cut points are moved out of words into the gaps around them, with a little air.
3. ffmpeg cuts every piece from your **original** files in one pass and frames it 1080x1920
   (horizontal footage is centre-cropped for now).
4. HyperFrames renders the captions (1 to 3 words, timed to each word) and the hook as a
   transparent layer over the footage.
5. Music: Urban Makers posts get an Epidemic Sound track that dips under your voice.
   Personal posts get no music, plus a note on what to add in the Instagram app.
6. The draft is re-transcribed to check no word was clipped, and its loudness is measured
   (target -14 LUFS). Both show under **Checks** on the review screen.

Drafts are saved in `Studio\Renders`. If an edit fails, the draft shows the reason and a
**Retry edit** button. To edit one production by hand: `worker.py --edit <id>`. To run the
worker without the editor: `--no-editor`. `python music.py` saves Epidemic Sound's full tool
list to `Studio\logs\epidemic_tools.json`, which is useful when the music step misbehaves.

Each draft costs one Claude call to plan the cut plus a few short calls to pick music,
typically tens of cents.

## Day to day

| Task | Command (from `studio`, with `.\venv\Scripts\python`) |
| --- | --- |
| See what it's doing | open `%USERPROFILE%\Studio\logs\worker.log` |
| Retry clips that failed (no internet, API down) | automatic when the worker starts, or `worker.py --retry-failed` |
| Process one specific file | `worker.py --file "D:\footage\clip.mov"` |
| Transcribe and catalogue without Claude | add `--no-claude` |
| Stop / start the background worker | Task Scheduler → **Studio Worker** → End / Run |
| Run the worker without the dashboard | add `--no-dashboard` |

Files the worker can't use are moved out of the inbox, not deleted. They go to
`Studio\Duplicates` (already in the library) or `Studio\Unreadable` (not a video).

## Cost

Whisper runs locally for free. Each clip is one Claude call with up to 20 small frames
plus the transcript. Check the first week's usage in the Anthropic console before you
send it a backlog of hundreds of clips. To spend less, set `STUDIO_CLAUDE_EFFORT=low` or
lower `STUDIO_MAX_FRAMES`.
