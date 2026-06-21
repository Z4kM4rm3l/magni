#!/usr/bin/env python3
"""
scripts/reset_monthly_usage.py

Standalone billing reset worker. No Flask imports. No app context.
Run directly: python scripts/reset_monthly_usage.py

Two reset paths:
  1. Stripe-managed clients — resets monthly_used only when Stripe confirms
     the billing period has rolled over.
  2. Manually-managed clients (stripe_subscription_id IS NULL, id != 'demo')
     — resets unconditionally on every run. Billing dates managed externally.
"""
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import stripe
from sqlalchemy.orm import Session
from core.db import SessionLocal
from core.models import Client

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")


def reset_monthly_usage() -> None:
    db: Session = SessionLocal()
    now = datetime.now(timezone.utc)
    stripe_reset = 0
    stripe_skip = 0
    stripe_error = 0
    manual_reset = 0

    print(f"[{now.isoformat()}] Billing reset worker starting.")

    try:
        # ── PATH 1: Stripe-managed clients ───────────────────────────────────
        expired = db.query(Client).filter(
            Client.is_active == True,
            Client.stripe_subscription_id.isnot(None),
            Client.billing_period_end <= now,
        ).all()

        print(f"  Stripe path: {len(expired)} client(s) with expired billing periods.")

        for client in expired:
            try:
                sub = stripe.Subscription.retrieve(client.stripe_subscription_id)
                stripe_end = datetime.fromtimestamp(sub.current_period_end, tz=timezone.utc)

                if stripe_end > client.billing_period_end:
                    client.monthly_used = 0
                    client.billing_period_start = datetime.fromtimestamp(
                        sub.current_period_start, tz=timezone.utc
                    )
                    client.billing_period_end = stripe_end
                    db.add(client)
                    stripe_reset += 1
                    print(f"    RESET  {client.id[:8]} ({client.business_name or 'unnamed'})"
                          f" — new period ends {stripe_end.date()}")
                else:
                    stripe_skip += 1
                    print(f"    SKIP   {client.id[:8]} — Stripe period not yet advanced, retry next run")

            except stripe.StripeError as e:
                stripe_error += 1
                print(f"    ERROR  {client.id[:8]} — Stripe error: {e}", file=sys.stderr)

        # ── PATH 2: Manually-managed clients (no Stripe subscription) ────────
        manual_clients = db.query(Client).filter(
            Client.is_active == True,
            Client.stripe_subscription_id.is_(None),
            Client.id != "demo",
        ).all()

        print(f"  Manual path: {len(manual_clients)} pilot client(s) — resetting unconditionally.")

        for client in manual_clients:
            client.monthly_used = 0
            db.add(client)
            manual_reset += 1
            print(f"    RESET  {client.id[:8]} ({client.business_name or 'unnamed'})")

        db.commit()
        print(
            f"\nDone. Stripe resets: {stripe_reset}  Stripe skipped: {stripe_skip}"
            f"  Stripe errors: {stripe_error}  Manual resets: {manual_reset}"
        )

    except Exception as e:
        db.rollback()
        print(f"CRITICAL: reset worker failed — {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    reset_monthly_usage()
