"""
core/client_manager.py

SQL-backed rewrite. Public function signatures are identical to the previous
JSON-file version so all admin routes in app.py continue to work unchanged.

data/clients.json is left on disk as a backup and is no longer written to.
"""
import uuid
import time
from datetime import datetime, timezone
from core.db import SessionLocal
from core.models import Client
from core.billing_policies import TIER_LIMITS
from core.utils import logger

# Feature flags per tier — kept here for admin UI display logic.
# The billing call-cap integers live in billing_policies.TIER_LIMITS.
TIER_FEATURES = {
    "starter": {
        "conversation_limit": 1000,
        "kb_article_limit": 10,
        "custom_identity": False,
        "white_label": False,
        "analytics": "basic",
    },
    "professional": {
        "conversation_limit": 5000,
        "kb_article_limit": 50,
        "custom_identity": True,
        "white_label": False,
        "analytics": "full",
    },
    "enterprise": {
        "conversation_limit": 25000,
        "kb_article_limit": -1,
        "custom_identity": True,
        "white_label": True,
        "analytics": "full",
    },
}


def _client_to_dict(c: Client) -> dict:
    """Translate a Client ORM row to the dict shape the admin UI expects."""
    tier = c.tier or "starter"
    features = TIER_FEATURES.get(tier, TIER_FEATURES["starter"])
    return {
        "client_id":          c.id,
        "business_name":      c.business_name or "",
        "email":              c.email or "",
        "tier":               tier,
        "status":             "active" if c.is_active else "suspended",
        "bot_name":           c.bot_name or "Magni",
        "api_key":            c.api_key or "",
        "notes":              c.notes or "",
        # Widget config
        "primary_color":      c.primary_color or "#f59e0b",
        "welcome_message":    c.welcome_message or "Hi! How can I help you today?",
        "allowed_domains":    c.allowed_domains or [],
        # Billing counters (kept for UI parity with old JSON shape)
        "conversation_limit": (
            c.monthly_limit if c.monthly_limit is not None
            else features["conversation_limit"]
        ),
        "conversations_used": c.monthly_used or 0,
        # Tier feature flags
        "kb_article_limit":   features["kb_article_limit"],
        "custom_identity":    features["custom_identity"],
        "white_label":        features["white_label"],
        "analytics":          features["analytics"],
        # Timestamps
        "created_at": c.created_at.timestamp() if c.created_at else None,
        "updated_at": None,  # not tracked on the SQL model; kept for shape parity
    }


def create_client(
    business_name: str,
    email: str,
    tier: str,
    bot_name: str = "Magni",
    api_key: str = "",
    billing_date: str = "",
    primary_color: str = "#f59e0b",
    welcome_message: str = "Hi! How can I help you today?",
    allowed_domains: list | None = None,
    notes: str = "",
) -> dict:
    features = TIER_FEATURES.get(tier, TIER_FEATURES["starter"])
    db = SessionLocal()
    try:
        client = Client(
            id=str(uuid.uuid4()),
            api_key=api_key or f"sk_magni_{uuid.uuid4().hex[:24]}",
            business_name=business_name,
            email=email,
            tier=tier,
            is_active=True,
            bot_name=bot_name,
            notes=notes,
            primary_color=primary_color,
            welcome_message=welcome_message,
            allowed_domains=allowed_domains or [],
            monthly_limit=TIER_LIMITS.get(tier, TIER_LIMITS["starter"]),
            monthly_used=0,
            created_at=datetime.now(timezone.utc),
        )
        db.add(client)
        db.commit()
        db.refresh(client)
        logger.info(f"Client created: {business_name} ({tier}) id={client.id[:8]}")
        return _client_to_dict(client)
    except Exception as e:
        db.rollback()
        logger.error(f"create_client error: {e}")
        raise
    finally:
        db.close()


def get_all_clients() -> list:
    db = SessionLocal()
    try:
        clients = db.query(Client).order_by(Client.created_at.desc()).all()
        return [_client_to_dict(c) for c in clients]
    finally:
        db.close()


def get_client(client_id: str) -> dict | None:
    db = SessionLocal()
    try:
        client = db.query(Client).filter(Client.id == client_id).first()
        return _client_to_dict(client) if client else None
    finally:
        db.close()


def update_client(client_id: str, **kwargs) -> dict | None:
    db = SessionLocal()
    try:
        client = db.query(Client).filter(Client.id == client_id).first()
        if not client:
            return None

        # If tier is changing, update the monthly_limit to match the new tier
        if "tier" in kwargs and kwargs["tier"] != client.tier:
            new_tier = kwargs["tier"]
            client.monthly_limit = TIER_LIMITS.get(new_tier, TIER_LIMITS["starter"])

        field_map = {
            "business_name":  "business_name",
            "email":          "email",
            "bot_name":       "bot_name",
            "notes":          "notes",
            "tier":           "tier",
            "primary_color":  "primary_color",
            "welcome_message": "welcome_message",
            "allowed_domains": "allowed_domains",
        }
        for kwarg_key, col_name in field_map.items():
            if kwarg_key in kwargs:
                setattr(client, col_name, kwargs[kwarg_key])

        db.commit()
        db.refresh(client)
        return _client_to_dict(client)
    except Exception as e:
        db.rollback()
        logger.error(f"update_client error: {e}")
        raise
    finally:
        db.close()


def suspend_client(client_id: str) -> dict | None:
    db = SessionLocal()
    try:
        client = db.query(Client).filter(Client.id == client_id).first()
        if not client:
            return None
        client.is_active = False
        db.commit()
        db.refresh(client)
        return _client_to_dict(client)
    except Exception as e:
        db.rollback()
        logger.error(f"suspend_client error: {e}")
        raise
    finally:
        db.close()


def reactivate_client(client_id: str) -> dict | None:
    db = SessionLocal()
    try:
        client = db.query(Client).filter(Client.id == client_id).first()
        if not client:
            return None
        client.is_active = True
        client.monthly_used = 0
        db.commit()
        db.refresh(client)
        return _client_to_dict(client)
    except Exception as e:
        db.rollback()
        logger.error(f"reactivate_client error: {e}")
        raise
    finally:
        db.close()


def delete_client(client_id: str) -> bool:
    db = SessionLocal()
    try:
        client = db.query(Client).filter(Client.id == client_id).first()
        if not client:
            return False
        db.delete(client)
        db.commit()
        return True
    except Exception as e:
        db.rollback()
        logger.error(f"delete_client error: {e}")
        raise
    finally:
        db.close()


def get_client_stats() -> dict:
    db = SessionLocal()
    try:
        all_clients = db.query(Client).all()
        active    = [c for c in all_clients if c.is_active]
        suspended = [c for c in all_clients if not c.is_active]

        mrr_map = {"starter": 79, "professional": 249, "enterprise": 599}
        mrr = sum(mrr_map.get(c.tier, 0) for c in active)

        return {
            "total":     len(all_clients),
            "active":    len(active),
            "suspended": len(suspended),
            "mrr":       mrr,
            "by_tier": {
                "starter":      sum(1 for c in active if c.tier == "starter"),
                "professional": sum(1 for c in active if c.tier == "professional"),
                "enterprise":   sum(1 for c in active if c.tier == "enterprise"),
            },
        }
    finally:
        db.close()
