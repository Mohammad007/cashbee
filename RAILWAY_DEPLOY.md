# Deploy CashBee backend to Railway

The backend is one Flask app that serves the **REST API** (`/api/*`), the
**website** (`/`), the **admin console** (`/admin`), and the **APK download**
(`/download`). These files make it run on Railway with Gunicorn.

| File | Purpose |
|------|---------|
| `Procfile` | start command (`gunicorn`) |
| `railway.json` | Railway-native build/deploy config + healthcheck |
| `runtime.txt` | pins Python 3.12 |
| `requirements.txt` | includes `gunicorn` |
| `.env.example` | the env vars to set in Railway |

---

## Steps

1. **Push the repo to GitHub** (the whole `cashbee/` repo is fine).

2. **Create the service on Railway**
   - railway.app → *New Project* → *Deploy from GitHub repo* → pick your repo.
   - Open the service → **Settings → Root Directory** and set it to **`backend`**.
     (The Flask app lives in `backend/`, not the repo root — this is required so
     Railway finds `requirements.txt` / `Procfile`.)

3. **Set Variables** (service → *Variables*) — from `.env.example`. At minimum:
   ```
   SECRET_KEY=<random long string>
   JWT_SECRET=<random long string>
   ADMIN_EMAIL=you@example.com
   ADMIN_PASSWORD=<strong password>
   OTP_DEV_MODE=false
   WHATSAPP_API_URL=https://whatsapp-api-production-807b.up.railway.app
   WHATSAPP_OTP_SESSION_ID=...
   WHATSAPP_OTP_TEMPLATE_ID=...
   WHATSAPP_OTP_TEMPLATE_NAME=OTP Verification
   CORS_ORIGINS=https://<your-service>.up.railway.app
   ```
   Do **not** set `PORT` — Railway injects it and Gunicorn binds to it.

4. **Add a Volume for persistent data (IMPORTANT)**
   Railway's container filesystem is **ephemeral** — without a volume, every
   redeploy/restart **wipes all users, coins, and withdrawals** (TinyDB JSON files).
   - Service → *Volumes* → *New Volume* → mount path `/data`.
   - Add variable `DB_DIR=/data`.
   The app seeds default settings + an ad slot on first boot inside `/data`.

5. **Deploy.** Railway builds with Nixpacks and starts Gunicorn. Healthcheck hits
   `/api/health`. When live you get `https://<service>.up.railway.app`.

6. **Point the app + website at the new URL**
   - Flutter: `flutter run --dart-define=API_BASE_URL=https://<service>.up.railway.app/api`
     (and the same for the release build).
   - Update `CORS_ORIGINS` to include that domain.
   - `referral_routes.py` `REF_BASE_URL` → your Railway domain `/ref/`.

---

## Verify after deploy
```
GET  https://<service>.up.railway.app/api/health      → {"status":"ok"}
GET  https://<service>.up.railway.app/                 → website
GET  https://<service>.up.railway.app/download         → CashBee.apk downloads
GET  https://<service>.up.railway.app/admin/login      → admin console
```

## Notes / limits
- **Single worker on purpose.** TinyDB is safe within one process only, so the
  start command uses `--workers 1 --threads 8`. Do not raise workers without
  first migrating to a real database (Postgres) — multiple processes writing the
  same JSON file will corrupt it. This is fine for MVP scale (~10k users).
- **APK in git.** `CashBee_v0.1.apk` (~53 MB) sits in `templates/site/` and ships
  with the deploy. To update the app, replace that file (keep the name, or update
  `APK_FILENAME` in `routes/site_routes.py`) and redeploy.
- **Rate limiting** uses in-memory storage (fine for 1 worker). Add a Redis plugin
  and set `RATELIMIT_STORAGE_URI=redis://...` if you scale out.
