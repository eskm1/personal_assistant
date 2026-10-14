# Studio worker (phase 1: capture and understand)

Film on your phone and the video lands in a Google Drive folder. Your Windows laptop at
home picks it up, transcribes it, and has Claude tag it as Personal or Urban Makers.
It also labels the format (talking head, day in the life or compilation footage) and the
best moments, then files it in a searchable library. Ava pings you on Telegram once each
clip is done.

This is the first phase of the Content Studio plan. Ideas, auto-editing and review come
next and build on the catalogue this creates.

```
Phone ──share──► Google Drive "Studio Inbox" ──sync──► laptop
                                                       │  ffmpeg: audio + frames
                                                       │  Whisper: transcript (eGPU if attached)
                                                       │  Claude: brand, format, moments
                                                       ▼
                         %USERPROFILE%\Studio\Library\2026-10\2026-10-14_1532_IMG_1234\
                             IMG_1234.MOV  transcript.json  analysis.json  frames\
                         %USERPROFILE%\Studio\catalog.db   (one row per clip)
                                                       │
                                                       ▼
                                              Telegram: "Studio: Kitchen tiling day 2…"
```

It runs on its own machine and shares nothing with the bot on the server except the
Telegram bot token, which it uses to send messages only. The server's auto-deploy
ignores this folder.

## 1. Keep the laptop awake at home

The laptop is the server now. When you take it out, uploads queue in Drive and are
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
5. **eGPU:** leave it plugged in at home. With an NVIDIA card, Whisper runs on it (much
   faster than the CPU). If it's unplugged, the worker falls back to the CPU and switches
   back once it's reconnected. AMD and Intel cards are ignored for now, since the CPU
   handles phase 1 fine.

## 2. Install the tools (one time)

In **PowerShell**:

```powershell
winget install Python.Python.3.12
winget install Gyan.FFmpeg
winget install Git.Git
winget install Google.GoogleDrive
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
# NVIDIA eGPU only:
.\venv\Scripts\pip install -r requirements-gpu.txt
```

The first clip downloads the Whisper model (about 1.6 GB for the GPU model, 0.5 GB for
the CPU one). Later clips reuse it.

## 4. Set up the inbox

1. Sign in to **Google Drive for desktop** and create a folder in My Drive called
   `Studio Inbox`. On the laptop it appears as `G:\My Drive\Studio Inbox` (the drive
   letter may differ).
2. On your **iPhone**, either:
   - In the Drive app, tap **+ → Upload** and pick videos, or
   - Make a one-tap share action. In **Shortcuts → +**, turn on **Show in Share Sheet**
     and set it to receive **Media**. Add **Save File** with the destination
     `Google Drive › Studio Inbox` and **Ask Where to Save** off, and name it
     "Send to Studio". From Photos, select clips, tap **Share → Send to Studio**.
   - On Android, share to Drive and choose the `Studio Inbox` folder.

Upload the original files, not compressed copies. Processed clips are moved out of
Drive into the library, so Drive storage only holds the queue.

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

## Day to day

| Task | Command (from `studio`, with `.\venv\Scripts\python`) |
| --- | --- |
| See what it's doing | open `%USERPROFILE%\Studio\logs\worker.log` |
| Retry clips that failed (no internet, API down) | automatic when the worker starts, or `worker.py --retry-failed` |
| Process one specific file | `worker.py --file "D:\footage\clip.mov"` |
| Transcribe and catalogue without Claude | add `--no-claude` |
| Stop / start the background worker | Task Scheduler → **Studio Worker** → End / Run |

Files the worker can't use are moved out of the inbox, not deleted. They go to
`Studio\Duplicates` (already in the library) or `Studio\Unreadable` (not a video).

## Cost

Whisper runs locally for free. Each clip is one Claude call with up to 20 small frames
plus the transcript. Check the first week's usage in the Anthropic console before you
send it a backlog of hundreds of clips. To spend less, set `STUDIO_CLAUDE_EFFORT=low` or
lower `STUDIO_MAX_FRAMES`.
