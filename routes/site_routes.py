"""
Public marketing website (Flask + Jinja2).

Server-rendered landing page, "how it works", and the referral landing page
(`/ref/<code>`). Live stats (users, coins paid, ads watched) are pulled from the
same TinyDB JSON store the API uses, with sensible marketing fallbacks so the
page always looks populated even on a brand-new install.
"""
import os

from flask import (
    Blueprint,
    render_template,
    abort,
    send_from_directory,
    current_app,
    Response,
)

import database.db as db

site_bp = Blueprint("site", __name__)

# Legacy fallback: an APK manually dropped into backend/templates/site/.
APK_FILENAME = "CashBee_v0.1.apk"
# The build the admin uploads from the panel (backend/uploads/cashbee-latest.apk).
STORED_APK = "cashbee-latest.apk"

# AdMob publisher id (from flutter_app ca-app-pub-6622501207630771). Overridable
# via env so you never have to touch code if your AdMob account changes.
ADMOB_PUBLISHER_ID = os.getenv("ADMOB_PUBLISHER_ID", "pub-6622501207630771")


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
    """Serve the latest Android APK uploaded by the admin (or a legacy fallback)."""
    apk_mime = "application/vnd.android.package-archive"

    # 1) Latest build uploaded from the admin panel.
    upload_dir = os.path.join(current_app.root_path, "uploads")
    if os.path.exists(os.path.join(upload_dir, STORED_APK)):
        build = db.get_app_build()
        version = (build or {}).get("version", "")
        name = f"CashBee-{version}.apk" if version else "CashBee.apk"
        return send_from_directory(
            upload_dir,
            STORED_APK,
            as_attachment=True,
            download_name=name,
            mimetype=apk_mime,
        )

    # 2) Legacy fallback: an APK manually placed in templates/site/.
    site_dir = os.path.join(current_app.root_path, "templates", "site")
    if os.path.exists(os.path.join(site_dir, APK_FILENAME)):
        return send_from_directory(
            site_dir,
            APK_FILENAME,
            as_attachment=True,
            download_name="CashBee.apk",
            mimetype=apk_mime,
        )

    abort(404)


@site_bp.get("/app-ads.txt")
def app_ads_txt():
    """
    IAB app-ads.txt — authorizes Google AdMob to sell this app's ad inventory
    and blocks impostors (anti-fraud). AdMob crawls this at the developer
    website declared on the app's store listing. Must be served at the domain
    ROOT: https://<your-domain>/app-ads.txt
    """
    body = f"google.com, {ADMOB_PUBLISHER_ID}, DIRECT, f08c47fec0942fa0\n"
    return Response(body, mimetype="text/plain")


@site_bp.get("/how-it-works")
def how_it_works():
    return render_template("site/how_it_works.html")


# Contact email shown on legal pages (Play Store requires a working contact).
SUPPORT_EMAIL = "bilalmalik1561@gmail.com"


@site_bp.get("/privacy-policy")
def privacy_policy():
    return render_template(
        "site/privacy.html", support_email=SUPPORT_EMAIL, updated="June 27, 2026"
    )


@site_bp.get("/terms")
def terms():
    return render_template(
        "site/terms.html", support_email=SUPPORT_EMAIL, updated="June 27, 2026"
    )


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
