"""
core/retention.py

How long Magni keeps what visitors type. Run daily by scripts/apply_retention.py.

  Chat transcripts (conversations + messages)    12 months after the chat started
  "Needs a person" records (hold a transcript)   12 months after the chat was flagged

The same period is stated in the portal and belongs in the terms / privacy
policy. Deletes run in batches so a big backlog never holds a long lock.
"""
from datetime import datetime, timezone, timedelta

from core.db import SessionLocal
from core.models import Conversation, Message, Escalation
from core.utils import logger

CHAT_RETENTION_DAYS = 365
BATCH = 500


def _delete_in_batches(db, model, key_col, filter_expr, before_delete=None) -> int:
    total = 0
    while True:
        ids = [r[0] for r in db.query(key_col).filter(filter_expr).limit(BATCH).all()]
        if not ids:
            return total
        if before_delete:
            before_delete(db, ids)
        db.query(model).filter(key_col.in_(ids)).delete(synchronize_session=False)
        db.commit()
        total += len(ids)


def _drop_messages(db, session_ids):
    # Postgres cascades this too; doing it explicitly keeps SQLite (tests) honest.
    db.query(Message).filter(Message.session_id.in_(session_ids)).delete(synchronize_session=False)


def apply_retention(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    chats_before = now - timedelta(days=CHAT_RETENTION_DAYS)
    db = SessionLocal()
    try:
        out = {
            "conversations": _delete_in_batches(db, Conversation, Conversation.session_id,
                                                Conversation.started_at < chats_before, _drop_messages),
            # "Needs a person" records hold the chat transcript, so they follow the chat period.
            "escalations": _delete_in_batches(db, Escalation, Escalation.id, Escalation.created_at < chats_before),
        }
        logger.info(f"retention: deleted {out}")
        return out
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
