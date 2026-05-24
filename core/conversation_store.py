import os
import json
import time
from datetime import datetime, timezone

CONVERSATIONS_FILE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', 'data', 'conversations.json')
)

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

def _load_conversations() -> list:
    try:
        if os.path.exists(CONVERSATIONS_FILE):
            with open(CONVERSATIONS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return []
    except Exception:
        return []

def _save_conversations(conversations: list):
    os.makedirs(os.path.dirname(CONVERSATIONS_FILE), exist_ok=True)
    with open(CONVERSATIONS_FILE, 'w', encoding='utf-8') as f:
        json.dump(conversations, f, indent=2, ensure_ascii=False)

def start_conversation(session_id: str) -> dict:
    """Initialize a new conversation record."""
    conversations = _load_conversations()

    # Check if session already exists
    for conv in conversations:
        if conv["session_id"] == session_id:
            return conv

    conversation = {
        "session_id": session_id,
        "started_at": time.time(),
        "ended_at": None,
        "message_count": 0,
        "intents": [],
        "primary_intent": "general",
        "messages": [],
        "resolved": None,          # None=unknown, True=resolved, False=unresolved
        "resolution_source": None, # "customer", "keyword", "ai"
        "rating": None,
        "feedback_comment": "",
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "hour": datetime.now(timezone.utc).hour
    }

    conversations.append(conversation)
    _save_conversations(conversations)
    return conversation

def add_message_to_conversation(session_id: str, role: str,
                                content: str, intent: str = None):
    """Add a message to an existing conversation."""
    conversations = _load_conversations()

    for i, conv in enumerate(conversations):
        if conv["session_id"] == session_id:
            conversations[i]["messages"].append({
                "role": role,
                "content": content,
                "timestamp": time.time()
            })
            conversations[i]["message_count"] += 1
            conversations[i]["ended_at"] = time.time()

            if intent and intent not in conversations[i]["intents"]:
                conversations[i]["intents"].append(intent)
                # Primary intent is the first detected
                if not conversations[i]["primary_intent"] or \
                   conversations[i]["primary_intent"] == "general":
                    conversations[i]["primary_intent"] = intent

            # Auto keyword detection on customer messages
            if role == "user":
                content_lower = content.lower()
                if any(kw in content_lower for kw in RESOLUTION_KEYWORDS_YES):
                    if conversations[i]["resolved"] is None:
                        conversations[i]["resolved"] = True
                        conversations[i]["resolution_source"] = "keyword"
                elif any(kw in content_lower for kw in RESOLUTION_KEYWORDS_NO):
                    conversations[i]["resolved"] = False
                    conversations[i]["resolution_source"] = "keyword"

            _save_conversations(conversations)
            return conversations[i]

    # Session not found — create it first
    start_conversation(session_id)
    return add_message_to_conversation(session_id, role, content, intent)

def set_resolution(session_id: str, resolved: bool) -> bool:
    """Set resolution from customer Yes/No click — highest priority source."""
    conversations = _load_conversations()
    for i, conv in enumerate(conversations):
        if conv["session_id"] == session_id:
            conversations[i]["resolved"] = resolved
            conversations[i]["resolution_source"] = "customer"
            conversations[i]["ended_at"] = time.time()
            _save_conversations(conversations)
            return True
    return False

def set_rating(session_id: str, rating: int, comment: str = "") -> bool:
    """Attach star rating to a conversation."""
    conversations = _load_conversations()
    for i, conv in enumerate(conversations):
        if conv["session_id"] == session_id:
            conversations[i]["rating"] = rating
            conversations[i]["feedback_comment"] = comment
            _save_conversations(conversations)
            return True
    return False

def get_conversation(session_id: str) -> dict | None:
    conversations = _load_conversations()
    for conv in conversations:
        if conv["session_id"] == session_id:
            return conv
    return None

def get_all_conversations() -> list:
    return _load_conversations()

def get_analytics_data() -> dict:
    """
    Aggregate all conversation data for the analytics dashboard.
    Returns stats for today, this week, and this month.
    """
    conversations = _load_conversations()
    now = time.time()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Time windows
    one_day = 86400
    one_week = 604800
    one_month = 2592000

    today_convs = [c for c in conversations
                   if now - c.get("started_at", 0) <= one_day]
    week_convs = [c for c in conversations
                  if now - c.get("started_at", 0) <= one_week]
    month_convs = [c for c in conversations
                   if now - c.get("started_at", 0) <= one_month]

    def resolution_rate(convs):
        resolved = [c for c in convs if c.get("resolved") is not None]
        if not resolved:
            return None
        rate = len([c for c in resolved if c.get("resolved")]) / len(resolved)
        return round(rate * 100, 1)

    def avg_messages(convs):
        if not convs:
            return 0
        return round(sum(c.get("message_count", 0) for c in convs) / len(convs), 1)

    def avg_rating(convs):
        rated = [c.get("rating") for c in convs if c.get("rating")]
        if not rated:
            return None
        return round(sum(rated) / len(rated), 1)

    def intent_breakdown(convs):
        counts = {}
        for c in convs:
            intent = c.get("primary_intent", "general")
            counts[intent] = counts.get(intent, 0) + 1
        return counts

    def hourly_breakdown(convs):
        hours = {str(h): 0 for h in range(24)}
        for c in convs:
            hour = str(c.get("hour", 0))
            hours[hour] = hours.get(hour, 0) + 1
        return hours

    def recent_feedback(convs, limit=10):
        with_feedback = [c for c in convs
                        if c.get("feedback_comment") or c.get("rating")]
        sorted_fb = sorted(with_feedback,
                          key=lambda x: x.get("ended_at", 0), reverse=True)
        return [{
            "session_id": c["session_id"][:8],
            "rating": c.get("rating"),
            "comment": c.get("feedback_comment", ""),
            "intent": c.get("primary_intent", "general"),
            "resolved": c.get("resolved"),
            "date": c.get("date", "")
        } for c in sorted_fb[:limit]]

    def unresolved_conversations(convs, limit=10):
        unresolved = [c for c in convs if c.get("resolved") is False]
        sorted_ur = sorted(unresolved,
                          key=lambda x: x.get("ended_at", 0), reverse=True)
        return [{
            "session_id": c["session_id"][:8],
            "intent": c.get("primary_intent", "general"),
            "message_count": c.get("message_count", 0),
            "last_message": c["messages"][-1]["content"][:100]
                           if c.get("messages") else "",
            "date": c.get("date", "")
        } for c in sorted_ur[:limit]]

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
        "hourly_breakdown": hourly_breakdown(week_convs),
        "recent_feedback": recent_feedback(conversations),
        "unresolved": unresolved_conversations(conversations)
    }
