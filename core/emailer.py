"""
core/emailer.py

Best-effort escalation email notifications via Gmail SMTP.
A failure here must never crash the orchestrator or block a chat response.

Required env vars:
  MAGNI_SMTP_FROM      — sender address, e.g. alerts@yourdomain.com
  MAGNI_SMTP_PASSWORD  — Gmail app password for the sender account
"""
import os
import smtplib
from datetime import datetime, timezone
from email.mime.text import MIMEText
from core.utils import logger

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


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
    smtp_from = os.getenv("MAGNI_SMTP_FROM", "")
    smtp_password = os.getenv("MAGNI_SMTP_PASSWORD", "")

    if not smtp_from or not smtp_password:
        logger.warning("Escalation alert skipped — MAGNI_SMTP_FROM or MAGNI_SMTP_PASSWORD not set.")
        return False

    if not to_email:
        logger.warning(f"Escalation alert skipped — no email on file for session {session_id[:8]}.")
        return False

    transcript = _build_transcript(history)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    body = f"""Magni Escalation Alert
{'=' * 40}

Business:   {business_name or 'Unknown'}
Session ID: {session_id[:8]}
Time:       {timestamp}

Action:     {escalation_action}
Reason:     {escalation_reason or 'Not specified'}

--- Conversation Transcript (last 4 messages) ---
{transcript}
{'=' * 40}

This alert was generated automatically by Magni.
A team member should follow up with this customer directly.
"""

    msg = MIMEText(body, "plain")
    msg["Subject"] = f"[Magni Alert] Escalation — {business_name or session_id[:8]}"
    msg["From"] = smtp_from
    msg["To"] = to_email

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_from, smtp_password)
            server.sendmail(smtp_from, [to_email], msg.as_string())
        logger.info(f"Escalation alert sent to {to_email} for session {session_id[:8]}")
        return True
    except Exception as e:
        logger.error(f"Escalation alert failed for session {session_id[:8]}: {e}")
        return False


def _build_transcript(history: list) -> str:
    if not history:
        return "(no conversation history available)"
    recent = history[-4:]
    lines = []
    for msg in recent:
        role = "Customer" if msg.get("role") == "user" else "Magni"
        content = msg.get("content", "")[:300]
        lines.append(f"{role}: {content}")
    return "\n\n".join(lines)
