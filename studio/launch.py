"""
What the desktop Studio icon runs: make sure the worker (and with it the
dashboard) is running, then open the dashboard in the browser.

  - Worker already running (started at sign-in, or by an earlier click): just
    opens the browser.
  - Not running: starts it in the background with no console window, waits for
    the dashboard to answer, then opens the browser.

Once Studio has moved to a server (server_move.py), the icon opens the server's
dashboard instead.

Run with pythonw so no window flashes. install_shortcut.ps1 puts it on the desktop.
"""
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

import settings

HERE = Path(__file__).resolve().parent
URL = f"http://127.0.0.1:{settings.DASHBOARD_PORT}/"


def dashboard_up() -> bool:
    try:
        urllib.request.urlopen(URL + "healthz", timeout=2)
        return True
    except urllib.error.HTTPError as e:
        return e.code == 401  # up, just password-protected
    except OSError:
        return False


def start_worker() -> None:
    # pythonw next to this interpreter: the venv's windowless Python.
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = str(pythonw if pythonw.exists() else sys.executable)
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([exe, str(HERE / "worker.py")], cwd=HERE, creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main() -> int:
    moved = settings.ROOT / "MOVED_TO_SERVER.txt"  # written by server_move.py
    if moved.exists():
        webbrowser.open(moved.read_text(encoding="utf-8").strip().splitlines()[0])
        return 0
    if not dashboard_up():
        start_worker()
        deadline = time.time() + 60
        while time.time() < deadline and not dashboard_up():
            time.sleep(1)
    # Open even on a timeout: the page says "can't be reached", and worker.log says why.
    webbrowser.open(URL)
    return 0


if __name__ == "__main__":
    sys.exit(main())
