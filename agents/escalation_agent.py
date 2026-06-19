import os
import google.generativeai as genai
import json
from agents import BaseAgent
from core.utils import logger

ESCALATION_PROMPT = """You are an escalation handler for a customer service AI. A conversation has been flagged for escalation.

Your job is to de-escalate where possible and determine the right action.

Actions:
- "collect_contact": Offer to have a human follow up — collect name/email/description
- "human_handoff": Situation requires immediate human attention (legal threat, safety, payment dispute)
- "deflect": Issue can still be handled by AI with a more careful response

Respond with ONLY valid JSON:
{
  "action": "collect_contact" | "human_handoff" | "deflect",
  "message_to_user": "<warm, professional message to send to the user>",
  "alert_client": true | false,
  "reason": "<internal reason for this routing>"
}

alert_client should be true for legal threats, safety concerns, or payment disputes over any amount."""


class EscalationAgent(BaseAgent):
    def run(self, payload: dict) -> dict:
        message = payload.get("message", "")
        history_summary = payload.get("history_summary", "No prior context.")
        escalation_reason = payload.get("escalation_reason", "Flagged for escalation.")
        intent = payload.get("intent", "general")

        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        model = genai.GenerativeModel(
            model_name="gemini-2.5-flash",
            system_instruction=ESCALATION_PROMPT
        )

        prompt = (
            f"Intent: {intent}\n"
            f"Escalation reason: {escalation_reason}\n"
            f"History summary:\n{history_summary}\n\n"
            f"Current message: {message}"
        )

        try:
            response = model.generate_content(prompt)
            text = response.text.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()

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
