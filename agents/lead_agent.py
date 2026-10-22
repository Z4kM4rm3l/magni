"""
agents/lead_agent.py

Reads one prospect message during the leasing intake and pulls out answers to
the intake questions. It does NOT decide what to ask or say; core/lead_flow.py
does that deterministically so every prospect gets identical questions.
"""
import os
import json
import google.generativeai as genai
from agents import BaseAgent
from core.leasing import QUESTION_CATALOG
from core.utils import logger

EXTRACT_PROMPT = """You extract answers from a rental prospect's chat message.

You are given the question the prospect was just asked (PENDING), the other
intake questions still open (OPEN), and their message. Return ONLY JSON:
{
  "answers": {"<question_key>": "<short answer in the prospect's own words>"},
  "asked_question": true | false,
  "needs_human": true | false,
  "declined": true | false
}

Rules:
- Only use keys from PENDING or OPEN. Omit any question the message does not answer.
- Copy facts; never guess, infer, or fill in missing details.
- Never record or describe race, color, religion, national origin, sex, disability,
  or familial status (children/pregnancy), even if the prospect mentions them.
  For "occupants" or "guests", record only the number of people.
- asked_question: true if the message also asks the business something
  (availability, price, pets, parking, policies...).
- needs_human: true if the prospect is angry, threatens legal action, reports a
  safety issue, or asks for a person.
- declined: true ONLY if the prospect clearly says they don't want to continue
  answering questions. A "no" or "none" that answers the PENDING question is an
  answer (record it), not declined.
- A phone number or email that is not literally in the message must not be recorded."""


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return text.strip()


class LeadExtractor(BaseAgent):
    def run(self, payload: dict) -> dict:
        pending = payload.get("pending_key")
        open_keys = [k for k in payload.get("open_keys", []) if k != pending]
        message = payload.get("message", "")
        empty = {"answers": {}, "asked_question": False, "needs_human": False,
                 "declined": False, "error": False}

        def describe(keys):
            return "\n".join(f"- {k}: {QUESTION_CATALOG[k]['label']}" for k in keys if k in QUESTION_CATALOG) or "(none)"

        prompt = (
            f"PENDING:\n{describe([pending] if pending else [])}\n\n"
            f"OPEN:\n{describe(open_keys)}\n\n"
            f"Prospect message: {message}"
        )
        try:
            genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
            model = genai.GenerativeModel(model_name="gemini-2.5-flash", system_instruction=EXTRACT_PROMPT)
            result = json.loads(_strip_fences(model.generate_content(prompt).text))
            allowed = set(open_keys) | ({pending} if pending else set())
            answers = {}
            for k, v in (result.get("answers") or {}).items():
                if k in allowed and v not in (None, "", [], {}):
                    answers[k] = str(v).strip()[:200]
            return {
                "answers": answers,
                "asked_question": bool(result.get("asked_question")),
                "needs_human": bool(result.get("needs_human")),
                "declined": bool(result.get("declined")),
                "error": False,
            }
        except Exception as e:
            logger.error(f"LeadExtractor error: {e}")
            return {**empty, "error": True}


lead_extractor = LeadExtractor()
