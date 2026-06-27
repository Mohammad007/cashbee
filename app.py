"""
CashBee — Flask application factory + entrypoint.

A single Flask app serves three things from one process:
  • the JSON REST API (mobile app)         → /api/*
  • the public marketing website           → /
  • the server-rendered admin console       → /admin

Run locally:
    pip install -r requirements.txt
    python app.py
Server boots on http://localhost:5000

Auth:
  • API + mobile app use JWT (Bearer token); all coin crediting is server-side.
  • The /admin web console uses a browser session cookie (ADMIN_EMAIL / ADMIN_PASSWORD).
Data is stored as JSON files (TinyDB) under backend/database/.
"""
from datetime import timedelta

from flask import Flask, jsonify, request, render_template
from flask_cors import CORS

from config import Config
from extensions import limiter
import database.db as db

from routes.auth_routes import auth_bp
from routes.user_routes import user_bp
from routes.ad_routes import ad_bp
from routes.referral_routes import referral_bp
from routes.wallet_routes import wallet_bp
from routes.admin_routes import admin_bp
from routes.site_routes import site_bp
from routes.admin_web_routes import admin_web_bp
from routes.gamification_routes import gami_bp
from routes.payment_routes import payment_bp


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)
    # Admin browser sessions live for 12h, matching the admin JWT policy.
    app.permanent_session_lifetime = timedelta(hours=12)

    CORS(app, resources={r"/api/*": {"origins": Config.CORS_ORIGINS}})
    limiter.init_app(app)

    # Seed defaults so the app is usable on first boot.
    db.get_settings()
    db.seed_default_ad()
    db.seed_festivals()

    # --- JSON REST API (mobile app) ---
    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(user_bp, url_prefix="/api/user")
    app.register_blueprint(ad_bp, url_prefix="/api/ads")
    app.register_blueprint(referral_bp, url_prefix="/api/referral")
    app.register_blueprint(wallet_bp, url_prefix="/api/wallet")
    app.register_blueprint(admin_bp, url_prefix="/api/admin")
    app.register_blueprint(gami_bp, url_prefix="/api")
    app.register_blueprint(payment_bp, url_prefix="/api")

    # --- Server-rendered web (website + admin console) ---
    app.register_blueprint(site_bp)            # /
    app.register_blueprint(admin_web_bp)       # /admin

    @app.get("/api/health")
    def health():
        return jsonify({"service": "CashBee API", "status": "ok"})

    @app.errorhandler(404)
    def not_found(_):
        if request.path.startswith("/api/"):
            return jsonify({"error": "Not found"}), 404
        return render_template("site/404.html"), 404

    @app.errorhandler(429)
    def rate_limited(_):
        return jsonify({"error": "Too many requests"}), 429

    @app.errorhandler(500)
    def server_error(_):
        if request.path.startswith("/api/"):
            return jsonify({"error": "Internal server error"}), 500
        return render_template("site/404.html"), 500

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
