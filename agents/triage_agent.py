import os
import json
from core import llm
from agents import BaseAgent
from core.utils import logger

TRIAGE_PROMPT = """You are a triage classifier for a customer service AI. Your only job is to analyze a customer message and output a routing decision as JSON.

Intents: billing, technical, product, orders, feedback, leasing, general

"leasing" = a PROSPECT interested in renting or booking: availability, pricing of a
rental, touring/showings, applying, move-in, reserving or booking a stay. A current
resident asking about paying rent, maintenance, or their own lease is NOT leasing.
Routes:
  - "resolver" — standard questions the AI can answer
  - "escalation" — situations requiring human intervention or sensitive handling

Escalate when: customer is angry/threatening, mentions legal action, requests human agent explicitly, reports safety issue, or the issue is clearly unresolvable by AI alone.

Respond with ONLY valid JSON in this exact shape:
{
  "intent": "<intent>",
  "confidence": <0.0-1.0>,
  "route": "resolver" | "escalation",
  "escalation_reason": "<brief reason or null>"
}"""


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

            if intent not in ("billing", "technical", "product", "orders", "feedback", "leasing", "general"):
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
