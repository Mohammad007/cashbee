"""User profile management."""
from flask import Blueprint, request, jsonify, g

import database.db as db
from services import auth_required

user_bp = Blueprint("user", __name__)


@user_bp.get("/profile")
@auth_required
def profile():
    user = db.get_user_by_id(g.user["id"])  # fresh read
    return jsonify(user)


@user_bp.post("/profile")
@auth_required
def update_profile():
    data = request.get_json(silent=True) or {}
    patch = {}
    if "name" in data:
        patch["name"] = str(data["name"])[:60]
    if "photo_url" in data:
        patch["photo_url"] = str(data["photo_url"])
    user = db.update_user(g.user["id"], patch)
    return jsonify(user)


@user_bp.post("/onboarding")
@auth_required
def onboarding():
    """
    One-time onboarding for a new user (shown right after OTP):
    set the display name and (optionally) apply a referral code.

    Applying a referral code is one-time only — once `referred_by` is set it
    cannot be changed. The referrer is credited the signup bonus here.
    """
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()[:60]
    referral_code = (data.get("referral_code") or "").strip().upper() or None

    user = db.get_user_by_id(g.user["id"])

    # Validate the referral code BEFORE writing anything.
    referrer = None
    if referral_code:
        if user.get("referred_by"):
            return jsonify({"error": "A referral code is already applied"}), 400
        if referral_code == user["referral_code"]:
            return jsonify({"error": "You can't use your own referral code"}), 400
        referrer = db.get_user_by_code(referral_code)
        if not referrer or referrer["id"] == user["id"]:
            return jsonify({"error": "Invalid referral code"}), 400

    patch = {}
    if name:
        patch["name"] = name
    if referrer:
        patch["referred_by"] = referral_code
    if patch:
        db.update_user(user["id"], patch)

    referral_applied = False
    if referrer:
        settings = db.get_settings()
        bonus = settings["referral_signup_bonus"]
        db.add_coins(referrer["id"], bonus)
        db.add_transaction(
            referrer["id"],
            "referral_earn",
            bonus,
            f"Signup bonus for referring {user['phone']}",
        )
        db.add_referral(referrer["id"], user["id"], coins_earned=bonus)
        # Award any referral milestones the referrer just unlocked.
        import gamification as gm

        gm.check_and_award_milestones(referrer["id"])
        referral_applied = True

    return jsonify(
        {"user": db.get_user_by_id(user["id"]), "referral_applied": referral_applied}
    )
