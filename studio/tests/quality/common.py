"""What every quality suite shares: where Studio's code is, a folder of its own for the run, a safe
environment (no real keys, no Telegram, no Supabase, studio/.env never read), and the PASS/FAIL tally.

Import it first, before any Studio module:  from common import STUDIO, WORK, ok, done"""
import atexit, os, shutil, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
STUDIO = HERE.parents[1]
FIXTURES = HERE / "fixtures"
sys.path.insert(0, str(STUDIO))

# studio/.env holds the machine's real keys and settings: a suite must see Studio's defaults only.
import dotenv
dotenv.load_dotenv = lambda *a, **k: False

# Blank means "unset" to settings._env, and it can't be filled in from anywhere else.
for _k in ("GEMINI_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "STUDIO_SUPABASE_URL",
           "STUDIO_SUPABASE_SERVICE_KEY", "STUDIO_SUPABASE_ANON_KEY", "STUDIO_ONEDRIVE_REMOTE", "STUDIO_WIKI_URL",
           "STUDIO_WIKI_JWT", "STUDIO_WIKI_ANON_KEY"):
    os.environ[_k] = ""
os.environ.setdefault("ANTHROPIC_API_KEY", "")
os.environ.setdefault("STUDIO_TOPICS_DAY", "off")
os.environ.setdefault("STUDIO_TOPICS_BEHIND", "0")

# The run's own folder: the runner hands one over (STUDIO_QUALITY_WORK); run on its own, a suite
# makes a temporary one and removes it after (STUDIO_QUALITY_KEEP=1 keeps it to look at).
if os.environ.get("STUDIO_QUALITY_WORK"):
    WORK = Path(os.environ["STUDIO_QUALITY_WORK"])
    WORK.mkdir(parents=True, exist_ok=True)
else:
    WORK = Path(tempfile.mkdtemp(prefix=f"studio-quality-{Path(sys.argv[0]).stem}-"))
    if not os.environ.get("STUDIO_QUALITY_KEEP"):
        atexit.register(shutil.rmtree, WORK, True)
    print(f"(working in {WORK})", flush=True)
ROOT = WORK / "root"
shutil.rmtree(ROOT, ignore_errors=True)
(WORK / "tmp").mkdir(exist_ok=True)
os.environ["TMPDIR"] = str(WORK / "tmp")
tempfile.tempdir = None
os.environ.update(STUDIO_ROOT=str(ROOT), STUDIO_INBOX=str(ROOT / "Inbox"))

results = []


def ok(c, m):
    results.append(bool(c)); print(("PASS " if c else "FAIL ") + m, flush=True)


def finish():
    """The last line every suite prints (the runner reads it), and its exit code."""
    print(f"\n{sum(results)}/{len(results)} passed", flush=True)
    sys.exit(0 if results and all(results) else 1)


def before_code(name: str) -> Path:
    """A file of Studio as it was before a fix (fixtures/before/<name>.xz), unpacked into the run's
    folder. The suites load it to show their test media still reproduces the bug the fix is for."""
    import lzma
    out = WORK / "before" / name
    out.parent.mkdir(exist_ok=True)
    src = FIXTURES / "before" / (name + ".xz")
    out.write_bytes(lzma.decompress(src.read_bytes()) if src.exists() else (FIXTURES / "before" / name).read_bytes())
    return out


def load_before(name: str, module: str):
    import importlib.util
    spec = importlib.util.spec_from_file_location(module, str(before_code(name)))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod
