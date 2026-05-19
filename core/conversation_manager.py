import threading
import time
from collections import defaultdict

SESSION_TTL = 3600  # 1 hour

class ConversationManager:
    def __init__(self):
        self._sessions = {}
        self._lock = threading.Lock()
        self._last_access = {}

    def get_history(self, session_id: str) -> list:
        with self._lock:
            self._last_access[session_id] = time.time()
            return self._sessions.get(session_id, [])

    def add_message(self, session_id: str, role: str, content: str):
        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = []
            self._sessions[session_id].append({
                "role": role,
                "content": content,
                "timestamp": time.time()
            })
            self._last_access[session_id] = time.time()

    def reset_session(self, session_id: str):
        with self._lock:
            self._sessions.pop(session_id, None)
            self._last_access.pop(session_id, None)

    def cleanup_expired(self):
        now = time.time()
        with self._lock:
            expired = [sid for sid, t in self._last_access.items()
                      if now - t > SESSION_TTL]
            for sid in expired:
                self._sessions.pop(sid, None)
                self._last_access.pop(sid, None)

conversation_manager = ConversationManager()
