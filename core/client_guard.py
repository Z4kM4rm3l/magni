"""
core/client_guard.py

Multi-layer billing protection for Magni.
Four independent safeguards — ALL must pass before Gemini is called.

Layer 1: Atomic DB increment with SELECT FOR UPDATE (race condition proof)
Layer 2: Hard fail-closed on any DB error (no silent bypass)
Layer 3: Global daily spend cap across ALL clients combined
Layer 4: Google Cloud budget alert (set in GCP console — external to code)
"""
import os
from typing import Tuple, Optional
from datetime import date
from sqlalchemy.orm import Session
from sqlalchemy import text
from core.models import Client
from core.billing_policies import TIER_LIMITS
from core.utils import logger

# ── GLOBAL SAFETY CAP ────────────────────────────────────────────────────────
# Maximum Gemini API calls across ALL clients in a single day.
# At ~$0.0001 per call this caps your daily exposure at roughly $1.
# Change this as your client base grows.
GLOBAL_DAILY_CAP = 10_000

# In-memory daily counter (resets when Railway restarts, which is fine —
# it's a secondary layer, not the primary one)
_global_usage = {"date": str(date.today()), "count": 0}


def _check_global_cap() -> Tuple[bool, str]:
    """
    Layer 3: Global daily cap across all clients.
    Protects against a scenario where multiple clients all hit their limits
    simultaneously due to a bug and the individual checks fail.
    """
    today = str(date.today())
    if _global_usage["date"] != today:
        # New day — reset the counter
        _global_usage["date"] = today
        _global_usage["count"] = 0

    if _global_usage["count"] >= GLOBAL_DAILY_CAP:
        logger.critical(
            f"GLOBAL DAILY CAP HIT: {_global_usage['count']} requests today. "
            f"All Gemini calls are blocked until midnight UTC."
        )
        return False, "global_cap_exceeded"

    _global_usage["count"] += 1
    return True, "ok"


def track_and_validate_request(
    db: Session, api_key: str
) -> Tuple[bool, str, Optional[Client]]:
    """
    Layer 1 + 2: Atomic per-client check with hard fail-closed.

    Uses SELECT FOR UPDATE to lock the client row during the check+increment,
    preventing race conditions where two simultaneous requests both pass
    a check against the same counter value.

    FAIL CLOSED: Any exception blocks the request. We never silently allow
    a request through if we cannot verify the client's usage.
    """
    if not api_key:
        return False, "Missing API key.", None

    try:
        # SELECT FOR UPDATE locks this row until the transaction commits.
        # A second simultaneous request for the same client will WAIT here
        # until the first one finishes — eliminating the race condition.
        client = (
            db.query(Client)
            .filter(Client.api_key == api_key)
            .with_for_update()
            .first()
        )

        if not client:
            return False, "Invalid API key.", None

        if not client.is_active:
            return False, "Account inactive. Please check your subscription.", None

        # Resolve limit from tier or explicit override
        tier = client.tier or "starter"
        limit = (
            client.monthly_limit
            if client.monthly_limit is not None
            else TIER_LIMITS.get(tier, TIER_LIMITS["starter"])
        )
        used = client.monthly_used or 0

        # -1 sentinel = unlimited (Enterprise custom plan)
        if limit != -1 and used >= limit:
            return (
                False,
                f"Monthly conversation limit reached ({used}/{limit}) "
                f"for tier '{tier}'. Please upgrade your plan.",
                None,
            )

        # Layer 3: Check global cap BEFORE incrementing individual counter
        global_ok, global_reason = _check_global_cap()
        if not global_ok:
            logger.error(
                f"Global cap blocked request for client {client.id}. "
                f"Individual limit was fine ({used}/{limit})."
            )
            return False, "Service temporarily unavailable. Please try again later.", None

        # All checks passed — increment atomically within the locked transaction
        client.monthly_used = used + 1
        db.add(client)
        # Caller is responsible for db.commit()

        return True, "Authorized", client

    except Exception as e:
        # Layer 2: HARD FAIL CLOSED
        # We do NOT return True here. If we cannot verify the limit,
        # we block the request. This is intentional.
        logger.critical(
            f"client_guard EXCEPTION — request BLOCKED (fail-closed): {e}",
            exc_info=True
        )
        return (
            False,
            "Unable to verify account status. Please try again in a moment.",
            None,
        )


def increment_client_usage(db: Session, client: Client) -> None:
    """
    Legacy helper kept for backward compatibility with existing routes.
    New code should use track_and_validate_request which increments atomically.
    """
    client.monthly_used = (client.monthly_used or 0) + 1
    db.add(client)
