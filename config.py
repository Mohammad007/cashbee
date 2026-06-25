"""
CashBee backend configuration.

All secrets are read from environment variables (see .env.example).
Sensible defaults are provided so the app boots out of the box for local dev.
"""
import os
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Where the TinyDB JSON files live. Override with the DB_DIR env var to point at
# a persistent disk — on Railway, mount a Volume (e.g. at /data) and set
# DB_DIR=/data, otherwise the container filesystem is EPHEMERAL and all users /
# coins / withdrawals are wiped on every redeploy or restart.
DB_DIR = os.getenv("DB_DIR", os.path.join(BASE_DIR, "database"))


class Config:
    # --- Core ---
    SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production")
    JWT_SECRET = os.getenv("JWT_SECRET", "change-me-jwt-secret")
    JWT_EXPIRY = timedelta(days=int(os.getenv("JWT_EXPIRY_DAYS", "30")))
    ADMIN_JWT_EXPIRY = timedelta(hours=12)

    # --- Admin login ---
    ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@cashbee.app")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

    # --- WhatsApp OTP provider ---
    # OTP is generated, delivered (over WhatsApp) and verified by this external
    # service. We only forward the user's phone number to it; CashBee then issues
    # its own JWT once the code is verified.
    WHATSAPP_API_URL = os.getenv(
        "WHATSAPP_API_URL", "https://whatsapp-api-production-807b.up.railway.app"
    )
    WHATSAPP_OTP_SESSION_ID = os.getenv(
        "WHATSAPP_OTP_SESSION_ID", "a12f37fe-aed1-4ef7-994f-3d21673b08df"
    )
    WHATSAPP_OTP_TEMPLATE_ID = os.getenv(
        "WHATSAPP_OTP_TEMPLATE_ID", "cd365b8d-802a-4e4d-b8dc-97a8286cad15"
    )
    WHATSAPP_OTP_TEMPLATE_NAME = os.getenv(
        "WHATSAPP_OTP_TEMPLATE_NAME", "OTP Verification"
    )
    # In dev mode no WhatsApp message is sent; a fixed OTP is accepted instead.
    # Set OTP_DEV_MODE=false to use real WhatsApp OTP.
    OTP_DEV_MODE = os.getenv("OTP_DEV_MODE", "false").lower() == "true"
    OTP_DEV_CODE = os.getenv("OTP_DEV_CODE", "123456")

    # --- Razorpay payouts ---
    RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
    RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")
    RAZORPAY_ACCOUNT_NUMBER = os.getenv("RAZORPAY_ACCOUNT_NUMBER", "")
    PAYOUT_DEV_MODE = os.getenv("PAYOUT_DEV_MODE", "true").lower() == "true"

    # --- App economy defaults (overridable via admin settings) ---
    DEFAULT_SETTINGS = {
        "coin_rate": 10,            # coins per ₹1
        "daily_ad_limit": 10,       # ads per user per day
        "ad_cooldown_seconds": 30,  # seconds between ads
        "min_withdrawal": 500,      # minimum coins to withdraw
        "referral_signup_bonus": 50,
        "referral_bonus_percent": 5,  # % of referee ad earnings to referrer
        "maintenance_mode": False,
    }

    # --- CORS ---
    CORS_ORIGINS = os.getenv(
        "CORS_ORIGINS",
        "http://localhost:3000,http://localhost:3001,https://web-production-962f5.up.railway.app",
    ).split(",")

    # --- Rate limiting ---
    RATELIMIT_STORAGE_URI = os.getenv("RATELIMIT_STORAGE_URI", "memory://")
