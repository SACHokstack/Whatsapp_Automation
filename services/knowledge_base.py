from __future__ import annotations

from functools import lru_cache
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
_KNOWLEDGE_DIR = _ROOT / "knowledge"

INTENT_TO_TOPIC = {
    "FEES": "fees",
    "SCHEDULE": "schedule",
    "VENUE": "venue",
    "PAYMENT": "payment",
    "CANCELLATION": "cancellation",
    "CERTIFICATION": "certification",
    "TRAINERS": "trainers",
    "PLACEMENT": "placement",
    "REQUIREMENTS": "requirements",
    "HRDC_QUERY": "hrdc",
}

DEFAULT_GENERAL_REPLY = "Thank you for your interest. A consultant will contact you shortly."

KEYWORD_FALLBACKS = [
    ("hi", "Hi, this is Timmins Training. How can we help you today?"),
    ("hello", "Hi, this is Timmins Training. How can we help you today?"),
    ("hey", "Hi, this is Timmins Training. How can we help you today?"),
]


@lru_cache(maxsize=1)
def load_knowledge_base() -> dict[str, str]:
    kb: dict[str, str] = {}
    if not _KNOWLEDGE_DIR.exists():
        return kb

    for path in _KNOWLEDGE_DIR.glob("*.md"):
        kb[path.stem.lower()] = path.read_text(encoding="utf-8").strip()
    return kb


def answer_for_message(message: str) -> str:
    msg_lower = (message or "").strip().lower()
    for keyword, reply in KEYWORD_FALLBACKS:
        if msg_lower == keyword or msg_lower.startswith(f"{keyword} "):
            return reply
    return DEFAULT_GENERAL_REPLY


def answer_for_intent(intent: str) -> str:
    intent_upper = (intent or "").strip().upper()
    topic = INTENT_TO_TOPIC.get(intent_upper)
    if not topic:
        if intent_upper == "GENERAL":
            return DEFAULT_GENERAL_REPLY
        return DEFAULT_GENERAL_REPLY

    kb = load_knowledge_base()
    topic_text = kb.get(topic)
    if topic_text:
        return topic_text

    return DEFAULT_GENERAL_REPLY
