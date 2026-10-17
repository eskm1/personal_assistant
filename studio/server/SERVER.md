# Studio on a server

Studio can run on a rented server instead of the laptop. Footage, editing, the review dashboard
and the planner then work around the clock, from anywhere, with the laptop off.

| What | Where | Cost |
| --- | --- | --- |
| Studio (the worker and the review dashboard) | Hetzner Cloud **CPX42**: 8 vCPU, 16 GB RAM, 320 GB disk, in the EU | about €25–30 a month |
| Server backups (a daily image, 7 kept) | Hetzner, a tick box | +20% (about €5–6) |
| Footage in and finished reels out | your OneDrive (already paid for) | free |
| Planner | Netlify and Supabase (as now) | free |
| Claude, Epidemic Sound | as now, per use | unchanged |

Hetzner raised its prices in 2026, so check the price the console shows before you buy. Pick an EU
location (Nuremberg, Falkenstein or Helsinki). Singapore costs about half as much again and includes
only 1 TB of traffic a month; the EU locations include 20 TB. Distance doesn't matter much here:
your phone uploads to OneDrive, which sits close to you, and the server collects from OneDrive
datacentre to datacentre. Only the dashboard's previews travel the long way, and a reel is about
30 MB.

**Use a new server, not the one that runs theurbanmakers.com, Bob and Ava.** Video work would take
over that server's processor for minutes at a time and slow the website down.

## What changes

- **Footage**: you and your colleague drop videos in the OneDrive folder **Studio Inbox** (one
  subfolder per project, as now), from the OneDrive app on your phones or from a computer. Within a
  couple of minutes of an upload finishing, the server collects it. The original then moves to
  **Studio Archive** in OneDrive, so OneDrive keeps a copy and the inbox shows only what's still waiting.
- **Reference reels you saved as videos** go in **Studio Inbox > References > Ours** or **> Inspiration**.
- **Dashboard**: **https://studio.theurbanmakers.com**, on any phone or computer, with the same
  accounts as now. No Tailscale needed.
- **Approved reels** are copied to **Studio Ready to post** in OneDrive, so you can post them from
  your phone. Every draft also has a **Download video** button.
- **DaVinci Resolve**: "Make Resolve project" now gives you a **Download (.zip)** with the project
  and the original clips. Unzip it on your computer and open it as before (the HOW TO OPEN.txt
  inside covers relinking the clips).
- **The laptop** stops running Studio. Its desktop icon opens the server's dashboard instead.
- **Updates** install themselves on the server, as they do on the laptop now. **Restart Studio**
  in the account menu still works.

## Moving (about 45 minutes, once)

### 1. A key for the laptop to sign in to the server (2 min)

In PowerShell on the laptop:

```powershell
ssh-keygen -t ed25519
```

Press Enter at every question. Then copy the public half:

```powershell
Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub | Set-Clipboard
```

(If `ssh-keygen` says the file already exists, answer **n** and just run the copy command.)

### 2. Rent the server (5 min)

In the Hetzner Cloud console (console.hetzner.com), make a **new project** called `Studio`, so it
stays apart from the website's server. Open it and click **Add server**:

- **Location**: Nuremberg, Falkenstein or Helsinki
- **Image**: Ubuntu 24.04
- **Type**: Shared vCPU, **x86 (Intel/AMD)** > **CPX42** (8 vCPU, 16 GB, 320 GB). Not Arm64:
  the headless Chrome that renders captions has no Arm build for Linux. If CPX42 is sold out
  there, try another of the three locations.
- **Networking**: keep Public IPv4 and IPv6 ticked
- **SSH keys**: **Add SSH key**, paste from step 1, and name it "Studio laptop"
- **Volumes**: none for now
- **Firewalls**: optional. The setup script switches on the server's own firewall either way.
  For a second layer, create one that allows only TCP 22, 80 and 443 in.
- **Backups**: tick it
- **Placement groups, labels, cloud config**: leave empty
- **Name**: `studio`

Click **Create & Buy now**, then copy the server's **IPv4** address (for example 49.12.34.56).

### 3. Point studio.theurbanmakers.com at it (2 min)

In GoDaddy, open the theurbanmakers.com DNS records and **add one record**:

| Type | Name | Value | TTL |
| --- | --- | --- | --- |
| A | `studio` | the server's IPv4 | 1/2 hour |

If a `studio` record is already there from the earlier tunnel tries, edit it instead. Nothing
else on the domain changes: the website, `app` and email keep their own records.

### 4. One command on the laptop (30 min, mostly waiting)

Leave Studio running. In PowerShell:

```powershell
cd $env:USERPROFILE\personal_assistant\studio
.\venv\Scripts\python server_move.py 49.12.34.56
```

Use your server's address in place of 49.12.34.56. The command walks you through the rest and
asks you for three things:

1. **A GitHub "deploy key"**, so the server can fetch Studio's updates (read only). It shows the
   key and opens the right GitHub page. Paste the key, tick nothing, and click **Add key**.
2. **A OneDrive sign-in**: a browser window opens. Sign in with the Microsoft account the Studio
   Inbox belongs to. If `rclone` isn't installed, it first tells you to run
   `winget install Rclone.Rclone`.
3. **The move itself**: it stops Studio on the laptop, then copies the library, the drafts and the
   catalogue to the server. This is the slow part: about 10 minutes per 10 GB on a home fibre line. If the
   connection drops partway, run the command again; the copy starts over from the beginning.

When it's done it prints **Done: open https://studio.theurbanmakers.com**. Sign in with your usual
account.

If anything stops it (a typo, the Wi-Fi), run the same command again. Finished steps are skipped.

### 5. Your colleague (5 min)

- In OneDrive, share **Studio Inbox** (and, if useful, **Studio Ready to post**) with them, with
  editing allowed. They upload from the OneDrive app (**+ > Upload**).
- Their dashboard and planner logins stay the same.

## Good to know

- **Is it safe?** Only SSH (key only, no passwords) and the website ports are open. The dashboard
  needs a login, the same as now, and the connection is HTTPS. Keys and passwords are in one file on
  the server that only Studio can read. Ubuntu installs security updates by itself.
- **Disk**: 320 GB holds about 2,000 clips. OneDrive's Studio Archive keeps every original as well,
  so the server's copy isn't the only one. The dashboard shows the free space. When it gets tight,
  add a Hetzner Volume (about €0.05 per GB a month) or ask Claude to have Studio remove old originals
  that are already archived.
- **Instagram links on the References tab**: Instagram sometimes refuses downloads from servers.
  If a reference says it "needs a login", save the reel to your phone and drop it in
  Studio Inbox > References > Inspiration. Or put a `cookies.txt` exported from a browser signed in
  to a spare Instagram account in `/home/studio/Studio` on the server.
- **Accounts** move across with the catalogue, so everyone signs in as before. The dashboard's
  first-time setup page only works on the machine itself, so a new owner account on a fresh
  server is made over SSH:
  `ssh root@<server address>`, then
  `cd /home/studio/personal_assistant/studio && sudo -u studio -H venv/bin/python worker.py --add-user you@example.com Bryan --owner`.
- **Checking on it** (rarely needed): in PowerShell on the laptop, run
  `ssh root@<server address> bash /root/studio-server/setup.sh status`. It shows whether Studio is
  running, its latest log lines and the disk use.
- **Going back to the laptop**: delete `Studio\MOVED_TO_SERVER.txt` on the laptop and re-enable the
  Studio Worker task in Task Scheduler. The laptop's copy of the library is as it was on the day of
  the move.
