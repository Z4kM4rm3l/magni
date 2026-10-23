"""
scripts/create_demo_client.py

Creates (or refreshes) the public demo account shown on the landing page:
a sample apartment community with a small knowledge base and online booking,
so visitors can try a real intake and tour booking in the widget.

Usage (Railway shell or locally with the production DATABASE_URL):
    python scripts/create_demo_client.py

Safe to run again: it updates the demo's settings and replaces its sample
articles. The demo never emails or texts anyone (see core/lead_flow.py), its
usage is capped at DEMO_DAILY_LIMIT conversations a day, and
scripts/reset_demo_daily.py clears its usage, leads and bookings every day.
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
from core.leasing import normalize_lead_config, default_lead_config
from core.scheduling import normalize_schedule

DEMO_CLIENT_ID = "demo"
DEMO_DAILY_LIMIT = 50  # conversations per day across all visitors

DEMO_ARTICLES = [
    ("Available units and pricing",
     "Sunset Palms has two floor plans available now.\n"
     "- Unit 2B: 2 bedrooms, 2 baths, 1,050 sq ft, $2,150/month, available now.\n"
     "- Unit 1A: 1 bedroom, 1 bath, 720 sq ft, $1,650/month, available December 1.\n"
     "Leases are 12 months. Security deposit is one month's rent. Application fee is $75 per adult."),
    ("Pets",
     "Cats and dogs under 50 lbs are welcome, up to two per home. Pet fee is $300 one-time and "
     "pet rent is $35/month per pet. Assistance animals are not pets and have no pet fees."),
    ("Parking and amenities",
     "Each home includes one covered parking space; a second space is $50/month. Amenities: "
     "pool, fitness center, package lockers, in-unit washer and dryer."),
    ("How to apply",
     "Apply online at the link the assistant shares. Our posted rental criteria are on the "
     "application page. The leasing office is at 100 Sunset Palms Blvd, open Mon-Fri 9-5 "
     "and Sat 10-2."),
]


def _demo_domain() -> list[str]:
    host = urlparse(os.getenv("PUBLIC_BASE_URL", "")).hostname or ""
    return [host] if host else ["localhost"]


def create_demo_client():
    now = datetime.now(timezone.utc)
    config = default_lead_config("long_term")
    config.update({
        "rental_criteria": "Income of 3x the monthly rent, and a background check for each adult.",
        "application_url": "https://example.com/apply",
        "notify_email": "",
        "notify_phone": "",
        "schedule": normalize_schedule({
            "enabled": True,
            "timezone": "America/New_York",
            "slot_minutes": 30,
            "min_notice_hours": 2,
            "max_days_ahead": 14,
            "details": "Meet us at the leasing office, 100 Sunset Palms Blvd. (This is a demo; no one will be there.)",
        }),
    })
    config = normalize_lead_config(config, "long_term")

    db = SessionLocal()
    try:
        demo = db.get(Client, DEMO_CLIENT_ID)
        if not demo:
            demo = Client(id=DEMO_CLIENT_ID, api_key=f"sk_magni_demo_{secrets.token_hex(12)}",
                          created_at=now, monthly_used=0)
            db.add(demo)
        demo.business_name = "Sunset Palms (demo)"
        demo.email = None  # no account email, so no alerts go anywhere
        demo.tier = "starter"
        demo.is_active = True
        demo.monthly_limit = DEMO_DAILY_LIMIT
        demo.trial_ends_at = None
        demo.vertical = "long_term"
        demo.lead_config = config
        demo.bot_name = "Leasing Assistant"
        demo.primary_color = "#b45309"
        demo.welcome_message = "Hi! Looking for a place at Sunset Palms? I can check availability and book a tour."
        demo.allowed_domains = _demo_domain()

        db.query(KnowledgeArticle).filter(KnowledgeArticle.client_id == DEMO_CLIENT_ID).delete()
        for title, content in DEMO_ARTICLES:
            db.add(KnowledgeArticle(id=uuid.uuid4().hex, client_id=DEMO_CLIENT_ID, title=title,
                                    content=content, summary=content[:200], category="general",
                                    created_at=now, updated_at=now))
        db.commit()
        print(f"Demo account ready: {demo.business_name}, widget allowed on {demo.allowed_domains}, "
              f"{DEMO_DAILY_LIMIT} conversations/day.")
        print("Schedule scripts/reset_demo_daily.py once a day to clear its usage, leads and bookings.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    init_db()
    create_demo_client()
