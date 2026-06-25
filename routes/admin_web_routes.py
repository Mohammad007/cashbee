"""
Server-rendered admin panel (Flask + Jinja2).

This is a full HTML admin dashboard rendered by Flask itself — no separate
Next.js app. It uses a browser **session** cookie for auth (not the JWT used by
the mobile app / API), so an admin simply logs in with the credentials from
`.env` (ADMIN_EMAIL / ADMIN_PASSWORD) and gets a normal cookie session.

All data is read/written through `database.db` (the TinyDB JSON layer), so the
panel and the mobile API share exactly the same source of truth.
"""
import functools
import os
from datetime import datetime, timezone

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    current_app,
)
from werkzeug.utils import secure_filename

from config import Config
import database.db as db
from services import create_payout

# The single APK on disk that the public /download route always serves.
STORED_APK = "cashbee-latest.apk"

admin_web_bp = Blueprint("admin_web", __name__, url_prefix="/admin")


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
def login_required(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("admin_web.login", next=request.path))
        return fn(*args, **kwargs)

    return wrapper


@admin_web_bp.route("/login", methods=["GET", "POST"])
def login():
    if session.get("is_admin"):
        return redirect(url_for("admin_web.dashboard"))

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        if email == Config.ADMIN_EMAIL.lower() and password == Config.ADMIN_PASSWORD:
            session["is_admin"] = True
            session["admin_email"] = Config.ADMIN_EMAIL
            session.permanent = True
            nxt = request.args.get("next") or url_for("admin_web.dashboard")
            return redirect(nxt)
        flash("Invalid email or password.", "error")

    return render_template("admin/login.html")


@admin_web_bp.get("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("admin_web.login"))


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #
@admin_web_bp.get("/")
@login_required
def dashboard():
    users = db.all_users()
    today = db.today_str()
    settings = db.get_settings()

    total_coins_issued = sum(u.get("total_earned", 0) for u in users)
    pending = db.get_withdrawals(status="pending")

    today_ad_views = 0
    daily_signups: dict[str, int] = {}
    daily_ad_views: dict[str, int] = {}
    for txn in db.transactions_db.all():
        day = txn["created_at"][:10]
        if txn["type"] == "ad_earn":
            daily_ad_views[day] = daily_ad_views.get(day, 0) + 1
            if day == today:
                today_ad_views += 1
    for u in users:
        day = u["created_at"][:10]
        daily_signups[day] = daily_signups.get(day, 0) + 1

    withdrawal_volume: dict[str, float] = {}
    total_paid = 0.0
    for w in db.withdrawals_db.all():
        if w["status"] in ("approved", "paid"):
            day = w["created_at"][:10]
            withdrawal_volume[day] = withdrawal_volume.get(day, 0) + w["amount_inr"]
            total_paid += w["amount_inr"]

    def series(d):
        items = sorted(d.items())[-14:]  # last 14 days
        return {"labels": [k[5:] for k, _ in items], "values": [v for _, v in items]}

    # Recent withdrawals for the activity table.
    recent_withdrawals = db.get_withdrawals()[:8]
    for w in recent_withdrawals:
        u = db.get_user_by_id(w["user_id"])
        w["user_name"] = (u.get("name") if u else "") or "CashBee User"
        w["user_phone"] = u["phone"] if u else ""

    stats = {
        "total_users": len(users),
        "total_coins_issued": total_coins_issued,
        "total_inr_issued": round(total_coins_issued / settings["coin_rate"], 2),
        "pending_withdrawals": len(pending),
        "today_ad_views": today_ad_views,
        "total_paid": round(total_paid, 2),
        "active_ads": len(db.get_active_ads()),
        "banned_users": sum(1 for u in users if u.get("is_banned")),
    }

    charts = {
        "daily_signups": series(daily_signups),
        "daily_ad_views": series(daily_ad_views),
        "withdrawal_volume": series(withdrawal_volume),
    }

    return render_template(
        "admin/dashboard.html",
        stats=stats,
        charts=charts,
        recent_withdrawals=recent_withdrawals,
        active="dashboard",
    )


# --------------------------------------------------------------------------- #
# Users
# --------------------------------------------------------------------------- #
@admin_web_bp.get("/users")
@login_required
def users():
    page = int(request.args.get("page", 1))
    search = (request.args.get("search") or "").strip().lower()
    per_page = 20

    rows = db.all_users()
    if search:
        rows = [
            u
            for u in rows
            if search in (u.get("name") or "").lower()
            or search in u["phone"]
            or search in u["referral_code"].lower()
        ]
    rows.sort(key=lambda u: u["created_at"], reverse=True)

    total = len(rows)
    start = (page - 1) * per_page
    page_rows = rows[start : start + per_page]
    for u in page_rows:
        u["referral_count"] = len(db.get_referrals_by_referrer(u["id"]))

    pages = max(1, (total + per_page - 1) // per_page)
    return render_template(
        "admin/users.html",
        users=page_rows,
        total=total,
        page=page,
        pages=pages,
        search=request.args.get("search") or "",
        active="users",
    )


@admin_web_bp.post("/users/<user_id>/ban")
@login_required
def ban_user(user_id):
    banned = request.form.get("banned") == "1"
    user = db.update_user(user_id, {"is_banned": banned})
    if user:
        flash(
            f"User {'banned' if banned else 'unbanned'} successfully.",
            "success",
        )
    else:
        flash("User not found.", "error")
    return redirect(request.referrer or url_for("admin_web.users"))


# --------------------------------------------------------------------------- #
# Ads
# --------------------------------------------------------------------------- #
@admin_web_bp.get("/ads")
@login_required
def ads():
    return render_template("admin/ads.html", ads=db.all_ads(), active="ads")


@admin_web_bp.post("/ads/create")
@login_required
def create_ad():
    db.create_ad(
        title=(request.form.get("title") or "Rewarded Ad").strip(),
        coins_reward=int(request.form.get("coins_reward") or 10),
        daily_limit=int(request.form.get("daily_limit") or 10),
    )
    flash("Ad campaign created.", "success")
    return redirect(url_for("admin_web.ads"))


@admin_web_bp.post("/ads/<ad_id>/update")
@login_required
def update_ad(ad_id):
    patch = {
        "title": (request.form.get("title") or "Rewarded Ad").strip(),
        "coins_reward": int(request.form.get("coins_reward") or 10),
        "daily_limit": int(request.form.get("daily_limit") or 10),
        "is_active": request.form.get("is_active") == "1",
    }
    if db.update_ad(ad_id, patch):
        flash("Ad campaign updated.", "success")
    else:
        flash("Ad not found.", "error")
    return redirect(url_for("admin_web.ads"))


@admin_web_bp.post("/ads/<ad_id>/delete")
@login_required
def delete_ad(ad_id):
    db.delete_ad(ad_id)
    flash("Ad campaign deleted.", "success")
    return redirect(url_for("admin_web.ads"))


# --------------------------------------------------------------------------- #
# Withdrawals
# --------------------------------------------------------------------------- #
@admin_web_bp.get("/withdrawals")
@login_required
def withdrawals():
    status = request.args.get("status") or None
    rows = db.get_withdrawals(status=status)
    for w in rows:
        u = db.get_user_by_id(w["user_id"])
        w["user_name"] = (u.get("name") if u else "") or "CashBee User"
        w["user_phone"] = u["phone"] if u else ""

    counts = {
        "all": len(db.get_withdrawals()),
        "pending": len(db.get_withdrawals(status="pending")),
        "paid": len(db.get_withdrawals(status="paid")),
        "rejected": len(db.get_withdrawals(status="rejected")),
    }
    return render_template(
        "admin/withdrawals.html",
        withdrawals=rows,
        status=status or "all",
        counts=counts,
        active="withdrawals",
    )


@admin_web_bp.post("/withdrawals/<wid>/approve")
@login_required
def approve_withdrawal(wid):
    w = db.get_withdrawal(wid)
    if not w:
        flash("Withdrawal not found.", "error")
        return redirect(url_for("admin_web.withdrawals"))
    if w["status"] != "pending":
        flash(f"Already {w['status']}.", "error")
        return redirect(url_for("admin_web.withdrawals"))

    payout = create_payout(w["upi_id"], w["amount_inr"], reference=wid)
    if not payout.get("success"):
        flash("Payout failed at the gateway.", "error")
        return redirect(url_for("admin_web.withdrawals"))

    user = db.get_user_by_id(w["user_id"])
    if user:
        db.update_user(
            w["user_id"], {"total_withdrawn": user["total_withdrawn"] + w["coins"]}
        )
    db.update_withdrawal(
        wid,
        {
            "status": "paid",
            "paid_at": datetime.now(timezone.utc).isoformat(),
            "admin_note": f"Payout {payout.get('payout_id')}",
        },
    )
    # TODO: notify the user that ₹{w['amount_inr']} was paid (WhatsApp notification API — to be added).
    flash(f"Withdrawal approved & ₹{w['amount_inr']} paid out.", "success")
    return redirect(request.referrer or url_for("admin_web.withdrawals"))


@admin_web_bp.post("/withdrawals/<wid>/reject")
@login_required
def reject_withdrawal(wid):
    note = (request.form.get("admin_note") or "Rejected by admin").strip()
    w = db.get_withdrawal(wid)
    if not w:
        flash("Withdrawal not found.", "error")
        return redirect(url_for("admin_web.withdrawals"))
    if w["status"] != "pending":
        flash(f"Already {w['status']}.", "error")
        return redirect(url_for("admin_web.withdrawals"))

    # Refund the held coins.
    db.add_coins(w["user_id"], w["coins"], count_as_earning=False)
    db.add_transaction(
        w["user_id"], "ad_earn", w["coins"], "Refund: withdrawal rejected"
    )
    db.update_withdrawal(wid, {"status": "rejected", "admin_note": note})

    # TODO: notify the user their withdrawal was rejected + refunded (WhatsApp notification API — to be added).
    flash("Withdrawal rejected and coins refunded.", "success")
    return redirect(request.referrer or url_for("admin_web.withdrawals"))


# --------------------------------------------------------------------------- #
# Referrals
# --------------------------------------------------------------------------- #
@admin_web_bp.get("/referrals")
@login_required
def referrals():
    users = db.all_users()
    top = []
    for u in users:
        edges = db.get_referrals_by_referrer(u["id"])
        if not edges:
            continue
        top.append(
            {
                "user_id": u["id"],
                "name": u.get("name") or "CashBee User",
                "phone": u["phone"],
                "referral_code": u["referral_code"],
                "direct_count": len(edges),
                "total_earned": sum(e.get("coins_earned", 0) for e in edges),
            }
        )
    top.sort(key=lambda x: x["total_earned"], reverse=True)

    totals = {
        "total_referrals": sum(t["direct_count"] for t in top),
        "total_coins": sum(t["total_earned"] for t in top),
        "active_referrers": len(top),
    }
    return render_template(
        "admin/referrals.html",
        top_referrers=top[:50],
        totals=totals,
        active="referrals",
    )


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
@admin_web_bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        patch = {}
        for key in (
            "coin_rate",
            "daily_ad_limit",
            "ad_cooldown_seconds",
            "min_withdrawal",
            "referral_signup_bonus",
            "referral_bonus_percent",
        ):
            if request.form.get(key) is not None:
                try:
                    patch[key] = int(request.form.get(key))
                except (TypeError, ValueError):
                    pass
        patch["maintenance_mode"] = request.form.get("maintenance_mode") == "1"
        db.update_settings(patch)
        flash("Settings saved.", "success")
        return redirect(url_for("admin_web.settings"))

    return render_template(
        "admin/settings.html",
        settings=db.get_settings(),
        build=db.get_app_build(),
        active="settings",
    )


# --------------------------------------------------------------------------- #
# App build upload — admin uploads a new APK, users download the latest
# --------------------------------------------------------------------------- #
@admin_web_bp.post("/app/upload")
@login_required
def upload_app():
    file = request.files.get("apk")
    version = (request.form.get("version") or "").strip()

    if not file or not file.filename:
        flash("Please choose an APK file to upload.", "error")
        return redirect(url_for("admin_web.settings"))
    if not file.filename.lower().endswith(".apk"):
        flash("Only .apk files are allowed.", "error")
        return redirect(url_for("admin_web.settings"))

    upload_dir = os.path.join(current_app.root_path, "uploads")
    os.makedirs(upload_dir, exist_ok=True)
    path = os.path.join(upload_dir, STORED_APK)
    file.save(path)

    db.set_app_build(
        {
            "version": version or "1.0",
            "original_name": secure_filename(file.filename),
            "size": os.path.getsize(path),
            "uploaded_at": db.now_iso(),
        }
    )
    flash(
        f"New build (v{version or '1.0'}) uploaded — users now get this version.",
        "success",
    )
    return redirect(url_for("admin_web.settings"))
