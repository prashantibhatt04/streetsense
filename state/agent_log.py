"""
Thread-safe in-process agent log buffer.
Agents import and call agent_log.append("message").
The /api/log endpoint drains and returns entries to the dashboard.
"""
import threading
from collections import deque
from datetime import datetime, timezone

_lock = threading.Lock()
_buffer: deque = deque(maxlen=100)


def append(message: str) -> None:
    with _lock:
        _buffer.append({
            "ts": datetime.now(timezone.utc).strftime("%H:%M:%S"),
            "msg": message,
        })


def drain() -> list[dict]:
    """Return all buffered entries and clear. Called by /api/log."""
    with _lock:
        entries = list(_buffer)
        _buffer.clear()
        return entries


def peek() -> list[dict]:
    """Return entries without clearing (for polling without loss)."""
    with _lock:
        return list(_buffer)


def clear() -> None:
    with _lock:
        _buffer.clear()
