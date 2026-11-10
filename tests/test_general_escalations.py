"""General (any business) accounts and the "needs a person" path: general
prompts and defaults, contact capture after an escalation, fail-safe routing
when the model is down, the durable record + alert retries, and the portal API.

Flask test client on an isolated SQLite DB. The LLM, SMTP and Twilio are
stubbed. Run: python tests/test_general_escalations.py
"""
import os, sys, json
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("PUBLIC_BASE_URL", "https://app.example.com")

# ── LLM stub, routed by system prompt ───────────────────────────────────────
STUB = {
    "triage_intent": "product", "triage_route": "resolver", "triage_raises": False,
    "resolver": "We're open 9 to 5.", "resolver_raises": False,
    "esc": {"action": "collect_contact", "message_to_user": "I'm passing this to the team.",
            "alert_client": True, "reason": "wants a refund"},
    "esc_raises": False,
    "triage_systems": [], "resolver_prompts": [], "calls": 0,
}
import core.llm as llm
def _stub_generate(prompt, system=None, **kw):
    STUB["calls"] += 1
    si = system or ""
    if "triage classifier" in si:
        STUB["triage_systems"].append(si)
        if STUB["triage_raises"]:
            raise RuntimeError("llm down")
        return json.dumps({"intent": STUB["triage_intent"], "confidence": 0.9,
                           "route": STUB["triage_route"], "escalation_reason": "triage reason"})
    if "escalation handler" in si:
        if STUB["esc_raises"]:
            raise RuntimeError("llm down")
        return json.dumps(STUB["esc"])
    if "extract answers" in si:
        return json.dumps({"answers": {}, "asked_question": False, "needs_human": False, "declined": False})
    return "stub summary"
def _stub_chat(system, history, message, **kw):
    STUB["calls"] += 1
    STUB["resolver_prompts"].append("\n\n".join(system) if isinstance(system, list) else system)
    if STUB["resolver_raises"]:
        raise RuntimeError("llm down")
    return STUB["resolver"]
llm.generate = _stub_generate
llm.chat = _stub_chat

# ── Isolated SQLite ─────────────────────────────────────────────────────────
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import core.db as _db
_PG = os.getenv("MAGNI_TEST_DATABASE_URL")
if _PG:
    _db.engine = create_engine(_PG, pool_pre_ping=True)
else:
    _db.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
_db.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_db.engine)
_db.init_db = lambda: None
import core.models as models
if not _PG:
    models.Base.metadata.create_all(bind=_db.engine)

import app as appmod
from core import leasing as L, notifier, rate_limit, escalations as E, lead_flow
from core.i18n import t
from core.models import Client, Escalation, Lead
from agents.triage_agent import GENERAL_TRIAGE_PROMPT, GENERAL_INTENTS
from agents.resolver_agent import GENERAL_SYSTEM_PROMPT, HANDOFF_TAG
from core.support_flows import GENERAL_FLOWS

notifier.RUN_IN_BACKGROUND = False
SENT = {"email": [], "sms": []}
MAIL = {"ok": True}
def _send_email(to, subj, body, reply_to=None, **kw):
    if not MAIL["ok"]:
        return False
    SENT["email"].append((to, subj, body, reply_to))
    return True
notifier.send_email = _send_email
notifier.send_sms = lambda to, body: SENT["sms"].append((to, body)) or True

failures = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)

TOKEN = "test-csrf-token"
def signup(email, name=None):
    c = appmod.app.test_client()
    with c.session_transaction() as s:
        s["_csrf_token"] = TOKEN
    r = c.post("/signup", data={"business_name": name or email.split("@")[0], "email": email,
                                "password": "Passw0rd123", "csrf_token": TOKEN})
    assert r.status_code in (302, 303), r.status_code
    with c.session_transaction() as s:
        s["_csrf_token"] = TOKEN
    return c
H = {"X-CSRF-Token": TOKEN}

def row(email):
    db = _db.SessionLocal()
    try:
        return db.query(Client).filter(Client.email == email).first()
    finally:
        db.close()

widget = appmod.app.test_client()
def wchat(key, msg, sid=None):
    rate_limit.reset()
    body = {"message": msg}
    if sid:
        body["session_id"] = sid
    r = widget.post("/widget/chat", json=body, headers={"X-Magni-API-Key": key})
    d = r.get_json() or {}
    return d.get("response", ""), d.get("session_id")

def esc_for(sid):
    db = _db.SessionLocal()
    try:
        return db.query(Escalation).filter(Escalation.session_id == sid).order_by(Escalation.created_at.desc()).first()
    finally:
        db.close()

def esc_count(sid):
    db = _db.SessionLocal()
    try:
        return db.query(Escalation).filter(Escalation.session_id == sid).count()
    finally:
        db.close()

def reset(**kw):
    SENT["email"].clear(); SENT["sms"].clear(); MAIL["ok"] = True
    STUB.update(triage_intent="product", triage_route="resolver", triage_raises=False,
                resolver="We're open 9 to 5.", resolver_raises=False, esc_raises=False,
                esc={"action": "collect_contact", "message_to_user": "I'm passing this to the team.",
                     "alert_client": True, "reason": "wants a refund"})
    STUB.update(kw)

A = signup("owner@acme.com", "Acme Bikes")
B = signup("owner@beta.com", "Beta Bakery")
A_KEY, B_KEY = row("owner@acme.com").api_key, row("owner@beta.com").api_key
A_ID, B_ID = row("owner@acme.com").id, row("owner@beta.com").id

# ════════════════════════════════════════════════════════════════════════════
print("\n-- general is the default business type --")
a = row("owner@acme.com")
check("new signup is a general account", a.vertical == "general")
check("general defaults: bot name and greeting", a.bot_name == "Assistant" and a.welcome_message == "Hi! How can I help you today?")
cfg = widget.get("/widget/config?key=" + A_KEY).get_json()
check("widget config carries the general defaults", cfg["bot_name"] == "Assistant" and cfg["welcome_message"] == "Hi! How can I help you today?")
js = widget.get("/widget/magni.js").get_data(as_text=True)
check("widget script has no leasing wording", "Leasing" not in js and "book a tour" not in js)
check("general accounts have no intake questions", L.normalize_lead_config(None, "general")["questions"] == [])
check("general triage intents", GENERAL_INTENTS == ("product", "account", "general"))
check("general flows cover product, account, other", set(GENERAL_FLOWS) == {"product", "account", "general"})

reset()
resp, sid = wchat(A_KEY, "What are your hours?")
check("general chat is answered by the resolver", resp == "We're open 9 to 5.")
check("general account uses the general triage prompt", STUB["triage_systems"][-1] == GENERAL_TRIAGE_PROMPT)
p = STUB["resolver_prompts"][-1]
check("general resolver prompt: customer service, not rentals",
      GENERAL_SYSTEM_PROMPT in p and "rental business" not in p and "FAIR HOUSING" not in p)
check("resolver introduces itself by the owner's names", "You are Assistant, the AI assistant for Acme Bikes." in p)
check("resolver prompt carries the handoff rule", HANDOFF_TAG in p)
check("no escalation for an ordinary question", esc_for(sid) is None and not SENT["email"])

reset(resolver="This place is great for families.")
resp, _ = wchat(A_KEY, "Is it nice?")
check("general account: no Fair Housing filter on replies", resp == "This place is great for families.")

print("\n-- switching to a rental type turns the leasing mode on --")
r = B.put("/api/portal/leasing", json={"vertical": "long_term", "config": {}}, headers=H)
b = row("owner@beta.com")
check("portal can switch an account to a rental type", r.status_code == 200 and b.vertical == "long_term")
check("uncustomized assistant picks up the rental name", b.bot_name == "Leasing Assistant")
reset(triage_intent="general", resolver="This home is great for families.")
resp, _ = wchat(B_KEY, "Tell me about the area")
check("rental account: Fair Housing filter still replaces steering replies", resp == L.SAFE_FALLBACK)
check("rental account still uses the rental prompt with Fair Housing rules",
      "FAIR HOUSING" in STUB["resolver_prompts"][-1] and "rental business" in STUB["resolver_prompts"][-1])
appmod.update_client(b.id, bot_name="Bea")
r = B.put("/api/portal/leasing", json={"vertical": "general", "config": {"notify_email": "alerts@beta.com"}}, headers=H)
b = row("owner@beta.com")
check("switching back to general keeps a customized assistant name", r.status_code == 200 and b.vertical == "general" and b.bot_name == "Bea")
check("general accounts can set where alerts go", r.get_json()["config"]["notify_email"] == "alerts@beta.com")

# ════════════════════════════════════════════════════════════════════════════
print("\n-- contact parsing and the high-risk gate (no model) --")
c = E.find_contact("I'm Dana Lee, dana.lee@example.com or 239-555-0147")
check("finds name, email and phone", c == {"name": "Dana Lee", "email": "dana.lee@example.com", "phone": "(239) 555-0147"})
check("a capitalized name on its own counts as a name", E.find_name("Maria Gomez") == "Maria Gomez")
check("a short name next to contact details counts", E.find_name("john smith 239-555-0147") == "John Smith")
check("a sentence is not a name", E.find_name("what are your hours") is None and E.find_name("I'm still waiting on my order") is None)
check("no contact invented", E.find_contact("my order never arrived") == {"name": None, "email": None, "phone": None})
for msg in ["Can I talk to a real person?", "I want to speak with a manager", "connect me to an agent please",
            "have someone call me", "Quiero hablar con una persona", "call me back"]:
    check(f"gate: asks for a person: {msg}", E.high_risk(msg) == "Visitor asked for a person.")
for msg in ["I'm going to sue you", "I'll file a chargeback", "this is discrimination", "talking to my lawyer about this"]:
    check(f"gate: legal or complaint: {msg}", (E.high_risk(msg) or "").startswith("Visitor mentioned legal"))
for msg in ["Do you sell fire pits?", "My name is Sue", "Do you have an attorney who handles wills?",
            "What's your phone number?", "Is someone there on Saturdays?", "How do I contact support?"]:
    check(f"gate stays quiet: {msg}", E.high_risk(msg) is None)

# ════════════════════════════════════════════════════════════════════════════
print("\n-- escalation: record, alert, then contact capture --")
reset(triage_intent="account")
_, sid = wchat(A_KEY, "Hi, do you ship to Canada?")          # an ordinary first turn
STUB["triage_route"] = "escalation"
resp, sid = wchat(A_KEY, "I want a refund for order 1182", sid)
e = esc_for(sid)
check("escalation is recorded for a general business", e is not None and e.status == "collecting" and e.client_id == A_ID)
check("record keeps why and what the visitor said", e.reason == "triage reason" and e.last_message == "I want a refund for order 1182")
check("reply is the model's line plus the fixed contact question",
      resp == "I'm passing this to the team.\n\n" + t("esc_ask_contact", "en"))
check("owner is alerted right away", len(SENT["email"]) == 1 and SENT["email"][0][0] == "owner@acme.com")
to, subj, body, reply_to = SENT["email"][0]
check("alert subject says a person is needed", subj.startswith("Needs a person: Acme Bikes"))
check("alert carries the whole chat, including the message that triggered it",
      "Visitor: Hi, do you ship to Canada?" in body and "Visitor: I want a refund for order 1182" in body)
check("alert says contact details are still pending", "not shared yet" in body and reply_to is None)
check("alert links to the portal list", "https://app.example.com/portal#escalations" in body)

calls = STUB["calls"]
resp, _ = wchat(A_KEY, "Dana Lee, dana@example.com", sid)
e = esc_for(sid)
check("contact is captured from the reply", (e.contact_name, e.contact_email, e.status) == ("Dana Lee", "dana@example.com", "open"))
check("capture is fixed wording", resp == t("esc_thanks", "en", name=", Dana Lee", contact="dana@example.com"))
check("capture makes no model call", STUB["calls"] == calls)
check("owner gets a follow-up with the contact details",
      len(SENT["email"]) == 2 and "dana@example.com" in SENT["email"][1][2] and SENT["email"][1][3] == "dana@example.com")
check("follow-up threads with the first alert (same subject)", SENT["email"][1][1] == subj)
check("one record per chat", esc_count(sid) == 1)

STUB["triage_route"] = "resolver"
resp, _ = wchat(A_KEY, "ok thanks", sid)
check("after capture the chat goes back to normal answers", resp == "We're open 9 to 5." and len(SENT["email"]) == 2)

print("\n-- contact already in the chat: confirm it is the best one --")
def earlier_contact():
    reset()
    _, s = wchat(A_KEY, "My email is sam@example.com, can you check on order 77?")
    STUB.update(triage_route="escalation")
    r, _ = wchat(A_KEY, "This is taking too long, I want it cancelled", s)
    STUB.update(triage_route="resolver")
    return r, s

resp, sid = earlier_contact()
e = esc_for(sid)
check("known contact is used, pending confirmation", e.contact_email == "sam@example.com" and e.status == "confirming")
check("reply asks whether it is the best contact, not for it again",
      resp.endswith(t("esc_confirm_contact", "en", contact="sam@example.com")))
check("the alert goes out right away with the contact and a reply-to",
      len(SENT["email"]) == 1 and SENT["email"][0][3] == "sam@example.com")
calls = STUB["calls"]
resp, _ = wchat(A_KEY, "yes that's fine", sid)
check("a yes confirms it", resp == t("esc_confirmed", "en", contact="sam@example.com") and esc_for(sid).status == "open")
check("confirming makes no model call and sends no duplicate alert", STUB["calls"] == calls and len(SENT["email"]) == 1)

resp, sid = earlier_contact()
resp, _ = wchat(A_KEY, "Actually call me at 239-555-0101", sid)
e = esc_for(sid)
check("a different contact replaces the earlier one",
      (e.contact_email, e.contact_phone, e.status) == (None, "(239) 555-0101", "open")
      and resp == t("esc_confirmed", "en", contact="(239) 555-0101"))
check("the owner gets the correction", len(SENT["email"]) == 2 and "(239) 555-0101" in SENT["email"][1][2])

resp, sid = earlier_contact()
resp, _ = wchat(A_KEY, "no", sid)
check("a no asks for the best contact", resp == t("esc_ask_best", "en") and esc_for(sid).status == "collecting")
resp, _ = wchat(A_KEY, "sam.work@example.com", sid)
e = esc_for(sid)
check("and the new one is recorded and sent", e.contact_email == "sam.work@example.com" and e.status == "open"
      and len(SENT["email"]) == 2 and SENT["email"][1][3] == "sam.work@example.com")

resp, sid = earlier_contact()
resp, _ = wchat(A_KEY, "what are your hours on sunday?", sid)
check("an unrelated question is just answered; the earlier contact stands",
      resp == "We're open 9 to 5." and esc_for(sid).status == "open" and esc_for(sid).contact_email == "sam@example.com")

print("\n-- visitor declines, or keeps chatting --")
reset(triage_route="escalation")
_, sid = wchat(A_KEY, "I need help with a damaged item")
resp, _ = wchat(A_KEY, "no thanks", sid)
check("a clear no is respected", resp == t("esc_no_contact", "en") and esc_for(sid).status == "open")
check("no second alert without contact", len(SENT["email"]) == 1)

reset(triage_route="escalation")
_, sid = wchat(A_KEY, "My delivery is wrong")
STUB["triage_route"] = "resolver"
resp, _ = wchat(A_KEY, "what time do you close?", sid)
check("an unrelated question is answered, with one short reminder",
      resp == "We're open 9 to 5.\n\n" + t("esc_reminder", "en") and esc_for(sid).status == "collecting")
resp, _ = wchat(A_KEY, "and on sunday?", sid)
check("then the assistant stops waiting", resp == "We're open 9 to 5." and esc_for(sid).status == "open")

reset(triage_route="escalation")
_, sid = wchat(A_KEY, "Something is wrong with my bill")
resp, _ = wchat(A_KEY, "Maria Gomez", sid)
check("a name alone gets the fixed follow-up question", resp == t("esc_ask_reach", "en", name="Maria Gomez"))
resp, _ = wchat(A_KEY, "(239) 555-0188", sid)
e = esc_for(sid)
check("then the phone number completes it", e.contact_name == "Maria Gomez" and e.contact_phone == "(239) 555-0188" and e.status == "open")

reset(triage_route="escalation")
resp, sid = wchat(A_KEY, "Hola, necesito ayuda con mi pedido, quiero un reembolso")
check("Spanish chats get the Spanish contact question", resp.endswith(t("esc_ask_contact", "es")) and esc_for(sid).lang == "es")

# ════════════════════════════════════════════════════════════════════════════
print("\n-- fail safe --")
reset(triage_raises=True, resolver_raises=True, esc_raises=True)
resp, sid = wchat(A_KEY, "I want to talk to a real person")
e = esc_for(sid)
check("model fully down: asking for a person still escalates", e is not None and e.source == "gate")
check("model fully down: reply is fixed wording that asks for contact",
      resp == t("esc_person_needed", "en") + "\n\n" + t("esc_ask_contact", "en"))
check("model fully down: owner is still alerted", len(SENT["email"]) == 1)

reset(triage_raises=True, resolver_raises=True)
resp, sid = wchat(A_KEY, "do you have this in blue?")
e = esc_for(sid)
check("model down on an ordinary question: handed to the team, not a dead end",
      e is not None and e.source == "error" and resp == t("esc_trouble", "en") + "\n\n" + t("esc_ask_contact", "en"))
check("the owner hears about it", len(SENT["email"]) == 1)

reset(triage_route="escalation", esc_raises=True)
resp, sid = wchat(A_KEY, "your product broke my bike")
check("escalation model failure still records and alerts",
      esc_for(sid) is not None and len(SENT["email"]) == 1 and resp.startswith(t("esc_person_needed", "en")))

reset(triage_route="escalation", esc={"action": "deflect", "message_to_user": "Happy to explain our policy.",
                                      "alert_client": False, "reason": "can still help"})
resp, sid = wchat(A_KEY, "hmm what is your return policy")
check("the model may talk a triage escalation back down", resp == "Happy to explain our policy." and esc_for(sid) is None)
resp, sid = wchat(A_KEY, "let me speak to a manager")
check("but never one the gate forced", esc_for(sid) is not None and resp.endswith(t("esc_ask_contact", "en")))

reset(resolver=f"I can't look up orders, so I'm passing this to the team.\n{HANDOFF_TAG}")
resp, sid = wchat(A_KEY, "where is order 5521?")
check("resolver handoff tag is removed from the reply", HANDOFF_TAG not in resp and resp.startswith("I can't look up orders"))
check("resolver handoff always reaches the owner, with the contact question",
      esc_for(sid).source == "resolver" and len(SENT["email"]) == 1 and resp.endswith(t("esc_ask_contact", "en")))

print("\n-- a failed email never loses the request --")
reset(triage_route="escalation")
MAIL["ok"] = False
resp, sid = wchat(A_KEY, "I need someone to fix my order")
e = esc_for(sid)
check("record exists even though the email failed", e is not None and e.notified_at is None and e.notify_attempts == 1)
lst = A.get("/api/portal/escalations").get_json()["escalations"]
check("owner still sees it in the portal", any(x["session_id"] == sid and x["notified"] is False for x in lst))
check("too soon to retry", E.retry_unsent() == 0)
db = _db.SessionLocal()
db.query(Escalation).filter(Escalation.session_id == sid).update({"updated_at": datetime.now(timezone.utc) - timedelta(minutes=5)})
db.commit(); db.close()
MAIL["ok"] = True
check("the sweep retries and delivers it", E.retry_unsent() >= 1 and esc_for(sid).notified_at is not None
      and any("I need someone to fix my order" in m[2] for m in SENT["email"]))
n = len(SENT["email"])
check("nothing is sent twice", E.retry_unsent() == 0 and len(SENT["email"]) == n)

print("\n-- alert targets --")
reset(triage_route="escalation")
appmod.update_client(B_ID, lead_config={"notify_email": "alerts@beta.com", "notify_phone": "+12395550100"})
_, sid = wchat(B_KEY, "I want to cancel my cake order")
check("alerts go to the account's alert email", SENT["email"][-1][0] == "alerts@beta.com")
check("and a short text to the alert phone", len(SENT["sms"]) == 1 and SENT["sms"][0][0] == "+12395550100")

# ════════════════════════════════════════════════════════════════════════════
print("\n-- portal API: tenant-scoped, CSRF-protected --")
la = A.get("/api/portal/escalations").get_json()["escalations"]
lb = B.get("/api/portal/escalations").get_json()["escalations"]
check("each tenant sees only its own", la and lb and all(esc_for(x["session_id"]).client_id == A_ID for x in la)
      and all(esc_for(x["session_id"]).client_id == B_ID for x in lb))
anon = appmod.app.test_client()
check("logged out -> 401", anon.get("/api/portal/escalations").status_code == 401)
target = la[0]["id"]
check("B cannot close A's", B.put(f"/api/portal/escalations/{target}", json={"status": "closed"}, headers=H).status_code == 404)
check("no CSRF token -> 403", A.put(f"/api/portal/escalations/{target}", json={"status": "closed"}).status_code == 403)
check("bad status -> 400", A.put(f"/api/portal/escalations/{target}", json={"status": "collecting"}, headers=H).status_code == 400)
r = A.put(f"/api/portal/escalations/{target}", json={"status": "closed"}, headers=H)
check("owner marks it handled", r.status_code == 200 and r.get_json()["escalation"]["status"] == "closed")
m = A.get("/api/portal/analytics").get_json()
check("analytics count chats that needed a person", m["rental"] is False and m["escalations"] == len(la)
      and m["escalations_waiting"] == len([x for x in la if x["status"] != "closed"]) - 1)

print("\n-- rental accounts: same safety net --")
R = signup("owner@rent.com", "Palm Props")
R.put("/api/portal/leasing", json={"vertical": "long_term", "config": {}}, headers=H)
R_KEY = row("owner@rent.com").api_key
reset(triage_intent="resident", triage_route="escalation")
resp, sid = wchat(R_KEY, "My landlord is ignoring me and I'm furious")
check("rental escalation is recorded and asks for contact", esc_for(sid) is not None and resp.endswith(t("esc_ask_contact", "en")))

print("\n-- the demo account never emails --")
import scripts.create_demo_client as demo_mod
demo_mod.create_demo_client()
demo_key = _db.SessionLocal().get(Client, "demo").api_key
reset(triage_route="escalation")
resp, sid = wchat(demo_key, "I'd like to talk to someone about my order")
e = esc_for(sid)
check("demo records the chat and asks for contact", e is not None and resp.endswith(t("esc_ask_contact", "en")))
wchat(demo_key, "test@example.com", sid)
check("demo sends no emails or texts", SENT["email"] == [] and SENT["sms"] == [])
import scripts.reset_demo_daily as reset_demo
reset_demo.reset_demo_usage()
check("daily demo reset clears its escalations", esc_for(sid) is None)

print("\n-- deleting a tenant removes its escalations --")
# The delete itself is done by the database (ON DELETE CASCADE), as for leads
# and appointments; SQLite doesn't enforce it here, so check the definition.
fk = next(iter(Escalation.__table__.c.client_id.foreign_keys))
check("escalations cascade when their client is deleted", fk.ondelete == "CASCADE" and fk.column.table.name == "clients")

# ── Migration 0009 on a real Alembic run ────────────────────────────────────
print("\n-- migration 0009 --")
import tempfile, sqlite3
from alembic.config import Config
from alembic import command
tmp = tempfile.mkdtemp()
url = f"sqlite:///{tmp}/m.db"
os.environ["DATABASE_URL"] = url
cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
cfg.set_main_option("sqlalchemy.url", url)
command.upgrade(cfg, "0008")
con = sqlite3.connect(f"{tmp}/m.db")
con.execute("INSERT INTO clients (id, api_key, vertical, bot_name) VALUES ('r', 'kr', 'long_term', 'Leasing Assistant')")
con.commit(); con.close()
command.upgrade(cfg, "head")
con = sqlite3.connect(f"{tmp}/m.db")
cols = {r[1] for r in con.execute("PRAGMA table_info(escalations)").fetchall()}
model_cols = {c.name for c in Escalation.__table__.columns}
check("0009 creates the escalations table matching the model", cols == model_cols)
check("0009 leaves existing rental accounts alone",
      con.execute("SELECT vertical, bot_name FROM clients WHERE id='r'").fetchone() == ("long_term", "Leasing Assistant"))
con.close()
command.downgrade(cfg, "0008")
con = sqlite3.connect(f"{tmp}/m.db")
check("0009 downgrades cleanly", con.execute("SELECT name FROM sqlite_master WHERE name='escalations'").fetchone() is None)
con.close()

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("ALL PASSED")
