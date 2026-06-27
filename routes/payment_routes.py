"""
Paid features: premium membership + boost packs — GOOGLE PLAY BILLING.

Flow: the app buys a product through Google Play (in_app_purchase), then sends
the product_id + purchase token to the server. The server VERIFIES the token
with the Google Play Developer API (play_billing) and only then applies the
membership/boost. The client is never trusted to grant a benefit.

A master switch (`membership_enabled`, Admin → Settings) disables the whole
store — the catalog comes back empty and purchases are refused. Use it to ship
a Play-compliant build with no in-app purchases.
"""
from flask import Blueprint, request, jsonify, g

import database.db as db
import billing
import play_billing
from config import Config
from services import auth_required

payment_bp = Blueprint("payment", __name__)


def _store_enabled() -> bool:
    return bool(db.get_settings().get("membership_enabled", True))


# --------------------------------------------------------------------------- #
# Catalog (product IDs must match the Play Console in-app products)
# --------------------------------------------------------------------------- #
@payment_bp.get("/membership/plans")
def membership_plans():
    if not _store_enabled():
        return jsonify({"plans": [], "enabled": False})
    return jsonify({
        "enabled": True,
        "plans": [{"id": k, **v} for k, v in db.get_membership_plans().items()],
    })


@payment_bp.get("/boost/packs")
def boost_packs():
    if not _store_enabled():
        return jsonify({"packs": [], "enabled": False})
    return jsonify({
        "enabled": True,
        "packs": [{"id": k, **v} for k, v in Config.BOOST_PACKS.items()],
    })


# --------------------------------------------------------------------------- #
# Verify a Google Play purchase + apply the benefit
# --------------------------------------------------------------------------- #
def _process(purchase_type: str, item_id: str, item: dict, token: str):
    """Verify the Play purchase token, then apply membership/boost. Idempotent
    on Google's orderId so retries don't double-credit."""
    if not token:
        return jsonify({"success": False, "message": "Missing purchase token."}), 400

    result = play_billing.verify_product(item_id, token)
    if not result.get("ok"):
        return jsonify({"success": False, "message": result.get("error", "Verification failed")}), 400

    order_id = result.get("order_id") or f"{purchase_type}_{item_id}_{g.user['id'][:8]}"

    # Idempotency: if we've already credited this order, just report success.
    existing = db.get_purchase_by_order(order_id)
    if existing and existing.get("status") == "completed":
        return jsonify({"success": True, "already_processed": True,
                        "item_name": item["name"]})

    if purchase_type == "membership":
        applied = billing.apply_membership(g.user["id"], item_id)
        expires_at = applied["expiry"]
    else:
        applied = billing.apply_boost(g.user["id"], item_id)
        expires_at = applied["expires_at"]

    db.create_purchase(
        g.user["id"], purchase_type, item_id, item["name"], item["price_inr"],
        order_id=order_id, purchase_token=token, status="completed",
    )
    db.update_purchase(order_id, {"expires_at": expires_at})

    # TODO: notify the user (WhatsApp): "✅ {item} activated!"
    return jsonify({
        "success": True,
        "item_name": item["name"],
        "expiry": expires_at,
        "coin_multiplier": item.get("coin_multiplier"),
    })


@payment_bp.post("/membership/verify")
@auth_required
def membership_verify():
    if not _store_enabled():
        return jsonify({"success": False, "message": "Store is disabled."}), 403
    data = request.get_json(silent=True) or {}
    plan_id = (data.get("product_id") or data.get("plan_id") or "").strip()
    token = (data.get("purchase_token") or "").strip()
    plan = db.get_membership_plans().get(plan_id)
    if not plan:
        return jsonify({"success": False, "message": "Invalid plan"}), 400
    return _process("membership", plan_id, plan, token)


@payment_bp.post("/boost/verify")
@auth_required
def boost_verify():
    if not _store_enabled():
        return jsonify({"success": False, "message": "Store is disabled."}), 403
    data = request.get_json(silent=True) or {}
    boost_id = (data.get("product_id") or data.get("boost_id") or "").strip()
    token = (data.get("purchase_token") or "").strip()
    boost = Config.BOOST_PACKS.get(boost_id)
    if not boost:
        return jsonify({"success": False, "message": "Invalid boost"}), 400
    return _process("boost", boost_id, boost, token)


# --------------------------------------------------------------------------- #
# Status
# --------------------------------------------------------------------------- #
@payment_bp.get("/membership/status")
@auth_required
def membership_status():
    return jsonify(billing.membership_status(db.get_user_by_id(g.user["id"])))


@payment_bp.get("/boost/status")
@auth_required
def boost_status():
    return jsonify(billing.boost_status(db.get_user_by_id(g.user["id"])))
