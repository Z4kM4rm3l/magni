"""
scripts/create_demo_client.py

Run this ONCE to create the locked-down demo client account in your
PostgreSQL database. This gives recruiters and visitors a real Magni
experience without any risk to your API budget.

Usage:
    cd "C:\\Users\\zmarm\\OneDrive\\Desktop\\Magni"
    python scripts/create_demo_client.py

What it creates:
    - A client row with id="demo" in your clients table
    - Hard cap of 50 conversations per day (resets daily via cron)
    - is_active=True, no Stripe subscription needed
    - Uses your managed Gemini key (not BYO)
"""
import os
import sys
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.db import SessionLocal, init_db
from core.models import Client

DEMO_CLIENT_ID   = "demo"
DEMO_API_KEY     = "sk_magni_demo_public_2026"   # hardcoded, not secret
DEMO_DAILY_LIMIT = 50                             # total across ALL visitors per day
DEMO_TIER        = "starter"


def create_demo_client():
    print("=" * 55)
    print("  Magni Demo Client Setup")
    print("=" * 55)

    db = SessionLocal()
    try:
        # Check if already exists
        existing = db.query(Client).filter(Client.id == DEMO_CLIENT_ID).first()
        if existing:
            print(f"\n⚠️  Demo client already exists.")
            print(f"   Current usage: {existing.monthly_used}/{existing.monthly_limit}")
            print(f"   Status: {'Active' if existing.is_active else 'Suspended'}")
            reset = input("\nReset usage counter to 0? (y/N): ").strip().lower()
            if reset == 'y':
                existing.monthly_used = 0
                db.commit()
                print("✅ Usage counter reset.")
            return

        demo = Client(
            id=DEMO_CLIENT_ID,
            api_key=DEMO_API_KEY,
            tier=DEMO_TIER,
            is_active=True,
            monthly_used=0,
            monthly_limit=DEMO_DAILY_LIMIT,
            stripe_customer_id=None,
            stripe_subscription_id=None,
            billing_period_start=datetime.now(timezone.utc),
            billing_period_end=None,
        )

        db.add(demo)
        db.commit()

        print(f"\n✅ Demo client created successfully!")
        print(f"   Client ID : {DEMO_CLIENT_ID}")
        print(f"   API Key   : {DEMO_API_KEY}")
        print(f"   Daily Cap : {DEMO_DAILY_LIMIT} conversations")
        print(f"\nNext steps:")
        print(f"  1. Set MAGNI_DEMO_API_KEY={DEMO_API_KEY} in Railway variables")
        print(f"  2. Update your /chat route to use demo client for unauthenticated visits")
        print(f"  3. Add daily reset cron job (see scripts/reset_demo_daily.py)")

    except Exception as e:
        db.rollback()
        print(f"\n❌ Error: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    init_db()
    create_demo_client()
