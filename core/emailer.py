"""
core/emailer.py

Best-effort escalation email notifications, sent through core.notifier (the
one place that talks to the SMTP provider). A failure here must never crash
the orchestrator or block a chat response.
"""
from datetime import datetime, timezone
from core.utils import logger


def send_escalation_alert(
    to_email: str,
    business_name: str,
    escalation_action: str,
    escalation_reason: str,
    history: list,
    session_id: str,
) -> bool:
    """
    Send an escalation alert to the client's registered email address.
    Returns True if sent, False if skipped or failed.
    Errors are logged but never re-raised.
    """
    if not to_email:
        logger.warning(f"Escalation alert skipped: no email on file for session {session_id[:8]}.")
        return False

    transcript = _build_transcript(history)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    body = f"""A chat needs a person
{'=' * 40}

Business:   {business_name or 'Unknown'}
Session ID: {session_id[:8]}
Time:       {timestamp}

Action:     {escalation_action}
Reason:     {escalation_reason or 'Not specified'}

--- Conversation Transcript (last 4 messages) ---
{transcript}
{'=' * 40}

Sent automatically by your website assistant.
Please follow up with this person directly.
"""

    from core import notifier
    sent = notifier.send_email(to_email, f"Needs a person: {business_name or session_id[:8]}", body)
    if sent:
        logger.info(f"Escalation alert sent for session {session_id[:8]}")
    return sent


def _build_transcript(history: list) -> str:
    if not history:
        return "(no conversation history available)"
    recent = history[-4:]
    lines = []
    for msg in recent:
        role = "Visitor" if msg.get("role") == "user" else "Assistant"
        content = msg.get("content", "")[:300]
        lines.append(f"{role}: {content}")
    return "\n\n".join(lines)
