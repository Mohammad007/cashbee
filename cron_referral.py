"""
Optional reconciliation cron (hourly).

Referral passive income is credited INSTANTLY in ad_routes.watch-complete, so
this script is only a safety-net reconciler: it verifies that each referral
edge's `coins_earned` matches the sum of referral_earn transactions, which is
useful for catching drift after manual DB edits.

Schedule with cron / Windows Task Scheduler:
    0 * * * *  python /path/to/backend/cron_referral.py
"""
import database.db as db


def reconcile():
    fixed = 0
    for edge in db.referrals_db.all():
        referrer = db.get_user_by_id(edge["referrer_id"])
        referee = db.get_user_by_id(edge["referee_id"])
        if not referrer or not referee:
            continue
        # Sum referral_earn txns for this referrer mentioning this referee.
        total = sum(
            t["coins"]
            for t in db.get_transactions(referrer["id"], type_="referral_earn")
            if referee["phone"] in t.get("description", "")
        )
        if total and total != edge.get("coins_earned", 0):
            db.referrals_db.update(
                {"coins_earned": total},
                (db.Q.referrer_id == referrer["id"])
                & (db.Q.referee_id == referee["id"]),
            )
            fixed += 1
    print(f"Referral reconcile complete. Edges adjusted: {fixed}")


if __name__ == "__main__":
    reconcile()
