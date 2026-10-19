"""
The posting API (cloud/CONTRACT.md, "Posting to Instagram"), for Review (the dashboard cookie)
and the planner (its Bearer token, like the upload API; dashboard._auth sends a request with
one through uploads.guard). Reads are for every member; changes are the owner's.

  GET  /api/instagram                       the connection (never the token or secret) + the posting limit
  POST /api/instagram/connect               {access_token, app_id, app_secret}  (owner)
  POST /api/instagram/disconnect            (owner)
  GET  /api/instagram/plan/<production_id>  what the Schedule dialog needs
  GET  /api/instagram/posts                 this month and next (and the last 62 days)
  POST /api/instagram/posts                 {production_id, when ("now" or ISO), caption, cover_ms,
                                             share_to_feed, collaborators}  (owner)
  POST /api/instagram/posts/<id>            change: the same fields  (owner)
  POST /api/instagram/posts/<id>/cancel     (owner)
  GET  /api/instagram/results               results grouped (post_results.summary)
  GET  /public/media/<token>/<name>         the files of a post going out, for Instagram only
                                             (no sign-in; a signed link, two hours, only while it posts)
"""
import logging

from flask import Blueprint, abort, g, jsonify, request

import instagram
import posting
import post_results
import settings
from webutil import body, db

log = logging.getLogger("studio.dashboard")
bp = Blueprint("posting", __name__)


def _who() -> dict:
    """The signed-in person: the planner's (Bearer) or the dashboard's (cookie)."""
    planner = getattr(g, "planner_user", None)
    if planner:
        return {"name": planner.get("name") or planner.get("email") or "", "role": planner.get("role")}
    user = getattr(g, "user", None) or {}
    return {"name": user["name"] if user else "", "role": user["role"] if user else None}


def _owner():
    if _who()["role"] != "owner":
        return jsonify({"error": "Only Bryan can do this"}), 403
    return None


def _status() -> dict:
    st = instagram.status()
    owner = _who()["role"] == "owner"
    if not owner:  # the team sees whether it's connected, and as whom
        st = {k: st.get(k) for k in ("connected", "username", "expired")}
    st["posting_on"] = settings.POSTING
    if owner and st.get("connected") and request.args.get("limit") != "0":
        try:
            st["limit"] = instagram.publishing_limit()
        except instagram.IgError as e:
            st["limit_error"] = str(e)
    return st


@bp.get("/api/instagram")
def ig_status():
    response = jsonify(_status())
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.post("/api/instagram/connect")
def ig_connect():
    refused = _owner()
    if refused:
        return refused
    data = body()
    try:
        instagram.connect(str(data.get("access_token") or ""), str(data.get("app_id") or ""),
                          str(data.get("app_secret") or ""), by=_who()["name"])
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    log.info("Instagram connected by %s", _who()["name"] or "the owner")
    posting._wake()
    return jsonify(_status())


@bp.post("/api/instagram/disconnect")
def ig_disconnect():
    refused = _owner()
    if refused:
        return refused
    instagram.disconnect()
    log.info("Instagram disconnected by %s", _who()["name"] or "the owner")
    return jsonify({"connected": False})


@bp.get("/api/instagram/plan/<int:production_id>")
def ig_plan(production_id: int):
    try:
        return jsonify(posting.plan_for(db(), production_id))
    except KeyError:
        abort(404)


@bp.get("/api/instagram/posts")
def ig_posts():
    return jsonify({"posts": posting.listing(db()), "connected": instagram.connected()})


def _fields() -> tuple[dict, dict]:
    data = body()
    out = {}
    for k in ("caption", "cover_ms", "share_to_feed", "collaborators"):
        if k in data:
            out[k] = data[k]
    if "caption" in out:
        out["caption"] = str(out["caption"] or "")
    return out, data


@bp.post("/api/instagram/posts")
def ig_schedule():
    refused = _owner()
    if refused:
        return refused
    fields, data = _fields()
    try:
        production_id = int(data.get("production_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "Which draft?"}), 400
    try:
        post = posting.schedule(db(), production_id, data.get("when"), fields.get("caption"), fields.get("cover_ms"),
                                fields.get("share_to_feed", True), fields.get("collaborators"), by=_who()["name"])
    except posting.PostError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"post": post}), 201


@bp.post("/api/instagram/posts/<int:post_id>")
def ig_change(post_id: int):
    refused = _owner()
    if refused:
        return refused
    fields, data = _fields()
    try:
        post = posting.change(db(), post_id, data.get("when"), fields.get("caption"), fields.get("cover_ms"),
                              fields.get("share_to_feed"), fields.get("collaborators"))
    except KeyError:
        abort(404)
    except posting.PostError as e:
        return jsonify({"error": str(e)}), 409
    return jsonify({"post": post})


@bp.post("/api/instagram/posts/<int:post_id>/cancel")
def ig_cancel(post_id: int):
    refused = _owner()
    if refused:
        return refused
    try:
        post = posting.cancel(db(), post_id)
    except KeyError:
        abort(404)
    except posting.PostError as e:
        return jsonify({"error": str(e)}), 409
    return jsonify({"post": post})


@bp.get("/api/instagram/results")
def ig_results():
    return jsonify(post_results.summary(db()))


@bp.route("/public/media/<token>/<name>", methods=["GET", "HEAD"])
def public_media(token: str, name: str):
    """What Instagram fetches while a post goes out. Anything else: 404."""
    path = posting.public_file(db(), token, name)
    if path is None:
        abort(404)
    from dashboard import _file_response
    response = _file_response(str(path))
    response.headers["Cache-Control"] = "no-store"
    return response
