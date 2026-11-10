"""
core/i18n.py

English and Spanish for the assistant's fixed (non-AI) replies: the lines it
uses when a chat is handed to a person. Free-form answers are written by the
model in the visitor's own language; these are the replies that must read the
same way every time.

detect_lang() decides the language from the visitor's own words. A handed-off
chat keeps the language it was flagged in (Escalation.lang), so a visitor never
gets a mix. Owner-facing text (alerts, the portal) stays in English.
"""
import re

LANGS = ("en", "es")

# Common Spanish words in a customer's messages. Two hits, or any Spanish-only
# punctuation or letter, mark a message as Spanish.
_ES_WORDS = re.compile(
    r"\b(?:hola|buenas|buenos|gracias|quiero|quisiera|busco|buscando|necesito|tiene|tienen|hay|"
    r"disponible|disponibles|pedido|orden|compra|env[ií]o|entrega|reembolso|devoluci[oó]n|factura|cuenta|"
    r"cu[aá]nto|cu[aá]ndo|d[oó]nde|cu[aá]l|precio|horario|ayuda|problema|cita|"
    r"por favor|se puede|puedo|podr[ií]a|me interesa|estoy|soy|tengo|tenemos|somos|"
    r"para|con|una|los|las|del|que|el|es|y|de|mi)\b", re.I)
_ES_STRONG = re.compile(r"[¿¡ñ]|\b(?:hola|gracias|quiero|quisiera|busco|necesito|disponible|pedido|"
                        r"reembolso|devoluci[oó]n|env[ií]o|cu[aá]nto|ayuda)\b", re.I)
_EN_WORDS = re.compile(r"\b(?:the|is|are|you|your|i|i'm|we|what|when|how|do|does|can|available|"
                       r"order|refund|help|please|thanks|hi|hello|my)\b", re.I)


def detect_lang(text: str) -> str | None:
    """'es' or 'en' when the message is clearly one of them, else None
    (numbers, names, "ok"), so a short reply never flips the language."""
    t = text or ""
    es = len(_ES_WORDS.findall(t))
    en = len(_EN_WORDS.findall(t))
    if _ES_STRONG.search(t) and es >= en:
        return "es"
    if es >= 2 and es > en:
        return "es"
    if en >= 1 and en >= es:
        return "en"
    return None


STRINGS = {
    # ── Chats that need a person ────────────────────────────────────────────
    "esc_ask_contact": {
        "en": "So the right person can follow up with you, what's your name and the best email or phone number to reach you?",
        "es": "Para que la persona indicada pueda comunicarse con usted, ¿cuál es su nombre y el mejor correo electrónico o número de teléfono para contactarle?",
    },
    "esc_ask_reach": {
        "en": "Thanks, {name}. What's the best email or phone number to reach you?",
        "es": "Gracias, {name}. ¿Cuál es el mejor correo electrónico o número de teléfono para contactarle?",
    },
    "esc_thanks": {
        "en": "Thank you{name}. I've passed this along to the team, and someone will follow up with you at {contact}.",
        "es": "Gracias{name}. Le pasé esto al equipo y alguien se comunicará con usted al {contact}.",
    },
    "esc_confirm_contact": {
        "en": "I have {contact} for you. Is that the best way for the team to reach you? If not, just share the email or phone number you'd prefer.",
        "es": "Tengo {contact} como su contacto. ¿Es la mejor forma de comunicarnos con usted? Si no, comparta el correo electrónico o número de teléfono que prefiera.",
    },
    "esc_confirmed": {
        "en": "Thank you. I've passed this along to the team, and someone will follow up with you at {contact}.",
        "es": "Gracias. Le pasé esto al equipo y alguien se comunicará con usted al {contact}.",
    },
    "esc_ask_best": {
        "en": "No problem. What's the best email or phone number to reach you?",
        "es": "No hay problema. ¿Cuál es el mejor correo electrónico o número de teléfono para contactarle?",
    },
    "esc_notified": {
        "en": "I've passed this along to the team with your contact details, and someone will follow up with you.",
        "es": "Le pasé esto al equipo con sus datos de contacto y alguien se comunicará con usted.",
    },
    "esc_noted": {
        "en": "I've let the team know about this conversation.",
        "es": "Le avisé al equipo sobre esta conversación.",
    },
    "esc_no_contact": {
        "en": "No problem. I've let the team know about this conversation. Is there anything else I can help with?",
        "es": "No hay problema. Le avisé al equipo sobre esta conversación. ¿Hay algo más en lo que pueda ayudarle?",
    },
    "esc_reminder": {
        "en": "If you'd like the team to follow up with you, just share an email or phone number here.",
        "es": "Si desea que el equipo se comunique con usted, comparta aquí un correo electrónico o número de teléfono.",
    },
    "esc_person_needed": {
        "en": "I want to make sure you get the right help, so I'm passing this to the team.",
        "es": "Quiero asegurarme de que reciba la ayuda adecuada, así que le paso esto al equipo.",
    },
    # ── Guard (core/guard.py) ──────────────────────────────────────────────
    "guard_refusal": {
        "en": "I can't help with that, but I'm happy to answer questions about {business}. What can I help you with?",
        "es": "No puedo ayudar con eso, pero con gusto respondo preguntas sobre {business}. ¿En qué puedo ayudarle?",
    },
    "guard_fallback": {
        "en": "I'm not able to help with that here. I can answer questions about {business}, or pass your request to the team if you'd like.",
        "es": "No puedo ayudar con eso aquí. Puedo responder preguntas sobre {business}, o pasar su solicitud al equipo si lo desea.",
    },
    "esc_trouble": {
        "en": "I'm sorry, I'm having trouble answering right now, so I'm passing this to the team.",
        "es": "Lo siento, tengo problemas para responder en este momento, así que le paso esto al equipo.",
    },
}


def t(key: str, lang: str | None = "en", **kw) -> str:
    entry = STRINGS[key]
    text = entry.get(lang or "en") or entry["en"]
    return text.format(**kw) if kw and isinstance(text, str) else text
