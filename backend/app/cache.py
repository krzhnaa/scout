import time
import hashlib
import threading
from typing import Any, Optional


class TTLCache:
    """Tiny thread-safe in-memory cache. Keeps demo runs fast on repeat queries
    and shields you from burning free-tier rate limits while rehearsing."""

    def __init__(self, ttl_seconds: int = 3600):
        self.ttl = ttl_seconds
        self._store: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def key_for(*parts: str) -> str:
        raw = "||".join(parts)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._store.get(key)
            if not entry:
                return None
            expires_at, value = entry
            if time.time() > expires_at:
                del self._store[key]
                return None
            return value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._store[key] = (time.time() + self.ttl, value)
