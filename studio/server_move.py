"""
Moves Studio from this Windows laptop to its own server, in one go. Run it on the
laptop, in PowerShell in the studio folder, once the Hetzner server exists (made
with this laptop's SSH key) and the DNS record is added:

    .\\venv\\Scripts\\python server_move.py <server-ip> [--domain studio.theurbanmakers.com]

It walks through, asking only for what it can't find itself:
  1. checks it can sign in to the server (ssh root@<server-ip>),
  2. prepares the server (server/setup.sh install: packages, firewall, HTTPS, the studio user),
  3. puts Studio's code on it (shows you a read-only GitHub "deploy key" to add, if needed),
  4. connects the server to OneDrive, where the footage arrives (a browser opens to sign in),
  5. copies the settings from .env, leaving out what only makes sense on this laptop,
  6. stops Studio on this laptop for good and sends everything in the Studio folder over,
  7. puts it in place on the server, updates the file paths stored in it, starts Studio there,
  8. waits for https://<domain> to answer and leaves a note here pointing to it.
Safe to run again: finished steps are skipped. --skip-data does everything except 6-7,
so the server gets ready while the laptop carries on. See server/SERVER.md.

Step 7 runs this same file on the server: server_move.py unpack <folder> --from-root ...
"""
import argparse
import base64
import io
import json
import os
import re
import shlex
import shutil
import socket
import sqlite3
import subprocess
import sys
import tarfile
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath

HERE = Path(__file__).resolve().parent
ENV_FILE = HERE / ".env"
SERVER_FILES = HERE / "server"   # setup.sh, Caddyfile, studio.service
STEPS = 8

# The server layout (server/SERVER.md).
HOME = "/home/studio"
REPO = f"{HOME}/personal_assistant/studio"
SERVER_ROOT = f"{HOME}/Studio"
INCOMING = f"{HOME}/Studio.incoming"
RCLONE_CONF = f"{HOME}/.config/rclone/rclone.conf"
REMOTE_DIR = "/root/studio-server"   # a copy of server/, so setup.sh finds its templates
REMOTE_SETUP = f"{REMOTE_DIR}/setup.sh"
AS_STUDIO = "sudo -u studio -H"

MOVED_NOTE = "MOVED_TO_SERVER.txt"         # on the laptop: Studio runs on the server now
SERVER_MARK = ".moved_from_laptop.json"     # on the server: this data came from the laptop
TASK = "Studio Worker"                      # install_task.ps1's scheduled task
SSH = os.environ.get("STUDIO_MOVE_SSH") or "ssh"   # tests point this at a stand-in

# Copied as-is, apart from these (step 6).
SKIP_NAMES = {"worker.lock", MOVED_NOTE, "__pycache__", "venv", ".venv", "node_modules", ".cache"}
SKIP_SUFFIXES = (".partial", ".part", ".tmp")
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm"}   # as settings.VIDEO_EXTS

# .env keys that only make sense on the laptop (step 5). STUDIO_DASHBOARD_PORT goes
# too: Caddy on the server always forwards to the default port.
LAPTOP_ONLY = {"STUDIO_ROOT", "STUDIO_INBOX", "FFMPEG_PATH", "FFPROBE_PATH", "STUDIO_YTDLP_COOKIES_BROWSER",
               "STUDIO_DASHBOARD_PORT"}


def say(text: str = "") -> None:
    print(text, flush=True)


def step(n: int, text: str) -> None:
    say(f"\n[{n}/{STEPS}] {text}")


def ask_yes(question: str, default: bool = False) -> bool:
    answer = input(f"{question} [{'Y/n' if default else 'y/N'}] ").strip().lower()
    return default if not answer else answer.startswith("y")


# ── Talking to the server ─────────────────────────────────────────────────────

def ssh(ip: str, command: str, *, data: bytes | None = None, capture: bool = False, check: str | None = None,
        timeout: float | None = None) -> subprocess.CompletedProcess:
    """Run a shell command on the server as root (Windows' built-in OpenSSH).
    data goes to the command's stdin: the only way settings and tokens travel, so
    they never appear on a command line. check: what to call it if it fails (stops)."""
    stdin = {"input": data} if data is not None else {"stdin": subprocess.DEVNULL}
    try:
        result = subprocess.run(_ssh_argv(ip, command), capture_output=capture, timeout=timeout, **stdin)
    except subprocess.TimeoutExpired:
        result = subprocess.CompletedProcess([], 255, b"", b"timed out")
    if check and result.returncode != 0:
        if capture:
            say(_text(result.stderr or result.stdout).strip()[-1500:])
        raise SystemExit(f"\nStopped: {check} failed on the server (see above). Fix that and run me again.")
    return result


def _ssh_argv(ip: str, command: str) -> list[str]:
    return [SSH, "-o", "StrictHostKeyChecking=accept-new", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
            "-o", "ServerAliveInterval=30", f"root@{ip}", command]


def _text(b: bytes | None) -> str:
    return (b or b"").decode("utf-8", "replace")


def put_file(ip: str, path: str, content: str) -> None:
    """Write a file on the server owned by studio, mode 600, sent over stdin."""
    script = f"umask 077; mkdir -p {shlex.quote(str(PurePosixPath(path).parent))} && cat > {path}.new && " \
             f"chmod 600 {path}.new && mv -f {path}.new {path}"
    ssh(ip, f"{AS_STUDIO} sh -c {shlex.quote(script)}", data=content.encode("utf-8"),
        check=f"writing {PurePosixPath(path).name}")


def read_server_file(ip: str, path: str) -> str:
    """A studio-owned file's contents ('' if it isn't there). Kept in memory, never printed."""
    return _text(ssh(ip, f"cat {path} 2>/dev/null || true", capture=True, check=f"reading {path}").stdout)


# ── 1. SSH ────────────────────────────────────────────────────────────────────

def _laptop_key() -> Path | None:
    for name in ("id_ed25519.pub", "id_ecdsa.pub", "id_rsa.pub"):
        key = Path.home() / ".ssh" / name
        if key.exists():
            return key
    return None


def check_ssh(ip: str) -> None:
    step(1, f"Signing in to the server (ssh root@{ip}).")
    if not shutil.which(SSH):
        raise SystemExit("  Windows' SSH client isn't installed. Settings > System > Optional features >\n"
                         "  View features > OpenSSH Client > Install, then reopen PowerShell and run me again.")
    forgot = False
    while True:
        result = ssh(ip, "true", capture=True, timeout=60)
        if result.returncode == 0:
            say("  Signed in.")
            return
        err = _text(result.stderr).lower()
        if ("identification has changed" in err or "host key verification failed" in err) and not forgot:
            say("  The server's identity changed since this laptop last signed in to that address\n"
                "  (normal after rebuilding or recreating the server).")
            if not ask_yes("  Forget the old one and continue?", default=True):
                raise SystemExit("Stopped.")
            subprocess.run(["ssh-keygen", "-R", ip], capture_output=True)
            forgot = True
            continue
        if "permission denied" in err:
            raise SystemExit(_key_help())
        raise SystemExit(f"  Can't reach {ip} ({_text(result.stderr).strip()[-200:] or 'no answer'}).\n"
                         "  If you just created the server, give it a minute to start and run me again;\n"
                         "  otherwise check the address in the Hetzner Cloud Console (Servers).")


def _key_help() -> str:
    key = _laptop_key()
    lines = ["  The server didn't accept this laptop's SSH key."]
    if key is None:
        lines += ["  This laptop has no SSH key yet. Make one (press Enter at every question):",
                  "      ssh-keygen -t ed25519",
                  "  then run me again: I'll show it to you."]
    else:
        lines += [f"  This laptop's key ({key}):",
                  "",
                  "      " + key.read_text(encoding="utf-8", errors="replace").strip(),
                  "",
                  "  Add it in the Hetzner Cloud Console:",
                  "    1. your project > Security > SSH keys > Add SSH key: paste the line above.",
                  "    2. Servers > the Studio server > Rebuild > Ubuntu 24.04 with that key (nothing is on",
                  "       the server yet, so a rebuild loses nothing). If Rebuild doesn't offer the key,",
                  "       delete the server and create it again with the key ticked under SSH keys.",
                  "  If the key has a passphrase, load it first: ssh-add (or make one without a passphrase).",
                  "  Then run me again."]
    return "\n".join(lines)


# ── 2. Prepare the server ─────────────────────────────────────────────────────

def prepare_server(ip: str, domain: str) -> None:
    step(2, "Preparing the server (packages, firewall, HTTPS, the studio user; quick when already done).")
    names = ("setup.sh", "Caddyfile", "studio.service")
    if not all((SERVER_FILES / n).exists() for n in names):
        raise SystemExit(f"  {SERVER_FILES} is incomplete: update Studio first (account menu > Update now).")
    bundle = io.BytesIO()
    with tarfile.open(fileobj=bundle, mode="w") as tar:
        for name in names:
            data = (SERVER_FILES / name).read_bytes().replace(b"\r\n", b"\n")   # bash chokes on Windows line ends
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(data), 0o755 if name.endswith(".sh") else 0o644, int(time.time())
            tar.addfile(info, io.BytesIO(data))
    ssh(ip, f"rm -rf {REMOTE_DIR} && mkdir -p -m 700 {REMOTE_DIR} && tar -xf - -C {REMOTE_DIR}",
        data=bundle.getvalue(), check="copying setup.sh")
    # STUDIO_SETUP_DRIVEN: setup.sh leaves out its "next, run ..." hints; this script runs those steps.
    ssh(ip, f"STUDIO_SETUP_DRIVEN=1 bash {REMOTE_SETUP} install {domain}", check="setup.sh install")


# ── 3. Code (GitHub deploy key) ───────────────────────────────────────────────

def git_origin() -> tuple[str, str, str]:
    """(owner, repo, branch) of this laptop's checkout."""
    def git(*args: str) -> str:
        r = subprocess.run(["git", *args], cwd=HERE, capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else ""
    url, branch = git("remote", "get-url", "origin"), git("rev-parse", "--abbrev-ref", "HEAD")
    m = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?/?$", url)
    if not m or not branch or branch == "HEAD":
        raise SystemExit("  Couldn't tell which GitHub repository and branch this Studio comes from\n"
                         f"  (origin: {url or 'none'}, branch: {branch or 'none'}).")
    return m.group(1), m.group(2), branch


def deploy_code(ip: str, owner: str, repo: str, branch: str) -> None:
    step(3, f"Putting Studio's code on the server (branch {branch}).")
    while True:
        code = ssh(ip, f"STUDIO_SETUP_DRIVEN=1 bash {REMOTE_SETUP} code {shlex.quote(branch)}").returncode
        if code == 0:
            return
        if code != 2:   # 2 = GitHub refused the deploy key; anything else is a real problem
            raise SystemExit("\nStopped: setup.sh code failed on the server (see above). Fix that and run me again.")
        key = _text(ssh(ip, f"cat {HOME}/.ssh/id_ed25519.pub", capture=True, check="reading the deploy key").stdout)
        page = f"https://github.com/{owner}/{repo}/settings/keys/new"
        say("\n  That's expected the first time: GitHub doesn't know the server yet. In the page that\n"
            "  opens (sign in to GitHub if asked):\n"
            "    Title: Studio server\n"
            "    Key:   paste this line\n\n"
            f"      {key.strip()}\n\n"
            "    Leave \"Allow write access\" unticked, then press Add key.\n"
            f"  ({page})")
        webbrowser.open(page)
        input("  Press Enter once it's added... ")


# ── 4. OneDrive ───────────────────────────────────────────────────────────────

def onedrive_folder(inbox: str, env=os.environ) -> tuple[str, str] | None:
    """Where the laptop's inbox is inside OneDrive: ("Studio Inbox", "OneDrive - Urban Makers"),
    or None when it isn't in a OneDrive folder."""
    path = PureWindowsPath(inbox)
    parts = [p.lower() for p in path.parts]
    for var in ("OneDriveCommercial", "OneDriveConsumer", "OneDrive"):
        base = PureWindowsPath(env.get(var) or "")
        n = len(base.parts)
        if env.get(var) and len(parts) > n and parts[:n] == [p.lower() for p in base.parts]:
            return "/".join(path.parts[n:]), base.name
    # OneDrive's own folder names, when the variables aren't set ("OneDrive - Urban Makers").
    for i, part in enumerate(path.parts[1:-1], 1):
        if part.lower().startswith("onedrive"):
            return "/".join(path.parts[i + 1:]), part
    return None


def parse_rclone_token(output: str) -> dict:
    """The OAuth token from `rclone authorize`'s output: JSON, or (newer rclone) base64 of it."""
    m = re.search(r"--->\s*(.+?)\s*<---", output, re.S)
    blob = (m.group(1) if m else output).strip()
    candidates = [blob]
    for decode in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            candidates.append(decode(blob + "=" * (-len(blob) % 4)).decode("utf-8"))
        except ValueError:
            pass
    for text in candidates:
        try:
            value = json.loads(text)
        except ValueError:
            continue
        for _ in range(3):   # the token may be wrapped: {"token": "{...}"}
            if isinstance(value, dict) and "access_token" in value:
                return value
            if isinstance(value, dict) and "token" in value:
                value = json.loads(value["token"]) if isinstance(value["token"], str) else value["token"]
            else:
                break
    raise ValueError("couldn't find the sign-in token in rclone's output")


def rclone_authorize(rclone: str) -> dict:
    """Runs `rclone authorize "onedrive"` (it opens the browser). Its messages are shown,
    the token block isn't."""
    proc = subprocess.Popen([rclone, "authorize", "onedrive"], stdout=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace")
    captured, hiding = [], False
    for line in proc.stdout:
        captured.append(line)
        if "--->" in line:
            hiding = True
            say("  (sign-in token received)")
        if not hiding:
            say("  " + line.rstrip())
        if "<---" in line:
            hiding = False
    if proc.wait() != 0:
        raise ValueError("rclone couldn't sign in")
    return parse_rclone_token("".join(captured))


def graph_drive(access_token: str) -> dict:
    """The signed-in account's OneDrive: {"id": ..., "driveType": personal | business}."""
    import requests
    r = requests.get("https://graph.microsoft.com/v1.0/me/drive",
                     headers={"Authorization": f"Bearer {access_token}"}, timeout=30)
    if r.status_code != 200:
        raise ValueError(f"Microsoft didn't return the OneDrive (HTTP {r.status_code})")
    drive = r.json()
    return {"id": drive["id"], "driveType": drive.get("driveType", "personal")}


def rclone_conf(token: dict, drive: dict) -> str:
    return ("[onedrive]\ntype = onedrive\n"
            f"token = {json.dumps(token, separators=(',', ':'))}\n"
            f"drive_id = {drive['id']}\ndrive_type = {drive['driveType']}\n")


def onedrive_works(ip: str) -> bool:
    return ssh(ip, f"{AS_STUDIO} rclone lsd onedrive: --max-depth 1", capture=True, timeout=120).returncode == 0


def onedrive_has(ip: str, folder: str) -> bool:
    return ssh(ip, f"{AS_STUDIO} rclone lsf --max-depth 1 {shlex.quote('onedrive:' + folder)}",
               capture=True, timeout=120).returncode == 0


def choose_inbox(server_env: dict, laptop_inbox: Path) -> tuple[str, str | None]:
    """The OneDrive folder footage arrives in (STUDIO_ONEDRIVE_INBOX), and whose OneDrive it is."""
    if server_env.get("STUDIO_ONEDRIVE_INBOX"):
        return server_env["STUDIO_ONEDRIVE_INBOX"], None
    found = onedrive_folder(str(laptop_inbox))
    if found:
        say(f"  The inbox, {laptop_inbox}, is \"{found[0]}\" in {found[1]}.")
        return found
    say(f"  This laptop's inbox ({laptop_inbox}) isn't in OneDrive.")
    name = input("  The OneDrive folder footage is uploaded to [Studio Inbox]: ").strip()
    return name.replace("\\", "/").strip("/") or "Studio Inbox", None


def connect_onedrive(ip: str, folder: str, account: str | None) -> None:
    if onedrive_works(ip):
        say("  The server is already connected to OneDrive.")
    else:
        rclone = shutil.which("rclone")
        if not rclone:
            raise SystemExit("  This step needs rclone on the laptop, just to sign in. Install it:\n"
                             "      winget install Rclone.Rclone\n"
                             "  then close and reopen PowerShell and run me again.")
        while True:
            say(f"  A browser window opens: sign in with the Microsoft account of "
                f"{account or 'the OneDrive the footage is uploaded to'} and accept.")
            try:
                token = rclone_authorize(rclone)
                drive = graph_drive(token["access_token"])
            except (ValueError, KeyError, OSError) as e:
                say(f"  {e}.")
                if ask_yes("  Try again?", default=True):
                    continue
                raise SystemExit("Stopped.")
            put_file(ip, RCLONE_CONF, rclone_conf(token, drive))
            say(f"  Connected ({drive['driveType']} OneDrive).")
            if onedrive_has(ip, folder) or not ask_yes(
                    f"  That OneDrive has no folder \"{folder}\". Sign in with another account?", default=True):
                break
    if not onedrive_has(ip, folder):
        say(f"  Note: the server can't see \"{folder}\" in OneDrive. Create it there (or fix the name\n"
            "  as STUDIO_ONEDRIVE_INBOX in the server's .env), or no footage reaches Studio.")
    else:
        say(f"  Footage will come from OneDrive \"{folder}\".")


# ── 5. Settings ───────────────────────────────────────────────────────────────

def parse_env(text: str) -> dict[str, str]:
    """KEY -> value as written (quotes kept), from .env text; comments and blanks skipped."""
    out = {}
    for line in text.splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            out[key.strip().removeprefix("export ").strip()] = value.strip()
    return out


def _windows_path(value: str) -> bool:
    return bool(re.match(r"""^["']?([A-Za-z]:[\\/]|\\\\)""", value))


def server_env(laptop_text: str, server_text: str, domain: str, onedrive_inbox: str) -> tuple[str, dict]:
    """The server's .env: what it already has (never changed), plus the laptop's
    settings it lacks, minus laptop-only ones, with the server's own dashboard and
    OneDrive settings. Returns (text, {"added", "set", "left_out"} key names)."""
    laptop = parse_env(laptop_text)
    left_out = sorted(k for k, v in laptop.items() if v.strip("\"'") and (
                      k in LAPTOP_ONLY or _windows_path(v)
                      or (k == "WHISPER_DEVICE" and v.strip("\"'").lower() in ("cuda", "auto"))))
    wanted = {k: v for k, v in laptop.items() if k not in left_out and v.strip("\"'")}
    forced = {"STUDIO_DASHBOARD_HOST": "127.0.0.1", "STUDIO_DASHBOARD_URL": f"https://{domain}"}
    if onedrive_inbox:
        forced["STUDIO_ONEDRIVE_INBOX"] = onedrive_inbox
    lines = server_text.splitlines() if server_text.strip() else [
        "# Studio on the server. Made by server_move.py from the laptop's .env; edit with care."]
    have = parse_env(server_text)
    changed = []
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if "=" in line and not line.lstrip().startswith("#") and key in forced:
            if have.get(key) != forced[key]:
                lines[i] = f"{key}={forced[key]}"
                changed.append(key)
            have[key] = forced[key]
    added = [k for k in wanted if k not in have and k not in forced]
    lines += [f"{k}={wanted[k]}" for k in added]
    missing = [k for k in forced if k not in have]
    lines += [f"{k}={forced[k]}" for k in missing]
    text = "\n".join(lines) + "\n"
    return text, {"added": added, "set": changed + missing, "left_out": left_out}


def write_settings(ip: str, domain: str, onedrive_inbox: str, current: str) -> bool:
    """Returns True when the server's .env changed (Studio there then needs a restart)."""
    laptop = ENV_FILE.read_text(encoding="utf-8") if ENV_FILE.exists() else ""
    text, report = server_env(laptop, current, domain, onedrive_inbox)
    if text == current:
        say("  The server's settings are up to date. Skipping.")
        return False
    put_file(ip, f"{REPO}/.env", text)
    if report["added"]:
        say("  Copied from this laptop: " + ", ".join(report["added"]))
    if report["set"]:
        say("  Set for the server: " + ", ".join(report["set"]))
    if report["left_out"]:
        say("  Left out (only for this laptop): " + ", ".join(report["left_out"]))
    return True


# ── 6. Send the data ──────────────────────────────────────────────────────────

_lock_handle = None


def _end_windows_worker() -> None:
    """Windows only: turn the scheduled task off (first, so it isn't restarted) and
    end every worker.py process with its children (ffmpeg, the renderer)."""
    r = subprocess.run(["schtasks", "/Change", "/TN", TASK, "/DISABLE"], capture_output=True, text=True)
    if r.returncode == 0:
        say(f"  Turned off the \"{TASK}\" scheduled task.")
    elif "cannot find" not in (r.stderr + r.stdout).lower():
        say(f"  Couldn't turn off the \"{TASK}\" scheduled task ({(r.stderr or r.stdout).strip()}).\n"
            "  Delete it later in Task Scheduler; it won't start Studio again anyway.")
    script = ("Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and "
              "$_.CommandLine -like '*worker.py*' } | ForEach-Object { $_.ProcessId }")
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand",
                        base64.b64encode(script.encode("utf-16-le")).decode()], capture_output=True, text=True)
    pids = [p for p in r.stdout.split() if p.isdigit() and int(p) != os.getpid()]
    for pid in pids:
        subprocess.run(["taskkill", "/PID", pid, "/T", "/F"], capture_output=True)
    subprocess.run(["schtasks", "/End", "/TN", TASK], capture_output=True)
    if pids:
        say(f"  Stopped the Studio worker (process {', '.join(pids)}).")


def _take_lock(root: Path, wait: float) -> bool:
    """Hold ROOT/worker.lock like worker._single_instance, so no worker can start on
    the laptop while its data is being copied."""
    global _lock_handle
    _lock_handle = open(root / "worker.lock", "a+")
    deadline = time.time() + wait
    while True:
        _lock_handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(_lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(_lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            if time.time() >= deadline:
                return False
            time.sleep(1)


def stop_laptop_worker(root: Path) -> None:
    if os.name == "nt":
        _end_windows_worker()
    if not _take_lock(root, wait=30):
        raise SystemExit("  Studio still seems to be running on this laptop (it holds worker.lock).\n"
                         "  Close it (Task Manager > end the python/pythonw processes) and run me again.")
    say("  Studio is stopped on this laptop.")


def files_to_send(root: Path) -> tuple[list[tuple[Path, str]], int]:
    """Everything under ROOT, minus locks, caches and half-downloaded files:
    ([(path, name in the archive)], total bytes). Folders are listed too (empty ones matter)."""
    entries, total = [], 0
    for folder, dirs, files in os.walk(root):
        here = Path(folder)
        keep = []
        for d in sorted(dirs):
            p = here / d
            if d in SKIP_NAMES or p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction()):
                continue
            keep.append(d)
            entries.append((p, p.relative_to(root).as_posix()))
        dirs[:] = keep
        for f in sorted(files):
            p = here / f
            if f in SKIP_NAMES or f.lower().endswith(SKIP_SUFFIXES) or p.is_symlink():
                continue
            try:
                total += p.stat().st_size
            except OSError:
                continue
            entries.append((p, p.relative_to(root).as_posix()))
    return entries, total


def _size(n: float) -> str:
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{max(n / 1e6, 1):.0f} MB"


def _duration(seconds: float) -> str:
    if seconds < 60:
        return "less than a minute"
    minutes = round(seconds / 60)
    return f"{minutes // 60} h {minutes % 60:02d} min" if minutes >= 60 else f"{minutes} min"


class _Meter:
    """A file-like wrapper that counts what goes through and shows progress."""

    def __init__(self, out, total: int):
        self.out, self.total, self.sent = out, max(total, 1), 0
        self.started = self.shown = time.time()

    def write(self, data) -> int:
        self.out.write(data)
        self.sent += len(data)
        now = time.time()
        if now - self.shown >= 1:
            self.shown = now
            rate = self.sent / max(now - self.started, 0.001)
            eta = f", {_duration((self.total - self.sent) / rate)} left" if now - self.started > 5 and rate > 1e5 else ""
            print(f"\r  {self.sent / 1e9:.1f} of {self.total / 1e9:.1f} GB sent, {rate / 1e6:.1f} MB/s{eta}   ",
                  end="", flush=True)
        return len(data)


def stream_data(ip: str, root: Path, entries: list[tuple[Path, str]], total: int) -> None:
    """Send the files as one tar stream straight into `tar -x` on the server."""
    ssh(ip, f"rm -rf {INCOMING} && {AS_STUDIO} mkdir -p {INCOMING}", check="making the incoming folder")
    proc = subprocess.Popen(_ssh_argv(ip, f"{AS_STUDIO} tar -xf - -C {INCOMING}"), stdin=subprocess.PIPE)
    meter = _Meter(proc.stdin, total)
    skipped = []
    try:
        with tarfile.open(fileobj=meter, mode="w|", bufsize=1 << 20, format=tarfile.PAX_FORMAT) as tar:
            for path, name in entries:
                try:
                    info = tar.gettarinfo(str(path), arcname=name)
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    info.mode = 0o755 if info.isdir() else 0o644
                    if info.isdir():
                        tar.addfile(info)
                        continue
                    with open(path, "rb") as f:
                        tar.addfile(info, f)
                except (FileNotFoundError, PermissionError) as e:
                    if name.startswith("catalog.db"):
                        raise
                    skipped.append(f"{name} ({type(e).__name__})")
        proc.stdin.close()
    except (BrokenPipeError, OSError) as e:
        proc.kill()
        raise SystemExit(f"\n  The copy broke off ({e}). This laptop's data is untouched: run me again to resend.\n"
                         "  Until then Studio is stopped here (the desktop Studio icon starts it if you need it).")
    print()
    if proc.wait() != 0:
        raise SystemExit("  The server couldn't unpack what was sent (see above). This laptop's data is\n"
                         "  untouched: run me again to resend.")
    say(f"  Sent {_size(total)} in {_duration(time.time() - meter.started)}.")
    for s in skipped:
        say(f"  Couldn't read (left out): {s}")


def send_data(ip: str, root: Path, inbox: Path) -> None:
    step(6, "Moving the data.")
    if not (root / "catalog.db").exists():
        raise SystemExit(f"  There's no catalog.db in {root}: is that the right Studio folder?")
    _, total = files_to_send(root)
    free = _text(ssh(ip, "df -B1 --output=avail /home | tail -1", capture=True, check="checking disk space").stdout)
    if free.strip().isdigit() and int(free) < total * 1.05 + 5e9:
        raise SystemExit(f"  The server has {_size(int(free))} free, not enough for {_size(total)}.")
    say("  Studio on this laptop stops for good; it carries on on the server. Everything in\n"
        f"  {root} ({_size(total)}) is copied over; this folder stays as it is, as a backup.\n"
        "  If Studio is in the middle of an edit, let it finish first.")
    if not ask_yes("  Move Studio now?"):
        raise SystemExit("Nothing moved. Run me again when you're ready (or with --skip-data).")
    stop_laptop_worker(root)
    entries, total = files_to_send(root)   # again: the worker may have written since
    try:
        inbox.resolve().relative_to(root.resolve())
    except ValueError:
        if onedrive_folder(str(inbox)):
            say(f"  The inbox ({inbox}) isn't copied: it's in OneDrive, and the server picks it up from there.")
        elif inbox.exists() and any(p.suffix.lower() in VIDEO_EXTS for p in inbox.rglob("*")):
            say(f"  Note: videos waiting in {inbox} aren't moved. Put them in the OneDrive inbox\n"
                "  and the server picks them up.")
    say(f"  Sending {_size(total)} ({len(entries)} files and folders).")
    stream_data(ip, root, entries, total)


# ── 7. Unpack on the server (runs there, as studio) ───────────────────────────

_STOP = "\"'\n\r\t<>|*?:"   # characters a Windows path can't contain: where a path in text ends


def path_rules(pairs: list[tuple[str | None, str]]) -> list[tuple[re.Pattern, str, str]]:
    """Patterns that find each old folder in stored text, in every spelling it gets
    stored in: C:\\Users\\..., C:/Users/..., JSON-escaped (C:\\\\Users\\\\...) and
    file:///C:/Users/... (Resolve timelines). Case doesn't matter for the old part.
    Returns [(pattern, new folder, separator to turn into /)]: file:/// forms first,
    then the longest folder first."""
    uris, paths = [], []
    stop = re.escape(_STOP)
    for old, new in sorted(((o, n) for o, n in pairs if o), key=lambda p: -len(p[0])):
        windows = bool(re.match(r"^([A-Za-z]:|\\\\)", old))
        comps = [c for c in re.split(r"[\\/]+", old) if c]
        lead = 2 if windows and old.startswith("\\\\") else (0 if windows else 1)
        for sep in ("/", "\\", "\\\\", "\\\\\\\\") if windows else ("/",):
            # \uXXXX escapes may sit inside a JSON string's path: one backslash less than its separator.
            uni = {"/": "\\", "\\": None, "\\\\": "\\", "\\\\\\\\": "\\\\"}[sep]
            char = rf"[^\\/{stop}]"
            seg = rf"(?:{char}|{re.escape(uni)}u[0-9a-fA-F]{{4}})" if uni else char
            head = re.escape(sep) * lead + re.escape(sep).join(map(re.escape, comps))
            paths.append((re.compile(rf"(?<![\w/.-]){head}(?=$|[\s\\/{stop},;)\]}}])"
                                     rf"(?P<rest>(?:{re.escape(sep)}{seg}*)*)", re.IGNORECASE), new, sep))
        try:
            uri = (PureWindowsPath(old) if windows else PurePosixPath(old)).as_uri().rstrip("/")
        except ValueError:
            continue
        uris.append((re.compile(re.escape(uri) + r"(?=$|[/\s\"'<>])(?P<rest>(?:/[^/\s\"'<>]*)*)", re.IGNORECASE),
                     PurePosixPath(new).as_uri(), "/"))
    return uris + paths


def rewrite(text: str, rules) -> str:
    for pattern, new, sep in rules:
        text = pattern.sub(lambda m: new + m.group("rest").replace(sep, "/"), text)
    return text


_LEFTOVER = re.compile(r"(?<![A-Za-z])[A-Za-z]:(?:\\{1,4}|/)[A-Za-z0-9_]")


def rewrite_catalog(db: Path, rules) -> dict[str, int]:
    """Rewrite old paths in every text value of every table. Returns {"table.column": values changed}."""
    conn = sqlite3.connect(db, timeout=30)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    changed: dict[str, int] = {}
    leftover: dict[str, int] = {}
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' "
                                         "AND name NOT LIKE 'sqlite_%'")]
    for table in tables:
        cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
        try:
            rows = conn.execute(f'SELECT rowid, {", ".join(chr(34) + c + chr(34) for c in cols)} FROM "{table}"')
        except sqlite3.OperationalError:
            say(f"  Skipped table {table} (no rowid).")
            continue
        for row in rows.fetchall():
            updates = {}
            for col, value in zip(cols, row[1:]):
                if isinstance(value, str):
                    new = rewrite(value, rules)
                    if new != value:
                        updates[col] = new
                        changed[f"{table}.{col}"] = changed.get(f"{table}.{col}", 0) + 1
                    if _LEFTOVER.search(new):
                        leftover[f"{table}.{col}"] = leftover.get(f"{table}.{col}", 0) + 1
            if updates:
                conn.execute(f'UPDATE "{table}" SET {", ".join(chr(34) + c + chr(34) + " = ?" for c in updates)} '
                             "WHERE rowid = ?", [*updates.values(), row[0]])
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    check = conn.execute("PRAGMA quick_check").fetchone()[0]
    conn.close()
    if check != "ok":
        say(f"  Warning: the catalogue's self-check says: {check}")
    if leftover:
        say("  Still mentioning a Windows-style path (left as is): "
            + ", ".join(f"{k} ({n})" for k, n in sorted(leftover.items())))
    return changed


TEXT_FILES = {".json", ".txt", ".md", ".fcpxml", ".xml", ".html", ".srt"}


def rewrite_files(root: Path, rules) -> list[str]:
    """Rewrite old paths inside Studio's own text files (edit plans, state, Resolve
    timelines...). Logs keep their history. Returns the files changed."""
    done = []
    for folder, dirs, files in os.walk(root):
        if Path(folder) == root:
            dirs[:] = [d for d in dirs if d != "logs"]
        for name in files:
            path = Path(folder) / name
            if path.suffix.lower() not in TEXT_FILES or path.stat().st_size > 20_000_000:
                continue
            try:
                text = path.read_bytes().decode("utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            new = rewrite(text, rules)
            if new != text:
                st = path.stat()
                path.write_bytes(new.encode("utf-8"))
                os.utime(path, (st.st_atime, st.st_mtime))
                done.append(path.relative_to(root).as_posix())
    return done


def _merge_into(src: Path, dest: Path) -> None:
    """Move src's contents into the existing folder dest; what src brings wins."""
    for item in src.iterdir():
        target = dest / item.name
        if item.is_dir() and target.is_dir() and not target.is_symlink():
            _merge_into(item, target)
            continue
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        elif target.exists() or target.is_symlink():
            target.unlink()
        item.rename(target)
    src.rmdir()


def unpack(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="server_move.py unpack",
                                     description="(On the server) put the laptop's Studio folder in place.")
    parser.add_argument("incoming", type=Path, help="the folder the laptop's data was sent to")
    parser.add_argument("--from-root", required=True, help="the laptop's Studio folder (STUDIO_ROOT)")
    parser.add_argument("--from-inbox", default="", help="the laptop's inbox folder (STUDIO_INBOX)")
    parser.add_argument("--replace", action="store_true", help="replace a catalogue already here")
    args = parser.parse_args(argv)
    import settings
    root, inbox, incoming = Path(settings.ROOT), Path(settings.INBOX), args.incoming
    if (root / "catalog.db").exists() and not args.replace:
        say(f"{root} already has a Studio catalogue. Run again with --replace to replace it.")
        return 2
    if not (incoming / "catalog.db").exists():
        say(f"Nothing to unpack: {incoming} has no catalog.db.")
        return 2
    old = None
    if (root / "catalog.db").exists():
        old = root.with_name(f"{root.name}.old-{datetime.now():%Y%m%d-%H%M%S}")
        root.rename(old)
        say(f"The catalogue that was here is kept in {old}.")
    if root.exists():
        _merge_into(incoming, root)
    else:
        root.parent.mkdir(parents=True, exist_ok=True)
        incoming.rename(root)
    try:   # footage the server pulled from OneDrive but hasn't processed yet stays in the inbox
        old_inbox = old / inbox.relative_to(root) if old else None
    except ValueError:
        old_inbox = None
    if old_inbox and old_inbox.is_dir():
        inbox.mkdir(parents=True, exist_ok=True)
        _merge_into(old_inbox, inbox)
    (root / MOVED_NOTE).unlink(missing_ok=True)
    (root / "worker.lock").unlink(missing_ok=True)
    rules = path_rules([(args.from_root, str(root)), (args.from_inbox, str(inbox))])
    changed = rewrite_catalog(root / "catalog.db", rules)
    files = rewrite_files(root, rules)
    (root / SERVER_MARK).write_text(json.dumps({"from_root": args.from_root, "from_inbox": args.from_inbox,
                                                "moved_at": datetime.now(timezone.utc).isoformat(
                                                    timespec="seconds")}, indent=1), encoding="utf-8")
    say(f"Studio data is in {root}.")
    say(f"Catalogue: {sum(changed.values())} stored paths updated"
        + (" (" + ", ".join(f"{k} {n}" for k, n in sorted(changed.items())) + ")" if changed else "") + ".")
    say(f"Files: {len(files)} updated" + (" (" + ", ".join(files[:8]) + (", ..." if len(files) > 8 else "") + ")"
                                         if files else "") + ".")
    return 0


def unpack_on_server(ip: str, root: Path, inbox: Path, replace: bool) -> None:
    step(7, "Putting the data in place on the server and starting Studio there.")
    ssh(ip, "systemctl stop studio 2>/dev/null || true")
    command = (f"cd {REPO} && {AS_STUDIO} venv/bin/python server_move.py unpack {INCOMING} "
               f"--from-root {shlex.quote(str(root))} --from-inbox {shlex.quote(str(inbox))}"
               + (" --replace" if replace else ""))
    ssh(ip, command, check="unpacking")


# ── 8. Done ───────────────────────────────────────────────────────────────────

def _wait(test, seconds: float, every: float = 5) -> bool:
    deadline = time.time() + seconds
    while True:
        if test():
            return True
        if time.time() >= deadline:
            return False
        time.sleep(every)


def _dns_points(domain: str, ip: str) -> bool:
    try:
        want = {a[4][0] for a in socket.getaddrinfo(ip, 443)}
        return bool(want & {a[4][0] for a in socket.getaddrinfo(domain, 443)})
    except OSError:
        return False


def _https_up(url: str) -> bool:
    import requests
    try:
        return requests.get(url + "/healthz", timeout=10).status_code in (200, 401)
    except requests.RequestException:
        return False


def write_note(root: Path, url: str) -> None:
    """ROOT/MOVED_TO_SERVER.txt: the first line is the address. worker.py refuses to
    start while it's there and the desktop icon opens the address instead."""
    (root / MOVED_NOTE).write_text(
        f"{url}\n\nStudio moved to its own server on {datetime.now():%d %b %Y} (server_move.py).\n"
        "It doesn't run on this laptop any more: open the address above instead.\n"
        "This folder is what Studio had on the day it moved; keep it a while as a backup.\n", encoding="utf-8")


def finish(ip: str, domain: str, root: Path) -> None:
    step(8, f"Checking https://{domain}.")
    url = f"https://{domain}"
    write_note(root, url)
    local = "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/healthz"
    if not _wait(lambda: _text(ssh(ip, local, capture=True, timeout=30).stdout).strip() in ("200", "401"), 120):
        ssh(ip, f"bash {REMOTE_SETUP} status")
        raise SystemExit("\n  Studio didn't come up on the server (its log is above). Fix that and run me again.")
    say("  Studio is running on the server.")
    if not _dns_points(domain, ip):
        sub, _, base = domain.partition(".") if domain.count(".") >= 2 else ("@", "", domain)
        say(f"  {domain} doesn't point at the server yet. In GoDaddy:\n"
            f"    My Products > {base} > DNS > Add New Record: Type A, Name {sub}, Value {ip} > Save.\n"
            f"  Studio is already running there; {url} opens a few minutes after\n"
            "  the record is saved (run me again to check).")
    elif not _wait(lambda: _https_up(url), 240, every=10):
        say(f"  {url} doesn't answer yet; the HTTPS certificate is probably still on its way.\n"
            f"  Try again in a few minutes (status: ssh root@{ip} bash {REMOTE_SETUP} status).")
    say(f"\nDone: open {url}")


# ── Putting it together ───────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["unpack"]:
        return unpack(argv[1:])
    parser = argparse.ArgumentParser(
        description="Move Studio from this laptop to its own server (run on the laptop).",
        epilog="Safe to run again: finished steps are skipped. "
               "(The server runs `server_move.py unpack` itself in step 7.)")
    parser.add_argument("ip", help="the server's IP address (Hetzner Cloud Console > Servers)")
    parser.add_argument("--domain", default="studio.theurbanmakers.com", help="the address Studio gets (default: %(default)s)")
    parser.add_argument("--skip-data", action="store_true",
                        help="get the server ready but leave the data (and Studio) on this laptop for now")
    parser.add_argument("--replace", action="store_true",
                        help="replace a Studio catalogue that's already on the server")
    args = parser.parse_args(argv)
    domain = args.domain.strip().lower().rstrip(".")
    if not re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", domain):
        parser.error(f"that doesn't look like a web address: {args.domain}")
    if not re.fullmatch(r"[A-Za-z0-9.:-]+", args.ip):
        parser.error(f"that doesn't look like an IP address: {args.ip}")
    ip = args.ip

    import settings
    root, inbox = Path(settings.ROOT), Path(settings.INBOX)
    owner, repo, branch = git_origin()
    say(f"Moving Studio to the server at {ip} ({domain}).\nThis laptop's Studio folder: {root}")

    check_ssh(ip)
    prepare_server(ip, domain)
    deploy_code(ip, owner, repo, branch)

    step(4, "Connecting the server to OneDrive, where the footage arrives.")
    current = read_server_file(ip, f"{REPO}/.env")
    folder, account = choose_inbox(parse_env(current), inbox)
    connect_onedrive(ip, folder, account)

    step(5, "Copying the settings (.env) to the server.")
    settings_changed = write_settings(ip, domain, folder, current)

    state = _text(ssh(ip, f"if [ -e {SERVER_ROOT}/{SERVER_MARK} ]; then echo moved; "
                          f"elif [ -e {SERVER_ROOT}/catalog.db ]; then echo other; else echo none; fi",
                      capture=True, check="checking the server's data").stdout).strip()
    unpacked = False
    if state == "moved" and ((root / MOVED_NOTE).exists() or not args.replace):
        step(6, "Moving the data: already on the server. Skipping.")
        step(7, "Unpacking on the server: already done. Skipping.")
    elif args.skip_data:
        step(6, "Moving the data: skipped (--skip-data).")
        step(7, "Unpacking on the server: skipped (--skip-data).")
    elif state == "other" and not args.replace:
        raise SystemExit("\n  The server already has a Studio catalogue that didn't come from this laptop.\n"
                         "  To replace it with this laptop's, run me again with --replace\n"
                         "  (the server's one is kept as Studio.old-<date>).")
    else:
        if (root / MOVED_NOTE).exists():
            say("\n  Studio moved from this laptop before, but this server doesn't have the data:\n"
                "  sending this laptop's copy (from the day it moved) again.")
        send_data(ip, root, inbox)
        unpack_on_server(ip, root, inbox, args.replace)
        write_note(root, f"https://{domain}")   # straight away: the laptop must never run Studio again
        unpacked = True
        state = "moved"

    if state != "moved":
        say("\nThe server is ready. Studio keeps running on this laptop until you run me again\n"
            "without --skip-data, which moves the data and starts Studio there.")
        return 0
    active = ssh(ip, "systemctl is-active --quiet studio", capture=True).returncode == 0
    if unpacked or settings_changed or not active:
        ssh(ip, f"bash {REMOTE_SETUP} start", check="setup.sh start")
    finish(ip, domain, root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
