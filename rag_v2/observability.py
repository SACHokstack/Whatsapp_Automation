from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_TRACE_PATH = Path(os.getenv("RAG_TRACE_PATH", "rag_traces/retrieval.jsonl"))


def utc_ms() -> int:
    return int(time.time() * 1000)


def write_retrieval_trace(record: dict[str, Any]) -> None:
    """Append a JSONL retrieval trace; never fail the user path because tracing failed."""
    try:
        _TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _TRACE_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception:
        logger.exception("event=rag_trace_write_failed")
