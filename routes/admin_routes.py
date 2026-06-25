"""Admin dashboard, user management, ads, withdrawals, referrals, settings."""
from datetime import datetime, timezone

from flask import Blueprint, request, jsonify

import database.db as db
from services import admin_required, create_payout

admin_bp = Blueprint("admin", __name__)


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #
@admin_bp.get("/dashboard")
@admin_required
def dashboard():
    users = db.all_users()
    today = db.today_str()

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
    for w in db.withdrawals_db.all():
        if w["status"] in ("approved", "paid"):
            day = w["created_at"][:10]
            withdrawal_volume[day] = withdrawal_volume.get(day, 0) + w["amount_inr"]

    def series(d):
        return [{"date": k, "value": v} for k, v in sorted(d.items())]

    return jsonify(
        {
            "total_users": len(users),
            "total_coins_issued": total_coins_issued,
            "pending_withdrawals": len(pending),
            "today_ad_views": today_ad_views,
            "charts": {
                "daily_signups": series(daily_signups),
                "daily_ad_views": series(daily_ad_views),
                "withdrawal_volume": series(withdrawal_volume),
            },
        }
    )


# --------------------------------------------------------------------------- #
# Users
# --------------------------------------------------------------------------- #
@admin_bp.get("/users")
@admin_required
def list_users():
    page = int(request.args.get("page", 1))
    search = (request.args.get("search") or "").strip().lower()
    per_page = 20

    users = db.all_users()
    if search:
        users = [
            u
            for u in users
            if search in (u.get("name") or "").lower()
            or search in u["phone"]
            or search in u["referral_code"].lower()
        ]
    users.sort(key=lambda u: u["created_at"], reverse=True)

    total = len(users)
    start = (page - 1) * per_page
    page_rows = users[start : start + per_page]

    for u in page_rows:
        u["referral_count"] = len(db.get_referrals_by_referrer(u["id"]))

    return jsonify(
        {
            "users": page_rows,
            "total": total,
            "page": page,
            "pages": (total + per_page - 1) // per_page,
        }
    )


@admin_bp.post("/users/<user_id>/ban")
@admin_required
def ban_user(user_id):
    data = request.get_json(silent=True) or {}
    banned = bool(data.get("banned", True))
    user = db.update_user(user_id, {"is_banned": banned})
    if not user:
        return jsonify({"error": "User not found"}), 404
    return jsonify({"message": "updated", "user": user})


# --------------------------------------------------------------------------- #
# Ads
# --------------------------------------------------------------------------- #
@admin_bp.get("/ads")
@admin_required
def list_ads():
    return jsonify({"ads": db.all_ads()})


@admin_bp.post("/ads")
@admin_required
def create_ad():
    data = request.get_json(silent=True) or {}
    ad = db.create_ad(
        title=(data.get("title") or "Rewarded Ad").strip(),
        coins_reward=int(data.get("coins_reward") or 10),
        daily_limit=int(data.get("daily_limit") or 10),
    )
    return jsonify({"ad": ad}), 201


@admin_bp.put("/ads/<ad_id>")
@admin_required
def edit_ad(ad_id):
    data = request.get_json(silent=True) or {}
    patch = {}
    for key in ("title", "coins_reward", "daily_limit", "is_active"):
        if key in data:
            patch[key] = data[key]
    ad = db.update_ad(ad_id, patch)
    if not ad:
        return jsonify({"error": "Ad not found"}), 404
    return jsonify({"ad": ad})


@admin_bp.delete("/ads/<ad_id>")
@admin_required
def remove_ad(ad_id):
    db.delete_ad(ad_id)
    return jsonify({"message": "deleted"})


# --------------------------------------------------------------------------- #
# Withdrawals
# --------------------------------------------------------------------------- #
@admin_bp.get("/withdrawals")
@admin_required
def list_withdrawals():
    status = request.args.get("status")
    rows = db.get_withdrawals(status=status)
    for w in rows:
        u = db.get_user_by_id(w["user_id"])
        w["user_name"] = (u.get("name") if u else "") or "CashBee User"
        w["user_phone"] = u["phone"] if u else ""
    return jsonify({"withdrawals": rows})


@admin_bp.post("/withdrawals/<wid>/approve")
@admin_required
def approve_withdrawal(wid):
    w = db.get_withdrawal(wid)
    if not w:
        return jsonify({"error": "Not found"}), 404
    if w["status"] != "pending":
        return jsonify({"error": f"Already {w['status']}"}), 409

    payout = create_payout(w["upi_id"], w["amount_inr"], reference=wid)
    if not payout.get("success"):
        return jsonify({"error": "Payout failed", "detail": payout}), 502

    user = db.get_user_by_id(w["user_id"])
    db.update_user(
        w["user_id"], {"total_withdrawn": user["total_withdrawn"] + w["coins"]}
    )
    updated = db.update_withdrawal(
        wid,
        {
            "status": "paid",
            "paid_at": datetime.now(timezone.utc).isoformat(),
            "admin_note": f"Payout {payout.get('payout_id')}",
        },
    )
    # TODO: notify the user that ₹{w['amount_inr']} was paid (WhatsApp notification API — to be added).
    return jsonify({"message": "approved & paid", "withdrawal": updated})


@admin_bp.post("/withdrawals/<wid>/reject")
@admin_required
def reject_withdrawal(wid):
    data = request.get_json(silent=True) or {}
    note = (data.get("admin_note") or "Rejected by admin").strip()

    w = db.get_withdrawal(wid)
    if not w:
        return jsonify({"error": "Not found"}), 404
    if w["status"] != "pending":
        return jsonify({"error": f"Already {w['status']}"}), 409

    # Refund the held coins.
    db.add_coins(w["user_id"], w["coins"], count_as_earning=False)
    db.add_transaction(
        w["user_id"], "ad_earn", w["coins"], "Refund: withdrawal rejected"
    )
    updated = db.update_withdrawal(wid, {"status": "rejected", "admin_note": note})

    # TODO: notify the user their withdrawal was rejected + refunded (WhatsApp notification API — to be added).
    return jsonify({"message": "rejected & refunded", "withdrawal": updated})


# --------------------------------------------------------------------------- #
# Referrals
# --------------------------------------------------------------------------- #
@admin_bp.get("/referrals")
@admin_required
def referrals_overview():
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
    return jsonify({"top_referrers": top[:50]})


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
@admin_bp.get("/settings")
@admin_required
def get_settings():
    return jsonify(db.get_settings())


@admin_bp.post("/settings")
@admin_required
def update_settings():
    data = request.get_json(silent=True) or {}
    updated = db.update_settings(data)
    return jsonify(updated)
