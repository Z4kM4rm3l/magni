"""
core/llm_health.py

Tells the operator (you, not the business owners) when model calls start
failing: the provider is down, the API key is bad, or the account is out of
credits or over its spend limit. Visitors are already covered (the chat falls
back to taking a message, see core/escalations.py); this is so the operator
hears about it from Magni instead of from a customer.

  OPERATOR_ALERT_EMAIL   where the alert email goes
  OPERATOR_ALERT_PHONE   optional US mobile for a text (needs the Twilio variables)

An alert goes out after FAILURES_TO_ALERT failed calls inside WINDOW_SECONDS,
then at most once per COOLDOWN_SECONDS while the problem lasts, and once more
when calls start working again. Counters live in this process's memory, which
is right for one web process (the same assumption as core/rate_limit.py).
Nothing here raises: a broken alert must never break a chat.
"""
import os
import threading
import time
from collections import deque

from core.utils import logger

FAILURES_TO_ALERT = 3
WINDOW_SECONDS = 300
COOLDOWN_SECONDS = 1800

_lock = threading.Lock()
_failures: deque = deque()          # timestamps of recent failed calls
_state = {"alerted_at": 0.0, "down": False, "since": 0.0, "total": 0}


def reset() -> None:
    with _lock:
        _failures.clear()
        _state.update(alerted_at=0.0, down=False, since=0.0, total=0)


def _hint(error: str) -> str:
    e = (error or "").lower()
    if "credit" in e or "billing" in e or "balance" in e:
        return "This looks like the account is out of credits. Check Billing in the Claude Console."
    if "401" in e or "authentication" in e or "api key" in e or "api_key" in e:
        return "This looks like a bad or missing API key. Check ANTHROPIC_API_KEY in Railway."
    if "403" in e or "permission" in e:
        return "The API key isn't allowed to do this. Check the key's workspace and the model name."
    if "429" in e or "rate" in e or "spend" in e or "limit" in e:
        return "This looks like a rate limit or the workspace spend limit. Check Limits in the Claude Console."
    if "404" in e or "not_found" in e or "model" in e:
        return "The model name may be wrong or retired. Check CLAUDE_MODEL in Railway."
    if "529" in e or "overloaded" in e or "500" in e or "timeout" in e or "connection" in e:
        return "This looks like a problem on the provider's side. Check status.claude.com."
    return "Check status.claude.com, then the API key and billing in the Claude Console."


def _targets() -> tuple[str, str]:
    from core.leasing import normalize_phone_e164
    email = os.getenv("OPERATOR_ALERT_EMAIL", "").strip()
    phone = normalize_phone_e164(os.getenv("OPERATOR_ALERT_PHONE", "").strip()) or ""
    return email, phone


def _send(subject: str, body: str, sms: str) -> None:
    try:
        from core import notifier
        email, phone = _targets()
        if not email and not phone:
            logger.critical(f"{subject} (no OPERATOR_ALERT_EMAIL / OPERATOR_ALERT_PHONE set, so nobody was told)")
            return

        def go():
            if email and not notifier.send_email(email, subject, body, from_name="Magni Operations"):
                logger.error("Operator alert email could not be sent.")
            if phone and not notifier.send_sms(phone, sms):
                logger.warning("Operator alert text not sent (Twilio not configured or failed).")
        notifier._dispatch(go)
    except Exception as e:  # never let alerting break a chat turn
        logger.error(f"operator alert error: {e}")


def record_failure(error: Exception | str) -> None:
    """Call when a model request failed. Sends the alert when failures pile up."""
    now = time.time()
    text = f"{type(error).__name__}: {error}" if isinstance(error, Exception) else str(error)
    with _lock:
        _failures.append(now)
        while _failures and now - _failures[0] > WINDOW_SECONDS:
            _failures.popleft()
        _state["total"] += 1
        count = len(_failures)
        if count < FAILURES_TO_ALERT or now - _state["alerted_at"] < COOLDOWN_SECONDS:
            return
        first = not _state["down"]
        if first:
            _state["since"] = _failures[0]
            _state["total"] = count
        _state["down"] = True
        _state["alerted_at"] = now
        total, since = _state["total"], _state["since"]
    minutes = max(1, round((now - since) / 60))
    hint = _hint(text)
    subject = "Magni: AI model calls are failing" if first else "Magni: AI model calls are still failing"
    body = (
        f"{subject}\n{'=' * 40}\n\n"
        f"Failed calls:  {total} in the last {minutes} minute(s)\n"
        f"Latest error:  {text[:500]}\n\n"
        f"Likely cause:  {hint}\n\n"
        "What visitors see: the assistant apologizes, asks for their contact details and\n"
        "passes the chat to the business owner, so no chat is lost. Answers resume on\n"
        "their own once calls succeed, and you'll get one more message when they do.\n"
    )
    logger.critical(f"{subject}: {total} failures, latest: {text[:200]}")
    _send(subject, body, f"Magni: AI model calls are failing ({total} so far). {hint}"[:300])


def record_success() -> None:
    """Call when a model request worked. Sends one "recovered" notice after an alert."""
    with _lock:
        if not _state["down"]:
            if _failures:
                _failures.clear()
            return
        minutes = max(1, round((time.time() - _state["since"]) / 60))
        total = _state["total"]
        _failures.clear()
        _state.update(alerted_at=0.0, down=False, since=0.0, total=0)
    subject = "Magni: AI model calls are working again"
    logger.info(subject)
    _send(subject,
          f"{subject}\n{'=' * 40}\n\nThe problem lasted about {minutes} minute(s) and {total} call(s) failed.\n"
          "Assistants are answering normally again. Chats handed to owners in the meantime are in\n"
          "each owner's portal under \"Needs a person\".\n",
          f"Magni: AI model calls are working again after about {minutes} min.")
