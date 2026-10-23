import os
import json
from core import llm
from agents import BaseAgent
from core.utils import logger

TRIAGE_PROMPT = """You are a triage classifier for a rental business's website chat. Your only job is to analyze a visitor message and output a routing decision as JSON.

Intents: leasing, resident, general

"leasing" = a PROSPECT interested in renting or booking: availability, pricing of a
rental, touring/showings, applying, move-in, reserving or booking a stay.
"resident" = a CURRENT resident or guest about their own lease or stay: paying rent,
maintenance or repairs, move-out, deposits back, a problem in the home.
"general" = anything else (business hours, contact info, or unrelated to rentals).

Routes:
  - "resolver" for questions the assistant can answer
  - "escalation" for situations needing a person or sensitive handling

Escalate when: the visitor asks for a person, is angry or threatening, mentions legal
action or a discrimination complaint, or reports a safety emergency.

Respond with ONLY valid JSON in this exact shape:
{
  "intent": "<intent>",
  "confidence": <0.0-1.0>,
  "route": "resolver" | "escalation",
  "escalation_reason": "<brief reason or null>"
}"""


INTENTS = ("leasing", "resident", "general")


class TriageAgent(BaseAgent):
    def run(self, payload: dict) -> dict:
        message = payload.get("message", "")
        history_summary = payload.get("history_summary", "No prior context.")

        prompt = f"History summary:\n{history_summary}\n\nCurrent message: {message}"

        try:
            text = llm.generate(prompt, TRIAGE_PROMPT, fast=True, thinking="minimal", json_mode=True)
            text = llm.strip_fences(text)

            result = json.loads(text)

            # Validate required keys; fall back gracefully
            intent = result.get("intent", "general")
            confidence = float(result.get("confidence", 0.7))
            route = result.get("route", "resolver")
            escalation_reason = result.get("escalation_reason", None)

            if intent not in INTENTS:
                intent = "general"
            if route not in ("resolver", "escalation"):
                route = "resolver"

            logger.info(f"Triage: intent={intent} route={route} confidence={confidence:.2f}")
            return {
                "intent": intent,
                "confidence": confidence,
                "route": route,
                "escalation_reason": escalation_reason,
                "error": False,
            }

        except Exception as e:
            logger.error(f"TriageAgent error: {e}")
            return {
                "intent": "general",
                "confidence": 0.5,
                "route": "resolver",
                "escalation_reason": None,
                "error": True,
            }


triage_agent = TriageAgent()
