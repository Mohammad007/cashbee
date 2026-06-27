"""
Google Play Billing — server-side purchase verification.

Verifies in-app purchase tokens via the Google Play Developer API
(androidpublisher v3) using a service account. This is the trustworthy signal
that a purchase is real — the Flutter client is never trusted on its own.

Setup (one-time):
  1. Play Console → create in-app products with IDs matching the app
     (pro_monthly, elite_monthly, gold_boost, seven_day_power).
  2. Google Cloud → create a service account, enable "Google Play Android
     Developer API", download its JSON key.
  3. Play Console → Users & permissions → invite the service-account email and
     grant "View financial data / Manage orders".
  4. On the server set:
        GOOGLE_PLAY_PACKAGE_NAME=com.cashbee.cashbee
        GOOGLE_SERVICE_ACCOUNT_FILE=/path/to/service-account.json
     and `pip install google-api-python-client google-auth`.

If anything is missing, verification FAILS CLOSED (no benefit granted).
"""
import os

from config import Config

_SCOPE = "https://www.googleapis.com/auth/androidpublisher"
_service = None  # cached androidpublisher client


def is_configured() -> bool:
    return bool(Config.GOOGLE_PLAY_PACKAGE_NAME and Config.GOOGLE_SERVICE_ACCOUNT_FILE
                and os.path.exists(Config.GOOGLE_SERVICE_ACCOUNT_FILE))


def _get_service():
    global _service
    if _service is not None:
        return _service
    if not is_configured():
        return None
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        creds = service_account.Credentials.from_service_account_file(
            Config.GOOGLE_SERVICE_ACCOUNT_FILE, scopes=[_SCOPE]
        )
        _service = build("androidpublisher", "v3", credentials=creds, cache_discovery=False)
        return _service
    except Exception:
        return None


def verify_product(product_id: str, token: str) -> dict:
    """
    Verify a one-time (consumable/managed) product purchase.
    Returns {ok: bool, order_id: str, error: str}.
    """
    if not is_configured():
        return {"ok": False, "error": "Play Billing is not configured on the server."}
    svc = _get_service()
    if svc is None:
        return {"ok": False, "error": "Play Billing client unavailable (check service account / libs)."}
    try:
        res = (
            svc.purchases()
            .products()
            .get(packageName=Config.GOOGLE_PLAY_PACKAGE_NAME, productId=product_id, token=token)
            .execute()
        )
    except Exception as exc:
        return {"ok": False, "error": f"Verification failed: {exc}"}

    # purchaseState: 0 = Purchased, 1 = Canceled, 2 = Pending
    if int(res.get("purchaseState", 1)) != 0:
        return {"ok": False, "error": "Purchase is not in a completed state."}
    return {"ok": True, "order_id": res.get("orderId", ""), "raw": res}
