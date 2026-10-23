"""
core/lead_flow.py

Turn-by-turn leasing intake for rental clients. The LLM only extracts answers
(agents/lead_agent.py) and answers side questions (resolver). WHAT to ask and in
WHICH order is decided here, deterministically, from the owner's config, so
every prospect is asked the same questions the same way.

Returns a result dict for the orchestrator, or None when the intake does not
apply to this turn and normal handling should continue.
"""
import re
import time
import threading
from datetime import datetime, timezone

import agents.lead_agent as _lead_mod
import agents.resolver_agent as _resolver_mod
from core.db import SessionLocal
from core.models import Client, Lead
from core import leasing as L
from core import leads as leads_store
from core import notifier
from core import scheduling as S
from core.utils import logger

ACKS = ("Thanks!", "Got it.", "Great, thank you.")
OPT_OUT_REPLY = ("No problem at all. If you change your mind, just let me know. "
                 "Is there anything else I can help with?")
REASK_PREFIX = "Sorry, I didn't quite catch that. "
BRIEF_ANSWER_RULE = ("Answer in 1-3 sentences using only the knowledge base for business-specific "
                     "facts. Do not ask the visitor any questions and do not offer further help; "
                     "a short set of intake questions follows your reply automatically.")


def _now():
    return datetime.now(timezone.utc)


# ── Client context ──────────────────────────────────────────────────────────
def load_client_context(client_id: str) -> dict | None:
    if not client_id:
        return None
    try:
        db = SessionLocal()
    except Exception as e:
        logger.error(f"load_client_context: no DB session: {e}")
        return None
    try:
        c = db.get(Client, client_id)
        if not c:
            return None
        vertical = L.vertical_of(c.vertical)
        return {
            "client_id": c.id,
            "business_name": c.business_name or "",
            "bot_name": c.bot_name or "",
            "email": c.email or "",
            "vertical": vertical,
            "config": L.normalize_lead_config(c.lead_config, vertical),
        }
    except Exception as e:
        # Fall back to the plain assistant rather than failing the chat turn.
        logger.error(f"load_client_context error: {e}")
        return None
    finally:
        db.close()


def is_rental(ctx: dict | None) -> bool:
    return bool(ctx) and ctx["vertical"] in L.RENTAL_VERTICALS


def _alert_targets(ctx):
    cfg = ctx["config"]
    return (cfg.get("notify_email") or ctx["email"]), cfg.get("notify_phone") or ""


# ── Notifications ───────────────────────────────────────────────────────────
# The landing-page demo never emails or texts anyone: visitors type any
# address they like there, and the demo has no owner to alert.
DEMO_CLIENT_ID = "demo"


def _is_demo(ctx) -> bool:
    return bool(ctx) and ctx.get("client_id") == DEMO_CLIENT_ID


def _send_alert(ctx, lead, kind):
    if _is_demo(ctx):
        return
    to_email, to_phone = _alert_targets(ctx)
    lead_d = leads_store.lead_to_dict(lead, ctx["config"])
    appt = getattr(lead, "_new_appt", None)
    owner_ics = None
    if appt is not None:
        lines = [f"{x['label']}: {x['value']}" for x in lead_d["summary"]]
        owner_ics = S.ics(appt, f"{_kind(ctx).capitalize()}: {appt.name or 'prospect'}",
                          "\n".join(lines), _schedule(ctx).get("details", ""))
    notifier.notify_owner(kind, ctx["business_name"], to_email, to_phone, lead_d, ics=owner_ics)


def _send_booking_emails(ctx, appt):
    if _is_demo(ctx):
        return
    sched = _schedule(ctx)
    a = S.appt_to_dict(appt, sched)
    to_email, _ = _alert_targets(ctx)
    prospect_ics = S.ics(appt, f"{_kind(ctx).capitalize()} with {ctx['business_name'] or 'us'}",
                         sched.get("details", ""), sched.get("details", ""))
    notifier.send_appointment_emails("booked", business_name=ctx["business_name"], owner_email=to_email,
                                     appt=a, details=sched.get("details", ""),
                                     manage_url=notifier.appt_url(appt.cancel_token),
                                     ics_for_prospect=prospect_ics)


# ── Scheduling helpers ──────────────────────────────────────────────────────
# Booking a time after the intake needs clear intent, not just the word "view".
_SCHEDULING_RE = re.compile(
    r"\b(?:book|schedule|set up|arrange|make|get)\b.{0,25}\b(?:tour|showing|appointment|viewing|walk[\s-]?through|time to (?:see|visit|tour))\b"
    r"|\b(?:tour|see|visit|view)\s+(?:the|it|a|this|that|your)\s*(?:\w+\s+){0,2}(?:unit|place|home|property|apartment|house|condo|rental|2br|3br|1br|studio)\b"
    r"|\bcan (?:i|we) (?:tour|come (?:see|by|look)|see it|take a look|visit)\b"
    r"|\b(?:reschedule|book (?:a |another |a different |a new )?(?:time|tour|showing))\b", re.I)
_CHANGE_RE = re.compile(r"\b(?:reschedule|cancel|change (?:my|the) (?:time|appointment|tour|showing))\b", re.I)
_DECLINE_BOOKING_RE = re.compile(
    r"^\s*(?:(?:maybe|ok,?|okay,?)\s+)?(?:later|not now|not yet|no thanks?|no,? thank you|i'?ll call|call me instead|"
    r"i'?ll book later|i'?ll do it later|nah|no)\s*[.!,]?\s*(?:(?:thanks|thank you|thx)\s*[.!]?\s*)?$", re.I)
_NONE_WORK_RE = re.compile(r"\bnone of (?:these|those|them|the times)\b|\bneither\b|\bdon'?t work\b|\bdoesn'?t work\b", re.I)
# A "yes" only when the whole reply is a yes ("Sure, but Friday instead?" is not).
_YES_RE = re.compile(
    r"\s*(?:yes|yeah|yep|yup|sure|ok|okay|correct|perfect|book it|please do|do it|sounds good|that works|great|"
    r"yes please|yes,? book it|that'?s right)\s*[.!]?\s*(?:,?\s*(?:please|thanks|thank you|thx)\s*[.!]?)?\s*", re.I)
_NO_RE = re.compile(r"^\s*(?:no|nope|nah|not that|wrong|actually)\b", re.I)
_HUMAN_RE = re.compile(r"\b(?:talk|speak|chat)\s+(?:to|with)\s+(?:a\s+)?(?:real\s+)?(?:person|human|someone|agent|manager|leasing agent)\b|\breal person\b", re.I)


def _schedule(ctx) -> dict:
    return ctx["config"].get("schedule") or {}


def _sched_on(ctx) -> bool:
    return S.is_enabled(_schedule(ctx))


def _kind(ctx) -> str:
    return "call" if ctx["vertical"] == "seasonal" else "tour"


def _offer(lead, ctx, lead_in: str = "", prefer_text: str | None = None) -> str | None:
    """Offer open slots and remember which ones were shown. None if nothing is open."""
    sched = _schedule(ctx)
    slots = S.open_slots(ctx["client_id"], sched)
    if not slots:
        return None
    flags = dict(lead.flags or {})
    offer = None
    pref = prefer_text or flags.get("tour_pref")
    if pref:
        r = S.parse_choice(pref, [], slots, sched)
        if r and r[0] == "slot":
            offer = [r[1]] + [s for s in S.spread_offer(slots, sched) if s != r[1]][:S.OFFER_COUNT - 1]
        elif r and r[0] == "choices":
            offer = r[1][:S.OFFER_COUNT]
    offer = offer or S.spread_offer(slots, sched)
    flags["offered"] = [s.isoformat() for s in offer]
    lead.flags = flags
    return S.offer_text(offer, sched, _kind(ctx), lead_in)


def _contact(lead) -> dict:
    a = lead.answers or {}
    real = lambda k: a.get(k) if L._is_real(a.get(k)) else ""
    return {"name": real("name"), "email": real("email"), "phone": real("phone"), "property": real("property")}


def _booked_line(ctx, appt) -> str:
    sched = _schedule(ctx)
    parts = [f"You're booked for a {_kind(ctx)} on {S.fmt_slot(appt.start_at, sched)}."]
    if sched.get("details"):
        parts.append(sched["details"])
    if appt.email:
        parts.append(f"A confirmation is on its way to {appt.email}.")
    parts.append(f"Need to cancel? {notifier.appt_url(appt.cancel_token)}")
    return " ".join(parts)


def _finish_booked(lead, ctx, appt, prefix="", fresh=True) -> str:
    sched = _schedule(ctx)
    flags = dict(lead.flags or {})
    for k in ("offered", "tour_pref", "confirm_slot", "booking_open"):
        flags.pop(k, None)
    flags["appointment_id"] = appt.id
    lead.flags = flags
    answers = dict(lead.answers or {})
    answers["tour_time"] = "Booked: " + S.fmt_slot(appt.start_at, sched)
    lead.answers = answers
    lead.reask_count = 0
    if fresh:
        lead._new_appt = appt
    return _ask(lead, ctx, (prefix + "\n\n" if prefix else "") + _booked_line(ctx, appt))


def _try_book(lead, ctx, slot, prefix) -> str:
    sched = _schedule(ctx)
    # One booking per prospect: a double-tap or retry must not book twice.
    existing = S.lead_booking(ctx["client_id"], lead.id)
    if existing:
        return _finish_booked(lead, ctx, existing, prefix, fresh=False)
    appt = S.book(ctx["client_id"], lead.id, slot, sched, _contact(lead))
    if appt is not None:
        return _finish_booked(lead, ctx, appt, prefix)
    existing = S.lead_booking(ctx["client_id"], lead.id)  # a parallel turn won
    if existing:
        return _finish_booked(lead, ctx, existing, prefix, fresh=False)
    text = _offer(lead, ctx, lead_in="Sorry, that time was just taken. Here's what's still open:")
    return text or _no_slots(lead, ctx)


def _stop_booking(lead, ctx, reply, reopened, answer):
    """Prospect isn't booking now. In reopen mode the finished lead stays as it was."""
    flags = dict(lead.flags or {})
    for k in ("offered", "confirm_slot", "booking_open", "tour_pref"):
        flags.pop(k, None)
    lead.flags = flags
    lead.reask_count = 0
    lead.pending_key = None
    answers = dict(lead.answers or {})
    if reopened:
        prev = flags.pop("prev_tour_time", None)
        lead.flags = flags
        if prev:
            answers["tour_time"] = prev
        lead.answers = answers
        return f"{reply} Is there anything else I can help with?"
    answers["tour_time"] = answer
    lead.answers = answers
    return _ask(lead, ctx, reply)


def _handle_booking(lead, ctx, message, history, history_summary, reopened=False) -> str:
    """The prospect is answering a slot offer (during the intake, or after it
    when reopened=True; then the lead stays complete throughout)."""
    sched = _schedule(ctx)
    flags = dict(lead.flags or {})
    offered = [datetime.fromisoformat(x) for x in flags.get("offered", [])]
    lead.updated_at = _now()

    # New contact details typed at this step still count.
    answers = dict(lead.answers or {})
    phone, email = L.find_phone(message), L.find_email(message)
    if email:
        answers["email"] = email
    if phone and not S._pick_number(message, len(offered)):
        answers["phone"] = phone
    lead.answers = answers
    leads_store.sync_contact(lead)

    # Waiting on "yes" to a slot we proposed?
    pending_slot = flags.get("confirm_slot")
    if pending_slot:
        flags.pop("confirm_slot", None)
        lead.flags = flags
        if _YES_RE.fullmatch(message):
            return _try_book(lead, ctx, datetime.fromisoformat(pending_slot), "")
        if _NO_RE.match(message) and not (S._parse_day(message, S.now_utc().date()) or S._parse_time(message)):
            body = S.offer_text(offered, sched, _kind(ctx), "No problem. Here are the open times again:") if offered else (_offer(lead, ctx) or "")
            return body

    if _DECLINE_BOOKING_RE.match(message) or L.is_skip(message):
        return _stop_booking(lead, ctx, "No problem, our team can set up a time with you.", reopened,
                             "Didn't book online; team to follow up")

    prefix = ""
    if L.detect_accommodation(message):
        flags = dict(lead.flags or {})
        flags["accommodation_request"] = True
        lead.flags = flags
        prefix = L.ACCOMMODATION_REPLY
        if not flags.get("accommodation_notified"):
            _maybe_notify(ctx, lead, completing=False)

    slots = S.open_slots(ctx["client_id"], sched)
    choice = S.parse_choice(message, offered, slots, sched)

    if choice and choice[0] == "slot":
        if choice[2]:
            return _try_book(lead, ctx, choice[1], prefix)
        flags = dict(lead.flags or {})
        flags["confirm_slot"] = choice[1].isoformat()
        lead.flags = flags
        lead.reask_count = 0
        ask = f"Want me to book a {_kind(ctx)} on {S.fmt_slot(choice[1], sched)}? (yes / no)"
        return f"{prefix}\n\n{ask}".strip()

    # A real question that also mentions a day ("Do you allow pets on Saturday?")
    # gets an answer as well as the times.
    if choice and choice[0] in ("choices", "unavailable") and "?" in message and not prefix:
        prefix = _side_answer(ctx, message, history, history_summary)

    if choice and choice[0] == "choices":
        flags = dict(lead.flags or {})
        flags["offered"] = [s.isoformat() for s in choice[1]]
        lead.flags = flags
        lead.reask_count = 0
        body = S.offer_text(choice[1], sched, _kind(ctx), "Here's what's open:")
        return f"{prefix}\n\n{body}".strip()

    if choice and choice[0] == "unavailable":
        lead.reask_count = 0
        when = f"on {choice[1]}" if choice[1] != "that time" else "then"
        text = _offer(lead, ctx, lead_in=f"Nothing's open {when}. Here are the next open times:")
        return f"{prefix}\n\n{text or _no_slots(lead, ctx)}".strip()

    current = S.offer_text(offered, sched, _kind(ctx)) if offered else (_offer(lead, ctx) or "")
    if _NONE_WORK_RE.search(message):
        return ("No problem. Tell me a day and time that would work (like \"Thursday afternoon\" "
                "or \"Saturday at 11am\") and I'll check.")
    if "?" in message:
        side = _side_answer(ctx, message, history, history_summary)
        if reopened:
            lead.reask_count = (lead.reask_count or 0) + 1
            if lead.reask_count >= 2:  # they've moved on; stop offering times
                return _stop_booking(lead, ctx, side, True, None).replace(" Is there anything else I can help with?", "")
        return f"{side}\n\n{current}".strip()
    if prefix:
        return f"{prefix}\n\n{current}".strip()

    lead.reask_count = (lead.reask_count or 0) + 1
    if lead.reask_count >= 2:
        return _stop_booking(lead, ctx, "No problem, I'll pass that along and our team will confirm a time with you.",
                             reopened, f"Prefers: {message.strip()[:150]}")
    return (REASK_PREFIX + "Reply with a number from the list, or tell me a day and time that works "
            "(like \"Saturday at 11am\").")


def _no_slots(lead, ctx) -> str:
    answers = dict(lead.answers or {})
    answers["tour_time"] = "No open times online; team to schedule"
    lead.answers = answers
    return _ask(lead, ctx, "Our calendar is full online right now, so our team will reach out to find a time.")


def _notify(ctx, lead, kind):
    """Queue an alert for this turn. It is claimed + sent in _save(), after the
    lead's new answers are stored, so the alert matches what's in the portal."""
    lead._pending_alert = kind
    lead.notify_kind = kind


def _maybe_notify(ctx, lead, completing: bool):
    lead._ctx = ctx
    flags = dict(lead.flags or {})
    if not L.has_contact(lead.answers):
        return
    if completing and (lead.notify_kind != "complete" or getattr(lead, "_new_appt", None) is not None):
        _notify(ctx, lead, "complete")
        if flags.get("accommodation_request"):
            flags["accommodation_notified"] = True
            lead.flags = flags
    elif flags.get("accommodation_request") and not flags.get("accommodation_notified"):
        _notify(ctx, lead, "accommodation")
        flags["accommodation_notified"] = True
        lead.flags = flags


# ── Answer capture ──────────────────────────────────────────────────────────
def _looks_like_number(v: str) -> bool:
    return sum(ch.isdigit() for ch in str(v)) >= 7


def _open_keys(answers, config):
    return [k for k in L.active_questions(config) if k not in (answers or {})]


def _capture(lead, ctx, message: str, pending: str | None) -> dict:
    """Merge answers from this message into the lead. Returns the extractor result."""
    answers = dict(lead.answers or {})
    open_keys = _open_keys(answers, ctx["config"])

    if pending and L.is_skip(message):
        answers[pending] = L.DECLINED
        ext = {"answers": {}, "asked_question": False, "needs_human": False, "declined": False, "error": False}
    else:
        ext = _lead_mod.lead_extractor.run({"pending_key": pending, "open_keys": open_keys, "message": message})
        for k, v in ext["answers"].items():
            if k in open_keys:
                answers[k] = v

    # Deterministic contact parsing wins over the model for phone/email.
    phone, email = L.find_phone(message), L.find_email(message)
    if phone and ("phone" in open_keys or pending == "phone"):
        answers["phone"] = phone
    if email and ("email" in open_keys or pending == "email"):
        answers["email"] = email
    # The model must not invent contact details that aren't in the message.
    if "phone" in open_keys and "phone" in ext["answers"] and not phone and _looks_like_number(ext["answers"]["phone"]):
        answers.pop("phone", None)
    if "email" in open_keys and "email" in ext["answers"] and not email and "@" in ext["answers"]["email"]:
        answers.pop("email", None)

    if L.detect_accommodation(message):
        flags = dict(lead.flags or {})
        flags["accommodation_request"] = True
        lead.flags = flags
        # Never record an assistance animal as a pet, even if the extractor did.
        if "pets" in L.active_questions(ctx["config"]) and ("pets" in open_keys or "pets" not in (lead.answers or {})):
            answers["pets"] = L.ASSISTANCE_NOTE

    if _sched_on(ctx) and "tour_time" in open_keys:
        # With online booking, a time mentioned early is a preference to offer
        # first, never a final answer. Noticed deterministically, not by the model.
        pref = answers.pop("tour_time", None)
        today = S.now_utc().astimezone(S.ZoneInfo(_schedule(ctx)["timezone"])).date()
        if _SCHEDULING_RE.search(message) and (S._parse_day(message, today) or S._parse_time(message)):
            pref = message
        if pref:
            flags = dict(lead.flags or {})
            flags["tour_pref"] = pref
            lead.flags = flags

    lead.answers = answers
    leads_store.sync_contact(lead)
    return ext


def _side_answer(ctx, message, history, history_summary) -> str:
    res = _resolver_mod.resolver_agent.run({
        "message": message,
        "intent": "leasing",
        "history_summary": history_summary,
        "history": history,
        "client_id": ctx["client_id"],
        "vertical": ctx["vertical"],
        "extra_instructions": BRIEF_ANSWER_RULE,
    })
    text = (res.get("response_text") or "").strip()
    # Guardrail the model's free text here, before the fixed intake question is
    # appended, so a blocked answer never swallows the question.
    safe, flagged = L.check_output(text)
    if flagged:
        logger.warning(f"Fair Housing filter replaced an intake side answer (client={ctx['client_id'][:8]} flagged={flagged})")
    return safe


def _ask(lead, ctx, prefix: str) -> str:
    """Advance to the next question, or complete the intake."""
    nxt = L.next_question(lead.answers, ctx["config"])
    lead.updated_at = _now()
    if nxt is None:
        lead.status = "complete"
        lead.pending_key = None
        lead.completed_at = _now()
        _maybe_notify(ctx, lead, completing=True)
        body = L.closing_message(lead.answers, ctx["config"], ctx["vertical"])
    elif nxt == "tour_time" and _sched_on(ctx):
        if not L.has_contact(lead.answers):
            answers = dict(lead.answers or {})
            answers["tour_time"] = "Not scheduled (no contact info)"
            lead.answers = answers
            return _ask(lead, ctx, prefix)
        offer = _offer(lead, ctx)
        if offer is None:
            return _no_slots(lead, ctx) if not prefix else f"{prefix}\n\n{_no_slots(lead, ctx)}"
        lead.pending_key = nxt
        _maybe_notify(ctx, lead, completing=False)
        body = offer
    else:
        lead.pending_key = nxt
        _maybe_notify(ctx, lead, completing=False)
        body = L.question_text(nxt, ctx["config"])
    return f"{prefix}\n\n{body}".strip() if prefix else body


def _result(text, lead, route="leasing"):
    return {"response": text, "intent": "leasing", "route": route, "lead_id": lead.id}


# ── Persistence (short transactions; never held open across LLM calls) ─────
def _load_detached(client_id, session_id, create=False):
    """Fetch (or create) the session's latest lead, then detach it so no DB
    connection stays checked out while the model is thinking."""
    db = SessionLocal()
    try:
        lead = leads_store.get_lead_for_session(db, client_id, session_id)
        if lead is None and create:
            lead = leads_store.create_lead(db, client_id, session_id)
            db.commit()
        if lead is None:
            return None
        db.refresh(lead)
        db.expunge(lead)
        return lead
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


_TURN_COLUMNS = ("status", "pending_key", "reask_count", "answers", "flags",
                 "contact_name", "contact_phone", "contact_email", "updated_at", "completed_at")


def _save(lead):
    """Write only the columns a chat turn owns, so an owner marking the lead
    'contacted' (or a sweep recording an alert) mid-turn is never overwritten.
    Then claim and send any alert this turn queued."""
    alert = getattr(lead, "_pending_alert", None)
    db = SessionLocal()
    try:
        db.query(Lead).filter(Lead.id == lead.id, Lead.client_id == lead.client_id).update(
            {c: getattr(lead, c) for c in _TURN_COLUMNS}, synchronize_session=False)
        db.commit()
        if alert:
            if alert == "complete":
                # Completion always goes out, even after an earlier partial alert.
                db.query(Lead).filter(Lead.id == lead.id).update(
                    {"notified_at": _now(), "notify_kind": "complete"}, synchronize_session=False)
                db.commit()
                claimed = True
            elif alert == "accommodation":
                # Always sent once (flags.accommodation_notified, saved above). Also
                # stamp notified_at if unset so the partial sweep doesn't re-alert
                # the owner about the same prospect.
                leads_store.claim_notification(db, lead.id, "accommodation")
                claimed = True
            else:
                claimed = leads_store.claim_notification(db, lead.id, alert)
            if claimed:
                _send_alert(lead._ctx, lead, alert)
        appt = getattr(lead, "_new_appt", None)
        if appt is not None and getattr(lead, "_ctx", None):
            _send_booking_emails(lead._ctx, appt)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
        lead._pending_alert = None
        lead._new_appt = None


# ── Public entry points ─────────────────────────────────────────────────────
def continue_turn(ctx, session_id, message, history, history_summary) -> dict | None:
    """Handle this message if the session has an intake in progress."""
    try:
        lead = _load_detached(ctx["client_id"], session_id)
        if not lead or lead.status != "in_progress":
            return None
        pending = lead.pending_key

        if L.is_opt_out(message):
            lead.status = "abandoned"
            lead.pending_key = None
            lead.updated_at = _now()
            _save(lead)
            return _result(OPT_OUT_REPLY, lead)

        if pending == "tour_time" and _sched_on(ctx):
            lead._ctx = ctx
            if _HUMAN_RE.search(message):
                lead.updated_at = _now()
                _save(lead)
                return {"handoff": True, "lead_id": lead.id}
            text = _handle_booking(lead, ctx, message, history, history_summary)
            _save(lead)
            return _result(text, lead)

        had_accommodation = bool((lead.flags or {}).get("accommodation_request"))
        ext = _capture(lead, ctx, message, pending)
        new_accommodation = bool((lead.flags or {}).get("accommodation_request")) and not had_accommodation

        if ext["needs_human"]:
            lead.updated_at = _now()
            _save(lead)
            return {"handoff": True, "lead_id": lead.id}

        if ext["declined"]:
            lead.status = "abandoned"
            lead.pending_key = None
            lead.updated_at = _now()
            _maybe_notify(ctx, lead, completing=False)
            _save(lead)
            return _result(OPT_OUT_REPLY, lead)

        answered = pending is None or pending in (lead.answers or {})
        if new_accommodation:
            prefix = L.ACCOMMODATION_REPLY
            lead.reask_count = 0
        elif ext["asked_question"]:
            prefix = _side_answer(ctx, message, history, history_summary)
        elif not answered:
            lead.reask_count = (lead.reask_count or 0) + 1
            if lead.reask_count >= 2:
                answers = dict(lead.answers or {})
                answers[pending] = L.NOT_PROVIDED
                lead.answers = answers
                lead.reask_count = 0
                prefix = "No worries, let's keep going."
            else:
                lead.updated_at = _now()
                _save(lead)
                return _result(REASK_PREFIX + L.question_text(pending, ctx["config"]), lead)
        else:
            lead.reask_count = 0
            prefix = ACKS[len(lead.answers or {}) % len(ACKS)]

        text = _ask(lead, ctx, prefix)
        _save(lead)
        return _result(text, lead)
    except Exception as e:
        logger.error(f"lead_flow.continue_turn error: {e}", exc_info=True)
        return None


def post_intake(ctx, session_id, message, lead=None) -> dict | None:
    """After the intake is done: book a time if the prospect clearly asks, or
    point them to their cancel link if they want to change a booking. The lead
    stays "complete" the whole time. None when this turn isn't about booking."""
    try:
        if L.detect_accommodation(message):
            return None  # accommodation requests always go to the human handoff path
        lead = lead or _load_detached(ctx["client_id"], session_id)
        if not lead or lead.status != "complete":
            return None
        lead._ctx = ctx
        flags = dict(lead.flags or {})

        # Mid-booking after the intake.
        if flags.get("booking_open") and _sched_on(ctx):
            if L.is_opt_out(message):
                text = _stop_booking(lead, ctx, "No problem.", True, None)
                _save(lead)
                return _result(text, lead)
            if _HUMAN_RE.search(message):
                _stop_booking(lead, ctx, "", True, None)
                _save(lead)
                return None  # let the normal escalation path take it
            text = _handle_booking(lead, ctx, message, [], "", reopened=True)
            lead.status = "complete"
            lead.pending_key = None
            _save(lead)
            return _result(text, lead)

        appt = S.lead_booking(ctx["client_id"], lead.id)
        if appt and _CHANGE_RE.search(message):
            return _result(f"You're booked for {S.fmt_slot(appt.start_at, _schedule(ctx))}. You can cancel here: "
                           f"{notifier.appt_url(appt.cancel_token)} and then I can help you pick a new time.", lead)
        if (_sched_on(ctx) and not appt and L.has_contact(lead.answers) and _SCHEDULING_RE.search(message)):
            flags["prev_tour_time"] = (lead.answers or {}).get("tour_time")
            flags["booking_open"] = True
            flags["tour_pref"] = message
            flags.pop("appointment_id", None)
            lead.flags = flags
            lead.reask_count = 0
            offer = _offer(lead, ctx, lead_in=f"Sure! Here are the next open times for a {_kind(ctx)}:")
            if offer is None:
                return None
            lead.updated_at = _now()
            _save(lead)
            return _result(offer, lead)
        return None
    except Exception as e:
        logger.error(f"lead_flow.post_intake error: {e}", exc_info=True)
        return None


def start(ctx, session_id, message, history, history_summary) -> dict | None:
    """Begin (or reopen) an intake for a prospect who just showed rental interest."""
    try:
        lead = _load_detached(ctx["client_id"], session_id)
        if lead and lead.status == "complete" and not L.detect_accommodation(message):
            return post_intake(ctx, session_id, message, lead=lead)
        if lead and lead.status == "complete":
            if not L.detect_accommodation(message):
                return None
            # An accommodation request after the intake still goes to a person.
            flags = dict(lead.flags or {})
            first_time = not flags.get("accommodation_request")
            flags["accommodation_request"] = True
            lead.flags = flags
            lead.updated_at = _now()
            if first_time:
                _maybe_notify(ctx, lead, completing=False)
            _save(lead)
            return _result(L.ACCOMMODATION_REPLY + "\n\nIs there anything else I can help with?", lead)
        if lead is None:
            lead = _load_detached(ctx["client_id"], session_id, create=True)
        if lead.status == "abandoned":
            lead.status = "in_progress"

        had_accommodation = bool((lead.flags or {}).get("accommodation_request"))
        _capture(lead, ctx, message, pending=None)
        new_accommodation = bool((lead.flags or {}).get("accommodation_request")) and not had_accommodation

        if new_accommodation:
            prefix = L.ACCOMMODATION_REPLY
        else:
            prefix = _side_answer(ctx, message, history, history_summary)
        if L.next_question(lead.answers, ctx["config"]) is not None:
            prefix = (prefix + "\n\n" if prefix else "") + L.intro_line(ctx["vertical"]).strip()

        text = _ask(lead, ctx, prefix)
        _save(lead)
        return _result(text, lead)
    except Exception as e:
        logger.error(f"lead_flow.start error: {e}", exc_info=True)
        return None


def _get_appt(client_id, appt_id):
    if not appt_id:
        return None
    from core.models import Appointment
    db = SessionLocal()
    try:
        a = db.query(Appointment).filter(Appointment.client_id == client_id, Appointment.id == appt_id).first()
        if a:
            db.expunge(a)
        return a
    finally:
        db.close()


def on_appointment_canceled(appt, by: str) -> None:
    """Keep the lead in sync and tell the other side."""
    ctx = load_client_context(appt.client_id)
    if not ctx:
        return
    sched = _schedule(ctx)
    if appt.lead_id:
        db = SessionLocal()
        try:
            lead = db.query(Lead).filter(Lead.id == appt.lead_id, Lead.client_id == appt.client_id).first()
            if lead:
                answers = dict(lead.answers or {})
                answers["tour_time"] = f"Canceled by {'prospect' if by == 'prospect' else 'you'}: {S.fmt_slot(appt.start_at, sched)}"
                flags = dict(lead.flags or {})
                flags.pop("appointment_id", None)
                lead.answers, lead.flags, lead.updated_at = answers, flags, _now()
                db.commit()
        finally:
            db.close()
    if _is_demo(ctx):
        return
    to_email, _ = _alert_targets(ctx)
    notifier.send_appointment_emails(
        "canceled_by_prospect" if by == "prospect" else "canceled_by_owner",
        business_name=ctx["business_name"], owner_email=to_email, appt=S.appt_to_dict(appt, sched),
        details=sched.get("details", ""), manage_url=notifier.appt_url(appt.cancel_token))


def send_due_reminders() -> int:
    """Day-before reminder emails to prospects."""
    db = SessionLocal()
    sent = 0
    try:
        for appt in S.due_reminders(db):
            if not S.wants_reminder(appt):
                S.claim_reminder(db, appt.id)  # booked <12h ahead: mark done so it never clogs the queue
                continue
            ctx = load_client_context(appt.client_id)
            if not ctx or not appt.email or not S.claim_reminder(db, appt.id):
                continue
            if _is_demo(ctx):
                continue
            sched = _schedule(ctx)
            to_email, _ = _alert_targets(ctx)
            notifier.send_appointment_emails(
                "reminder", business_name=ctx["business_name"], owner_email=to_email,
                appt=S.appt_to_dict(appt, sched), details=sched.get("details", ""),
                manage_url=notifier.appt_url(appt.cancel_token))
            sent += 1
    except Exception as e:
        db.rollback()
        logger.error(f"send_due_reminders error: {e}")
    finally:
        db.close()
    return sent


# ── Partial-lead sweep ──────────────────────────────────────────────────────
_sweep_lock = threading.Lock()
_last_sweep = {"t": 0.0}
SWEEP_EVERY_SECONDS = 60


def sweep_partial_leads() -> int:
    """Alert owners about prospects who shared contact info, then went quiet."""
    db = SessionLocal()
    sent = 0
    try:
        for lead in leads_store.stale_partial_leads(db):
            ctx = load_client_context(lead.client_id)
            if not ctx or not leads_store.claim_notification(db, lead.id, "partial"):
                continue
            _send_alert(ctx, lead, "partial")
            sent += 1
    except Exception as e:
        db.rollback()
        logger.error(f"sweep_partial_leads error: {e}")
    finally:
        db.close()
    return sent


def maybe_sweep() -> None:
    """Cheap opportunistic trigger from live traffic, at most once a minute per process.
    scripts/sweep_leads.py does the same on a schedule for quiet periods."""
    now = time.time()
    if now - _last_sweep["t"] < SWEEP_EVERY_SECONDS or not _sweep_lock.acquire(blocking=False):
        return
    try:
        _last_sweep["t"] = now
        sweep_partial_leads()
        send_due_reminders()
    finally:
        _sweep_lock.release()
