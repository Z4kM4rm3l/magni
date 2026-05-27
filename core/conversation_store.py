import os
import json
import time
from datetime import datetime, timezone
from core.utils import logger

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

def start_conversation(session_id: str, initial_intent: str = "general") -> dict:
    conversations = _load_conversations()
    
    # Check if session exists
    for c in conversations:
        if c["session_id"] == session_id:
            return c

    now = datetime.now(timezone.utc)
    new_conv = {
        "session_id": session_id,
        "started_at": time.time(),
        "ended_at": None,
        "message_count": 0,
        "intents": [initial_intent],
        "primary_intent": initial_intent,
        "messages": [],
        "resolved": None,
        "resolution_source": None,
        "resolution_confidence": None,       # Enhancement A: Confidence Tracking
        "resolution_last_user": None,         # Enhancement B: Direct exchange logging
        "resolution_last_agent": None,        # Enhancement B: Direct exchange logging
        "rating": None,
        "feedback_comment": "",
        "date": now.strftime("%Y-%m-%d"),
        "hour": now.hour
    }
    
    conversations.append(new_conv)
    _save_conversations(conversations)
    return new_conv

def add_message_to_conversation(session_id: str, role: str, content: str, current_intent: str = None) -> bool:
    conversations = _load_conversations()
    for i, c in enumerate(conversations):
        if c["session_id"] == session_id:
            conversations[i]["messages"].append({
                "role": role,
                "content": content,
                "timestamp": time.time()
            })
            conversations[i]["message_count"] = len(conversations[i]["messages"])
            
            if current_intent and current_intent not in conversations[i]["intents"]:
                conversations[i]["intents"].append(current_intent)
                # Simple majority/first intent fallback for primary selection
                conversations[i]["primary_intent"] = conversations[i]["intents"][0]
                
            _save_conversations(conversations)
            return True
    return False

def evaluate_resolution_with_ai(last_user: str, last_agent: str) -> bool:
    """
    Asymmetric LLM Fallback: Safely determines if the sentiment implies a clear resolution.
    Returns True ONLY if explicitly positive; defaults to False on any uncertainty or failure.
    """
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

def set_resolution(session_id: str, client_resolved: bool = None) -> dict:
    """
    Executes the Hybrid Resolution Analytics Pipeline (Option C).
    Extracts high-signal analytics data, confidence ratings, and final contexts.
    """
    conversations = _load_conversations()
    for i, c in enumerate(conversations):
        if c["session_id"] == session_id:
            messages = c.get("messages", [])
            
            # Extract final text assets safely for Enhancement B
            last_user_text = ""
            last_agent_text = ""
            
            user_turns = [m["content"] for m in messages if m["role"] == "user"]
            agent_turns = [m["content"] for m in messages if m["role"] == "assistant"]
            
            if user_turns:
                last_user_text = user_turns[-1]
            if agent_turns:
                last_agent_text = agent_turns[-1]
                
            conversations[i]["resolution_last_user"] = last_user_text or None
            conversations[i]["resolution_last_agent"] = last_agent_text or None

            # --- STEP 1: Explicit Client Override ---
            if client_resolved is not None:
                conversations[i]["resolved"] = client_resolved
                conversations[i]["resolution_source"] = "user_explicit"  # Enhancement C: Enum Match
                conversations[i]["resolution_confidence"] = 1.0          # Enhancement A: Absolute certainty
                conversations[i]["ended_at"] = time.time()
                _save_conversations(conversations)
                logger.info(f"Resolution saved via explicit UI action for session {session_id[:8]}. Value: {client_resolved}")
                return conversations[i]

            # --- EDGE CASE CHECK: No actual context to process ---
            if not messages:
                conversations[i]["resolved"] = False
                conversations[i]["resolution_source"] = "no_data"
                conversations[i]["resolution_confidence"] = 0.0
                _save_conversations(conversations)
                return conversations[i]
                
            if not last_user_text:
                conversations[i]["resolved"] = False
                conversations[i]["resolution_source"] = "no_user_turn"
                conversations[i]["resolution_confidence"] = 0.0
                _save_conversations(conversations)
                return conversations[i]

            # --- STEP 2: Keyword Heuristics ---
            user_text_lower = last_user_text.lower()
            
            # Check negative indicators first to lower false positive metrics
            if any(kw in user_text_lower for kw in RESOLUTION_KEYWORDS_NO):
                conversations[i]["resolved"] = False
                conversations[i]["resolution_source"] = "keyword_no"      # Enhancement C: Clean Enum
                conversations[i]["resolution_confidence"] = 1.0          # Absolute negative certainty
                conversations[i]["ended_at"] = time.time()
                _save_conversations(conversations)
                logger.info(f"Resolution logic matched negative heuristic for session {session_id[:8]}.")
                return conversations[i]

            # Check positive indicators next
            if any(kw in user_text_lower for kw in RESOLUTION_KEYWORDS_YES):
                conversations[i]["resolved"] = True
                conversations[i]["resolution_source"] = "keyword_yes"     # Enhancement C: Clean Enum
                conversations[i]["resolution_confidence"] = 0.90         # Keywords are highly accurate, but not 100%
                conversations[i]["ended_at"] = time.time()
                _save_conversations(conversations)
                logger.info(f"Resolution logic matched positive heuristic for session {session_id[:8]}.")
                return conversations[i]

            # --- STEP 3: Asymmetric LLM Fallback Engine ---
            ai_verdict = evaluate_resolution_with_ai(last_user_text, last_agent_text)
            conversations[i]["resolved"] = ai_verdict
            conversations[i]["resolution_source"] = "ai_fallback"        # Enhancement C: Clean Enum
            conversations[i]["resolution_confidence"] = 0.85             # Enhancement A: Scaled for model accuracy
            conversations[i]["ended_at"] = time.time()
            
            _save_conversations(conversations)
            logger.info(f"Resolution telemetry saved for session {session_id[:8]}. Source: ai_fallback, Result: {ai_verdict}")
            return conversations[i]
            
    return {}

def set_rating(session_id: str, rating: int, comment: str = "") -> bool:
    conversations = _load_conversations()
    for i, c in enumerate(conversations):
        if c["session_id"] == session_id:
            conversations[i]["rating"] = rating
            conversations[i]["feedback_comment"] = comment
            _save_conversations(conversations)
            return True
    return False

def get_all_conversations() -> list:
    return _load_conversations()

# --- ANALYTICS DASHBOARD PRE-AGGREGATION ---
def get_analytics_data() -> dict:
    conversations = _load_conversations()
    
    now = time.time()
    one_day = 86400
    one_week = 604800
    one_month = 2592000
    
    today_convs = [c for c in conversations if now - c.get("started_at", 0) <= one_day]
    week_convs = [c for c in conversations if now - c.get("started_at", 0) <= one_week]
    month_convs = [c for c in conversations if now - c.get("started_at", 0) <= one_month]
    
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
    
    # Enhanced unresolved payload snippet using the new fields
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