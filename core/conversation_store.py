"""
core/conversation_store.py

Durable, tenant-scoped conversation record, backed by PostgreSQL.

Ownership rule: start_conversation(client_id, session_id) is the ONLY operation
that creates a conversation and assigns its owning client.
add_message_to_conversation locates an existing conversation by its globally
unique session_id and never creates one; if the session is unknown or owned by
another client it logs and no-ops, so a message can never land on another
tenant's conversation.

Reads are client-scoped: get_all_conversations(client_id) only ever sees one
tenant's data.
"""
from datetime import datetime, timezone

from core.db import SessionLocal
from core.models import Conversation, Message
from core.utils import logger

def _epoch(dt):
    return dt.timestamp() if dt else None


def _conv_to_dict(c: Conversation) -> dict:
    """Conversation row -> summary dict. Messages are omitted (separate table)."""
    return {
        "session_id": c.session_id,
        "client_id": c.client_id,
        "started_at": _epoch(c.started_at),
        "ended_at": _epoch(c.ended_at),
        "message_count": c.message_count or 0,
        "intents": c.intents or [],
        "primary_intent": c.primary_intent or "general",
        "resolved": c.resolved,
        "resolution_source": c.resolution_source,
        "resolution_confidence": c.resolution_confidence,
        "resolution_last_user": c.resolution_last_user,
        "resolution_last_agent": c.resolution_last_agent,
        "rating": c.rating,
        "feedback_comment": c.feedback_comment or "",
        "date": c.date or "",
        "hour": c.hour,
    }


def start_conversation(client_id: str, session_id: str, initial_intent: str = "general") -> dict:
    """Create (or return) the conversation for session_id, owned by client_id.
    This is the only operation that establishes ownership."""
    db = SessionLocal()
    try:
        c = db.get(Conversation, session_id)
        if c:
            if c.client_id != client_id:
                logger.warning(f"start_conversation: session {str(session_id)[:8]} already owned by another client - refusing")
                return {}
            return _conv_to_dict(c)
        now = datetime.now(timezone.utc)
        c = Conversation(
            session_id=session_id,
            client_id=client_id,
            started_at=now,
            ended_at=None,
            message_count=0,
            intents=[initial_intent],
            primary_intent=initial_intent,
            resolved=None,
            resolution_source=None,
            resolution_confidence=None,
            resolution_last_user=None,
            resolution_last_agent=None,
            rating=None,
            feedback_comment="",
            date=now.strftime("%Y-%m-%d"),
            hour=now.hour,
        )
        db.add(c)
        db.commit()
        db.refresh(c)
        return _conv_to_dict(c)
    except Exception as e:
        db.rollback()
        logger.error(f"start_conversation error (client={str(client_id)[:8]}, session={str(session_id)[:8]}): {e}")
        raise
    finally:
        db.close()


def add_message_to_conversation(client_id: str, session_id: str, role: str, content: str, current_intent: str = None) -> bool:
    """Append a message to a conversation owned by client_id. Never creates one,
    and never touches another tenant's conversation: if there is no conversation
    matching (client_id, session_id), logs and returns False."""
    db = SessionLocal()
    try:
        c = db.get(Conversation, session_id)
        if not c or c.client_id != client_id:
            logger.warning(f"add_message: no conversation for (client={str(client_id)[:8]}, session={str(session_id)[:8]}) - not logging")
            return False
        db.add(Message(
            session_id=session_id,
            role=role,
            content=content,
            timestamp=datetime.now(timezone.utc),
        ))
        c.message_count = (c.message_count or 0) + 1
        if current_intent:
            intents = list(c.intents or [])
            if current_intent not in intents:
                intents.append(current_intent)
                c.intents = intents
                c.primary_intent = intents[0]  # first-intent fallback, matches prior logic
        db.commit()
        return True
    except Exception as e:
        db.rollback()
        logger.error(f"add_message error (session={str(session_id)[:8]}): {e}")
        raise
    finally:
        db.close()


def _load_client_conversations(client_id: str) -> list:
    """All of one client's conversations as analytics-shaped dicts (no messages)."""
    db = SessionLocal()
    try:
        rows = (
            db.query(Conversation)
            .filter(Conversation.client_id == client_id)
            .order_by(Conversation.started_at.desc())
            .all()
        )
        return [_conv_to_dict(c) for c in rows]
    finally:
        db.close()


def get_all_conversations(client_id: str) -> list:
    """Tenant-scoped list of conversations (summary dicts, no message bodies)."""
    return _load_client_conversations(client_id)
