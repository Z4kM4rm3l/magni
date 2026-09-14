"""
core/conversation_store.py

Durable, tenant-scoped conversation record, backed by PostgreSQL.

Ownership rule: start_conversation(client_id, session_id) is the ONLY operation
that creates a conversation and assigns its owning client. Every other write
(add_message_to_conversation, set_resolution, set_rating) locates an existing
conversation by its globally unique session_id and never creates one; if the
session is unknown it logs and no-ops, so a message can never land on an
unowned conversation.

Reads are client-scoped: get_all_conversations(client_id) and
get_analytics_data(client_id) only ever see one tenant's data.

Dict shape matches the previous JSON store (epoch-float timestamps) so the
analytics routes and dashboard keep working unchanged.
"""
import time
from datetime import datetime, timezone

from sqlalchemy import select

from core.db import SessionLocal
from core.models import Conversation, Message
from core.utils import logger

RESOLUTION_KEYWORDS_YES = [
    "thank you", "thanks", "that helped", "that's helpful", "helpful",
    "perfect", "great", "resolved", "got it", "understood", "makes sense",
    "awesome", "excellent", "appreciate", "solved", "fixed", "works now",
    "all good", "that's all", "no more questions", "you're great"
]

RESOLUTION_KEYWORDS_NO = [
    "still not working", "didn't help", "doesn't work", "not resolved",
    "still having issues", "same problem", "frustrated", "useless",
    "not helpful", "wrong", "incorrect", "that's wrong", "no that's not"
]


def _epoch(dt):
    return dt.timestamp() if dt else None


def _conv_to_dict(c: Conversation) -> dict:
    """Conversation row -> the dict shape analytics and the dashboard expect.
    Messages are intentionally omitted (a separate table now); analytics uses
    message_count, and set_resolution reads messages directly when needed."""
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


def evaluate_resolution_with_ai(last_user: str, last_agent: str) -> bool:
    """Asymmetric LLM fallback: True only if the sentiment is clearly positive;
    defaults to False on any uncertainty or failure."""
    try:
        import google.generativeai as genai
        model = genai.GenerativeModel(model_name="gemini-2.5-flash")
        prompt = f"""Analyze this final exchange between a customer support AI and a user.
Determine if the user's issue was successfully RESOLVED by the agent's last answer.

Last Agent Answer: "{last_agent}"
User Response: "{last_user}"

Respond with exactly one word: YES or NO. If unsure, respond NO.
Resolution:"""
        response = model.generate_content(prompt)
        verdict = response.text.strip().upper()
        logger.info(f"AI Resolution analysis verdict: {verdict}")
        return "YES" in verdict
    except Exception as e:
        logger.error(f"Failed to evaluate resolution via AI fallback: {e}")
        return False


def _last_turns(db, session_id: str):
    rows = db.execute(
        select(Message.role, Message.content)
        .where(Message.session_id == session_id)
        .order_by(Message.id)
    ).all()
    last_user = ""
    last_agent = ""
    for role, content in rows:
        if role == "user":
            last_user = content
        elif role == "assistant":
            last_agent = content
    return last_user, last_agent, len(rows)


def set_resolution(client_id: str, session_id: str, client_resolved: bool = None) -> dict:
    """Hybrid resolution pipeline (explicit override -> keyword heuristics ->
    asymmetric AI fallback). Locates the conversation by (client_id, session_id);
    never creates one and never touches another tenant's conversation."""
    db = SessionLocal()
    try:
        c = db.get(Conversation, session_id)
        if not c or c.client_id != client_id:
            logger.warning(f"set_resolution: no conversation for (client={str(client_id)[:8]}, session={str(session_id)[:8]}) - no-op")
            return {}

        last_user_text, last_agent_text, msg_count = _last_turns(db, session_id)
        c.resolution_last_user = last_user_text or None
        c.resolution_last_agent = last_agent_text or None

        # STEP 1: explicit client override
        if client_resolved is not None:
            c.resolved = client_resolved
            c.resolution_source = "user_explicit"
            c.resolution_confidence = 1.0
            c.ended_at = datetime.now(timezone.utc)
            db.commit(); db.refresh(c)
            logger.info(f"Resolution via explicit UI action for session {session_id[:8]}: {client_resolved}")
            return _conv_to_dict(c)

        # EDGE CASES
        if msg_count == 0:
            c.resolved = False; c.resolution_source = "no_data"; c.resolution_confidence = 0.0
            db.commit(); db.refresh(c); return _conv_to_dict(c)
        if not last_user_text:
            c.resolved = False; c.resolution_source = "no_user_turn"; c.resolution_confidence = 0.0
            db.commit(); db.refresh(c); return _conv_to_dict(c)

        # STEP 2: keyword heuristics (negative first to suppress false positives)
        user_text_lower = last_user_text.lower()
        if any(kw in user_text_lower for kw in RESOLUTION_KEYWORDS_NO):
            c.resolved = False; c.resolution_source = "keyword_no"; c.resolution_confidence = 1.0
            c.ended_at = datetime.now(timezone.utc)
            db.commit(); db.refresh(c)
            logger.info(f"Resolution negative heuristic for session {session_id[:8]}.")
            return _conv_to_dict(c)
        if any(kw in user_text_lower for kw in RESOLUTION_KEYWORDS_YES):
            c.resolved = True; c.resolution_source = "keyword_yes"; c.resolution_confidence = 0.90
            c.ended_at = datetime.now(timezone.utc)
            db.commit(); db.refresh(c)
            logger.info(f"Resolution positive heuristic for session {session_id[:8]}.")
            return _conv_to_dict(c)

        # STEP 3: asymmetric LLM fallback
        ai_verdict = evaluate_resolution_with_ai(last_user_text, last_agent_text)
        c.resolved = ai_verdict; c.resolution_source = "ai_fallback"; c.resolution_confidence = 0.85
        c.ended_at = datetime.now(timezone.utc)
        db.commit(); db.refresh(c)
        logger.info(f"Resolution telemetry saved for session {session_id[:8]}. Source: ai_fallback, Result: {ai_verdict}")
        return _conv_to_dict(c)
    except Exception as e:
        db.rollback()
        logger.error(f"set_resolution error (session={str(session_id)[:8]}): {e}")
        raise
    finally:
        db.close()


def set_rating(client_id: str, session_id: str, rating: int, comment: str = "") -> bool:
    """Attach a rating/comment to a conversation owned by client_id."""
    db = SessionLocal()
    try:
        c = db.get(Conversation, session_id)
        if not c or c.client_id != client_id:
            logger.warning(f"set_rating: no conversation for (client={str(client_id)[:8]}, session={str(session_id)[:8]}) - no-op")
            return False
        c.rating = rating
        c.feedback_comment = comment
        db.commit()
        return True
    except Exception as e:
        db.rollback()
        logger.error(f"set_rating error (session={str(session_id)[:8]}): {e}")
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


# --- ANALYTICS DASHBOARD PRE-AGGREGATION (tenant-scoped) ---
def get_analytics_data(client_id: str) -> dict:
    conversations = _load_client_conversations(client_id)

    now = time.time()
    one_day = 86400
    one_week = 604800
    one_month = 2592000

    today_convs = [c for c in conversations if now - (c.get("started_at") or 0) <= one_day]
    week_convs = [c for c in conversations if now - (c.get("started_at") or 0) <= one_week]
    month_convs = [c for c in conversations if now - (c.get("started_at") or 0) <= one_month]

    def resolution_rate(conv_list):
        evaluated = [c for c in conv_list if c.get("resolved") is not None]
        if not evaluated: return 0
        positives = [c for c in evaluated if c["resolved"] is True]
        return round((len(positives) / len(evaluated)) * 100)

    def avg_messages(conv_list):
        if not conv_list: return 0
        return round(sum(c.get("message_count", 0) for c in conv_list) / len(conv_list), 1)

    def avg_rating(conv_list):
        rated = [c for c in conv_list if c.get("rating") is not None]
        if not rated: return 0
        return round(sum(c["rating"] for c in rated) / len(rated), 1)

    def intent_breakdown(conv_list):
        counts = {}
        for c in conv_list:
            intent = c.get("primary_intent", "general")
            counts[intent] = counts.get(intent, 0) + 1
        return counts

    unresolved = [c for c in conversations if c.get("resolved") is False]
    sorted_ur = sorted(unresolved, key=lambda x: x.get("ended_at", 0) or 0, reverse=True)
    unresolved_snippets = [{
        "session_id": c["session_id"][:8],
        "intent": c.get("primary_intent", "general"),
        "message_count": c.get("message_count", 0),
        "last_message": c.get("resolution_last_user", "")[:100] if c.get("resolution_last_user") else "",
        "date": c.get("date", "")
    } for c in sorted_ur[:10]]

    return {
        "totals": {
            "today": len(today_convs),
            "week": len(week_convs),
            "month": len(month_convs),
            "all_time": len(conversations)
        },
        "resolution_rate": {
            "today": resolution_rate(today_convs),
            "week": resolution_rate(week_convs),
            "month": resolution_rate(month_convs)
        },
        "avg_messages": {
            "today": avg_messages(today_convs),
            "week": avg_messages(week_convs),
            "month": avg_messages(month_convs)
        },
        "satisfaction": {
            "today": avg_rating(today_convs),
            "week": avg_rating(week_convs),
            "month": avg_rating(month_convs)
        },
        "intent_breakdown": intent_breakdown(month_convs),
        "unresolved": unresolved_snippets
    }
