from __future__ import annotations

import re
from dataclasses import dataclass

from services.course_loader import get_active_courses
from services.structured_facts import load_policies


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    reason: str = ""


def validate_reply(reply: str, *, course=None, catalog: bool = False) -> ValidationResult:
    lower = reply.lower()
    if not reply.strip():
        return ValidationResult(False, "empty response")
    # Safety net against runaway generation only. Grounded curriculum / multi-part
    # answers are legitimately long; the RAG generator is already capped at ~500 tokens.
    if len(reply.split()) > 300:
        return ValidationResult(False, "response exceeds 300 words")
    for phrase in ("knowledge base", "system prompt", "language model", "rag context"):
        if phrase in lower:
            return ValidationResult(False, f"internal phrase: {phrase}")

    policies = load_policies()
    approved_domains = policies["company"].get("approved_domains") or []
    for url in re.findall(r"https?://[^\s]+", reply):
        if not any(domain in url.lower() for domain in approved_domains):
            return ValidationResult(False, "unapproved URL")

    # Retrieval now spans every course, so a reply may legitimately mention any
    # active course by name. We only guard against *invented* fees: any RM
    # figure must match some active course's real fee schedule.
    allowed_fees = {
        int(value)
        for candidate in get_active_courses()
        for value in candidate.fees.values()
        if value
    }
    mentioned_fees = {
        int(value.replace(",", ""))
        for value in re.findall(r"RM\s*([0-9][0-9,]*)", reply, re.IGNORECASE)
    }
    if mentioned_fees - allowed_fees:
        return ValidationResult(False, "invented fee")

    if "yes" in lower and "installment" in lower and "not" not in lower:
        return ValidationResult(False, "unsupported installment confirmation")
    return ValidationResult(True)
