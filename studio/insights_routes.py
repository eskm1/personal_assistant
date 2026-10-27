"""
What kinds of posts work (insights.py), for the planner's Insights page. Under /api/instagram, so the planner
reaches it with its Bearer token like the posting API (dashboard._BEARER sends it through uploads.guard) and
Review with the dashboard cookie. Reads only, for every member: nothing here changes a post.

  GET /api/instagram/insights              the grade, the latest memo, every post's scores, the findings,
                                           the typical post of each format, followers, the forecasts
  GET /api/instagram/insights?memo=<date>  an earlier memo instead of the latest
"""
import re

from flask import Blueprint, jsonify, request

import insights
from webutil import db

bp = Blueprint("insights", __name__)


@bp.get("/api/instagram/insights")
def insights_page():
    day = request.args.get("memo") or None
    if day and not re.match(r"^\d{4}-\d{2}-\d{2}$", day):
        return jsonify({"error": "Which memo?"}), 400
    response = jsonify(insights.page(db(), day))
    response.headers["Cache-Control"] = "no-store"
    return response
