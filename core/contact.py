"""
core/contact.py

Reads an email address or US phone number out of what a visitor typed.
Deterministic (no model), so nothing is ever recorded that isn't literally in
the message. Used by core/escalations.py and for owners' alert settings.
"""
import re

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+?1[\s.\-]?)?\(?\b(\d{3})\)?[\s.\-]?(\d{3})[\s.\-]?(\d{4})\b")


def find_email(text: str) -> str | None:
    m = EMAIL_RE.search(text or "")
    return m.group(0).lower() if m else None


def find_phone(text: str) -> str | None:
    m = PHONE_RE.search(text or "")
    return f"({m.group(1)}) {m.group(2)}-{m.group(3)}" if m else None


def is_email(value: str) -> bool:
    return bool(EMAIL_RE.fullmatch((value or "").strip()))


def normalize_phone_e164(raw: str) -> str | None:
    """US numbers only, for SMS alerts. Returns +1XXXXXXXXXX or None."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10 or digits[0] in "01":
        return None
    return "+1" + digits
