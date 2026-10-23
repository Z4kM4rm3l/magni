"""Rental-only cleanup: no "general" business type, rental prompts and intents,
leasing analytics, operator-only tiers, a silent demo account, and the 0007
migration. Run: python tests/test_rental_focus.py
"""
import os, sys, time, uuid
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("PUBLIC_BASE_URL", "https://app.example.com")

import core.llm as _llm
_llm.generate = lambda *a, **k: "stub"
_llm.chat = lambda *a, **k: "ok"

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import core.db as _db
_db.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
_db.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_db.engine)
_db.init_db = lambda: None

import core.models as models
models.Base.metadata.create_all(bind=_db.engine)

import app as appmod
from core import leasing as L, lead_flow, notifier
from core.models import Client, Lead, Appointment, Conversation
from core.leads import leasing_metrics
from agents.triage_agent import INTENTS, TRIAGE_PROMPT
from agents.resolver_agent import MAGNI_SYSTEM_PROMPT
from core.support_flows import SUPPORT_FLOWS

failures = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)

# --- Business types ---
check("only rental business types remain", set(L.VERTICALS) == {"long_term", "seasonal"})
check("unknown or retired type falls back to long-term", L.vertical_of("general") == "long_term" and L.vertical_of(None) == "long_term")

# --- Prompts and intents are rental-only ---
check("triage intents are leasing / resident / general", INTENTS == ("leasing", "resident", "general"))
check("triage prompt has no shipping or order intents", "orders" not in TRIAGE_PROMPT and "shipping" not in TRIAGE_PROMPT)
check("flows are prospect, resident, other", set(SUPPORT_FLOWS) == {"leasing", "resident", "general"})
check("no flow offers refunds", not any("refund" in v.lower() and "process" in v.lower() for v in SUPPORT_FLOWS.values()))
check("resolver declines off-topic work", "writing code" in MAGNI_SYSTEM_PROMPT and "Don't help with anything unrelated" in MAGNI_SYSTEM_PROMPT)
check("resolver forbids invented business facts", "Never make up facts about this business" in MAGNI_SYSTEM_PROMPT)

# --- New accounts get rental defaults ---
c = appmod.app.test_client()
with c.session_transaction() as s:
    s["_csrf_token"] = "t"
c.post("/signup", data={"business_name": "Palm Props", "email": "o@palm.com", "password": "Passw0rd123", "csrf_token": "t"})
d = _db.SessionLocal(); row = d.query(Client).filter(Client.email == "o@palm.com").first(); d.close()
check("new signup is a long-term rental account", row.vertical == "long_term")
check("new signup gets the rental bot name and greeting",
      row.bot_name == "Leasing Assistant" and "book a tour" in (row.welcome_message or ""))
with c.session_transaction() as s:
    s["_csrf_token"] = "t"
r = c.put("/api/portal/leasing", json={"vertical": "general", "config": {}}, headers={"X-CSRF-Token": "t"})
check("portal refuses the retired general type", r.status_code == 400)

# --- Leasing analytics ---
now = datetime.now(timezone.utc)
d = _db.SessionLocal()
cid = row.id
for i in range(4):
    d.add(Conversation(session_id=f"s{i}", client_id=cid, started_at=now - timedelta(days=i), message_count=2))
d.add(Conversation(session_id="old", client_id=cid, started_at=now - timedelta(days=90), message_count=2))
d.add(Lead(id="l1", client_id=cid, session_id="s0", status="complete", answers={"name": "A"}, flags={"after_hours": True}, created_at=now))
d.add(Lead(id="l2", client_id=cid, session_id="s1", status="in_progress", answers={"phone": "x"}, contact_phone="5551234567", created_at=now))
d.add(Lead(id="l3", client_id=cid, session_id="s2", status="in_progress", answers={}, flags={}, created_at=now))
d.add(Appointment(id="a1", client_id=cid, lead_id="l1", start_at=now + timedelta(days=2), end_at=now + timedelta(days=2, minutes=30),
                  status="booked", cancel_token=uuid.uuid4().hex, created_at=now))
d.add(Appointment(id="a2", client_id=cid, start_at=now + timedelta(days=3), end_at=now + timedelta(days=3, minutes=30),
                  status="canceled", cancel_token=uuid.uuid4().hex, created_at=now))
d.add(Client(id="other", api_key="k-other", tier="starter"))
d.add(Lead(id="l9", client_id="other", session_id="z", status="complete", answers={"name": "Z"}, created_at=now))
d.commit(); d.close()
m = leasing_metrics(cid)
check("metrics count 30-day conversations", m["conversations"] == 4 and m["conversations_all_time"] == 5)
check("metrics split finished and partial leads (empty sessions ignored)", m["leads_complete"] == 1 and m["leads_partial"] == 1)
check("metrics count after-hours leads", m["leads_after_hours"] == 1)
check("metrics count tours booked, canceled, upcoming", m["tours_booked"] == 2 and m["tours_canceled"] == 1 and m["tours_upcoming"] == 1)
check("metrics are tenant-scoped", leasing_metrics("other")["leads_complete"] == 1 and leasing_metrics("other")["conversations"] == 0)
with c.session_transaction() as s:
    s["_csrf_token"] = "t"
pa = c.get("/api/portal/analytics").get_json()
check("portal analytics returns leasing metrics", pa.get("tours_booked") == 2 and "resolution_rate" not in pa)

# --- Removed admin AI insights ---
admin = appmod.app.test_client()
with admin.session_transaction() as s:
    s["_csrf_token"] = "t"; s["admin_logged_in"] = True; s["admin_login_time"] = time.time()
check("admin AI insights endpoint is gone", admin.post("/api/insights", json={"prompt": "x"}, headers={"X-CSRF-Token": "t"}).status_code in (404, 405))
check("admin analytics returns leasing metrics", admin.get("/api/analytics?client_id=" + cid).get_json().get("leads_complete") == 1)

# --- Tiers: real tiers only, unlimited for operator accounts only ---
H = {"X-CSRF-Token": "t"}
check("admin can't set a made-up tier", admin.put(f"/api/clients/{cid}", json={"tier": "platinum"}, headers=H).status_code == 400)
check("admin can't make a self-serve account unlimited", admin.put(f"/api/clients/{cid}", json={"tier": "unlimited"}, headers=H).status_code == 400)
r = admin.post("/api/clients", json={"business_name": "Pilot Co", "email": "p@pilot.com", "tier": "unlimited"}, headers=H)
check("operator-created pilot can be unlimited", r.status_code == 201)
check("admin can still set a normal tier", admin.put(f"/api/clients/{cid}", json={"tier": "professional"}, headers=H).status_code == 200)

# --- The demo account never emails anyone ---
SENT = []
notifier.RUN_IN_BACKGROUND = False
notifier.send_email = lambda *a, **k: SENT.append(a) or True
notifier.send_sms = lambda *a, **k: SENT.append(a) or True
import scripts.create_demo_client as demo_mod
demo_mod.create_demo_client()
ctx = lead_flow.load_client_context("demo")
check("demo account is a rental with booking on", ctx["vertical"] == "long_term" and ctx["config"]["schedule"]["enabled"])
check("demo widget is allowed on the app's own domain", _db.SessionLocal().get(Client, "demo").allowed_domains == ["app.example.com"])
fake_lead = Lead(id="dl", client_id="demo", session_id="ds", status="complete", answers={"email": "x@y.com", "name": "X"}, flags={})
lead_flow._send_alert(ctx, fake_lead, "complete")
appt = Appointment(id="da", client_id="demo", start_at=now + timedelta(days=1), end_at=now + timedelta(days=1, minutes=30),
                   status="booked", cancel_token="tok", email="x@y.com", name="X")
lead_flow._send_booking_emails(ctx, appt)
lead_flow.on_appointment_canceled(appt, by="prospect")
check("demo sends no alerts, confirmations or cancel emails", SENT == [])

import scripts.reset_demo_daily as reset_demo
d = _db.SessionLocal()
d.add(Lead(id="dl2", client_id="demo", session_id="ds2", status="complete", answers={"name": "X"}, created_at=now))
d.add(Conversation(session_id="ds2", client_id="demo", started_at=now))
d.get(Client, "demo").monthly_used = 12
d.commit(); d.close()
reset_demo.reset_demo_usage()
d = _db.SessionLocal()
check("daily demo reset clears usage, leads and chats",
      d.get(Client, "demo").monthly_used == 0 and d.query(Lead).filter(Lead.client_id == "demo").count() == 0
      and d.query(Conversation).filter(Conversation.client_id == "demo").count() == 0)
check("daily demo reset leaves other tenants alone", d.query(Lead).filter(Lead.client_id == cid).count() == 3)
d.close()

# --- Migration 0007 on a real Alembic run ---
import tempfile, sqlite3
from alembic.config import Config
from alembic import command
tmp = tempfile.mkdtemp()
url = f"sqlite:///{tmp}/m.db"
os.environ["DATABASE_URL"] = url
cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
cfg.set_main_option("sqlalchemy.url", url)
command.upgrade(cfg, "0006")
con = sqlite3.connect(f"{tmp}/m.db")
con.execute("INSERT INTO clients (id, api_key, vertical, bot_name, welcome_message) VALUES ('g', 'kg', 'general', 'Magni', 'Hi! How can I help you today?')")
con.execute("INSERT INTO clients (id, api_key, vertical, bot_name, welcome_message) VALUES ('s', 'ks', 'seasonal', 'Beach Bot', 'Aloha!')")
con.commit(); con.close()
command.upgrade(cfg, "head")
con = sqlite3.connect(f"{tmp}/m.db")
g = con.execute("SELECT vertical, bot_name, welcome_message FROM clients WHERE id='g'").fetchone()
s_ = con.execute("SELECT vertical, bot_name, welcome_message FROM clients WHERE id='s'").fetchone()
con.close()
check("0007 moves general accounts to long-term with rental defaults",
      g[0] == "long_term" and g[1] == "Leasing Assistant" and "book a tour" in g[2])
check("0007 leaves customized accounts alone", s_ == ("seasonal", "Beach Bot", "Aloha!"))

print()
if failures:
    print(f"{len(failures)} FAILED")
    sys.exit(1)
print("ALL PASSED")
