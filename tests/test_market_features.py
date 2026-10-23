"""Rental-market features: a current-resident path, after-hours lead tagging,
Spanish-speaking prospects (fixed Spanish wording end to end, Spanish Fair
Housing guardrails, Spanish booking and emails), and multiple properties per
account. Run: python tests/test_market_features.py
"""
import os, sys, json
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("PUBLIC_BASE_URL", "https://app.example.com")

STUB = {"triage_intent": "leasing", "extract": {}, "resolver": "Happy to help."}
import core.llm as llm
def _gen(prompt, system=None, **kw):
    si = system or ""
    if "triage classifier" in si:
        return json.dumps({"intent": STUB["triage_intent"], "confidence": 0.9, "route": "resolver", "escalation_reason": None})
    if "extract answers" in si:
        return json.dumps({"answers": STUB["extract"], "asked_question": False, "needs_human": False, "declined": False})
    return "stub"
llm.generate = _gen
SYSTEMS = []
def _chat(system, history, message, **kw):
    SYSTEMS.append("\n".join(system) if isinstance(system, list) else system)
    return STUB["resolver"]
llm.chat = _chat

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
from core import leasing as L, notifier, rate_limit, scheduling as S
from core.i18n import detect_lang, t
from core.models import Client, Lead, Appointment

notifier.RUN_IN_BACKGROUND = False
SENT = []
notifier.send_email = lambda to, subj, body, reply_to=None, **kw: SENT.append((to, subj, body)) or True
notifier.send_sms = lambda to, body: True

failures = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)

TOKEN = "t"
owner = appmod.app.test_client()
with owner.session_transaction() as s:
    s["_csrf_token"] = TOKEN
owner.post("/signup", data={"business_name": "Palm Props", "email": "o@palm.com", "password": "Passw0rd123", "csrf_token": TOKEN})
with owner.session_transaction() as s:
    s["_csrf_token"] = TOKEN
H = {"X-CSRF-Token": TOKEN}
d = _db.SessionLocal(); KEY = d.query(Client).filter(Client.email == "o@palm.com").first().api_key; d.close()

def save(config):
    return owner.put("/api/portal/leasing", json={"vertical": "long_term", "config": config}, headers=H)

base = {"questions": L.PRESETS["long_term"], "notify_email": "alerts@palm.com"}
r = save(dict(base, properties=["Palm Court", "Bayview Townhomes"], resident_contact="Call the office at (239) 555-0100."))
check("portal saves properties and resident contact", r.status_code == 200
      and r.get_json()["config"]["properties"] == ["Palm Court", "Bayview Townhomes"]
      and r.get_json()["config"]["resident_contact"].startswith("Call the office"))
check("property names can't steer", save(dict(base, properties=["Great for families Villas"])).status_code == 400)
check("resident contact has a length cap", save(dict(base, resident_contact="x" * 301)).status_code == 400)
save(dict(base, properties=["Palm Court", "Bayview Townhomes"], resident_contact="Call the office at (239) 555-0100."))

w = appmod.app.test_client()
def chat(msg, sid=None):
    rate_limit.reset()
    body = {"message": msg}
    if sid:
        body["session_id"] = sid
    d = w.post("/widget/chat", json=body, headers={"X-Magni-API-Key": KEY}).get_json() or {}
    return d.get("response", ""), d.get("session_id")

def lead_for(sid):
    d = _db.SessionLocal()
    try:
        return d.query(Lead).filter(Lead.session_id == sid).first()
    finally:
        d.close()

# --- Multiple properties ---
STUB.update(triage_intent="leasing", extract={})
resp, sid = chat("Do you have anything for rent?")
check("property question lists the owner's properties", "1. Palm Court" in resp and "2. Bayview Townhomes" in resp)
resp, _ = chat("2", sid)
check("a number picks the listed property", lead_for(sid).answers.get("property") == "Bayview Townhomes")
resp2, sid2 = chat("I'm interested in renting")
chat("the palm court one please", sid2)
check("naming a property picks it", lead_for(sid2).answers.get("property") == "Palm Court")
check("resolver hears the property list", any("PROPERTIES this business lists" in s and "Bayview Townhomes" in s for s in SYSTEMS))
check("intake side answers know the business name", any("the AI assistant for Palm Props" in s for s in SYSTEMS))

# --- Current residents ---
STUB.update(triage_intent="resident", resolver="The office handles repairs and rent questions.")
resp, rsid = chat("My kitchen sink is leaking, who do I call?")
check("resident reply includes the owner's office contact", "Call the office at (239) 555-0100." in resp)
check("resident does not become a lead", lead_for(rsid) is None)
check("resident reply has no 911 line for a non-emergency", "911" not in resp)
resp, _ = chat("I smell gas in my apartment")
check("resident emergency gets the 911 line first", "call 911 first" in resp)
resp, _ = chat("Huele a gas en mi apartamento, ¿qué hago?")
check("Spanish resident emergency gets the Spanish 911 line", "llame primero al 911" in resp)

# --- After-hours tagging ---
real_now = S.now_utc
STUB.update(triage_intent="leasing", resolver="Happy to help.", extract={})
S.now_utc = lambda: datetime(2026, 10, 25, 15, 0, tzinfo=timezone.utc)  # Sunday 11am ET (closed)
_, ah = chat("Is the 2 bedroom available?")
S.now_utc = lambda: datetime(2026, 10, 21, 18, 0, tzinfo=timezone.utc)  # Wednesday 2pm ET (open)
_, bh = chat("Is the 2 bedroom available?")
S.now_utc = real_now
check("lead on a closed day is tagged after hours", lead_for(ah).flags.get("after_hours") is True)
check("lead during business hours is not tagged", lead_for(bh).flags.get("after_hours") is False)
sched = S.normalize_schedule({"hours": {"mon": ["09:00", "17:00"]}, "blocked_dates": ["2026-10-26"]})
check("a day off counts as after hours", S.is_after_hours(sched, datetime(2026, 10, 26, 15, 0, tzinfo=timezone.utc)))
subj, body, sms = notifier.build_lead_alert("complete", "Palm Props", {"name": "Al", "summary": [], "flags": {"after_hours": True, "lang": "es"}})
check("owner alert says it came in after hours and in Spanish",
      "after your business hours" in body and "in Spanish" in body and "After hours" in sms)

# --- Spanish detection ---
check("Spanish is detected", detect_lang("Hola, ¿tienen apartamentos disponibles?") == "es" and detect_lang("busco casa para rentar") == "es")
check("English is detected", detect_lang("Is the 2 bedroom available?") == "en")
check("short replies don't flip the language", detect_lang("2") is None and detect_lang("María López") is None)
check("Spanish accommodation request is recognized", L.detect_accommodation("Tengo un perro de apoyo emocional"))
check("Spanish opt-out is recognized", L.is_opt_out("No gracias"))
check("Spanish steering is caught", L.find_steering("Es ideal para familias y está en un barrio seguro"))

# --- Spanish intake, end to end, with booking ---
save(dict(base, properties=["Palm Court", "Bayview Townhomes"],
          schedule={"enabled": True, "timezone": "America/New_York", "slot_minutes": 30, "min_notice_hours": 0,
                    "max_days_ahead": 14, "hours": {k: ["09:00", "17:00"] for k in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}}))
STUB.update(triage_intent="leasing", extract={}, resolver="¡Claro! Con gusto le ayudo.")
SENT.clear()
resp, es = chat("Hola, ¿tienen apartamentos disponibles?")
check("Spanish prospect gets the fixed Spanish intro and property question",
      "Puedo pasar sus datos" in resp and "¿Qué propiedad o unidad le interesa?" in resp and "(Responda con un número o el nombre.)" in resp)
check("lead remembers Spanish", lead_for(es).flags.get("lang") == "es")
STUB["extract"] = {"move_in": "enero", "name": "María López", "occupants": "2", "lease_length": "12 meses",
                   "budget": "1500", "pets": "no"}
resp, _ = chat("1", es)
check("ack and next question stay in Spanish",
      resp.split("\n")[0] in t("acks", "es") and "¿Cuál es el mejor número de teléfono para contactarle?" in resp)
STUB["extract"] = {}
resp, _ = chat("(239) 555-0123, maria@example.com", es)
check("booking offer is in Spanish", "Estos son los próximos horarios disponibles para una visita" in resp and "Responda con un número" in resp)
check("offered times use Spanish day names", any(day + "," in resp for day in ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")))
STUB["extract"] = {}
resp, _ = chat("el primero", es)
check("Spanish pick books the time", "quedó reservada" in resp and "¿Necesita cancelar?" in resp)
d = _db.SessionLocal(); appt = d.query(Appointment).first(); d.close()
check("appointment saved", appt is not None and appt.email == "maria@example.com")
subjects = [s for _, s, _ in SENT]
check("prospect confirmation email is in Spanish", any(s.startswith("Confirmada:") for s in subjects))
check("owner alert stays in English", any(s.startswith("New lead + appointment booked") for s in subjects))
check("Spanish closing after booking", "¿Hay algo más en lo que pueda ayudarle" in resp)

STUB.update(triage_intent="general", resolver="Es ideal para familias.")
resp, _ = chat("¿Cómo es la zona?", es)
check("Spanish steering reply is replaced with the Spanish fallback", resp == t("safe_fallback", "es"))

resp, es2 = chat("Hola, busco un apartamento para rentar")
STUB["triage_intent"] = "leasing"
resp, es2 = chat("Hola, busco un apartamento para rentar")
resp, _ = chat("No gracias", es2)
check("Spanish opt-out gets the Spanish reply", resp == t("opt_out", "es"))

# --- Spanish slot parsing ---
from datetime import date
check("Spanish day and time parse", S._parse_day("el sábado", date(2026, 10, 23)) == date(2026, 10, 24)
      and S._parse_time("a las 11 de la mañana")[1].hour == 11)
check("'por la mañana' is a part of day, not tomorrow", S._parse_day("por la mañana", date(2026, 10, 23)) is None
      and S._parse_time("por la mañana")[0] == "window")
check("Spanish ordinal picks", S._pick_number("la segunda", 3) == 2 and S._pick_number("el último", 3) == 3)

print()
if failures:
    print(f"{len(failures)} FAILED")
    sys.exit(1)
print("ALL PASSED")
