import os
from core.db import SessionLocal
from core.client_guard import track_and_validate_request
from core.utils import logger
import agents.triage_agent as _triage_mod
import agents.resolver_agent as _resolver_mod
import agents.escalation_agent as _escalation_mod
from core import lead_flow as _lead_flow
from core import escalations as _esc
from core.leasing import check_output, detect_accommodation, looks_like_leasing, DEFAULT_VERTICAL
from core.i18n import detect_lang, t


def _summarize_history(history: list) -> str:
    if not history:
        return "No prior context."
    recent = history[-6:]  # last 3 exchanges
    return "\n".join(
        f"{m['role'].upper()}: {m['content'][:100]}" for m in recent
    )


# Routes where the reply already talks about handing the chat to the team.
_ESCALATION_ROUTES = ("escalation", "resolver_escalated", "escalation_contact")


class Orchestrator:
    def run(
        self,
        message: str,
        session_id: str,
        history: list,
        api_key: str | None = None,
        client_id: str | None = None,
    ) -> dict:
        """
        Entry point for all chat traffic.

        api_key=None means demo path — billing gate already cleared at the
        route level by _check_demo_limit(). Pass api_key for paying clients;
        the orchestrator will run track_and_validate_request() and commit.
        """
        # Client identity for escalation emails — populated from billing gate.
        client_email = ""
        client_name = ""
        resolved_client_id = client_id  # demo uses this; paying path overrides below

        # ── PAYING CLIENT BILLING GATE ───────────────────────────────────────
        if api_key:
            db = SessionLocal()
            try:
                allowed, reason, client = track_and_validate_request(db, api_key, session_id)
                if not allowed:
                    db.close()
                    return {
                        "response": reason,
                        "intent": "general",
                        "session_id": session_id,
                        "route": "blocked",
                    }
                # client_guard.py defers commit to caller — close the loop here.
                db.commit()
                # Extract identity strings before closing the session.
                client_email = client.email or ""
                client_name = client.business_name or ""
                resolved_client_id = client.id  # authoritative KB scope from the API key
                usage_alert = getattr(client, "_usage_alert", None)
                if usage_alert and client_email and client.id != "demo":
                    from core import notifier
                    notifier.send_usage_warning(client_email, client_name, client.monthly_used, client._usage_limit,
                                                at_limit=(usage_alert == "limit"))
            except Exception as e:
                logger.error(f"Orchestrator billing gate error: {e}")
                db.rollback()
                db.close()
                return {
                    "response": "Unable to verify account status. Please try again in a moment.",
                    "intent": "general",
                    "session_id": session_id,
                    "route": "blocked",
                }
            finally:
                db.close()

        # ── HISTORY SUMMARIZATION ────────────────────────────────────────────
        history_summary = _summarize_history(history)

        # ── LEASING ASSISTANT (rental clients only) ──────────────────────────
        try:
            _lead_flow.maybe_sweep()
        except Exception as e:  # never let the sweep affect a chat reply
            logger.error(f"lead sweep error: {e}")
        ctx = _lead_flow.load_client_context(resolved_client_id)
        rental = _lead_flow.is_rental(ctx)
        vertical = ctx["vertical"] if ctx else DEFAULT_VERTICAL
        lang = detect_lang(message) or "en"

        extra_note = ""  # fixed text appended to the final reply (residents)
        contact_reminder = ""  # fixed nudge while a chat waits on contact details

        def finish(result: dict) -> dict:
            result.setdefault("session_id", session_id)
            result["client_id"] = resolved_client_id
            # Intake replies are already filtered (side answers) or fixed text.
            if rental and result.get("route") != "leasing":
                safe, flagged = check_output(result.get("response", ""), detect_lang(message))
                if flagged:
                    logger.warning(
                        f"Fair Housing filter replaced a reply (client={str(resolved_client_id)[:8]} "
                        f"session={session_id[:8]} flagged={flagged})"
                    )
                    result["response"] = safe
            if extra_note:
                result["response"] = f"{result.get('response', '').rstrip()}\n\n{extra_note}".strip()
            if contact_reminder and result.get("route") not in _ESCALATION_ROUTES:
                result["response"] = f"{result.get('response', '').rstrip()}\n\n{contact_reminder}".strip()
            return result

        def escalate(reply: str, reason: str, source: str, action: str = "collect_contact") -> str:
            """Record that this chat needs a person, alert the owner, and return
            the reply with the fixed contact line added. Never raises."""
            record = _esc.open_escalation(ctx, session_id, message, history,
                                          reason=reason, source=source, action=action)
            if not record:
                return reply
            return f"{reply.rstrip()}\n\n{record['suffix']}".strip()

        # ── CONTACT CAPTURE (a chat already handed to the team) ──────────────
        # Runs before any model call: reading an email or phone number out of
        # the visitor's reply is ordinary code.
        captured = _esc.continue_capture(ctx, session_id, message, history)
        if captured and captured.get("response"):
            return finish({
                "response": captured["response"],
                "intent": "general",
                "session_id": session_id,
                "route": "escalation_contact",
            })
        if captured:
            contact_reminder = captured.get("reminder", "")

        forced_escalation = None
        if rental:
            lead_result = _lead_flow.continue_turn(ctx, session_id, message, history, history_summary)
            if lead_result and lead_result.get("handoff"):
                forced_escalation = "Prospect asked for a person or raised a concern during the rental intake."
            elif lead_result:
                return finish(lead_result)
            elif (post := _lead_flow.post_intake(ctx, session_id, message)):
                return finish(post)
            elif detect_accommodation(message):
                # Never let the general assistant quote pet rules at an assistance-animal request.
                lead_result = _lead_flow.start(ctx, session_id, message, history, history_summary)
                if lead_result:
                    return finish(lead_result)

        # ── HIGH-RISK GATE (fixed patterns, no model) ────────────────────────
        # "Let me talk to a person" and legal threats go to a person even if
        # the model is down or would have read them differently.
        gate_source = "intake" if forced_escalation else ""
        if not forced_escalation:
            forced_escalation = _esc.high_risk(message)
            gate_source = "gate" if forced_escalation else ""

        # ── TRIAGE ───────────────────────────────────────────────────────────
        if forced_escalation:
            triage_result = {"intent": "leasing" if rental else "general", "route": "escalation",
                             "escalation_reason": forced_escalation, "error": False}
        else:
            triage_result = _triage_mod.triage_agent.run({
                "message": message,
                "history_summary": history_summary,
                "rental": rental,
            })

        intent = triage_result["intent"]
        route = triage_result["route"]
        escalation_reason = triage_result.get("escalation_reason")

        # Current residents get the owner's office contact (and a 911 line in an
        # emergency) added to whatever the assistant says. They never start the
        # leasing intake and never become leads.
        if rental and intent == "resident":
            extra_note = _lead_flow.resident_note(ctx, message)

        if rental and route != "escalation" and (
            intent == "leasing"
            or (triage_result.get("error") and looks_like_leasing(message))
        ):
            lead_result = _lead_flow.start(ctx, session_id, message, history, history_summary)
            if lead_result:
                return finish(lead_result)
        if intent == "leasing" and not rental:
            intent = "product"

        logger.info(
            f"Orchestrator: session={session_id[:8]} "
            f"intent={intent} route={route}"
        )

        # ── ESCALATION — triage-directed ─────────────────────────────────────
        if route == "escalation":
            esc_result = _escalation_mod.escalation_agent.run({
                "message": message,
                "intent": intent,
                "history_summary": history_summary,
                "escalation_reason": escalation_reason or "Triage directed escalation.",
            })
            reply = (esc_result.get("message_to_user") or "").strip() or t("esc_person_needed", lang)
            # The model may only talk a chat back down ("deflect") when nothing
            # forced the escalation and it didn't fail. Otherwise a person is told.
            needs_person = bool(
                gate_source or esc_result.get("error") or esc_result.get("alert_client")
                or esc_result.get("action") != "deflect"
            )
            if needs_person:
                reply = escalate(
                    reply,
                    reason=escalation_reason or esc_result.get("reason") or "Flagged for a person.",
                    source=gate_source or "triage",
                    action=esc_result.get("action") or "collect_contact",
                )
            return finish({
                "response": reply,
                "intent": intent,
                "session_id": session_id,
                "route": "escalation",
                "escalation_action": esc_result["action"],
                "alert_client": needs_person,
            })

        # ── RESOLVER ─────────────────────────────────────────────────────────
        res_result = _resolver_mod.resolver_agent.run({
            "message": message,
            "intent": intent,
            "history_summary": history_summary,
            "history": history,
            "client_id": resolved_client_id,
            "vertical": vertical,
            "bot_name": (ctx or {}).get("bot_name", ""),
            "business_name": (ctx or {}).get("business_name", ""),
            "properties": ((ctx or {}).get("config") or {}).get("properties") or [],
        })

        # ── ESCALATION — the assistant could not answer at all ───────────────
        # A model outage must not leave the visitor at "please try again": the
        # chat is handed to the team with fixed wording instead.
        if res_result.get("error") and ctx:
            reply = escalate(
                t("esc_trouble", lang),
                reason="The assistant had a technical problem and could not answer, so the visitor was offered a follow-up.",
                source="error",
            )
            return finish({
                "response": reply,
                "intent": intent,
                "session_id": session_id,
                "route": "escalation",
                "escalation_action": "collect_contact",
                "alert_client": True,
            })

        # ── ESCALATION — resolver-directed ───────────────────────────────────
        # The assistant said it needs a person. That always reaches the owner;
        # no second model call gets to decide otherwise.
        if res_result.get("escalation_flag"):
            reply = escalate(
                res_result["response_text"],
                reason="The assistant could not resolve this without a person.",
                source="resolver",
            )
            return finish({
                "response": reply,
                "intent": intent,
                "session_id": session_id,
                "route": "resolver_escalated",
                "escalation_action": "collect_contact",
                "alert_client": True,
            })

        return finish({
            "response": res_result["response_text"],
            "intent": intent,
            "session_id": session_id,
            "route": "resolver",
        })


orchestrator = Orchestrator()
