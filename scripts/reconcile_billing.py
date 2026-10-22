#!/usr/bin/env python3
"""
scripts/reconcile_billing.py

Re-syncs every client's subscription state from Stripe (the same logic the
webhook uses), as a safety net for missed webhooks.

It also lists accounts that look paying in Stripe but are suspended in Magni.
The pre-MVP billing code suspended accounts (is_active = False) on a failed
charge and never undid it. Review them, then pass --reactivate to turn them back
on. Accounts you suspended on purpose would be listed too, which is why
reactivation is opt-in.

Run:  python scripts/reconcile_billing.py              # sync + report
      python scripts/reconcile_billing.py --reactivate # also re-enable the listed accounts
"""
import os
import sys

from dotenv import load_dotenv
load_dotenv()
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import stripe
from core.db import SessionLocal
from core.models import Client
from routes.billing import retrieve_subscription, apply_subscription, LIVE_STATUSES


def main(reactivate: bool) -> int:
    if not os.getenv("STRIPE_SECRET_KEY"):
        print("STRIPE_SECRET_KEY is not set.")
        return 1
    db = SessionLocal()
    synced, errors, suspended_paying = 0, 0, []
    try:
        for c in db.query(Client).filter(Client.stripe_subscription_id.isnot(None)).all():
            try:
                apply_subscription(db, retrieve_subscription(c.stripe_subscription_id))
                db.commit()
                synced += 1
            except stripe.StripeError as e:
                db.rollback()
                errors += 1
                print(f"  ! {c.id[:8]} {c.business_name}: {e}")
            if not c.is_active and (c.subscription_status or "") in LIVE_STATUSES:
                suspended_paying.append(c)

        print(f"Synced {synced} subscription(s), {errors} error(s).")
        if suspended_paying:
            print("Paying in Stripe but suspended in Magni:")
            for c in suspended_paying:
                print(f"  - {c.id[:8]}  {c.business_name}  <{c.email}>  status={c.subscription_status}")
            if reactivate:
                for c in suspended_paying:
                    c.is_active = True
                db.commit()
                print(f"Reactivated {len(suspended_paying)} account(s).")
            else:
                print("Rerun with --reactivate to turn these back on.")
        return 0 if errors == 0 else 2
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main("--reactivate" in sys.argv))
