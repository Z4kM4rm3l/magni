"""
scripts/reset_demo_daily.py

Daily cron job for the landing-page demo: resets its usage counter and clears
the leads, bookings and chats visitors left behind, so the demo calendar stays
open. Run this separately from reset_monthly_usage.py: the demo resets DAILY,
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
from core.models import Client, Lead, Appointment, Conversation, Message
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
        db.query(Appointment).filter(Appointment.client_id == DEMO_CLIENT_ID).delete()
        db.query(Lead).filter(Lead.client_id == DEMO_CLIENT_ID).delete()
        demo_sessions = db.query(Conversation.session_id).filter(Conversation.client_id == DEMO_CLIENT_ID)
        db.query(Message).filter(Message.session_id.in_(demo_sessions.scalar_subquery())).delete(synchronize_session=False)
        db.query(Conversation).filter(Conversation.client_id == DEMO_CLIENT_ID).delete(synchronize_session=False)
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
