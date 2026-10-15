"""
The renderer toolchain the editor uses: HyperFrames (HTML compositions rendered
to video by a headless Chrome) on Node.js, installed in studio/render with
`npm ci`. HyperFrames is run as `node <its entry script>` rather than through
npx or a .cmd shim, so Windows argument quoting never gets in the way.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

RENDER_DIR = Path(__file__).resolve().parent / "render"
MIN_NODE = 22

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _node() -> str | None:
    found = shutil.which("node")
    return os.path.realpath(found) if found else None


def _cli() -> Path | None:
    pkg = RENDER_DIR / "node_modules" / "hyperframes" / "package.json"
    if not pkg.exists():
        return None
    entry = json.loads(pkg.read_text(encoding="utf-8"))["bin"]["hyperframes"]
    return pkg.parent / entry


def hyperframes(args: list[str], cwd: Path | None = None, timeout: int = 1800) -> subprocess.CompletedProcess:
    node, cli = _node(), _cli()
    if not node:
        raise RuntimeError("Node.js not found; install it with: winget install OpenJS.NodeJS.LTS")
    if not cli:
        raise RuntimeError("HyperFrames isn't installed; run npm ci in studio\\render")
    return subprocess.run([node, str(cli), *args], cwd=cwd or RENDER_DIR, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, creationflags=_NO_WINDOW)


def node_version() -> str:
    node = _node()
    if not node:
        raise RuntimeError("not found; install it with: winget install OpenJS.NodeJS.LTS")
    version = subprocess.run([node, "--version"], capture_output=True, text=True,
                             creationflags=_NO_WINDOW).stdout.strip()
    major = int(re.match(r"v(\d+)", version).group(1)) if re.match(r"v(\d+)", version) else 0
    if major < MIN_NODE:
        raise RuntimeError(f"{version} is too old; HyperFrames needs Node {MIN_NODE}+ "
                           "(winget upgrade OpenJS.NodeJS.LTS)")
    return version


def hyperframes_version() -> str:
    result = hyperframes(["--version"], timeout=120)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip()[-300:])
    return result.stdout.strip().splitlines()[-1]


def browser_path() -> str:
    """The headless Chrome HyperFrames renders with ('npx hyperframes browser ensure' fetches it)."""
    result = hyperframes(["browser", "path"], timeout=120)
    path = (result.stdout or "").strip().splitlines()[-1:] or [""]
    if result.returncode != 0 or not path[0]:
        raise RuntimeError("Chrome for rendering is missing; in studio\\render run: npx hyperframes browser ensure")
    return path[0]


def smoke_test() -> float:
    """Render the 2-second check composition; returns the seconds it took."""
    with tempfile.TemporaryDirectory(prefix="studio-render-check-") as tmp:
        project = Path(tmp) / "check"
        shutil.copytree(RENDER_DIR / "smoke", project)
        shutil.copy(RENDER_DIR / "node_modules" / "gsap" / "dist" / "gsap.min.js", project)
        out = Path(tmp) / "check.mp4"
        started = time.time()
        result = hyperframes(["render", str(project), "--quality", "draft", "--output", str(out)], timeout=600)
        if result.returncode != 0 or not out.exists():
            raise RuntimeError(((result.stderr or "") + (result.stdout or "")).strip()[-400:])
        return time.time() - started
