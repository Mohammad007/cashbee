"""
Gamification endpoints: daily streak, level, referral milestones,
festivals, and lucky spin. All earning is credited server-side.
"""
from flask import Blueprint, request, jsonify, g

import database.db as db
import gamification as gm
from services import auth_required

gami_bp = Blueprint("gamification", __name__)


# --------------------------------------------------------------------------- #
# Streak
# --------------------------------------------------------------------------- #
@gami_bp.post("/streak/checkin")
@auth_required
def streak_checkin():
    result = gm.streak_checkin(g.user["id"])
    if not result.get("ok"):
        return jsonify({"error": result.get("error", "Check-in failed")}), 400
    return jsonify(result)


@gami_bp.get("/streak/status")
@auth_required
def streak_status():
    user = db.get_user_by_id(g.user["id"])
    return jsonify(gm.streak_status(user))


# --------------------------------------------------------------------------- #
# Level
# --------------------------------------------------------------------------- #
@gami_bp.get("/user/level")
@auth_required
def user_level():
    user = db.get_user_by_id(g.user["id"])
    return jsonify(gm.level_info(user))


# --------------------------------------------------------------------------- #
# Referral milestones
# --------------------------------------------------------------------------- #
@gami_bp.post("/referral/check-milestones")
@auth_required
def check_milestones():
    return jsonify(gm.check_and_award_milestones(g.user["id"]))


@gami_bp.get("/referral/milestones")
@auth_required
def referral_milestones():
    user = db.get_user_by_id(g.user["id"])
    return jsonify(gm.milestones_overview(user))


# --------------------------------------------------------------------------- #
# Festivals
# --------------------------------------------------------------------------- #
@gami_bp.get("/festivals/active")
def festivals_active():
    fest = db.active_festival()
    if not fest:
        return jsonify({"active": False, "festival": None})

    # hours remaining until end_date (inclusive, end of that day IST)
    from datetime import datetime, timedelta
    hours_remaining = None
    try:
        end = datetime.strptime(fest["end_date"], "%Y-%m-%d").replace(tzinfo=db.IST)
        end = end + timedelta(days=1)  # festival valid through the whole end day
        delta = end - datetime.now(db.IST)
        hours_remaining = max(0, int(delta.total_seconds() // 3600))
    except (ValueError, KeyError):
        pass

    return jsonify({
        "active": True,
        "festival": {
            "name": fest["name"], "multiplier": fest["multiplier"],
            "end_date": fest.get("end_date"), "banner_text": fest.get("banner_text"),
            "banner_color": fest.get("banner_color"), "emoji": fest.get("emoji"),
            "hours_remaining": hours_remaining,
        },
    })


# --------------------------------------------------------------------------- #
# Lucky spin
# --------------------------------------------------------------------------- #
@gami_bp.post("/spin/use-ticket")
@auth_required
def spin_use_ticket():
    result = gm.do_spin(g.user["id"])
    if not result.get("ok"):
        return jsonify({"error": result.get("error", "Spin failed")}), 400
    return jsonify(result)


@gami_bp.get("/spin/status")
@auth_required
def spin_status():
    user = db.get_user_by_id(g.user["id"])
    return jsonify({
        "tickets": db.get_field(user, "spin_tickets", 0),
        "total_spins": db.get_field(user, "total_spins", 0),
        "total_earned": db.get_field(user, "total_spin_earnings", 0),
        "prizes": [{"label": p["label"], "coins": p["coins"]} for p in gm.spin_prizes()],
    })
