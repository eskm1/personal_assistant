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
"""
import hashlib
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

import catalog
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


def users_exist(conn) -> bool:
    return conn.execute("SELECT 1 FROM users WHERE disabled = 0 LIMIT 1").fetchone() is not None


def create_user(conn, email: str, name: str, password: str, role: str = TEAM) -> int:
    email, name = email.strip().lower(), name.strip()
    if "@" not in email or not name:
        raise ValueError("enter a name and an email address")
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


def login(conn, email: str, password: str, client_ip: str) -> dict:
    """The user row on success; raises ValueError with a message to show otherwise."""
    email = email.strip().lower()
    for key in (f"ip:{client_ip}", f"email:{email}"):
        if _throttled(key):
            raise ValueError("Too many wrong attempts. Try again in 15 minutes.")
    row = conn.execute("SELECT * FROM users WHERE email = ? AND disabled = 0", (email,)).fetchone()
    if row is None or not check_password_hash(row["password_hash"], password):
        _failed(f"ip:{client_ip}")
        _failed(f"email:{email}")
        raise ValueError("That email and password don't match.")
    conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (catalog.now(), row["id"]))
    conn.commit()
    return dict(row)


def current(conn, session) -> dict | None:
    uid, version = session.get("uid"), session.get("sv")
    if not uid:
        return None
    row = conn.execute("SELECT * FROM users WHERE id = ? AND disabled = 0", (uid,)).fetchone()
    if row is None or row["session_version"] != version:
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
