"""
Ad earning.

SECURITY: Coins are credited ONLY here, server-side, on the rewarded-ad
completion callback relayed by the app. The client cannot set its own balance.
Daily limit and cooldown are enforced server-side regardless of the client.
"""
from datetime import datetime, timezone

from flask import Blueprint, request, jsonify, g

import database.db as db
from services import auth_required
from extensions import limiter

ad_bp = Blueprint("ads", __name__)


@ad_bp.get("/available")
def available_ads():
    ads = db.get_active_ads()
    return jsonify({"ads": ads})


@ad_bp.post("/watch-complete")
@auth_required
@limiter.limit("20 per minute")  # belt-and-suspenders; daily limit enforced below
def watch_complete():
    data = request.get_json(silent=True) or {}
    ad_id = (data.get("ad_id") or "").strip()

    ad = db.get_ad(ad_id)
    if not ad or not ad.get("is_active"):
        return jsonify({"error": "Ad not available"}), 404

    settings = db.get_settings()
    if settings.get("maintenance_mode"):
        return jsonify({"error": "Service under maintenance"}), 503

    user = db.get_user_by_id(g.user["id"])
    today = db.today_str()
    daily_limit = min(ad.get("daily_limit", 10), settings["daily_ad_limit"])

    # Reset the daily counter if it's a new day.
    watched_today = user["ads_watched_today"] if user["last_ad_date"] == today else 0

    if watched_today >= daily_limit:
        return jsonify({"error": "Daily ad limit reached", "limit": daily_limit}), 429

    # Cooldown enforcement.
    cooldown = settings["ad_cooldown_seconds"]
    if user.get("last_ad_time"):
        try:
            last = datetime.fromisoformat(user["last_ad_time"])
            elapsed = (datetime.now(timezone.utc) - last).total_seconds()
            if elapsed < cooldown:
                wait = int(cooldown - elapsed)
                return jsonify({"error": "Cooldown active", "wait_seconds": wait}), 429
        except ValueError:
            pass

    reward = ad["coins_reward"]

    # Credit the viewer.
    db.update_user(
        user["id"],
        {
            "ads_watched_today": watched_today + 1,
            "last_ad_date": today,
            "last_ad_time": db.now_iso(),
        },
    )
    updated = db.add_coins(user["id"], reward)
    db.add_transaction(user["id"], "ad_earn", reward, f"Watched: {ad['title']}")
    db.increment_ad_views(ad_id)

    # Referral passive income: instantly credit the referrer their %.
    referrer_bonus = 0
    if user.get("referred_by"):
        referrer = db.get_user_by_code(user["referred_by"])
        if referrer and not referrer.get("is_banned"):
            percent = settings["referral_bonus_percent"]
            referrer_bonus = round(reward * percent / 100)
            if referrer_bonus > 0:
                db.add_coins(referrer["id"], referrer_bonus)
                db.add_transaction(
                    referrer["id"],
                    "referral_earn",
                    referrer_bonus,
                    f"{percent}% of {user['phone']}'s ad earning",
                )
                db.add_referral_earning(referrer["id"], user["id"], referrer_bonus)

    return jsonify(
        {
            "coins_earned": reward,
            "new_balance": updated["coins"],
            "referrer_bonus": referrer_bonus,
            "ads_watched_today": watched_today + 1,
            "daily_limit": daily_limit,
        }
    )


@ad_bp.get("/status")
@auth_required
def ad_status():
    """How many ads the user can still watch today + cooldown remaining."""
    user = db.get_user_by_id(g.user["id"])
    settings = db.get_settings()
    today = db.today_str()
    watched = user["ads_watched_today"] if user["last_ad_date"] == today else 0
    limit = settings["daily_ad_limit"]

    cooldown_remaining = 0
    if user.get("last_ad_time"):
        try:
            last = datetime.fromisoformat(user["last_ad_time"])
            elapsed = (datetime.now(timezone.utc) - last).total_seconds()
            cooldown_remaining = max(0, int(settings["ad_cooldown_seconds"] - elapsed))
        except ValueError:
            pass

    return jsonify(
        {
            "ads_watched_today": watched,
            "daily_limit": limit,
            "remaining": max(0, limit - watched),
            "cooldown_remaining": cooldown_remaining,
        }
    )
