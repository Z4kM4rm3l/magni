import os
import json
import google.generativeai as genai
from agents import BaseAgent
from core.utils import logger

TRIAGE_PROMPT = """You are a triage classifier for a customer service AI. Your only job is to analyze a customer message and output a routing decision as JSON.

Intents: billing, technical, product, orders, feedback, general
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

        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        model = genai.GenerativeModel(
            model_name="gemini-2.5-flash",
            system_instruction=TRIAGE_PROMPT
        )

        prompt = f"History summary:\n{history_summary}\n\nCurrent message: {message}"

        try:
            response = model.generate_content(prompt)
            text = response.text.strip()
            # Strip markdown fences if model wraps output
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()

            result = json.loads(text)

            # Validate required keys; fall back gracefully
            intent = result.get("intent", "general")
            confidence = float(result.get("confidence", 0.7))
            route = result.get("route", "resolver")
            escalation_reason = result.get("escalation_reason", None)

            if intent not in ("billing", "technical", "product", "orders", "feedback", "general"):
                intent = "general"
            if route not in ("resolver", "escalation"):
                route = "resolver"

            logger.info(f"Triage: intent={intent} route={route} confidence={confidence:.2f}")
            return {
                "intent": intent,
                "confidence": confidence,
                "route": route,
                "escalation_reason": escalation_reason,
            }

        except Exception as e:
            logger.error(f"TriageAgent error: {e}")
            return {
                "intent": "general",
                "confidence": 0.5,
                "route": "resolver",
                "escalation_reason": None,
            }


triage_agent = TriageAgent()
