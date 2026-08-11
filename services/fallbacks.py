"""User-facing recovery copy for unanswered questions.

Low-level RAG components may still report ``not_found`` for observability and tests.
Only this module turns that internal state into conversational copy, so every
customer-facing path offers a useful next step instead of a dead end.
"""

from __future__ import annotations


def _consultant_contact() -> str:
    """Company phone/email for the consultant, from the approved policies file."""
    try:
        from services.structured_facts import load_policies

        company = load_policies()["company"]
        return f"{company['phone']} or email {company['email']}"
    except Exception:  # noqa: BLE001 — copy must never fail the reply path
        return "our office line"


def warm_fallback(course=None) -> str:
    subject = f" for {course.name}" if course is not None else ""
    return (
        f"I don't have that specific detail confirmed{subject}, but our consultant can confirm "
        f"it for you — you can reach them directly at {_consultant_contact()}."
    )


def generation_unavailable(course=None) -> str:
    """Truthful recovery copy when evidence exists but the LLM provider failed."""
    subject = f" for {course.name}" if course is not None else ""
    return (
        f"I have the course information{subject}, but the answer service is temporarily "
        "unavailable. Please try that question again in a moment."
    )
