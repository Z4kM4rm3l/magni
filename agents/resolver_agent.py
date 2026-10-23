import os
from core import llm
from agents import BaseAgent
from core.support_flows import get_flow_context, RENTAL_RULES
from core.leasing import RENTAL_VERTICALS
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
- For facts specific to this business (prices, fees, hours, availability, policies,
  addresses), use ONLY the knowledge base. If it isn't there, say you're not sure
  and that the team can confirm. Never guess these.

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
        # history is a list of {role, content} dicts for the chat context
        history = payload.get("history", [])
        client_id = payload.get("client_id")
        vertical = payload.get("vertical") or "general"
        extra_instructions = payload.get("extra_instructions") or ""

        try:
            flow_context = get_flow_context(intent)
            kb_context = get_kb_context(client_id, message) if client_id else ""

            full_system = f"{MAGNI_SYSTEM_PROMPT}\n\n{flow_context}"
            if vertical in RENTAL_VERTICALS:
                full_system += f"\n\n{RENTAL_RULES}"
            if extra_instructions:
                full_system += f"\n\n{extra_instructions}"
            if kb_context:
                full_system += f"\n\n{kb_context}"
                logger.info(f"KB context injected for: {message[:40]}")

            # Stable part first (cacheable across turns), per-turn summary second.
            system_parts = [full_system]
            if history_summary and history_summary != "No prior context.":
                system_parts.append(f"Conversation summary so far:\n{history_summary}")

            response_text = llm.chat(system_parts, history[-10:], message, thinking="low")
            if not response_text:
                raise RuntimeError("empty model response")

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
