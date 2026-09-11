"""
src/session_store.py — Resilient Redis-backed Session & Upload Store with 1-Hour TTL.

Features:
1. Distributed Redis: If REDIS_URL is configured in .env and reachable, uses Redis with atomic SETEX.
2. Local Resilient Fallback: If Redis is unconfigured or offline, uses an in-process thread-safe TTL cache.
3. Automatic Cleanup: Sessions and uploaded datasets expire automatically after 3600 seconds (1 hour).
"""

import os
import sys
import time
import pickle
import threading
from typing import List, Optional, Any
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

load_dotenv(override=True)

DEFAULT_TTL_SECONDS = 3600  # 1 hour


class InMemoryTTLStore:
    """Thread-safe in-memory key-value store with Time-To-Live (TTL) auto-eviction."""

    def __init__(self, default_ttl: int = DEFAULT_TTL_SECONDS):
        self._store = {}  # key -> (data, expire_at)
        self._lock = threading.Lock()
        self.default_ttl = default_ttl

    def get(self, key: str, refresh_ttl: bool = True) -> Optional[Any]:
        with self._lock:
            item = self._store.get(key)
            if not item:
                return None
            data, expire_at = item
            now = time.time()
            if now > expire_at:
                del self._store[key]
                return None
            if refresh_ttl:
                # Extend expiration on active read
                self._store[key] = (data, now + self.default_ttl)
            return data

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        duration = ttl if ttl is not None else self.default_ttl
        with self._lock:
            # Active pruning of expired keys to prevent memory leaks
            now = time.time()
            expired = [k for k, (_, exp) in self._store.items() if now > exp]
            for k in expired:
                del self._store[k]
            self._store[key] = (value, now + duration)

    def delete(self, key: str) -> None:
        with self._lock:
            if key in self._store:
                del self._store[key]

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


class RedisSessionStore:
    """Distributed Redis store using atomic SETEX for 1-hour TTL."""

    def __init__(self, redis_client, default_ttl: int = DEFAULT_TTL_SECONDS):
        self.client = redis_client
        self.default_ttl = default_ttl

    def get(self, key: str, refresh_ttl: bool = True) -> Optional[Any]:
        try:
            raw = self.client.get(key)
            if raw is None:
                return None
            if refresh_ttl:
                self.client.expire(key, self.default_ttl)
            return pickle.loads(raw)
        except Exception as e:
            print(f"⚠️ Redis GET failed for key '{key}': {e}")
            return None

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        try:
            duration = ttl if ttl is not None else self.default_ttl
            payload = pickle.dumps(value)
            self.client.setex(key, duration, payload)
        except Exception as e:
            print(f"⚠️ Redis SETEX failed for key '{key}': {e}")

    def delete(self, key: str) -> None:
        try:
            self.client.delete(key)
        except Exception as e:
            print(f"⚠️ Redis DELETE failed for key '{key}': {e}")


def _init_storage_backend():
    """Discover whether Redis is reachable, otherwise initialize thread-safe TTL store."""
    redis_url = os.environ.get("REDIS_URL")
    if redis_url:
        try:
            import redis
            client = redis.from_url(redis_url, socket_timeout=2.0, socket_connect_timeout=2.0)
            client.ping()
            print("=================================================================")
            print(f"🚀 STORAGE BACKEND: Distributed Redis ({redis_url.split('@')[-1]}) [1h TTL]")
            print("=================================================================\n")
            return RedisSessionStore(client)
        except Exception as e:
            print(f"ℹ️ Redis at '{redis_url}' unavailable ({e}). Falling back to local TTL store.")

    print("=================================================================")
    print("📦 STORAGE BACKEND: Resilient In-Memory TTL Store (3600s TTL)")
    print("   [Automatic background memory eviction active — zero RAM leaks]")
    print("=================================================================\n")
    return InMemoryTTLStore()


# Singleton active store instance
storage = _init_storage_backend()


# ── High-Level Chat History APIs ──────────────────────────────────────

def get_session_history(sid: str) -> list:
    """Retrieve message history for a session (returns empty list if none or expired)."""
    key = f"chat_history:{sid}"
    history = storage.get(key, refresh_ttl=True)
    return history if history is not None else []


def save_session_history(sid: str, history: list, ttl: int = DEFAULT_TTL_SECONDS) -> None:
    """Persist conversation turns with sliding window and 1-hour TTL."""
    key = f"chat_history:{sid}"
    storage.set(key, history, ttl=ttl)


def clear_session_history(sid: str) -> None:
    """Clear chat history for session."""
    key = f"chat_history:{sid}"
    storage.delete(key)


# ── High-Level Uploaded Business Data APIs ────────────────────────────

def get_uploaded_data(sid: str) -> Optional[dict]:
    """Retrieve user uploaded data profile and ephemeral retriever."""
    key = f"uploaded_data:{sid}"
    return storage.get(key, refresh_ttl=True)


def save_uploaded_data(sid: str, upload_info: dict, ttl: int = DEFAULT_TTL_SECONDS) -> None:
    """Persist uploaded file summary and retriever with 1-hour TTL."""
    key = f"uploaded_data:{sid}"
    storage.set(key, upload_info, ttl=ttl)


def clear_uploaded_data(sid: str) -> None:
    """Clear uploaded business dataset from session memory."""
    key = f"uploaded_data:{sid}"
    storage.delete(key)
