"""User profile management."""
from flask import Blueprint, request, jsonify, g

import database.db as db
from services import auth_required

user_bp = Blueprint("user", __name__)


@user_bp.get("/profile")
@auth_required
def profile():
    user = db.get_user_by_id(g.user["id"])  # fresh read
    return jsonify(user)


@user_bp.post("/profile")
@auth_required
def update_profile():
    data = request.get_json(silent=True) or {}
    patch = {}
    if "name" in data:
        patch["name"] = str(data["name"])[:60]
    if "photo_url" in data:
        patch["photo_url"] = str(data["photo_url"])
    user = db.update_user(g.user["id"], patch)
    return jsonify(user)
