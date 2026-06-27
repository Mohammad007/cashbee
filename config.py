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

    # --- Google Play Billing (in-app purchases for membership / boosts) ---
    # Purchases are verified server-side via the Google Play Developer API.
    # Set GOOGLE_PLAY_PACKAGE_NAME + GOOGLE_SERVICE_ACCOUNT_FILE (path to the
    # service-account JSON downloaded from Google Cloud, with Play access).
    GOOGLE_PLAY_PACKAGE_NAME = os.getenv("GOOGLE_PLAY_PACKAGE_NAME", "com.cashbee.cashbee")
    GOOGLE_SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "")

    # --- App economy defaults (overridable via admin settings) ---
    DEFAULT_SETTINGS = {
        "coin_rate": 10,            # coins per ₹1
        "daily_ad_limit": 10,       # ads per user per day
        "ad_cooldown_seconds": 30,  # seconds between ads
        "min_withdrawal": 500,      # minimum coins to withdraw
        "referral_signup_bonus": 50,
        "referral_bonus_percent": 5,  # % of referee ad earnings to referrer
        "maintenance_mode": False,
        # --- Website "stats" section (editable from Admin → Settings) ---
        # Displayed value = real activity from the DB + these baseline figures.
        # Set all to 0 to show only the true numbers.
        "stats_baseline_users": 120000,
        "stats_baseline_paid_inr": 4800000,
        "stats_baseline_ads": 9200000,
        # --- WhatsApp OTP provider (editable from Admin → Settings) ---
        # Defaults seed from env on first boot; after that the admin panel owns them.
        "whatsapp_api_url": WHATSAPP_API_URL,
        "whatsapp_session_id": WHATSAPP_OTP_SESSION_ID,
        "whatsapp_template_id": WHATSAPP_OTP_TEMPLATE_ID,
        "whatsapp_template_name": WHATSAPP_OTP_TEMPLATE_NAME,
        # --- AdMob ad unit IDs (served to the app at runtime; change these from
        # the admin panel and the app picks them up on next launch — NO rebuild).
        # NOTE: the AdMob *App ID* (ca-app-pub-...~...) lives in the Android
        # manifest and still needs a rebuild; only the unit IDs below are remote.
        "ads_enabled": True,
        "use_test_ads": False,  # True => app uses Google's official test units
        "admob_rewarded_id": "ca-app-pub-6622501207630771/8376042409",
        # --- Razorpay payouts (managed entirely from the admin panel) ---
        # Keep both test and live credentials; `razorpay_mode` selects which set
        # is used for real payouts. Secrets are write-only in the admin UI.
        "razorpay_mode": "test",  # "test" | "live"
        "razorpay_test_key_id": "",
        "razorpay_test_key_secret": "",
        "razorpay_test_account_number": "",
        "razorpay_live_key_id": "",
        "razorpay_live_key_secret": "",
        "razorpay_live_account_number": "",
        # Membership plan overrides (admin-editable; empty => Config defaults).
        "membership_plans": {},
        # Master switch for paid features (membership + boosts). When False the
        # app hides the store and the purchase endpoints are disabled — useful to
        # ship a Play-compliant build with no in-app purchases.
        "membership_enabled": True,
        # --- Gamification (all editable from Admin → Gamification) ---
        # Daily streak rewards: coins for day 1..7 (cycle restarts after day 7).
        "streak_rewards": [10, 15, 20, 25, 30, 40, 100],
        # First-withdrawal bonus (coins) credited on the user's first withdraw.
        "first_withdrawal_bonus": 250,
        # Lucky-spin prize table (weighted random; weights need not sum to 100).
        "spin_prizes": [
            {"label": "Try Again", "coins": 0, "weight": 40},
            {"label": "Small Win", "coins": 5, "weight": 25},
            {"label": "Medium Win", "coins": 15, "weight": 15},
            {"label": "Good Win", "coins": 30, "weight": 10},
            {"label": "Great Win", "coins": 75, "weight": 7},
            {"label": "Jackpot!", "coins": 250, "weight": 2},
            {"label": "MEGA JACKPOT", "coins": 500, "weight": 1},
        ],
    }

    # Level tiers: (min_lifetime_coins, level, name, multiplier). Highest first.
    LEVELS = [
        (40000, 5, "Diamond", 2.0),
        (15000, 4, "Platinum", 1.75),
        (5000, 3, "Gold", 1.5),
        (1000, 2, "Silver", 1.25),
        (0, 1, "Bronze", 1.0),
    ]

    # Referral milestones: friends -> (coins, badge).
    MILESTONES = [
        (5, 500, "Connector"),
        (10, 1500, "Influencer"),
        (25, 5000, "Ambassador"),
        (50, 15000, "Legend"),
        (100, 50000, "CashBee Champion"),
    ]

    # --- Premium membership plans (paid, Razorpay) ---
    MEMBERSHIP_PLANS = {
        "pro_monthly": {
            "name": "Pro", "price_inr": 99, "duration_days": 30,
            "coin_multiplier": 2.0, "daily_ad_limit": 20, "daily_spins": 5,
            "instant_withdrawal": False, "color": "#C0C0C0", "emoji": "🥈",
        },
        "elite_monthly": {
            "name": "Elite", "price_inr": 249, "duration_days": 30,
            "coin_multiplier": 3.0, "daily_ad_limit": 999, "daily_spins": 10,
            "instant_withdrawal": True, "color": "#FFD700", "emoji": "👑",
        },
    }

    # --- Boost packs (paid, time-limited coin multiplier) ---
    BOOST_PACKS = {
        "gold_boost": {
            "name": "Gold Boost", "price_inr": 39, "duration_hours": 24,
            "coin_multiplier": 2.0, "extra_spins": 0, "emoji": "⚡",
            "color": "#FFD700", "tag": None,
        },
        "seven_day_power": {
            "name": "7-Day Power", "price_inr": 149, "duration_hours": 168,
            "coin_multiplier": 2.0, "extra_spins": 0, "emoji": "🚀",
            "color": "#FF6B00", "tag": "BEST VALUE",
        },
    }

    # Hard cap on coins from a single ad watch (after all multipliers).
    MAX_COINS_PER_AD = 500

    # --- CORS ---
    CORS_ORIGINS = os.getenv(
        "CORS_ORIGINS",
        "http://localhost:3000,http://localhost:3001,https://cashbee.up.railway.app",
    ).split(",")

    # --- Rate limiting ---
    RATELIMIT_STORAGE_URI = os.getenv("RATELIMIT_STORAGE_URI", "memory://")
