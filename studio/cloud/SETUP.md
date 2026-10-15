# Online planner: one-time setup

The planning board (projects, arcs, ideas, storyboards, votes, comments and the on-site
checklist) runs online, so you and your colleague can plan from anywhere, even with the Studio
laptop off. The laptop still does the heavy, private work (transcribing, quality checks, editing,
music, the footage itself) and syncs with the board whenever it's on.

| Piece | Where | Cost |
| --- | --- | --- |
| Planner website | Netlify (free) | free |
| Database, sign-in, live updates, stills | Supabase (free plan) | free |
| Organise / Refine with Claude | Supabase Edge Functions | API use only, a few cents per run |

About 30 minutes, once. You'll need your Supabase and Netlify logins and the laptop.

## 1. Create the Supabase project (5 min)

supabase.com > **New project**: name it `urban-makers-studio`, region **Southeast Asia
(Singapore)**, free plan. Choose a database password and keep it (step 2 asks for it once).
Wait until the project says it's ready.

## 2. Run the setup script on the laptop (10 min)

In PowerShell, in the `studio` folder:

```powershell
.\venv\Scripts\python cloud_setup.py
```

It asks for the project's address (paste it from the browser), opens the browser once to sign
the Supabase command-line tool in, asks for the database password, then creates the tables and
security rules, stores your Anthropic key as a Supabase secret (taken from `.env`), and deploys
the Claude functions (no Docker needed). It then asks for the project's **secret key**
(Supabase: *Project Settings > API Keys*, the `sb_secret_...` key; or the legacy `service_role`
key), checks it, saves it in `.env`, and makes your owner account. Run it again any time; it
skips what's done.

## 3. Supabase sign-in settings (2 min)

Supabase dashboard > **Authentication**:

- **Sign In / Providers > Email**: turn **off** "Allow new users to sign up". People join only
  by invite.
- **URL Configuration**: set **Site URL** to the planner's address (from step 4; come back to
  this) and add the same address under **Redirect URLs**. Invite and password-reset emails link
  there.

## 4. The planner website on Netlify (10 min)

Netlify > **Add new site > Import an existing project > GitHub** > `eskm1/personal_assistant`.

- Branch: `claude/sleepy-thompson-iewk09`
- Base directory: `studio/cloud/planner` (the build command and publish folder come from its
  `netlify.toml`)
- **Environment variables**: `SUPABASE_URL` = `https://<your project>.supabase.co`, and
  `SUPABASE_ANON_KEY` = the **publishable** key (`sb_publishable_...`) or the legacy `anon` key.
  Both are safe to publish: the database's security rules decide what anyone can see.
- Deploy. You get an address like `https://urban-makers-studio.netlify.app` (rename it under
  *Site configuration*). Netlify redeploys by itself whenever Studio is updated.

Optional, your own address: Netlify *Domain management > Add a domain* > `plan.theurbanmakers.com`,
then in GoDaddy add **one** record: CNAME `plan` > your `....netlify.app` address. Nothing else on
the domain changes.

Then:

- put the address in Supabase (step 3) and in the laptop's `.env` as `STUDIO_PLANNER_URL=...`;
- Studio account menu > **Restart Studio**.

## 5. Sign in, invite, move your plan over

1. Open the planner on your phone and computer and sign in with the owner account from step 2.
2. **Team** page > invite your colleague by email. They get an email, set a password and are in.
   They can plan, vote, comment and tick shots; Go / Park / Dismiss and "Make this" are yours.
3. If you already planned on the laptop (say, Kensington), move it online once:
   `.\venv\Scripts\python worker.py --cloud-import`. From then on the laptop dashboard's Plan tab
   links to the planner.

## Good to know

- **The laptop syncs** about every 30 seconds while it's on: footage gets matched to shots, stills
  and quality verdicts appear on the board, music shortlists and "Make this" run. While it's off
  those wait, and the board shows "Studio laptop off since …". Planning itself never waits.
- **Free plan limits** are far above what two people need: 500 MB database, 1 GB of stills
  (a still is ~30 KB), and Claude jobs split into short steps to fit the 150-second function limit.
  Supabase pauses a free project after a week with no activity; the laptop's check-ins keep it
  awake, and a paused project is restored with one click in the dashboard.
- **Security**: invite-only sign-in; every table is locked to members by row-level rules; stills
  are in a private bucket. The secret key lives only in the laptop's `.env` and in Supabase; never
  paste it anywhere else.
