"""Referral info + tree."""
from flask import Blueprint, jsonify, g

import database.db as db
from services import auth_required

referral_bp = Blueprint("referral", __name__)

REF_BASE_URL = "https://cashbee.up.railway.app/ref/"


@referral_bp.get("/info")
@auth_required
def referral_info():
    user = db.get_user_by_id(g.user["id"])
    edges = db.get_referrals_by_referrer(user["id"])

    total_earned = sum(e.get("coins_earned", 0) for e in edges)

    tree = []
    for e in edges:
        referee = db.get_user_by_id(e["referee_id"])
        if not referee:
            continue
        tree.append(
            {
                "referee_id": referee["id"],
                "name": referee.get("name") or "CashBee User",
                "phone_masked": _mask(referee["phone"]),
                "coins_earned": e.get("coins_earned", 0),
                "joined_at": e.get("created_at"),
            }
        )

    return jsonify(
        {
            "code": user["referral_code"],
            "link": REF_BASE_URL + user["referral_code"],
            "direct_count": len(tree),
            "total_earned": total_earned,
            "tree": tree,
        }
    )


@referral_bp.get("/public/<code>")
def public_referrer(code):
    """Public (no-auth) lookup used by the website /ref/[code] landing page."""
    user = db.get_user_by_code(code.upper())
    if not user:
        return jsonify({"error": "Invalid code"}), 404
    edges = db.get_referrals_by_referrer(user["id"])
    return jsonify(
        {
            "code": user["referral_code"],
            "name": user.get("name") or "A CashBee user",
            "referral_count": len(edges),
            "total_earned": sum(e.get("coins_earned", 0) for e in edges),
            "link": REF_BASE_URL + user["referral_code"],
        }
    )


def _mask(phone: str) -> str:
    if len(phone) <= 4:
        return phone
    return phone[:3] + "****" + phone[-3:]
