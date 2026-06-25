"""
External integrations: JWT, WhatsApp OTP, Razorpay payouts.

Each integration degrades gracefully into a "dev mode" so the whole stack
runs locally with zero third-party credentials.
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
# OTP — delivered & verified over WhatsApp by an external provider.
#
# The provider exposes:
#   POST /api/auth/otp/request  {phone, sessionId, templateId, templateName}
#   POST /api/auth/otp/verify   {phone, code}
# It generates the code, sends it to the user's WhatsApp, and verifies it.
# CashBee only forwards the phone number and issues its own JWT on success.
#
# `phone` arrives as "+91XXXXXXXXXX"; the provider expects "91XXXXXXXXXX".
# A dev-mode shortcut (OTP_DEV_MODE=true) skips WhatsApp entirely and accepts
# OTP_DEV_CODE, so the stack runs offline.
# --------------------------------------------------------------------------- #
_otp_store: dict[str, float] = {}  # phone -> expiry (dev mode only)
OTP_TTL = 300  # seconds


def _wa_url(path: str) -> str:
    return f"{Config.WHATSAPP_API_URL.rstrip('/')}{path}"


def _wa_headers() -> dict:
    # ngrok-skip header is harmless on non-ngrok hosts and kept for parity.
    return {"Content-Type": "application/json", "ngrok-skip-browser-warning": "true"}


def _wa_error(data: dict) -> str:
    msg = data.get("message")
    if isinstance(msg, str):
        return msg
    if isinstance(msg, list) and msg:
        return "; ".join(str(m) for m in msg)
    return "OTP request failed"


def send_otp(phone: str) -> dict:
    """Request an OTP. Dev mode skips WhatsApp; otherwise delegates delivery."""
    if Config.OTP_DEV_MODE:
        _otp_store[phone] = time.time() + OTP_TTL
        return {"sent": True, "dev_mode": True, "otp": Config.OTP_DEV_CODE,
                "expires_in": OTP_TTL}

    wa_phone = phone.lstrip("+")
    try:
        resp = requests.post(
            _wa_url("/api/auth/otp/request"),
            headers=_wa_headers(),
            json={
                "phone": wa_phone,
                "sessionId": Config.WHATSAPP_OTP_SESSION_ID,
                "templateId": Config.WHATSAPP_OTP_TEMPLATE_ID,
                "templateName": Config.WHATSAPP_OTP_TEMPLATE_NAME,
            },
            timeout=20,
        )
    except requests.RequestException as exc:
        return {"sent": False, "error": f"Cannot reach OTP service: {exc}"}

    data = {}
    try:
        data = resp.json()
    except ValueError:
        pass

    if resp.status_code // 100 == 2:
        return {
            "sent": True,
            "dev_mode": False,
            "expires_in": int(data.get("expiresInSeconds") or OTP_TTL),
        }
    return {"sent": False, "error": _wa_error(data)}


def verify_otp(phone: str, otp: str) -> bool:
    """Verify the OTP. Dev mode checks the fixed code; else asks the provider."""
    if Config.OTP_DEV_MODE:
        if otp != Config.OTP_DEV_CODE:
            return False
        expiry = _otp_store.get(phone)
        # Accept the dev code whether or not a send was recorded (offline-friendly).
        if expiry is not None and time.time() > expiry:
            _otp_store.pop(phone, None)
            return False
        _otp_store.pop(phone, None)
        return True

    wa_phone = phone.lstrip("+")
    try:
        resp = requests.post(
            _wa_url("/api/auth/otp/verify"),
            headers=_wa_headers(),
            json={"phone": wa_phone, "code": otp},
            timeout=20,
        )
    except requests.RequestException:
        return False
    return resp.status_code // 100 == 2


# --------------------------------------------------------------------------- #
# Razorpay payout
# --------------------------------------------------------------------------- #
def create_payout(upi_id: str, amount_inr: float, reference: str) -> dict:
    """Trigger a Razorpay UPI payout. Returns a result dict."""
    if Config.PAYOUT_DEV_MODE or not Config.RAZORPAY_KEY_ID:
        return {"success": True, "dev_mode": True, "payout_id": f"dev_payout_{reference}"}

    try:
        # Razorpay payouts use a contact + fund_account + payout flow.
        auth = (Config.RAZORPAY_KEY_ID, Config.RAZORPAY_KEY_SECRET)
        payload = {
            "account_number": Config.RAZORPAY_ACCOUNT_NUMBER,
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
        data = resp.json()
        if resp.status_code in (200, 201):
            return {"success": True, "payout_id": data.get("id"), "raw": data}
        return {"success": False, "error": data}
    except requests.RequestException as exc:
        return {"success": False, "error": str(exc)}

# NOTE: User notifications (e.g. withdrawal paid/rejected) will be added later
# via a WhatsApp notification API. FCM push was removed.
