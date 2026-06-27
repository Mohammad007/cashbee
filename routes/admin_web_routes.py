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
    ad_type = (request.form.get("ad_type") or "rewarded").strip()
    db.create_ad(
        title=(request.form.get("title") or "Rewarded Ad").strip(),
        coins_reward=int(request.form.get("coins_reward") or 10),
        daily_limit=int(request.form.get("daily_limit") or 10),
        ad_type=ad_type,
        media_type=(request.form.get("media_type") or "").strip(),
        media_url=(request.form.get("media_url") or "").strip(),
        click_url=(request.form.get("click_url") or "").strip(),
        watch_seconds=int(request.form.get("watch_seconds") or 30),
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
    # Custom-ad fields are only present when editing a custom ad.
    if request.form.get("ad_type"):
        patch["ad_type"] = request.form.get("ad_type").strip()
    for f in ("media_type", "media_url", "click_url"):
        if f in request.form:
            patch[f] = (request.form.get(f) or "").strip()
    if request.form.get("watch_seconds"):
        patch["watch_seconds"] = int(request.form.get("watch_seconds"))

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
        err = payout.get("error")
        # Razorpay errors come back as a nested dict; pull out the human message.
        if isinstance(err, dict):
            err = (err.get("error") or {}).get("description") or err.get("description") or str(err)
        flash(f"Payout failed: {err or 'gateway error'}", "error")
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


@admin_web_bp.post("/withdrawals/<wid>/mark-paid")
@login_required
def mark_paid_withdrawal(wid):
    """Manually mark a withdrawal paid — the admin sent the money over UPI by
    hand (no payment gateway). Optionally records a UPI transaction reference."""
    upi_ref = (request.form.get("upi_ref") or "").strip()
    w = db.get_withdrawal(wid)
    if not w:
        flash("Withdrawal not found.", "error")
        return redirect(url_for("admin_web.withdrawals"))
    if w["status"] != "pending":
        flash(f"Already {w['status']}.", "error")
        return redirect(url_for("admin_web.withdrawals"))

    user = db.get_user_by_id(w["user_id"])
    if user:
        db.update_user(
            w["user_id"], {"total_withdrawn": user["total_withdrawn"] + w["coins"]}
        )
    note = f"Paid manually (UPI ref: {upi_ref})" if upi_ref else "Paid manually"
    db.update_withdrawal(
        wid,
        {
            "status": "paid",
            "paid_at": datetime.now(timezone.utc).isoformat(),
            "admin_note": note,
        },
    )
    # TODO: notify the user that ₹{w['amount_inr']} was paid (WhatsApp notification API — to be added).
    flash(f"Marked as paid — ₹{w['amount_inr']} to {w['upi_id']}.", "success")
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
            "stats_baseline_users",
            "stats_baseline_paid_inr",
            "stats_baseline_ads",
        ):
            if request.form.get(key) is not None:
                try:
                    patch[key] = int(request.form.get(key))
                except (TypeError, ValueError):
                    pass
        patch["maintenance_mode"] = request.form.get("maintenance_mode") == "1"

        # AdMob ad unit IDs (strings) + flags — served to the app at runtime.
        for key in ("admob_rewarded_id",):
            if request.form.get(key) is not None:
                patch[key] = (request.form.get(key) or "").strip()
        patch["ads_enabled"] = request.form.get("ads_enabled") == "1"
        patch["use_test_ads"] = request.form.get("use_test_ads") == "1"
        patch["membership_enabled"] = request.form.get("membership_enabled") == "1"

        # Razorpay — mode + non-secret fields always saved; secrets only when a
        # new value is typed (blank = keep the existing stored secret).
        mode = (request.form.get("razorpay_mode") or "test").lower()
        patch["razorpay_mode"] = "live" if mode == "live" else "test"
        for key in (
            "razorpay_test_key_id",
            "razorpay_test_account_number",
            "razorpay_live_key_id",
            "razorpay_live_account_number",
        ):
            if request.form.get(key) is not None:
                patch[key] = (request.form.get(key) or "").strip()
        for key in ("razorpay_test_key_secret", "razorpay_live_key_secret"):
            val = (request.form.get(key) or "").strip()
            if val:
                patch[key] = val

        # WhatsApp OTP provider settings.
        for key in (
            "whatsapp_api_url",
            "whatsapp_session_id",
            "whatsapp_template_id",
            "whatsapp_template_name",
        ):
            if request.form.get(key) is not None:
                patch[key] = (request.form.get(key) or "").strip()

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


# --------------------------------------------------------------------------- #
# Festivals (limited-time coin multipliers)
# --------------------------------------------------------------------------- #
@admin_web_bp.get("/festivals")
@login_required
def festivals():
    return render_template(
        "admin/festivals.html",
        festivals=db.all_festivals(),
        active_festival=db.active_festival(),
        active="festivals",
    )


@admin_web_bp.post("/festivals/create")
@login_required
def create_festival():
    db.create_festival(
        {
            "name": (request.form.get("name") or "Festival").strip(),
            "multiplier": float(request.form.get("multiplier") or 2.0),
            "start_date": (request.form.get("start_date") or "").strip(),
            "end_date": (request.form.get("end_date") or "").strip(),
            "banner_text": (request.form.get("banner_text") or "").strip(),
            "banner_color": (request.form.get("banner_color") or "#FF6B00").strip(),
            "emoji": (request.form.get("emoji") or "🎉").strip(),
            "is_active": request.form.get("is_active") == "1",
        }
    )
    flash("Festival created.", "success")
    return redirect(url_for("admin_web.festivals"))


@admin_web_bp.post("/festivals/<fid>/toggle")
@login_required
def toggle_festival(fid):
    fest = db.get_festival(fid)
    if fest:
        db.update_festival(fid, {"is_active": not fest.get("is_active")})
        flash("Festival updated.", "success")
    return redirect(url_for("admin_web.festivals"))


@admin_web_bp.post("/festivals/<fid>/delete")
@login_required
def delete_festival(fid):
    db.delete_festival(fid)
    flash("Festival deleted.", "success")
    return redirect(url_for("admin_web.festivals"))


@admin_web_bp.post("/festivals/flash")
@login_required
def flash_offer():
    """Instant flash offer — 2x coins for the next 2 hours (today, IST)."""
    from datetime import timedelta

    today = db.today_ist()
    mult = float(request.form.get("multiplier") or 2.0)
    hours = int(request.form.get("hours") or 2)
    end = datetime.now(db.IST) + timedelta(hours=hours)
    db.create_festival(
        {
            "name": f"Flash {mult:g}x Offer",
            "multiplier": mult,
            "start_date": today,
            "end_date": end.strftime("%Y-%m-%d"),
            "end_at": end.isoformat(),  # hour-precise end for the live countdown
            "banner_text": f"⚡ Flash Offer! {mult:g}x Coins for {hours}h only!",
            "banner_color": "#7C3AED",
            "emoji": "⚡",
            "is_active": True,
        }
    )
    flash(f"Flash offer live: {mult:g}x for {hours}h.", "success")
    return redirect(url_for("admin_web.festivals"))


# --------------------------------------------------------------------------- #
# Lucky-spin analytics
# --------------------------------------------------------------------------- #
@admin_web_bp.get("/spins")
@login_required
def spins():
    spins = db.all_spins()
    today = db.today_str()
    today_spins = [s for s in spins if s.get("spun_at", "")[:10] == today]
    total_coins = sum(s.get("prize_coins", 0) for s in spins)

    jackpots = [s for s in spins if s.get("prize_coins", 0) >= 250]
    jackpots.sort(key=lambda s: s.get("spun_at", ""), reverse=True)
    for j in jackpots[:50]:
        u = db.get_user_by_id(j["user_id"])
        j["user_phone"] = u["phone"] if u else "—"

    # Spin vs ad earnings ratio
    spin_earn = total_coins
    ad_earn = sum(
        t["coins"] for t in db.transactions_db.all() if t.get("type") == "ad_earn"
    )
    return render_template(
        "admin/spins.html",
        total_spins=len(spins),
        today_spins=len(today_spins),
        total_coins=total_coins,
        jackpots=jackpots[:50],
        spin_earn=spin_earn,
        ad_earn=ad_earn,
        active="spins",
    )


# --------------------------------------------------------------------------- #
# Gamification settings (streak rewards, first-withdrawal bonus, spin weights)
# --------------------------------------------------------------------------- #
@admin_web_bp.route("/gamification", methods=["GET", "POST"])
@login_required
def gamification():
    settings = db.get_settings()
    if request.method == "POST":
        patch = {}
        # 7 streak day rewards
        rewards = []
        for i in range(7):
            rewards.append(int(request.form.get(f"streak_{i}") or 0))
        patch["streak_rewards"] = rewards
        patch["first_withdrawal_bonus"] = int(
            request.form.get("first_withdrawal_bonus") or 0
        )
        # Spin prizes: admin tunes both the coin payout and the odds (weight).
        prizes = [dict(p) for p in settings.get("spin_prizes", [])]
        for i, p in enumerate(prizes):
            if request.form.get(f"spin_coins_{i}") is not None:
                p["coins"] = int(request.form.get(f"spin_coins_{i}") or p["coins"])
            p["weight"] = int(request.form.get(f"spin_weight_{i}") or p["weight"])
        patch["spin_prizes"] = prizes
        db.update_settings(patch)
        flash("Gamification settings saved.", "success")
        return redirect(url_for("admin_web.gamification"))

    return render_template(
        "admin/gamification.html",
        settings=db.get_settings(),
        levels=Config.LEVELS,
        milestones=Config.MILESTONES,
        active="gamification",
    )


# --------------------------------------------------------------------------- #
# Membership plans (Pro / Elite) — edit price & benefits
# --------------------------------------------------------------------------- #
@admin_web_bp.route("/membership", methods=["GET", "POST"])
@login_required
def membership():
    plans = db.get_membership_plans()
    if request.method == "POST":
        updated = {}
        for pid, plan in plans.items():
            updated[pid] = {
                **plan,  # keep color/emoji and any other fields
                "name": (request.form.get(f"{pid}_name") or plan["name"]).strip(),
                "price_inr": int(request.form.get(f"{pid}_price_inr") or plan["price_inr"]),
                "duration_days": int(request.form.get(f"{pid}_duration_days") or plan["duration_days"]),
                "coin_multiplier": float(request.form.get(f"{pid}_coin_multiplier") or plan["coin_multiplier"]),
                "daily_ad_limit": int(request.form.get(f"{pid}_daily_ad_limit") or plan["daily_ad_limit"]),
                "daily_spins": int(request.form.get(f"{pid}_daily_spins") or plan["daily_spins"]),
                "instant_withdrawal": request.form.get(f"{pid}_instant_withdrawal") == "1",
            }
        db.set_membership_plans(updated)
        flash("Membership plans updated.", "success")
        return redirect(url_for("admin_web.membership"))

    # Render as an ordered list so the template is simple.
    plan_list = [{"id": pid, **p} for pid, p in plans.items()]
    return render_template(
        "admin/membership.html", plans=plan_list, active="membership"
    )


# --------------------------------------------------------------------------- #
# Database backup — export & import the full TinyDB store
# --------------------------------------------------------------------------- #
@admin_web_bp.get("/backup")
@login_required
def backup():
    tables = db.export_backup()
    counts = {k: len(v) for k, v in tables.items() if isinstance(v, list)}
    return render_template("admin/backup.html", counts=counts, active="backup")


@admin_web_bp.get("/backup/export")
@login_required
def backup_export():
    import json
    from flask import Response

    data = db.export_backup()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    body = json.dumps(data, indent=2, ensure_ascii=False)
    return Response(
        body,
        mimetype="application/json",
        headers={
            "Content-Disposition": f"attachment; filename=cashbee-backup-{stamp}.json"
        },
    )


@admin_web_bp.post("/backup/import")
@login_required
def backup_import():
    import json

    file = request.files.get("backup")
    if not file or not file.filename:
        flash("Please choose a backup .json file to import.", "error")
        return redirect(url_for("admin_web.backup"))
    if not file.filename.lower().endswith(".json"):
        flash("Only .json backup files are allowed.", "error")
        return redirect(url_for("admin_web.backup"))

    try:
        data = json.loads(file.read().decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        flash("Invalid backup file — could not parse JSON.", "error")
        return redirect(url_for("admin_web.backup"))

    if not isinstance(data, dict) or "users" not in data:
        flash("This doesn't look like a CashBee backup file.", "error")
        return redirect(url_for("admin_web.backup"))

    counts = db.restore_backup(data)
    total = sum(counts.values())
    flash(f"Backup restored — {total} records across {len(counts)} tables.", "success")
    return redirect(url_for("admin_web.backup"))


# --------------------------------------------------------------------------- #
# Revenue & Purchases (paid memberships + boosts)
# --------------------------------------------------------------------------- #
@admin_web_bp.get("/revenue")
@login_required
def revenue():
    import billing
    purchases = db.all_purchases()
    completed = [p for p in purchases if p.get("status") == "completed"]
    today = db.today_str()
    month = today[:7]

    today_rev = sum(p["amount_inr"] for p in completed if p["created_at"][:10] == today)
    month_rev = sum(p["amount_inr"] for p in completed if p["created_at"][:7] == month)
    total_rev = sum(p["amount_inr"] for p in completed)

    pro = elite = active_boosts = 0
    for u in db.all_users():
        if billing.membership_active(u):
            if u.get("membership_plan") == "elite_monthly":
                elite += 1
            elif u.get("membership_plan") == "pro_monthly":
                pro += 1
        if billing.boost_active(u):
            active_boosts += 1

    recent = completed[:15]
    for p in recent:
        u = db.get_user_by_id(p["user_id"])
        p["user_phone"] = u["phone"] if u else "—"

    return render_template(
        "admin/revenue.html",
        today_rev=today_rev, month_rev=month_rev, total_rev=total_rev,
        pro=pro, elite=elite, active_boosts=active_boosts,
        recent=recent, active="revenue",
    )


@admin_web_bp.get("/purchases")
@login_required
def purchases():
    type_filter = request.args.get("type", "")
    rows = db.all_purchases()
    if type_filter in ("membership", "boost"):
        rows = [p for p in rows if p.get("purchase_type") == type_filter]
    for p in rows:
        u = db.get_user_by_id(p["user_id"])
        p["user_phone"] = u["phone"] if u else "—"
    return render_template(
        "admin/purchases.html", purchases=rows, type_filter=type_filter,
        active="purchases",
    )


@admin_web_bp.get("/purchases/export.csv")
@login_required
def purchases_export():
    import csv, io
    from flask import Response

    rows = db.all_purchases()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Date", "User", "Type", "Item", "Amount (INR)", "Status",
                "Order ID", "Payment ID", "Expires"])
    for p in rows:
        u = db.get_user_by_id(p["user_id"])
        w.writerow([
            p.get("created_at", ""), (u["phone"] if u else ""),
            p.get("purchase_type", ""), p.get("item_name", ""),
            p.get("amount_inr", 0), p.get("status", ""),
            p.get("razorpay_order_id", ""), p.get("razorpay_payment_id", ""),
            p.get("expires_at", "") or "",
        ])
    return Response(
        buf.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=cashbee-purchases.csv"},
    )
