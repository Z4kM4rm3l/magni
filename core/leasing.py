"""
core/leasing.py

Leasing assistant for property managers (long-term rentals) and seasonal /
vacation rental operators. Pure logic, no DB and no LLM, so it is easy to test.

Fair Housing design (the reason this module looks the way it does):
  * Every prospect is asked the SAME questions, in the SAME order, with the SAME
    wording. Owners can switch questions on or off from a fixed, vetted catalog,
    but cannot write their own wording.
  * The assistant only COLLECTS answers. It never scores, ranks, approves, or
    rejects anyone, and everyone gets the same next-step links.
  * Mentions of assistance/service/support animals or disability accommodations
    are never treated as pets; they are flagged and handed to a human.
  * Every reply the assistant sends for a rental business passes through
    check_output(), which blocks steering language (comments about who a
    property or neighborhood "suits"). Owner-written criteria get the same check.

This is a guardrail, not legal advice. Owners should have their question list
and criteria reviewed by their own counsel.
"""
import re

VERTICALS = {
    "general": "General business",
    "long_term": "Long-term rentals",
    "seasonal": "Seasonal / vacation rentals",
}
RENTAL_VERTICALS = {"long_term", "seasonal"}

# ── Question catalog ────────────────────────────────────────────────────────
# Order here IS the order asked. Wording is fixed on purpose.
# "verticals" lists where the question is available.
QUESTION_CATALOG = {
    "property": {
        "label": "Property / unit",
        "text": "Which property or unit are you interested in?",
        "verticals": {"long_term", "seasonal"},
    },
    "move_in": {
        "label": "Move-in date",
        "text": "When are you hoping to move in?",
        "verticals": {"long_term"},
    },
    "stay_dates": {
        "label": "Stay dates",
        "text": "What dates are you looking to stay (check-in and check-out)?",
        "verticals": {"seasonal"},
    },
    "name": {
        "label": "Name",
        "text": "So our team can follow up, what's your name?",
        "verticals": {"long_term", "seasonal"},
    },
    "phone": {
        "label": "Phone",
        "text": "What's the best phone number to reach you?",
        "verticals": {"long_term", "seasonal"},
    },
    "email": {
        "label": "Email",
        "text": "And your email address?",
        "verticals": {"long_term", "seasonal"},
    },
    "occupants": {
        "label": "Occupants",
        "text": "How many people will be living in the home?",
        "verticals": {"long_term"},
    },
    "guests": {
        "label": "Guests",
        "text": "How many guests will be staying?",
        "verticals": {"seasonal"},
    },
    "lease_length": {
        "label": "Lease length",
        "text": "How long of a lease are you looking for?",
        "verticals": {"long_term"},
    },
    "budget": {
        "label": "Budget",
        "text": "What monthly budget do you have in mind?",
        "verticals": {"long_term"},
    },
    "stay_budget": {
        "label": "Budget",
        "text": "What budget do you have in mind for the stay?",
        "verticals": {"seasonal"},
    },
    "pets": {
        "label": "Pets",
        "text": ("Will any pets be coming with you? If so, what kind and how many? "
                 "(Assistance animals are not considered pets.)"),
        "verticals": {"long_term", "seasonal"},
    },
    "criteria": {
        "label": "Meets posted criteria",
        # {criteria} is filled from the owner's posted rental criteria.
        "text": "Our posted rental criteria are: {criteria} Do you expect to meet them?",
        "verticals": {"long_term"},
        "needs_criteria": True,
    },
    "tour_time": {
        "label": "Preferred tour / call time",
        "text": "When would be a good time for a tour or a quick call?",
        "verticals": {"long_term", "seasonal"},
    },
}
CATALOG_ORDER = list(QUESTION_CATALOG)
CONTACT_KEYS = ("phone", "email")

PRESETS = {
    "long_term": ["property", "move_in", "name", "phone", "email", "occupants",
                  "lease_length", "budget", "pets", "criteria", "tour_time"],
    "seasonal": ["property", "stay_dates", "name", "phone", "email", "guests",
                 "stay_budget", "pets", "tour_time"],
}

MAX_CRITERIA = 600
MAX_URL = 500

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_URL_RE = re.compile(r"^https?://[^\s<>\"']+$", re.I)
_PHONE_RE = re.compile(r"(?:\+?1[\s.\-]?)?\(?\b(\d{3})\)?[\s.\-]?(\d{3})[\s.\-]?(\d{4})\b")


# ── Guardrails ──────────────────────────────────────────────────────────────
# Phrases that describe WHO a home or area is for, or that steer by a
# protected class. Matched case-insensitively on word boundaries.
_STEERING_PATTERNS = [
    r"family[\s-]friendly", r"great for (?:(?:young |growing )?families|kids|children|couples|singles|young professionals|retirees|seniors)",
    r"perfect for (?:(?:young |growing )?families|kids|children|couples|singles|young professionals|retirees|seniors)",
    r"ideal for (?:(?:young |growing )?families|kids|children|couples|singles|young professionals|retirees|seniors)",
    r"best (?:suited )?for (?:(?:young |growing )?families|kids|children|couples|singles|young professionals|retirees|seniors)",
    r"no (?:kids|children)(?!['\u2019])", r"adults? only", r"child[\s-]free",
    r"not (?:suitable|ideal|good) for (?:kids|children|families|seniors|the elderly)",
    r"(?:safe|good|bad|rough|sketchy|dangerous|exclusive|desirable) (?:neighborhood|part of town)",
    r"(?:safe|bad|rough|sketchy|dangerous|exclusive|desirable) area",
    r"(?:good|great|nice|perfect|ideal|best)\s+(?:area|neighborhood|place|home|unit|fit|spot|community)\s+for\s+"
    r"(?:(?:young |growing )?families|kids|children|couples|singles|young professionals|retirees|seniors|the elderly)",
    r"(?:christian|catholic|jewish|muslim|hindu) (?:community|neighborhood|home|household|tenants?)",
    r"near (?:a |the )?(?:church|synagogue|mosque|temple)",
    r"able[\s-]bodied", r"no wheelchairs?", r"must be able to (?:climb|walk)",
    r"english[\s-]speaking (?:only|tenants?|applicants?)", r"(?:american|u\.?s\.?) citizens? only",
    r"(?:single|married) (?:person|people|couples?) only",
    r"mature (?:person|people|tenants?|adults?)", r"national origin",
]
_STEERING_RE = re.compile(r"\b(?:" + "|".join(_STEERING_PATTERNS) + r")\b", re.I)

# Sent instead of any rental-business reply that trips the steering filter.
SAFE_FALLBACK = (
    "I'm not able to describe who a home or area would suit, but I'm happy to share "
    "details about the property itself, like size, amenities, pricing, and policies. "
    "Our leasing team can also answer anything else you'd like to know."
)


def find_steering(text: str) -> list[str]:
    return sorted({m.group(0).lower() for m in _STEERING_RE.finditer(text or "")})


def check_output(text: str) -> tuple[str, list[str]]:
    """Return (text_to_send, flagged_phrases). Replaces the whole reply when
    anything is flagged, since a partial edit could still read as steering."""
    flagged = find_steering(text)
    return (SAFE_FALLBACK, flagged) if flagged else (text, [])


_ACCOMMODATION_RE = re.compile(
    r"\b(?:(?:service|assistance|assistive|support|emotional[\s-]support|therapy|comfort|companion|"
    r"medical[\s-]alert|alert|seizure|psychiatric|guide|hearing)\s+(?:animal|dog|cat|pet|bird)s?|"
    r"emotional support|\besa\b|seeing[\s-]eye|"
    r"reasonable accommodations?|accommodation requests?|disabilit(?:y|ies)|disabled|"
    r"wheelchair|accessible (?:unit|home|apartment|entrance)|ada (?:unit|accessible))\b",
    re.I,
)


def detect_accommodation(message: str) -> bool:
    return bool(_ACCOMMODATION_RE.search(message or ""))


ACCOMMODATION_REPLY = (
    "Thank you for letting me know. Assistance animals are not treated as pets, and "
    "requests like this are handled personally by our team, so I've flagged it for "
    "them to follow up with you directly."
)

_OPT_OUT_RE = re.compile(
    r"^\s*(?:stop|no thanks?|no,? thank you|not interested|never ?mind|nvm|cancel|quit|"
    r"i'?m (?:just )?(?:looking|browsing)|just (?:looking|browsing))\s*[.!]?\s*$",
    re.I,
)
_SKIP_RE = re.compile(
    r"^\s*(?:skip|pass|n/?a|none of your business|prefer not to (?:say|answer)|"
    r"i'?d rather not(?: say)?|rather not(?: say)?|no comment|next)\s*[.!]?\s*$",
    re.I,
)


def is_opt_out(message: str) -> bool:
    return bool(_OPT_OUT_RE.match(message or ""))


def is_skip(message: str) -> bool:
    return bool(_SKIP_RE.match(message or ""))


# Fallback start trigger when the triage model is unavailable.
_LEASING_HINT_RE = re.compile(
    r"\b(?:tour|showing|apply|application|availab(?:le|ility)|vacanc(?:y|ies)|for rent|"
    r"move[\s-]?in|lease|rent(?:ing)? (?:a|the|your)|book(?:ing)?|reserv(?:e|ation)|"
    r"check[\s-]?in|stay)\b",
    re.I,
)


def looks_like_leasing(message: str) -> bool:
    return bool(_LEASING_HINT_RE.search(message or ""))


# ── Contact parsing (deterministic, runs alongside the LLM extractor) ──────
def find_email(text: str) -> str | None:
    m = _EMAIL_RE.search(text or "")
    return m.group(0).lower() if m else None


def find_phone(text: str) -> str | None:
    m = _PHONE_RE.search(text or "")
    return f"({m.group(1)}) {m.group(2)}-{m.group(3)}" if m else None


def normalize_phone_e164(raw: str) -> str | None:
    """US numbers only for SMS alerts. Returns +1XXXXXXXXXX or None."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10 or digits[0] in "01":
        return None
    return "+1" + digits


# ── Config ──────────────────────────────────────────────────────────────────
def available_questions(vertical: str) -> list[str]:
    return [k for k in CATALOG_ORDER if vertical in QUESTION_CATALOG[k]["verticals"]]


def default_lead_config(vertical: str) -> dict:
    return {
        "questions": list(PRESETS.get(vertical, [])),
        "rental_criteria": "",
        "application_url": "",
        "booking_url": "",
        "notify_email": "",
        "notify_phone": "",
        "schedule": _default_schedule(),
    }


def _default_schedule() -> dict:
    from core.scheduling import normalize_schedule
    return normalize_schedule(None)


class LeadConfigError(ValueError):
    pass


def _clean_url(v: str, field: str) -> str:
    v = (v or "").strip()
    if not v:
        return ""
    if len(v) > MAX_URL or not _URL_RE.match(v):
        raise LeadConfigError(f"{field} must be a full link starting with https://")
    return v


def normalize_lead_config(raw: dict | None, vertical: str, strict: bool = False) -> dict:
    """Validate and normalize an owner-submitted (or stored) config.

    strict=True raises LeadConfigError on bad input (portal saves).
    strict=False quietly repairs it (reading stored config).
    """
    base = default_lead_config(vertical)
    if not isinstance(raw, dict):
        return base
    allowed = available_questions(vertical)

    def fail(msg):
        if strict:
            raise LeadConfigError(msg)

    qs = raw.get("questions", base["questions"])
    if not isinstance(qs, list):
        fail("questions must be a list")
        qs = base["questions"]
    unknown = [q for q in qs if q not in allowed]
    if unknown:
        fail(f"Unknown question(s) for this rental type: {', '.join(map(str, unknown))}")
    chosen = {q for q in qs if q in allowed}
    if allowed:
        # Name plus at least one way to reach the prospect are always asked.
        if "name" not in chosen:
            fail("The name question can't be turned off")
            chosen.add("name")
        if not (chosen & set(CONTACT_KEYS)):
            fail("Keep at least one of phone or email so your team can follow up")
            chosen.update(CONTACT_KEYS)
    out = dict(base)
    out["questions"] = [k for k in CATALOG_ORDER if k in chosen]  # catalog order, always

    criteria = re.sub(r"<[^>]+>", "", str(raw.get("rental_criteria") or "")).strip()
    if len(criteria) > MAX_CRITERIA:
        fail(f"Rental criteria is too long (max {MAX_CRITERIA} characters)")
        criteria = criteria[:MAX_CRITERIA]
    flagged = find_steering(criteria)
    if flagged:
        fail("Rental criteria can't describe who the home is for (found: " + ", ".join(flagged) + ")")
        criteria = ""
    out["rental_criteria"] = criteria

    for field in ("application_url", "booking_url"):
        try:
            out[field] = _clean_url(raw.get(field, ""), field.replace("_", " ").capitalize())
        except LeadConfigError:
            if strict:
                raise
            out[field] = ""

    email = str(raw.get("notify_email") or "").strip().lower()
    if email and not _EMAIL_RE.fullmatch(email):
        fail("Alert email doesn't look like a valid email address")
        email = ""
    out["notify_email"] = email

    phone_raw = str(raw.get("notify_phone") or "").strip()
    phone = normalize_phone_e164(phone_raw) if phone_raw else ""
    if phone_raw and not phone:
        fail("Alert phone must be a 10-digit US number")
        phone = ""
    out["notify_phone"] = phone or ""

    from core.scheduling import normalize_schedule, ScheduleError
    try:
        out["schedule"] = normalize_schedule(raw.get("schedule"), strict=strict)
    except ScheduleError as e:
        raise LeadConfigError(str(e))
    return out


# ── Flow helpers ────────────────────────────────────────────────────────────
def active_questions(config: dict) -> list[str]:
    """Enabled questions that can actually be asked (criteria needs text)."""
    qs = []
    for k in config.get("questions", []):
        spec = QUESTION_CATALOG.get(k)
        if not spec:
            continue
        if spec.get("needs_criteria") and not config.get("rental_criteria"):
            continue
        qs.append(k)
    # Online booking replaces the free-text tour question, so it's always the last step.
    if (config.get("schedule") or {}).get("enabled") and "tour_time" not in qs:
        qs.append("tour_time")
    return qs


def question_text(key: str, config: dict) -> str:
    spec = QUESTION_CATALOG[key]
    text = spec["text"]
    if spec.get("needs_criteria"):
        crit = config.get("rental_criteria", "").strip()
        if crit and crit[-1] not in ".!?":
            crit += "."
        text = text.format(criteria=crit)
    return text


def next_question(answers: dict, config: dict) -> str | None:
    answers = answers or {}
    for k in active_questions(config):
        if k not in answers:
            return k
    return None


def has_contact(answers: dict) -> bool:
    answers = answers or {}
    return any(_is_real(answers.get(k)) for k in CONTACT_KEYS)


NOT_PROVIDED = "Not provided"
DECLINED = "Prefers not to say"
ASSISTANCE_NOTE = "Assistance animal mentioned; team to follow up"


def _is_real(v) -> bool:
    return bool(v) and v not in (NOT_PROVIDED, DECLINED)


def intro_line(vertical: str) -> str:
    team = "reservations team" if vertical == "seasonal" else "leasing team"
    return f"I can pass your details to our {team} so they can follow up. "


def closing_message(answers: dict, config: dict, vertical: str) -> str:
    """Same closing for every prospect: thanks + the same links."""
    team = "reservations team" if vertical == "seasonal" else "leasing team"
    if has_contact(answers):
        parts = [f"Thank you! I've sent your details to our {team}, and someone will reach out soon."]
    else:
        parts = [f"Thanks! Without a phone number or email, our {team} won't be able to reach you, "
                 "but you're welcome to take the next step anytime."]
    if config.get("application_url"):
        label = "book or request a stay" if vertical == "seasonal" else "start an application"
        parts.append(f"If you'd like to {label} now: {config['application_url']}")
    if config.get("booking_url") and not (config.get("schedule") or {}).get("enabled"):
        parts.append(f"To pick a time that works for you: {config['booking_url']}")
    parts.append("Is there anything else I can help with in the meantime?")
    return "\n\n".join(parts)


def summary_lines(answers: dict, config: dict) -> list[tuple[str, str]]:
    """(label, value) pairs in catalog order, for alerts and the portal."""
    answers = answers or {}
    out = []
    for k in CATALOG_ORDER:
        if k in answers:
            out.append((QUESTION_CATALOG[k]["label"], str(answers[k])))
    return out
