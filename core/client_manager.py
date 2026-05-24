import os
import json
import time
import uuid

CLIENTS_FILE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', 'data', 'clients.json')
)

TIER_LIMITS = {
    "starter": {
        "conversation_limit": 500,
        "kb_article_limit": 10,
        "custom_identity": False,
        "custom_domain": False,
        "white_label": False,
        "analytics": "basic"
    },
    "professional": {
        "conversation_limit": 2000,
        "kb_article_limit": 50,
        "custom_identity": True,
        "custom_domain": True,
        "white_label": False,
        "analytics": "full"
    },
    "enterprise": {
        "conversation_limit": 5000,
        "kb_article_limit": -1,  # unlimited
        "custom_identity": True,
        "custom_domain": True,
        "white_label": True,
        "analytics": "full"
    }
}

def _load_clients() -> list:
    try:
        if os.path.exists(CLIENTS_FILE):
            with open(CLIENTS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return []
    except Exception:
        return []

def _save_clients(clients: list):
    os.makedirs(os.path.dirname(CLIENTS_FILE), exist_ok=True)
    with open(CLIENTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(clients, f, indent=2, ensure_ascii=False)

def create_client(business_name: str, email: str, tier: str,
                  bot_name: str = "Magni", api_key: str = "",
                  billing_date: str = "") -> dict:
    """Create a new client account."""
    clients = _load_clients()
    limits = TIER_LIMITS.get(tier, TIER_LIMITS["starter"])

    client = {
        "client_id": str(uuid.uuid4())[:8],
        "business_name": business_name,
        "email": email,
        "tier": tier,
        "status": "active",
        "bot_name": bot_name,
        "api_key": api_key,
        "billing_date": billing_date,
        "conversation_limit": limits["conversation_limit"],
        "conversations_used": 0,
        "kb_article_limit": limits["kb_article_limit"],
        "custom_identity": limits["custom_identity"],
        "white_label": limits["white_label"],
        "analytics": limits["analytics"],
        "created_at": time.time(),
        "updated_at": time.time(),
        "notes": ""
    }

    clients.append(client)
    _save_clients(clients)
    return client

def get_all_clients() -> list:
    clients = _load_clients()
    return sorted(clients, key=lambda x: x.get("created_at", 0), reverse=True)

def get_client(client_id: str) -> dict | None:
    clients = _load_clients()
    for client in clients:
        if client["client_id"] == client_id:
            return client
    return None

def update_client(client_id: str, **kwargs) -> dict | None:
    """Update any client fields."""
    clients = _load_clients()
    for i, client in enumerate(clients):
        if client["client_id"] == client_id:
            # If tier is changing, update limits automatically
            if "tier" in kwargs and kwargs["tier"] != client["tier"]:
                limits = TIER_LIMITS.get(kwargs["tier"], TIER_LIMITS["starter"])
                kwargs["conversation_limit"] = limits["conversation_limit"]
                kwargs["custom_identity"] = limits["custom_identity"]
                kwargs["white_label"] = limits["white_label"]
                kwargs["analytics"] = limits["analytics"]
                kwargs["kb_article_limit"] = limits["kb_article_limit"]

            kwargs["updated_at"] = time.time()
            clients[i].update(kwargs)
            _save_clients(clients)
            return clients[i]
    return None

def suspend_client(client_id: str) -> dict | None:
    """Suspend a client — bot stops responding."""
    return update_client(client_id, status="suspended")

def reactivate_client(client_id: str) -> dict | None:
    """Reactivate a suspended client."""
    return update_client(client_id, status="active",
                        conversations_used=0)  # Reset monthly count

def delete_client(client_id: str) -> bool:
    clients = _load_clients()
    original = len(clients)
    clients = [c for c in clients if c["client_id"] != client_id]
    if len(clients) < original:
        _save_clients(clients)
        return True
    return False

def increment_conversations(client_id: str) -> tuple[bool, str]:
    """
    Increment conversation count for a client.
    Returns (allowed, reason) — allowed=False means limit reached.
    """
    clients = _load_clients()
    for i, client in enumerate(clients):
        if client["client_id"] == client_id:
            if client["status"] != "active":
                return False, "Account suspended"

            limit = client.get("conversation_limit", 500)
            used = client.get("conversations_used", 0)

            # Enterprise overage — allow but track
            if client["tier"] == "enterprise" and used >= limit:
                overage = used - limit
                overage_cost = overage * 0.10
                if overage_cost >= 500:
                    return False, "Monthly overage cap reached ($500)"

            elif client["tier"] != "enterprise" and used >= limit:
                return False, f"Monthly conversation limit reached ({limit})"

            clients[i]["conversations_used"] = used + 1
            clients[i]["updated_at"] = time.time()
            _save_clients(clients)
            return True, "ok"

    return True, "ok"  # No client tracking — default allow

def reset_monthly_conversations() -> int:
    """Reset conversation counts for all active clients. Call on billing date."""
    clients = _load_clients()
    count = 0
    for i, client in enumerate(clients):
        if client["status"] == "active":
            clients[i]["conversations_used"] = 0
            count += 1
    _save_clients(clients)
    return count

def get_client_stats() -> dict:
    clients = _load_clients()
    active = [c for c in clients if c["status"] == "active"]
    suspended = [c for c in clients if c["status"] == "suspended"]

    mrr = sum(
        79 if c["tier"] == "starter"
        else 249 if c["tier"] == "professional"
        else 599
        for c in active
    )

    return {
        "total": len(clients),
        "active": len(active),
        "suspended": len(suspended),
        "mrr": mrr,
        "by_tier": {
            "starter": len([c for c in active if c["tier"] == "starter"]),
            "professional": len([c for c in active if c["tier"] == "professional"]),
            "enterprise": len([c for c in active if c["tier"] == "enterprise"])
        }
    }
