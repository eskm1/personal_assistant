"""Shared plumbing for the dashboard's routes: the per-request catalogue
connection, who is signed in, and the owner-only guard."""
import functools
import sqlite3

from flask import g, jsonify, request

LOOPBACK = {"127.0.0.1", "localhost", "::1"}
# Set by proxies (Cloudflare Tunnel, Tailscale Serve/Funnel) in front of the laptop.
PROXY_HEADERS = ("Cf-Connecting-Ip", "X-Forwarded-For", "X-Forwarded-Host", "Forwarded", "Tailscale-User-Login")


def db() -> sqlite3.Connection:
    if "db" not in g:
        import catalog
        g.db = catalog.connect()
    return g.db


def user() -> dict:
    return g.user


def is_owner() -> bool:
    return bool(getattr(g, "user", None)) and g.user["role"] == "owner"


def is_local() -> bool:
    """A browser on the laptop itself, not a request relayed by a tunnel (which
    also arrives from 127.0.0.1, but carries forwarding headers)."""
    return request.remote_addr in LOOPBACK and not any(h in request.headers for h in PROXY_HEADERS)


def client_ip() -> str:
    return request.headers.get("Cf-Connecting-Ip") or request.remote_addr or "?"


def owner_only(view):
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        if not is_owner():
            return jsonify({"error": "only Bryan can do this"}), 403
        return view(*args, **kwargs)
    return wrapper


def body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def text(data: dict, key: str, limit: int = 2000) -> str:
    value = data.get(key, "")
    return str(value if value is not None else "").strip()[:limit]
