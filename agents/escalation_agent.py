import os
from core import llm
import json
from agents import BaseAgent
from core.utils import logger

ESCALATION_PROMPT = """You are the escalation handler for a rental business's website chat. A conversation has been flagged because it needs a person or careful handling.

Decide the right action:
- "collect_contact": offer to have the leasing or office team follow up, and ask for
  name and phone or email
- "human_handoff": needs prompt human attention (legal threat, discrimination or Fair
  Housing complaint, safety emergency, an angry resident)
- "deflect": the assistant can still help with a careful answer

Never promise outcomes (approvals, refunds, repairs, exceptions). For a safety emergency
(fire, gas, flooding), tell the visitor to call 911 first.

Respond with ONLY valid JSON:
{
  "action": "collect_contact" | "human_handoff" | "deflect",
  "message_to_user": "<warm, professional message to send to the visitor>",
  "alert_client": true | false,
  "reason": "<internal reason for this routing>"
}

alert_client should be true when the visitor asks for a person, raises an accommodation
or disability request, makes a complaint (including discrimination), mentions legal
action, or reports a safety issue."""


class EscalationAgent(BaseAgent):
    def run(self, payload: dict) -> dict:
        message = payload.get("message", "")
        history_summary = payload.get("history_summary", "No prior context.")
        escalation_reason = payload.get("escalation_reason", "Flagged for escalation.")
        intent = payload.get("intent", "general")

        prompt = (
            f"Intent: {intent}\n"
            f"Escalation reason: {escalation_reason}\n"
            f"History summary:\n{history_summary}\n\n"
            f"Current message: {message}"
        )

        try:
            text = llm.generate(prompt, ESCALATION_PROMPT, fast=False, thinking="low", json_mode=True)
            text = llm.strip_fences(text)

            result = json.loads(text)

            action = result.get("action", "collect_contact")
            if action not in ("collect_contact", "human_handoff", "deflect"):
                action = "collect_contact"

            message_to_user = result.get(
                "message_to_user",
                "I'd like to make sure you get the right help. Could you share your name and email so our team can follow up with you directly?"
            )
            alert_client = bool(result.get("alert_client", False))
            reason = result.get("reason", "")

            logger.info(f"Escalation: action={action} alert_client={alert_client} reason={reason}")
            return {
                "action": action,
                "message_to_user": message_to_user,
                "alert_client": alert_client,
                "reason": reason,
            }

        except Exception as e:
            logger.error(f"EscalationAgent error: {e}")
            return {
                "action": "collect_contact",
                "message_to_user": (
                    "I want to make sure you get the best support possible. "
                    "Could you share your contact details so a member of our team can reach out to you directly?"
                ),
                "alert_client": False,
                "reason": "escalation_agent_error",
            }


escalation_agent = EscalationAgent()
