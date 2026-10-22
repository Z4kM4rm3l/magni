"""
core/lead_flow.py

Turn-by-turn leasing intake for rental clients. The LLM only extracts answers
(agents/lead_agent.py) and answers side questions (resolver). WHAT to ask and in
WHICH order is decided here, deterministically, from the owner's config, so
every prospect is asked the same questions the same way.

Returns a result dict for the orchestrator, or None when the intake does not
apply to this turn and normal handling should continue.
"""
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
        vertical = c.vertical or "general"
        return {
            "client_id": c.id,
            "business_name": c.business_name or "",
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
def _send_alert(ctx, lead, kind):
    to_email, to_phone = _alert_targets(ctx)
    notifier.notify_owner(kind, ctx["business_name"], to_email, to_phone,
                          leads_store.lead_to_dict(lead, ctx["config"]))


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
    if completing and lead.notify_kind != "complete":
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
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
        lead._pending_alert = None


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


def start(ctx, session_id, message, history, history_summary) -> dict | None:
    """Begin (or reopen) an intake for a prospect who just showed rental interest."""
    try:
        lead = _load_detached(ctx["client_id"], session_id)
        if lead and lead.status == "complete":
            if not L.detect_accommodation(message):
                return None  # already handed off; let the normal assistant answer
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
    finally:
        _sweep_lock.release()
