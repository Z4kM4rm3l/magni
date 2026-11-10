"""
core/escalations.py

Chats that need a person.

What this module guarantees:
  * A durable record is written BEFORE any alert is sent, so a failed or
    unconfigured email never loses the request. The owner sees it in the
    portal, and retry_unsent() keeps trying the email.
  * Asking for the visitor's contact details and reading them out of the reply
    is ordinary code with fixed wording (English and Spanish in core/i18n.py),
    never a model call. An email or phone number is only recorded when it is
    literally in the visitor's message.
  * high_risk() catches the clearest "I need a person" and legal-threat
    messages with fixed patterns, so they escalate even when the model is down
    or misreads them.

Nothing here raises into a chat turn: every public function logs and returns a
safe value on error.
"""
import re
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta

from core import notifier
from core.db import SessionLocal
from core.emailer import build_escalation_alert, build_transcript
from core.i18n import detect_lang, t
from core.contact import find_email, find_phone
from core.models import Escalation
from core.utils import logger

DEMO_CLIENT_ID = "demo"  # the landing-page demo never emails or texts anyone

# collecting = asked for contact details, none yet
# confirming = the visitor gave contact details earlier in the chat; asked
#              whether that is the best way to reach them
# open       = the owner needs to follow up
# closed     = the owner marked it handled
ACTIVE = ("collecting", "confirming", "open")
STATUSES = ("collecting", "confirming", "open", "closed")
# After asking for contact details, how many visitor turns without any before
# the assistant stops waiting (the owner already has the alert either way).
MAX_ASKS = 2
MAX_NOTIFY_ATTEMPTS = 5
RETRY_AFTER = timedelta(minutes=2)
RETRY_WINDOW = timedelta(days=3)
MAX_TRANSCRIPT_CHARS = 60000


def _now():
    return datetime.now(timezone.utc)


def _aware(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ── Deterministic high-risk gate ────────────────────────────────────────────
_PERSON = (r"(?:human|person|agent|representative|rep|someone|somebody|manager|supervisor|"
           r"operator|owner|staff member|team member|live chat)")
_HUMAN_RE = re.compile(
    r"\b(?:speak|talk|chat)\s+(?:to|with)\s+(?:an?\s+|the\s+|your\s+)?(?:real\s+|live\s+|actual\s+)?" + _PERSON + r"\b|"
    r"\b(?:connect|transfer|put|get)\s+me\s+(?:to|through to|with|in touch with)?\s*(?:an?\s+|the\s+|your\s+)?(?:real\s+|live\s+)?" + _PERSON + r"\b|"
    r"\b(?:real|live|actual)\s+(?:human|person|agent)\b|"
    r"\b(?:call|phone|ring)\s+me\b|"
    r"\b(?:have|get)\s+(?:someone|somebody)\s+(?:to\s+)?(?:call|contact|email|reach)\b|"
    r"\b(?:hablar|comunicarme|chatear)\s+con\s+(?:una?\s+)?(?:persona|humano|agente|alguien|gerente|encargad[oa]|representante|asesor[a]?)\b|"
    r"\bpersona\s+real\b|\bll[aá]m[ae]n?me\b",
    re.I,
)
_LEGAL_RE = re.compile(
    r"\b(?:lawsuit|legal action|small claims|take (?:you|this|it) to court|"
    r"sue you|suing you|i(?:'ll| will| am going to|'m going to| am gonna|'m gonna) sue|"
    r"my (?:lawyer|attorney)|(?:contact|call|talk to|speak to|get|hire) (?:a|an|my) (?:lawyer|attorney)|"
    r"chargeback|dispute (?:the|this|my) charge|attorney general|better business bureau|"
    r"report(?:ing)? you|file a complaint|formal complaint|discriminat\w+|"
    r"demandar(?:l[oa]s|les)?|acci[oó]n legal|mi abogad[oa]|discriminaci[oó]n|queja formal)\b",
    re.I,
)


def high_risk(message: str) -> str | None:
    """A plain-language reason when the message must go to a person no matter
    what the model says, else None."""
    text = message or ""
    if _LEGAL_RE.search(text):
        return "Visitor mentioned legal action or a formal complaint."
    if _HUMAN_RE.search(text):
        return "Visitor asked for a person."
    return None


# ── Contact parsing (deterministic) ─────────────────────────────────────────
_NAME_INTRO_RE = re.compile(
    r"\b(?:my name is|my name's|name is|name's|this is|i am|i'm|im|it's|its|"
    r"me llamo|mi nombre es|soy)\s+([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'\-]+(?:\s+[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'\-]+){0,2})",
    re.I,
)
_NOT_NAMES = {
    "a", "an", "the", "not", "no", "yes", "ok", "okay", "sure", "hi", "hello", "hey", "thanks", "thank",
    "please", "just", "still", "very", "really", "so", "trying", "looking", "having", "waiting", "calling",
    "writing", "going", "upset", "angry", "frustrated", "interested", "here", "back", "sorry", "done",
    "unable", "unsure", "wondering", "available", "fine", "good", "new", "your", "my", "in", "on", "at",
    "and", "or", "but", "email", "phone", "number", "cell", "mobile", "is", "it", "me", "you", "call",
    "text", "reach", "contact", "best", "si", "sí", "gracias", "hola", "un", "una", "el", "la", "muy",
    "what", "when", "where", "why", "how", "who", "which", "can", "could", "do", "does", "did", "will",
    "would", "are", "was", "order", "refund", "help", "cancel", "return", "nothing", "none", "nevermind",
}
_FILLER_RE = re.compile(
    r"\b(?:my|the|best|email|e-mail|phone|number|cell|mobile|is|are|at|and|or|you|can|reach|me|call|text|"
    r"contact|it's|its|name|mi|correo|tel[eé]fono|n[uú]mero|es|y|o|al)\b", re.I)
_AFFIRM_RE = re.compile(
    r"^\s*(?:yes|yep|yeah|yup|ya|correct|right|sure|ok(?:ay)?|perfect|great|"
    r"that'?s (?:right|correct|fine|good|it|the best|best)|that works|that is (?:right|correct|fine)|"
    r"sounds good|works for me|s[ií]|claro|correcto|as[ií] es|est[aá] bien|ese (?:est[aá] bien|es))\b"
    r"[^?]{0,40}$", re.I)
_DECLINE_RE = re.compile(
    r"^\s*(?:no|nope|nah|no thanks?|no,? thank you|never ?mind|nvm|not now|skip|"
    r"i'?d rather not|rather not|prefer not(?: to)?(?: say)?|no,? gracias|prefiero no|mejor no|olv[ií]delo)"
    r"\s*[.!]?\s*$", re.I)


def _clean_name(raw: str) -> str | None:
    words = [w for w in re.split(r"\s+", (raw or "").strip()) if w]
    kept = []
    for w in words:
        if w.lower().strip(".,!") in _NOT_NAMES:
            break
        kept.append(w.strip(".,!"))
    if not kept or len(kept) > 3:
        return None
    name = " ".join(kept)
    if len(name) < 2 or len(name) > 60 or any(ch.isdigit() for ch in name):
        return None
    return name.title() if name.islower() or name.isupper() else name


def find_name(text: str) -> str | None:
    """A name the visitor clearly gave: "my name is Dana Lee", a short name next
    to an email or phone number ("Dana Lee, dana@example.com"), or a reply that
    is nothing but a capitalized name ("Maria Gomez"). Anything looser is left
    alone: a missed name costs nothing, an invented one is embarrassing."""
    text = (text or "").strip()
    m = _NAME_INTRO_RE.search(text)
    if m:
        name = _clean_name(m.group(1))
        if name:
            return name
    rest = text
    email = find_email(rest)
    has_contact = bool(email or find_phone(rest))
    if email:
        rest = re.sub(re.escape(email), " ", rest, flags=re.I)
    rest = re.sub(r"(?:\+?1[\s.\-]?)?\(?\b\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}\b", " ", rest)
    rest = _FILLER_RE.sub(" ", rest)
    rest = re.sub(r"[,;:.!\-–()/]+", " ", rest).strip()
    words = rest.split()
    if 1 <= len(words) <= 3 and all(re.fullmatch(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ']*", w) for w in words):
        if not any(w.lower() in _NOT_NAMES for w in words) and (has_contact or all(w[0].isupper() for w in words)):
            return _clean_name(rest)
    return None


def find_contact(text: str) -> dict:
    return {"name": find_name(text), "email": find_email(text), "phone": find_phone(text)}


def _contact_from_chat(db, client_id, session_id, history, message) -> dict:
    """Contact details the visitor already gave earlier in this chat."""
    out = {"name": None, "email": None, "phone": None}
    for text in [m.get("content", "") for m in (history or []) if m.get("role") == "user"] + [message or ""]:
        out["email"] = find_email(text) or out["email"]
        out["phone"] = find_phone(text) or out["phone"]
        m = _NAME_INTRO_RE.search(text or "")
        if m:
            out["name"] = _clean_name(m.group(1)) or out["name"]
    return out


# ── Records ─────────────────────────────────────────────────────────────────
def _to_dict(e: Escalation) -> dict:
    def iso(dt):
        dt = _aware(dt)
        return dt.isoformat() if dt else None
    return {
        "id": e.id,
        "session_id": e.session_id,
        "status": e.status,
        "source": e.source or "",
        "action": e.action or "",
        "reason": e.reason or "",
        "last_message": e.last_message or "",
        "transcript": e.transcript or "",
        "name": e.contact_name or "",
        "email": e.contact_email or "",
        "phone": e.contact_phone or "",
        "notified": bool(e.notified_at),
        "created_at": iso(e.created_at),
        "updated_at": iso(e.updated_at),
    }


def _active(db, client_id: str, session_id: str) -> Escalation | None:
    return (
        db.query(Escalation)
        .filter(Escalation.client_id == client_id, Escalation.session_id == session_id,
                Escalation.status.in_(ACTIVE))
        .order_by(Escalation.created_at.desc())
        .first()
    )


def _has_contact(e: Escalation) -> bool:
    return bool(e.contact_email or e.contact_phone)


def _reach(e: Escalation) -> str:
    return e.contact_email or e.contact_phone or ""


def _all_contact(e: Escalation, lang: str) -> str:
    """Every way we have to reach the visitor, for the "is this the best
    contact?" question."""
    joiner = " y " if lang == "es" else " and "
    return joiner.join(x for x in (e.contact_email, e.contact_phone) if x)


def open_escalation(ctx: dict | None, session_id: str, message: str, history: list, *,
                    reason: str, source: str, action: str = "collect_contact") -> dict | None:
    """Record that this chat needs a person and alert the owner.

    Returns {"id", "needs_contact", "suffix"}: `suffix` is the fixed line to add
    to the assistant's reply (ask for contact details, or confirm the team has
    them). Returns None when there is no account to record against, or on error;
    the caller then just sends its reply unchanged.
    """
    if not ctx or not ctx.get("client_id"):
        return None
    client_id = ctx["client_id"]
    try:
        db = SessionLocal()
    except Exception as e:
        logger.error(f"open_escalation: no DB session: {e}")
        return None
    try:
        now = _now()
        esc = _active(db, client_id, session_id)
        is_new = esc is None
        if is_new:
            esc = Escalation(id=str(uuid.uuid4()), client_id=client_id, session_id=session_id,
                             status="collecting", ask_count=0, notify_attempts=0, created_at=now,
                             lang=detect_lang(message) or "en")
            db.add(esc)
        esc.reason = (reason or "")[:500] or esc.reason
        esc.source = source or esc.source
        if action in ("collect_contact", "human_handoff"):
            esc.action = action
        esc.last_message = (message or "")[:1000]
        esc.transcript = build_transcript(history, message)[-MAX_TRANSCRIPT_CHARS:]
        esc.updated_at = now

        had_contact = _has_contact(esc)
        found = _contact_from_chat(db, client_id, session_id, history, message)
        esc.contact_name = esc.contact_name or found["name"]
        esc.contact_email = esc.contact_email or found["email"]
        esc.contact_phone = esc.contact_phone or found["phone"]
        # Contact details from earlier in the chat are used, but the visitor is
        # asked whether they are the best way to reach them for the follow-up.
        if _has_contact(esc) and esc.status == "collecting":
            esc.status = "confirming"

        lang = esc.lang or "en"
        if esc.status == "confirming":
            suffix = t("esc_confirm_contact", lang, contact=_all_contact(esc, lang))
        elif _has_contact(esc):
            suffix = t("esc_notified", lang)
        elif esc.status == "collecting":
            suffix = t("esc_ask_contact", lang)
        else:
            suffix = t("esc_noted", lang)
        out = {"id": esc.id, "needs_contact": not _has_contact(esc), "suffix": suffix}
        send = is_new or (_has_contact(esc) and not had_contact)
        db.commit()
        logger.info(f"Escalation {'opened' if is_new else 'updated'}: client={client_id[:8]} "
                    f"session={session_id[:8]} source={source} contact={'yes' if not out['needs_contact'] else 'no'}")
    except Exception as e:
        db.rollback()
        logger.error(f"open_escalation error: {e}", exc_info=True)
        return None
    finally:
        db.close()
    if send:
        notifier._dispatch(deliver, out["id"])
    return out


def continue_capture(ctx: dict | None, session_id: str, message: str, history: list) -> dict | None:
    """If this chat is waiting on the visitor's contact details, read them out
    of this message.

    Returns {"response": text} when this module fully answers the turn (contact
    captured, a name alone, or a clear "no thanks"), {"reminder": text} when the
    message has no contact details and should be answered normally with a short
    reminder added, or None when there's nothing to do.
    """
    if not ctx or not ctx.get("client_id"):
        return None
    client_id = ctx["client_id"]
    try:
        db = SessionLocal()
    except Exception as e:
        logger.error(f"continue_capture: no DB session: {e}")
        return None
    deliver_id = None
    try:
        esc = _active(db, client_id, session_id)
        if not esc or esc.status not in ("collecting", "confirming"):
            return None
        lang = esc.lang or "en"
        now = _now()
        found = find_contact(message)
        esc.updated_at = now

        if esc.status == "confirming" and not (found["email"] or found["phone"]):
            short = len((message or "").strip()) <= 60
            if short and _AFFIRM_RE.match(message or ""):
                # The owner's alert already carries these details.
                esc.status = "open"
                result = {"response": t("esc_confirmed", lang, contact=_all_contact(esc, lang))}
            elif short and _DECLINE_RE.match(message or ""):
                esc.status = "collecting"
                esc.ask_count = 0
                result = {"response": t("esc_ask_best", lang)}
            else:
                # They moved on. The details we have stand; answer normally.
                esc.status = "open"
                result = None
        elif esc.status == "confirming":
            # They gave a different email or phone: that one replaces what we
            # had, and the owner gets the correction.
            changed = ((found["email"] and found["email"] != esc.contact_email)
                       or (found["phone"] and found["phone"] != esc.contact_phone))
            if found["email"]:
                esc.contact_email, esc.contact_phone = found["email"], found["phone"]
            else:
                esc.contact_phone, esc.contact_email = found["phone"], None
            esc.contact_name = found["name"] or esc.contact_name
            esc.status = "open"
            esc.transcript = build_transcript(history, message)[-MAX_TRANSCRIPT_CHARS:]
            result = {"response": t("esc_confirmed", lang, contact=_all_contact(esc, lang))}
            if changed:
                esc.contact_notified_at = None
                deliver_id = esc.id
        elif found["email"] or found["phone"]:
            if ((found["email"] and found["email"] != esc.contact_email)
                    or (found["phone"] and found["phone"] != esc.contact_phone)):
                esc.contact_notified_at = None  # the owner is owed these details
            esc.contact_email = found["email"] or esc.contact_email
            esc.contact_phone = found["phone"] or esc.contact_phone
            esc.contact_name = found["name"] or esc.contact_name
            esc.status = "open"
            esc.transcript = build_transcript(history, message)[-MAX_TRANSCRIPT_CHARS:]
            name = f", {esc.contact_name}" if esc.contact_name else ""
            result = {"response": t("esc_thanks", lang, name=name, contact=_reach(esc))}
            deliver_id = esc.id
        elif _DECLINE_RE.match(message or ""):
            esc.status = "open"
            result = {"response": t("esc_no_contact", lang)}
        elif found["name"] and not esc.contact_name:
            esc.contact_name = found["name"]
            esc.ask_count = (esc.ask_count or 0) + 1
            if esc.ask_count >= MAX_ASKS:
                esc.status = "open"
            result = {"response": t("esc_ask_reach", lang, name=esc.contact_name)}
        else:
            esc.ask_count = (esc.ask_count or 0) + 1
            if esc.ask_count >= MAX_ASKS:
                esc.status = "open"   # stop waiting; the owner already has the alert
                result = None
            else:
                result = {"reminder": t("esc_reminder", lang)}
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"continue_capture error: {e}", exc_info=True)
        return None
    finally:
        db.close()
    if deliver_id:
        notifier._dispatch(deliver, deliver_id)
    return result


# ── Owner alerts ────────────────────────────────────────────────────────────
def portal_url() -> str:
    return f"{notifier.base_url()}/portal#escalations"


def deliver(esc_id: str) -> bool:
    """Send whichever alert this record still owes the owner: the first alert,
    or the follow-up carrying contact details that arrived later. Marks the
    record only when the email actually went out, so retry_unsent() can pick up
    anything that failed."""
    from core.client_manager import client_context
    try:
        db = SessionLocal()
    except Exception as e:
        logger.error(f"escalation deliver: no DB session: {e}")
        return False
    try:
        esc = db.get(Escalation, esc_id)
        if not esc:
            return False
        first = esc.notified_at is None
        owes_contact = _has_contact(esc) and esc.contact_notified_at is None
        if not first and not owes_contact:
            return True
        now = _now()
        if esc.client_id == DEMO_CLIENT_ID:
            esc.notified_at = esc.notified_at or now
            esc.contact_notified_at = now if _has_contact(esc) else None
            db.commit()
            return True
        ctx = client_context(esc.client_id)
        if not ctx:
            return False
        to_email = (ctx["notify_email"] or ctx["email"] or "").strip()
        to_phone = ctx["notify_phone"] or ""
        contact = {"name": esc.contact_name, "email": esc.contact_email, "phone": esc.contact_phone}
        subject, body, reply_to = build_escalation_alert(
            ctx["business_name"], reason=esc.reason or "", action=esc.action or "",
            session_id=esc.session_id, transcript=esc.transcript or "", contact=contact,
            update=not first, portal_link=portal_url(),
        )
        esc.notify_attempts = (esc.notify_attempts or 0) + 1
        sent = bool(to_email) and notifier.send_email(to_email, subject, body, reply_to=reply_to)
        if sent:
            esc.notified_at = esc.notified_at or now
            if _has_contact(esc):
                esc.contact_notified_at = now
            if first and to_phone:
                who = esc.contact_name or esc.contact_email or esc.contact_phone or "A website visitor"
                notifier.send_sms(to_phone, f"{ctx['business_name'] or 'Your website chat'}: {who} needs a person. "
                                            f"{(esc.reason or '')[:120]} Details in your email.")
        else:
            logger.warning(f"Escalation alert not sent (attempt {esc.notify_attempts}) for "
                           f"client={esc.client_id[:8]} session={esc.session_id[:8]}; it stays in the portal.")
        db.commit()
        return sent
    except Exception as e:
        db.rollback()
        logger.error(f"escalation deliver error: {e}", exc_info=True)
        return False
    finally:
        db.close()


def retry_unsent() -> int:
    """Retry alerts that never went out (email provider down, bad config).
    Run from the sweep. Returns how many were delivered this pass."""
    from sqlalchemy import or_, and_
    try:
        db = SessionLocal()
    except Exception as e:
        logger.error(f"escalation retry: no DB session: {e}")
        return 0
    try:
        now = _now()
        owes_contact = and_(Escalation.contact_notified_at.is_(None),
                            or_(Escalation.contact_email.isnot(None), Escalation.contact_phone.isnot(None)))
        ids = [r[0] for r in db.query(Escalation.id).filter(
            or_(Escalation.notified_at.is_(None), owes_contact),
            Escalation.status != "closed",
            or_(Escalation.notify_attempts.is_(None), Escalation.notify_attempts < MAX_NOTIFY_ATTEMPTS),
            Escalation.created_at >= now - RETRY_WINDOW,
            Escalation.updated_at <= now - RETRY_AFTER,
        ).limit(50).all()]
    except Exception as e:
        logger.error(f"escalation retry error: {e}")
        return 0
    finally:
        db.close()
    return sum(1 for i in ids if deliver(i))


_retry_lock = threading.Lock()
_last_retry = {"t": 0.0}
RETRY_EVERY_SECONDS = 60


def maybe_retry() -> None:
    """Cheap opportunistic trigger from live chat traffic, at most once a
    minute per process. scripts/retry_alerts.py does the same on a schedule for
    quiet periods."""
    now = time.time()
    if now - _last_retry["t"] < RETRY_EVERY_SECONDS or not _retry_lock.acquire(blocking=False):
        return
    try:
        _last_retry["t"] = now
        retry_unsent()
    finally:
        _retry_lock.release()


# ── Portal ──────────────────────────────────────────────────────────────────
def list_for_client(client_id: str, limit: int = 100) -> list[dict]:
    db = SessionLocal()
    try:
        rows = (db.query(Escalation).filter(Escalation.client_id == client_id)
                .order_by(Escalation.created_at.desc()).limit(limit).all())
        return [_to_dict(e) for e in rows]
    finally:
        db.close()


def set_status(client_id: str, esc_id: str, status: str) -> dict | None:
    """Owner marks a chat handled ("closed") or reopens it ("open"). Always
    addressed by (client_id, id), never by id alone."""
    if status not in ("open", "closed"):
        return None
    db = SessionLocal()
    try:
        esc = (db.query(Escalation)
               .filter(Escalation.id == esc_id, Escalation.client_id == client_id).first())
        if not esc:
            return None
        esc.status = status
        esc.updated_at = _now()
        db.commit()
        db.refresh(esc)
        return _to_dict(esc)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def metrics(client_id: str, days: int = 30) -> dict:
    """The numbers on the Analytics card: chats, and how many needed a person.
    Every number is a COUNT in the database (indexed by client and date), so it
    stays fast however much history a tenant has."""
    from sqlalchemy import func
    from core.models import Conversation
    since = _now() - timedelta(days=days)
    db = SessionLocal()
    try:
        conv = db.query(func.count(Conversation.session_id)).filter(Conversation.client_id == client_id)
        base = db.query(func.count(Escalation.id)).filter(Escalation.client_id == client_id)
        recent = base.filter(Escalation.created_at >= since)
        return {
            "days": days,
            "conversations": conv.filter(Conversation.started_at >= since).scalar() or 0,
            "conversations_all_time": conv.scalar() or 0,
            "escalations": recent.scalar() or 0,
            "escalations_with_contact": recent.filter(
                (Escalation.contact_email.isnot(None)) | (Escalation.contact_phone.isnot(None))).scalar() or 0,
            "escalations_waiting": base.filter(Escalation.status.in_(ACTIVE)).scalar() or 0,
        }
    finally:
        db.close()
