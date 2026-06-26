"""
Ad earning.

SECURITY: Coins are credited ONLY here, server-side, on the rewarded-ad
completion callback relayed by the app. The client cannot set its own balance.
Daily limit and cooldown are enforced server-side regardless of the client.
"""
from datetime import datetime, timezone

from flask import Blueprint, request, jsonify, g

import database.db as db
import gamification as gm
from services import auth_required, create_watch_token, watch_token_elapsed
from extensions import limiter

ad_bp = Blueprint("ads", __name__)


@ad_bp.get("/available")
def available_ads():
    ads = db.get_active_ads()
    return jsonify({"ads": ads})


@ad_bp.post("/start")
@auth_required
def start_watch():
    """
    Begin a custom-ad watch session. Returns a signed watch token that
    /watch-complete checks to confirm the user watched the full duration.
    (Rewarded/interstitial ads don't need this — AdMob guarantees the view.)
    """
    data = request.get_json(silent=True) or {}
    ad_id = (data.get("ad_id") or "").strip()
    ad = db.get_ad(ad_id)
    if not ad or not ad.get("is_active"):
        return jsonify({"error": "Ad not available"}), 404
    return jsonify(
        {
            "watch_token": create_watch_token(g.user["id"], ad_id),
            "watch_seconds": int(ad.get("watch_seconds") or 30),
        }
    )


# Google's official TEST rewarded unit (always fills) — used when use_test_ads is on.
_TEST_REWARDED = "ca-app-pub-3940256099942544/5224354917"


@ad_bp.get("/config")
def ads_config():
    """
    Runtime AdMob config for the mobile app. The app fetches this on launch, so
    the admin can change ad unit IDs (or flip to test ads / disable ads) from the
    panel WITHOUT shipping a new build. IDs are not secret — they ship in any APK.
    """
    s = db.get_settings()
    test = bool(s.get("use_test_ads"))
    return jsonify(
        {
            "maintenance_mode": bool(s.get("maintenance_mode")),
            "ads_enabled": bool(s.get("ads_enabled", True)),
            "use_test_ads": test,
            "rewarded_ad_unit_id": _TEST_REWARDED if test else (s.get("admob_rewarded_id") or ""),
        }
    )


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

    # Custom ads have no AdMob reward callback, so verify the signed watch token:
    # the user must have actually spent `watch_seconds` watching.
    if ad.get("ad_type") == "custom":
        watch_seconds = int(ad.get("watch_seconds") or 30)
        token = (data.get("watch_token") or "").strip()
        elapsed = watch_token_elapsed(token, g.user["id"], ad_id)
        if elapsed is None:
            return jsonify({"error": "Invalid watch session. Please retry."}), 400
        if elapsed < watch_seconds - 2:  # 2s grace for network/render lag
            return jsonify({"error": "Please watch the full ad to earn coins."}), 400

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

    base = ad["coins_reward"]

    # Multipliers: base × user level multiplier × active festival multiplier.
    level_mult = float(db.get_field(user, "coin_multiplier", 1.0))
    festival = db.active_festival()
    festival_mult = float(festival["multiplier"]) if festival else 1.0
    total_mult = level_mult * festival_mult
    reward = max(1, round(base * total_mult))

    # Mark the watch (daily counter + cooldown) and hand out a spin ticket.
    db.update_user(
        user["id"],
        {
            "ads_watched_today": watched_today + 1,
            "last_ad_date": today,
            "last_ad_time": db.now_iso(),
            "spin_tickets": db.get_field(user, "spin_tickets", 0) + 1,
        },
    )
    db.increment_ad_views(ad_id)

    # Credit via the central earning path (updates lifetime + level).
    updated, level_up, new_level = gm.credit_earning(
        user["id"], reward, "ad_earn", f"Watched: {ad['title']}"
    )

    # Referral passive income: instantly credit the referrer their %.
    referrer_bonus = 0
    if user.get("referred_by"):
        referrer = db.get_user_by_code(user["referred_by"])
        if referrer and not referrer.get("is_banned"):
            percent = settings["referral_bonus_percent"]
            referrer_bonus = round(reward * percent / 100)
            if referrer_bonus > 0:
                gm.credit_earning(
                    referrer["id"], referrer_bonus, "referral_earn",
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
            # Earnings breakdown for the UI ("10 × 1.5 × 3.0 = 45")
            "breakdown": {
                "base": base,
                "level_multiplier": level_mult,
                "festival_multiplier": festival_mult,
                "total_multiplier": round(total_mult, 2),
            },
            # Level
            "level_up": level_up,
            "new_level": new_level,
            # Spin ticket
            "spin_tickets_earned": 1,
            "total_tickets": db.get_field(updated, "spin_tickets", 0),
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
