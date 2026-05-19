import os
import google.generativeai as genai
from dotenv import load_dotenv
from core.support_flows import get_flow_context
from core.utils import logger

load_dotenv()

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

MAGNI_SYSTEM_PROMPT = """You are Magni, a warm, professional, and highly capable AI customer service agent.

Your personality:
- Human-forward: You lead with empathy before solutions
- Clear and concise: You avoid jargon and explain things simply
- Proactive: You anticipate follow-up questions
- Honest: You never make up information or pretend to access systems you cannot
- Warm but professional: Friendly without being overly casual

Your boundaries:
- Never claim to be human if directly asked
- Never make up account details, order numbers, or policies
- If you don't know something, say so and offer to escalate
- Keep responses focused and under 150 words unless detail is truly needed

Always end responses with a clear next step or offer to help further."""

def get_magni_response(message: str, history: list, intent: str) -> str:
    try:
        flow_context = get_flow_context(intent)

        full_system = f"{MAGNI_SYSTEM_PROMPT}\n\n{flow_context}"

        model = genai.GenerativeModel(
            model_name="gemini-2.5-flash",
            system_instruction=full_system
        )

        gemini_history = []
        for msg in history[-10:]:
            role = "user" if msg["role"] == "user" else "model"
            gemini_history.append({
                "role": role,
                "parts": [msg["content"]]
            })

        chat = model.start_chat(history=gemini_history)
        response = chat.send_message(message)

        return response.text

    except Exception as e:
        logger.error(f"Gemini API error: {e}")
        return "I apologize, I'm having trouble connecting right now. Please try again in a moment, or contact our support team directly."
