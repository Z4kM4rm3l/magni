# core/client_guard.py
from typing import Tuple, Optional
from sqlalchemy.orm import Session
from core.models import Client
from core.billing_policies import TIER_LIMITS

def track_and_validate_request(db: Session, api_key: str) -> Tuple[bool, str, Optional[Client]]:
    """
    Validates an incoming client request against database billing status and usage.
    Returns: (is_allowed, message, client_or_none)
    """
    if not api_key:
        return False, "Missing API Key.", None

    client = db.query(Client).filter(Client.api_key == api_key).first()
    if not client:
        return False, "Invalid API Key.", None

    if not client.is_active:
        return False, "Account is inactive. Please check your subscription status.", None

    tier = client.tier or "starter"
    # Fallback to TIER_LIMITS if an explicit custom monthly_limit override isn't configured
    limit = client.monthly_limit if client.monthly_limit is not None else TIER_LIMITS.get(tier, TIER_LIMITS["starter"])
    used = client.monthly_used or 0

    if limit != -1 and used >= limit:
        return False, f"Monthly conversation limit reached ({used}/{limit}) for tier '{tier}'.", None

    return True, "Authorized", client

def increment_client_usage(db: Session, client: Client) -> None:
    """
    Atomically increments usage for a verified tenant row.
    Call inside an open transaction.
    """
    client.monthly_used = (client.monthly_used or 0) + 1
    db.add(client)