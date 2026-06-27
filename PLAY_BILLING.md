# Google Play Billing — setup

CashBee sells **Membership** (Pro/Elite) and **Boost packs** as Google Play
in-app products. Purchases are verified **server-side** before any benefit is
applied. Razorpay is no longer used for these.

## 1. Create the products in Play Console
Play Console → your app → **Monetize → Products → In-app products** → create
**Consumable** products with these exact IDs (must match the app & backend):

| Product ID | What it is |
|------------|------------|
| `pro_monthly`     | Pro membership (30 days) |
| `elite_monthly`   | Elite membership (30 days) |
| `gold_boost`      | Gold Boost (24h) |
| `seven_day_power` | 7-Day Power boost |

Set each product's price in Play Console. (The price shown in the app comes from
Play; the ₹ value in the admin panel is only a fallback/label.)

## 2. Service account for verification
1. Google Cloud Console → create a **service account** → create a **JSON key**.
2. Enable the **Google Play Android Developer API** for that project.
3. Play Console → **Users & permissions** → invite the service-account email →
   grant **View financial data** + **Manage orders & subscriptions**.
4. Put the JSON file on the server and set:
   ```
   GOOGLE_PLAY_PACKAGE_NAME=com.cashbee.cashbee
   GOOGLE_SERVICE_ACCOUNT_FILE=/path/to/service-account.json
   ```
5. `pip install -r requirements.txt` (adds `google-api-python-client`, `google-auth`).

If these aren't set, verification **fails closed** — no purchase is granted.

## 3. Admin toggle
**Admin → Settings → Paid Features**: a master switch (`membership_enabled`).
Turn it **OFF** to ship a Play-compliant build with **no in-app purchases**
(the app hides the store, endpoints refuse). The app reads this on launch — no
rebuild needed.

## 4. App side
- `in_app_purchase` plugin buys the product → sends the purchase token to
  `POST /api/membership/verify` or `/api/boost/verify`.
- Server verifies via `play_billing.verify_product`, applies the benefit, and
  records the purchase (idempotent on Google's `orderId`).

## Policy note
Selling digital features on Google Play **must** use Play Billing (done here).
Do not route these purchases through UPI/Razorpay on the Play build — that
violates Play policy. UPI is still fine for **withdrawals** (paying users out).
