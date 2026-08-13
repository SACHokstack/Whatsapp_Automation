from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from rag_v2.models import Chunk, CorpusDocument

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_DAY_NUMBER_RE = re.compile(r"\bday\s*[- ]?\s*(\d{1,2})\b", re.IGNORECASE)
_DAY_RANGE_RE = re.compile(
    r"\bdays?\s*[- ]?\s*(\d{1,2})\s*(?:[–—-]|to|through)\s*"
    r"(\d{1,2})\b(?!\s*:)",
    re.IGNORECASE,
)
_SESSION_HEADING_RE = re.compile(
    r"^Sessions?\s+(\d{1,2})(?:\s*[–—-]\s*(\d{1,2}))?"
    r"(?:\s*\(([^)]*)\))?\s*:\s*(.+?)\s*$",
    re.IGNORECASE,
)
_MODULE_HEADING_RE = re.compile(
    r"^Modules?\s+(\d{1,2})(?:\s*[–—-]\s*(\d{1,2}))?\s*:\s*(.+?)\s*$",
    re.IGNORECASE,
)
_QA_HEADING_RE = re.compile(r"^(Q\d+\.\s+.+?)(?:\s+(Answer\s*:.*))?$", re.IGNORECASE)
_SECTION_HEADING_RE = re.compile(r"^(SECTION\s+[A-Z0-9]+\s*[–—-]\s*.+)$", re.IGNORECASE)
_LABEL_HEADING_RE = re.compile(
    r"^(Who should attend|Prerequisites|Course (?:description|syllabus|content|outline)|"
    r"Curriculum|Course structure(?:\s*\([^)]*\))?|Learning outcomes|Key topics|"
    r"Tools used|Practical outcomes|Investment|Assessment method|Methodology|"
    r"Laptop setup instructions)(?:\s*:\s*(.*))?$",
    re.IGNORECASE,
)
_TOKEN_RE = re.compile(r"\w+(?:[-'’]\w+)*|[^\w\s]", re.UNICODE)


def day_range_from_heading(heading: str) -> tuple[int, int] | None:
    """Read a day/range only from text that structurally looks like a day heading."""
    clean = heading.strip().rstrip(":")
    if len(clean) > 180 or not (
        re.match(r"^days?\b|^day\s*-", clean, re.IGNORECASE)
        or re.search(r"\(\s*day\s*[- ]?\s*\d", clean, re.IGNORECASE)
    ):
        return None
    range_match = _DAY_RANGE_RE.search(clean)
    if range_match:
        start, end = int(range_match.group(1)), int(range_match.group(2))
        return (min(start, end), max(start, end))
    numbers = [int(value) for value in _DAY_NUMBER_RE.findall(clean)]
    if not numbers:
        return None
    return min(numbers), max(numbers)


def _hours_from_text(text: str) -> float | int | None:
    match = re.search(r"\b(\d+(?:\.\d+)?)\s*hours?\b", text, re.IGNORECASE)
    if not match:
        return None
    value = float(match.group(1))
    return int(value) if value.is_integer() else value


@dataclass(frozen=True)
class ChunkingConfig:
    max_tokens: int = 220
    overlap_tokens: int = 35
    min_tokens: int = 20

    def __post_init__(self) -> None:
        if self.max_tokens < 40:
            raise ValueError("max_tokens must be at least 40")
        if not 0 <= self.overlap_tokens < self.max_tokens:
            raise ValueError("overlap_tokens must be smaller than max_tokens")
        if self.min_tokens < 1:
            raise ValueError("min_tokens must be positive")


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text)


def _join_tokens(tokens: list[str]) -> str:
    text = " ".join(tokens)
    text = re.sub(r"\s+([,.;:!?%)\]])", r"\1", text)
    text = re.sub(r"([(\[])\s+", r"\1", text)
    text = re.sub(r"(?<=\d),\s+(?=\d)", ",", text)
    text = re.sub(r"(?<=\d)\s+([–—-])\s+(?=\d)", r"\1", text)
    return text.strip()


def _section_metadata(heading: str) -> dict[str, str | int | float]:
    day_range = day_range_from_heading(heading)
    if day_range:
        return {
            "section_type": "agenda",
            "day_start": day_range[0],
            "day_end": day_range[1],
            "section_heading": heading,
        }

    session_match = _SESSION_HEADING_RE.match(heading)
    if session_match:
        session_start = int(session_match.group(1))
        metadata: dict[str, str | int | float] = {
            "section_type": "agenda",
            "session_start": session_start,
            "session_end": int(session_match.group(2) or session_start),
            "session_title": session_match.group(4).strip(),
            "section_heading": heading,
        }
        duration_hours = _hours_from_text(session_match.group(3) or "")
        if duration_hours is not None:
            metadata["duration_hours"] = duration_hours
        return metadata

    module_match = _MODULE_HEADING_RE.match(heading)
    if module_match:
        module_start = int(module_match.group(1))
        return {
            "section_type": "agenda",
            "module_start": module_start,
            "module_end": int(module_match.group(2) or module_start),
            "module_title": module_match.group(3).strip(),
            "section_heading": heading,
        }

    lower = heading.lower()
    if re.match(r"q\d+\.", lower):
        return {"section_type": "qa"}
    labels = {
        "who should attend": "audience",
        "prerequisites": "prerequisites",
        "course description": "description",
        "course structure": "agenda_overview",
        "course syllabus": "topics",
        "course content": "topics",
        "course outline": "topics",
        "curriculum": "topics",
        "learning outcomes": "outcomes",
        "key topics": "topics",
        "tools used": "tools",
        "practical outcomes": "outcomes",
        "investment": "commercial",
        "assessment method": "assessment",
        "methodology": "methodology",
        "laptop setup instructions": "setup",
    }
    for prefix, section_type in labels.items():
        if lower.startswith(prefix):
            return {"section_type": section_type}
    return {"section_type": "general"}


def _plain_heading(line: str) -> tuple[str, str] | None:
    clean = line.strip().rstrip(":")
    if day_range_from_heading(clean):
        return clean, ""
    if _SESSION_HEADING_RE.match(clean) or _MODULE_HEADING_RE.match(clean):
        return clean, ""
    label_match = _LABEL_HEADING_RE.match(line)
    if label_match:
        return label_match.group(1).strip(), (label_match.group(2) or "").strip()
    if _SECTION_HEADING_RE.match(line):
        return line.strip(), ""
    qa_match = _QA_HEADING_RE.match(line)
    if qa_match:
        return qa_match.group(1).strip(), (qa_match.group(2) or "").strip()
    return None


def _sections(text: str, default_title: str) -> list[tuple[str, str, dict[str, str | int | float]]]:
    sections: list[tuple[str, str, dict[str, str | int | float]]] = []
    heading = default_title
    body: list[str] = []

    def flush() -> None:
        content = "\n".join(body).strip()
        if content:
            sections.append((heading, content, _section_metadata(heading)))

    for raw_line in text.splitlines():
        line = raw_line.strip()
        match = _HEADING_RE.match(line)
        if match:
            flush()
            heading = match.group(2).strip()
            body = []
        else:
            plain_heading = _plain_heading(line)
            if plain_heading:
                flush()
                heading, inline_body = plain_heading
                body = [inline_body] if inline_body else []
            else:
                body.append(raw_line)
    flush()
    return sections


def _stable_chunk_id(document_id: str, ordinal: int, text: str) -> str:
    digest = hashlib.sha256(f"{document_id}\0{ordinal}\0{text}".encode()).hexdigest()[:20]
    return f"chk_{digest}"


def chunk_document(document: CorpusDocument, config: ChunkingConfig | None = None) -> list[Chunk]:
    config = config or ChunkingConfig()
    chunks: list[Chunk] = []
    ordinal = 0

    for heading, body, section_metadata in _sections(document.text, document.title):
        heading_tokens = _tokens(heading)
        body_tokens = _tokens(body)
        available = max(1, config.max_tokens - len(heading_tokens) - 2)
        start = 0

        while start < len(body_tokens):
            end = min(len(body_tokens), start + available)
            window = body_tokens[start:end]
            if len(window) < config.min_tokens and chunks and heading == chunks[-1].title:
                previous = chunks.pop()
                combined = f"{previous.text}\n{_join_tokens(window)}".strip()
                chunks.append(
                    Chunk(
                        chunk_id=_stable_chunk_id(document.document_id, previous.ordinal, combined),
                        document_id=document.document_id,
                        title=heading,
                        text=combined,
                        source_ref=document.source_ref,
                        ordinal=previous.ordinal,
                        course_id=document.course_id,
                        topic=document.topic,
                        metadata={**document.metadata, **section_metadata},
                    )
                )
                break

            content = f"{heading}\n{_join_tokens(window)}".strip()
            chunks.append(
                Chunk(
                    chunk_id=_stable_chunk_id(document.document_id, ordinal, content),
                    document_id=document.document_id,
                    title=heading,
                    text=content,
                    source_ref=document.source_ref,
                    ordinal=ordinal,
                    course_id=document.course_id,
                    topic=document.topic,
                    metadata={**document.metadata, **section_metadata},
                )
            )
            ordinal += 1
            if end == len(body_tokens):
                break
            start = max(start + 1, end - config.overlap_tokens)

    return chunks
