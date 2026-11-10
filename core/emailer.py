"""
core/emailer.py

Owner alert for a chat that needs a person, sent through core.notifier (the
one place that talks to the SMTP provider). Best-effort: a failure here is
logged and returned, never raised, and never blocks a chat reply. The durable
record and the retries live in core/escalations.py.
"""
from datetime import datetime, timezone
from core.utils import logger

# The alert carries the whole recent chat, not a clipped excerpt, so the owner
# can follow up without opening anything else.
TRANSCRIPT_MAX_MESSAGES = 40
TRANSCRIPT_MAX_CHARS = 1500


def build_transcript(history: list, current_message: str | None = None) -> str:
    """history is [{"role": "user"|"assistant", "content": str}]. current_message
    is the visitor message being handled right now (not yet in history)."""
    msgs = list(history or [])
    if current_message:
        msgs.append({"role": "user", "content": current_message})
    if not msgs:
        return "(no conversation history available)"
    lines = []
    skipped = len(msgs) - TRANSCRIPT_MAX_MESSAGES
    if skipped > 0:
        lines.append(f"(... {skipped} earlier message(s) not shown)")
    for msg in msgs[-TRANSCRIPT_MAX_MESSAGES:]:
        role = "Visitor" if msg.get("role") == "user" else "Assistant"
        content = str(msg.get("content", ""))
        if len(content) > TRANSCRIPT_MAX_CHARS:
            content = content[:TRANSCRIPT_MAX_CHARS] + " [...]"
        lines.append(f"{role}: {content}")
    return "\n\n".join(lines)


def build_escalation_alert(business_name: str, *, reason: str = "", action: str = "",
                           session_id: str = "", transcript: str = "",
                           contact: dict | None = None, update: bool = False,
                           portal_link: str = "") -> tuple[str, str, str | None]:
    """(subject, body, reply_to). `update` marks the follow-up sent when the
    visitor shares contact details after the first alert already went out."""
    contact = contact or {}
    name, email, phone = contact.get("name") or "", contact.get("email") or "", contact.get("phone") or ""
    who = name or email or phone or "a website visitor"
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    # Same subject for the first alert and the update, so they thread together.
    subject = f"Needs a person: {business_name or 'your website chat'} ({(session_id or '')[:8]})"

    if email or phone:
        contact_block = "\n".join(x for x in (
            f"Name:       {name}" if name else "",
            f"Email:      {email}" if email else "",
            f"Phone:      {phone}" if phone else "",
        ) if x)
    else:
        contact_block = ("Contact:    not shared yet. The assistant asked for it; you'll get a\n"
                         "            second email if the visitor replies with it.")

    head = (f"{who} shared contact details for a chat that needs a person"
            if update else "A chat on your website needs a person")
    body = f"""{head}
{'=' * 40}

Business:   {business_name or 'Unknown'}
Time:       {timestamp}
Chat ID:    {(session_id or '')[:8]}
{contact_block}

Why:        {reason or 'Not specified'}
Suggested:  {_ACTION_LABELS.get(action, 'Follow up with this person')}

--- Conversation ---
{transcript or '(no conversation history available)'}
{'=' * 40}
"""
    if portal_link:
        body += f"\nSee every chat that needs a person: {portal_link}\n"
    body += "\nSent automatically by your website assistant."
    if email:
        body += " Reply to this email to write to the visitor directly."
    body += "\n"
    return subject, body, (email or None)


_ACTION_LABELS = {
    "human_handoff": "Prompt follow-up by a person",
    "collect_contact": "Follow up with this person",
}


def send_escalation_alert(
    to_email: str,
    business_name: str,
    escalation_action: str,
    escalation_reason: str,
    history: list,
    session_id: str,
    contact: dict | None = None,
    transcript: str | None = None,
    update: bool = False,
    portal_link: str = "",
) -> bool:
    """Send the alert to the business's alert address. Returns True if sent,
    False if skipped or failed. Errors are logged but never re-raised."""
    if not to_email:
        logger.warning(f"Escalation alert skipped: no email on file for session {(session_id or '')[:8]}.")
        return False
    subject, body, reply_to = build_escalation_alert(
        business_name, reason=escalation_reason, action=escalation_action, session_id=session_id,
        transcript=transcript if transcript is not None else build_transcript(history),
        contact=contact, update=update, portal_link=portal_link,
    )
    from core import notifier
    sent = notifier.send_email(to_email, subject, body, reply_to=reply_to)
    if sent:
        logger.info(f"Escalation alert sent for session {(session_id or '')[:8]}")
    return sent
