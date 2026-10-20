"""
Help, feedback and usage for Review (the dashboard cookie) and the planner (its Bearer token, like the upload API;
dashboard._auth sends a request with one through uploads.guard).

  POST /api/usage                      {session, qa, events: [...]}  (activity.py; also sent by sendBeacon)
  POST /api/help/ask                   {question, page, page_name, page_help: [...], qa} -> {id, answer, answered, left}
                                        or {unavailable, error, left}; 429 when today's questions are used up
  POST /api/help/ask/<id>/helpful      {helpful: true|false}
  POST /api/feedback                   {page, route, control, words, viewport, version, qa}  (Something not working?)
  GET  /api/feedback                   the owner: every flag, newest first
  POST /api/feedback/<id>/done         the owner: {done: true|false}
  GET  /api/report/weekly              ?start=YYYY-MM-DD&format=md: the report's key (Bearer) or the owner
  GET  /api/report/token               the owner: the key; POST /api/report/token/rotate: a new one
"""
import logging
from datetime import date

from flask import Blueprint, Response, g, jsonify, request

import activity
import help_desk
import settings
import usage_report
from webutil import body, db

log = logging.getLogger("studio.dashboard")
bp = Blueprint("help", __name__)


def who() -> dict | None:
    """The signed-in person: the planner's (Bearer) or the dashboard's (cookie). id = email."""
    planner = getattr(g, "planner_user", None)
    if planner:
        email = (planner.get("email") or planner.get("id") or "").lower()
        # One person, one row in the report: the dashboard account with this email, and for the owner the
        # dashboard's owner (the two may use different emails, as /api/planner-session allows).
        local = db().execute("SELECT email, name FROM users WHERE lower(email) = ? AND disabled = 0", (email,)).fetchone()
        if not local and planner.get("role") == "owner":
            local = db().execute("SELECT email, name FROM users WHERE role = 'owner' AND disabled = 0 ORDER BY id").fetchone()
        if local:
            return {"id": local["email"].lower(), "name": local["name"], "role": planner.get("role") or "team"}
        return {"id": email, "name": planner.get("name") or email.split("@")[0], "role": planner.get("role") or "team"}
    user = getattr(g, "user", None)
    if user:
        return {"id": (user["email"] or str(user["id"])).lower(), "name": user["name"], "role": user["role"]}
    return None


def _owner() -> bool:
    w = who()
    return bool(w) and w["role"] == "owner"


def _no_store(response):
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.post("/api/usage")
def post_usage():
    w = who()
    if not w:
        return jsonify({"error": "signed out"}), 401
    if not settings.USAGE:
        return jsonify({"on": False})
    if request.content_length and request.content_length > activity.MAX_BODY:
        return jsonify({"error": "too big"}), 413
    if activity._limited(w["id"]):
        return jsonify({"error": "too many"}), 429
    import json
    try:
        data = json.loads(request.get_data(cache=False, as_text=True)[:activity.MAX_BODY + 1] or "{}")
    except ValueError:
        return jsonify({"error": "not JSON"}), 400
    if not isinstance(data, dict):
        return jsonify({"error": "not an object"}), 400
    try:
        n = activity.record(db(), w, data.get("session"), data.get("events"), bool(data.get("qa")), data.get("batch"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"on": True, "kept": n})


@bp.post("/api/help/ask")
def help_ask():
    w = who()
    if not w:
        return jsonify({"error": "signed out"}), 401
    data = body()
    qa = bool(data.get("qa"))
    try:
        result = help_desk.ask(db(), w, data.get("question"), data.get("page"), data.get("page_name"),
                               data.get("page_help"), qa)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except LookupError as e:
        return jsonify({"error": str(e), "left": 0}), 429
    activity.add(db(), w, str(data.get("page") or "other")[:40], "action", "help_asked",
                 {"answered": bool(result.get("answered")), "unavailable": bool(result.get("unavailable"))}, qa)
    return _no_store(jsonify(result))


@bp.post("/api/help/ask/<int:qid>/helpful")
def help_helpful(qid: int):
    w = who()
    if not w:
        return jsonify({"error": "signed out"}), 401
    return jsonify({"ok": help_desk.helpful(db(), w, qid, bool(body().get("helpful")))})


@bp.post("/api/feedback")
def feedback_add():
    w = who()
    if not w:
        return jsonify({"error": "signed out"}), 401
    data = body()
    if not (str(data.get("words") or "").strip() or str(data.get("control") or "").strip()):
        return jsonify({"error": "Tap the part that's not working, or say what happened"}), 400
    fid = help_desk.flag(db(), w, data)
    log.info("Friction flag %s from %s on %s", fid, w["name"], str(data.get("page") or "")[:40])
    return jsonify({"ok": True, "id": fid})


@bp.get("/api/feedback")
def feedback_list():
    if not _owner():
        return jsonify({"error": "Only Bryan can see this"}), 403
    rows = help_desk.flags(db())
    return _no_store(jsonify({"flags": rows, "open": sum(1 for r in rows if not r["done_at"])}))


@bp.post("/api/feedback/<int:flag_id>/done")
def feedback_done(flag_id: int):
    if not _owner():
        return jsonify({"error": "Only Bryan can do this"}), 403
    ok = help_desk.mark_flag(db(), flag_id, bool(body().get("done", True)), who()["name"])
    return jsonify({"ok": ok}), 200 if ok else 404


@bp.get("/api/report/weekly")
def report_weekly():
    # Through the report's key (dashboard._auth checked it and set g.report_key) or the signed-in owner.
    if not getattr(g, "report_key", False) and not _owner():
        return jsonify({"error": "Only Bryan or the report's key can read this"}), 403
    start = None
    if request.args.get("start"):
        try:
            start = date.fromisoformat(request.args["start"])
        except ValueError:
            return jsonify({"error": "start must be YYYY-MM-DD"}), 400
    report = usage_report.weekly(db(), start)
    if request.args.get("format") == "md":
        return _no_store(Response(usage_report.markdown(report), mimetype="text/markdown; charset=utf-8"))
    return _no_store(jsonify(report))


@bp.get("/api/report/token")
def report_token():
    if not _owner():
        return jsonify({"error": "Only Bryan can see this"}), 403
    base = settings.DASHBOARD_URL or request.host_url.rstrip("/")
    return _no_store(jsonify({"token": usage_report.token(), "url": f"{base}/api/report/weekly"}))


@bp.post("/api/report/token/rotate")
def report_rotate():
    if not _owner():
        return jsonify({"error": "Only Bryan can do this"}), 403
    log.info("Weekly report key rotated by %s", who()["name"])
    return _no_store(jsonify({"token": usage_report.rotate()}))
