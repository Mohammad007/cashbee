"""
TinyDB data layer for CashBee.

TinyDB stores each "table" inside a single JSON file. We keep one TinyDB
instance per logical table for clean separation and to match the documented
folder structure (users.json, transactions.json, ...).

NOTE ON SCALE:
    TinyDB is perfect for an MVP (comfortably up to ~10k users). It loads the
    whole file into memory and is not concurrency-safe across processes, so
    when you outgrow it migrate to PostgreSQL:
      - users / transactions / withdrawals -> normalized SQL tables
      - swap the helper functions below for SQLAlchemy queries
      - the route layer never touches TinyDB directly, only these helpers,
        so the migration surface is small.
"""
import os
import threading
import uuid
from datetime import datetime, timezone

from tinydb import TinyDB, Query

from config import DB_DIR

os.makedirs(DB_DIR, exist_ok=True)

# A single process-wide lock. TinyDB is not thread-safe and Flask's dev server
# is multi-threaded, so every write goes through this lock.
_lock = threading.RLock()


def _open(name: str) -> TinyDB:
    path = os.path.join(DB_DIR, name)
    # Plain JSONStorage (no CachingMiddleware): every write is flushed to disk
    # immediately. CachingMiddleware buffers writes in memory and only persists
    # on db.close(), which the long-running dev server never calls — so new
    # signups would be lost on the next reload and never reach the JSON files
    # or the admin panel. Immediate writes are perfectly fine at MVP scale.
    return TinyDB(path)


users_db = _open("users.json")
transactions_db = _open("transactions.json")
ads_db = _open("ads.json")
referrals_db = _open("referrals.json")
withdrawals_db = _open("withdrawals.json")
settings_db = _open("settings.json")
app_build_db = _open("app_build.json")

Q = Query()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def new_id() -> str:
    return str(uuid.uuid4())


def gen_referral_code() -> str:
    """Generate a unique CASHxxxx referral code."""
    import random
    import string

    while True:
        code = "CASH" + "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
        with _lock:
            if not users_db.contains(Q.referral_code == code):
                return code


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
def get_settings() -> dict:
    from config import Config

    with _lock:
        rows = settings_db.all()
        if not rows:
            settings_db.insert(dict(Config.DEFAULT_SETTINGS))
            return dict(Config.DEFAULT_SETTINGS)
        # merge defaults so newly added keys always exist
        merged = dict(Config.DEFAULT_SETTINGS)
        merged.update(rows[0])
        return merged


def update_settings(patch: dict) -> dict:
    current = get_settings()
    allowed = set(current.keys())
    clean = {k: v for k, v in patch.items() if k in allowed}
    current.update(clean)
    with _lock:
        settings_db.truncate()
        settings_db.insert(current)
    return current


# --------------------------------------------------------------------------- #
# App build (the latest APK the admin uploads for users to download)
# --------------------------------------------------------------------------- #
def get_app_build() -> dict | None:
    with _lock:
        rows = app_build_db.all()
        return rows[0] if rows else None


def set_app_build(meta: dict) -> dict:
    with _lock:
        app_build_db.truncate()
        app_build_db.insert(meta)
    return meta


# --------------------------------------------------------------------------- #
# Users
# --------------------------------------------------------------------------- #
def get_user_by_phone(phone: str):
    with _lock:
        return users_db.get(Q.phone == phone)


def get_user_by_id(user_id: str):
    with _lock:
        return users_db.get(Q.id == user_id)


def get_user_by_code(code: str):
    with _lock:
        return users_db.get(Q.referral_code == code)


def create_user(phone: str, referred_by: str | None = None) -> dict:
    user = {
        "id": new_id(),
        "name": "",
        "phone": phone,
        "referral_code": gen_referral_code(),
        "referred_by": referred_by,
        "coins": 0,
        "total_earned": 0,
        "total_withdrawn": 0,
        "ads_watched_today": 0,
        "last_ad_date": "",
        "last_ad_time": "",
        "is_banned": False,
        "photo_url": "",
        "created_at": now_iso(),
    }
    with _lock:
        users_db.insert(user)
    return user


def update_user(user_id: str, patch: dict) -> dict | None:
    with _lock:
        users_db.update(patch, Q.id == user_id)
        return users_db.get(Q.id == user_id)


def add_coins(user_id: str, coins: int, count_as_earning: bool = True) -> dict | None:
    """Atomically add (or subtract) coins for a user."""
    with _lock:
        user = users_db.get(Q.id == user_id)
        if not user:
            return None
        patch = {"coins": user["coins"] + coins}
        if count_as_earning and coins > 0:
            patch["total_earned"] = user["total_earned"] + coins
        users_db.update(patch, Q.id == user_id)
        return users_db.get(Q.id == user_id)


def all_users() -> list:
    with _lock:
        return users_db.all()


# --------------------------------------------------------------------------- #
# Transactions
# --------------------------------------------------------------------------- #
def add_transaction(user_id: str, type_: str, coins: int, description: str) -> dict:
    txn = {
        "id": new_id(),
        "user_id": user_id,
        "type": type_,
        "coins": coins,
        "description": description,
        "created_at": now_iso(),
    }
    with _lock:
        transactions_db.insert(txn)
    return txn


def get_transactions(user_id: str, type_: str | None = None, limit: int = 100) -> list:
    with _lock:
        if type_:
            rows = transactions_db.search((Q.user_id == user_id) & (Q.type == type_))
        else:
            rows = transactions_db.search(Q.user_id == user_id)
    rows.sort(key=lambda r: r["created_at"], reverse=True)
    return rows[:limit]


# --------------------------------------------------------------------------- #
# Ads
# --------------------------------------------------------------------------- #
def get_active_ads() -> list:
    with _lock:
        return ads_db.search(Q.is_active == True)  # noqa: E712


def get_ad(ad_id: str):
    with _lock:
        return ads_db.get(Q.id == ad_id)


def increment_ad_views(ad_id: str):
    with _lock:
        ad = ads_db.get(Q.id == ad_id)
        if ad:
            ads_db.update({"total_views": ad.get("total_views", 0) + 1}, Q.id == ad_id)


def create_ad(title: str, coins_reward: int, daily_limit: int) -> dict:
    ad = {
        "id": new_id(),
        "title": title,
        "coins_reward": coins_reward,
        "daily_limit": daily_limit,
        "is_active": True,
        "total_views": 0,
    }
    with _lock:
        ads_db.insert(ad)
    return ad


def update_ad(ad_id: str, patch: dict):
    with _lock:
        ads_db.update(patch, Q.id == ad_id)
        return ads_db.get(Q.id == ad_id)


def delete_ad(ad_id: str):
    with _lock:
        ads_db.remove(Q.id == ad_id)


def all_ads() -> list:
    with _lock:
        return ads_db.all()


def seed_default_ad():
    """Ensure at least one ad slot exists so the app is usable immediately."""
    with _lock:
        if not ads_db.all():
            ads_db.insert(
                {
                    "id": new_id(),
                    "title": "Rewarded Video",
                    "coins_reward": 10,
                    "daily_limit": 10,
                    "is_active": True,
                    "total_views": 0,
                }
            )


# --------------------------------------------------------------------------- #
# Referrals
# --------------------------------------------------------------------------- #
def add_referral(referrer_id: str, referee_id: str, coins_earned: int = 0) -> dict:
    rec = {
        "id": new_id(),
        "referrer_id": referrer_id,
        "referee_id": referee_id,
        "coins_earned": coins_earned,
        "created_at": now_iso(),
    }
    with _lock:
        referrals_db.insert(rec)
    return rec


def get_referrals_by_referrer(referrer_id: str) -> list:
    with _lock:
        return referrals_db.search(Q.referrer_id == referrer_id)


def add_referral_earning(referrer_id: str, referee_id: str, coins: int):
    """Increase the lifetime coins_earned counter on a referral edge."""
    with _lock:
        rec = referrals_db.get(
            (Q.referrer_id == referrer_id) & (Q.referee_id == referee_id)
        )
        if rec:
            referrals_db.update(
                {"coins_earned": rec.get("coins_earned", 0) + coins},
                (Q.referrer_id == referrer_id) & (Q.referee_id == referee_id),
            )


# --------------------------------------------------------------------------- #
# Withdrawals
# --------------------------------------------------------------------------- #
def create_withdrawal(user_id: str, coins: int, amount_inr: float, upi_id: str) -> dict:
    w = {
        "id": new_id(),
        "user_id": user_id,
        "coins": coins,
        "amount_inr": amount_inr,
        "upi_id": upi_id,
        "status": "pending",
        "admin_note": "",
        "created_at": now_iso(),
        "paid_at": None,
    }
    with _lock:
        withdrawals_db.insert(w)
    return w


def get_withdrawals(user_id: str | None = None, status: str | None = None) -> list:
    with _lock:
        if user_id and status:
            rows = withdrawals_db.search((Q.user_id == user_id) & (Q.status == status))
        elif user_id:
            rows = withdrawals_db.search(Q.user_id == user_id)
        elif status:
            rows = withdrawals_db.search(Q.status == status)
        else:
            rows = withdrawals_db.all()
    rows.sort(key=lambda r: r["created_at"], reverse=True)
    return rows


def get_withdrawal(wid: str):
    with _lock:
        return withdrawals_db.get(Q.id == wid)


def update_withdrawal(wid: str, patch: dict):
    with _lock:
        withdrawals_db.update(patch, Q.id == wid)
        return withdrawals_db.get(Q.id == wid)


def has_pending_withdrawal(user_id: str) -> bool:
    with _lock:
        return withdrawals_db.contains(
            (Q.user_id == user_id) & (Q.status == "pending")
        )
