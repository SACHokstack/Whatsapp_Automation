from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import re


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

TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "fees": ("fee", "fees", "price", "pricing", "cost", "how much"),
    "schedule": ("schedule", "date", "dates", "when", "timing", "intake"),
    "venue": ("venue", "where", "location", "site", "ibis", "petaling jaya"),
    "payment": ("payment", "pay", "installment", "installments", "bank transfer", "invoice", "quotation"),
    "cancellation": ("cancel", "cancellation", "reschedule", "refund"),
    "certification": ("certificate", "certification", "certificates"),
    "trainers": ("trainer", "trainers", "trainer profile", "trainer discussion", "who is the trainer"),
    "placement": ("placement", "career", "job support", "post-training", "opportunity"),
    "requirements": ("requirement", "requirements", "prerequisite", "prerequisites", "need to"),
    "hrdc": ("hrdc", "claimable", "grant", "approval", "portal"),
}

STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "to",
    "for",
    "of",
    "is",
    "are",
    "i",
    "you",
    "we",
    "my",
    "your",
    "can",
    "do",
    "does",
    "what",
    "when",
    "where",
    "how",
    "please",
    "tell",
    "me",
    "about",
    "with",
    "this",
    "that",
    "be",
    "it",
    "on",
    "in",
}


@lru_cache(maxsize=1)
def load_knowledge_base() -> dict[str, str]:
    kb: dict[str, str] = {}
    if not _KNOWLEDGE_DIR.exists():
        return kb

    for path in _KNOWLEDGE_DIR.glob("*.md"):
        kb[path.stem.lower()] = path.read_text(encoding="utf-8").strip()
    return kb


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _tokenize(text: str) -> set[str]:
    tokens = set(re.findall(r"[a-z0-9]+", _normalize(text)))
    return {token for token in tokens if token not in STOPWORDS}


def _topic_for_message(message: str) -> str | None:
    msg_lower = _normalize(message)
    for topic, keywords in TOPIC_KEYWORDS.items():
        if any(keyword in msg_lower for keyword in keywords):
            return topic
    return None


def topic_for_message(message: str) -> str | None:
    topic = _topic_for_message(message)
    if topic:
        return topic
    return _best_search_topic(message)


def _best_search_topic(message: str) -> str | None:
    kb = load_knowledge_base()
    if not kb:
        return None

    query_tokens = _tokenize(message)
    if not query_tokens:
        return None

    best_topic = None
    best_score = 0
    for topic, text in kb.items():
        topic_tokens = _tokenize(topic) | _tokenize(text)
        score = len(query_tokens & topic_tokens)
        if score > best_score:
            best_score = score
            best_topic = topic

    if best_score >= 2:
        return best_topic
    return None


def answer_for_message(message: str) -> str:
    msg_lower = (message or "").strip().lower()
    for keyword, reply in KEYWORD_FALLBACKS:
        if msg_lower == keyword or msg_lower.startswith(f"{keyword} "):
            return reply

    kb = load_knowledge_base()
    topic = topic_for_message(message)
    if topic and topic in kb:
        return kb[topic]

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
