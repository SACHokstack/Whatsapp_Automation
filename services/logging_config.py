from __future__ import annotations

import hashlib
import logging
import os


def configure_logging() -> None:
    """Configure compact key/value logs suitable for hosted log collectors."""
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s level=%(levelname)s logger=%(name)s %(message)s",
    )


def subject_id(value: str) -> str:
    """Return a stable, non-reversible identifier for customer-related logs."""
    raw = str(value or "").encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12] if raw else "unknown"
