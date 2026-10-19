"""
The online planner, served by the dashboard at /plan/: one address and one login
for reviewing and planning.

The planner is the same static site Netlify serves (studio/cloud/planner), behind
the dashboard's sign-in. Its config.js is made here (the Supabase address and its
public key; uploads go to this same address), and /api/planner-session opens a
planner session for whoever is signed in to the dashboard, so the planner opens
signed in. The Netlify planner keeps working as before.
"""
import json
import logging
from pathlib import Path

from flask import Blueprint, Response, abort, g, jsonify, redirect, request, send_from_directory

import cloud_auth
import cloud_sync
import settings
from webutil import own_origin

log = logging.getLogger("studio.dashboard")
bp = Blueprint("planner_site", __name__)

PLANNER = Path(__file__).resolve().parent / "cloud" / "planner"
# What the planner is made of; anything else in the folder (build script, notes) isn't served.
SERVED = {".html", ".css", ".js", ".ico", ".svg", ".png", ".webp", ".woff2"}


def _origin(url: str) -> str:
    return "/".join(url.split("/")[:3])


def _csp() -> str:
    """The Netlify planner's policy (netlify.toml), for this Supabase project."""
    sb = _origin(settings.SUPABASE_URL)
    ws = sb.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
    https = request.is_secure or request.headers.get("X-Forwarded-Proto") == "https"
    return ("default-src 'self'; script-src 'self'; style-src 'self'; "
            f"img-src 'self' data: blob: {sb}; media-src 'self' https: {sb}; connect-src 'self' {sb} {ws}; "
            "font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
            + ("; upgrade-insecure-requests" if https else ""))


def _secure(response: Response, cache: str = "no-cache") -> Response:
    response.headers["Content-Security-Policy"] = _csp()
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
    response.headers["Cache-Control"] = cache
    return response


def available() -> bool:
    return cloud_sync.enabled() and (PLANNER / "index.html").is_file()


def here() -> bool:
    """The planner opens at /plan/ on this machine (with its public key): Studio then
    opens on Plan, and Review is the dashboard next to it."""
    return available() and bool(cloud_auth.anon_key())


@bp.get("/static/shell.js")
@bp.get("/static/shell.css")
@bp.get("/static/theme.css")
def shell_file():
    """The top bar and phone tab bar shared by Review and Plan, and the look (theme.css):
    one copy, in the planner's folder (Netlify serves it from there too)."""
    return send_from_directory(PLANNER, Path(request.path).name, max_age=0)


@bp.get("/static/fonts/<name>")
def font_file(name: str):
    """The bundled fonts theme.css names (IBM Plex Sans and Mono, Barlow), for Review and
    its sign-in page; the planner has them at /plan/fonts/."""
    if not name.endswith(".woff2") or "/" in name or name.startswith("."):
        abort(404)
    return send_from_directory(PLANNER / "fonts", name, max_age=86400)


@bp.get("/plan")
def plan_root():
    return redirect("/plan/")


@bp.get("/plan/")
@bp.get("/plan/<path:name>")
def planner_file(name: str = "index.html"):
    if not available():
        abort(404)
    if name == "config.js":
        return config_js()
    if Path(name).suffix.lower() not in SERVED or name.startswith(".") or "/." in name:
        abort(404)
    return _secure(send_from_directory(PLANNER, name, max_age=0))


def config_js() -> Response:
    key = cloud_auth.anon_key() or ""
    config = {"supabaseUrl": settings.SUPABASE_URL, "supabaseAnonKey": key, "studioUrl": ""}
    if not key:
        config["problem"] = cloud_auth.anon_problem() or "The planner's public key isn't known on this machine"
    body = f"// Made by Studio (planner_site.py).\nwindow.STUDIO_CONFIG = {json.dumps(config, indent=2)};\n"
    return _secure(Response(body, mimetype="text/javascript"), "no-store")


@bp.get("/api/planner-session")
def planner_session():
    """A planner session for the signed-in dashboard user: {access_token,
    refresh_token, expires_at, expires_in, token_type}. Same-origin pages only; owners,
    and team members who have signed in here with their planner password."""
    fetch_site = request.headers.get("Sec-Fetch-Site")
    origin = request.headers.get("Origin")
    if (fetch_site and fetch_site not in ("same-origin", "none")) or (origin and not own_origin(origin)):
        return jsonify({"error": "cross-site request refused"}), 403
    if not cloud_sync.enabled():
        return jsonify({"error": "Online planning isn't set up on this Studio machine"}), 404
    if g.user["role"] != "owner" and not g.user.get("planner_ok"):
        # A team account made from an invite link chose its own email: only a sign-in with the
        # planner's password proves the planner account is theirs.
        return jsonify({"error": "Sign out, then sign in with your planner email and password"}), 403
    try:
        session = cloud_auth.planner_session(g.user["email"], owner=g.user["role"] == "owner")
    except cloud_auth.AuthError as e:
        if e.status != 403:
            log.warning("Planner session for %s: %s", g.user["email"], e)
        return jsonify({"error": str(e)}), e.status
    response = jsonify(session)
    response.headers["Cache-Control"] = "no-store"
    return response
