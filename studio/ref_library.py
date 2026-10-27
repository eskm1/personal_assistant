"""
The reference library (cloud/CONTRACT.md, "Reference library"): hundreds of
Instagram posts the team imported at once in the planner (a pasted list or a CSV).

Imported references are held: nothing is downloaded or spent until the owner
presses "Analyse" (all of them, or a sample first: up to 150, spread across kinds
of post and accounts, at most two per account). That queues a 'refs' job; this
module runs it and then analyses the queued posts one at a time, slowly, so
Instagram doesn't block the server (2026-10-27: hundreds of downloads in a row from one
server and one spare sign-in had Instagram refusing even the hand-added ones):

  - references added by hand go first, in their own queue (hand_queue): a new one is
    tried once straight away, even while the library is paused; one Instagram refused
    waits for the backoff and any pause like the rest, then goes before the library;
    one with its own file (uploaded, or from OneDrive: ref_files) needs no download;
  - the library at a slow, steady rate: STUDIO_REFERENCE_LIBRARY_PER_HOUR (4) an hour at
    most, spaced at random around 60 / that many minutes, never two in one tick;
  - when Instagram refuses ("too many requests", or wants a login), the post goes
    back in the queue and the next try waits 10 minutes, then 20, 40 ... up to 4
    hours; after four refusals in a row, or when most recent tries want a login,
    the batch pauses and says why (a fresh cookies.txt resumes a login pause by
    itself; a rate pause resumes after a few hours); while a pause is on, nothing of
    the library touches Instagram;
  - when the sign-in itself looks dead (ig_health: every signed-in call failing for
    hours, or a checkpoint / login page), the batch pauses with "the sign-in account
    needs attention" until a fresh cookies.txt arrives;
  - a deleted or private post fails for good (it isn't retried every day);
  - the month's budget (STUDIO_REFERENCE_BUDGET_USD) and the free disk are checked
    before each one.

The planner reads the pacing, the budget and the estimates from the Studio
machine's heartbeat (laptop_status.info.references).
"""
import json
import logging
import random
import shutil
import time
from datetime import datetime, timedelta, timezone

import settings

log = logging.getLogger("studio.refs")

PER_POST_USD = 0.06          # Claude's look at one post (a reel ~0.065 with its beat timeline, a carousel ~0.05), until real costs are known
SECONDS_PER_POST = 80        # download + measuring + Claude + the pause between, on the server
GAP_SECONDS = (45, 90)       # after a hand-added download, before the next one (random within)
JITTER = (0.6, 1.4)          # a library slot is 60 / per-hour minutes times a random factor in this range
BACKOFF_FIRST = 600          # after Instagram refuses: 10 minutes, doubling each time
BACKOFF_MAX = 4 * 3600
PAUSE_AFTER_REFUSALS = 4     # refusals in a row: pause the batch
LOGIN_PAUSE = (6, 10)        # this many of the last tries wanting a login: pause and ask for cookies
RATE_PAUSE_HOURS = 6         # a rate pause resumes by itself after this long
NET_WAIT = 600               # no internet: try again in 10 minutes
MIN_FREE = 3 << 30           # disk that must stay free to download a post
SAMPLE_SIZE = 150
PER_ACCOUNT = 2              # at most this many posts of one account in a sample
QUEUE_CHUNK = 150            # ids per update when queueing
FOLLOWERS_DAYS = 30          # how long an account's follower count is kept
STATE_KEY = "ref_library"

COOKIES_HINT = ("Instagram wants a login. On the References page, open \"Instagram sign-in for downloads\" and "
                "upload a cookies.txt from a spare Instagram account (four short steps there), then press Resume.")
COOKIES_EXPIRED = ("Instagram wants a login again: the sign-in file has probably expired. Export a fresh cookies.txt "
                   "from the spare account and upload it in \"Instagram sign-in for downloads\", then press Resume.")


def is_library(ref: dict) -> bool:
    """Imported in bulk (held until the owner presses Analyse), not added by hand."""
    return ref.get("import_id") is not None


# ── State (cloud_state.json) ─────────────────────────────────────────────────

def _state() -> dict:
    import cloud_sync
    st = cloud_sync._state().get(STATE_KEY) or {}
    st.setdefault("next_at", 0.0)
    st.setdefault("level", 0)
    st.setdefault("recent", [])
    st.setdefault("paused", None)
    st.setdefault("backoff_at", 0.0)   # after a refusal: no Instagram download (library or hand-added) before this
    st.setdefault("lib_times", [])     # when the library's last downloads started (the hourly cap)
    return st


def _save(st: dict) -> None:
    import cloud_sync
    cloud_sync._save_state({STATE_KEY: st})


def _cookies_mtime() -> float:
    import style_study
    try:
        return style_study.COOKIES_FILE.stat().st_mtime
    except OSError:
        return 0.0


# ── Money ─────────────────────────────────────────────────────────────────────

def month_spent(conn) -> float:
    """What analysing references and taste reports cost this calendar month (US$)."""
    import catalog
    import usage
    conn.executescript(usage._SCHEMA)
    row = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM usage WHERE kind IN ('style', 'taste') "
                       "AND substr(at, 1, 7) = ?", (catalog.now()[:7],)).fetchone()
    return round(row[0] or 0, 4)


def per_post_usd(conn) -> float:
    """The average real cost of one reference's look, once there are a few; else the estimate."""
    import usage
    conn.executescript(usage._SCHEMA)
    row = conn.execute("SELECT COUNT(*), AVG(cost_usd) FROM usage WHERE kind = 'style' AND ref LIKE 'ref:%' "
                       "AND cost_usd > 0").fetchone()
    return round(row[1], 4) if row[0] >= 10 and row[1] else PER_POST_USD


def budget(conn) -> dict:
    cap = settings.REFERENCE_BUDGET_USD
    spent = month_spent(conn)
    return {"cap_usd": round(cap, 2), "spent_usd": round(spent, 2), "left_usd": round(max(cap - spent, 0.0), 2)}


def estimates(conn) -> dict:
    """What the planner shows before the owner presses Analyse or Write the taste report."""
    import taste
    return {"per_post_usd": per_post_usd(conn), "seconds_per_post": seconds_per_post(), "per_hour": per_hour(),
            "sample": SAMPLE_SIZE, "report": taste.estimate_parts()}


def seconds_per_post() -> int:
    """How long one library post takes, the hourly cap included."""
    return int(max(SECONDS_PER_POST, 3600 / per_hour()))


# ── Choosing a sample ─────────────────────────────────────────────────────────

def choose_sample(refs: list[dict], n: int = SAMPLE_SIZE, per_account: int = PER_ACCOUNT, seed: int = 0) -> list[dict]:
    """Up to n references spread like the library: each kind of post (reel, carousel,
    photo, video) in proportion (at least a few of each), and within a kind as many
    different accounts as possible, at most per_account of one account (relaxed only
    when there aren't enough accounts). The same seed picks the same sample."""
    rng = random.Random(seed)
    if n >= len(refs):
        return list(refs)
    by_type: dict[str, list[dict]] = {}
    for r in refs:
        by_type.setdefault(r.get("post_type") or "other", []).append(r)
    # Quotas: proportional (largest remainder), at least min(5, available) of each kind.
    total = len(refs)
    raw = {t: n * len(rs) / total for t, rs in by_type.items()}
    quota = {t: min(len(by_type[t]), max(int(v), min(5, len(by_type[t])))) for t, v in raw.items()}
    while sum(quota.values()) > n:
        t = max(quota, key=lambda k: (quota[k] - raw[k], quota[k]))
        quota[t] -= 1
    for t in sorted(raw, key=lambda k: raw[k] - int(raw[k]), reverse=True) * 3:
        if sum(quota.values()) >= n:
            break
        if quota[t] < len(by_type[t]):
            quota[t] += 1
    used: dict[str, int] = {}
    picked: list[dict] = []
    for t in sorted(by_type):
        accounts: dict[str, list[dict]] = {}
        for r in by_type[t]:
            accounts.setdefault((r.get("account") or f"#{r['id']}").lower(), []).append(r)
        for posts in accounts.values():
            rng.shuffle(posts)
        names = sorted(accounts)
        rng.shuffle(names)
        mine: list[dict] = []
        for cap in (per_account, 10 ** 6):  # the cap, then (only if short) without it
            for round_ in range(max(len(p) for p in accounts.values())):
                for a in names:
                    if len(mine) >= quota[t]:
                        break
                    posts = accounts[a]
                    if round_ < len(posts) and posts[round_] not in mine and used.get(a, 0) < cap:
                        mine.append(posts[round_])
                        used[a] = used.get(a, 0) + 1
            if len(mine) >= quota[t]:
                break
        picked += mine
    return sorted(picked, key=lambda r: r["id"])


# ── Pacing ────────────────────────────────────────────────────────────────────

def _pause(st: dict, kind: str, reason: str, until: float | None = None) -> None:
    st["paused"] = {"kind": kind, "reason": reason, "at": time.time(), "until": until}
    log.warning("Reference library paused: %s", reason)


def per_hour() -> float:
    return max(float(settings.REFERENCE_LIBRARY_PER_HOUR), 0.1)


def _slot(now: float) -> float:
    """When the library's next download may start after one now: about 60 / per-hour minutes, jittered."""
    return now + 3600 / per_hour() * random.uniform(*JITTER)


def _pause_over(st: dict, conn, now: float) -> bool:
    """Is the pause (if any) over? Clears it (and the backoff) when it is. False while it holds."""
    p = st.get("paused")
    if not p:
        return True
    resume = False
    if p["kind"] == "login" and _cookies_mtime() > p["at"]:
        resume = True  # a new sign-in file since the pause
    elif p["kind"] == "rate" and p.get("until") and now >= p["until"]:
        resume = True
    elif p["kind"] == "budget" and conn is not None and budget(conn)["left_usd"] >= per_post_usd(conn):
        resume = True
    elif p["kind"] == "disk" and _free() >= MIN_FREE:
        resume = True
    if not resume:
        return False
    log.info("Reference library resumed (%s pause over)", p["kind"])
    st.update(paused=None, level=0, recent=[], next_at=0.0, backoff_at=0.0)
    _save(st)
    return True


def instagram_free(now: float | None = None) -> bool:
    """May a hand-added reference ask Instagram now? Not during an Instagram pause (rate, login: the
    owner's, budget and disk pauses are the library's own) nor before the backoff after a refusal."""
    now = now or time.time()
    st = _state()
    p = st.get("paused")
    if p and p["kind"] in ("rate", "login") and not _pause_over(st, None, now):
        return False
    return now >= (st.get("backoff_at") or 0.0)


def ready(conn, now: float | None = None) -> tuple[bool, str]:
    """May the next library download start now? (False, why) when not."""
    now = now or time.time()
    st = _state()
    if not _pause_over(st, conn, now):
        return False, st["paused"]["reason"]
    left = budget(conn)["left_usd"]
    if left < per_post_usd(conn):
        _pause(st, "budget", f"This month's reference budget (US${settings.REFERENCE_BUDGET_USD:.0f}) is used up: "
                             "analysing carries on next month, or raise STUDIO_REFERENCE_BUDGET_USD.")
        _save(st)
        return False, st["paused"]["reason"]
    if _free() < MIN_FREE:
        _pause(st, "disk", "The Studio machine's disk is nearly full: analysing waits until there's 3 GB free.")
        _save(st)
        return False, st["paused"]["reason"]
    wait = max(st["next_at"], st.get("backoff_at") or 0.0)
    if now < wait:
        return False, f"next download in {int(wait - now)} s"
    if len([t for t in st.get("lib_times") or [] if now - t < 3600]) >= per_hour():
        return False, f"at most {per_hour():g} an hour"
    return True, ""


def _free() -> int:
    try:
        return shutil.disk_usage(settings.ROOT).free
    except OSError:
        return 0


def record(outcome: str, now: float | None = None, library: bool = True) -> dict:
    """After one Instagram try (library=False: a hand-added reference's): ok / gone / other (the library
    waits for its next slot), rate / login (back off, maybe pause the batch), net (wait 10 minutes).
    Hand-added and library tries share the backoff and the pauses: it is one server to Instagram.
    Returns the state."""
    import ig_health
    now = now or time.time()
    st = _state()
    st["recent"] = (st["recent"] + [outcome])[-LOGIN_PAUSE[1]:]
    if library:
        st["lib_times"] = [t for t in st.get("lib_times") or [] if now - t < 3600] + [now]
    if outcome in ("rate", "login"):
        st["level"] = st["level"] + 1
        wait = min(BACKOFF_FIRST * 2 ** (st["level"] - 1), BACKOFF_MAX)
        st["backoff_at"] = now + wait * random.uniform(0.9, 1.2)
        st["next_at"] = max(st["next_at"], _slot(now) if library else 0.0)
        logins = st["recent"].count("login")
        if logins >= LOGIN_PAUSE[0] or (outcome == "login" and st["level"] >= PAUSE_AFTER_REFUSALS):
            _pause(st, "login", COOKIES_EXPIRED if _cookies_mtime() else COOKIES_HINT)
        elif st["level"] >= PAUSE_AFTER_REFUSALS:
            _pause(st, "rate", f"Instagram is limiting downloads from the server: paused for {RATE_PAUSE_HOURS} hours, "
                               "then it carries on by itself (or press Resume).", until=now + RATE_PAUSE_HOURS * 3600)
    elif outcome == "net":
        st["backoff_at"] = now + NET_WAIT
    else:
        st["level"] = 0
        st["backoff_at"] = 0.0
        st["next_at"] = _slot(now) if library else max(st["next_at"], now + random.uniform(*GAP_SECONDS))
    # A dead sign-in isn't a limit that passes: pause until a fresh cookies.txt (not the owner's own pause).
    p = st.get("paused")
    if ig_health.attention() and (not p or p["kind"] == "rate"):
        _pause(st, "login", "Instagram downloads are paused. " + ig_health.ATTENTION_PAGE)
    _save(st)
    return st


# ── Jobs (the owner's buttons) ────────────────────────────────────────────────

def _set_state(api, ids: list[int], frm: str, to: str, extra: dict | None = None) -> int:
    n = 0
    for i in range(0, len(ids), QUEUE_CHUNK):
        chunk = ids[i:i + QUEUE_CHUNK]
        n += len(api.update("reference_analysis", {"reference_id": f"in.({','.join(map(str, chunk))})",
                                                   "state": f"eq.{frm}"}, {"state": to, **(extra or {})}))
    return n


def run_jobs(api, refs: list[dict], analyses: dict[int, dict], conn) -> list[str]:
    """The planner's Analyse / Pause / Resume / Stop / Try again (job kind 'refs')."""
    import cloud_sync
    lines = []
    for job in api.select("jobs", {"runner": "eq.laptop", "state": "eq.queued", "kind": "eq.refs"}):
        claimed = api.update("jobs", {"id": f"eq.{job['id']}", "state": "eq.queued"},
                             {"state": "running", "started_at": cloud_sync._now()})
        if not claimed:
            continue
        p = job.get("payload") or {}
        action = p.get("action") or "analyse"
        try:
            result = _job(api, refs, analyses, conn, action, p, job["id"])
            cloud_sync._finish(api, job, result)
            lines.append(f"refs #{job['id']}: {action} ({result.get('note', '')[:80]})")
        except Exception as e:
            if isinstance(e, cloud_sync.CloudError) and "can't reach" in str(e):
                raise
            if not isinstance(e, (RuntimeError, cloud_sync.CloudError)):
                log.exception("Reference job %s failed", job["id"])
            cloud_sync._finish(api, job, error=str(e)[:1000])
            lines.append(f"refs #{job['id']}: failed ({str(e)[:80]})")
    return lines


def _job(api, refs, analyses, conn, action: str, p: dict, job_id: int) -> dict:
    lib = [r for r in refs if is_library(r) and r["status"] == "active"]
    st = _state()
    if action == "pause":
        _pause(st, "owner", "Paused by the owner: press Resume to carry on.")
        _save(st)
        return {"note": "paused"}
    if action == "resume":
        st.update(paused=None, level=0, recent=[], next_at=0.0, backoff_at=0.0)
        _save(st)
        return {"note": "resumed"}
    if action == "stop":
        ids = [r["id"] for r in lib if (analyses.get(r["id"]) or {}).get("state") == "pending"]
        n = _set_state(api, ids, "pending", "held")
        return {"held": n, "note": f"{n} put back, nothing more is spent"}
    if action == "retry":
        ids = [r["id"] for r in lib if (analyses.get(r["id"]) or {}).get("state") == "failed"
               and (analyses[r["id"]].get("problem") or "other") != "gone"]
        n = _set_state(api, ids, "failed", "pending", {"error": None, "problem": None})
        st.update(paused=None, level=0, recent=[], next_at=0.0, backoff_at=0.0)
        _save(st)
        return {"queued": n, "note": f"{n} queued again"}
    if action != "analyse":
        raise RuntimeError(f"unknown action {action!r}")
    held = [r for r in lib if (analyses.get(r["id"]) or {}).get("state") == "held"]
    if p.get("brand"):
        held = [r for r in held if r["brand"] == p["brand"]]
    if p.get("ids"):
        wanted = {int(i) for i in p["ids"] if str(i).isdigit()}
        held = [r for r in held if r["id"] in wanted]
    if not held:
        return {"queued": 0, "note": "nothing waiting to be analysed"}
    mode = p.get("mode") or "all"
    n = len(held) if mode == "all" else max(1, min(int(p.get("n") or SAMPLE_SIZE), len(held)))
    each = per_post_usd(conn)
    left = budget(conn)["left_usd"]
    fit = int(left / each) if each > 0 else n
    note = ""
    if fit < n:
        note = (f"only {fit} fit in what's left of this month's budget (US${left:.2f} of "
                f"US${settings.REFERENCE_BUDGET_USD:.0f}); the rest stay waiting")
        n = fit
    chosen = held if n >= len(held) else choose_sample(held, n, seed=job_id)
    queued = _set_state(api, [r["id"] for r in chosen], "held", "pending")
    if st.get("paused") and st["paused"]["kind"] in ("owner", "budget"):
        st["paused"] = None
    st["next_at"] = min(st.get("next_at") or 0.0, time.time())
    _save(st)
    hours = queued * seconds_per_post() / 3600
    return {"queued": queued, "estimate_usd": round(queued * each, 2), "hours": round(hours, 1),
            "note": note or f"{queued} queued, about US${queued * each:.2f} and {hours:.0f} hours"}


# ── Analysing, one at a time ──────────────────────────────────────────────────

def next_up(refs: list[dict], analyses: dict[int, dict]) -> dict | None:
    """The next queued library reference: a stale 'running' one (its worker died) first, then in
    the order they were queued; one Instagram refused waits behind the rest."""
    now = datetime.now(timezone.utc)
    import cloud_sync
    stale = []
    pending = []
    for r in refs:
        a = analyses.get(r["id"])
        if not a or not is_library(r) or r["status"] != "active":
            continue
        if a["state"] == "pending" and _own(r):
            continue  # its own file: no download, analysed with the hand-added ones (hand_queue)
        if a["state"] == "pending":
            pending.append(r)
        elif a["state"] == "running":
            updated = cloud_sync._ts(a.get("updated_at"))
            if updated and now - datetime.fromisoformat(updated) > timedelta(minutes=cloud_sync.REF_STALE_MINUTES):
                stale.append(r)
    # A post Instagram refused goes back in the queue with a newer updated_at: to the back of it.
    pending.sort(key=lambda r: (str(analyses[r["id"]].get("updated_at") or ""), r["id"]))
    return (sorted(stale, key=lambda r: r["id"]) + pending or [None])[0]


def _instagram(ref: dict) -> bool:
    return "instagram.com" in (ref.get("url") or "").lower()


def _own(ref: dict) -> bool:
    import ref_files
    return ref_files.files_for(ref["id"]) is not None


REFUSED = ("rate", "login")   # a failed hand-added reference Instagram turned away: tried again when it lets us


def hand_queue(refs: list[dict], analyses: dict[int, dict], now: datetime | None = None) -> list[dict]:
    """The references to analyse this tick before any library one, in order: new or re-queued
    hand-added ones (and any reference with its own file), then hand-added ones Instagram refused once
    it may be asked again, then the rest that are due (cloud_sync._due). At most one of them downloads
    from Instagram in a tick; the others need no download (a file, YouTube, TikTok)."""
    import cloud_sync
    now = now or datetime.now(timezone.utc)
    free = None
    first, retry, rest = [], [], []
    for r in refs:
        a = analyses.get(r["id"])
        if not a or r["status"] != "active":
            continue
        own = a["state"] == "pending" and _own(r)
        if is_library(r) and not own:
            continue  # the library's own queue (next_up), paced
        if own or a["state"] == "pending":
            first.append(r)  # tried once now, pause or not
        elif _instagram(r) and a["state"] == "failed" and a.get("problem") in REFUSED and not _own(r):
            if free is None:
                free = instagram_free()
            if free:
                retry.append(r)
        elif cloud_sync._due(a, now):
            rest.append(r)
    out, downloads = [], 0
    for r in sorted(first, key=lambda r: r["id"]) + sorted(retry, key=lambda r: (str(analyses[r["id"]].get("updated_at")), r["id"])) \
            + sorted(rest, key=lambda r: r["id"]):
        if _instagram(r) and not _own(r):
            if downloads:
                continue
            downloads += 1
        out.append(r)
    return out


def hand_one(api, ref: dict, analysis: dict, client=None) -> str:
    """One reference of hand_queue: analysed, and an Instagram try recorded for the pacing."""
    import cloud_sync
    downloads = _instagram(ref) and not _own(ref)
    line, outcome = cloud_sync.analyse_one(api, ref, analysis, client)
    if line and downloads:
        record(outcome or "other", library=False)
    return line


def tick(api, refs: list[dict], analyses: dict[int, dict], conn, client=None) -> list[str]:
    """At most one library analysis, when the pacing, budget and disk allow."""
    import cloud_sync
    ref = next_up(refs, analyses)
    if not ref:
        return []
    ok, why = ready(conn)
    if not ok:
        return []
    line, outcome = cloud_sync.analyse_one(api, ref, analyses[ref["id"]], client, library=True)
    if not line:
        return []
    record(outcome)
    if outcome == "ok":
        try:
            followers_for(api, ref)
        except Exception as e:  # nice to have: never stops the batch
            log.info("Follower count for %s not read: %s", ref.get("account"), e)
    return [line]


# ── Follower counts (for engagement per follower) ─────────────────────────────

def _accounts_file():
    return settings.ROOT / "Style" / "accounts.json"


def accounts() -> dict:
    try:
        return json.loads(_accounts_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def followers_for(api, ref: dict) -> int | None:
    """The account's follower count (read once a month per account, only with the sign-in
    file: gallery-dl's profile info), written into this reference's engagement."""
    import style_study
    rows = api.select("reference_analysis", {"reference_id": f"eq.{ref['id']}", "select": "reference_id,engagement",
                                             "order": "reference_id"})
    eng = (rows[0].get("engagement") if rows else None) or {}
    account = (eng.get("account") or ref.get("account") or "").strip().lower()
    if not account or eng.get("followers") is not None or not style_study.COOKIES_FILE.is_file():
        return eng.get("followers")
    cache = accounts()
    hit = cache.get(account)
    fresh = hit and time.time() - hit.get("at", 0) < FOLLOWERS_DAYS * 86400
    if fresh:
        n = hit.get("followers")
    else:
        n = style_study.account_followers(account)
        cache[account] = {"followers": n, "at": time.time()}
        path = _accounts_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, indent=1), encoding="utf-8")
    if n is not None:
        api.update("reference_analysis", {"reference_id": f"eq.{ref['id']}"}, {"engagement": {**eng, "followers": n}})
    return n


# ── For the heartbeat (laptop_status.info.references) ─────────────────────────

def info(conn) -> dict:
    """The pacing, the budget and the estimates, for the planner. Never the sign-in itself."""
    st = _state()
    import style_study
    p = st.get("paused")
    try:
        mtime = style_study.COOKIES_FILE.stat().st_mtime
        signin = {"present": True, "added_at": datetime.fromtimestamp(mtime, timezone.utc).isoformat(timespec="seconds")}
    except OSError:
        signin = {"present": False}
    try:
        import ig_health
        signin.update(ig_health.info())
    except Exception as e:  # nice to have: never stops the heartbeat
        log.debug("No sign-in health: %s", e)
    wait = max(st.get("next_at") or 0.0, st.get("backoff_at") or 0.0)
    return {"paused": {"kind": p["kind"], "reason": p["reason"],
                       "at": datetime.fromtimestamp(p["at"], timezone.utc).isoformat(timespec="seconds")} if p else None,
            "next_at": datetime.fromtimestamp(wait, timezone.utc).isoformat(timespec="seconds")
            if wait > time.time() else None,
            "backing_off": st.get("level", 0) > 0, "budget": budget(conn), "estimates": estimates(conn),
            "signin": signin}
