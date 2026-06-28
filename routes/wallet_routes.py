"""Wallet balance, transaction history, leaderboard, withdrawal requests."""
from datetime import datetime, timedelta, timezone

from flask import Blueprint, request, jsonify, g

import database.db as db
from services import auth_required

wallet_bp = Blueprint("wallet", __name__)


def _inr(coins: int, coin_rate: int) -> float:
    return round(coins / coin_rate, 2)


@wallet_bp.get("/balance")
@auth_required
def balance():
    user = db.get_user_by_id(g.user["id"])
    settings = db.get_settings()
    type_ = request.args.get("type")  # All/ads/referral/withdrawal filter
    type_map = {
        "ads": "ad_earn",
        "referral": "referral_earn",
        "withdrawal": "withdrawal",
    }
    txn_type = type_map.get((type_ or "").lower())
    txns = db.get_transactions(user["id"], type_=txn_type)
    return jsonify(
        {
            "coins": user["coins"],
            "coin_rate": settings["coin_rate"],
            "min_withdrawal": settings["min_withdrawal"],
            "inr_value": _inr(user["coins"], settings["coin_rate"]),
            "total_earned": user["total_earned"],
            "total_withdrawn": user["total_withdrawn"],
            "transactions": txns,
        }
    )


@wallet_bp.post("/withdraw")
@auth_required
def withdraw():
    data = request.get_json(silent=True) or {}
    coins = int(data.get("coins") or 0)
    upi_id = (data.get("upi_id") or "").strip()

    settings = db.get_settings()
    user = db.get_user_by_id(g.user["id"])

    if "@" not in upi_id or len(upi_id) < 5:
        return jsonify({"error": "Invalid UPI ID"}), 400
    if coins < settings["min_withdrawal"]:
        return jsonify(
            {"error": f"Minimum withdrawal is {settings['min_withdrawal']} coins"}
        ), 400
    if coins > user["coins"]:
        return jsonify({"error": "Insufficient balance"}), 400
    if db.has_pending_withdrawal(user["id"]):
        return jsonify({"error": "You already have a pending withdrawal"}), 409

    amount_inr = _inr(coins, settings["coin_rate"])

    # Deduct immediately (held) so the balance can't be double-spent.
    db.add_coins(user["id"], -coins, count_as_earning=False)
    w = db.create_withdrawal(user["id"], coins, amount_inr, upi_id)
    db.add_transaction(
        user["id"], "withdrawal", -coins, f"Withdrawal request ₹{amount_inr}"
    )

    resp = {"message": "Withdrawal requested", "withdrawal": w}

    # First-withdrawal bonus: credit a one-time bonus the very first time.
    if not db.get_field(user, "first_withdrawal_done", False):
        bonus = int(settings.get("first_withdrawal_bonus", 250))
        db.update_user(user["id"], {"first_withdrawal_done": True})
        if bonus > 0:
            import gamification as gm

            gm.credit_earning(
                user["id"], bonus, "first_withdrawal_bonus",
                "🎉 First withdrawal bonus",
            )
            bonus_inr = _inr(bonus, settings["coin_rate"])
            resp["first_withdrawal_bonus"] = bonus
            resp["bonus_message"] = (
                f"🎉 First Withdrawal Bonus! ₹{bonus_inr} extra credited to your wallet!"
            )

    return jsonify(resp)


@wallet_bp.get("/withdrawals")
@auth_required
def my_withdrawals():
    rows = db.get_withdrawals(user_id=g.user["id"])
    return jsonify({"withdrawals": rows})


@wallet_bp.get("/leaderboard")
@auth_required
def leaderboard():
    """Top 50 earners this week (by ad/referral transactions) + my rank."""
    period = request.args.get("period", "week")
    days = 7 if period == "week" else 30
    since = datetime.now(timezone.utc) - timedelta(days=days)

    earnings: dict[str, int] = {}
    for txn in db.transactions_db.all():
        if txn["type"] in ("ad_earn", "referral_earn") and txn["coins"] > 0:
            try:
                created = datetime.fromisoformat(txn["created_at"])
            except ValueError:
                continue
            if created >= since:
                earnings[txn["user_id"]] = earnings.get(txn["user_id"], 0) + txn["coins"]

    ranked = sorted(earnings.items(), key=lambda kv: kv[1], reverse=True)

    board = []
    for rank, (uid, coins) in enumerate(ranked[:50], start=1):
        u = db.get_user_by_id(uid)
        if not u:
            continue
        board.append(
            {
                "rank": rank,
                "user_id": uid,
                "name": u.get("name") or "CashBee User",
                "coins_earned": coins,
                "referral_count": len(db.get_referrals_by_referrer(uid)),
            }
        )

    my_rank = next(
        (i + 1 for i, (uid, _) in enumerate(ranked) if uid == g.user["id"]), None
    )
    my_coins = earnings.get(g.user["id"], 0)

    return jsonify(
        {
            "period": period,
            "leaderboard": board,
            "my_rank": my_rank,
            "my_coins_earned": my_coins,
        }
    )
