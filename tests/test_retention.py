"""Data retention (chats 12 months, leads and appointments 24 months) and the
database-side leasing counts. Runs on SQLite by default; set
MAGNI_TEST_DATABASE_URL to an empty, migrated Postgres to run it there.
Run: python tests/test_retention.py
"""
import os, sys, uuid
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("PUBLIC_BASE_URL", "https://app.example.com")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import core.db as _db
_PG = os.getenv("MAGNI_TEST_DATABASE_URL")
_db.engine = create_engine(_PG, pool_pre_ping=True) if _PG else \
    create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
_db.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_db.engine)
_db.init_db = lambda: None
import core.models as models
if not _PG:
    models.Base.metadata.create_all(bind=_db.engine)

from core.models import Client, Conversation, Message, Lead, Appointment
from core.leads import leasing_metrics
from core import retention

failures = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)

now = datetime.now(timezone.utc)
ago = lambda days: now - timedelta(days=days)
d = _db.SessionLocal()
for cid in ("a", "b"):
    d.add(Client(id=cid, api_key="k-" + cid, tier="starter", is_active=True, monthly_used=0))
d.commit()

def conv(cid, sid, when, n=2):
    d.add(Conversation(session_id=sid, client_id=cid, started_at=when, message_count=n))
    d.flush()
    for i in range(n):
        d.add(Message(session_id=sid, role="user" if i % 2 == 0 else "assistant", content=f"m{i}", timestamp=when))

def lead(cid, lid, when, status="complete", phone=None, flags=None):
    d.add(Lead(id=lid, client_id=cid, session_id="s-" + lid, status=status, answers={"name": "X"},
               flags=flags or {}, contact_phone=phone, created_at=when, updated_at=when))
    d.flush()  # Postgres enforces appointments.lead_id -> leads.id

def appt(cid, aid, start, status="booked", created=None, lead_id=None):
    d.add(Appointment(id=aid, client_id=cid, lead_id=lead_id, start_at=start, end_at=start + timedelta(minutes=30),
                      status=status, cancel_token=uuid.uuid4().hex, created_at=created or start))

# Tenant A: recent activity plus very old history.
conv("a", "a-new", ago(3)); conv("a", "a-11mo", ago(330)); conv("a", "a-13mo", ago(400), n=4)
lead("a", "a-l-new", ago(2), flags={"after_hours": True})
lead("a", "a-l-partial", ago(5), status="in_progress", phone="(239) 555-0100", flags={"after_hours": True})
lead("a", "a-l-empty", ago(5), status="in_progress", flags={"accommodation_request": True})
lead("a", "a-l-23mo", ago(700)); lead("a", "a-l-25mo", ago(760))
appt("a", "a-ap-up", now + timedelta(days=2), created=ago(1), lead_id="a-l-new")
appt("a", "a-ap-cx", now + timedelta(days=4), status="canceled", created=ago(1))
appt("a", "a-ap-23mo", ago(700)); appt("a", "a-ap-25mo", ago(760), lead_id="a-l-25mo")
# Tenant B: only old things, plus one recent chat.
conv("b", "b-new", ago(1)); conv("b", "b-13mo", ago(390))
lead("b", "b-l-25mo", ago(800))
d.commit(); d.close()

# --- Database-side counts ---
m = leasing_metrics("a")
check("counts 30-day chats and all-time chats", m["conversations"] == 1 and m["conversations_all_time"] == 3)
check("finished vs partial leads (an empty intake isn't a lead)", m["leads_complete"] == 1 and m["leads_partial"] == 1)
check("after-hours count reads the JSON flag", m["leads_after_hours"] == 2)
check("accommodation count reads the JSON flag", m["accommodation_requests"] == 1)
check("tours booked, canceled, upcoming", (m["tours_booked"], m["tours_canceled"], m["tours_upcoming"]) == (2, 1, 1))
check("lead rate", m["lead_rate"] == 200)
check("counts are per tenant", leasing_metrics("b")["conversations"] == 1 and leasing_metrics("b")["leads_complete"] == 0)

# --- Retention ---
counts = retention.apply_retention(now)
d = _db.SessionLocal()
sids = {s for (s,) in d.query(Conversation.session_id).all()}
check("chats older than 12 months are deleted (both tenants)", sids == {"a-new", "a-11mo", "b-new"})
check("their messages are deleted too",
      d.query(Message).filter(Message.session_id.in_(["a-13mo", "b-13mo"])).count() == 0
      and d.query(Message).filter(Message.session_id == "a-11mo").count() == 2)
lids = {l for (l,) in d.query(Lead.id).all()}
check("leads older than 24 months are deleted", "a-l-25mo" not in lids and "b-l-25mo" not in lids)
check("leads under 24 months are kept", {"a-l-new", "a-l-partial", "a-l-23mo"} <= lids)
aids = {a for (a,) in d.query(Appointment.id).all()}
check("appointments older than 24 months are deleted, newer kept", aids == {"a-ap-up", "a-ap-cx", "a-ap-23mo"})
check("the run reports what it deleted", counts == {"conversations": 2, "leads": 2, "appointments": 1})
d.close()
check("a second run deletes nothing", retention.apply_retention(now) == {"conversations": 0, "leads": 0, "appointments": 0})

retention.BATCH = 1
d = _db.SessionLocal()
for i in range(3):
    conv("a", f"old-{i}", ago(500))
d.commit(); d.close()
check("works through a backlog in small batches", retention.apply_retention(now)["conversations"] == 3)

if _PG:  # leave the shared database empty for the next suite
    d = _db.SessionLocal()
    for model in (Message, Appointment, Lead, Conversation, Client):
        d.query(model).delete()
    d.commit(); d.close()

print()
if failures:
    print(f"{len(failures)} FAILED")
    sys.exit(1)
print("ALL PASSED")
