from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from typing import Any, Protocol

from rag_v2.models import Claim, GroundedAnswer, RagRequest, SearchResult
from rag_v2.query_expansion import requested_day, requested_session
from services.bedrock import DEFAULT_BEDROCK_MODEL, bedrock_region, converse_json

logger = logging.getLogger(__name__)


class GroundedGenerator(Protocol):
    def generate(self, request: RagRequest, evidence: Sequence[SearchResult]) -> GroundedAnswer: ...


_SYSTEM_PROMPT = """You are a grounded question-answering component.

Use only the supplied evidence. Do not use outside knowledge and do not infer missing commercial
facts. Each factual claim must cite one or more evidence IDs.

CUSTOMER_PROFILE (if present) is context for tailoring tone and relevance only. It is NOT evidence:
never cite it and never state its contents back as a fact.

Follow RESPONSE_STYLE exactly. Default answers should be professional and scannable: the first claim
must be a short descriptive overview sentence, followed by grouped supporting points. Avoid repeating
phrases such as "you will learn", and do not dump every retrieved topic. Only provide the expanded
version when RESPONSE_STYLE explicitly requests detail.

When TARGET_COURSE is present, answer about that course. Do not present another course named in the
evidence as the subject of the answer unless the question explicitly asks for a comparison.

Return one JSON object only, with this schema:
{
  "status": "answered" | "clarify" | "not_found",
  "claims": [{"text": "overview first, then concise supported points", "source_ids": ["evidence_id"]}],
  "clarification_question": "string or null"
}

For answered responses, claims must be non-empty and every claim must have source_ids. Never cite
an ID that is absent from the supplied evidence. Do not include Markdown or commentary outside the
JSON object.

DECISION RULES — be helpful, not paralyzed:
- If the evidence contains relevant information that answers ANY part of the question, return
  "answered" with the supported claims. You do NOT need to have a perfect verbatim match.
  For example, if the user asks "what is on day one" and evidence says "Days 1-2: topic X",
  that IS sufficient — answer it.
- Return "clarify" ONLY when the question is genuinely ambiguous (e.g., "tell me about the course"
  when multiple courses are in context and you cannot tell which one).
- Return "not_found" ONLY when the evidence does not contain relevant information at all —
  e.g., the user asks about parking and no evidence mentions parking.
- When the evidence is partially relevant, answer what you can and note what is not confirmed.
- Treat DAY_RANGE and SESSION_RANGE as verified source structure. Never infer a day from a session
  number, page number, section order, or arithmetic. If SCHEDULE_GUIDANCE says the source has no
  explicit day-to-session mapping, say so briefly and summarize the verified session-based outline;
  this is still an "answered" response, not "not_found".
- If DURATION_STATUS is "conflict", never resolve the conflict by choosing or calculating a new
  duration. Use the explicitly declared duration when relevant and mention the source inconsistency
  when the question concerns duration or day/session allocation.
- VALUE / JUSTIFICATION questions ("why is it costly", "why so expensive", "why should I take
  this", "is it worth it") ARE answerable: synthesize the value from the course's scope, depth,
  hands-on labs, hardware, outcomes, and target audience found in the evidence. Do NOT return
  not_found just because the evidence doesn't literally explain the price — frame the value, and
  simply omit any specific figure that isn't in the evidence.
- Never invent facts. If a specific value (fee, date, venue) is not in the evidence, do not
  include it — but still answer the parts you CAN support."""

_GROUNDED_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "status": {"type": "string", "enum": ["answered", "clarify", "not_found"]},
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "text": {"type": "string"},
                    "source_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["text", "source_ids"],
            },
        },
        "clarification_question": {"type": ["string", "null"]},
    },
    "required": ["status", "claims", "clarification_question"],
}

_DETAIL_RE = re.compile(
    r"\b(?:detailed?|in depth|full (?:details?|breakdown|outline)|comprehensive|"
    r"explain more|expand|more detail|day[ -]by[ -]day)\b",
    re.IGNORECASE,
)


def wants_detailed_response(question: str) -> bool:
    return bool(_DETAIL_RE.search(question))


_COURSE_CONTENT_RE = re.compile(
    r"\b(?:syllabus|curriculum|course (?:outline|content|structure)|topics?|"
    r"covered|cover|learn|teach|day[ -]by[ -]day|daily breakdown|"
    r"day\s+\d{1,2}|session\s+\d{1,2})\b",
    re.IGNORECASE,
)


def wants_detail_offer(question: str, retrieval_query: str = "") -> bool:
    return bool(_COURSE_CONTENT_RE.search(f"{question}\n{retrieval_query}"))


def _compact_claim(text: str, max_words: int) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    words = cleaned.split()
    if len(words) <= max_words:
        return cleaned
    prefix = " ".join(words[:max_words])
    # Prefer ending at the latest complete list item rather than cutting mid-phrase.
    boundary = max(prefix.rfind(","), prefix.rfind(";"))
    if boundary >= len(prefix) // 2:
        prefix = prefix[:boundary]
    return prefix.rstrip(" ,;:.") + "."


def _render_claims(claims: Sequence[Claim], *, detailed: bool, offer_details: bool = False) -> str:
    selected = list(claims[:9] if detailed else claims[:5])
    rendered = [
        claim.text.strip() if detailed else _compact_claim(claim.text, max_words=24)
        for claim in selected
    ]
    if len(rendered) == 1:
        answer = rendered[0]
    else:
        overview = rendered[0]
        if overview[-1:] not in ".!?:":
            overview += "."
        answer = overview + "\n\n" + "\n".join(f"• {text}" for text in rendered[1:])
    if offer_details and not detailed:
        answer += (
            "\n\nIf you'd like a more detailed breakdown, just ask and I can explain "
            "the syllabus in depth."
        )
    return answer


def _misattributed_course_name(
    claim_text: str, *, expected_course_id: str | None, question: str
) -> str | None:
    if not expected_course_id or re.search(
        r"\b(?:compare|comparison|difference|different|versus|vs\.?|how .* differ)\b",
        question,
        re.IGNORECASE,
    ):
        return None
    try:
        from services.course_loader import get_active_courses

        courses = get_active_courses()
    except Exception:  # pragma: no cover - validation must remain fail-safe
        return None
    lower = claim_text.lower()
    for course in courses:
        if course.slug == expected_course_id:
            continue
        name = course.name.lower()
        start = lower.find(name)
        if start < 0:
            continue
        tail = lower[start + len(name) : start + len(name) + 80]
        if re.match(
            r"\s+(?:course\s+)?(?:syllabus|curriculum|covers|includes|teaches|focuses)",
            tail,
        ):
            return course.name
    return None


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        # Bedrock GPT-OSS can occasionally prepend a constrained-output opening
        # brace to the model's own complete JSON object. Repair only that exact
        # shape; the normal schema and citation validators still run afterward.
        if not re.match(r"^\{\s*\{", cleaned):
            raise
        value = json.loads(cleaned[1:].lstrip())
    if not isinstance(value, dict):
        raise ValueError("model output must be a JSON object")
    return value


_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_MONEY_RE = re.compile(r"\bRM\s*[0-9][0-9,\s]*(?:\.\d{2})?\b", re.IGNORECASE)
_PHONE_RE = re.compile(r"\+\s?\d[\d\s().-]{6,}\d")
_DATE_RE = re.compile(
    r"\b\d{1,2}\s*(?:[–—-]\s*\d{1,2})?\s+"
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"(?:\s+\d{4})?\b",
    re.IGNORECASE,
)
_COURSE_NAME_RE = re.compile(r"\bCourse\s+[A-Z][A-Za-z0-9&/+-]*(?:\s+[A-Z][A-Za-z0-9&/+-]*){0,5}\b")


def _normalize_support_text(text: str) -> str:
    normalized = text.lower().replace("–", "-").replace("—", "-")
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = re.sub(r"\s*-\s*", "-", normalized)
    return normalized.strip()


def _normalize_money(value: str) -> str:
    return re.sub(r"[\s,]", "", value.lower())


def _normalize_phone(value: str) -> str:
    return re.sub(r"\D", "", value)


def _high_risk_values(text: str) -> dict[str, set[str]]:
    return {
        "money": {_normalize_money(value) for value in _MONEY_RE.findall(text)},
        "email": {value.lower() for value in _EMAIL_RE.findall(text)},
        "phone": {_normalize_phone(value) for value in _PHONE_RE.findall(text)},
        "date": {_normalize_support_text(value) for value in _DATE_RE.findall(text)},
        "course": {_normalize_support_text(value) for value in _COURSE_NAME_RE.findall(text)},
    }


def _unsupported_high_risk_values(claim_text: str, cited_text: str) -> list[str]:
    claim_values = _high_risk_values(claim_text)
    evidence_values = _high_risk_values(cited_text)
    unsupported: list[str] = []
    normalized_evidence = _normalize_support_text(cited_text)
    normalized_money_evidence = re.sub(r"[\s,]", "", normalized_evidence)
    normalized_phone_evidence = re.sub(r"\D", "", cited_text)

    for value in claim_values["money"]:
        if value not in evidence_values["money"] and value not in normalized_money_evidence:
            unsupported.append(value)
    for value in claim_values["email"]:
        if value not in evidence_values["email"] and value not in normalized_evidence:
            unsupported.append(value)
    for value in claim_values["phone"]:
        if value not in evidence_values["phone"] and value not in normalized_phone_evidence:
            unsupported.append(value)
    for value in claim_values["date"]:
        if value not in evidence_values["date"] and value not in normalized_evidence:
            unsupported.append(value)
    for value in claim_values["course"]:
        if value not in evidence_values["course"] and value not in normalized_evidence:
            unsupported.append(value)
    return unsupported


def validate_generation(
    payload: dict[str, Any],
    evidence: Sequence[SearchResult],
    *,
    detailed: bool = False,
    offer_details: bool = False,
    expected_course_id: str | None = None,
    question: str = "",
) -> GroundedAnswer:
    status = payload.get("status")
    if status not in {"answered", "clarify", "not_found"}:
        return GroundedAnswer("rejected", "", reason="invalid generation status")
    if status == "clarify":
        question = payload.get("clarification_question")
        if not isinstance(question, str) or not question.strip():
            return GroundedAnswer("rejected", "", reason="missing clarification question")
        return GroundedAnswer("clarify", question.strip(), sources=tuple(evidence))
    if status == "not_found":
        return GroundedAnswer(
            "not_found",
            "I don't have enough verified information to answer that.",
            sources=tuple(evidence),
            reason="generator found insufficient evidence",
        )

    allowed_ids = {result.chunk.chunk_id for result in evidence}
    evidence_by_id = {result.chunk.chunk_id: result for result in evidence}
    raw_claims = payload.get("claims")
    if not isinstance(raw_claims, list) or not raw_claims:
        return GroundedAnswer("rejected", "", reason="answered response has no claims")
    claims: list[Claim] = []
    for raw_claim in raw_claims:
        if not isinstance(raw_claim, dict):
            return GroundedAnswer("rejected", "", reason="claim must be an object")
        text = raw_claim.get("text")
        source_ids = raw_claim.get("source_ids")
        if not isinstance(text, str) or not text.strip():
            return GroundedAnswer("rejected", "", reason="claim has no text")
        if not isinstance(source_ids, list) or not source_ids:
            return GroundedAnswer("rejected", "", reason="claim has no citations")
        if not all(
            isinstance(source_id, str) and source_id in allowed_ids for source_id in source_ids
        ):
            return GroundedAnswer("rejected", "", reason="claim cites unknown evidence")
        cited_text = "\n".join(
            "\n".join(
                (
                    evidence_by_id[source_id].chunk.title,
                    evidence_by_id[source_id].chunk.text,
                    evidence_by_id[source_id].chunk.source_ref,
                )
            )
            for source_id in source_ids
        )
        unsupported = _unsupported_high_risk_values(text, cited_text)
        if unsupported:
            return GroundedAnswer(
                "rejected",
                "",
                reason="unsupported high-risk claim values: " + ", ".join(unsupported),
            )
        wrong_course = _misattributed_course_name(
            text, expected_course_id=expected_course_id, question=question
        )
        if wrong_course:
            return GroundedAnswer(
                "rejected",
                "",
                reason=f"claim misattributes the answer to {wrong_course}",
            )
        claims.append(Claim(text=text.strip(), source_ids=tuple(dict.fromkeys(source_ids))))

    return GroundedAnswer(
        "answered",
        _render_claims(claims, detailed=detailed, offer_details=offer_details),
        claims=tuple(claims),
        sources=tuple(evidence),
    )


def _metadata_contains(metadata: dict[str, Any], prefix: str, value: int) -> bool:
    try:
        start = int(metadata.get(f"{prefix}_start"))
        end = int(metadata.get(f"{prefix}_end", start))
    except (TypeError, ValueError):
        return False
    return start <= value <= end


class _GroundedPromptGenerator:
    model: str

    def _validated_answer(
        self,
        payload: dict[str, Any],
        request: RagRequest,
        evidence: Sequence[SearchResult],
    ) -> GroundedAnswer:
        return validate_generation(
            payload,
            evidence,
            detailed=wants_detailed_response(request.question),
            offer_details=wants_detail_offer(request.question, request.retrieval_query),
            expected_course_id=request.retrieval_filter.course_id,
            question=request.question,
        )

    def _user_prompt(self, request: RagRequest, evidence: Sequence[SearchResult]) -> str:
        schedule_query = f"{request.question}\n{request.retrieval_query}"
        day = requested_day(schedule_query)
        session = requested_session(schedule_query)
        schedule_guidance = "No special schedule constraint."
        if day is not None:
            matching_day = any(
                _metadata_contains(result.chunk.metadata, "day", day) for result in evidence
            )
            has_unmapped_sessions = any(
                result.chunk.metadata.get("session_start") is not None
                and result.chunk.metadata.get("day_mapping") in {"not_explicit", "partial"}
                for result in evidence
            )
            if matching_day:
                schedule_guidance = (
                    f"Day {day} has an explicit DAY_RANGE in the supplied evidence; answer only "
                    "from chunks whose range includes that day."
                )
            elif has_unmapped_sessions:
                schedule_guidance = (
                    f"The source does not explicitly map its sessions to Day {day}. Do not assign "
                    f"sessions to Day {day}. Explain that the approved outline is session-based, "
                    "then summarize the verified sessions that were retrieved."
                )
        elif session is not None:
            schedule_guidance = (
                f"Answer Session {session} only from evidence whose SESSION_RANGE contains "
                f"{session}; do not substitute a day or another session."
            )

        evidence_blocks = []
        for result in evidence:
            chunk = result.chunk
            declared_hours = chunk.metadata.get("declared_hours")
            declared_days = chunk.metadata.get("declared_days")
            evidence_blocks.append(
                "\n".join(
                    (
                        f"EVIDENCE_ID: {chunk.chunk_id}",
                        f"COURSE_ID: {chunk.course_id or '[general]'}",
                        f"TOPIC: {chunk.topic or '[general]'}",
                        f"SECTION_TYPE: {chunk.metadata.get('section_type', '[general]')}",
                        "DAY_RANGE: "
                        + (
                            f"{chunk.metadata.get('day_start')}-{chunk.metadata.get('day_end')}"
                            if chunk.metadata.get("day_start") is not None
                            else "[not applicable]"
                        ),
                        "SESSION_RANGE: "
                        + (
                            f"{chunk.metadata.get('session_start')}-"
                            f"{chunk.metadata.get('session_end')}"
                            if chunk.metadata.get("session_start") is not None
                            else "[not applicable]"
                        ),
                        f"DAY_MAPPING: {chunk.metadata.get('day_mapping', '[not specified]')}",
                        "DECLARED_DURATION: "
                        + (
                            f"{declared_hours} hours / {declared_days} days"
                            if declared_hours is not None or declared_days is not None
                            else "[not specified]"
                        ),
                        "SESSION_HOURS_TOTAL: "
                        f"{chunk.metadata.get('session_hours_total', '[not available]')}",
                        f"DURATION_STATUS: {chunk.metadata.get('duration_status', '[not checked]')}",
                        f"SOURCE: {chunk.source_ref}",
                        f"TEXT:\n{chunk.text}",
                    )
                )
            )
        conversation = "\n".join(
            f"{role.upper()}: {text}" for role, text in request.conversation[-6:]
        )
        detailed = wants_detailed_response(request.question)
        target_course_id = request.retrieval_filter.course_id or "[not course-scoped]"
        target_course_name = next(
            (
                str(result.chunk.metadata.get("course_name"))
                for result in evidence
                if result.chunk.course_id == request.retrieval_filter.course_id
                and result.chunk.metadata.get("course_name")
            ),
            "[not course-scoped]",
        )
        response_style = (
            "DETAILED: provide one descriptive overview claim followed by up to 8 "
            "informative supporting claims, each at most 30 words."
            if detailed
            else "CONCISE: provide one descriptive overview claim followed by up to 4 "
            "grouped supporting claims, each at most 22 words."
        )
        return (
            f"QUESTION:\n{request.question}\n\n"
            f"STANDALONE_REQUEST:\n{request.retrieval_query or request.question}\n\n"
            f"TARGET_COURSE:\n{target_course_name} ({target_course_id})\n\n"
            f"SCHEDULE_GUIDANCE:\n{schedule_guidance}\n\n"
            f"RESPONSE_STYLE:\n{response_style}\n\n"
            f"CUSTOMER_PROFILE:\n{request.profile or '[none]'}\n\n"
            f"RECENT_CONVERSATION:\n{conversation or '[none]'}\n\n"
            f"EVIDENCE:\n\n" + "\n\n---\n\n".join(evidence_blocks)
        )


class GroqGroundedGenerator(_GroundedPromptGenerator):
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "llama-3.3-70b-versatile",
        timeout_seconds: float = 20.0,
    ) -> None:
        if not api_key:
            raise ValueError("Groq API key is required")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds

    def generate(self, request: RagRequest, evidence: Sequence[SearchResult]) -> GroundedAnswer:
        try:
            from groq import Groq

            client = Groq(api_key=self.api_key, max_retries=1, timeout=self.timeout_seconds)
            response = client.chat.completions.create(
                model=self.model,
                temperature=0,
                max_tokens=700,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": self._user_prompt(request, evidence)},
                ],
            )
            payload = _extract_json(response.choices[0].message.content or "")
            return self._validated_answer(payload, request, evidence)
        except Exception as error:
            logger.exception("grounded generation failed provider=groq model=%s", self.model)
            return GroundedAnswer(
                "rejected",
                "",
                sources=tuple(evidence),
                reason=f"generation failure: {type(error).__name__}",
            )


class OpenRouterGroundedGenerator(_GroundedPromptGenerator):
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "openai/gpt-oss-120b",
        timeout_seconds: float = 30.0,
        base_url: str = "https://openrouter.ai/api/v1",
        site_url: str = "",
        app_name: str = "Timmins WhatsApp Assistant",
    ) -> None:
        if not api_key:
            raise ValueError("OpenRouter API key is required")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.base_url = base_url.rstrip("/")
        self.site_url = site_url.strip()
        self.app_name = app_name.strip()

    def generate(self, request: RagRequest, evidence: Sequence[SearchResult]) -> GroundedAnswer:
        try:
            import requests

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            if self.site_url:
                headers["HTTP-Referer"] = self.site_url
            if self.app_name:
                headers["X-Title"] = self.app_name

            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json={
                    "model": self.model,
                    "temperature": 0,
                    "max_tokens": 1200,
                    "reasoning": {"effort": "low", "exclude": True},
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "grounded_answer",
                            "strict": True,
                            "schema": _GROUNDED_RESPONSE_SCHEMA,
                        },
                    },
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": self._user_prompt(request, evidence)},
                    ],
                },
                timeout=self.timeout_seconds,
            )
            if response.status_code >= 400:
                logger.error(
                    "grounded generation rejected provider=openrouter model=%s status=%d",
                    self.model,
                    response.status_code,
                )
                return GroundedAnswer(
                    "rejected",
                    "",
                    sources=tuple(evidence),
                    reason=f"openrouter HTTP {response.status_code}",
                )

            body = response.json()
            content = body["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise ValueError("OpenRouter response content must be a string")
            payload = _extract_json(content)
            return self._validated_answer(payload, request, evidence)
        except Exception as error:
            logger.exception("grounded generation failed provider=openrouter model=%s", self.model)
            return GroundedAnswer(
                "rejected",
                "",
                sources=tuple(evidence),
                reason=f"openrouter generation failure: {type(error).__name__}",
            )


class BedrockGroundedGenerator(_GroundedPromptGenerator):
    def __init__(
        self,
        *,
        model: str = DEFAULT_BEDROCK_MODEL,
        region: str | None = None,
        timeout_seconds: float = 30.0,
        client=None,
    ) -> None:
        self.model = model
        self.region = (region or bedrock_region()).strip()
        self.timeout_seconds = timeout_seconds
        self.client = client

    def generate(self, request: RagRequest, evidence: Sequence[SearchResult]) -> GroundedAnswer:
        try:
            content = converse_json(
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=self._user_prompt(request, evidence),
                schema=_GROUNDED_RESPONSE_SCHEMA,
                schema_name="grounded_answer",
                model=self.model,
                region=self.region,
                # Bedrock counts GPT-OSS reasoning tokens inside maxTokens. A
                # 1,200-token cap can truncate even a short structured answer.
                max_tokens=4000,
                timeout_seconds=self.timeout_seconds,
                client=self.client,
            )
            payload = _extract_json(content)
            return self._validated_answer(payload, request, evidence)
        except Exception as error:
            logger.exception(
                "grounded generation failed provider=bedrock model=%s region=%s",
                self.model,
                self.region,
            )
            return GroundedAnswer(
                "rejected",
                "",
                sources=tuple(evidence),
                reason=f"bedrock generation failure: {type(error).__name__}",
            )
