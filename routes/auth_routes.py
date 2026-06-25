"""Authentication: OTP send/verify, signup with referral, admin login."""
import re

from flask import Blueprint, request, jsonify

from config import Config
import database.db as db
from services import send_otp, verify_otp, create_token

auth_bp = Blueprint("auth", __name__)

PHONE_RE = re.compile(r"^\+91\d{10}$")


def _public_user(user: dict) -> dict:
    """Strip nothing sensitive (no passwords stored) but normalize output."""
    return user


@auth_bp.post("/send-otp")
def send_otp_route():
    data = request.get_json(silent=True) or {}
    phone = (data.get("phone") or "").strip()
    if not PHONE_RE.match(phone):
        return jsonify({"error": "Invalid phone. Use +91XXXXXXXXXX"}), 400
    result = send_otp(phone)
    if not result.get("sent"):
        return jsonify({"error": "Failed to send OTP", "detail": result}), 502
    resp = {"message": "OTP sent via WhatsApp", "expires_in": result.get("expires_in", 300)}
    if result.get("dev_mode"):
        resp["message"] = "OTP sent (dev mode)"
        resp["dev_otp"] = result.get("otp")  # convenience for local testing only
    return jsonify(resp)


@auth_bp.post("/verify-otp")
def verify_otp_route():
    data = request.get_json(silent=True) or {}
    phone = (data.get("phone") or "").strip()
    otp = (data.get("otp") or "").strip()
    referral_code = (data.get("referral_code") or "").strip().upper() or None

    if not PHONE_RE.match(phone):
        return jsonify({"error": "Invalid phone"}), 400
    if not verify_otp(phone, otp):
        return jsonify({"error": "Invalid or expired OTP"}), 401

    user = db.get_user_by_phone(phone)
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
