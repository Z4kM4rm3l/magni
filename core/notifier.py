"""
core/notifier.py

Real-time owner alerts for new leads: email (Gmail SMTP, already used for
escalations) and optional SMS through Twilio's REST API (no SDK needed).

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

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587

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


def portal_url() -> str:
    return f"{base_url()}/portal#leads"


def appt_url(token: str) -> str:
    return f"{base_url()}/appt/{token}"


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
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_from, smtp_password)
            server.sendmail(smtp_from, [to_email], msg.as_string())
        return True
    except Exception as e:
        logger.error(f"Lead email failed: {e}")
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
        logger.error(f"Lead SMS failed: {e}")
        return False


_KIND_TITLE = {
    "complete": "New lead",
    "partial": "New lead (didn't finish the questions)",
    "accommodation": "Accommodation request",
}


def build_lead_alert(kind: str, business_name: str, lead: dict) -> tuple[str, str, str]:
    """Return (subject, email_body, sms_body)."""
    title = _KIND_TITLE.get(kind, "New lead")
    booked = next((x["value"] for x in lead.get("summary", []) if str(x["value"]).startswith("Booked: ")), "")
    if kind == "complete" and booked:
        title = "New lead + appointment booked"
    who = lead.get("name") or lead.get("phone") or lead.get("email") or "a website visitor"
    first = next((x["value"] for x in lead.get("summary", []) if x["label"] in ("Property / unit",)), "")
    subject = f"{title}: {who}" + (f" · {first}" if first else "")

    lines = [f"{title} from your website chat ({business_name or 'your business'})", ""]
    for item in lead.get("summary", []):
        lines.append(f"{item['label']}: {item['value']}")
    if lead.get("flags", {}).get("accommodation_request"):
        lines += ["", "NOTE: The prospect mentioned an assistance animal or an accommodation need.",
                  "Please follow up personally. Do not apply pet rules or fees to assistance animals."]
    lines += ["", f"See all leads: {portal_url()}", "",
              "Magni collects answers only. It does not screen, score, or reject anyone."]
    email_body = "\n".join(lines)

    bits = [f"{title}: {who}"]
    for item in lead.get("summary", []):
        if item["label"] in ("Phone", "Property / unit", "Move-in date", "Stay dates") or str(item["value"]).startswith("Booked: "):
            bits.append(f"{item['label']}: {item['value']}")
    if lead.get("flags", {}).get("accommodation_request"):
        bits.append("Accommodation request, follow up personally")
    bits.append(portal_url())
    sms_body = "\n".join(bits)
    return subject, email_body, sms_body


def notify_owner(kind: str, business_name: str, to_email: str, to_phone: str, lead: dict,
                 ics: str | None = None) -> None:
    subject, email_body, sms_body = build_lead_alert(kind, business_name, lead)

    def _send():
        if to_email:
            send_email(to_email, subject, email_body, reply_to=lead.get("email") or None, ics=ics)
        if to_phone:
            send_sms(to_phone, sms_body)
        logger.info(f"Lead alert dispatched kind={kind} lead={lead.get('id', '')[:8]}")

    _dispatch(_send)


# ── Appointment emails ──────────────────────────────────────────────────────
def send_appointment_emails(kind: str, *, business_name: str, owner_email: str, appt: dict,
                            details: str, manage_url: str, ics_for_prospect: str | None = None) -> None:
    """kind: booked | canceled_by_prospect | canceled_by_owner | reminder.
    Prospect emails come "from" the business name with Reply-To the owner, so a
    prospect who hits reply reaches the business, not Magni."""
    biz = business_name or "our team"
    when = appt.get("when", "")
    who = appt.get("name") or "A prospect"
    prop = f" ({appt['property']})" if appt.get("property") else ""

    def _send():
        if kind == "booked" and appt.get("email"):
            body = "\n".join([
                f"Hi {appt.get('name') or 'there'},", "",
                f"You're booked with {biz} on {when}{prop}.",
                *(["", details] if details else []), "",
                f"Need to cancel? {manage_url}",
                "To reschedule, cancel there and pick a new time in the chat, or just reply to this email.", "",
                "A calendar invite is attached.",
            ])
            send_email(appt["email"], f"Confirmed: {when} with {biz}", body,
                       reply_to=owner_email or None, ics=ics_for_prospect, from_name=biz)
        elif kind == "canceled_by_prospect" and owner_email:
            body = "\n".join([f"{who} canceled their appointment on {when}{prop}.", "",
                               f"Phone: {appt.get('phone') or 'n/a'}   Email: {appt.get('email') or 'n/a'}", "",
                               f"That time is open again. See your schedule: {base_url()}/portal#appointments"])
            send_email(owner_email, f"Canceled: {who} · {when}", body, reply_to=appt.get("email") or None)
        elif kind == "canceled_by_owner" and appt.get("email"):
            body = "\n".join([f"Hi {appt.get('name') or 'there'},", "",
                               f"{biz} had to cancel your appointment on {when}. We're sorry for the change.",
                               "Reply to this email or chat with us on our website to pick a new time."])
            send_email(appt["email"], f"Canceled: {when} with {biz}", body,
                       reply_to=owner_email or None, from_name=biz)
        elif kind == "reminder" and appt.get("email"):
            body = "\n".join([f"Hi {appt.get('name') or 'there'},", "",
                               f"A reminder that you're scheduled with {biz} on {when}{prop}.",
                               *(["", details] if details else []), "",
                               f"Can't make it? Cancel here: {manage_url}"])
            send_email(appt["email"], f"Reminder: {when} with {biz}", body,
                       reply_to=owner_email or None, from_name=biz)

    _dispatch(_send)
