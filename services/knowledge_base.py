from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_KNOWLEDGE_DIR = _ROOT / "knowledge"

DEFAULT_GENERAL_REPLY = "Thank you for your interest. A consultant will contact you shortly."

KEYWORD_FALLBACKS = [
    ("hi", "Hi, this is Timmins Training. How can we help you today?"),
    ("hello", "Hi, this is Timmins Training. How can we help you today?"),
    ("hey", "Hi, this is Timmins Training. How can we help you today?"),
]

# Shared KB topics only — course-specific topics (fees, schedule, venue, course content)
# now live in courses/<slug>/config.yaml and courses/<slug>/overview.md
SHARED_TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "company": (
        "who is timmins",
        "about timmins",
        "timmins training",
        "what is timmins",
        "your company",
        "clients",
        "industries",
        "vendor registration",
        "vendor",
        "contact timmins",
        "your clients",
        "contact",
        "email",
        "phone number",
        "how to reach",
        "reach you",
        "get in touch",
        "your email",
        "your number",
        "your phone",
        "whatsapp number",
        "call you",
    ),
    "payment": (
        "payment",
        "pay",
        "installment",
        "installments",
        "bank transfer",
        "invoice",
        "quotation",
    ),
    "cancellation": ("cancel", "cancellation", "reschedule", "refund"),
    "certification": (
        "certificate",
        "certification",
        "certificates",
        "what is included",
        "whats included",
    ),
    "trainers": (
        "trainer",
        "trainers",
        "trainer profile",
        "trainer discussion",
        "who is the trainer",
    ),
    "placement": ("placement", "career", "job support", "post-training", "opportunity"),
    "requirements": (
        "requirement",
        "requirements",
        "prerequisite",
        "prerequisites",
        "background",
        "laptop",
        "bring my own",
        "beginner",
    ),
    "hrdc": (
        "hrdc",
        "claimable",
        "grant",
        "hrdc approval",
        "portal",
        "sponsor",
        "sponsored",
        "hrdc funding",
        "unemployed",
    ),
    "online": ("online", "virtual", "remote", "zoom", "google meet"),
    "batch_size": ("batch size", "batch", "students per batch", "class size", "how many students"),
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


def _strip_internal_notes(text: str) -> str:
    parts = re.split(r"^\s*---\s*$", text, maxsplit=1, flags=re.MULTILINE)
    return parts[0].strip()


def knowledge_content_version() -> tuple[tuple[str, int, int], ...]:
    """Fingerprint knowledge files so edits invalidate the local cache."""
    if not _KNOWLEDGE_DIR.exists():
        return ()
    return tuple(
        (path.name, path.stat().st_mtime_ns, path.stat().st_size)
        for path in sorted(_KNOWLEDGE_DIR.glob("*.md"))
    )


@lru_cache(maxsize=4)
def _load_knowledge_base_cached(_version) -> dict[str, str]:
    kb: dict[str, str] = {}
    if not _KNOWLEDGE_DIR.exists():
        return kb
    for path in _KNOWLEDGE_DIR.glob("*.md"):
        raw = path.read_text(encoding="utf-8").strip()
        kb[path.stem.lower()] = _strip_internal_notes(raw)
    return kb


def load_knowledge_base() -> dict[str, str]:
    return _load_knowledge_base_cached(knowledge_content_version())


def refresh_knowledge_base() -> dict[str, str]:
    """Reload shared knowledge files without restarting the process."""
    _load_knowledge_base_cached.cache_clear()
    return load_knowledge_base()


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _tokenize(text: str) -> set[str]:
    tokens = set(re.findall(r"[a-z0-9]+", _normalize(text)))
    return {t for t in tokens if t not in STOPWORDS}


def _ranked_shared_topics(message: str, limit: int = 3) -> list[str]:
    kb = load_knowledge_base()
    if not kb:
        return []

    msg_lower = _normalize(message)
    query_tokens = _tokenize(message)
    scored: list[tuple[int, str]] = []

    for topic, text in kb.items():
        score = 0
        for keyword in SHARED_TOPIC_KEYWORDS.get(topic, ()):
            if keyword in msg_lower:
                score += 3
        score += len(query_tokens & _tokenize(topic))
        score += len(query_tokens & _tokenize(text))
        if score > 0:
            scored.append((score, topic))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return [topic for _, topic in scored[:limit]]


def search_knowledge(query: str, limit: int = 3) -> list[dict[str, str]]:
    """Return top matching shared KB docs for a query."""
    kb = load_knowledge_base()
    topics = _ranked_shared_topics(query, limit=limit)
    return [{"topic": t, "content": kb[t]} for t in topics if kb.get(t)]


def topic_for_message(message: str) -> str | None:
    """Return the best matching shared KB topic label (for intent logging)."""
    msg_lower = _normalize(message)
    for topic, keywords in SHARED_TOPIC_KEYWORDS.items():
        if any(kw in msg_lower for kw in keywords):
            return topic
    topics = _ranked_shared_topics(message, limit=1)
    return topics[0] if topics else None


def build_rag_context(message: str, course=None) -> str:
    """
    Build the full RAG context string to pass to the LLM.
    Includes: course config facts + course overview + relevant shared KB docs.
    """
    parts: list[str] = []

    if course is not None:
        from services.course_loader import course_context_text

        parts.append(course_context_text(course))
        if course.overview:
            parts.append(f"[COURSE OVERVIEW]\n{course.overview}")

    shared_docs = search_knowledge(message, limit=2)
    for doc in shared_docs:
        parts.append(f"[{doc['topic'].upper()}]\n{doc['content']}")

    return "\n\n".join(parts)
