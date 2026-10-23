"""Built-in scheduler tests: availability (time zones, DST, notice, blocked days),
understanding replies ("2", "Saturday at 11", "tomorrow afternoon"), booking
with the double-booking guard, the booking flow end to end through the real
widget, confirmation/cancel/reminder emails, the public cancel page, and the
portal APIs.

Flask test client on an isolated SQLite DB (or MAGNI_TEST_DATABASE_URL for an
empty, migrated Postgres). The LLM and email are stubbed; the clock is frozen.
Run: python tests/test_scheduler.py
"""
import os, sys, json, re
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ["PUBLIC_BASE_URL"] = "https://app.example.com"

STUB = {"triage_intent": "leasing", "extract": {}, "resolver": "Happy to help."}
import core.llm as llm
def _gen(prompt, system=None, **kw):
    si = system or ""
    if "triage classifier" in si:
        return json.dumps({"intent": STUB["triage_intent"], "confidence": 0.9, "route": "resolver", "escalation_reason": None})
    if "extract answers" in si:
        return json.dumps({"answers": STUB["extract"].get("answers", {}), "asked_question": STUB["extract"].get("asked_question", False),
                           "needs_human": False, "declined": False})
    return "stub"
llm.generate = _gen
llm.chat = lambda system, history, message, **kw: STUB["resolver"]

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

import app as appmod
from core import scheduling as S, notifier, lead_flow, rate_limit, leasing as L
from core.models import Client, Lead, Appointment

notifier.RUN_IN_BACKGROUND = False
SENT = []
notifier.send_email = lambda to, subj, body, reply_to=None, ics=None, from_name=None: SENT.append(
    {"to": to, "subject": subj, "body": body, "reply_to": reply_to, "ics": ics, "from": from_name}) or True
notifier.send_sms = lambda to, body: True

# Monday Oct 26 2026, 9:00 AM Eastern (EDT, UTC-4)
NOW = datetime(2026, 10, 26, 13, 0, tzinfo=timezone.utc)
S.NOW_OVERRIDE = NOW

failures = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)

def utc(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)

SCHED = S.normalize_schedule({"enabled": True, "timezone": "America/New_York", "slot_minutes": 30,
                              "min_notice_hours": 2, "max_days_ahead": 14,
                              "hours": {"mon": ["09:00", "17:00"], "tue": ["09:00", "17:00"], "wed": ["09:00", "17:00"],
                                        "thu": ["09:00", "17:00"], "fri": ["09:00", "17:00"], "sat": ["10:00", "14:00"], "sun": None},
                              "blocked_dates": ["2026-10-28"], "details": "Meet at the leasing office, 12 Palm St."}, strict=True)

# ════════════════════════════════════════════════════════════════════════════
print("\n-- config validation --")
for bad, label in ((dict(timezone="Mars/Base"), "bad time zone"), (dict(slot_minutes=25), "odd slot length"),
                   (dict(hours={"mon": ["17:00", "09:00"]}), "start after end"),
                   (dict(blocked_dates=["Nov 26"]), "bad blocked date"),
                   (dict(enabled=True, hours={d: None for d in S.DAYS}), "enabled with no days"),
                   (dict(details="Perfect for young families!"), "steering in details")):
    try:
        S.normalize_schedule(bad, strict=True); ok = False
    except S.ScheduleError:
        ok = True
    check(f"rejects {label}", ok)
check("lenient read falls back to defaults", S.normalize_schedule({"slot_minutes": "x"})["slot_minutes"] == 30)
check("blocked dates accept a comma string", S.normalize_schedule({"blocked_dates": "2026-11-26, 2026-12-25"})["blocked_dates"] == ["2026-11-26", "2026-12-25"])

print("\n-- availability --")
CID = "sched-unit"
db = _db.SessionLocal(); db.add(Client(id=CID, api_key="k-unit", tier="starter", is_active=True, monthly_used=0)); db.commit(); db.close()
slots = S.open_slots(CID, SCHED)
check("first slot respects 2h notice (11:00 ET)", slots[0] == utc(2026, 10, 26, 15))
check("no Sunday slots", not any(s.astimezone(S.ZoneInfo("America/New_York")).weekday() == 6 for s in slots))
check("blocked date has no slots", not any(s.astimezone(S.ZoneInfo("America/New_York")).date().isoformat() == "2026-10-28" for s in slots))
check("Saturday window 10-14 gives 8 slots",
      len([s for s in slots if s.astimezone(S.ZoneInfo("America/New_York")).date().isoformat() == "2026-10-31"]) == 8)
check("last slot ends at closing (4:30 PM start)", any(s == utc(2026, 10, 26, 20, 30) for s in slots) and not any(s == utc(2026, 10, 26, 21) for s in slots))
check("DST: Mon Nov 2 9:00 ET is 14:00 UTC", utc(2026, 11, 2, 14) in slots)
check("booking window stops after 14 days", max(slots) < utc(2026, 11, 10, 5))
offer = S.spread_offer(slots, SCHED)
days = [s.astimezone(S.ZoneInfo("America/New_York")).date() for s in offer]
check("offer has 5 slots, max 2 per day", len(offer) == 5 and max(days.count(d) for d in days) == 2)
check("slot formatting", S.fmt_slot(utc(2026, 10, 26, 15), SCHED) == "Mon, Oct 26 at 11:00 AM ET")

print("\n-- understanding replies --")
pc = lambda msg, offered=offer: S.parse_choice(msg, offered, slots, SCHED)
check("'2' picks the second offer", pc("2") == ("slot", offer[1], True))
check("'#3 please' picks the third", pc("#3 please") == ("slot", offer[2], True))
check("'the first one' picks the first", pc("the first one") == ("slot", offer[0], True))
check("'7' (not offered) isn't a pick", pc("7") is None or pc("7")[0] != "slot")
check("'Saturday at 11' finds Sat 11:00 ET but asks to confirm", pc("Saturday at 11") == ("slot", utc(2026, 10, 31, 15), False))
check("'sat 11:30am' is a sure pick", pc("sat 11:30am") == ("slot", utc(2026, 10, 31, 15, 30), True))
r = pc("Do you have anything Saturday?")
check("'Saturday' offers Saturday times", r[0] == "choices" and all(s.astimezone(S.ZoneInfo("America/New_York")).weekday() == 5 for s in r[1]))
r = pc("tomorrow afternoon")
check("'tomorrow afternoon' offers Tue 12-5 PM", r[0] == "choices" and all(16 <= s.hour < 21 and s.day == 27 for s in r[1]))
check("'Thursday at 3' means 3 PM (confirm first)", pc("Thursday at 3") == ("slot", utc(2026, 10, 29, 19), False))
check("'10/30 at 2pm' is a sure pick", pc("10/30 at 2pm") == ("slot", utc(2026, 10, 30, 18), True))
for msg in ("I have two dogs", "Just two of us", "Is there one parking spot?", "Do I pay first month?",
            "I currently live at 11 Palm St", "meet at 10 Main St", "$1800 a month", "2 people", "239-555-0100"):
    check(f"not a booking: {msg!r}", pc(msg) is None)
check("a question never books outright", pc("Saturday at 11:30am?")[2] is False)
check("'Sunday' is unavailable", pc("how about Sunday")[0] == "unavailable")
check("blocked Wednesday is unavailable", pc("wednesday at 10")[0] == "unavailable")
check("small talk isn't a pick", pc("hmm let me think") is None)
check("'I have 2 dogs' isn't a time", pc("I have 2 dogs") is None)

print("\n-- booking guard --")
a1 = S.book(CID, None, slots[0], SCHED, {"name": "A"})
a2 = S.book(CID, None, slots[0], SCHED, {"name": "B"})
check("first booking succeeds", a1 is not None and a1.status == "booked")
check("same slot can't be booked twice", a2 is None)
check("booked slot disappears from availability", slots[0] not in S.open_slots(CID, SCHED))
# Bypass the availability check to prove the database index itself blocks a duplicate.
from sqlalchemy.exc import IntegrityError
db = _db.SessionLocal()
db.add(Appointment(id="dup", client_id=CID, start_at=slots[0], end_at=slots[0] + timedelta(minutes=30),
                   status="booked", cancel_token="tok-dup", created_at=NOW))
try:
    db.commit(); idx_ok = False
except IntegrityError:
    db.rollback(); idx_ok = True
db.close()
check("database index blocks a duplicate booked slot", idx_ok)
check("cancel frees the slot", S.cancel(a1.id, client_id=CID) is not None and slots[0] in S.open_slots(CID, SCHED))
check("canceling twice is a no-op", S.cancel(a1.id, client_id=CID) is None)
check("canceled slot can be rebooked", S.book(CID, None, slots[0], SCHED, {"name": "C"}) is not None)
inv = S.ics(a1, "Tour, with us; ok", "line1\nline2", "12 Palm St")
check("calendar invite has UTC times and escaping",
      "DTSTART:20261026T150000Z" in inv and "SUMMARY:Tour\\, with us\\; ok" in inv and "\r\n" in inv)

# ════════════════════════════════════════════════════════════════════════════
print("\n-- booking in the chat --")
TOKEN = "t"
class C:
    def __init__(self, c): self.c = c
    def _h(self, kw):
        h = dict(kw.pop("headers", None) or {}); h["X-CSRF-Token"] = TOKEN; kw["headers"] = h; return kw
    def get(self, *a, **k): return self.c.get(*a, **k)
    def post(self, *a, **k): return self.c.post(*a, **self._h(k))
    def put(self, *a, **k): return self.c.put(*a, **self._h(k))
def tok(c):
    with c.session_transaction() as s:
        s["_csrf_token"] = TOKEN
def signup(email):
    c = appmod.app.test_client(); tok(c)
    c.post("/signup", data={"business_name": "Palm Props", "email": email, "password": "Passw0rd123", "csrf_token": TOKEN})
    tok(c); return C(c)
def row(email):
    db = _db.SessionLocal()
    try:
        return db.query(Client).filter(Client.email == email).first()
    finally:
        db.close()

A = signup("owner@palm.com"); B = signup("owner@other.com")
A_KEY, B_KEY = row("owner@palm.com").api_key, row("owner@other.com").api_key
cfg = {"questions": ["property", "name", "phone", "email"], "schedule": {**SCHED, "blocked_dates": []}}
r = A.put("/api/portal/leasing", json={"vertical": "long_term", "config": cfg})
check("leasing save with schedule -> 200", r.status_code == 200 and r.get_json()["config"]["schedule"]["enabled"])
check("invalid schedule -> 400 with message",
      A.put("/api/portal/leasing", json={"vertical": "long_term", "config": {**cfg, "schedule": {"enabled": True, "hours": {"mon": ["18:00", "08:00"]}}}}).status_code == 400)

w = appmod.app.test_client()
def chat(key, msg, sid=None, extract=None):
    rate_limit.reset()
    STUB["extract"] = {"answers": extract or {}}
    body = {"message": msg, **({"session_id": sid} if sid else {})}
    d = w.post("/widget/chat", json=body, headers={"X-Magni-API-Key": key}).get_json()
    return d["response"], d["session_id"]
def lead(sid):
    db = _db.SessionLocal()
    try:
        return db.query(Lead).filter(Lead.session_id == sid).first()
    finally:
        db.close()
def intake(name, email, phone="2395550100", first="Hi, I'd like to see the 2BR on Palm"):
    resp, sid = chat(A_KEY, first, extract={"property": "2BR on Palm"})
    chat(A_KEY, name, sid, extract={"name": name})
    chat(A_KEY, phone, sid)
    resp, _ = chat(A_KEY, email, sid)
    return resp, sid

resp, sid = intake("Maria Lopez", "maria@x.com")
check("tour step offers real numbered times", resp.startswith("Great") or "Here are the next open times for a tour:" in resp)
check("offer lists 5 options + how to reply", all(f"\n{i}. " in resp for i in range(1, 6)) and "Reply with a number" in resp)
check("offer shows the first open slot (Mon 11:00 AM)", "1. Mon, Oct 26 at 11:00 AM" in resp)
SENT.clear()
resp, _ = chat(A_KEY, "2", sid)
L1 = lead(sid)
check("picking '2' books it", "You're booked for a tour on Mon, Oct 26 at 11:30 AM ET." in resp)
check("reply includes meeting details + cancel link", "12 Palm St" in resp and "https://app.example.com/appt/" in resp)
check("intake completes after booking", L1.status == "complete" and L1.answers["tour_time"] == "Booked: Mon, Oct 26 at 11:30 AM ET")
pros = [m for m in SENT if m["to"] == "maria@x.com"]
own = [m for m in SENT if m["to"] == "owner@palm.com"]
check("prospect gets a confirmation with a calendar invite", len(pros) == 1 and pros[0]["ics"] and "Confirmed:" in pros[0]["subject"])
check("confirmation comes from the business, replies go to the owner", pros[0]["from"] == "Palm Props" and pros[0]["reply_to"] == "owner@palm.com")
check("owner alert says appointment booked + has an invite", len(own) == 1 and "appointment booked" in own[0]["subject"] and own[0]["ics"])
appts = A.get("/api/portal/appointments").get_json()["appointments"]
check("portal lists the appointment", len(appts) == 1 and appts[0]["name"] == "Maria Lopez" and appts[0]["status"] == "booked")
check("B can't see A's appointments", B.get("/api/portal/appointments").get_json()["appointments"] == [])

resp2, sid2 = intake("Sam", "sam@x.com")
check("next prospect's offer excludes the booked slot", "Mon, Oct 26 at 11:30 AM" not in resp2.split("Here are")[1])
resp, _ = chat(A_KEY, "Do you have anything Saturday?", sid2)
check("asking for a day offers that day's times", "Here's what's open:" in resp and "Sat, Oct 31" in resp)
resp, _ = chat(A_KEY, "11:30", sid2)
check("then a time on that list books it", "You're booked for a tour on Sat, Oct 31 at 11:30 AM ET." in resp)

resp8, sid8 = intake("Ray", "ray@x.com")
resp, _ = chat(A_KEY, "Thursday at 3", sid8)
check("loose pick asks to confirm first", resp == "Want me to book a tour on Thu, Oct 29 at 3:00 PM ET? (yes / no)" and lead(sid8).status == "in_progress")
resp, _ = chat(A_KEY, "no", sid8)
check("'no' to the confirm re-offers", "open times again" in resp and lead(sid8).status == "in_progress")
resp, _ = chat(A_KEY, "Thursday at 3", sid8)
resp, _ = chat(A_KEY, "yes please", sid8)
check("'yes' books it", "You're booked for a tour on Thu, Oct 29 at 3:00 PM ET." in resp)
resp11, sid11 = intake("Bea", "bea@x.com")
chat(A_KEY, "Saturday at 11", sid11)
resp, _ = chat(A_KEY, "Sure, but can we do Friday at 2pm instead?", sid11)
check("'Sure, but Friday at 2pm instead?' doesn't book the pending Saturday slot", "Sat, Oct 31 at 11:00 AM ET" not in resp or "Want me to book" in resp)
check("...it proposes Friday 2 PM instead", "Fri, Oct 30 at 2:00 PM ET" in resp)
resp, _ = chat(A_KEY, "yes", sid11)
check("...and 'yes' books Friday", "You're booked for a tour on Fri, Oct 30 at 2:00 PM ET." in resp)
resp, _ = chat(A_KEY, "I have two dogs", (intake("Ivy", "ivy@x.com")[1]))
check("'I have two dogs' at the time step books nothing", "You're booked" not in resp)

resp9, sid9 = intake("Gus", "gus@x.com")
resp, _ = chat(A_KEY, "Do you have anything later in the week?", sid9)
check("'later in the week' isn't a decline", lead(sid9).status == "in_progress" and "Didn't book" not in str(lead(sid9).answers))
resp, _ = chat(A_KEY, "can I talk to a real person?", sid9)
check("asking for a person at the time step goes to a human", "Here are the next" not in resp and lead(sid9).status == "in_progress")

resp10, sid10 = intake("Dot", "dot@x.com")
L10 = lead(sid10)
first = datetime.fromisoformat(L10.flags["offered"][0])
S.book(row("owner@palm.com").id, L10.id, first, {**SCHED, "blocked_dates": []}, {"name": "Dot"})  # a parallel turn already booked
resp, _ = chat(A_KEY, "1", sid10)
db = _db.SessionLocal(); n_dot = db.query(Appointment).filter(Appointment.lead_id == L10.id, Appointment.status == "booked").count(); db.close()
check("double-tap: second turn reuses the booking, no second appointment", n_dot == 1 and "You're booked" in resp and lead(sid10).status == "complete")
db = _db.SessionLocal()
second = datetime.fromisoformat(L10.flags["offered"][1])
db.add(Appointment(id="dup-lead", client_id=row("owner@palm.com").id, lead_id=L10.id, start_at=second,
                   end_at=second + timedelta(minutes=30), status="booked", cancel_token="tok-dup-lead", created_at=NOW))
try:
    db.commit(); lead_idx = False
except IntegrityError:
    db.rollback(); lead_idx = True
db.close()
check("database blocks a second booking for the same prospect", lead_idx)

resp3, sid3 = intake("Lee", "lee@x.com")
resp, _ = chat(A_KEY, "how about Sunday", sid3)
check("unavailable day explains and re-offers", resp.startswith("Nothing's open on Sunday") and "\n1. " in resp)
resp, _ = chat(A_KEY, "blah", sid3)
check("unclear reply re-asks once", resp.startswith("Sorry, I didn't quite catch that."))
resp, _ = chat(A_KEY, "whenever", sid3)
check("second unclear reply hands off to the team", "our team will confirm a time" in resp and lead(sid3).status == "complete"
      and lead(sid3).answers["tour_time"].startswith("Prefers:"))

resp4, sid4 = intake("Pat", "pat@x.com")
resp, _ = chat(A_KEY, "not now, thanks", sid4)
check("declining to book completes the intake", lead(sid4).status == "complete" and "Didn't book online" in lead(sid4).answers["tour_time"])

resp5, sid5 = intake("Kim", "kim@x.com")
offered = lead(sid5).flags["offered"]
S.book(row("owner@palm.com").id, None, datetime.fromisoformat(offered[0]), {**SCHED, "blocked_dates": []}, {"name": "someone else"})
resp, _ = chat(A_KEY, "1", sid5)
check("slot taken mid-chat -> apologize and re-offer", resp.startswith("Sorry, that time was just taken") and lead(sid5).status == "in_progress")

resp6, sid6 = intake("Jo", "jo@x.com", first="Can I tour the 2BR Saturday at 11?")
check("time mentioned early is offered first", "1. Sat, Oct 31 at 11:00 AM" in resp6)

resp, sid7 = chat(A_KEY, "Is the 2BR available?", extract={"property": "2BR"})
chat(A_KEY, "Al", sid7, extract={"name": "Al"})
chat(A_KEY, "skip", sid7)
resp, _ = chat(A_KEY, "skip", sid7)
check("no phone or email -> no booking offered", "Here are the next open times" not in resp and lead(sid7).status == "complete")

print("\n-- after the intake --")
resp, _ = chat(A_KEY, "I need to reschedule my tour", sid)
check("reschedule request gets the cancel link", "You're booked for Mon, Oct 26 at 11:30 AM ET" in resp and "/appt/" in resp)
STUB["triage_intent"] = "general"
resp, _ = chat(A_KEY, "What's the view like from the balcony?", sid4)
check("'view' question doesn't reopen booking", "Here are the next open times" not in resp)
resp, _ = chat(A_KEY, "Is there a payment schedule for the deposit?", sid4)
check("'payment schedule' doesn't reopen booking", "Here are the next open times" not in resp)
resp, _ = chat(A_KEY, "Can I book a tour?", sid4)
check("clear booking request after the intake offers times", "Here are the next open times for a tour:" in resp and lead(sid4).status == "complete")
resp, _ = chat(A_KEY, "no thanks", sid4)
check("declining then keeps the finished lead intact", lead(sid4).status == "complete" and "Didn't book online" in lead(sid4).answers["tour_time"]
      and not (lead(sid4).flags or {}).get("booking_open"))
resp, _ = chat(A_KEY, "Thanks, I'll check back another time", sid4)
check("'check back another time' doesn't reopen booking", "Here are the next open times" not in resp)
resp, _ = chat(A_KEY, "Actually can I book a tour?", sid4)
check("booking after a declined intake offers times (even if triage says general)", "Here are the next open times for a tour:" in resp)
resp, _ = chat(A_KEY, "3", sid4)
check("...and books it", "You're booked for a tour" in resp)
STUB["triage_intent"] = "leasing"

print("\n-- cancel page + owner cancel --")
db = _db.SessionLocal(); maria = db.query(Appointment).filter(Appointment.name == "Maria Lopez").first(); db.close()
pub = appmod.app.test_client()
page = pub.get(f"/appt/{maria.cancel_token}")
check("cancel page shows the appointment", page.status_code == 200 and b"Mon, Oct 26 at 11:30 AM ET" in page.data and b"Cancel appointment" in page.data)
check("unknown token -> 404", pub.get("/appt/not-a-real-token").status_code == 404)
check("cancel without CSRF -> 403", pub.post(f"/appt/{maria.cancel_token}/cancel").status_code == 403)
tok(pub); SENT.clear()
r = pub.post(f"/appt/{maria.cancel_token}/cancel", data={"csrf_token": TOKEN})
check("prospect can cancel", r.status_code == 200 and b"Your appointment is canceled" in r.data)
check("owner is emailed about the cancellation", any(m["to"] == "owner@palm.com" and m["subject"].startswith("Canceled: Maria") for m in SENT))
check("lead shows the cancellation", lead(sid).answers["tour_time"].startswith("Canceled by prospect"))
check("slot is open again", utc(2026, 10, 26, 15, 30) in S.open_slots(row("owner@palm.com").id, {**SCHED, "blocked_dates": []}))
resp, _ = chat(A_KEY, "can I book a different tour time?", sid)
check("after canceling, the prospect can book again", "Here are the next open times" in resp)

db = _db.SessionLocal(); sam = db.query(Appointment).filter(Appointment.name == "Sam", Appointment.status == "booked").first(); db.close()
check("B can't cancel A's appointment", B.post(f"/api/portal/appointments/{sam.id}/cancel", json={}).status_code == 404)
check("owner cancel needs CSRF", A.c.post(f"/api/portal/appointments/{sam.id}/cancel", json={}).status_code == 403)
SENT.clear()
check("owner can cancel", A.post(f"/api/portal/appointments/{sam.id}/cancel", json={}).status_code == 200)
check("prospect is emailed when the owner cancels", any(m["to"] == "sam@x.com" and m["subject"].startswith("Canceled:") for m in SENT))

print("\n-- reminders --")
A_ID = row("owner@palm.com").id
sch = {**SCHED, "blocked_dates": []}
soon = S.book(A_ID, None, utc(2026, 10, 30, 14), sch, {"name": "Rem", "email": "rem@x.com"})   # Fri 10 AM ET
later = S.book(A_ID, None, utc(2026, 11, 2, 15), sch, {"name": "Far", "email": "far@x.com"})   # Mon, days away
SENT.clear()
S.NOW_OVERRIDE = utc(2026, 10, 29, 19)  # Thu 3 PM ET
n1 = lead_flow.send_due_reminders()
due = [m for m in SENT if m["subject"].startswith("Reminder:")]
check("reminder goes to the appointment within 24h", any(m["to"] == "rem@x.com" and "Cancel here" in m["body"] for m in due))
check("no reminder for appointments days away", not any(m["to"] == "far@x.com" for m in due) and n1 == len(due) >= 1)
check("each reminder is sent once", lead_flow.send_due_reminders() == 0)
S.NOW_OVERRIDE = NOW

print("\n-- widget from a real website (CORS) --")
pre = w.open("/widget/chat", method="OPTIONS", headers={"Origin": "https://palmprops.com",
             "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type,x-magni-api-key"})
check("browser preflight is allowed (no API key in preflights)", pre.status_code == 204
      and pre.headers.get("Access-Control-Allow-Origin") == "https://palmprops.com"
      and "X-Magni-API-Key" in pre.headers.get("Access-Control-Allow-Headers", ""))
rate_limit.reset()
r = w.post("/widget/chat", json={"message": "hi"}, headers={"X-Magni-API-Key": A_KEY, "Origin": "https://palmprops.com"})
check("site not in allowed domains -> 403 before any AI call", r.status_code == 403)
A.put("/api/portal/settings", json={"allowed_domains": "palmprops.com"})
rate_limit.reset(); STUB["triage_intent"] = "general"
r = w.post("/widget/chat", json={"message": "hi"}, headers={"X-Magni-API-Key": A_KEY, "Origin": "https://palmprops.com"})
check("allowed site works and gets CORS header", r.status_code == 200 and r.headers.get("Access-Control-Allow-Origin") == "https://palmprops.com")

soonish = S.book(A_ID, None, utc(2026, 10, 29, 20), sch, {"name": "Same", "email": "same@x.com"})  # booked at 3 PM for 4 PM
db = _db.SessionLocal(); db.query(Appointment).filter(Appointment.id == soonish.id).update({"created_at": utc(2026, 10, 29, 19)}); db.commit()
S.NOW_OVERRIDE = utc(2026, 10, 29, 19, 5)
check("same-day bookings skip the day-before reminder", not any(a.id == soonish.id for a in S.due_reminders(db)))
db.close(); S.NOW_OVERRIDE = NOW
check("invite lines are folded to 75 octets", all(len(l.encode()) <= 75 for l in S.ics(soon, "x", "y" * 300).split("\r\n")))

print("\n-- portal page --")
html = A.get("/portal").data
check("portal renders booking settings + appointments", b'id="schEnabled"' in html and b'id="appointments"' in html)

print("\n" + ("ALL PASSED" if not failures else f"{len(failures)} FAILED: {failures}"))
sys.exit(1 if failures else 0)
