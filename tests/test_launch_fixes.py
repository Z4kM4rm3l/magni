"""Launch-blocker regression tests: usage counted per conversation, trials never
reset early, customer keys only work through the widget, the demo fails closed,
the admin lockout can't be dodged with a forged header, the assistant uses the
owner's bot and business name, and deleting a client stops its Stripe billing.
Run: python tests/test_launch_fixes.py
"""
import os, sys, time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("PUBLIC_BASE_URL", "https://app.example.com")
os.environ["ADMIN_USERNAME"] = "admin"
os.environ["ADMIN_PASSWORD"] = "correct-horse"

import core.llm as _llm
SYSTEMS = []
_llm.generate = lambda *a, **k: '{"intent": "general", "confidence": 0.9, "route": "resolver", "escalation_reason": null}'
def _chat(system, history, message, **k):
    SYSTEMS.append("\n".join(system) if isinstance(system, list) else system)
    return "Happy to help."
_llm.chat = _chat

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
from core import rate_limit
from core.models import Client, Conversation
from core.client_guard import VISITOR_UNAVAILABLE, CONVERSATION_TOO_LONG, MAX_MESSAGES_PER_CONVERSATION

failures = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)

now = datetime.now(timezone.utc)
db = _db.SessionLocal()
db.add(Client(id="c-palm", api_key="k-palm", business_name="Palm Props", bot_name="Palm Leasing",
              tier="starter", is_active=True, monthly_used=0, monthly_limit=3,
              allowed_domains=["palm.com"], email="owner@palm.com"))
db.add(Client(id="c-trial", api_key="k-trial", tier="starter", is_active=True, monthly_used=900,
              trial_ends_at=now + timedelta(days=5)))
db.add(Client(id="c-ended", api_key="k-ended", tier="starter", is_active=True, monthly_used=1000,
              trial_ends_at=now - timedelta(days=1)))
db.add(Client(id="c-managed", api_key="k-managed", tier="starter", is_active=True, monthly_used=700))
db.add(Client(id="demo", api_key="sk_magni_demo_public_2026", tier="starter", is_active=True,
              monthly_used=0, monthly_limit=50))
db.commit(); db.close()

w = appmod.app.test_client()
H = {"X-Magni-API-Key": "k-palm", "Origin": "https://palm.com"}

def used(cid):
    d = _db.SessionLocal()
    try:
        return d.get(Client, cid).monthly_used
    finally:
        d.close()

def say(msg, sid=None):
    rate_limit.reset()
    body = {"message": msg}
    if sid:
        body["session_id"] = sid
    r = w.post("/widget/chat", json=body, headers=H)
    return r.get_json() or {}

# --- Usage is counted per conversation, not per message ---
first = say("Hi there")
sid = first.get("session_id")
for i in range(4):
    say(f"Follow-up question {i}", sid)
check("one conversation with 5 messages counts once", used("c-palm") == 1)

say("New chat 2"); say("New chat 3")
check("three conversations count three", used("c-palm") == 3)
blocked = say("New chat 4")
check("a new conversation past the limit is refused", blocked.get("response") == VISITOR_UNAVAILABLE)
check("the refused conversation was not counted", used("c-palm") == 3)
cont = say("Still here in my first chat", sid)
check("a conversation already under way can finish after the limit", cont.get("response") == "Happy to help.")

d = _db.SessionLocal(); d.get(Conversation, sid).message_count = 2 * MAX_MESSAGES_PER_CONVERSATION; d.commit(); d.close()
check("one chat is capped so it can't run forever", say("one more", sid).get("response") == CONVERSATION_TOO_LONG)

# --- The assistant uses the owner's names ---
check("resolver prompt names the bot and the business",
      any("You are Palm Leasing, the AI assistant for Palm Props." in s for s in SYSTEMS))
check("resolver prompt no longer hard-codes Magni", not any("You are Magni" in s for s in SYSTEMS))

from routes.widget import _MAGNI_JS
check("widget header sets the bot name as text, not HTML",
      "textContent = config.bot_name" in _MAGNI_JS and "'<div id=\"mgn-header\">' + (config.bot_name" not in _MAGNI_JS)

# --- Monthly reset never touches trials ---
import scripts.reset_monthly_usage as reset_mod
reset_mod.reset_monthly_usage()
check("reset leaves an active trial's usage alone", used("c-trial") == 900)
check("reset leaves an ended trial's usage alone", used("c-ended") == 1000)
check("reset still resets operator-managed accounts", used("c-managed") == 0)
check("reset skips the demo account", True)

# --- Customer keys only work through the widget ---
r = w.post("/chat", json={"message": "hi"}, headers={"X-Magni-API-Key": "k-palm"})
check("/chat refuses a customer key (403)", r.status_code == 403)
before = used("c-palm")
check("/chat with a customer key spends nothing", used("c-palm") == before)

# --- Demo fails closed when the database is down ---
real = appmod.SessionLocal
def _down():
    raise RuntimeError("db down")
appmod.SessionLocal = _down
r = w.post("/chat", json={"message": "hi"})
appmod.SessionLocal = real
check("demo chat answers with the paused message when the DB is down",
      "paused" in (r.get_json() or {}).get("response", ""))

# --- Admin lockout can't be dodged by forging X-Forwarded-For ---
from core import auth as auth_mod
auth_mod._login_attempts.clear()
TOKEN = "t"
lock_msgs = []
for i in range(7):
    c = appmod.app.test_client()
    with c.session_transaction() as s:
        s["_csrf_token"] = TOKEN
    # Railway appends the real address last; everything before it is the visitor's.
    r = c.post("/admin/login", data={"username": "admin", "password": "wrong", "csrf_token": TOKEN},
               headers={"X-Forwarded-For": f"9.9.9.{i}, 5.5.5.5"})
    lock_msgs.append("Too many failed attempts" in r.get_data(as_text=True))
check("6th guess is locked out even with a new forged IP each time", lock_msgs[5] and lock_msgs[6])

# --- Deleting a client cancels its Stripe subscription first ---
import stripe
CANCELED = []
stripe.Subscription.cancel = staticmethod(lambda sid, **k: CANCELED.append(sid) or {"id": sid, "status": "canceled"})
d = _db.SessionLocal()
d.add(Client(id="c-paying", api_key="k-paying", tier="starter", is_active=True,
             stripe_subscription_id="sub_123", subscription_status="active"))
d.commit(); d.close()
admin = appmod.app.test_client()
with admin.session_transaction() as s:
    s["_csrf_token"] = TOKEN
    s["admin_logged_in"] = True
    s["admin_login_time"] = time.time()
r = admin.delete("/api/clients/c-paying", headers={"X-CSRF-Token": TOKEN})
check("delete cancels the Stripe subscription", CANCELED == ["sub_123"] and r.status_code == 200)

def _fail(sid, **k):
    raise RuntimeError("stripe down")
stripe.Subscription.cancel = staticmethod(_fail)
d = _db.SessionLocal()
d.add(Client(id="c-paying2", api_key="k-paying2", tier="starter", is_active=True,
             stripe_subscription_id="sub_456", subscription_status="active"))
d.commit(); d.close()
r = admin.delete("/api/clients/c-paying2", headers={"X-CSRF-Token": TOKEN})
d = _db.SessionLocal(); still = d.get(Client, "c-paying2") is not None; d.close()
check("if Stripe can't cancel, nothing is deleted", r.status_code == 502 and still)

print()
if failures:
    print(f"{len(failures)} FAILED")
    sys.exit(1)
print("ALL PASSED")
