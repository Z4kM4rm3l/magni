"""
scripts/reset_demo_daily.py

Daily cron job that resets the demo client's usage counter at midnight.
Run this separately from reset_monthly_usage.py — demo resets DAILY,
paying clients reset MONTHLY on their billing cycle.

Railway cron schedule (runs daily at midnight UTC):
    0 0 * * * python scripts/reset_demo_daily.py

Usage locally:
    python scripts/reset_demo_daily.py
"""
import os
import sys
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.db import SessionLocal
from core.models import Client
from core.utils import logger

DEMO_CLIENT_ID = "demo"


def reset_demo_usage():
    db = SessionLocal()
    try:
        demo = db.query(Client).filter(Client.id == DEMO_CLIENT_ID).first()
        if not demo:
            logger.warning("Demo client not found — run create_demo_client.py first.")
            return

        prev = demo.monthly_used
        demo.monthly_used = 0
        db.commit()

        logger.info(
            f"Demo client daily reset complete. "
            f"Usage cleared: {prev} -> 0 at {datetime.now(timezone.utc).isoformat()}"
        )
        print(f"Demo usage reset: {prev} conversations cleared.")

    except Exception as e:
        db.rollback()
        logger.error(f"Demo reset failed: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    reset_demo_usage()
