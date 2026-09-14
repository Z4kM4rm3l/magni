"""Standalone tenant-ownership tests for the conversation store and the
in-memory conversation manager. No Postgres required (isolated SQLite) and no
Gemini calls. Run: python tests/test_conversation_ownership.py
"""
import os, sys, types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Stub Gemini so importing the store (and its AI resolution fallback) needs no network.
_g = types.ModuleType("google"); _ga = types.ModuleType("google.generativeai")
_ga.configure = lambda **k: None
class _StubModel:
    def __init__(self, *a, **k): pass
    def generate_content(self, *a, **k): return types.SimpleNamespace(text="NO")
_ga.GenerativeModel = _StubModel
sys.modules["google"] = _g; sys.modules["google.generativeai"] = _ga

# Bind an isolated in-memory SQLite into core.db BEFORE importing the store.
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import core.db as db
db.engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
db.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=db.engine)

import core.models as models
models.Base.metadata.create_all(bind=db.engine)

import core.conversation_store as store
from core.conversation_manager import ConversationManager
from datetime import datetime, timezone

# Seed two tenants.
_s = db.SessionLocal()
for cid in ("client_a", "client_b"):
    _s.add(models.Client(id=cid, api_key="sk_" + cid, business_name=cid, is_active=True,
                         tier="starter", monthly_used=0, created_at=datetime.now(timezone.utc)))
_s.commit(); _s.close()

failures = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)

SID = "shared-session-id"
store.start_conversation("client_a", SID)  # client_a owns it

# Durable store: client_b must not be able to touch client_a's conversation.
check("B cannot claim A's session", store.start_conversation("client_b", SID) == {})
check("B cannot append to A's conversation", store.add_message_to_conversation("client_b", SID, "user", "hi") is False)
check("B cannot resolve A's conversation", store.set_resolution("client_b", SID, True) == {})
check("B cannot rate A's conversation", store.set_rating("client_b", SID, 5) is False)

# client_a can operate on its own conversation.
check("A can append to its conversation", store.add_message_to_conversation("client_a", SID, "user", "thanks that helped") is True)
check("A can resolve its conversation", store.set_resolution("client_a", SID, True).get("resolved") is True)
check("A can rate its conversation", store.set_rating("client_a", SID, 5, "great") is True)

# In-memory manager: same session_id under different tenants must NOT share history.
mgr = ConversationManager()
mgr.add_message(f"client_a:{SID}", "user", "A-only secret context")
check("B does not receive A's in-memory history (same session_id)",
      mgr.get_history(f"client_b:{SID}") == [])
check("A still sees its own in-memory history",
      any(m["content"] == "A-only secret context" for m in mgr.get_history(f"client_a:{SID}")))

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("ALL PASSED")
sys.exit(0)
