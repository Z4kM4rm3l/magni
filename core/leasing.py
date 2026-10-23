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

from core.i18n import t as _t

# Magni serves rental businesses only. Every account is one of these.
VERTICALS = {
    "long_term": "Long-term rentals",
    "seasonal": "Seasonal / vacation rentals",
}
RENTAL_VERTICALS = {"long_term", "seasonal"}
DEFAULT_VERTICAL = "long_term"


def vertical_of(value) -> str:
    """The account's business type, falling back to long-term rentals for
    anything unknown (including the retired "general" type)."""
    return value if value in VERTICALS else DEFAULT_VERTICAL

# ── Question catalog ────────────────────────────────────────────────────────
# Order here IS the order asked. Wording is fixed on purpose, in English and
# Spanish ("text_es"), so every prospect in a language gets the same words.
# "verticals" lists where the question is available.
QUESTION_CATALOG = {
    "property": {
        "label": "Property / unit",
        "text": "Which property or unit are you interested in?",
        "text_es": "¿Qué propiedad o unidad le interesa?",
        "verticals": {"long_term", "seasonal"},
    },
    "move_in": {
        "label": "Move-in date",
        "text": "When are you hoping to move in?",
        "text_es": "¿Cuándo le gustaría mudarse?",
        "verticals": {"long_term"},
    },
    "stay_dates": {
        "label": "Stay dates",
        "text": "What dates are you looking to stay (check-in and check-out)?",
        "text_es": "¿Qué fechas busca para su estadía (llegada y salida)?",
        "verticals": {"seasonal"},
    },
    "name": {
        "label": "Name",
        "text": "So our team can follow up, what's your name?",
        "text_es": "Para que nuestro equipo le dé seguimiento, ¿cuál es su nombre?",
        "verticals": {"long_term", "seasonal"},
    },
    "phone": {
        "label": "Phone",
        "text": "What's the best phone number to reach you?",
        "text_es": "¿Cuál es el mejor número de teléfono para contactarle?",
        "verticals": {"long_term", "seasonal"},
    },
    "email": {
        "label": "Email",
        "text": "And your email address?",
        "text_es": "¿Y su correo electrónico?",
        "verticals": {"long_term", "seasonal"},
    },
    "occupants": {
        "label": "Occupants",
        "text": "How many people will be living in the home?",
        "text_es": "¿Cuántas personas vivirán en la vivienda?",
        "verticals": {"long_term"},
    },
    "guests": {
        "label": "Guests",
        "text": "How many guests will be staying?",
        "text_es": "¿Cuántos huéspedes se quedarán?",
        "verticals": {"seasonal"},
    },
    "lease_length": {
        "label": "Lease length",
        "text": "How long of a lease are you looking for?",
        "text_es": "¿De cuánto tiempo busca el contrato de arrendamiento?",
        "verticals": {"long_term"},
    },
    "budget": {
        "label": "Budget",
        "text": "What monthly budget do you have in mind?",
        "text_es": "¿Qué presupuesto mensual tiene en mente?",
        "verticals": {"long_term"},
    },
    "stay_budget": {
        "label": "Budget",
        "text": "What budget do you have in mind for the stay?",
        "text_es": "¿Qué presupuesto tiene en mente para la estadía?",
        "verticals": {"seasonal"},
    },
    "pets": {
        "label": "Pets",
        "text": ("Will any pets be coming with you? If so, what kind and how many? "
                 "(Assistance animals are not considered pets.)"),
        "text_es": "¿Vendrán mascotas con usted? Si es así, ¿de qué tipo y cuántas? (Los animales de asistencia no se consideran mascotas.)",
        "verticals": {"long_term", "seasonal"},
    },
    "criteria": {
        "label": "Meets posted criteria",
        # {criteria} is filled from the owner's posted rental criteria.
        "text": "Our posted rental criteria are: {criteria} Do you expect to meet them?",
        "text_es": "Nuestros criterios de alquiler publicados son: {criteria} ¿Espera cumplirlos?",
        "verticals": {"long_term"},
        "needs_criteria": True,
    },
    "tour_time": {
        "label": "Preferred tour / call time",
        "text": "When would be a good time for a tour or a quick call?",
        "text_es": "¿Cuándo le vendría bien una visita o una llamada breve?",
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
MAX_RESIDENT_CONTACT = 300
MAX_PROPERTIES = 50
MAX_PROPERTY_NAME = 100

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
    # Spanish
    r"(?:ideal|perfect[oa]|excelente|buen[oa]|genial) para (?:familias|niños|parejas|solteros|jubilados|personas mayores|j[oó]venes profesionales)",
    r"(?:barrio|vecindario|zona|[aá]rea|colonia) (?:segur[oa]|peligros[oa]|exclusiv[oa]|tranquil[oa] para familias)",
    r"solo (?:adultos|parejas|solteros)", r"sin niños", r"no (?:se aceptan|aceptamos) niños",
    r"(?:comunidad|vecindario|barrio) (?:cristian[oa]|cat[oó]lic[oa]|judí[oa]|musulm[aá]n[oa]|hispan[oa]|latin[oa])",
]
_STEERING_RE = re.compile(r"\b(?:" + "|".join(_STEERING_PATTERNS) + r")\b", re.I)

# Sent instead of any rental-business reply that trips the steering filter
# (English; check_output picks the visitor's language from core/i18n.py).
SAFE_FALLBACK = _t("safe_fallback", "en")


def find_steering(text: str) -> list[str]:
    return sorted({m.group(0).lower() for m in _STEERING_RE.finditer(text or "")})


def check_output(text: str, lang: str | None = None) -> tuple[str, list[str]]:
    """Return (text_to_send, flagged_phrases). Replaces the whole reply when
    anything is flagged, since a partial edit could still read as steering.
    Checks English and Spanish phrasing either way."""
    flagged = find_steering(text)
    if not flagged:
        return text, []
    return _t("safe_fallback", lang or "en"), flagged


_ACCOMMODATION_RE = re.compile(
    r"\b(?:(?:service|assistance|assistive|support|emotional[\s-]support|therapy|comfort|companion|"
    r"medical[\s-]alert|alert|seizure|psychiatric|guide|hearing)\s+(?:animal|dog|cat|pet|bird)s?|"
    r"emotional support|\besa\b|seeing[\s-]eye|"
    r"reasonable accommodations?|accommodation requests?|disabilit(?:y|ies)|disabled|"
    r"wheelchair|accessible (?:unit|home|apartment|entrance)|ada (?:unit|accessible)|"
    # Spanish
    r"(?:animal|perro|gato|mascota)s? (?:de (?:servicio|asistencia|apoyo(?: emocional)?|terapia)|gu[ií]a)|"
    r"apoyo emocional|discapacidad(?:es)?|discapacitad[oa]s?|silla de ruedas|"
    r"ajustes? razonables?|acomodo razonable|adaptaci[oó]n razonable)\b",
    re.I,
)


def detect_accommodation(message: str) -> bool:
    return bool(_ACCOMMODATION_RE.search(message or ""))


ACCOMMODATION_REPLY = _t("accommodation", "en")

_OPT_OUT_RE = re.compile(
    r"^\s*(?:stop|no thanks?|no,? thank you|not interested|never ?mind|nvm|cancel|quit|"
    r"i'?m (?:just )?(?:looking|browsing)|just (?:looking|browsing)|"
    r"no,? gracias|no me interesa|ya no|olv[ií]delo|solo estoy (?:mirando|viendo))\s*[.!]?\s*$",
    re.I,
)
_SKIP_RE = re.compile(
    r"^\s*(?:skip|pass|n/?a|none of your business|prefer not to (?:say|answer)|"
    r"i'?d rather not(?: say)?|rather not(?: say)?|no comment|next|"
    r"paso|prefiero no (?:decir|contestar|responder)|siguiente)\s*[.!]?\s*$",
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
    r"check[\s-]?in|stay|"
    r"disponibles?|alquil(?:er|ar)|rentar|renta|mudarme|visita|cita|reservar?|reservaci[oó]n|estad[ií]a)\b",
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
        "resident_contact": "",
        "properties": [],
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

    contact = re.sub(r"<[^>]+>", "", str(raw.get("resident_contact") or "")).strip()
    if len(contact) > MAX_RESIDENT_CONTACT:
        fail(f"Resident contact info is too long (max {MAX_RESIDENT_CONTACT} characters)")
        contact = contact[:MAX_RESIDENT_CONTACT]
    out["resident_contact"] = contact

    props_raw = raw.get("properties") or []
    if isinstance(props_raw, str):
        props_raw = props_raw.splitlines()
    if not isinstance(props_raw, list):
        fail("properties must be a list")
        props_raw = []
    props = []
    for name in props_raw:
        name = re.sub(r"<[^>]+>", "", str(name)).strip(" \t-*")
        if not name:
            continue
        if len(name) > MAX_PROPERTY_NAME:
            fail(f"Property names must be {MAX_PROPERTY_NAME} characters or less")
            name = name[:MAX_PROPERTY_NAME]
        if find_steering(name):
            fail(f"Property name can't describe who the home is for: {name}")
            continue
        if name.lower() not in {p.lower() for p in props}:
            props.append(name)
    if len(props) > MAX_PROPERTIES:
        fail(f"Up to {MAX_PROPERTIES} properties")
        props = props[:MAX_PROPERTIES]
    out["properties"] = props

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


def question_text(key: str, config: dict, lang: str = "en") -> str:
    spec = QUESTION_CATALOG[key]
    text = spec["text_es"] if lang == "es" else spec["text"]
    if spec.get("needs_criteria"):
        crit = config.get("rental_criteria", "").strip()
        if crit and crit[-1] not in ".!?":
            crit += "."
        text = text.format(criteria=crit)
    if key == "property" and config.get("properties"):
        lines = [text] + [f"{i}. {name}" for i, name in enumerate(config["properties"], 1)]
        lines.append("(Responda con un número o el nombre.)" if lang == "es" else "(Reply with a number or the name.)")
        text = "\n".join(lines)
    return text


def match_property(message: str, config: dict) -> str | None:
    """Map a reply to one of the owner's listed properties: a bare number from
    the list ("2", "#2"), or a reply that names exactly one property (the
    longest name wins when one contains another, like "Palm" / "Palm Court")."""
    props = config.get("properties") or []
    if not props:
        return None
    t = " ".join((message or "").strip().lower().split())
    m = re.fullmatch(r"(?:#|no\.?\s*|number\s*|n[uú]mero\s*|option\s*|opci[oó]n\s*)?(\d{1,2})\s*[.!)]?", t)
    if m:
        k = int(m.group(1))
        return props[k - 1] if 1 <= k <= len(props) else None
    hits = [p for p in props if re.search(r"(?<!\w)" + re.escape(p.lower()) + r"(?!\w)", t)]
    if len(hits) > 1:
        longest = max(hits, key=len)
        if all(h.lower() in longest.lower() for h in hits):
            hits = [longest]
    return hits[0] if len(hits) == 1 else None


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


def _team(vertical: str, lang: str) -> str:
    return _t("team_seasonal" if vertical == "seasonal" else "team_long_term", lang)


def intro_line(vertical: str, lang: str = "en") -> str:
    return _t("intro", lang, team=_team(vertical, lang)) + " "


def closing_message(answers: dict, config: dict, vertical: str, lang: str = "en") -> str:
    """Same closing for every prospect: thanks + the same links."""
    t = _t
    team = _team(vertical, lang)
    parts = [t("closing_contact" if has_contact(answers) else "closing_no_contact", lang, team=team)]
    if config.get("application_url"):
        key = "closing_apply_seasonal" if vertical == "seasonal" else "closing_apply_long_term"
        parts.append(t(key, lang, url=config["application_url"]))
    if config.get("booking_url") and not (config.get("schedule") or {}).get("enabled"):
        parts.append(t("closing_booking_link", lang, url=config["booking_url"]))
    parts.append(t("anything_else", lang))
    return "\n\n".join(parts)


def summary_lines(answers: dict, config: dict) -> list[tuple[str, str]]:
    """(label, value) pairs in catalog order, for alerts and the portal."""
    answers = answers or {}
    out = []
    for k in CATALOG_ORDER:
        if k in answers:
            out.append((QUESTION_CATALOG[k]["label"], str(answers[k])))
    return out
