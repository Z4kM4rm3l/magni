"""
core/rate_limit.py

Small in-process sliding-window limiter for login/signup and the public widget.
Process-local by design (Magni runs one web process); move to Redis if it ever
runs several.
"""
import time
import threading
from collections import defaultdict, deque

_lock = threading.Lock()
_hits: dict[str, deque] = defaultdict(deque)
_MAX_KEYS = 50_000


def hit(key: str, limit: int, window_seconds: int) -> bool:
    """Record one attempt for key. Returns False if it exceeds limit per window."""
    now = time.time()
    with _lock:
        if len(_hits) > _MAX_KEYS:
            _hits.clear()  # crude memory guard; limits reset, nothing breaks
        q = _hits[key]
        while q and now - q[0] > window_seconds:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


def reset() -> None:
    with _lock:
        _hits.clear()
