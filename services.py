"""
External integrations: JWT, Razorpay payouts.

Production configuration:
  - Razorpay payout credentials (test + live) are managed from the admin panel
    and read from the database at call time.
"""
import functools
import time

import jwt
import requests
from flask import request, jsonify, g

from config import Config
import database.db as db


# --------------------------------------------------------------------------- #
# JWT
# --------------------------------------------------------------------------- #
def create_token(user_id: str, is_admin: bool = False) -> str:
    expiry = Config.ADMIN_JWT_EXPIRY if is_admin else Config.JWT_EXPIRY
    payload = {
        "sub": user_id,
        "admin": is_admin,
        "exp": int(time.time()) + int(expiry.total_seconds()),
        "iat": int(time.time()),
    }
    return jwt.encode(payload, Config.JWT_SECRET, algorithm="HS256")


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, Config.JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


# --------------------------------------------------------------------------- #
# Custom-ad watch token
#
# For custom (admin-uploaded) ads we cannot rely on an AdMob reward callback, so
# the server issues a signed "watch token" when playback starts. On completion
# we decode it and require that at least `watch_seconds` have elapsed — making
# the "must watch the full ad" rule tamper-proof from the client.
# --------------------------------------------------------------------------- #
def create_watch_token(user_id: str, ad_id: str) -> str:
    now = int(time.time())
    payload = {
        "k": "watch",
        "sub": user_id,
        "ad": ad_id,
        "iat": now,
        "exp": now + 1800,  # session valid for 30 min
    }
    return jwt.encode(payload, Config.JWT_SECRET, algorithm="HS256")


def watch_token_elapsed(token: str, user_id: str, ad_id: str) -> int | None:
    """Seconds elapsed since the token was issued, or None if it's not a valid
    watch token for this exact user + ad."""
    try:
        p = jwt.decode(token, Config.JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    if p.get("k") != "watch" or p.get("sub") != user_id or p.get("ad") != ad_id:
        return None
    return int(time.time()) - int(p.get("iat", 0))


def _extract_token() -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return None


def auth_required(fn):
    """Require a valid user JWT. Populates g.user."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        token = _extract_token()
        payload = decode_token(token) if token else None
        if not payload or payload.get("admin"):
            return jsonify({"error": "Unauthorized"}), 401
        user = db.get_user_by_id(payload["sub"])
        if not user:
            return jsonify({"error": "User not found"}), 401
        if user.get("is_banned"):
            return jsonify({"error": "Account banned"}), 403
        g.user = user
        return fn(*args, **kwargs)

    return wrapper


def admin_required(fn):
    """Require a valid admin JWT."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        token = _extract_token()
        payload = decode_token(token) if token else None
        if not payload or not payload.get("admin"):
            return jsonify({"error": "Admin authorization required"}), 401
        g.admin_id = payload["sub"]
        return fn(*args, **kwargs)

    return wrapper






# --------------------------------------------------------------------------- #
# Razorpay payout — credentials come from the admin panel (DB settings).
# --------------------------------------------------------------------------- #
def _razorpay_creds() -> dict:
    """Active Razorpay credentials based on the admin-selected mode (test/live)."""
    s = db.get_settings()
    mode = (s.get("razorpay_mode") or "test").lower()
    prefix = "razorpay_live_" if mode == "live" else "razorpay_test_"
    return {
        "mode": mode,
        "key_id": (s.get(prefix + "key_id") or "").strip(),
        "key_secret": (s.get(prefix + "key_secret") or "").strip(),
        "account_number": (s.get(prefix + "account_number") or "").strip(),
    }


def create_payout(upi_id: str, amount_inr: float, reference: str) -> dict:
    """Trigger a Razorpay UPI payout using the admin-configured credentials."""
    creds = _razorpay_creds()
    if not (creds["key_id"] and creds["key_secret"] and creds["account_number"]):
        return {
            "success": False,
            "error": (
                f"Razorpay {creds['mode']} credentials are not configured. "
                "Add them in Admin → Settings → Payments."
            ),
        }

    try:
        # Razorpay payouts use a contact + fund_account + payout flow.
        auth = (creds["key_id"], creds["key_secret"])
        payload = {
            "account_number": creds["account_number"],
            "amount": int(amount_inr * 100),  # paise
            "currency": "INR",
            "mode": "UPI",
            "purpose": "payout",
            "fund_account": {
                "account_type": "vpa",
                "vpa": {"address": upi_id},
                "contact": {"name": "CashBee User", "type": "customer"},
            },
            "queue_if_low_balance": True,
            "reference_id": reference,
            "narration": "CashBee Withdrawal",
        }
        resp = requests.post(
            "https://api.razorpay.com/v1/payouts",
            json=payload,
            auth=auth,
            timeout=20,
        )
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code in (200, 201):
            return {"success": True, "payout_id": data.get("id"), "mode": creds["mode"], "raw": data}

        # A 404 on /v1/payouts means RazorpayX (Payouts) isn't enabled for these
        # keys — the single most common setup mistake. Give an actionable hint.
        if resp.status_code == 404:
            return {
                "success": False,
                "error": (
                    f"RazorpayX Payouts is not enabled for your {creds['mode']} keys. "
                    "Activate RazorpayX at x.razorpay.com and use its keys + account "
                    "number (regular Razorpay payment-gateway keys cannot do payouts)."
                ),
            }
        if resp.status_code == 401:
            return {
                "success": False,
                "error": f"Razorpay {creds['mode']} key/secret is invalid (401 unauthorized).",
            }
        return {"success": False, "error": data}
    except requests.RequestException as exc:
        return {"success": False, "error": str(exc)}

# NOTE: User notifications (e.g. withdrawal paid/rejected) will be added later
# via a WhatsApp notification API. FCM push was removed.
