# Personal assistant — project instructions

Three things live here:

- **Ava** (`bot.py`, `router.py`, `tools/`): the Telegram assistant. Runs on the server; `systemd/deploy.sh`
  pulls `main` every 2 minutes and restarts the bot, so a push to `main` is live.
- **Studio** (`studio/`): the video studio. Runs on Bryan's Windows laptop, not the server; it updates
  itself from the branch the laptop checked out (`studio/updater.py`). Setup is `studio/README.md`.
- **The online planner** (`studio/cloud/`): Netlify site plus Supabase Edge Functions. `CONTRACT.md` is
  the shape the laptop and the planner agree on.

## Talking to me

I have ADHD. Long replies cost me more than they tell me.

- **Lead with the answer.** What changed, what I have to do, what broke. One or two sentences.
- **Then stop.** Detail only if I ask, or if skipping it would let me ship something wrong.
- No recaps of what I just said, no restating the request, no summarising the summary.
- Reasoning belongs in the code comments and the commit message, not the reply.
- A list of five short bullets beats a paragraph. A number beats an adjective.
- Say it once. If it's in the commit message it does not need to be in the reply too.
- One question at a time, and only one I alone can answer. Pick sensible defaults for the rest and say
  which you picked.
- A setup step is a numbered list of what to click or type, never a page of background.

## Giving me terminal commands

My machine is Windows, and I only use **PowerShell**. I don't know which machine or shell a
command is meant for unless you say so.

- Label every command with where it runs: **[PowerShell — your PC]**, **[PowerShell — the Studio
  laptop]** or **[Server — after `ssh`]** (give the ssh line too). Netlify/Supabase/GitHub/Telegram
  steps are browser or app steps: say which site and which page.
- Commands for my PC must actually run in PowerShell. Never assume bash tools (`openssl`, `grep`,
  `cat`, `sed`) exist on Windows; use PowerShell or `python -c` equivalents.
- Remote commands inside a `.ps1` must not contain double quotes (Windows PowerShell 5.1 mangles
  them when calling `ssh`).
- Say which folder to run it from when it matters.

## Working style

- **Do the work, then report.** Commit and push your branch as soon as it's tested; don't wait for
  "commit". Don't hand me tasks to start or ask me to choose between options you could pick yourself.
- **One task, one branch** (`claude/<area>-<short-task>`). Never push to `main` unless I've said so in
  this conversation: `main` is Ava's live deploy.
- **Prove it before you say it's done.** Run what you changed: `python -m py_compile` on touched files,
  `studio/cloud/supabase/functions/tests` for the planner, and a real run where you can. If something
  wasn't tested, say so in one line.
- **End with one line:** `DONE | branch | what I have to do, or nothing`.

## Text the user sees (Ava's replies, Studio's dashboard and Telegram notices, the planner)

Helper text exists so someone mid-task keeps moving, not to document the feature.

- One short sentence, or 2–4 bullets when there are genuinely several things to say.
- Say what it does and what happens next. Never internals or jargon.
- Full stops, no em-dashes. No first person, no ALL-CAPS emphasis. Buttons are verbs (Save, Render,
  Send). An empty state names the thing and the one action.
- One message per batch, not one per item (Studio already does this; keep it that way).
- Ava writes for a phone: plain text, no markdown, the answer first.
