"""
scripts/create_demo_client.py

Creates (or refreshes) the public demo account shown on the landing page:
a sample coffee shop and online store with a small knowledge base, so visitors
can try real customer service answers and a handoff to a person in the widget.

Usage (Railway shell or locally with the production DATABASE_URL):
    python scripts/create_demo_client.py

Safe to run again: it updates the demo's settings and replaces its sample
articles. The demo never emails or texts anyone (see core/escalations.py), its
usage is capped at DEMO_DAILY_LIMIT conversations a day, and
scripts/reset_demo_daily.py clears its usage and chats every day.
"""
import os
import sys
import uuid
import secrets
from datetime import datetime, timezone
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.db import SessionLocal, init_db
from core.models import Client, KnowledgeArticle

DEMO_CLIENT_ID = "demo"
DEMO_DAILY_LIMIT = 50  # conversations per day across all visitors

DEMO_ARTICLES = [
    ("Hours and location",
     "Harbor Roasters cafe is at 12 Wharf Street. Open Monday to Friday 7am to 5pm, Saturday "
     "and Sunday 8am to 3pm. Closed on Thanksgiving and Christmas Day. Street parking is free "
     "for two hours."),
    ("Coffee and pricing",
     "We roast in small batches every Tuesday and Friday. 12 oz bags: House Blend $16, "
     "Dark Harbor (dark roast) $17, Morning Tide (light roast) $17, Decaf $17. Whole bean or "
     "ground to order. The coffee subscription is $15 per bag, delivered every 2 or 4 weeks, "
     "and can be paused or canceled any time from the account page."),
    ("Shipping and delivery",
     "Orders ship within 2 business days by USPS Priority, and usually arrive in 2 to 4 business "
     "days. Shipping is $6 flat, and free on orders of $40 or more. We ship within the United "
     "States only. A tracking link is emailed when the order ships."),
    ("Returns and refunds",
     "Unopened bags can be returned within 30 days for a full refund. If a bag arrives damaged "
     "or you don't love the coffee, contact us within 30 days and we'll replace it or refund "
     "it. Refunds go back to the original payment method within 5 to 7 business days. Our team "
     "handles every return and refund personally."),
    ("Contacting the team",
     "The team answers messages Monday to Friday, 9am to 4pm. For anything about a specific "
     "order, have your order number ready."),
]


def _demo_domain() -> list[str]:
    host = urlparse(os.getenv("PUBLIC_BASE_URL", "")).hostname or ""
    return [host] if host else ["localhost"]


def create_demo_client():
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        demo = db.get(Client, DEMO_CLIENT_ID)
        if not demo:
            demo = Client(id=DEMO_CLIENT_ID, api_key=f"sk_magni_demo_{secrets.token_hex(12)}",
                          created_at=now, monthly_used=0)
            db.add(demo)
        demo.business_name = "Harbor Roasters (demo)"
        demo.email = None  # no account email, so no alerts go anywhere
        demo.tier = "starter"
        demo.is_active = True
        demo.monthly_limit = DEMO_DAILY_LIMIT
        demo.trial_ends_at = None
        demo.notify_email = None
        demo.notify_phone = None
        demo.bot_name = "Harbor Helper"
        demo.primary_color = "#b45309"
        demo.welcome_message = "Hi! Questions about our coffee, shipping or an order? I can help."
        demo.allowed_domains = _demo_domain()

        db.query(KnowledgeArticle).filter(KnowledgeArticle.client_id == DEMO_CLIENT_ID).delete()
        for title, content in DEMO_ARTICLES:
            db.add(KnowledgeArticle(id=uuid.uuid4().hex, client_id=DEMO_CLIENT_ID, title=title,
                                    content=content, summary=content[:200], category="general",
                                    created_at=now, updated_at=now))
        db.commit()
        print(f"Demo account ready: {demo.business_name}, widget allowed on {demo.allowed_domains}, "
              f"{DEMO_DAILY_LIMIT} conversations/day.")
        print("Schedule scripts/reset_demo_daily.py once a day to clear its usage and chats.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    init_db()
    create_demo_client()
