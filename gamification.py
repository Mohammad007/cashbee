"""
Gamification logic: levels, daily streak, referral milestones, lucky spin.

All "earning" (ads, streak, milestone, spin, bonuses) flows through
[credit_earning] so the level system stays consistent: every credited coin
counts toward `lifetime_coins_earned`, which drives the user's level/multiplier.
"""
import random
from datetime import datetime, timezone, timedelta

import database.db as db
from config import Config


def _today_yesterday() -> tuple[str, str]:
    d = datetime.now(timezone.utc)
    return d.strftime("%Y-%m-%d"), (d - timedelta(days=1)).strftime("%Y-%m-%d")


# --------------------------------------------------------------------------- #
# Levels
# --------------------------------------------------------------------------- #
def calculate_level(lifetime_coins: int) -> tuple[int, str, float]:
    for min_coins, level, name, mult in Config.LEVELS:  # highest threshold first
        if lifetime_coins >= min_coins:
            return level, name, mult
    return 1, "Bronze", 1.0


def credit_earning(user_id: str, coins: int, txn_type: str, description: str):
    """
    Credit coins that count toward lifetime earnings + leveling.
    Returns (updated_user, level_up: bool, level: {level, level_name, multiplier}).
    """
    user = db.get_user_by_id(user_id)
    if not user:
        return None, False, None

    lifetime = db.get_field(user, "lifetime_coins_earned", user.get("total_earned", 0))
    new_lifetime = lifetime + coins
    prev_level = db.get_field(user, "level", 1)
    level, name, mult = calculate_level(new_lifetime)

    db.update_user(
        user_id,
        {
            "coins": user["coins"] + coins,
            "total_earned": user["total_earned"] + coins,
            "lifetime_coins_earned": new_lifetime,
            "level": level,
            "level_name": name,
            "coin_multiplier": mult,
        },
    )
    if coins != 0:
        db.add_transaction(user_id, txn_type, coins, description)

    updated = db.get_user_by_id(user_id)
    return updated, level > prev_level, {"level": level, "level_name": name, "multiplier": mult}


def level_info(user: dict) -> dict:
    lifetime = db.get_field(user, "lifetime_coins_earned", user.get("total_earned", 0))
    level, name, mult = calculate_level(lifetime)
    ascending = sorted(Config.LEVELS, key=lambda x: x[0])
    nxt = next((row for row in ascending if row[0] > lifetime), None)
    if nxt:
        prev_min = max(minc for minc, *_ in ascending if minc <= lifetime)
        span = nxt[0] - prev_min
        progress = int(((lifetime - prev_min) / span) * 100) if span > 0 else 100
        return {
            "level": level, "level_name": name, "multiplier": mult,
            "lifetime_earned": lifetime, "next_level_name": nxt[2],
            "coins_to_next_level": nxt[0] - lifetime, "progress_percent": progress,
        }
    return {
        "level": level, "level_name": name, "multiplier": mult,
        "lifetime_earned": lifetime, "next_level_name": None,
        "coins_to_next_level": 0, "progress_percent": 100,
    }


# --------------------------------------------------------------------------- #
# Daily streak
# --------------------------------------------------------------------------- #
def _streak_rewards() -> list:
    return db.get_settings().get("streak_rewards", Config.DEFAULT_SETTINGS["streak_rewards"])


def streak_checkin(user_id: str) -> dict:
    user = db.get_user_by_id(user_id)
    today, yesterday = _today_yesterday()

    # Must watch at least 1 ad today to qualify for the streak check-in.
    if db.get_field(user, "last_ad_date", "") != today:
        return {"ok": False, "error": "Watch at least 1 ad today to check in."}

    last = db.get_field(user, "last_active_date", "")
    rewards = _streak_rewards()

    if last == today:  # already checked in today
        day = ((db.get_field(user, "current_streak", 1) - 1) % 7) + 1
        return {
            "ok": True, "already_checked_in": True, "streak_day": day,
            "current_streak": db.get_field(user, "current_streak", 1),
            "coins_earned": 0, "new_balance": user["coins"],
            "next_reward": rewards[(day % 7)], "is_jackpot": False,
        }

    streak = (db.get_field(user, "current_streak", 0) + 1) if last == yesterday else 1
    day = ((streak - 1) % 7) + 1
    coins = rewards[day - 1]
    longest = max(db.get_field(user, "longest_streak", 0), streak)

    db.update_user(user_id, {
        "current_streak": streak, "longest_streak": longest, "last_active_date": today,
    })
    updated, _, _ = credit_earning(user_id, coins, "streak_bonus", f"Day {day} streak bonus")

    return {
        "ok": True, "already_checked_in": False, "streak_day": day,
        "current_streak": streak, "longest_streak": longest,
        "coins_earned": coins, "new_balance": updated["coins"],
        "next_reward": rewards[(day % 7)], "is_jackpot": day == 7,
    }


def streak_status(user: dict) -> dict:
    today, _ = _today_yesterday()
    rewards = _streak_rewards()
    return {
        "current_streak": db.get_field(user, "current_streak", 0),
        "longest_streak": db.get_field(user, "longest_streak", 0),
        "today_checked_in": db.get_field(user, "last_active_date", "") == today,
        "ad_watched_today": db.get_field(user, "last_ad_date", "") == today,
        "upcoming_rewards": [{"day": i + 1, "coins": rewards[i]} for i in range(7)],
    }


# --------------------------------------------------------------------------- #
# Referral milestones
# --------------------------------------------------------------------------- #
def check_and_award_milestones(user_id: str) -> dict:
    user = db.get_user_by_id(user_id)
    if not user:
        return {"new_milestones": [], "total_referrals": 0, "next_milestone": None, "referrals_needed": 0}

    total = len(db.get_referrals_by_referrer(user_id))
    claimed = list(db.get_field(user, "milestones_claimed", []))
    badges = list(db.get_field(user, "referral_badges", []))
    new = []

    for friends, coins, badge in Config.MILESTONES:
        if total >= friends and friends not in claimed:
            claimed.append(friends)
            badges.append(badge)
            db.add_milestone_record(user_id, friends, badge, coins)
            credit_earning(user_id, coins, "milestone_bonus", f"{badge} badge ({friends} referrals)")
            new.append({"milestone": friends, "badge": badge, "coins": coins})

    db.update_user(user_id, {
        "total_referrals": total, "milestones_claimed": claimed, "referral_badges": badges,
    })

    nxt = next(((f, c, b) for f, c, b in Config.MILESTONES if total < f), None)
    return {
        "new_milestones": new, "total_referrals": total,
        "next_milestone": nxt[0] if nxt else None,
        "next_badge": nxt[2] if nxt else None,
        "next_reward": nxt[1] if nxt else None,
        "referrals_needed": (nxt[0] - total) if nxt else 0,
    }


def milestones_overview(user: dict) -> dict:
    total = db.get_field(user, "total_referrals", len(db.get_referrals_by_referrer(user["id"])))
    claimed_nums = set(db.get_field(user, "milestones_claimed", []))
    claimed, upcoming = [], []
    for friends, coins, badge in Config.MILESTONES:
        row = {"milestone": friends, "badge": badge, "coins": coins,
               "unlocked": friends in claimed_nums or total >= friends}
        (claimed if (friends in claimed_nums or total >= friends) else upcoming).append(row)
    nxt = next(((f, c, b) for f, c, b in Config.MILESTONES if total < f), None)
    return {
        "claimed": claimed, "upcoming": upcoming, "total_referrals": total,
        "next_milestone_at": nxt[0] if nxt else None,
        "referrals_needed": (nxt[0] - total) if nxt else 0,
        "badges": db.get_field(user, "referral_badges", []),
    }


# --------------------------------------------------------------------------- #
# Lucky spin
# --------------------------------------------------------------------------- #
def spin_prizes() -> list:
    return db.get_settings().get("spin_prizes", Config.DEFAULT_SETTINGS["spin_prizes"])


def do_spin(user_id: str) -> dict:
    user = db.get_user_by_id(user_id)
    tickets = db.get_field(user, "spin_tickets", 0)
    if tickets <= 0:
        return {"ok": False, "error": "No spin tickets. Watch an ad to earn one."}

    prizes = spin_prizes()
    prize = random.choices(prizes, weights=[p["weight"] for p in prizes])[0]
    coins = int(prize["coins"])

    db.update_user(user_id, {
        "spin_tickets": tickets - 1,
        "total_spins": db.get_field(user, "total_spins", 0) + 1,
        "total_spin_earnings": db.get_field(user, "total_spin_earnings", 0) + coins,
    })

    balance = user["coins"]
    if coins > 0:
        updated, _, _ = credit_earning(user_id, coins, "spin_win", f"Lucky Spin: {prize['label']}")
        balance = updated["coins"]

    db.add_spin_record(user_id, coins, prize["label"])
    return {
        "ok": True, "prize_label": prize["label"], "coins_won": coins,
        "new_balance": balance, "tickets_remaining": tickets - 1,
        "is_jackpot": coins >= 250,
    }
