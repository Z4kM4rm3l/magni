"""
core/notifier.py

Outgoing email (any SMTP provider) and optional SMS through Twilio's REST API
(no SDK needed): owner alerts, account emails, and operator alerts.

Everything here is best-effort: a failed alert is logged, never raised, and
never blocks a chat reply. Sends run on a background thread by default.

Env vars:
  MAGNI_SMTP_FROM, MAGNI_SMTP_PASSWORD   email (Gmail app password)
  TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER   SMS (optional)
  PUBLIC_BASE_URL                        e.g. https://app.yourdomain.com (for portal links)
"""
import os
import base64
import smtplib
import threading
import urllib.parse
import urllib.request
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from core.utils import logger

# Any SMTP provider works. Gmail is only the fallback for local testing; for
# real customers use a transactional sender on your own domain (Postmark,
# Resend, SendGrid, Amazon SES all offer SMTP). See docs/LAUNCH_CHECKLIST.md.
#   MAGNI_SMTP_FROM      the address mail comes from (alerts@yourdomain.com)
#   MAGNI_SMTP_PASSWORD  the provider's SMTP password or API token
#   MAGNI_SMTP_HOST      default smtp.gmail.com
#   MAGNI_SMTP_PORT      default 587 (STARTTLS)
#   MAGNI_SMTP_USER      login name if it isn't MAGNI_SMTP_FROM (Postmark: the token; Resend: "resend")
def _smtp_settings():
    return (os.getenv("MAGNI_SMTP_HOST", "").strip() or "smtp.gmail.com",
            int(os.getenv("MAGNI_SMTP_PORT", "").strip() or 587),
            os.getenv("MAGNI_SMTP_USER", "").strip() or os.getenv("MAGNI_SMTP_FROM", ""))

# Tests flip this to run sends inline.
RUN_IN_BACKGROUND = True


def _dispatch(fn, *args):
    if RUN_IN_BACKGROUND:
        threading.Thread(target=fn, args=args, daemon=True).start()
    else:
        fn(*args)


def base_url() -> str:
    """Public site root for links in emails and chat. Always PUBLIC_BASE_URL,
    never the incoming Host header (which a visitor could forge to plant a
    look-alike domain in an owner's alert email). The web app refuses to start
    in production without it."""
    return os.getenv("PUBLIC_BASE_URL", "").rstrip("/")


def send_email(to_email: str, subject: str, body: str, reply_to: str | None = None,
               ics: str | None = None, from_name: str = "Magni Alerts") -> bool:
    smtp_from = os.getenv("MAGNI_SMTP_FROM", "")
    smtp_password = os.getenv("MAGNI_SMTP_PASSWORD", "")
    if not (smtp_from and smtp_password and to_email):
        logger.warning("Email skipped: SMTP not configured or no recipient.")
        return False
    if ics:
        msg = MIMEMultipart()
        msg.attach(MIMEText(body, "plain", "utf-8"))
        part = MIMEBase("text", "calendar", method="PUBLISH", name="invite.ics")
        part.set_payload(ics.encode("utf-8"))
        import email.encoders
        email.encoders.encode_base64(part)
        part.add_header("Content-Disposition", "attachment", filename="invite.ics")
        msg.attach(part)
    else:
        msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = formataddr((from_name or "Magni Alerts", smtp_from))
    msg["To"] = to_email
    if reply_to:
        msg["Reply-To"] = reply_to
    try:
        host, port, user = _smtp_settings()
        with smtplib.SMTP(host, port, timeout=10) as server:
            server.ehlo()
            server.starttls()
            server.login(user, smtp_password)
            server.sendmail(smtp_from, [to_email], msg.as_string())
        return True
    except Exception as e:
        logger.error(f"Email failed ({subject[:40]}): {e}")
        return False


def send_sms(to_e164: str, body: str) -> bool:
    sid = os.getenv("TWILIO_ACCOUNT_SID", "")
    token = os.getenv("TWILIO_AUTH_TOKEN", "")
    from_number = os.getenv("TWILIO_FROM_NUMBER", "")
    if not (sid and token and from_number and to_e164):
        return False
    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    data = urllib.parse.urlencode({"To": to_e164, "From": from_number, "Body": body[:600]}).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", "Basic " + base64.b64encode(f"{sid}:{token}".encode()).decode())
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return 200 <= r.status < 300
    except Exception as e:
        logger.error(f"SMS failed: {e}")
        return False


# ── Account emails (to the business owner, English) ─────────────────────────
def send_password_reset(to_email: str, reset_url: str) -> None:
    body = "\n".join([
        "Someone asked to reset the password for your Magni account.", "",
        f"Choose a new password here (the link works for one hour): {reset_url}", "",
        "If this wasn't you, you can ignore this email. Your password won't change.",
    ])
    _dispatch(lambda: send_email(to_email, "Reset your Magni password", body, from_name="Magni"))


def send_usage_warning(to_email: str, business_name: str, used: int, limit: int, at_limit: bool) -> None:
    """Tell the owner before their assistant goes quiet, so they can upgrade
    instead of silently missing customers."""
    plan_url = f"{base_url()}/portal#billing"
    if at_limit:
        subject = "Your assistant has reached this month's conversation limit"
        lines = [f"{business_name or 'Your business'}'s website assistant has used all {limit:,} conversations "
                 "included this month.", "",
                 "Chats already under way can finish, but new visitors will see a short message asking them "
                 "to contact you directly until your allowance resets.", "",
                 f"To keep answering customers now, upgrade your plan: {plan_url}"]
    else:
        subject = "Your assistant has used 80% of this month's conversations"
        lines = [f"{business_name or 'Your business'}'s website assistant has used {used:,} of the {limit:,} "
                 "conversations included this month.", "",
                 "When it reaches the limit, new visitors will be asked to contact you directly instead of chatting.", "",
                 f"If you expect more traffic, you can upgrade here: {plan_url}"]
    _dispatch(lambda: send_email(to_email, subject, "\n".join(lines), from_name="Magni"))
