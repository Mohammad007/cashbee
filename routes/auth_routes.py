"""Authentication: direct phone login (OTP skipped), signup with referral, admin login."""
import re

from flask import Blueprint, request, jsonify

from config import Config
import database.db as db
from services import create_token

auth_bp = Blueprint("auth", __name__)

PHONE_RE = re.compile(r"^\+91\d{10}$")


def _public_user(user: dict) -> dict:
    """Strip nothing sensitive (no passwords stored) but normalize output."""
    return user


@auth_bp.post("/send-otp")
def send_otp_route():
    """Kept for backward compatibility with existing mobile app builds.
    OTP is no longer sent — just validates the phone and returns success."""
    data = request.get_json(silent=True) or {}
    phone = (data.get("phone") or "").strip()
    if not PHONE_RE.match(phone):
        return jsonify({"error": "Invalid phone. Use +91XXXXXXXXXX"}), 400

    # Block banned users.
    existing = db.get_user_by_phone(phone)
    if existing and existing.get("is_banned"):
        return jsonify(
            {
                "error": "Your account has been banned. Please contact support.",
                "banned": True,
            }
        ), 403

    return jsonify(
        {"message": "OTP sent", "expires_in": 300}
    )


@auth_bp.post("/verify-otp")
def verify_otp_route():
    """OTP verification is skipped — any code is accepted.
    This directly logs in or registers the user."""
    data = request.get_json(silent=True) or {}
    phone = (data.get("phone") or "").strip()
    referral_code = (data.get("referral_code") or "").strip().upper() or None

    if not PHONE_RE.match(phone):
        return jsonify({"error": "Invalid phone"}), 400

    # OTP check skipped — accept any code.

    user = db.get_user_by_phone(phone)

    # Block banned users right at login with a clear message.
    if user and user.get("is_banned"):
        return jsonify(
            {
                "error": "Your account has been banned. Please contact support.",
                "banned": True,
            }
        ), 403

    is_new_user = user is None

    if is_new_user:
        referrer = None
        if referral_code:
            referrer = db.get_user_by_code(referral_code)
            if not referrer:
                return jsonify({"error": "Invalid referral code"}), 400

        user = db.create_user(phone, referred_by=referral_code if referrer else None)

        # Award signup bonus to referrer immediately.
        if referrer:
            settings = db.get_settings()
            bonus = settings["referral_signup_bonus"]
            db.add_coins(referrer["id"], bonus)
            db.add_transaction(
                referrer["id"],
                "referral_earn",
                bonus,
                f"Signup bonus for referring {phone}",
            )
            db.add_referral(referrer["id"], user["id"], coins_earned=bonus)

    token = create_token(user["id"])
    return jsonify(
        {
            "token": token,
            "user": _public_user(db.get_user_by_id(user["id"])),
            "is_new_user": is_new_user,
        }
    )


@auth_bp.post("/admin/login")
def admin_login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    if email == Config.ADMIN_EMAIL.lower() and password == Config.ADMIN_PASSWORD:
        token = create_token("admin", is_admin=True)
        return jsonify({"token": token, "email": Config.ADMIN_EMAIL})
    return jsonify({"error": "Invalid credentials"}), 401
