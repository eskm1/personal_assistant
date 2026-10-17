"""
Team logins for the dashboard.

Everyone signs in with an email and password (hashed with scrypt; nothing is
stored in the clear). Bryan is the owner: he invites people with one-time links,
and only he can spend on edits, approve, reject or post. The team can plan,
comment, vote, tick off shots and fix tags.

The first account is made at /setup, which only answers on the laptop itself
(not through a tunnel), or with `worker.py --add-user`. Sessions are signed
cookies that last 30 days; changing a password or removing someone signs them
out everywhere.

When planning is online, the planner's email and password work here too
(checked with Supabase, cloud_auth.py): the first sign-in makes the local
account, with the planner role. Team accounts must use an email at one of
STUDIO_ALLOWED_EMAIL_DOMAINS (theurbanmakers.com); owners are always let in.
"""
import hashlib
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

import catalog
import settings
from settings import ROOT

OWNER, TEAM = "owner", "team"
INVITE_DAYS = 7
MIN_PASSWORD = 10

# A few wrong passwords are typos; many are someone guessing.
_FAILS: dict[str, list[float]] = {}
_FAILS_LOCK = threading.Lock()
MAX_FAILS, FAIL_WINDOW = 8, 15 * 60


def secret_key() -> bytes:
    """Signs session cookies. Made once and kept next to the catalogue."""
    path = Path(ROOT) / "session.key"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(secrets.token_bytes(32))
    return path.read_bytes()


def email_allowed(email: str, role: str = TEAM) -> bool:
    """Owners always; everyone else only with an email at an allowed domain."""
    domains = settings.ALLOWED_EMAIL_DOMAINS
    if role == OWNER or not domains or "*" in domains:
        return True
    return (email or "").strip().lower().rpartition("@")[2] in domains


def domain_hint() -> str:
    domains = [d for d in settings.ALLOWED_EMAIL_DOMAINS if d != "*"]
    return f"Use your @{domains[0]} email." if domains else ""


def users_exist(conn) -> bool:
    return conn.execute("SELECT 1 FROM users WHERE disabled = 0 LIMIT 1").fetchone() is not None


def create_user(conn, email: str, name: str, password: str, role: str = TEAM) -> int:
    email, name = email.strip().lower(), name.strip()
    if "@" not in email or not name:
        raise ValueError("enter a name and an email address")
    if not email_allowed(email, role):
        raise ValueError(domain_hint())
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"use at least {MIN_PASSWORD} characters for the password")
    if conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
        raise ValueError("there's already an account with that email")
    cur = conn.execute(
        "INSERT INTO users (email, name, role, password_hash, created_at) VALUES (?, ?, ?, ?, ?)",
        (email, name[:80], role, generate_password_hash(password), catalog.now()))
    conn.commit()
    return cur.lastrowid


def _throttled(key: str) -> bool:
    with _FAILS_LOCK:
        now = time.time()
        recent = [t for t in _FAILS.get(key, []) if now - t < FAIL_WINDOW]
        _FAILS[key] = recent
        return len(recent) >= MAX_FAILS


def _failed(key: str) -> None:
    with _FAILS_LOCK:
        _FAILS.setdefault(key, []).append(time.time())


def login(conn, email: str, password: str, client_ip: str, planner=None) -> dict:
    """The user row on success; raises ValueError with a message to show otherwise.
    planner(email, password) -> {"name", "role"} or None: the planner's check, tried
    when the local password doesn't match (the throttle covers both)."""
    email = email.strip().lower()
    for key in (f"ip:{client_ip}", f"email:{email}"):
        if _throttled(key):
            raise ValueError("Too many wrong attempts. Try again in 15 minutes.")
    row = conn.execute("SELECT * FROM users WHERE email = ? AND disabled = 0", (email,)).fetchone()
    if row is None or not check_password_hash(row["password_hash"], password):
        found = planner(email, password) if planner and password else None
        if not found:
            _failed(f"ip:{client_ip}")
            _failed(f"email:{email}")
            raise ValueError("That email and password don't match.")
        row = planner_user(conn, email, found["name"], found["role"])
    elif planner and row["role"] != OWNER and not row["planner_ok"]:
        # Same password in the planner? Then the email is proven theirs (the planner opens here).
        try:
            if planner(email, password):
                conn.execute("UPDATE users SET planner_ok = 1 WHERE id = ?", (row["id"],))
                row = conn.execute("SELECT * FROM users WHERE id = ?", (row["id"],)).fetchone()
        except ValueError:
            pass
    if not email_allowed(row["email"], row["role"]):
        raise ValueError(domain_hint())
    conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (catalog.now(), row["id"]))
    conn.commit()
    return dict(row)


def planner_user(conn, email: str, name: str, role: str):
    """The local account for someone who signed in with their planner password: made
    the first time (no usable local password), with the planner's role after that.
    The last owner is never demoted. A removed account stays removed."""
    role = OWNER if role == OWNER else TEAM
    row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if row is not None and row["disabled"]:
        raise ValueError("This account was removed from Studio. Ask Bryan to add you again.")
    if row is None:
        if not email_allowed(email, role):
            raise ValueError(domain_hint())
        conn.execute("INSERT INTO users (email, name, role, password_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                     (email, (name or email.split("@")[0]).strip()[:80], role,
                      generate_password_hash(secrets.token_urlsafe(32)), catalog.now()))
    elif row["role"] != role:
        owners = conn.execute("SELECT COUNT(*) FROM users WHERE role = ? AND disabled = 0", (OWNER,)).fetchone()[0]
        if not (row["role"] == OWNER and owners <= 1):
            conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, row["id"]))
    conn.execute("UPDATE users SET planner_ok = 1 WHERE email = ?", (email,))
    conn.commit()
    return conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()


def current(conn, session) -> dict | None:
    uid, version = session.get("uid"), session.get("sv")
    if not uid:
        return None
    row = conn.execute("SELECT * FROM users WHERE id = ? AND disabled = 0", (uid,)).fetchone()
    if row is None or row["session_version"] != version or not email_allowed(row["email"], row["role"]):
        return None
    return dict(row)


def set_password(conn, user_id: int, password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"use at least {MIN_PASSWORD} characters for the password")
    conn.execute("UPDATE users SET password_hash = ?, session_version = session_version + 1 WHERE id = ?",
                 (generate_password_hash(password), user_id))
    conn.commit()


def remove_user(conn, user_id: int) -> None:
    conn.execute("UPDATE users SET disabled = 1, session_version = session_version + 1 WHERE id = ?", (user_id,))
    conn.commit()


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_invite(conn, created_by: int, role: str = TEAM, note: str = "") -> str:
    """A one-time token for an invite link, valid for a week."""
    token = secrets.token_urlsafe(24)
    expires = (datetime.now(timezone.utc) + timedelta(days=INVITE_DAYS)).isoformat(timespec="seconds")
    conn.execute("""INSERT INTO invites (token_hash, role, note, created_by, created_at, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                 (_hash(token), role, note[:200], created_by, catalog.now(), expires))
    conn.commit()
    return token


def open_invite(conn, token: str):
    row = conn.execute("SELECT * FROM invites WHERE token_hash = ?", (_hash(token),)).fetchone()
    if row is None or row["used_at"] or row["expires_at"] < catalog.now():
        return None
    return row


def accept_invite(conn, token: str, email: str, name: str, password: str) -> int:
    invite = open_invite(conn, token)
    if invite is None:
        raise ValueError("This invite link has expired or was already used. Ask Bryan for a new one.")
    user_id = create_user(conn, email, name, password, invite["role"])
    conn.execute("UPDATE invites SET used_by = ?, used_at = ? WHERE id = ?", (user_id, catalog.now(), invite["id"]))
    conn.commit()
    return user_id
