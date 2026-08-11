from __future__ import annotations

import re
import time

_cache: dict[str, tuple[str, float]] = {}
_TTL_SECONDS = 6 * 3600  # 6 hours
_MAX_ENTRIES = 1000


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (text or "").lower().strip())


def get(message: str) -> str | None:
    key = _normalize(message)
    if not key:
        return None
    entry = _cache.get(key)
    if entry and (time.time() - entry[1]) < _TTL_SECONDS:
        return entry[0]
    if entry:
        _cache.pop(key, None)
    return None


def set(message: str, reply: str) -> None:
    key = _normalize(message)
    if key:
        if len(_cache) >= _MAX_ENTRIES:
            oldest = min(_cache, key=lambda item: _cache[item][1])
            _cache.pop(oldest, None)
        _cache[key] = (reply, time.time())


def clear() -> None:
    """Clear cached AI replies after knowledge or course content changes."""
    _cache.clear()
