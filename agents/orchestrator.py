import os
from core.db import SessionLocal
from core.client_guard import track_and_validate_request
from core.emailer import send_escalation_alert
from core.utils import logger
import agents.triage_agent as _triage_mod
import agents.resolver_agent as _resolver_mod
import agents.escalation_agent as _escalation_mod


def _summarize_history(history: list) -> str:
    if not history:
        return "No prior context."
    recent = history[-6:]  # last 3 exchanges
    return "\n".join(
        f"{m['role'].upper()}: {m['content'][:100]}" for m in recent
    )


def _maybe_alert(esc_result: dict, client_email: str, client_name: str,
                 history: list, session_id: str) -> None:
    """Fire escalation email if alert_client is set. Best-effort — never raises."""
    if esc_result.get("alert_client") and client_email:
        send_escalation_alert(
            to_email=client_email,
            business_name=client_name,
            escalation_action=esc_result.get("action", ""),
            escalation_reason=esc_result.get("reason", ""),
            history=history,
            session_id=session_id,
        )


class Orchestrator:
    def run(
        self,
        message: str,
        session_id: str,
        history: list,
        api_key: str | None = None,
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

        # ── PAYING CLIENT BILLING GATE ───────────────────────────────────────
        if api_key:
            db = SessionLocal()
            try:
                allowed, reason, client = track_and_validate_request(db, api_key)
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

        # ── TRIAGE ───────────────────────────────────────────────────────────
        triage_result = _triage_mod.triage_agent.run({
            "message": message,
            "history_summary": history_summary,
        })

        intent = triage_result["intent"]
        route = triage_result["route"]
        escalation_reason = triage_result.get("escalation_reason")

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
            _maybe_alert(esc_result, client_email, client_name, history, session_id)
            return {
                "response": esc_result["message_to_user"],
                "intent": intent,
                "session_id": session_id,
                "route": "escalation",
                "escalation_action": esc_result["action"],
                "alert_client": esc_result["alert_client"],
            }

        # ── RESOLVER ─────────────────────────────────────────────────────────
        res_result = _resolver_mod.resolver_agent.run({
            "message": message,
            "intent": intent,
            "history_summary": history_summary,
            "history": history,
        })

        # ── ESCALATION — resolver-directed ───────────────────────────────────
        if res_result.get("escalation_flag"):
            esc_result = _escalation_mod.escalation_agent.run({
                "message": message,
                "intent": intent,
                "history_summary": history_summary,
                "escalation_reason": "Resolver determined it could not resolve this without human help.",
            })
            _maybe_alert(esc_result, client_email, client_name, history, session_id)
            return {
                "response": res_result["response_text"],
                "intent": intent,
                "session_id": session_id,
                "route": "resolver_escalated",
                "escalation_action": esc_result["action"],
                "alert_client": esc_result["alert_client"],
            }

        return {
            "response": res_result["response_text"],
            "intent": intent,
            "session_id": session_id,
            "route": "resolver",
        }


orchestrator = Orchestrator()
