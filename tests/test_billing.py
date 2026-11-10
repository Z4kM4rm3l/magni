"""Billing and account tests: signup trial, the trial / subscription gate in
front of the model, Stripe Checkout and Customer Portal, webhooks, knowledge
base context, rate limits, and operator MRR.

Flask test client on an isolated SQLite DB. The LLM, SMTP, Twilio, and Stripe
are all stubbed. Run: python tests/test_billing.py
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
    "resolver": "Resolver answer.", "resolver_prompts": [],
}
import core.llm as llm
def _stub_generate(prompt, system=None, **kw):
    si = system or ""
    if "triage classifier" in si:
        if STUB["triage_raises"]:
            raise RuntimeError("llm down")
        return json.dumps({"intent": STUB["triage_intent"], "confidence": 0.9,
                           "route": STUB["triage_route"], "escalation_reason": None})
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
from core import notifier, rate_limit
from core.models import Client
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

# ════════════════════════════════════════════════════════════════════════════
print("\n-- signup starts a trial --")
A = signup("owner@acme.com")
B = signup("owner@beta.com")
a_row = client_row("owner@acme.com")
check("signup sets a 14-day trial", billing_state(a_row) == "trial")
check("portal shows trial banner", b"Free trial: 14 days left" in A.get("/portal").data)
A_KEY, B_KEY = a_row.api_key, client_row("owner@beta.com").api_key

anon = appmod.app.test_client()

print("\n-- knowledge base context --")
from core.knowledge_base import get_kb_context, add_article, WHOLE_KB_CHAR_BUDGET
add_article(a_row.id, "Refund Policy", "Unopened items can be returned within 30 days for a full refund.")
add_article(a_row.id, "Pet Policy", "Dogs are welcome on the patio. Service animals are welcome everywhere.")
ctx = get_kb_context(a_row.id, "can I get my money back?")
check("small KB: whole KB sent even with no keyword overlap", "Refund Policy" in ctx and "Pet Policy" in ctx)
add_article(B_KEY and client_row("owner@beta.com").id, "Big", "x " * (WHOLE_KB_CHAR_BUDGET // 2 + 100))
add_article(client_row("owner@beta.com").id, "Parking", "Parking is free in lot B for every customer.")
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
      _cust_n["last"]["metadata"]["client_id"] == a_row.id and CAP["customer"] == client_row("owner@acme.com").stripe_customer_id)
check("checkout uses the starter price", CAP["line_items"][0]["price"] == "price_starter_123")
check("checkout tags client_id in metadata (session + subscription)",
      CAP["metadata"]["client_id"] == a_row.id and CAP["subscription_data"]["metadata"]["client_id"] == a_row.id)
check("checkout defers first charge to trial end", "trial_end" in CAP["subscription_data"])
A_CUS = client_row("owner@acme.com").stripe_customer_id
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
a_row = client_row("owner@acme.com")
check("checkout.completed attaches subscription", r.status_code == 200 and a_row.stripe_subscription_id == "sub_A" and billing_state(a_row) == "active")
check("checkout blocked while a live subscription exists -> 409", A.post("/api/portal/billing/checkout", json={"plan": "starter"}).status_code == 409)
# Same-second race: stale 'incomplete' payload arrives AFTER 'active'. Live state wins.
deliver({"id": "evt_inc", "type": "customer.subscription.created", "created": T0, "data": {"object": sub("sub_A", "incomplete")}})
check("same-second stale 'incomplete' payload can't pause a paid client", client_row("owner@acme.com").subscription_status == "active")
LIVE["sub_A"] = sub("sub_A", "past_due", tier="professional", price="price_pro_456")
deliver({"id": "evt_2", "type": "customer.subscription.updated", "created": T0 + 10, "data": {"object": LIVE["sub_A"]}})
a_row = client_row("owner@acme.com")
check("subscription.updated syncs status, tier, period", a_row.subscription_status == "past_due" and a_row.tier == "professional" and a_row.billing_period_end is not None)
check("past_due still answers (Stripe retry grace)", billing_entitlement(a_row)[0])
LIVE["sub_A"] = sub("sub_A", "active", tier="professional", price="price_pro_456")
update_row("owner@acme.com", subscription_status="active")
r = deliver({"id": "evt_2", "type": "customer.subscription.updated", "created": T0 + 10, "data": {"object": sub("sub_A", "past_due")}})
check("duplicate event id is ignored", r.get_json()["status"] == "duplicate" and client_row("owner@acme.com").subscription_status == "active")
# A second, duplicate subscription gets canceled: the paying one must not be cut off.
LIVE["sub_DUP"] = sub("sub_DUP", "canceled")
deliver({"id": "evt_dupdel", "type": "customer.subscription.deleted", "created": T0 + 12, "data": {"object": LIVE["sub_DUP"]}})
a_row = client_row("owner@acme.com")
check("deleting a non-current subscription doesn't pause the client", a_row.subscription_status == "active" and a_row.stripe_subscription_id == "sub_A")
LIVE["sub_DUP2"] = sub("sub_DUP2", "active")
deliver({"id": "evt_dup2", "type": "customer.subscription.created", "created": T0 + 13, "data": {"object": LIVE["sub_DUP2"]}})
check("a second live subscription never replaces the one on file", client_row("owner@acme.com").stripe_subscription_id == "sub_A")
# Late payment_failed after recovery: live status (active) wins.
deliver({"id": "evt_fail", "type": "invoice.payment_failed", "created": T0 + 20, "data": {"object": {"customer": A_CUS, "subscription": "sub_A"}}})
a_row = client_row("owner@acme.com")
check("late payment_failed can't flip a recovered client", a_row.is_active and a_row.subscription_status == "active")
# Abandoned checkout's expired subscription must not pause a trialing client.
C_ID = client_row("owner@gamma.com").id
update_row("owner@gamma.com", stripe_customer_id="cus_C")
LIVE["sub_C"] = sub("sub_C", "incomplete_expired", cus="cus_C", cid=C_ID)
deliver({"id": "evt_cexp", "type": "customer.subscription.updated", "created": T0 + 25, "data": {"object": LIVE["sub_C"]}})
check("expired abandoned checkout doesn't end a trial", billing_state(client_row("owner@gamma.com")) == "trial")
LIVE["sub_A"] = sub("sub_A", "canceled", tier="professional")
deliver({"id": "evt_del", "type": "customer.subscription.deleted", "created": T0 + 30, "data": {"object": {"id": "sub_A", "customer": A_CUS}}})
a_row = client_row("owner@acme.com")
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
update_row("owner@acme.com", subscription_status="active", stripe_subscription_id="sub_A")  # paying again, on Pro
check("MRR = paying Pro ($99) + managed ($49); trials excluded", get_client_stats()["mrr"] == 99 + 49)

print("\n" + ("ALL PASSED" if not failures else f"{len(failures)} FAILED: {failures}"))
sys.exit(1 if failures else 0)
