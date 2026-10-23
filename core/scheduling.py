"""
core/scheduling.py

Built-in showing / call scheduler for rental clients. No Calendly needed.

Owners set weekly hours, slot length, notice, how far ahead to book, blocked
dates, and meeting details (in the portal, stored at lead_config["schedule"]).
The assistant offers real open slots in the chat; the prospect picks one by
number or by saying a day/time; the booking is written with a database-level
guard so two prospects can never get the same slot.

All times are stored in UTC and shown in the owner's timezone.
"""
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone, date, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.exc import IntegrityError

from core.db import SessionLocal
from core.models import Appointment
from core.utils import logger

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_LABELS = {"mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday",
              "fri": "Friday", "sat": "Saturday", "sun": "Sunday"}
TIMEZONES = {
    "America/New_York": "Eastern", "America/Chicago": "Central", "America/Denver": "Mountain",
    "America/Phoenix": "Arizona", "America/Los_Angeles": "Pacific", "America/Anchorage": "Alaska",
    "Pacific/Honolulu": "Hawaii",
}
SLOT_LENGTHS = (15, 20, 30, 45, 60)
OFFER_COUNT = 5
MAX_PER_DAY_IN_OFFER = 2
MAX_DETAILS = 300
MAX_BLOCKED = 60

DEFAULT_SCHEDULE = {
    "enabled": False,
    "timezone": "America/New_York",
    "slot_minutes": 30,
    "min_notice_hours": 2,
    "max_days_ahead": 14,
    "hours": {"mon": ["09:00", "17:00"], "tue": ["09:00", "17:00"], "wed": ["09:00", "17:00"],
              "thu": ["09:00", "17:00"], "fri": ["09:00", "17:00"], "sat": ["10:00", "14:00"],
              "sun": None},
    "blocked_dates": [],
    "details": "",
}

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

# Tests freeze the clock by setting this.
NOW_OVERRIDE = None


def now_utc() -> datetime:
    return NOW_OVERRIDE or datetime.now(timezone.utc)


class ScheduleError(ValueError):
    pass


def _aware(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ── Config ──────────────────────────────────────────────────────────────────
def normalize_schedule(raw, strict: bool = False) -> dict:
    """Validate an owner-submitted (strict) or stored (lenient) schedule."""
    import copy
    out = copy.deepcopy(DEFAULT_SCHEDULE)
    if not isinstance(raw, dict):
        return out

    def fail(msg):
        if strict:
            raise ScheduleError(msg)

    out["enabled"] = bool(raw.get("enabled", False))

    tz = str(raw.get("timezone") or out["timezone"])
    try:
        ZoneInfo(tz)
        out["timezone"] = tz
    except (ZoneInfoNotFoundError, ValueError):
        fail("Unknown time zone")

    try:
        slot = int(raw.get("slot_minutes", out["slot_minutes"]))
        if slot not in SLOT_LENGTHS:
            raise ValueError
        out["slot_minutes"] = slot
    except (TypeError, ValueError):
        fail("Appointment length must be 15, 20, 30, 45, or 60 minutes")

    for key, lo, hi, label in (("min_notice_hours", 0, 72, "Minimum notice"),
                               ("max_days_ahead", 1, 60, "Booking window")):
        try:
            v = int(raw.get(key, out[key]))
            if not lo <= v <= hi:
                raise ValueError
            out[key] = v
        except (TypeError, ValueError):
            fail(f"{label} must be between {lo} and {hi}")

    hours = raw.get("hours")
    if isinstance(hours, dict):
        clean = {}
        for d in DAYS:
            win = hours.get(d)
            if not win:
                clean[d] = None
                continue
            if (not isinstance(win, (list, tuple)) or len(win) != 2
                    or not all(isinstance(x, str) and _HHMM.match(x) for x in win)):
                fail(f"{DAY_LABELS[d]} hours must look like 09:00 to 17:00")
                clean[d] = out["hours"][d]
                continue
            if win[0] >= win[1]:
                fail(f"{DAY_LABELS[d]}: the start time must be before the end time")
                clean[d] = out["hours"][d]
                continue
            clean[d] = [win[0], win[1]]
        out["hours"] = clean
    elif hours is not None:
        fail("Weekly hours are malformed")

    blocked = raw.get("blocked_dates") or []
    if isinstance(blocked, str):
        blocked = [b for b in re.split(r"[,\s]+", blocked) if b]
    good = []
    for b in blocked if isinstance(blocked, list) else []:
        try:
            iso = date.fromisoformat(str(b).strip()).isoformat()
        except ValueError:
            fail(f"Blocked date {b!r} must look like 2026-11-26")
            continue
        if iso not in good:
            good.append(iso)
    if len(good) > MAX_BLOCKED:
        fail(f"Up to {MAX_BLOCKED} blocked dates")
        good = good[:MAX_BLOCKED]
    out["blocked_dates"] = sorted(good)

    details = re.sub(r"<[^>]+>", "", str(raw.get("details") or "")).strip()
    if len(details) > MAX_DETAILS:
        fail(f"Meeting details are too long (max {MAX_DETAILS} characters)")
        details = details[:MAX_DETAILS]
    from core.leasing import find_steering
    flagged = find_steering(details)
    if flagged:
        fail("Meeting details can't describe who the home is for (found: " + ", ".join(flagged) + ")")
        details = ""
    out["details"] = details

    if out["enabled"] and not any(out["hours"].values()):
        fail("Turn on at least one day of the week to accept bookings")
    return out


def is_enabled(schedule: dict | None) -> bool:
    return bool(schedule and schedule.get("enabled") and any((schedule.get("hours") or {}).values()))


# ── Availability ────────────────────────────────────────────────────────────
def _booked_ranges(client_id, start, end):
    db = SessionLocal()
    try:
        rows = (db.query(Appointment.start_at, Appointment.end_at)
                .filter(Appointment.client_id == client_id, Appointment.status == "booked",
                        Appointment.end_at > start, Appointment.start_at < end)
                .all())
        return [(_aware(a), _aware(b)) for a, b in rows]
    finally:
        db.close()


def open_slots(client_id: str, schedule: dict, now: datetime | None = None) -> list[datetime]:
    """Every bookable slot start (UTC), soonest first."""
    if not is_enabled(schedule):
        return []
    now = now or now_utc()
    tz = ZoneInfo(schedule["timezone"])
    step = timedelta(minutes=schedule["slot_minutes"])
    earliest = now + timedelta(hours=schedule["min_notice_hours"])
    local_today = now.astimezone(tz).date()
    last_day = local_today + timedelta(days=schedule["max_days_ahead"])
    blocked = set(schedule.get("blocked_dates") or [])
    booked = _booked_ranges(client_id, now, datetime.combine(last_day + timedelta(days=1), time(0), tz).astimezone(timezone.utc))

    slots = []
    d = local_today
    while d <= last_day:
        win = schedule["hours"].get(DAYS[d.weekday()])
        if win and d.isoformat() not in blocked:
            sh, sm = map(int, win[0].split(":"))
            eh, em = map(int, win[1].split(":"))
            cur = datetime.combine(d, time(sh, sm), tz)
            close = datetime.combine(d, time(eh, em), tz)
            while cur + step <= close:
                s_utc = cur.astimezone(timezone.utc)
                e_utc = s_utc + step
                if s_utc >= earliest and not any(s_utc < b_end and e_utc > b_start for b_start, b_end in booked):
                    slots.append(s_utc)
                cur += step
        d += timedelta(days=1)
    return slots


def spread_offer(slots: list[datetime], schedule: dict, n: int = OFFER_COUNT) -> list[datetime]:
    """Next n slots, at most two per day, so prospects see a few different days."""
    tz = ZoneInfo(schedule["timezone"])
    out, per_day = [], {}
    for s in slots:
        day = s.astimezone(tz).date()
        if per_day.get(day, 0) >= MAX_PER_DAY_IN_OFFER:
            continue
        out.append(s)
        per_day[day] = per_day.get(day, 0) + 1
        if len(out) >= n:
            break
    return out


_DAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def is_after_hours(schedule: dict, when: datetime | None = None) -> bool:
    """True when `when` falls outside the owner's weekly hours (or on a day
    off), in the owner's time zone. Uses the hours whether or not online
    booking is on; they default to weekdays 9-5 and Saturday 10-2."""
    sched = schedule or DEFAULT_SCHEDULE
    try:
        tz = ZoneInfo(sched.get("timezone") or DEFAULT_SCHEDULE["timezone"])
    except (ZoneInfoNotFoundError, ValueError):
        tz = ZoneInfo(DEFAULT_SCHEDULE["timezone"])
    local = _aware(when or now_utc()).astimezone(tz)
    if local.date().isoformat() in (sched.get("blocked_dates") or []):
        return True
    window = (sched.get("hours") or DEFAULT_SCHEDULE["hours"]).get(_DAY_KEYS[local.weekday()])
    if not window:
        return True
    hhmm = local.strftime("%H:%M")
    return not (window[0] <= hhmm < window[1])


def tz_label(schedule: dict) -> str:
    tz = schedule.get("timezone", "America/New_York")
    return {"America/New_York": "ET", "America/Chicago": "CT", "America/Denver": "MT",
            "America/Phoenix": "MST", "America/Los_Angeles": "PT", "America/Anchorage": "AKT",
            "Pacific/Honolulu": "HT"}.get(tz, tz)


def fmt_slot(dt: datetime, schedule: dict, with_tz: bool = True, lang: str = "en") -> str:
    local = _aware(dt).astimezone(ZoneInfo(schedule["timezone"]))
    hour = local.strftime("%I").lstrip("0") or "12"
    if lang == "es":
        from core.i18n import ES_DAYS, ES_MONTHS
        ampm = "a. m." if local.hour < 12 else "p. m."
        s = f"{ES_DAYS[local.weekday()]}, {local.day} {ES_MONTHS[local.month - 1]} a las {hour}:{local.strftime('%M')} {ampm}"
    else:
        s = f"{local.strftime('%a, %b')} {local.day} at {hour}:{local.strftime('%M %p')}"
    return f"{s} {tz_label(schedule)}" if with_tz else s


def offer_text(offer: list[datetime], schedule: dict, kind: str, lead_in: str = "", lang: str = "en") -> str:
    from core.i18n import t
    lines = [lead_in or t("offer_lead_in", lang, kind=kind)]
    for i, s in enumerate(offer, 1):
        lines.append(f"{i}. {fmt_slot(s, schedule, with_tz=False, lang=lang)}")
    lines.append(t("offer_footer", lang, tz=tz_label(schedule)))
    return "\n".join(lines)


# ── Understanding the prospect's pick ───────────────────────────────────────
_ORDINALS = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3,
             "fourth": 4, "4th": 4, "fifth": 5, "5th": 5, "one": 1, "two": 2,
             "three": 3, "four": 4, "five": 5, "last": -1,
             "primero": 1, "primera": 1, "segundo": 2, "segunda": 2, "tercero": 3, "tercera": 3,
             "cuarto": 4, "cuarta": 4, "quinto": 5, "quinta": 5, "uno": 1, "una": 1, "dos": 2,
             "tres": 3, "cuatro": 4, "cinco": 5, "último": -1, "ultimo": -1, "última": -1, "ultima": -1}
_WEEKDAYS = {"mon": 0, "monday": 0, "tue": 1, "tues": 1, "tuesday": 1, "wed": 2, "weds": 2,
             "wednesday": 2, "thu": 3, "thur": 3, "thurs": 3, "thursday": 3, "fri": 4, "friday": 4,
             "sat": 5, "saturday": 5, "sun": 6, "sunday": 6,
             "lunes": 0, "martes": 1, "miércoles": 2, "miercoles": 2, "jueves": 3, "viernes": 4,
             "sábado": 5, "sabado": 5, "domingo": 6}
_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug",
                                         "sep", "oct", "nov", "dec"], 1)}
_ES_MONTHS = {m: i for i, m in enumerate(["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago",
                                            "sep", "oct", "nov", "dic"], 1)}
_TIME_RE = re.compile(r"\b(?:at\s+|a\s+las?\s+)?(\d{1,2})(?::(\d{2}))?\s*(a\.?\s?m\.?|p\.?\s?m\.?)?(?=\s|$|[.,!?])", re.I)


_PICK_DIGIT_RE = re.compile(
    r"(?:(?:option|number|no\.?)\s*|#\s*)?(\d)\s*[.!)]?"
    r"(?:\s*(?:please|pls|works|is good|sounds good|works for me|thanks|thank you)[.!]?)?")
_PICK_WORD_RE = re.compile(
    r"(?:(?:i'?ll take|let'?s do|i'?d like|go with|quiero|prefiero|me quedo con)\s+)?(?:the\s+|el\s+|la\s+)?"
    r"(first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th|last|one|two|three|four|five|"
    r"primero|primera|segundo|segunda|tercero|tercera|cuarto|cuarta|quinto|quinta|"
    r"[uú]ltimo|[uú]ltima|uno|una|dos|tres|cuatro|cinco)"
    r"(?:\s+(?:one|option|time|slot|opci[oó]n|horario))?(?:\s*(?:please|pls|works|is good|sounds good|por favor|gracias))?[.!]?")


def _pick_number(text: str, n_offered: int) -> int | None:
    """Only a reply that is JUST a pick counts ("2", "#3 please", "the first one").
    Sentences that merely contain a number ("I have two dogs") never do."""
    t = " ".join(text.strip().lower().split())
    t = re.sub(r"^(?:el|la|opci[oó]n|n[uú]mero)\s+", "", t)
    t = re.sub(r"\s*(?:por favor|gracias)[.!]?$", "", t)
    m = _PICK_DIGIT_RE.fullmatch(t)
    if m:
        k = int(m.group(1))
        return k if 1 <= k <= n_offered else None
    m = _PICK_WORD_RE.fullmatch(t)
    if m:
        k = _ORDINALS[m.group(1)]
        k = n_offered if k == -1 else k
        return k if 1 <= k <= n_offered else None
    return None


def _parse_day(text: str, today: date) -> date | None:
    t = text.lower()
    if re.search(r"\btoday\b", t):
        return today
    if re.search(r"\btomorrow\b", t):
        return today + timedelta(days=1)
    if re.search(r"\bhoy\b", t):
        return today
    if re.search(r"\bpasado mañana\b", t):
        return today + timedelta(days=2)
    # "mañana" alone is tomorrow; "por la mañana" / "en la mañana" is a part of day.
    if re.search(r"(?<!la )\bmañana\b", t):
        return today + timedelta(days=1)
    m = re.search(r"\b(\d{1,2})\s+de\s+(ene|feb|mar|abr|may|jun|jul|ago|sep|oct|nov|dic)[a-z]*\b", t)
    if m:
        return _next_date(today, _ES_MONTHS[m.group(2)], int(m.group(1)))
    m = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/\d{2,4})?\b", t)
    if m:
        mo, dy = int(m.group(1)), int(m.group(2))
        return _next_date(today, mo, dy)
    m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b", t)
    if m:
        return _next_date(today, _MONTHS[m.group(1)], int(m.group(2)))
    for word, wd in _WEEKDAYS.items():
        if re.search(rf"\b{word}\b", t):
            delta = (wd - today.weekday()) % 7
            if re.search(rf"\b(?:next\s+{word}|{word}\s+(?:que viene|pr[oó]ximo))\b", t) and delta == 0:
                delta = 7
            return today + timedelta(days=delta)
    return None


def _next_date(today: date, mo: int, dy: int) -> date | None:
    for year in (today.year, today.year + 1):
        try:
            d = date(year, mo, dy)
        except ValueError:
            return None
        if d >= today:
            return d
    return None


# Words that may follow a bare "at 3" when it really is a time.
_AFTER_BARE_TIME = (r"(?:$|[.,!?]|\s+(?:on|this|next|tomorrow|today|please|works|ok|okay|or|and|if|then|work|good|in the"
                    r"|el|este|esta|mañana|hoy|por favor|o|y|si|est[aá] bien|me sirve)\b"
                    r"|\s+(?:mon|tue|wed|thu|fri|sat|sun|lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)[a-z]*\b)")


def _parse_time(text: str):
    """Returns ("exact", time, explicit) | ("window", (start_h, end_h), False) | None.
    explicit means am/pm, a colon, or "noon": an unambiguous clock time."""
    t = text.lower()
    if re.search(r"\bnoon\b|\bmediod[ií]a\b", t):
        return ("exact", time(12, 0), True)
    t = re.sub(r"\b(\d{1,2})(?::(\d{2}))?\s+de la (mañana|tarde|noche)\b",
               lambda m: f"{m.group(1)}{':' + m.group(2) if m.group(2) else ''} {'am' if m.group(3) == 'mañana' else 'pm'}", t)
    for m in _TIME_RE.finditer(t):
        h = int(m.group(1))
        mins = int(m.group(2) or 0)
        ap = (m.group(3) or "").replace(".", "").replace(" ", "")
        if h > 12 or mins > 59 or (h == 0 and not ap):
            continue
        explicit = bool(ap) or m.group(2) is not None
        if not explicit:
            # A bare number is a time only as "at 3" followed by nothing that
            # makes it an address ("at 11 Palm St") or a count.
            if not re.match(r"(?:at|a las?)\b", m.group(0).strip()):
                continue
            if not re.match(_AFTER_BARE_TIME, t[m.end():]):
                continue
        if ap == "pm" and h < 12:
            h += 12
        elif ap == "am" and h == 12:
            h = 0
        elif not ap and 1 <= h <= 7:
            h += 12  # "at 3" in a showing context means 3 PM
        return ("exact", time(h, mins), explicit)
    if re.search(r"\bmorning\b|\b(?:por|en) la mañana\b", t):
        return ("window", (0, 12), False)
    if re.search(r"\bafternoon\b|\b(?:por|en) la tarde\b", t):
        return ("window", (12, 17), False)
    if re.search(r"\bevening\b|\bafter work\b|\b(?:por|en) la noche\b|despu[eé]s del trabajo", t):
        return ("window", (17, 24), False)
    return None


def parse_choice(message: str, offered: list[datetime], slots: list[datetime], schedule: dict,
                 now: datetime | None = None, lang: str = "en"):
    """Map a reply to a slot. Returns one of:
      ("slot", dt, sure)      a specific open slot; sure=False means confirm first
      ("choices", [dt...])    several candidates to offer (a day, a part of day)
      ("unavailable", label)  they named a day/time with nothing open
      None                    not a scheduling answer
    Only an exact pick ("2") or an explicit day + clock time is "sure"; anything
    looser is confirmed with the prospect before it's booked.
    """
    tz = ZoneInfo(schedule["timezone"])
    now = now or now_utc()
    k = _pick_number(message, len(offered))
    if k:
        return ("slot", offered[k - 1], True)

    question = "?" in message
    day = _parse_day(message, now.astimezone(tz).date())
    tm = _parse_time(message)
    if day is None and tm is None:
        return None
    local_t = lambda s: s.astimezone(tz).time().replace(second=0, microsecond=0)
    # A bare time ("11:30") right after an offer means one of the offered times.
    if day is None and tm and tm[0] == "exact" and offered:
        hit = [s for s in offered if local_t(s) == tm[1] and s in slots]
        if len(hit) == 1:
            return ("slot", hit[0], tm[2] and not question)
    cands = slots
    if day is not None:
        cands = [s for s in cands if s.astimezone(tz).date() == day]
    if tm and tm[0] == "exact":
        exact = [s for s in cands if local_t(s) == tm[1]]
        if exact:
            if day is not None or len({s.astimezone(tz).date() for s in exact}) == 1:
                return ("slot", exact[0], tm[2] and day is not None and not question)
            return ("choices", spread_offer(exact, schedule))
    elif tm and tm[0] == "window":
        lo, hi = tm[1]
        cands = [s for s in cands if lo <= s.astimezone(tz).hour < hi]
    if cands:
        return ("choices", cands[:OFFER_COUNT] if day is not None else spread_offer(cands, schedule))
    if not day:
        return ("unavailable", "that time")
    if lang == "es":
        from core.i18n import ES_DAYS_LONG, ES_MONTHS
        return ("unavailable", f"{ES_DAYS_LONG[day.weekday()]} {day.day} {ES_MONTHS[day.month - 1]}")
    return ("unavailable", day.strftime("%A, %b ") + str(day.day))


# ── Booking ─────────────────────────────────────────────────────────────────
def book(client_id: str, lead_id: str | None, start: datetime, schedule: dict, contact: dict) -> Appointment | None:
    """Book a slot. Returns the appointment, or None if it's no longer open.
    A partial unique index on (client_id, start_at) for booked rows is the
    final guard against two prospects taking the same slot."""
    start = _aware(start)
    if start not in open_slots(client_id, schedule):
        return None
    appt = Appointment(
        id=str(uuid.uuid4()), client_id=client_id, lead_id=lead_id,
        start_at=start, end_at=start + timedelta(minutes=schedule["slot_minutes"]),
        status="booked", name=(contact.get("name") or "")[:200], email=(contact.get("email") or "")[:200],
        phone=(contact.get("phone") or "")[:50], property=(contact.get("property") or "")[:200],
        cancel_token=secrets.token_urlsafe(24), created_at=now_utc(),
    )
    db = SessionLocal()
    try:
        db.add(appt)
        db.commit()
        db.refresh(appt)
        db.expunge(appt)
        return appt
    except IntegrityError:
        db.rollback()
        logger.info(f"Slot {start.isoformat()} was taken a moment ago (client {client_id[:8]})")
        return None
    finally:
        db.close()


def appt_to_dict(a: Appointment, schedule: dict) -> dict:
    return {
        "id": a.id, "lead_id": a.lead_id, "status": a.status,
        "start_at": _aware(a.start_at).isoformat(), "end_at": _aware(a.end_at).isoformat(),
        "when": fmt_slot(a.start_at, schedule), "when_es": fmt_slot(a.start_at, schedule, lang="es"),
        "name": a.name or "", "email": a.email or "",
        "phone": a.phone or "", "property": a.property or "", "canceled_by": a.canceled_by,
    }


def list_appointments(client_id: str, schedule: dict, past_days: int = 7) -> list[dict]:
    since = now_utc() - timedelta(days=past_days)
    db = SessionLocal()
    try:
        rows = (db.query(Appointment)
                .filter(Appointment.client_id == client_id, Appointment.start_at >= since)
                .order_by(Appointment.start_at).limit(300).all())
        return [appt_to_dict(a, schedule) for a in rows]
    finally:
        db.close()


def cancel(appt_id: str = None, *, client_id: str = None, token: str = None, by: str = "owner") -> Appointment | None:
    """Cancel by (client_id + id) for owners, or by token for prospects.
    Returns the appointment if this call canceled it, else None."""
    db = SessionLocal()
    try:
        q = db.query(Appointment)
        if token:
            q = q.filter(Appointment.cancel_token == token)
        else:
            q = q.filter(Appointment.client_id == client_id, Appointment.id == appt_id)
        a = q.first()
        if not a or a.status != "booked":
            return None
        n = (db.query(Appointment).filter(Appointment.id == a.id, Appointment.status == "booked")
             .update({"status": "canceled", "canceled_at": now_utc(), "canceled_by": by}, synchronize_session=False))
        db.commit()
        if n != 1:
            return None
        db.refresh(a)
        db.expunge(a)
        return a
    finally:
        db.close()


def get_by_token(token: str) -> Appointment | None:
    db = SessionLocal()
    try:
        a = db.query(Appointment).filter(Appointment.cancel_token == token).first()
        if a:
            db.expunge(a)
        return a
    finally:
        db.close()


def due_reminders(db, now: datetime | None = None):
    """Booked appointments starting 1-24 hours from now with no reminder sent, soonest first."""
    now = now or now_utc()
    return (db.query(Appointment)
            .filter(Appointment.status == "booked", Appointment.reminder_sent_at.is_(None),
                    Appointment.start_at > now + timedelta(hours=1),
                    Appointment.start_at <= now + timedelta(hours=24))
            .order_by(Appointment.start_at)
            .limit(200).all())


def wants_reminder(a) -> bool:
    """Bookings made less than 12 hours ahead skip the reminder; the confirmation is fresh."""
    return not a.created_at or _aware(a.start_at) - _aware(a.created_at) >= timedelta(hours=12)


def lead_booking(client_id: str, lead_id: str) -> Appointment | None:
    """The lead's current booked appointment, if any."""
    if not lead_id:
        return None
    db = SessionLocal()
    try:
        a = (db.query(Appointment)
             .filter(Appointment.client_id == client_id, Appointment.lead_id == lead_id,
                     Appointment.status == "booked")
             .first())
        if a:
            db.expunge(a)
        return a
    finally:
        db.close()


def claim_reminder(db, appt_id: str) -> bool:
    n = (db.query(Appointment)
         .filter(Appointment.id == appt_id, Appointment.reminder_sent_at.is_(None))
         .update({"reminder_sent_at": now_utc()}, synchronize_session=False))
    db.commit()
    return n == 1


# ── Calendar invite ─────────────────────────────────────────────────────────
def _ics_escape(s: str) -> str:
    s = (s or "").replace("\r\n", "\n").replace("\r", "\n")
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    """RFC 5545: lines longer than 75 octets continue on the next line after a space."""
    out, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > (75 if not out else 74):
            out.append(cur.decode("utf-8"))
            cur = b""
        cur += b
    out.append(cur.decode("utf-8"))
    return "\r\n ".join(out)


def ics(a: Appointment, summary: str, description: str, location: str = "") -> str:
    fmt = lambda d: _aware(d).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Magni//Scheduler//EN", "METHOD:PUBLISH",
        "BEGIN:VEVENT", f"UID:{a.id}@magni", f"DTSTAMP:{fmt(now_utc())}",
        f"DTSTART:{fmt(a.start_at)}", f"DTEND:{fmt(a.end_at)}",
        f"SUMMARY:{_ics_escape(summary)}", f"DESCRIPTION:{_ics_escape(description)}",
    ]
    if location:
        lines.append(f"LOCATION:{_ics_escape(location)}")
    lines += ["STATUS:CONFIRMED", "END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(_fold(l) for l in lines) + "\r\n"
