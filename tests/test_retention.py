"""Data retention (chats and "needs a person" records, 12 months) and the
database-side analytics counts. Runs on SQLite by default; set
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

from core.models import Client, Conversation, Message, Escalation
from core.escalations import metrics
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

def esc(cid, eid, when, status="open", email=None):
    d.add(Escalation(id=eid, client_id=cid, session_id="s-" + eid, status=status, contact_email=email,
                     transcript="Visitor: hi", created_at=when, updated_at=when))

# Tenant A: recent activity plus very old history.
conv("a", "a-new", ago(3)); conv("a", "a-11mo", ago(330)); conv("a", "a-13mo", ago(400), n=4)
esc("a", "a-e-new", ago(2), email="v@example.com")
esc("a", "a-e-wait", ago(4), status="collecting")
esc("a", "a-e-done", ago(6), status="closed", email="w@example.com")
esc("a", "a-e-11mo", ago(330), status="closed")
esc("a", "a-e-13mo", ago(400))
# Tenant B: only old things, plus one recent chat.
conv("b", "b-new", ago(1)); conv("b", "b-13mo", ago(390))
esc("b", "b-e-13mo", ago(390), status="closed")
d.commit(); d.close()

# --- Database-side counts ---
m = metrics("a")
check("counts 30-day chats and all-time chats", m["conversations"] == 1 and m["conversations_all_time"] == 3)
check("counts 30-day chats that needed a person", m["escalations"] == 3)
check("counts how many left contact details", m["escalations_with_contact"] == 2)
check("counts the ones still waiting on the owner (any age)", m["escalations_waiting"] == 3)
check("counts are per tenant", metrics("b")["conversations"] == 1 and metrics("b")["escalations"] == 0)

# --- Retention ---
counts = retention.apply_retention(now)
d = _db.SessionLocal()
sids = {s for (s,) in d.query(Conversation.session_id).all()}
check("chats older than 12 months are deleted (both tenants)", sids == {"a-new", "a-11mo", "b-new"})
check("their messages are deleted too",
      d.query(Message).filter(Message.session_id.in_(["a-13mo", "b-13mo"])).count() == 0
      and d.query(Message).filter(Message.session_id == "a-11mo").count() == 2)
eids = {e for (e,) in d.query(Escalation.id).all()}
check("needs-a-person records older than 12 months are deleted, newer kept",
      eids == {"a-e-new", "a-e-wait", "a-e-done", "a-e-11mo"})
check("the run reports what it deleted", counts == {"conversations": 2, "escalations": 2})
d.close()
check("a second run deletes nothing", retention.apply_retention(now) == {"conversations": 0, "escalations": 0})

retention.BATCH = 1
d = _db.SessionLocal()
for i in range(3):
    conv("a", f"old-{i}", ago(500))
d.commit(); d.close()
check("works through a backlog in small batches", retention.apply_retention(now)["conversations"] == 3)

if _PG:  # leave the shared database empty for the next suite
    d = _db.SessionLocal()
    for model in (Message, Escalation, Conversation, Client):
        d.query(model).delete()
    d.commit(); d.close()

print()
if failures:
    print(f"{len(failures)} FAILED")
    sys.exit(1)
print("ALL PASSED")
