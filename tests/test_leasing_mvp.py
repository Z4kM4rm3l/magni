"""Leasing-assistant MVP tests: Fair Housing guardrails, the intake flow end to
end through the real widget endpoint, lead alerts, the portal Leads/Leasing
APIs, trial + Stripe billing, and rate limits.

Flask test client on an isolated SQLite DB. The LLM, SMTP, Twilio, and Stripe
are all stubbed. Run: python tests/test_leasing_mvp.py
"""
import os, sys, types, json
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("PUBLIC_BASE_URL", "https://app.example.com")
os.environ["STRIPE_SECRET_KEY"] = "sk_test_dummy"
os.environ["STRIPE_PRICE_STARTER"] = "price_starter_123"
os.environ["STRIPE_PRICE_PROFESSIONAL"] = "price_pro_456"

# ── LLM stub, routed by system prompt ────────────────────────────────────
STUB = {
    "triage_intent": "general", "triage_route": "resolver", "triage_raises": False,
    "extract": {}, "resolver": "Resolver answer.", "resolver_prompts": [],
}
import core.llm as llm
def _stub_generate(prompt, system=None, **kw):
    si = system or ""
    if "triage classifier" in si:
        if STUB["triage_raises"]:
            raise RuntimeError("llm down")
        return json.dumps({"intent": STUB["triage_intent"], "confidence": 0.9,
                           "route": STUB["triage_route"], "escalation_reason": None})
    if "extract answers" in si:
        ext = STUB["extract"]
        body = ext(prompt) if callable(ext) else ext
        return json.dumps({"answers": body.get("answers", {}), "asked_question": body.get("asked_question", False),
                           "needs_human": body.get("needs_human", False), "declined": body.get("declined", False)})
    if "escalation handler" in si:
        return json.dumps({"action": "human_handoff", "message_to_user": "A team member will reach out.",
                           "alert_client": False, "reason": "stub"})
    return "stub summary"
def _stub_chat(system, history, message, **kw):
    STUB["resolver_prompts"].append("\n\n".join(system) if isinstance(system, list) else system)
    return STUB["resolver"]
llm.generate = _stub_generate
llm.chat = _stub_chat

# ── Isolated SQLite ─────────────────────────────────────────────────────────
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import core.db as _db
# Optional: MAGNI_TEST_DATABASE_URL points at an EMPTY, already-migrated
# Postgres database (alembic upgrade head) to run this suite against the real
# schema instead of SQLite.
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
from core import leasing as L, notifier, lead_flow, rate_limit
from core.models import Client, Lead
from core.client_manager import create_client
from core.billing_policies import billing_state, billing_entitlement
from core.client_guard import VISITOR_UNAVAILABLE
import routes.billing as billing_routes
import stripe

# Notifications run inline and are recorded instead of sent.
notifier.RUN_IN_BACKGROUND = False
SENT = {"email": [], "sms": []}
notifier.send_email = lambda to, subj, body, reply_to=None, **kw: SENT["email"].append((to, subj, body, reply_to)) or True
notifier.send_sms = lambda to, body: SENT["sms"].append((to, body)) or True

failures = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)

TOKEN = "test-csrf-token"
class CsrfClient:
    def __init__(self, c): self.c = c
    def _h(self, kw):
        h = dict(kw.pop("headers", None) or {}); h["X-CSRF-Token"] = TOKEN; kw["headers"] = h; return kw
    def get(self, *a, **k): return self.c.get(*a, **k)
    def post(self, *a, **k): return self.c.post(*a, **self._h(k))
    def put(self, *a, **k): return self.c.put(*a, **self._h(k))
    def delete(self, *a, **k): return self.c.delete(*a, **self._h(k))

def set_token(c):
    with c.session_transaction() as s:
        s["_csrf_token"] = TOKEN

def signup(email):
    c = appmod.app.test_client(); set_token(c)
    r = c.post("/signup", data={"business_name": email.split("@")[0], "email": email, "password": "Passw0rd123", "csrf_token": TOKEN})
    assert r.status_code in (302, 303), r.status_code
    set_token(c)
    return CsrfClient(c)

def client_row(email):
    db = _db.SessionLocal()
    try:
        return db.query(Client).filter(Client.email == email).first()
    finally:
        db.close()

def update_row(email, **fields):
    db = _db.SessionLocal()
    try:
        c = db.query(Client).filter(Client.email == email).first()
        for k, v in fields.items():
            setattr(c, k, v)
        db.commit()
    finally:
        db.close()

widget = appmod.app.test_client()
def wchat(key, msg, sid=None):
    if not STUB.get("keep_rate_limits"):
        rate_limit.reset()  # these tests drive one IP far faster than a human would
    body = {"message": msg}
    if sid:
        body["session_id"] = sid
    r = widget.post("/widget/chat", json=body, headers={"X-Magni-API-Key": key})
    d = r.get_json() or {}
    return r.status_code, d.get("response", ""), d.get("session_id")

def lead_for(sid):
    db = _db.SessionLocal()
    try:
        return db.query(Lead).filter(Lead.session_id == sid).first()
    finally:
        db.close()

# ════════════════════════════════════════════════════════════════════════════
print("\n-- leasing.py: config + guardrails --")
cfg = L.normalize_lead_config({"questions": ["tour_time", "name", "property", "phone"]}, "long_term", strict=True)
check("questions come back in catalog order", cfg["questions"] == ["property", "name", "phone", "tour_time"])
try:
    L.normalize_lead_config({"questions": ["property", "phone"]}, "long_term", strict=True); ok = False
except L.LeadConfigError: ok = True
check("name can't be turned off (strict)", ok)
try:
    L.normalize_lead_config({"questions": ["property", "name"]}, "long_term", strict=True); ok = False
except L.LeadConfigError: ok = True
check("at least one of phone/email required (strict)", ok)
try:
    L.normalize_lead_config({"questions": ["name", "phone", "kids"]}, "long_term", strict=True); ok = False
except L.LeadConfigError: ok = True
check("owner can't add a custom question", ok)
try:
    L.normalize_lead_config({"rental_criteria": "Perfect for young professionals, no kids"}, "long_term", strict=True); ok = False
except L.LeadConfigError: ok = True
check("steering language in criteria rejected", ok)
try:
    L.normalize_lead_config({"application_url": "javascript:alert(1)"}, "long_term", strict=True); ok = False
except L.LeadConfigError: ok = True
check("non-http application link rejected", ok)
check("seasonal preset excludes long-term-only questions",
      "move_in" not in L.default_lead_config("seasonal")["questions"] and "stay_dates" in L.default_lead_config("seasonal")["questions"])
check("phone normalizes to E.164", L.normalize_lead_config({"notify_phone": "(239) 555-0123"}, "long_term")["notify_phone"] == "+12395550123")
check("lenient read repairs bad stored config", L.normalize_lead_config({"questions": "oops"}, "seasonal")["questions"] == L.PRESETS["seasonal"])
check("no catalog question mentions a protected class",
      not any(L.find_steering(q["text"]) or any(w in q["text"].lower() for w in ("child", "kid", "married", "religio", "disab", "pregnan", "national"))
              for q in L.QUESTION_CATALOG.values()))
check("steering: 'great for families' flagged", L.check_output("This 3BR is great for families!")[0] == L.SAFE_FALLBACK)
check("steering: 'safe neighborhood' flagged", bool(L.find_steering("It's in a really safe neighborhood.")))
check("steering: 'good area for families' flagged", bool(L.find_steering("It's a good area for families.")) and bool(L.find_steering("A great neighborhood for young families")))
check("steering: 'good area for a home office' passes", not L.find_steering("The loft is a good area for a home office."))
check("steering: plain property facts pass", L.check_output("2 bed, 1 bath, $1,750/mo. Cats allowed with a $300 deposit.")[1] == [])
check("accommodation: emotional support dog detected", L.detect_accommodation("I have an emotional support dog"))
check("accommodation: ordinary pet question not flagged", not L.detect_accommodation("Do you allow dogs?"))
check("opt-out detected", L.is_opt_out("no thanks") and L.is_opt_out("Just browsing!") and not L.is_opt_out("no"))
check("skip detected", L.is_skip("prefer not to say") and not L.is_skip("maybe"))
check("criteria question shows posted criteria",
      "3x the rent." in L.question_text("criteria", {"rental_criteria": "Income of 3x the rent"}))

# ════════════════════════════════════════════════════════════════════════════
print("\n-- signup starts a trial --")
A = signup("owner@palmprops.com")
B = signup("owner@beta.com")
a_row = client_row("owner@palmprops.com")
check("signup sets a 14-day trial", billing_state(a_row) == "trial")
check("portal shows trial banner", b"Free trial: 14 days left" in A.get("/portal").data)
A_KEY, B_KEY = a_row.api_key, client_row("owner@beta.com").api_key

print("\n-- leasing settings API --")
anon = appmod.app.test_client()
check("leasing GET logged out -> 401", anon.get("/api/portal/leasing").status_code == 401)
check("leasing PUT without CSRF -> 403", A.c.put("/api/portal/leasing", json={"vertical": "long_term"}).status_code == 403)
r = A.put("/api/portal/leasing", json={"vertical": "long_term", "config": {"rental_criteria": "Great for families only"}})
check("leasing PUT with steering criteria -> 400", r.status_code == 400)
r = A.put("/api/portal/leasing", json={"vertical": "long_term", "config": {
    "questions": L.PRESETS["long_term"],
    "rental_criteria": "Gross income of 3x the rent, no evictions in 5 years",
    "application_url": "https://apply.palmprops.com/2br",
    "booking_url": "https://calendly.com/palmprops/tour",
    "notify_phone": "239-555-9999",
}})
check("leasing PUT valid -> 200", r.status_code == 200)
check("saved config normalized phone", r.get_json()["config"]["notify_phone"] == "+12395559999")
g = A.get("/api/portal/leasing").get_json()
check("leasing GET returns saved vertical + catalog", g["vertical"] == "long_term" and "seasonal" in g["catalog"])
check("B's settings untouched by A", client_row("owner@beta.com").vertical == "long_term")
check("portal shows Leads card for rental client", b'id="leads"' in A.get("/portal").data)

# ════════════════════════════════════════════════════════════════════════════
print("\n-- full intake through the widget --")
SENT["email"].clear(); SENT["sms"].clear(); STUB["resolver_prompts"].clear()
STUB.update(triage_intent="leasing", resolver="Yes, the 2BR on Palm St is listed at $1,750 and available January 1.")
STUB["extract"] = {"answers": {"property": "2BR on Palm St", "move_in": "January"}}
code, resp, sid = wchat(A_KEY, "Hi, is the 2BR on Palm St available in January?")
check("turn 1: answers the question", "listed at $1,750" in resp)
check("turn 1: then asks the first missing question (name)", resp.endswith(L.question_text("name", {})))
check("turn 1: resolver got Fair Housing rules", any("FAIR HOUSING RULES" in p for p in STUB["resolver_prompts"]))
STUB["extract"] = {"answers": {"name": "Maria Lopez"}}
_, resp, _ = wchat(A_KEY, "Maria Lopez", sid)
check("turn 2: asks phone", resp.endswith(L.question_text("phone", {})))
STUB["extract"] = {"answers": {}}
_, resp, _ = wchat(A_KEY, "sure it's 239.555.0123", sid)
check("turn 3: phone parsed deterministically, asks email", resp.endswith(L.question_text("email", {})))
check("turn 3: phone stored formatted", lead_for(sid).contact_phone == "(239) 555-0123")
_, resp, _ = wchat(A_KEY, "skip", sid)
check("turn 4: skip moves on to occupants", resp.endswith(L.question_text("occupants", {})))
STUB["extract"] = {"answers": {"occupants": "2"}, "asked_question": True}
STUB["resolver"] = "There is one assigned parking spot."
_, resp, _ = wchat(A_KEY, "It'd be 2 of us. Is there parking?", sid)
check("turn 5: answers side question AND records answer", "assigned parking" in resp and resp.endswith(L.question_text("lease_length", {})))
STUB["extract"] = {"answers": {}}
_, resp, _ = wchat(A_KEY, "hmm", sid)
check("turn 6: unclear answer re-asked once", resp.startswith("Sorry, I didn't quite catch that.") and resp.endswith(L.question_text("lease_length", {})))
_, resp, _ = wchat(A_KEY, "idk", sid)
check("turn 7: second miss marks not provided and moves on", resp.endswith(L.question_text("budget", {})))
check("turn 7: recorded as Not provided", lead_for(sid).answers["lease_length"] == L.NOT_PROVIDED)
STUB["extract"] = {"answers": {"budget": "$1,800"}}
_, resp, _ = wchat(A_KEY, "around 1800", sid)
check("turn 8: asks pets", resp.endswith(L.question_text("pets", {})))
before = len(SENT["email"])
STUB["extract"] = {"answers": {}}
_, resp, _ = wchat(A_KEY, "I have an emotional support dog", sid)
check("turn 9: accommodation acknowledged, not treated as a pet", resp.startswith(L.ACCOMMODATION_REPLY))
check("turn 9: pets recorded as assistance note", lead_for(sid).answers["pets"] == L.ASSISTANCE_NOTE)
check("turn 9: owner alerted right away (contact on file)", len(SENT["email"]) == before + 1 and "Accommodation request" in SENT["email"][-1][1])
check("turn 9: next question shows posted criteria", "3x the rent" in resp)
STUB["extract"] = {"answers": {"criteria": "Yes"}}
_, resp, _ = wchat(A_KEY, "yes", sid)
check("turn 10: asks tour time", resp.endswith(L.question_text("tour_time", {})))
SENT["email"].clear(); SENT["sms"].clear()
STUB["extract"] = {"answers": {"tour_time": "Saturday morning"}}
_, resp, _ = wchat(A_KEY, "Saturday morning works", sid)
check("turn 11: completes with both links", "https://apply.palmprops.com/2br" in resp and "https://calendly.com/palmprops/tour" in resp)
l = lead_for(sid)
check("lead complete + contact stored", l.status == "complete" and l.contact_name == "Maria Lopez")
check("completion email sent to account email", len(SENT["email"]) == 1 and SENT["email"][0][0] == "owner@palmprops.com")
check("completion email lists answers + accommodation note",
      "Maria Lopez" in SENT["email"][0][2] and "Saturday morning" in SENT["email"][0][2] and "assistance animal" in SENT["email"][0][2])
check("completion SMS sent to alert phone", len(SENT["sms"]) == 1 and SENT["sms"][0][0] == "+12395559999")
check("alert says Magni never screens", "does not screen" in SENT["email"][0][2])
STUB.update(triage_intent="general", resolver="You're welcome!")
_, resp, _ = wchat(A_KEY, "thanks!", sid)
check("after completion, normal assistant answers (no restart)", resp == "You're welcome!" and lead_for(sid).status == "complete")

print("\n-- portal leads --")
leads = A.get("/api/portal/leads").get_json()["leads"]
check("A sees its lead", any(x["name"] == "Maria Lopez" for x in leads))
check("B sees none of A's leads", B.get("/api/portal/leads").get_json()["leads"] == [])
lid = next(x["id"] for x in leads if x["name"] == "Maria Lopez")
check("A can mark contacted", A.put(f"/api/portal/leads/{lid}", json={"owner_status": "contacted"}).status_code == 200)
check("B can't touch A's lead -> 404", B.put(f"/api/portal/leads/{lid}", json={"owner_status": "closed"}).status_code == 404)
check("bad owner_status -> 400", A.put(f"/api/portal/leads/{lid}", json={"owner_status": "rejected"}).status_code == 400)
csv_body = A.get("/api/portal/leads.csv").data.decode()
check("CSV export has the lead + its flag", "Maria Lopez" in csv_body and "accommodation_request" in csv_body)
check("B's CSV has no A data", "Maria" not in B.get("/api/portal/leads.csv").data.decode())

print("\n-- opt-out, partial sweep, triage fallback, outside-intake accommodation --")
STUB.update(triage_intent="leasing", resolver="We have a few units open.")
STUB["extract"] = {"answers": {}}
_, resp, sid2 = wchat(A_KEY, "Do you have anything for rent?")
_, resp, _ = wchat(A_KEY, "no thanks", sid2)
check("opt-out ends intake politely", resp == lead_flow.OPT_OUT_REPLY and lead_for(sid2).status == "abandoned")

SENT["email"].clear()
_, resp, sid3 = wchat(A_KEY, "I'd like to apply")
STUB["extract"] = {"answers": {"property": "the 1BR"}}
wchat(A_KEY, "the 1BR", sid3)
STUB["extract"] = {"answers": {"name": "Sam"}}
wchat(A_KEY, "Sam", sid3)
wchat(A_KEY, "sam@example.com and 2395550199", sid3)
check("no alert while prospect is still answering", SENT["email"] == [])
db = _db.SessionLocal()
row = db.query(Lead).filter(Lead.session_id == sid3).first()
row.updated_at = datetime.now(timezone.utc) - timedelta(minutes=45)
db.commit(); db.close()
check("sweep alerts the quiet partial lead once", lead_flow.sweep_partial_leads() == 1 and "didn't finish" in SENT["email"][-1][1])
check("partial alert reply-to is the prospect", SENT["email"][-1][3] == "sam@example.com")
check("second sweep sends nothing", lead_flow.sweep_partial_leads() == 0)

STUB["triage_raises"] = True
STUB["extract"] = {"answers": {}}
_, resp, sid4 = wchat(A_KEY, "Can I schedule a tour this weekend?")
STUB["triage_raises"] = False
check("triage down: keyword fallback still starts intake", lead_for(sid4) is not None and resp.endswith(L.question_text("property", {})))

STUB["triage_intent"] = "general"
_, resp, sid5 = wchat(A_KEY, "Do you accept emotional support animals?")
check("accommodation question outside intake -> human handoff reply + flagged lead",
      resp.startswith(L.ACCOMMODATION_REPLY) and (lead_for(sid5).flags or {}).get("accommodation_request"))

print("\n-- guardrail on free-form replies --")
STUB.update(triage_intent="general", resolver="This home is great for families and it's in a safe neighborhood.")
_, resp, _ = wchat(A_KEY, "Tell me about the area")
check("rental client: steering reply replaced", resp == L.SAFE_FALLBACK)
_, resp, _ = wchat(B_KEY, "Tell me about the area")
check("never-configured account is a rental too: steering reply replaced", resp == L.SAFE_FALLBACK)
STUB["triage_intent"] = "leasing"
_, resp, sid6 = wchat(B_KEY, "Do you have a unit for rent?")
check("never-configured account: leasing intent starts the default intake", lead_for(sid6) is not None)

print("\n-- knowledge base context --")
from core.knowledge_base import get_kb_context, add_article, WHOLE_KB_CHAR_BUDGET
add_article(a_row.id, "Refund Policy", "Application fees are non-refundable once screening has started.")
add_article(a_row.id, "Pet Policy", "Cats and dogs under 40 lbs, $300 deposit. Assistance animals are not pets.")
ctx = get_kb_context(a_row.id, "can I get my money back?")
check("small KB: whole KB sent even with no keyword overlap", "Refund Policy" in ctx and "Pet Policy" in ctx)
add_article(B_KEY and client_row("owner@beta.com").id, "Big", "x " * (WHOLE_KB_CHAR_BUDGET // 2 + 100))
add_article(client_row("owner@beta.com").id, "Parking", "Parking is free in lot B for every unit.")
ctx = get_kb_context(client_row("owner@beta.com").id, "where is parking")
check("large KB: keyword retrieval used", "Parking" in ctx and "x x x x" not in ctx[:200])
check("KB context isolates tenants", "Pet Policy" not in get_kb_context(client_row("owner@beta.com").id, "pet policy"))

# ════════════════════════════════════════════════════════════════════════════
print("\n-- trial gate --")
STUB.update(triage_intent="general", resolver="Hello from the assistant.")
update_row("owner@beta.com", trial_ends_at=datetime.now(timezone.utc) - timedelta(minutes=1))
STUB["resolver_prompts"].clear()
_, resp, _ = wchat(B_KEY, "hello?")
check("trial ended: visitor gets neutral message", resp == VISITOR_UNAVAILABLE)
check("trial ended: no LLM call made", STUB["resolver_prompts"] == [])
check("trial ended: portal still works + shows banner", b"Your free trial has ended" in B.get("/portal").data)
op = create_client(business_name="Managed Co", email="m@managed.com", tier="starter")
db = _db.SessionLocal(); mc = db.get(Client, op["client_id"]); db.close()
check("operator-created client is 'managed' and allowed", billing_entitlement(mc) == (True, "managed"))

print("\n-- checkout + customer portal --")
CAP = {}
LIVE = {}            # sub_id -> live subscription dict (what Stripe would return)
OPEN_SESSIONS = []   # open checkout sessions Stripe would list
EXPIRED = []
class _Obj:
    def __init__(self, **kw): self.__dict__.update(kw)
def _create(**params):
    CAP.clear(); CAP.update(params); return _Obj(url="https://checkout.stripe.com/c/test")
_cust_n = {"n": 0}
def _cust_create(**kw):
    _cust_n["n"] += 1; _cust_n["last"] = kw; return _Obj(id=f"cus_new{_cust_n['n']}")
def _retrieve(sub_id):
    if sub_id not in LIVE:
        raise stripe.InvalidRequestError("No such subscription", "id")
    return dict(LIVE[sub_id])
stripe.checkout.Session.create = staticmethod(_create)
stripe.checkout.Session.list = staticmethod(lambda **kw: {"data": list(OPEN_SESSIONS)})
stripe.checkout.Session.expire = staticmethod(lambda sid: EXPIRED.append(sid))
stripe.Customer.create = staticmethod(_cust_create)
stripe.Subscription.retrieve = staticmethod(_retrieve)
stripe.Subscription.list = staticmethod(lambda customer=None, **kw: {"data": [v for v in LIVE.values() if v.get("customer") == customer]})
stripe.billing_portal.Session.create = staticmethod(lambda **p: CAP.update({"portal": p}) or _Obj(url="https://billing.stripe.com/p/test"))
billing_routes.stripe.api_key = "sk_test_dummy"
C = signup("owner@gamma.com")
check("manage billing without customer -> 400", C.post("/api/portal/billing/manage", json={}).status_code == 400)
check("checkout logged out -> 401", anon.post("/api/portal/billing/checkout", json={"plan": "starter"}).status_code == 401)
check("checkout unknown plan -> 400", A.post("/api/portal/billing/checkout", json={"plan": "gold"}).status_code == 400)
r = A.post("/api/portal/billing/checkout", json={"plan": "starter"})
check("checkout returns Stripe URL", r.status_code == 200 and r.get_json()["url"].startswith("https://checkout.stripe.com"))
check("checkout creates one Stripe customer up front with client_id",
      _cust_n["last"]["metadata"]["client_id"] == a_row.id and CAP["customer"] == client_row("owner@palmprops.com").stripe_customer_id)
check("checkout uses the starter price", CAP["line_items"][0]["price"] == "price_starter_123")
check("checkout tags client_id in metadata (session + subscription)",
      CAP["metadata"]["client_id"] == a_row.id and CAP["subscription_data"]["metadata"]["client_id"] == a_row.id)
check("checkout defers first charge to trial end", "trial_end" in CAP["subscription_data"])
A_CUS = client_row("owner@palmprops.com").stripe_customer_id
OPEN_SESSIONS[:] = [{"id": "cs_open1", "url": "https://checkout.stripe.com/c/existing", "metadata": {"tier": "starter"}}]
r = A.post("/api/portal/billing/checkout", json={"plan": "starter"})
check("second tab reuses the open checkout (no duplicate subscription)", r.get_json()["url"] == "https://checkout.stripe.com/c/existing")
r = A.post("/api/portal/billing/checkout", json={"plan": "professional"})
check("switching plan expires the old open checkout", "cs_open1" in EXPIRED and r.status_code == 200)
OPEN_SESSIONS.clear()
r = B.post("/api/portal/billing/checkout", json={"plan": "professional"})
check("expired trial: no trial_end passed", r.status_code == 200 and "trial_end" not in CAP["subscription_data"])
saved = os.environ.pop("STRIPE_PRICE_STARTER")
check("missing price id -> 503", A.post("/api/portal/billing/checkout", json={"plan": "starter"}).status_code == 503)
os.environ["STRIPE_PRICE_STARTER"] = saved

print("\n-- webhooks (state always re-read live from Stripe) --")
def deliver(evt):
    billing_routes.stripe.Webhook.construct_event = staticmethod(lambda payload, sig, secret: evt)
    return anon.post("/api/v1/stripe-webhook", data=b"{}", headers={"Stripe-Signature": "t", "Content-Type": "application/json"})
T0 = int(datetime.now(timezone.utc).timestamp())
def sub(sid, status, cus=None, tier="starter", cid=None, price="price_starter_123"):
    return {"id": sid, "customer": cus or A_CUS, "status": status, "metadata": {"client_id": cid or a_row.id, "tier": tier},
            "items": {"data": [{"price": {"id": price}, "current_period_start": T0, "current_period_end": T0 + 2592000}]}}
LIVE["sub_A"] = sub("sub_A", "active")
r = deliver({"id": "evt_1", "type": "checkout.session.completed", "created": T0,
             "data": {"object": {"client_reference_id": a_row.id, "customer": A_CUS, "subscription": "sub_A",
                                  "metadata": {"client_id": a_row.id, "tier": "starter"}}}})
a_row = client_row("owner@palmprops.com")
check("checkout.completed attaches subscription", r.status_code == 200 and a_row.stripe_subscription_id == "sub_A" and billing_state(a_row) == "active")
check("checkout blocked while a live subscription exists -> 409", A.post("/api/portal/billing/checkout", json={"plan": "starter"}).status_code == 409)
# Same-second race: stale 'incomplete' payload arrives AFTER 'active'. Live state wins.
deliver({"id": "evt_inc", "type": "customer.subscription.created", "created": T0, "data": {"object": sub("sub_A", "incomplete")}})
check("same-second stale 'incomplete' payload can't pause a paid client", client_row("owner@palmprops.com").subscription_status == "active")
LIVE["sub_A"] = sub("sub_A", "past_due", tier="professional", price="price_pro_456")
deliver({"id": "evt_2", "type": "customer.subscription.updated", "created": T0 + 10, "data": {"object": LIVE["sub_A"]}})
a_row = client_row("owner@palmprops.com")
check("subscription.updated syncs status, tier, period", a_row.subscription_status == "past_due" and a_row.tier == "professional" and a_row.billing_period_end is not None)
check("past_due still answers (Stripe retry grace)", billing_entitlement(a_row)[0])
LIVE["sub_A"] = sub("sub_A", "active", tier="professional", price="price_pro_456")
update_row("owner@palmprops.com", subscription_status="active")
r = deliver({"id": "evt_2", "type": "customer.subscription.updated", "created": T0 + 10, "data": {"object": sub("sub_A", "past_due")}})
check("duplicate event id is ignored", r.get_json()["status"] == "duplicate" and client_row("owner@palmprops.com").subscription_status == "active")
# A second, duplicate subscription gets canceled: the paying one must not be cut off.
LIVE["sub_DUP"] = sub("sub_DUP", "canceled")
deliver({"id": "evt_dupdel", "type": "customer.subscription.deleted", "created": T0 + 12, "data": {"object": LIVE["sub_DUP"]}})
a_row = client_row("owner@palmprops.com")
check("deleting a non-current subscription doesn't pause the client", a_row.subscription_status == "active" and a_row.stripe_subscription_id == "sub_A")
LIVE["sub_DUP2"] = sub("sub_DUP2", "active")
deliver({"id": "evt_dup2", "type": "customer.subscription.created", "created": T0 + 13, "data": {"object": LIVE["sub_DUP2"]}})
check("a second live subscription never replaces the one on file", client_row("owner@palmprops.com").stripe_subscription_id == "sub_A")
# Late payment_failed after recovery: live status (active) wins.
deliver({"id": "evt_fail", "type": "invoice.payment_failed", "created": T0 + 20, "data": {"object": {"customer": A_CUS, "subscription": "sub_A"}}})
a_row = client_row("owner@palmprops.com")
check("late payment_failed can't flip a recovered client", a_row.is_active and a_row.subscription_status == "active")
# Abandoned checkout's expired subscription must not pause a trialing client.
C_ID = client_row("owner@gamma.com").id
update_row("owner@gamma.com", stripe_customer_id="cus_C")
LIVE["sub_C"] = sub("sub_C", "incomplete_expired", cus="cus_C", cid=C_ID)
deliver({"id": "evt_cexp", "type": "customer.subscription.updated", "created": T0 + 25, "data": {"object": LIVE["sub_C"]}})
check("expired abandoned checkout doesn't end a trial", billing_state(client_row("owner@gamma.com")) == "trial")
LIVE["sub_A"] = sub("sub_A", "canceled", tier="professional")
deliver({"id": "evt_del", "type": "customer.subscription.deleted", "created": T0 + 30, "data": {"object": {"id": "sub_A", "customer": A_CUS}}})
a_row = client_row("owner@palmprops.com")
check("subscription.deleted pauses assistant, keeps portal", billing_state(a_row) == "canceled" and a_row.is_active)
STUB["resolver"] = "hi"
_, resp, _ = wchat(A_KEY, "hello")
check("canceled: widget returns neutral message", resp == VISITOR_UNAVAILABLE)
check("canceled: portal offers plans again", b"Your subscription has ended" in A.get("/portal").data and b"Choose Starter" in A.get("/portal").data)
r = A.post("/api/portal/billing/manage", json={})
check("manage billing opens Stripe portal for known customer", r.status_code == 200 and CAP["portal"]["customer"] == A_CUS)
billing_routes.stripe.Webhook.construct_event = staticmethod(lambda *a, **k: (_ for _ in ()).throw(ValueError("bad sig")))
check("bad webhook signature -> 400", anon.post("/api/v1/stripe-webhook", data=b"{}", headers={"Stripe-Signature": "x", "Content-Type": "application/json"}).status_code == 400)
def _boom(sid): raise stripe.APIConnectionError("network down")
stripe.Subscription.retrieve = staticmethod(_boom)
r = deliver({"id": "evt_net", "type": "customer.subscription.updated", "created": T0 + 40, "data": {"object": sub("sub_A", "active")}})
check("Stripe unreachable -> 500 so Stripe redelivers", r.status_code == 500)
stripe.Subscription.retrieve = staticmethod(_retrieve)
r = deliver({"id": "evt_net", "type": "customer.subscription.updated", "created": T0 + 40, "data": {"object": sub("sub_A", "active")}})
check("redelivery after failure is processed (not marked duplicate)", r.get_json()["status"] == "success")

print("\n-- review regressions: intake --")
STUB.update(triage_intent="leasing", resolver="We have a 2BR open.")
STUB["extract"] = {"answers": {"property": "2BR"}}
update_row("owner@palmprops.com", subscription_status="active", stripe_subscription_id="sub_A")
LIVE["sub_A"] = sub("sub_A", "active")
_, resp, s7 = wchat(A_KEY, "Is the 2BR available?")
for msg, ext in (("Ana", {"name": "Ana"}), ("2395550111", {}), ("ana@x.com", {}), ("2", {"occupants": "2"}),
                 ("1 year", {"lease_length": "1 year"}), ("1500", {"budget": "$1500"})):
    STUB["extract"] = {"answers": ext}; _, resp, _ = wchat(A_KEY, msg, s7)
check("setup: pets is pending", resp.endswith(L.question_text("pets", {})))
STUB["extract"] = {"answers": {"pets": "one emotional support cat"}}
_, resp, _ = wchat(A_KEY, "Just one emotional support cat", s7)
check("ESA answer to pets question is NOT recorded as a pet", lead_for(s7).answers["pets"] == L.ASSISTANCE_NOTE)
db = _db.SessionLocal(); lid7 = db.query(Lead).filter(Lead.session_id == s7).first().id; db.close()
A.put(f"/api/portal/leads/{lid7}", json={"owner_status": "contacted"})
STUB["extract"] = {"answers": {"criteria": "yes"}}
wchat(A_KEY, "yes", s7)
check("chat turn doesn't overwrite owner's 'contacted' status", lead_for(s7).owner_status == "contacted")
STUB.update(resolver="It's a great for families spot in a safe neighborhood.")
STUB["extract"] = {"answers": {}, "asked_question": True}
_, resp, _ = wchat(A_KEY, "what's the neighborhood like?", s7)
check("blocked side answer keeps the pending question", resp.startswith(L.SAFE_FALLBACK) and resp.endswith(L.question_text("tour_time", {})))
STUB.update(resolver="ok")
STUB["extract"] = {"answers": {"tour_time": "Friday"}}
wchat(A_KEY, "Friday", s7)
check("setup: lead complete", lead_for(s7).status == "complete")
SENT["email"].clear()
_, resp, _ = wchat(A_KEY, "Oh, I also have a service dog", s7)
check("repeat accommodation mention on an already-flagged lead doesn't re-alert", resp.startswith(L.ACCOMMODATION_REPLY) and SENT["email"] == [])
db = _db.SessionLocal(); db.query(Lead).filter(Lead.session_id == s7).update({"flags": {}}); db.commit(); db.close()
SENT["email"].clear()
_, resp, _ = wchat(A_KEY, "Oh, I also have a service dog", s7)
check("accommodation after completion -> handoff reply + owner alert",
      resp.startswith(L.ACCOMMODATION_REPLY) and len(SENT["email"]) == 1 and "Accommodation request" in SENT["email"][0][1])

SENT["email"].clear()
STUB.update(triage_intent="leasing", resolver="Sure.")
STUB["extract"] = {"answers": {}}
_, _, s8 = wchat(A_KEY, "Any rentals open?")
STUB["extract"] = {"answers": {"property": "any"}}; wchat(A_KEY, "any", s8)
STUB["extract"] = {"answers": {"name": "Bo"}}; wchat(A_KEY, "Bo", s8)
STUB["extract"] = {"answers": {}}; wchat(A_KEY, "2395550177", s8)
wchat(A_KEY, "I have a therapy cat", s8)
db = _db.SessionLocal()
db.query(Lead).filter(Lead.session_id == s8).update({"updated_at": datetime.now(timezone.utc) - timedelta(hours=1)}); db.commit(); db.close()
lead_flow.sweep_partial_leads()
check("accommodation alert + quiet prospect = exactly one owner alert",
      len(SENT["email"]) == 1 and "Accommodation request: Bo" in SENT["email"][0][1])

print("\n-- review regressions: sweep --")
db = _db.SessionLocal()
old = datetime.now(timezone.utc) - timedelta(hours=2)
for i in range(120):  # visitors who asked one question and left, no contact
    db.add(Lead(id=f"junk{i}", client_id=a_row.id, session_id=f"junk{i}", status="in_progress", answers={"property": "x"},
                flags={}, owner_status="new", created_at=old, updated_at=old))
db.add(Lead(id="real1", client_id=a_row.id, session_id="real1", status="in_progress", answers={"name": "Lee", "phone": "(239) 555-0100"},
            flags={}, contact_name="Lee", contact_phone="(239) 555-0100", owner_status="new",
            created_at=datetime.now(timezone.utc) - timedelta(minutes=30), updated_at=datetime.now(timezone.utc) - timedelta(minutes=30)))
db.commit(); db.close()
SENT["email"].clear()
check("sweep still finds the real lead behind 120 no-contact leads", lead_flow.sweep_partial_leads() == 1 and "Lee" in SENT["email"][-1][1])
db = _db.SessionLocal()
check("claim is atomic: second claim on same lead loses", not __import__("core.leads", fromlist=["x"]).claim_notification(db, "real1", "partial"))
db.close()

print("\n-- rate limits --")
rate_limit.reset()
lc = appmod.app.test_client(); set_token(lc)
codes = [lc.post("/client/login", data={"email": "owner@beta.com", "password": "wrong", "csrf_token": TOKEN}).status_code for _ in range(9)]
check("client login: 9th attempt for one email -> 429", codes[:8].count(429) == 0 and codes[8] == 429)
rate_limit.reset()
update_row("owner@beta.com", trial_ends_at=None)
STUB.update(triage_intent="general", resolver="ok", keep_rate_limits=True)
codes = [wchat(B_KEY, f"hi {i}")[0] for i in range(21)]
check("widget: 21st message in a minute from one IP -> 429", codes[:20].count(429) == 0 and codes[20] == 429)
STUB["keep_rate_limits"] = False
rate_limit.reset()
update_row("owner@beta.com", trial_ends_at=datetime.now(timezone.utc) - timedelta(days=1))

print("\n-- operator MRR --")
from core.client_manager import get_client_stats
check("MRR = paying Pro ($99) + managed ($49); trials excluded", get_client_stats()["mrr"] == 99 + 49)

print("\n" + ("ALL PASSED" if not failures else f"{len(failures)} FAILED: {failures}"))
sys.exit(1 if failures else 0)
