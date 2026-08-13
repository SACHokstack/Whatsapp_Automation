"""LLM-grounded course recommendation.

The *reasoning* — which course fits a person's background, goals, or constraints — is
delegated to the model, which already understands that (say) Python/Java point at test
automation and C/C++ point at embedded. We do not re-encode that knowledge as keyword
rules; that approach is whack-a-mole and never generalises.

The *facts* stay grounded: the model may only choose from the real catalogue (by slug),
and course names, dates, venues, and fees are rendered from the live course config — never
from the model's free text. Price-superlative questions ("which is cheapest") are answered
by a deterministic fee comparison, because that is a fact, not a judgement.

When the model is disabled or errors, a minimal deterministic fallback asks for the
person's background — no silent wrong pick.
"""

from __future__ import annotations

import logging
import os
import re

from services.course_loader import get_active_courses

logger = logging.getLogger(__name__)

_PRICE_SUPERLATIVE = re.compile(
    r"\b(?:cheap(?:est|er)?|affordable|inexpensive|budget|lowest|least expensive|"
    r"most expensive|priciest|dearest|highest)\b|\bcosts? (?:the )?(?:least|most)\b",
    re.IGNORECASE,
)
_WANT_MAX = re.compile(
    r"\b(?:most expensive|priciest|dearest|highest)\b|\bcosts? (?:the )?most\b", re.IGNORECASE
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "recommended": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string"},
        "clarify": {"type": "string"},
        "unavailable_topic": {"type": "string"},
    },
    "required": ["recommended", "reason", "clarify", "unavailable_topic"],
    "additionalProperties": False,
}

_SYSTEM_PROMPT = (
    "You recommend Timmins training courses. You are given the FULL list of currently "
    "available courses (slug, name, fee, keywords, and an overview covering who it is for "
    "and its prerequisites), plus the customer's message. Reason about which listed "
    "course(s) genuinely fit the customer's stated background, goals, or budget.\n"
    "Return ONLY these fields:\n"
    "- recommended: course SLUGS chosen strictly from the provided list, best first. "
    "IMPORTANT: if the customer's background points to MORE THAN ONE distinct domain, you "
    "MUST include a course for EACH such domain (2-3 slugs), not just the single strongest. "
    "For example someone who lists BOTH C/C++ (→ embedded) AND Python/Java (→ test "
    "automation) should get one embedded course AND the testing course — surfacing every "
    "genuinely relevant track is the goal, and the customer chooses. Use a single slug only "
    "when the background clearly points to one domain. Empty list only if there is truly no "
    "signal to go on.\n"
    "- reason: one short sentence on why these fit. Do NOT state dates, venues, or fees — "
    "those are added separately. Never mention a course that is not in the list.\n"
    "- clarify: when you returned courses from different domains, a short question that lets "
    "them narrow it (e.g. 'Are you aiming at test automation or embedded/firmware?'). "
    "Otherwise an empty string.\n"
    "- unavailable_topic: if the customer asked for a specific subject or course that is NOT "
    "in the list above (e.g. cyber security, web3, blockchain, data science, mobile app "
    "development, web development), put that subject here — so we can tell them honestly we "
    "don't offer it. Empty string if they didn't ask for a specific unavailable subject. Use "
    "your own knowledge to judge whether the subject is genuinely outside the listed courses.\n"
    "Never invent a course, slug, date, or fee."
)


def recommend_courses(message: str) -> str:
    """Return a grounded course recommendation for a free-text background/advice message."""
    courses = get_active_courses()
    if not courses:
        return "We don't have any courses scheduled at the moment."
    if _PRICE_SUPERLATIVE.search(message):
        return _fee_comparison(message, courses)
    picked = _llm_recommendation(message, courses)
    if picked is None:
        return _fallback(courses)
    return _render(picked, courses)


# ── deterministic fee comparison (a fact, not a judgement) ──────────────────────


def _fee_comparison(message: str, courses) -> str:
    priced = sorted(
        (
            (item, int(item.fees["standard"]))
            for item in courses
            if item.fees and item.fees.get("standard")
        ),
        key=lambda pair: pair[1],
    )
    if not priced:
        return _fallback(courses)
    want_max = bool(_WANT_MAX.search(message))
    target_fee = priced[-1][1] if want_max else priced[0][1]
    winners = [item for item, fee in priced if fee == target_fee]
    label = "most expensive" if want_max else "most affordable"
    names = " and ".join(item.name for item in winners)
    lead = (
        f"The {label} course is {names}, at RM{target_fee:,} per participant"
        if len(winners) == 1
        else f"The {label} courses are {names} — both RM{target_fee:,} per participant"
    )
    fee_lines = "\n".join(f"- {item.name}: RM{fee:,}" for item, fee in priced)
    return f"{lead}. Standard per-participant fees:\n{fee_lines}"


# ── LLM reasoning, grounded on the real catalogue ───────────────────────────────


def _catalog_context(courses) -> str:
    rows = []
    for item in courses:
        fee = item.fees.get("standard") if item.fees else None
        fee_text = f"RM{int(fee):,}" if fee else "not listed"
        overview = " ".join((item.overview or "").split())[:600]
        keywords = ", ".join(item.keywords[:12])
        rows.append(
            f"- slug: {item.slug}\n  name: {item.name}\n  fee: {fee_text}\n"
            f"  keywords: {keywords}\n  overview: {overview}"
        )
    return "\n".join(rows)


def _llm_recommendation(message: str, courses):
    """Ask the model to pick fitting course slugs. Returns {slugs, reason, clarify} or
    None (disabled / unsupported provider / error) so the caller can fall back."""
    from services.interpret import _enabled, _extract_json, _interpreter_settings

    if not _enabled():
        return None
    provider, api_key, model = _interpreter_settings()
    if provider not in {"bedrock", "openrouter"} or not api_key:
        return None  # groq not wired for the recommender yet -> deterministic fallback

    user_prompt = (
        f"AVAILABLE COURSES:\n{_catalog_context(courses)}\n\n"
        f"CUSTOMER MESSAGE:\n{message}\n\n"
        "Return the recommendation JSON."
    )
    try:
        if provider == "bedrock":
            from services.bedrock import bedrock_region, converse_json

            raw = converse_json(
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                schema=_SCHEMA,
                schema_name="course_recommendation",
                model=model,
                region=bedrock_region(),
                max_tokens=1200,
                timeout_seconds=20.0,
            )
        else:  # openrouter
            raw = _openrouter_json(api_key, model, _SYSTEM_PROMPT, user_prompt)
        payload = _extract_json(raw)
    except Exception as error:  # noqa: BLE001 — never fail the reply path on the model
        logger.warning(
            "event=recommend_failed provider=%s error=%s", provider, type(error).__name__
        )
        return None

    known = {item.slug for item in courses}
    raw_slugs = payload.get("recommended") if isinstance(payload.get("recommended"), list) else []
    slugs = [s for s in (str(x).strip() for x in raw_slugs) if s in known]
    return {
        "slugs": slugs,
        "reason": str(payload.get("reason") or "").strip(),
        "clarify": str(payload.get("clarify") or "").strip(),
        "unavailable": str(payload.get("unavailable_topic") or "").strip(),
    }


def _openrouter_json(api_key: str, model: str, system_prompt: str, user_prompt: str) -> str:
    """Call OpenRouter chat completions in JSON mode and return the content string.
    Mirrors the interpreter's OpenRouter call so both share the same headers/params."""
    import requests

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    site_url = os.getenv("OPENROUTER_SITE_URL", "").strip()
    app_name = os.getenv("OPENROUTER_APP_NAME", "Timmins WhatsApp Assistant").strip()
    if site_url:
        headers["HTTP-Referer"] = site_url
    if app_name:
        headers["X-Title"] = app_name
    response = requests.post(
        os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
        + "/chat/completions",
        headers=headers,
        json={
            "model": model,
            "temperature": 0,
            "max_tokens": 900,
            "reasoning": {"effort": "low", "exclude": True},
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        },
        timeout=20.0,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    if not isinstance(content, str):
        raise ValueError("OpenRouter response content must be a string")
    return content


def _course_line(item) -> str:
    fee = item.fees.get("standard") if item.fees else None
    detail = f"{item.dates}, {item.venue}"
    if fee:
        detail += f" — RM{int(fee):,} per participant"
    return f"- {item.name}: {detail}"


def _render(picked, courses) -> str:
    by_slug = {item.slug: item for item in courses}
    chosen = [by_slug[slug] for slug in picked["slugs"] if slug in by_slug]

    # Off-catalogue subject — say so honestly, show what we DO run, and hand the consultant.
    if picked.get("unavailable"):
        lines = [
            f"We don't currently offer a course on {picked['unavailable']}. Our courses focus "
            "on embedded systems, Linux, and software testing."
        ]
        if chosen:
            lines.append("The closest we run:")
            lines.extend(_course_line(item) for item in chosen)
        else:
            lines.append("Here's what we currently run:")
            lines.extend(f"- {item.name}: {item.dates}" for item in courses)
        lines.append(
            f"Our consultant can advise on alternatives — reach them at {_consultant_contact()}."
        )
        return "\n".join(lines)

    if not chosen:
        # Model gave no usable pick — ask its clarifying question, or fall back.
        return picked["clarify"] or _fallback(courses)

    lines = []
    if picked["reason"]:
        lines.append(picked["reason"])
    lines.extend(_course_line(item) for item in chosen)
    if picked["clarify"]:
        lines.append(picked["clarify"])
    return "\n".join(lines)


def _consultant_contact() -> str:
    try:
        from services.structured_facts import load_policies

        company = load_policies()["company"]
        return f"{company['phone']} or email {company['email']}"
    except Exception:  # noqa: BLE001
        return "our office line"


def _fallback(courses) -> str:
    lines = [
        "Tell me a bit about your background — the languages or domain you work in — and "
        "I'll point you to the right course. We currently run:"
    ]
    lines.extend(f"- {item.name}: {item.dates}" for item in courses)
    return "\n".join(lines)
