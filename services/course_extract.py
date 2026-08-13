"""Read a course brochure and propose the fields for a new course.

Typing a fee table and a full overview into a form is data entry, not administration — the
client already has the brochure. This module turns that document into a filled-in form.

It only ever *proposes*. The values it returns are shown in the editor for a human to check
before anything is saved, because these facts become what the bot quotes to paying customers:
a fee misread from a table would otherwise be stated to every enquirer with total confidence.
Nothing here writes to the database.

The overview is deliberately not summarised — it is the document's own text, lightly tidied.
The bot answers from it, so losing detail to a summary would lose answers.
"""

from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger(__name__)

# Ceiling on how much of a document is sent for field extraction. The facts we want (title,
# dates, venue, fees, deadlines) live in the opening pages of every brochure, and the full text
# is kept for the overview regardless — so this bounds cost without losing anything.
_MAX_PROMPT_CHARS = 12_000

_SYSTEM_PROMPT = """You extract course details from a training brochure.

Return ONLY a JSON object with these keys:
  name              - the course title, exactly as written. ""
  dates             - when it runs, as written (e.g. "20-21 August 2026"). ""
  venue             - where it is held. ""
  fees              - object of price tiers, integers, no currency symbols. Use the keys
                      "standard", "group_2", "group_3_plus" when those tiers exist. {}
  hrdc_deadline     - HRDC/grant registration deadline. ""
  payment_deadline  - payment deadline. ""
  keywords          - 8-20 lowercase topic terms a customer might use to ask about this
                      course (technologies, tools, skills taught). []

Rules:
- Copy values from the document. Never invent, infer, or convert a figure that is not written.
- If something is absent, return the empty value shown above. An empty field is correct and
  useful; a guessed one is not.
- Fees are numbers only: "RM 3,200" becomes 3200.
"""

_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "dates": {"type": "string"},
        "venue": {"type": "string"},
        "fees": {"type": "object", "additionalProperties": {"type": "integer"}},
        "hrdc_deadline": {"type": "string"},
        "payment_deadline": {"type": "string"},
        "keywords": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["name"],
}


class ExtractionUnavailable(RuntimeError):
    """No language model is configured, or the provider could not be reached."""


def propose_course_fields(text: str) -> dict:
    """Propose course fields from document text. Returns a dict shaped like the editor form.

    Raises ExtractionUnavailable when no model is configured or the call fails — the caller
    turns that into a message telling the admin to fill the form in by hand, which always works.
    """
    body = (text or "").strip()
    if not body:
        raise ExtractionUnavailable("the document has no readable text")

    payload = _ask_model(body[:_MAX_PROMPT_CHARS])
    fields = _normalise(payload)
    # The overview is the document itself, not the model's rendering of it.
    fields["overview"] = _as_overview(body, fields.get("name", ""))
    return fields


def _ask_model(document_text: str) -> dict:
    from services.interpret import _extract_json, _interpreter_settings

    provider, api_key, model = _interpreter_settings()
    if not api_key:
        raise ExtractionUnavailable("no language model is configured")

    user_prompt = f"Brochure text:\n\n{document_text}"
    try:
        if provider == "bedrock":
            from services.bedrock import bedrock_region, converse_json

            content = converse_json(
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                schema=_RESPONSE_SCHEMA,
                schema_name="course_fields",
                model=model,
                region=bedrock_region(),
                max_tokens=2000,
                timeout_seconds=45.0,
            )
            return _extract_json(content)

        if provider == "openrouter":
            import requests

            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            site_url = os.getenv("OPENROUTER_SITE_URL", "").strip()
            if site_url:
                headers["HTTP-Referer"] = site_url
            response = requests.post(
                os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
                + "/chat/completions",
                headers=headers,
                json={
                    "model": model,
                    "temperature": 0,
                    "max_tokens": 1500,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                },
                timeout=45.0,
            )
            if response.status_code >= 400:
                raise ExtractionUnavailable(f"the model provider returned {response.status_code}")
            content = response.json()["choices"][0]["message"]["content"]
            return _extract_json(content)

        from groq import Groq

        completion = Groq(api_key=api_key).chat.completions.create(
            model=model,
            temperature=0,
            max_tokens=1500,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        )
        return _extract_json(completion.choices[0].message.content or "")
    except ExtractionUnavailable:
        raise
    except Exception as error:  # noqa: BLE001 - any provider failure means "fill it in by hand"
        logger.warning("event=course_extract_failed provider=%s error=%s", provider, error)
        raise ExtractionUnavailable(f"could not read the document: {error}") from error


def _normalise(payload: dict) -> dict:
    """Coerce the model's output into the shapes the course form and CourseConfig expect."""
    fees_raw = payload.get("fees")
    fees: dict[str, int] = {}
    if isinstance(fees_raw, dict):
        for tier, value in fees_raw.items():
            amount = _as_int(value)
            if amount is not None:
                fees[str(tier).strip().lower().replace(" ", "_")] = amount

    keywords_raw = payload.get("keywords")
    keywords: list[str] = []
    if isinstance(keywords_raw, str):
        keywords_raw = keywords_raw.split(",")
    if isinstance(keywords_raw, list):
        for item in keywords_raw:
            keyword = str(item).strip().lower()
            if keyword and keyword not in keywords:
                keywords.append(keyword)

    return {
        "name": _as_text(payload.get("name")),
        "dates": _as_text(payload.get("dates")),
        "venue": _as_text(payload.get("venue")),
        "fees": fees,
        "hrdc_deadline": _as_text(payload.get("hrdc_deadline")),
        "payment_deadline": _as_text(payload.get("payment_deadline")),
        "keywords": keywords[:20],
    }


def _as_text(value) -> str:
    return "" if value is None else str(value).strip()


def _as_int(value) -> int | None:
    """Accept 3200, "3200", "RM 3,200" — reject anything with no digits."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    digits = re.sub(r"[^0-9]", "", str(value or ""))
    return int(digits) if digits else None


def _as_overview(text: str, name: str) -> str:
    """The document's own text as the overview, with the heading the parsers expect.

    `structured_facts` reads the overview with regexes that key off "# Course Overview", so the
    heading is added when the document does not already start with one.
    """
    body = re.sub(r"\n{3,}", "\n\n", text.strip())
    if body.lstrip().startswith("#"):
        return body
    title = name.strip() or "Course Overview"
    return (
        f"# Course Overview\n\n{title}\n\n{body}"
        if name.strip()
        else f"# Course Overview\n\n{body}"
    )
