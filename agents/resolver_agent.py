import os
import google.generativeai as genai
from agents import BaseAgent
from core.support_flows import get_flow_context
from core.knowledge_base import get_kb_context
from core.utils import logger

MAGNI_SYSTEM_PROMPT = """You are Magni, a warm, professional, and highly capable AI customer service agent.

Your personality:
- Human-forward: You lead with empathy before solutions
- Clear and concise: You avoid jargon and explain things simply
- Proactive: You anticipate follow-up questions
- Honest: You never make up information or pretend to access systems you cannot
- Warm but professional: Friendly without being overly casual

Your bounds:
- Never claim to be human if directly asked
- Never make up account details, order numbers, or policies
- If you don't know something, say so and offer to escalate
- Keep responses focused and under 150 words unless detail is truly needed
- When knowledge base information is provided, prioritize it over general knowledge

Always end responses with a clear next step or offer to help further."""

# Phrases that signal the resolver itself has determined escalation is needed
_ESCALATION_SIGNALS = [
    "i'll escalate", "i will escalate", "escalating this",
    "connect you with a", "transfer you to", "human agent",
    "speak to a specialist", "pass this to our team",
]


class ResolverAgent(BaseAgent):
    def run(self, payload: dict) -> dict:
        message = payload.get("message", "")
        intent = payload.get("intent", "general")
        history_summary = payload.get("history_summary", "No prior context.")
        # history is a list of {role, content} dicts for Gemini chat context
        history = payload.get("history", [])

        # Configure Gemini here, not at module import, to avoid import-time side effects
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

        try:
            flow_context = get_flow_context(intent)
            kb_context = get_kb_context(message)

            full_system = f"{MAGNI_SYSTEM_PROMPT}\n\n{flow_context}"
            if kb_context:
                full_system += f"\n\n{kb_context}"
                logger.info(f"KB context injected for: {message[:40]}")

            if history_summary and history_summary != "No prior context.":
                full_system += f"\n\nConversation summary so far:\n{history_summary}"

            model = genai.GenerativeModel(
                model_name="gemini-2.5-flash",
                system_instruction=full_system
            )

            gemini_history = []
            for msg in history[-10:]:
                role = "user" if msg["role"] == "user" else "model"
                gemini_history.append({"role": role, "parts": [msg["content"]]})

            chat = model.start_chat(history=gemini_history)
            response = chat.send_message(message)
            response_text = response.text

            escalation_flag = any(
                sig in response_text.lower() for sig in _ESCALATION_SIGNALS
            )

            logger.info(f"Resolver: intent={intent} escalation_flag={escalation_flag}")
            return {
                "response_text": response_text,
                "escalation_flag": escalation_flag,
                "confidence": 0.9,
            }

        except Exception as e:
            logger.error(f"ResolverAgent error: {e}")
            return {
                "response_text": (
                    "I apologize, I'm having trouble connecting right now. "
                    "Please try again in a moment, or contact our support team directly."
                ),
                "escalation_flag": False,
                "confidence": 0.0,
            }


resolver_agent = ResolverAgent()
