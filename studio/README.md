# Studio worker and dashboard

Film on your phone and upload the video to a OneDrive folder. Your Windows laptop at
home picks it up, transcribes it, and has Claude tag it as Personal or Urban Makers.
It also labels the format (talking head, day in the life or compilation footage) and the
best moments, then files it in a searchable library. Every video shows up on the
**dashboard**, a Reels-style review site the laptop serves, and Ava sends a Telegram ping.

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
                      Telegram: "Studio: Kitchen tiling day 2…" (notification only)
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
cd personal_assistant\studio
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
account, and the clearer it is, the better the sorting. Fill in the two TODO lines.

## 6. Check, test, and leave it running

```powershell
.\venv\Scripts\python worker.py --check     # ffmpeg, GPU, API key; sends a Telegram test
.\venv\Scripts\python worker.py --once      # process what's in the inbox now, with logs on screen
powershell -ExecutionPolicy Bypass -File .\install_task.ps1   # start at sign-in, from now on
```

Drop one short clip in the inbox before `--once`. It's picked up after it has been
unchanged for 20 seconds, and you should get a Telegram message within a minute or two.

## 7. The dashboard (where you review)

While the worker runs, it serves a review site at **http://127.0.0.1:8765** on the laptop.
It's laid out like Reels: a grid of tiles you can filter by account, with tabs for each stage.

| Tab | What's in it |
| --- | --- |
| To review | Edited drafts waiting for you |
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

Editing isn't built yet (phase 4), so for now **To review** stays empty, and the Library is
where you check Claude's tags. To try the full review flow with sample videos, run
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
Telegram messages link straight to the clip. Telegram is just for notifications now; the
dashboard is where you give feedback.

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
