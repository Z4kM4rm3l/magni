INTENT_KEYWORDS = {
    "billing": [
        "bill", "charge", "payment", "invoice", "refund", "price",
        "cost", "fee", "subscription", "cancel", "upgrade", "downgrade"
    ],
    "technical": [
        "error", "bug", "broken", "crash", "not working", "issue",
        "problem", "fix", "help", "support", "install", "setup", "login"
    ],
    "product": [
        "feature", "how to", "how do", "what is", "explain", "use",
        "product", "service", "works", "does", "can you", "capability"
    ],
    "orders": [
        "order", "shipping", "delivery", "track", "package", "arrive",
        "status", "purchase", "bought", "return", "exchange"
    ],
    "feedback": [
        "feedback", "suggest", "idea", "improve", "rating", "review",
        "complaint", "compliment", "experience", "opinion"
    ],
    "general": []
}

def classify_intent(message: str) -> str:
    message_lower = message.lower()
    scores = {intent: 0 for intent in INTENT_KEYWORDS}

    for intent, keywords in INTENT_KEYWORDS.items():
        for keyword in keywords:
            if keyword in message_lower:
                scores[intent] += 1

    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "general"
