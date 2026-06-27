"""
Paid features: premium membership + boost packs (Razorpay).

SECURITY: payments are ALWAYS verified server-side (Razorpay signature) before
any benefit is applied. The Flutter side is never trusted for confirmation.

Razorpay credentials come from the admin panel (DB settings, test/live), reused
from services._razorpay_creds — the same keys used for payouts.
"""
from datetime import datetime, timezone, timedelta

import database.db as db
from config import Config
from services import _razorpay_creds


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(iso: str | None):
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Razorpay client (built from admin-configured creds)
# --------------------------------------------------------------------------- #
def _client():
    creds = _razorpay_creds()
    if not (creds["key_id"] and creds["key_secret"]):
        return None, creds
    import razorpay

    return razorpay.Client(auth=(creds["key_id"], creds["key_secret"])), creds


def create_order(amount_inr: int, receipt: str) -> dict:
    """Create a Razorpay order. Returns {ok, order_id, amount, key_id} or error."""
    client, creds = _client()
    if client is None:
        return {"ok": False, "error": "Payments are not configured. Contact support."}
    try:
        order = client.order.create({
            "amount": int(amount_inr) * 100,   # paise
            "currency": "INR",
            "receipt": receipt,
            "payment_capture": 1,
        })
        return {
            "ok": True,
            "order_id": order["id"],
            "amount": order["amount"],
            "key_id": creds["key_id"],
        }
    except Exception as exc:  # razorpay raises various errors
        return {"ok": False, "error": f"Could not create order: {exc}"}


def verify_signature(order_id: str, payment_id: str, signature: str) -> bool:
    client, _ = _client()
    if client is None:
        return False
    try:
        client.utility.verify_payment_signature({
            "razorpay_order_id": order_id,
            "razorpay_payment_id": payment_id,
            "razorpay_signature": signature,
        })
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Membership
# --------------------------------------------------------------------------- #
def reset_membership(user_id: str):
    db.update_user(user_id, {
        "membership_plan": "free", "membership_expiry": None,
        "membership_coin_multiplier": 1.0, "membership_daily_ad_limit": 10,
        "membership_daily_spins": 0, "membership_instant_withdrawal": False,
    })


def membership_active(user: dict) -> bool:
    """True if the user has a live membership; auto-resets if expired."""
    exp = _parse(db.get_field(user, "membership_expiry", None))
    if db.get_field(user, "membership_plan", "free") == "free" or exp is None:
        return False
    if exp < _now():
        reset_membership(user["id"])
        return False
    return True


def apply_membership(user_id: str, plan_id: str) -> dict:
    plan = db.get_membership_plans()[plan_id]
    expiry = _now() + timedelta(days=plan["duration_days"])
    db.update_user(user_id, {
        "membership_plan": plan_id,
        "membership_expiry": expiry.isoformat(),
        "membership_coin_multiplier": plan["coin_multiplier"],
        "membership_daily_ad_limit": plan["daily_ad_limit"],
        "membership_daily_spins": plan["daily_spins"],
        "membership_instant_withdrawal": plan["instant_withdrawal"],
    })
    return {"expiry": expiry.isoformat(), **plan}


def membership_status(user: dict) -> dict:
    active = membership_active(user)
    if not active:
        return {
            "plan": "free", "plan_name": "Free", "is_active": False,
            "expiry": None, "days_remaining": 0, "coin_multiplier": 1.0,
            "daily_ad_limit": 10, "daily_spins": 0, "instant_withdrawal": False,
        }
    plan_id = user["membership_plan"]
    plan = db.get_membership_plans().get(plan_id, {})
    exp = _parse(user["membership_expiry"])
    days = max(0, (exp - _now()).days) if exp else 0
    return {
        "plan": plan_id, "plan_name": plan.get("name", plan_id),
        "is_active": True, "expiry": user["membership_expiry"],
        "days_remaining": days,
        "coin_multiplier": db.get_field(user, "membership_coin_multiplier", 1.0),
        "daily_ad_limit": db.get_field(user, "membership_daily_ad_limit", 10),
        "daily_spins": db.get_field(user, "membership_daily_spins", 0),
        "instant_withdrawal": db.get_field(user, "membership_instant_withdrawal", False),
    }


# --------------------------------------------------------------------------- #
# Boost
# --------------------------------------------------------------------------- #
def reset_boost(user_id: str):
    db.update_user(user_id, {
        "active_boost_multiplier": 1.0, "active_boost_expiry": None,
        "active_boost_name": "",
    })


def boost_active(user: dict) -> bool:
    exp = _parse(db.get_field(user, "active_boost_expiry", None))
    if exp is None:
        return False
    if exp < _now():
        reset_boost(user["id"])
        return False
    return True


def apply_boost(user_id: str, boost_id: str) -> dict:
    boost = Config.BOOST_PACKS[boost_id]
    user = db.get_user_by_id(user_id)
    existing = _parse(db.get_field(user, "active_boost_expiry", None))
    base = existing if (existing and existing > _now()) else _now()
    expiry = base + timedelta(hours=boost["duration_hours"])

    patch = {
        "active_boost_expiry": expiry.isoformat(),
        "active_boost_multiplier": boost["coin_multiplier"],
        "active_boost_name": boost["name"],
    }
    db.update_user(user_id, patch)
    if boost["extra_spins"] > 0:
        db.update_user(user_id, {
            "spin_tickets": db.get_field(user, "spin_tickets", 0) + boost["extra_spins"]
        })
    return {"expires_at": expiry.isoformat(), **boost}


def boost_status(user: dict) -> dict:
    if not boost_active(user):
        return {"is_active": False, "boost_name": "", "multiplier": 1.0,
                "expires_at": None, "hours_remaining": 0, "minutes_remaining": 0}
    exp = _parse(user["active_boost_expiry"])
    secs = max(0, int((exp - _now()).total_seconds()))
    return {
        "is_active": True,
        "boost_name": db.get_field(user, "active_boost_name", "Boost"),
        "multiplier": db.get_field(user, "active_boost_multiplier", 1.0),
        "expires_at": user["active_boost_expiry"],
        "hours_remaining": secs // 3600,
        "minutes_remaining": (secs % 3600) // 60,
    }


# --------------------------------------------------------------------------- #
# Final multiplier (used by ads/watch-complete)
# --------------------------------------------------------------------------- #
def calculate_final_coins(user: dict, base_coins: int) -> tuple[int, dict]:
    """
    effective_base = max(level_mult, membership_mult)
    final = effective_base × boost_mult × festival_mult   (capped at MAX_COINS_PER_AD)
    """
    level_mult = float(db.get_field(user, "coin_multiplier", 1.0))
    membership_mult = (
        float(db.get_field(user, "membership_coin_multiplier", 1.0))
        if membership_active(user) else 1.0
    )
    boost_mult = (
        float(db.get_field(user, "active_boost_multiplier", 1.0))
        if boost_active(user) else 1.0
    )
    fest = db.active_festival()
    festival_mult = float(fest["multiplier"]) if fest else 1.0

    effective_base = max(level_mult, membership_mult)
    final_mult = effective_base * boost_mult * festival_mult
    final_coins = min(int(base_coins * final_mult), Config.MAX_COINS_PER_AD)
    final_coins = max(1, final_coins)

    breakdown = {
        "base_coins": base_coins,
        "level_multiplier": level_mult,
        "membership_multiplier": membership_mult,
        "boost_multiplier": boost_mult,
        "festival_multiplier": festival_mult,
        "effective_multiplier": round(final_mult, 2),
        "coins_earned": final_coins,
    }
    return final_coins, breakdown


def effective_daily_ad_limit(user: dict, settings: dict) -> int:
    """Membership raises the daily ad limit; otherwise the global default."""
    if membership_active(user):
        return int(db.get_field(user, "membership_daily_ad_limit", settings["daily_ad_limit"]))
    return int(settings["daily_ad_limit"])
