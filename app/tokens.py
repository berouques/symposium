"""Token accounting: per-message, per-member, per-session, per-profile."""
from __future__ import annotations

from . import db


def session_totals(symposium_id: int) -> dict:
    row = db.query_one(
        "SELECT COALESCE(SUM(tokens_in),0) AS ti, COALESCE(SUM(tokens_out),0) AS to_, "
        "COUNT(*) AS calls FROM token_usage WHERE symposium_id=?",
        (symposium_id,),
    )
    by_type = db.query_all(
        "SELECT call_type, SUM(tokens_in) AS ti, SUM(tokens_out) AS to_, COUNT(*) AS calls "
        "FROM token_usage WHERE symposium_id=? GROUP BY call_type",
        (symposium_id,),
    )
    return {
        "tokens_in": row["ti"], "tokens_out": row["to_"], "calls": row["calls"],
        "by_type": [
            {"call_type": r["call_type"], "tokens_in": r["ti"], "tokens_out": r["to_"], "calls": r["calls"]}
            for r in by_type
        ],
    }


def member_totals(symposium_id: int) -> dict[int, dict]:
    rows = db.query_all(
        "SELECT member_id, SUM(tokens_in) AS ti, SUM(tokens_out) AS to_, COUNT(*) AS calls "
        "FROM token_usage WHERE symposium_id=? GROUP BY member_id",
        (symposium_id,),
    )
    return {r["member_id"]: {"tokens_in": r["ti"] or 0, "tokens_out": r["to_"] or 0, "calls": r["calls"]}
            for r in rows}


def profile_totals() -> list[dict]:
    rows = db.query_all(
        "SELECT p.id, p.name, "
        "(SELECT COALESCE(SUM(t.tokens_in + t.tokens_out),0) FROM token_usage t WHERE t.profile_id=p.id) AS tokens, "
        "(SELECT COALESCE(SUM(calls),0) FROM (SELECT COUNT(*) AS calls FROM token_usage t WHERE t.profile_id=p.id) ) AS calls "
        "FROM api_profiles p ORDER BY p.id"
    )
    out = []
    for r in rows:
        calls = db.query_value(
            "SELECT COUNT(*) FROM token_usage WHERE profile_id=?", (r["id"],)
        )
        out.append({"id": r["id"], "name": r["name"], "tokens": r["tokens"], "calls": calls})
    return out


def global_stats(user: dict) -> dict:
    """Token consumption dashboard (top-bar button).

    Regular users see their own symposia; admins see the whole installation.
    """
    is_admin = bool(user.get("is_admin"))
    uid = user["id"]

    if is_admin:
        total = db.query_one(
            "SELECT COALESCE(SUM(tokens_in),0) AS ti, COALESCE(SUM(tokens_out),0) AS to_, "
            "COUNT(*) AS calls FROM token_usage"
        )
        by_type = db.query_all(
            "SELECT call_type, SUM(tokens_in) AS ti, SUM(tokens_out) AS to_, COUNT(*) AS calls "
            "FROM token_usage GROUP BY call_type ORDER BY COUNT(*) DESC"
        )
        by_profile = db.query_all(
            "SELECT p.id, p.name, p.model, COALESCE(SUM(u.tokens_in),0) AS ti, "
            "COALESCE(SUM(u.tokens_out),0) AS to_, COUNT(u.id) AS calls "
            "FROM api_profiles p LEFT JOIN token_usage u ON u.profile_id = p.id "
            "GROUP BY p.id ORDER BY (ti + to_) DESC"
        )
        by_symposium = db.query_all(
            "SELECT s.id, s.title, s.status, COALESCE(SUM(u.tokens_in),0) AS ti, "
            "COALESCE(SUM(u.tokens_out),0) AS to_, COUNT(u.id) AS calls, s.last_activity "
            "FROM symposia s LEFT JOIN token_usage u ON u.symposium_id = s.id "
            "GROUP BY s.id ORDER BY (ti + to_) DESC LIMIT 200"
        )
        by_day = db.query_all(
            "SELECT substr(created_at, 1, 10) AS day, SUM(tokens_in + tokens_out) AS tokens, "
            "COUNT(*) AS calls FROM token_usage GROUP BY day ORDER BY day DESC LIMIT 30"
        )
    else:
        total = db.query_one(
            "SELECT COALESCE(SUM(u.tokens_in),0) AS ti, COALESCE(SUM(u.tokens_out),0) AS to_, "
            "COUNT(*) AS calls FROM token_usage u "
            "JOIN symposia s ON s.id = u.symposium_id WHERE s.owner_id=?",
            (uid,),
        )
        by_type = db.query_all(
            "SELECT u.call_type, SUM(u.tokens_in) AS ti, SUM(u.tokens_out) AS to_, COUNT(*) AS calls "
            "FROM token_usage u JOIN symposia s ON s.id = u.symposium_id WHERE s.owner_id=? "
            "GROUP BY u.call_type ORDER BY COUNT(*) DESC",
            (uid,),
        )
        by_profile = db.query_all(
            "SELECT p.id, p.name, p.model, COALESCE(SUM(u.tokens_in),0) AS ti, "
            "COALESCE(SUM(u.tokens_out),0) AS to_, COUNT(u.id) AS calls "
            "FROM api_profiles p LEFT JOIN token_usage u ON u.profile_id = p.id "
            "WHERE p.id IN (SELECT DISTINCT s.api_profile_id FROM symposia s WHERE s.owner_id=?) "
            "GROUP BY p.id ORDER BY (ti + to_) DESC",
            (uid,),
        )
        by_symposium = db.query_all(
            "SELECT s.id, s.title, s.status, COALESCE(SUM(u.tokens_in),0) AS ti, "
            "COALESCE(SUM(u.tokens_out),0) AS to_, COUNT(u.id) AS calls, s.last_activity "
            "FROM symposia s LEFT JOIN token_usage u ON u.symposium_id = s.id "
            "WHERE s.owner_id=? GROUP BY s.id ORDER BY (ti + to_) DESC LIMIT 200",
            (uid,),
        )
        by_day = db.query_all(
            "SELECT substr(u.created_at, 1, 10) AS day, SUM(u.tokens_in + u.tokens_out) AS tokens, "
            "COUNT(*) AS calls FROM token_usage u "
            "JOIN symposia s ON s.id = u.symposium_id WHERE s.owner_id=? "
            "GROUP BY day ORDER BY day DESC LIMIT 30",
            (uid,),
        )
    return {
        "scope": "all" if user.get("is_admin") else "own",
        "total": {
            "tokens_in": total["ti"] or 0,
            "tokens_out": total["to_"] or 0,
            "tokens": (total["ti"] or 0) + (total["to_"] or 0),
            "calls": total["calls"] or 0,
        },
        "by_type": [
            {"call_type": r["call_type"], "tokens_in": r["ti"], "tokens_out": r["to_"],
             "tokens": (r["ti"] or 0) + (r["to_"] or 0), "calls": r["calls"]}
            for r in by_type
        ],
        "by_profile": [
            {"id": r["id"], "name": r["name"], "model": r["model"], "tokens_in": r["ti"],
             "tokens_out": r["to_"], "tokens": (r["ti"] or 0) + (r["to_"] or 0), "calls": r["calls"]}
            for r in by_profile
        ],
        "by_symposium": [
            {"id": r["id"], "title": r["title"], "status": r["status"], "tokens_in": r["ti"],
             "tokens_out": r["to_"], "tokens": (r["ti"] or 0) + (r["to_"] or 0), "calls": r["calls"],
             "last_activity": r["last_activity"]}
            for r in by_symposium
        ],
        "by_day": [
            {"day": r["day"], "tokens": r["tokens"], "calls": r["calls"]} for r in by_day
        ],
    }
