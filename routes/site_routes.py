"""
Public marketing website (Flask + Jinja2).

Server-rendered landing page, "how it works", and the referral landing page
(`/ref/<code>`). Live stats (users, coins paid, ads watched) are pulled from the
same TinyDB JSON store the API uses, with sensible marketing fallbacks so the
page always looks populated even on a brand-new install.
"""
import os

from flask import Blueprint, render_template, abort, send_from_directory, current_app

import database.db as db

site_bp = Blueprint("site", __name__)

# The release APK the user drops into backend/templates/site/.
APK_FILENAME = "CashBee_v0.1.apk"


def _live_stats() -> dict:
    """Real numbers from the DB, blended with a marketing baseline."""
    users = db.all_users()
    total_users = len(users)
    total_coins = sum(u.get("total_earned", 0) for u in users)

    settings = db.get_settings()
    total_inr = total_coins / max(settings.get("coin_rate", 10), 1)

    ads_watched = sum(
        1 for t in db.transactions_db.all() if t.get("type") == "ad_earn"
    )

    # Marketing baseline so a fresh install still reads well. Real activity
    # stacks on top of these floors.
    return {
        "users": _humanize(total_users + 120_000),
        "paid": "₹" + _humanize(total_inr + 4_800_000),
        "ads": _humanize(ads_watched + 9_200_000),
    }


def _humanize(n: float) -> str:
    n = int(n)
    if n >= 10_000_000:
        return f"{n / 10_000_000:.1f}Cr+"
    if n >= 100_000:
        return f"{n / 100_000:.1f}L+"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K+"
    return str(n)


@site_bp.get("/")
def index():
    return render_template("site/index.html", stats=_live_stats())


@site_bp.get("/download")
@site_bp.get("/download/cashbee.apk")
def download_apk():
    """Serve the Android APK as a file download."""
    site_dir = os.path.join(current_app.root_path, "templates", "site")
    if not os.path.exists(os.path.join(site_dir, APK_FILENAME)):
        abort(404)
    return send_from_directory(
        site_dir,
        APK_FILENAME,
        as_attachment=True,
        download_name="CashBee.apk",
        mimetype="application/vnd.android.package-archive",
    )


@site_bp.get("/how-it-works")
def how_it_works():
    return render_template("site/how_it_works.html")


@site_bp.get("/ref/<code>")
def referral_landing(code):
    user = db.get_user_by_code(code.upper())
    if not user:
        abort(404)
    edges = db.get_referrals_by_referrer(user["id"])
    referrer = {
        "code": user["referral_code"],
        "name": user.get("name") or "A CashBee user",
        "referral_count": len(edges),
        "total_earned": sum(e.get("coins_earned", 0) for e in edges),
    }
    return render_template("site/ref.html", referrer=referrer)
