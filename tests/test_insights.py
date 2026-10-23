"""Owner insights: counted only from the tenant's own data, no model calls,
suggestions only past their minimum sample sizes and always with the numbers
behind them, contact details masked in examples, tenant-scoped.
Run: python tests/test_insights.py
"""
import os, sys, uuid
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("PUBLIC_BASE_URL", "https://app.example.com")

import core.llm as _llm
def _no_model(*a, **k):
    raise AssertionError("insights must never call the model")
_llm.generate = _no_model
_llm.chat = _no_model

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
from core.insights import build_insights, PROSPECT_TOPICS, RESIDENT_TOPICS
from core.leasing import normalize_lead_config

failures = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)

now = datetime.now(timezone.utc)
d = _db.SessionLocal()
for cid in ("a", "b"):
    d.add(Client(id=cid, api_key="k-" + cid, tier="starter", is_active=True, monthly_used=0))
d.commit()
n = {"i": 0}

def chat(cid, turns, intent="leasing", days_ago=2):
    n["i"] += 1
    sid = f"{cid}-c{n['i']}"
    when = now - timedelta(days=days_ago)
    d.add(Conversation(session_id=sid, client_id=cid, started_at=when, message_count=len(turns) * 2, intents=[intent]))
    d.flush()
    for user, bot in turns:
        d.add(Message(session_id=sid, role="user", content=user, timestamp=when))
        d.add(Message(session_id=sid, role="assistant", content=bot, timestamp=when))
    d.flush()

def lead(cid, status="complete", pending=None, answers=None, flags=None, phone="(239) 555-0100", days_ago=2):
    n["i"] += 1
    when = now - timedelta(days=days_ago)
    d.add(Lead(id=f"{cid}-l{n['i']}", client_id=cid, session_id=f"s{n['i']}", status=status, pending_key=pending,
               answers=answers or {"name": "X"}, flags=flags or {}, contact_phone=phone, created_at=when, updated_at=when))
    d.flush()

UNSURE = "I'm not sure about that, but our team will confirm."
# Tenant A prospects: 3 unanswered parking questions, 1 answered price question.
chat("a", [("Is there parking for 2 cars? text me at 239-555-0123", UNSURE)])
chat("a", [("Where do guests park?", UNSURE)])
chat("a", [("Do you have covered parking? me@x.com", "No hay estacionamiento techado, el equipo puede confirmar.")])
chat("a", [("How much is the rent for the 2 bedroom?", "The 2 bedroom is $1,750 a month.")])
chat("a", [("What's the weather like?", UNSURE)])
# Tenant A residents: plumbing three times, one noise.
chat("a", [("My kitchen sink is leaking", "Please contact the office.")], intent="resident")
chat("a", [("The toilet is clogged again", "Please contact the office.")], intent="resident")
chat("a", [("Tengo una fuga en el baño", "Comuníquese con la oficina.")], intent="resident")
chat("a", [("Neighbors are really loud at night", "Please contact the office.")], intent="resident")
# Old chat outside a 30-day window.
chat("a", [("Is parking included?", UNSURE)], days_ago=60)

# Leads: 5 finished with a typed tour time (booking off), 4 stopped at budget, 1 at phone.
for i in range(5):
    lead("a", answers={"name": "X", "property": "Palm Court" if i < 3 else "Bayview", "tour_time": "Saturday morning"},
         flags={"after_hours": i < 3, "lang": "es" if i == 0 else "en"})
for _ in range(4):
    lead("a", status="in_progress", pending="budget", flags={"after_hours": True})
lead("a", status="abandoned", pending="phone", phone=None, answers={"name": "Y"})
d.add(Appointment(id="ap1", client_id="a", start_at=now + timedelta(days=1), end_at=now + timedelta(days=1, minutes=30),
                  status="booked", cancel_token=uuid.uuid4().hex, created_at=now - timedelta(days=1), property="Palm Court"))
d.add(Appointment(id="ap2", client_id="a", start_at=now + timedelta(days=2), end_at=now + timedelta(days=2, minutes=30),
                  status="canceled", cancel_token=uuid.uuid4().hex, created_at=now - timedelta(days=1), property="Bayview"))
# Tenant B: one resident chat that must never show up for A.
chat("b", [("Roaches in my unit", "Please contact the office.")], intent="resident")
lead("b", answers={"name": "B", "property": "B Tower"})
d.commit(); d.close()

cfg = normalize_lead_config({"questions": ["property", "name", "phone", "budget", "tour_time"]}, "long_term")
ins = build_insights("a", cfg, 30)
kinds = {s["kind"]: s for s in ins["suggestions"]}

check("built without any model call", True)
check("unanswered questions are counted from the assistant's own 'team will confirm' replies (EN + ES)",
      ins["unanswered"]["total"] == 4)
park = next((t for t in ins["unanswered"]["topics"] if t["key"] == "parking"), None)
check("unanswered questions are grouped by topic", park is not None and park["count"] == 3)
check("off-topic unanswered questions are listed, not forced into a topic",
      any("weather" in q for q in ins["unanswered"]["other_examples"]))
check("knowledge-gap suggestion fires at 3 and shows its basis",
      "knowledge_gap" in kinds and "parking" in kinds["knowledge_gap"]["title"]
      and kinds["knowledge_gap"]["basis"].startswith("3 unanswered"))
check("examples mask phone numbers and emails",
      all("555-0123" not in q and "me@x.com" not in q for q in park["examples"])
      and any("[phone]" in q or "[email]" in q for q in park["examples"]))
check("old chats outside the period are not counted",
      sum(t["count"] for t in ins["prospect_topics"] if t["key"] == "parking") == 3)
check("answered topics still show in 'what prospects ask about'",
      any(t["key"] == "price" for t in ins["prospect_topics"]))

res = ins["resident"]
check("resident chats are separated from prospect chats", res["chats"] == 4 and ins["funnel"][0]["count"] == 5)
check("resident topics counted in English and Spanish", res["topics"][0]["key"] == "plumbing" and res["topics"][0]["count"] == 3)
check("resident trend suggestion quotes residents' own words",
      "resident_trend" in kinds and len(kinds["resident_trend"]["examples"]) == 2)
check("missing resident contact is suggested when residents ask", "resident_contact" in kinds)

check("funnel counts real leads, contact, finished, tours",
      [f["count"] for f in ins["funnel"]] == [5, 10, 9, 5, 2])
check("canceled tours reported", ins["tours_canceled"] == 1)
check("drop-off counts where unfinished intakes stopped",
      ins["drop_off"][0]["key"] == "budget" and ins["drop_off"][0]["count"] == 4 and ins["drop_off"][0]["share"] == 80)
check("drop-off suggestion offers turning off an optional question",
      "drop_off" in kinds and "turning the question off" in kinds["drop_off"]["detail"])
check("booking suggestion fires when booking is off and 5 prospects typed a tour time", "booking" in kinds)
check("after-hours share", ins["timing"]["after_hours"] == 7 and ins["timing"]["after_hours_share"] == 70 and "after_hours" in kinds)
check("Spanish share", ins["timing"]["spanish"] == 1 and "spanish" not in kinds)  # 1 is below the minimum
props = {p["name"]: p for p in ins["properties"]}
check("by property: leads, finished, tours (canceled tours excluded)",
      props["Palm Court"] == {"name": "Palm Court", "leads": 3, "finished": 3, "tours": 1}
      and props["Bayview"]["tours"] == 0)
check("tenant B's data never appears", "B Tower" not in props and all("Roach" not in q for t in res["topics"] for q in t["examples"]))
check("every suggestion carries its numbers", all(s["basis"] and any(c.isdigit() for c in s["basis"]) for s in ins["suggestions"]))

# Below the minimums: tenant B gets no suggestions at all.
check("no suggestions without enough data", build_insights("b", cfg, 30)["suggestions"] == [])
# Booking on: no booking suggestion.
cfg_on = normalize_lead_config({"questions": ["property", "name", "phone", "budget", "tour_time"],
                                "schedule": {"enabled": True}, "resident_contact": "Call the office"}, "long_term")
k2 = {s["kind"] for s in build_insights("a", cfg_on, 30)["suggestions"]}
check("booking suggestion disappears once booking is on", "booking" not in k2 and "resident_contact" not in k2)
check("only 7, 30 or 90 days", build_insights("a", cfg, 365)["days"] == 30 and build_insights("a", cfg, 90)["days"] == 90)
check("90 days includes the older chat", sum(t["count"] for t in build_insights("a", cfg, 90)["unanswered"]["topics"] if t["key"] == "parking") == 4)

# Fair Housing: no topic about people or "good areas".
labels = " ".join(l for _, l, _ in PROSPECT_TOPICS + RESIDENT_TOPICS).lower()
check("no topics about schools, safety of an area, or who lives there",
      not any(w in labels for w in ("school", "safe area", "neighborhood", "famil", "kids", "children")))

if not _PG:
    os.environ["FLASK_ENV"] = "production"
    import app as appmod
    c = appmod.app.test_client()
    check("insights endpoint requires login", c.get("/api/portal/insights").status_code == 401)
    with c.session_transaction() as s:
        s["client_id"] = "a"; s["client_login_time"] = __import__("time").time()
    r = c.get("/api/portal/insights?days=30")
    check("portal insights endpoint returns the owner's own report",
          r.status_code == 200 and r.get_json()["unanswered"]["total"] == 4)
    a = appmod.app.test_client()
    check("admin insights endpoint requires the admin login", a.get("/api/analytics/insights?client_id=a").status_code == 401)

if _PG:
    d = _db.SessionLocal()
    for model in (Message, Appointment, Lead, Conversation, Client):
        d.query(model).delete()
    d.commit(); d.close()

print()
if failures:
    print(f"{len(failures)} FAILED")
    sys.exit(1)
print("ALL PASSED")
