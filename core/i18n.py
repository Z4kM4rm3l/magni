"""
core/i18n.py

English and Spanish for the assistant's fixed (non-AI) replies. The intake's
questions live in core/leasing.py (QUESTION_CATALOG "text" / "text_es"); this
file holds everything else the intake and booking steps say.

A conversation's language is decided once, from the prospect's own words
(detect_lang), and then kept for the whole intake (lead.flags["lang"]), so a
prospect never gets a mix of languages and every Spanish speaker gets the same
fixed wording. Owner-facing text (alerts, the portal) stays in English.
"""
import re

LANGS = ("en", "es")

# Common Spanish words in a renter's first messages. Two hits, or any
# Spanish-only punctuation or letter, mark a message as Spanish.
_ES_WORDS = re.compile(
    r"\b(?:hola|buenas|buenos|gracias|quiero|quisiera|busco|buscando|necesito|tiene|tienen|hay|"
    r"disponible|disponibles|apartamento|apartamentos|departamento|casa|renta|rentar|alquiler|alquilar|"
    r"cu[aá]nto|cu[aá]ndo|d[oó]nde|cu[aá]l|precio|mudarme|mudanza|visita|visitar|cita|"
    r"habitaciones|rec[aá]maras|cuartos|mascota|mascotas|perro|gato|por favor|se puede|puedo|"
    r"podr[ií]a|me interesa|estoy|soy|tengo|tenemos|somos|para|con|una|los|las|del|que|el|es|y|de)\b", re.I)
_ES_STRONG = re.compile(r"[¿¡ñ]|\b(?:hola|gracias|quiero|quisiera|busco|disponible|apartamento|"
                        r"alquiler|rentar|mudarme|cu[aá]nto|habitaciones|rec[aá]maras|mascotas?)\b", re.I)
_EN_WORDS = re.compile(r"\b(?:the|is|are|you|your|i|i'm|we|what|when|how|do|does|can|available|"
                       r"apartment|rent|tour|please|thanks|hi|hello)\b", re.I)


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
    "acks": {
        "en": ("Thanks!", "Got it.", "Great, thank you."),
        "es": ("¡Gracias!", "Entendido.", "Perfecto, gracias."),
    },
    "opt_out": {
        "en": "No problem at all. If you change your mind, just let me know. Is there anything else I can help with?",
        "es": "No hay problema. Si cambia de opinión, avíseme. ¿Hay algo más en lo que pueda ayudarle?",
    },
    "reask": {
        "en": "Sorry, I didn't quite catch that. ",
        "es": "Disculpe, no entendí bien. ",
    },
    "keep_going": {
        "en": "No worries, let's keep going.",
        "es": "No se preocupe, sigamos.",
    },
    "intro": {
        "en": "I can pass your details to our {team} so they can follow up.",
        "es": "Puedo pasar sus datos a nuestro {team} para que le den seguimiento.",
    },
    "team_long_term": {"en": "leasing team", "es": "equipo de arrendamiento"},
    "team_seasonal": {"en": "reservations team", "es": "equipo de reservaciones"},
    "closing_contact": {
        "en": "Thank you! I've sent your details to our {team}, and someone will reach out soon.",
        "es": "¡Gracias! Envié sus datos a nuestro {team} y alguien se comunicará con usted pronto.",
    },
    "closing_no_contact": {
        "en": "Thanks! Without a phone number or email, our {team} won't be able to reach you, but you're welcome to take the next step anytime.",
        "es": "¡Gracias! Sin un teléfono o correo electrónico, nuestro {team} no podrá contactarle, pero puede dar el siguiente paso cuando guste.",
    },
    "closing_apply_long_term": {
        "en": "If you'd like to start an application now: {url}",
        "es": "Si desea iniciar una solicitud ahora: {url}",
    },
    "closing_apply_seasonal": {
        "en": "If you'd like to book or request a stay now: {url}",
        "es": "Si desea reservar o solicitar una estadía ahora: {url}",
    },
    "closing_booking_link": {
        "en": "To pick a time that works for you: {url}",
        "es": "Para elegir un horario que le convenga: {url}",
    },
    "anything_else": {
        "en": "Is there anything else I can help with in the meantime?",
        "es": "¿Hay algo más en lo que pueda ayudarle mientras tanto?",
    },
    "anything_else_short": {
        "en": "Is there anything else I can help with?",
        "es": "¿Hay algo más en lo que pueda ayudarle?",
    },
    "accommodation": {
        "en": ("Thank you for letting me know. Assistance animals are not treated as pets, and "
               "requests like this are handled personally by our team, so I've flagged it for "
               "them to follow up with you directly."),
        "es": ("Gracias por avisarme. Los animales de asistencia no se tratan como mascotas, y "
               "nuestro equipo atiende personalmente este tipo de solicitudes, así que lo marqué "
               "para que le den seguimiento directamente."),
    },
    "safe_fallback": {
        "en": ("I'm not able to describe who a home or area would suit, but I'm happy to share "
               "details about the property itself, like size, amenities, pricing, and policies. "
               "Our leasing team can also answer anything else you'd like to know."),
        "es": ("No puedo describir para quién sería adecuada una vivienda o una zona, pero con gusto "
               "le comparto detalles de la propiedad, como tamaño, amenidades, precios y políticas. "
               "Nuestro equipo también puede responder cualquier otra pregunta."),
    },
    # ── Booking ──
    "kind_tour": {"en": "tour", "es": "visita"},
    "kind_call": {"en": "call", "es": "llamada"},
    "offer_lead_in": {
        "en": "Here are the next open times for a {kind}:",
        "es": "Estos son los próximos horarios disponibles para una {kind}:",
    },
    "offer_footer": {
        "en": "Reply with a number, or tell me a day and time that works better. (Times are {tz}.)",
        "es": "Responda con un número, o dígame un día y una hora que le convengan más. (Horario {tz}.)",
    },
    "offer_again": {
        "en": "No problem. Here are the open times again:",
        "es": "No hay problema. Estos son los horarios disponibles de nuevo:",
    },
    "offer_taken": {
        "en": "Sorry, that time was just taken. Here's what's still open:",
        "es": "Lo siento, ese horario se acaba de ocupar. Esto es lo que sigue disponible:",
    },
    "offer_choices": {"en": "Here's what's open:", "es": "Esto es lo que hay disponible:"},
    "offer_none_on": {
        "en": "Nothing's open {when}. Here are the next open times:",
        "es": "No hay horarios disponibles {when}. Estos son los próximos:",
    },
    "when_then": {"en": "then", "es": "en ese horario"},
    "when_on": {"en": "on {day}", "es": "el {day}"},
    "offer_sure": {
        "en": "Sure! Here are the next open times for a {kind}:",
        "es": "¡Claro! Estos son los próximos horarios disponibles para una {kind}:",
    },
    "confirm_slot": {
        "en": "Want me to book a {kind} on {when}? (yes / no)",
        "es": "¿Quiere que reserve una {kind} el {when}? (sí / no)",
    },
    "booked": {
        "en": "You're booked for a {kind} on {when}.",
        "es": "Su {kind} quedó reservada para el {when}.",
    },
    "booked_email": {
        "en": "A confirmation is on its way to {email}.",
        "es": "Le enviamos una confirmación a {email}.",
    },
    "booked_cancel": {"en": "Need to cancel? {url}", "es": "¿Necesita cancelar? {url}"},
    "decline_booking": {
        "en": "No problem, our team can set up a time with you.",
        "es": "No hay problema, nuestro equipo puede acordar un horario con usted.",
    },
    "none_work": {
        "en": "No problem. Tell me a day and time that would work (like \"Thursday afternoon\" or \"Saturday at 11am\") and I'll check.",
        "es": "No hay problema. Dígame un día y una hora que le funcionen (por ejemplo, \"jueves por la tarde\" o \"sábado a las 11\") y lo reviso.",
    },
    "booking_moved_on": {
        "en": "No problem, I'll pass that along and our team will confirm a time with you.",
        "es": "No hay problema, le pasaré el mensaje a nuestro equipo y le confirmarán un horario.",
    },
    "booking_reask": {
        "en": "Reply with a number from the list, or tell me a day and time that works (like \"Saturday at 11am\").",
        "es": "Responda con un número de la lista, o dígame un día y una hora que le convengan (por ejemplo, \"sábado a las 11\").",
    },
    "calendar_full": {
        "en": "Our calendar is full online right now, so our team will reach out to find a time.",
        "es": "Nuestro calendario en línea está lleno por ahora, así que nuestro equipo le contactará para buscar un horario.",
    },
    "change_booking": {
        "en": "You're booked for {when}. You can cancel here: {url} and then I can help you pick a new time.",
        "es": "Tiene una cita el {when}. Puede cancelarla aquí: {url} y luego le ayudo a elegir otro horario.",
    },
    "no_problem": {"en": "No problem.", "es": "No hay problema."},
    # ── Residents ──
    "resident_contact": {
        "en": "For your current home or lease, here's how to reach the office: {contact}",
        "es": "Para su vivienda o contrato actual, así puede comunicarse con la oficina: {contact}",
    },
    "emergency": {
        "en": "If this is an emergency (fire, a gas smell, flooding), call 911 first.",
        "es": "Si es una emergencia (incendio, olor a gas, inundación), llame primero al 911.",
    },
}


def t(key: str, lang: str | None = "en", **kw) -> str:
    entry = STRINGS[key]
    text = entry.get(lang or "en") or entry["en"]
    return text.format(**kw) if kw and isinstance(text, str) else text


_EMERGENCY_RE = re.compile(
    r"\b(?:fire|smoke|gas (?:smell|leak)|smell(?:s)? (?:like )?gas|flood(?:ing|ed)?|"
    r"burst pipe|sparks?|no heat|carbon monoxide|emergency|"
    r"incendio|humo|(?:olor|huele) a gas|fuga de gas|inundaci[oó]n|inundad[oa]|emergencia)\b", re.I)


def is_emergency(text: str) -> bool:
    return bool(_EMERGENCY_RE.search(text or ""))


# Spanish names for slot formatting ("sáb, 25 oct a las 11:00 a. m.").
ES_DAYS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
ES_DAYS_LONG = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
ES_MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
