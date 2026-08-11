"""
Ingestion helpers for external knowledge sources:
  - KB_extracted/  : .docx Q&A knowledge bases (one per course)
  - NEW_Courses/   : .pdf flyers + HRDC course outlines (one or more per course)

Each source is parsed into CorpusDocuments and linked to the matching course slug.
The runtime imports build_kb_documents() and includes the results in the RAG index.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    pass

from rag_v2.chunking import day_range_from_heading
from rag_v2.models import CorpusDocument

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[1]
_KB_DIR = _ROOT / "KB_extracted" / "2026 Meta Lead Automation - Knowledge Base"
_PDF_DIR = _ROOT / "NEW_Courses"
_COURSES_DIR = _ROOT / "courses"

# Map keywords in file stem (lower) → course slug
_SLUG_MAP: list[tuple[str, str]] = [
    ("embedded linux debugging", "embedded-linux-debugging-aug-2026"),
    ("embedded linux system internals", "embedded-linux-internals-aug-2026"),
    ("embedded linux with yocto", "embedded-linux-yocto-aug-2026"),
    ("embedded linux with yocto_ deep", "embedded-linux-yocto-aug-2026"),
    ("embedded linux with yocto deep", "embedded-linux-yocto-aug-2026"),
    ("embedded c programming", "embedded-c-july-2026"),
    ("embedded c", "embedded-c-july-2026"),
    ("linux kernel", "linux-kernel-aug-2026"),
    ("modern software testing", "sw-testing-aug-2026"),
    ("python automation", "python-automation-july-2026"),
]

_QA_SPLIT_RE = re.compile(r"(?=\bQ\d+\.\s)", re.MULTILINE)


def _slug_for(filename: str) -> str | None:
    lower = filename.lower()
    # Longest match first (list is already ordered longest-key-first where ambiguous)
    for key, slug in _SLUG_MAP:
        if key in lower:
            return slug
    return None


# ── DOCX ──────────────────────────────────────────────────────────────────────


def _extract_docx_text(path: Path) -> str:
    try:
        import docx  # python-docx

        doc = docx.Document(path)
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    except Exception as primary_error:
        # DOCX is a ZIP of XML files. Keep ingestion functional even when the optional
        # python-docx package is absent in a local/test environment.
        try:
            with zipfile.ZipFile(path) as archive:
                root = ET.fromstring(archive.read("word/document.xml"))
            namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            paragraphs = []
            for paragraph in root.findall(".//w:p", namespace):
                text = "".join(
                    node.text or "" for node in paragraph.findall(".//w:t", namespace)
                ).strip()
                if text:
                    paragraphs.append(text)
            logger.info("docx extraction used XML fallback: %s", path.name)
            return "\n".join(paragraphs)
        except Exception:
            logger.exception(
                "docx extraction failed: %s (primary=%s)",
                path.name,
                type(primary_error).__name__,
            )
            return ""


def _split_qa_sections(text: str) -> list[str]:
    """Split on Q1. Q2. … boundaries, keeping each Q+Answer together."""
    raw = _QA_SPLIT_RE.split(text)
    sections: list[str] = []
    preamble_done = False
    for chunk in raw:
        stripped = chunk.strip()
        if not stripped:
            continue
        if not preamble_done and not re.match(r"Q\d+\.", stripped):
            # Skip preamble ("Below is the knowledge base…")
            preamble_done = True
            continue
        preamble_done = True
        sections.append(stripped)
    return sections


def build_docx_documents() -> list[CorpusDocument]:
    """Parse each KB docx into per-Q&A CorpusDocuments."""
    documents: list[CorpusDocument] = []
    if not _KB_DIR.exists():
        logger.warning("KB_extracted directory not found: %s", _KB_DIR)
        return documents

    for path in sorted(_KB_DIR.glob("*.docx")):
        slug = _slug_for(path.stem)
        if not slug:
            logger.warning("No course slug matched for docx: %s", path.name)
            continue

        text = _extract_docx_text(path)
        if not text.strip():
            continue

        sections = _split_qa_sections(text)
        if not sections:
            # Fallback: ingest as single large doc
            sections = [text]

        for i, section in enumerate(sections):
            if len(section.split()) < 5:
                continue
            # Derive a short title from the first line
            first_line = section.splitlines()[0].strip()
            title = first_line[:100] if first_line else f"KB section {i + 1}"
            documents.append(
                CorpusDocument(
                    document_id=f"kb-{slug}-q{i + 1:03d}",
                    title=title,
                    text=section,
                    source_ref=f"KB_extracted/{path.name}",
                    course_id=slug,
                    topic="course_kb",
                    metadata={
                        "authority": "approved_kb_docx",
                        "source_file": path.name,
                        "section_index": i + 1,
                    },
                )
            )

    logger.info(
        "kb_ingestion: loaded %d Q&A sections from %d docx files",
        len(documents),
        sum(1 for _ in _KB_DIR.glob("*.docx")),
    )
    return documents


# ── PDF ───────────────────────────────────────────────────────────────────────


def _extract_pdf_pages(path: Path) -> list[tuple[int, str]]:
    try:
        import pdfplumber

        pages: list[tuple[int, str]] = []
        with pdfplumber.open(path) as pdf:
            for page_number, page in enumerate(pdf.pages, start=1):
                txt = page.extract_text()
                if txt:
                    pages.append((page_number, txt))
        return pages
    except Exception:
        logger.exception("pdf extraction failed: %s", path.name)
        return []


def _split_pdf_sections(text: str, max_chars: int = 1200) -> list[str]:
    """
    Split PDF text into logical sections:
    - Try heading-level splits first (lines that look like section titles)
    - Fall back to paragraph-based chunking
    """
    # Split on double newlines (paragraphs) or all-caps lines (headings)
    heading_re = re.compile(r"\n(?=[A-Z][A-Z\s/&:]{10,})\n", re.MULTILINE)
    raw = heading_re.split(text)
    if len(raw) < 3:
        # Fall back to paragraph split
        raw = re.split(r"\n{2,}", text)

    sections: list[str] = []
    buffer = ""
    for chunk in raw:
        chunk = chunk.strip()
        if not chunk or len(chunk.split()) < 5:
            continue
        if len(buffer) + len(chunk) < max_chars:
            buffer = (buffer + "\n\n" + chunk).strip()
        else:
            if buffer:
                sections.append(buffer)
            buffer = chunk
    if buffer:
        sections.append(buffer)
    return sections


def _is_flyer(filename: str) -> bool:
    return filename.lower().startswith("flyer") or filename.lower().startswith("shared")


_PDF_SESSION_RE = re.compile(
    r"^Sessions?\s+(\d{1,2})(?:\s*[–—-]\s*(\d{1,2}))?"
    r"(?:\s*\(([^)]*)\))?\s*:\s*(.+?)\s*$",
    re.IGNORECASE,
)
_PDF_MODULE_RE = re.compile(
    r"^Modules?\s+(\d{1,2})(?:\s*[–—-]\s*(\d{1,2}))?\s*:\s*(.+?)\s*$",
    re.IGNORECASE,
)
_PDF_LABEL_RE = re.compile(
    r"^(Course description|Learning outcomes|Prerequisites|Course outline|Course content|"
    r"Course syllabus|Curriculum|Who should attend|Target audience|Assessment method|"
    r"Methodology|Training methodology|Laptop setup instructions|Course objectives)"
    r"(?:\s*:\s*(.*))?$",
    re.IGNORECASE,
)
_PDF_LABEL_TYPES = {
    "course description": "description",
    "learning outcomes": "outcomes",
    "prerequisites": "prerequisites",
    "course outline": "topics",
    "course content": "topics",
    "course syllabus": "topics",
    "curriculum": "topics",
    "who should attend": "audience",
    "target audience": "audience",
    "assessment method": "assessment",
    "methodology": "methodology",
    "training methodology": "methodology",
    "laptop setup instructions": "setup",
    "course objectives": "outcomes",
}
_PDF_MAJOR_LABELS = frozenset(_PDF_LABEL_TYPES)
_PDF_CONTACT_RE = re.compile(
    r"(?:Timmins Training Consulting Sdn\.?\s*Bhd|Komplek Danau Kota|Taman Zeta|"
    r"\bOffice\s*:.*\bMobile\s*:|raj@consult-timmins\.com)",
    re.IGNORECASE,
)
_INTAKE_DATE_RANGE_RE = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s*[–—-]\s*"
    r"(\d{1,2})(?:st|nd|rd|th)?\s+"
    r"(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(\d{4})\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _PdfSection:
    heading: str
    body: str
    page_start: int
    page_end: int
    metadata: dict[str, object]


def _normalized_pdf_line(line: str) -> str:
    line = line.replace("\u200b", "").replace("\ufeff", "").replace("\xa0", " ")
    line = re.sub(r"[ \t]+", " ", line)
    return line.strip()


def _clean_pdf_pages(pages: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Remove recurring page-margin boilerplate before it can pollute embeddings."""
    margin_occurrences: dict[str, set[int]] = defaultdict(set)
    normalized_pages: list[tuple[int, list[str]]] = []
    for page_number, text in pages:
        lines = [_normalized_pdf_line(line) for line in text.splitlines()]
        lines = [line for line in lines if line]
        normalized_pages.append((page_number, lines))
        for line in [*lines[:5], *lines[-5:]]:
            margin_occurrences[line.casefold()].add(page_number)

    recurring_margin = {
        line for line, page_numbers in margin_occurrences.items() if len(page_numbers) >= 2
    }
    cleaned: list[tuple[int, str]] = []
    for page_number, lines in normalized_pages:
        kept = [
            line
            for line in lines
            if not _PDF_CONTACT_RE.search(line) and line.casefold() not in recurring_margin
        ]
        cleaned.append((page_number, "\n".join(kept)))
    return cleaned


def _intake_signature(text: str) -> tuple[int, int, str, int] | None:
    match = _INTAKE_DATE_RANGE_RE.search(text)
    if not match:
        return None
    start, end = int(match.group(1)), int(match.group(2))
    return min(start, end), max(start, end), match.group(3).casefold(), int(match.group(4))


def _course_intake_facts(slug: str) -> dict[str, object]:
    config_path = _COURSES_DIR / slug / "config.yaml"
    if not config_path.exists():
        return {}
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        logger.exception("could not read course intake config: %s", config_path)
        return {}
    return {
        "active": bool(data.get("active", True)),
        "dates": str(data.get("dates") or "").strip(),
        "venue": str(data.get("venue") or "").strip(),
    }


def _filter_flyer_intake_rows(
    pages: list[tuple[int, str]], *, active_dates: str, active: bool = True
) -> tuple[list[tuple[int, str]], dict[str, object]]:
    """Keep reusable flyer content while removing schedule rows for stale intakes."""
    active_signature = _intake_signature(active_dates)
    if active and active_signature is None:
        return pages, {"intake_filter_status": "unversioned"}

    filtered_pages: list[tuple[int, str]] = []
    stale_rows = 0
    active_rows = 0
    for page_number, text in pages:
        kept: list[str] = []
        for line in text.splitlines():
            row_signature = _intake_signature(line)
            if row_signature is None:
                kept.append(line)
            elif not active or row_signature != active_signature:
                stale_rows += 1
            else:
                active_rows += 1
                kept.append(line)
        filtered_pages.append((page_number, "\n".join(kept)))

    if not active:
        status = "inactive_schedule_removed"
    elif stale_rows and active_rows:
        status = "filtered_to_active_intake"
    elif stale_rows:
        status = "stale_schedule_removed_no_active_row"
    elif active_rows:
        status = "active_intake_match"
    else:
        status = "no_schedule_rows"
    return filtered_pages, {
        "intake_filter_status": status,
        "active_intake_dates": active_dates,
        "active_schedule_rows": active_rows,
        "stale_schedule_rows_removed": stale_rows,
        "commercial_authority": "course_config",
    }


def _number(value: str) -> int | float:
    parsed = float(value)
    return int(parsed) if parsed.is_integer() else parsed


def _hours_in(text: str) -> int | float | None:
    match = re.search(r"\b(\d+(?:\.\d+)?)\s*hours?\b", text, re.IGNORECASE)
    return _number(match.group(1)) if match else None


def _schedule_metadata(pages: list[tuple[int, str]]) -> dict[str, object]:
    """Summarize only schedule relationships explicitly printed in the PDF."""
    lines = [line for _, text in pages for line in text.splitlines() if line.strip()]
    duration_line = next(
        (line for line in lines if re.match(r"^(?:Course\s+)?Duration\s*:", line, re.I)),
        "",
    )
    declared_hours = _hours_in(duration_line)
    declared_days_match = re.search(
        r"\b(\d+(?:\.\d+)?)\s*days?\b", duration_line, re.IGNORECASE
    )
    declared_days = _number(declared_days_match.group(1)) if declared_days_match else None

    current_day: tuple[int, int] | None = None
    sessions: dict[int, tuple[int | float | None, tuple[int, int] | None]] = {}
    modules: set[int] = set()
    explicit_ranges: set[tuple[int, int]] = set()
    for line in lines:
        label = _PDF_LABEL_RE.match(line)
        if label and label.group(1).casefold() in _PDF_MAJOR_LABELS:
            current_day = None
        day_range = day_range_from_heading(line)
        if day_range:
            current_day = day_range
            explicit_ranges.add(day_range)
            continue
        session = _PDF_SESSION_RE.match(line)
        if session:
            session_number = int(session.group(1))
            sessions[session_number] = (_hours_in(session.group(3) or ""), current_day)
            continue
        module = _PDF_MODULE_RE.match(line)
        if module:
            modules.add(int(module.group(1)))

    metadata: dict[str, object] = {}
    if declared_hours is not None:
        metadata["declared_hours"] = declared_hours
    if declared_days is not None:
        metadata["declared_days"] = declared_days
    if sessions:
        metadata["session_count"] = len(sessions)
        mapped = sum(1 for _, day_range in sessions.values() if day_range is not None)
        metadata["day_mapping"] = (
            "explicit" if mapped == len(sessions) else "partial" if mapped else "not_explicit"
        )
    elif explicit_ranges:
        metadata["day_mapping"] = "explicit_day_ranges"
    else:
        metadata["day_mapping"] = "not_applicable"
    if modules:
        metadata["module_count"] = len(modules)
    if explicit_ranges:
        metadata["explicit_day_ranges"] = [list(value) for value in sorted(explicit_ranges)]

    session_hours = [duration for duration, _ in sessions.values() if duration is not None]
    if sessions and len(session_hours) == len(sessions):
        total = sum(session_hours)
        metadata["session_hours_total"] = total
        if declared_hours is not None:
            metadata["duration_status"] = (
                "consistent" if abs(float(total) - float(declared_hours)) < 0.01 else "conflict"
            )

    if sessions and explicit_ranges:
        metadata["schedule_granularity"] = "day_and_session"
    elif sessions:
        metadata["schedule_granularity"] = "session"
    elif explicit_ranges and modules:
        metadata["schedule_granularity"] = "day_and_module"
    elif explicit_ranges:
        metadata["schedule_granularity"] = "day"
    else:
        metadata["schedule_granularity"] = "unstructured"
    return metadata


def _heading_metadata(line: str) -> tuple[str, str, dict[str, object]] | None:
    clean = line.strip().rstrip(":")
    day_range = day_range_from_heading(clean)
    if day_range:
        section_type = "outcomes" if "outcome" in clean.casefold() else "agenda"
        return clean, "", {
            "section_type": section_type,
            "day_start": day_range[0],
            "day_end": day_range[1],
            "section_heading": clean,
        }
    session = _PDF_SESSION_RE.match(clean)
    if session:
        start = int(session.group(1))
        metadata: dict[str, object] = {
            "section_type": "agenda",
            "session_start": start,
            "session_end": int(session.group(2) or start),
            "session_title": session.group(4).strip(),
            "section_heading": clean,
        }
        duration = _hours_in(session.group(3) or "")
        if duration is not None:
            metadata["duration_hours"] = duration
        return clean, "", metadata
    module = _PDF_MODULE_RE.match(clean)
    if module:
        start = int(module.group(1))
        return clean, "", {
            "section_type": "agenda",
            "module_start": start,
            "module_end": int(module.group(2) or start),
            "module_title": module.group(3).strip(),
            "section_heading": clean,
        }
    label = _PDF_LABEL_RE.match(line)
    if label:
        heading = label.group(1).strip()
        return heading, (label.group(2) or "").strip(), {
            "section_type": _PDF_LABEL_TYPES[heading.casefold()],
            "section_heading": heading,
        }
    return None


def _structured_pdf_sections(
    pages: list[tuple[int, str]], *, default_title: str
) -> tuple[list[_PdfSection], dict[str, object]]:
    """Preserve cross-page day/session/module structure instead of flattening each page."""
    cleaned_pages = _clean_pdf_pages(pages)
    schedule = _schedule_metadata(cleaned_pages)
    sections: list[_PdfSection] = []
    heading = default_title
    body: list[str] = []
    metadata: dict[str, object] = {"section_type": "general", "section_heading": heading}
    page_start = cleaned_pages[0][0] if cleaned_pages else 1
    page_end = page_start
    current_day: tuple[int, int] | None = None
    current_day_heading = ""

    def flush() -> None:
        text = "\n".join(body).strip()
        if text and len(text.split()) >= 3:
            sections.append(_PdfSection(heading, text, page_start, page_end, dict(metadata)))

    for page_number, text in cleaned_pages:
        for line in text.splitlines():
            possible_heading = _heading_metadata(line)
            if possible_heading is None:
                body.append(line)
                page_end = page_number
                continue

            flush()
            new_heading, inline_body, new_metadata = possible_heading
            heading = new_heading
            body = [inline_body] if inline_body else []
            metadata = dict(new_metadata)
            page_start = page_number
            page_end = page_number

            label_key = new_heading.casefold()
            if label_key in _PDF_MAJOR_LABELS:
                current_day = None
                current_day_heading = ""
            explicit_day = day_range_from_heading(new_heading)
            if explicit_day:
                current_day = explicit_day
                current_day_heading = new_heading
            elif "session_start" in metadata or "module_start" in metadata:
                if current_day is not None:
                    metadata["day_start"] = current_day[0]
                    metadata["day_end"] = current_day[1]
                    metadata["day_heading"] = current_day_heading
        page_end = page_number
    flush()
    return sections, schedule


def build_pdf_documents() -> list[CorpusDocument]:
    """Parse PDFs into structure-aware course sections with explicit provenance."""
    documents: list[CorpusDocument] = []
    if not _PDF_DIR.exists():
        logger.warning("NEW_Courses directory not found: %s", _PDF_DIR)
        return documents

    seen_content: dict[str, str] = {}  # slug → accumulated text (dedup)

    for path in sorted(_PDF_DIR.glob("*.pdf")):
        slug = _slug_for(path.stem)
        if not slug:
            logger.warning("No course slug matched for pdf: %s", path.name)
            continue

        pages = _extract_pdf_pages(path)
        text = "\n".join(page_text for _, page_text in pages)
        if not text.strip():
            continue

        doc_type = "course_flyer" if _is_flyer(path.name) else "course_hrdc_outline"

        # Skip near-duplicate files (same course, very similar extracted text)
        fingerprint = text[:400]
        existing = seen_content.get(slug, "")
        if existing and fingerprint[:200] in existing:
            logger.info("kb_ingestion: skipping near-duplicate pdf: %s", path.name)
            continue
        seen_content[slug] = seen_content.get(slug, "") + fingerprint

        intake_metadata: dict[str, object] = {}
        if doc_type == "course_flyer":
            intake_facts = _course_intake_facts(slug)
            pages, intake_metadata = _filter_flyer_intake_rows(
                pages,
                active_dates=str(intake_facts.get("dates") or ""),
                active=bool(intake_facts.get("active", True)),
            )
            if intake_facts.get("venue"):
                intake_metadata["active_intake_venue"] = intake_facts["venue"]

        sections, schedule_metadata = _structured_pdf_sections(
            pages, default_title=path.stem[:100]
        )
        for section_index, section in enumerate(sections, start=1):
            page_anchor = (
                f"page={section.page_start}"
                if section.page_start == section.page_end
                else f"pages={section.page_start}-{section.page_end}"
            )
            documents.append(
                CorpusDocument(
                    document_id=(
                        f"pdf-{slug}-{path.stem[:30].replace(' ', '_')}"
                        f"-p{section.page_start:03d}-s{section_index:03d}"
                    ),
                    title=section.heading[:120],
                    text=f"{section.heading}\n{section.body}",
                    source_ref=f"NEW_Courses/{path.name}#{page_anchor}",
                    course_id=slug,
                    topic=doc_type,
                    metadata={
                        "authority": "approved_pdf",
                        "source_file": path.name,
                        "doc_type": doc_type,
                        "section_index": section_index,
                        "page_start": section.page_start,
                        "page_end": section.page_end,
                        **schedule_metadata,
                        **intake_metadata,
                        **section.metadata,
                    },
                )
            )

    pdf_count = len(list(_PDF_DIR.glob("*.pdf")))
    logger.info("kb_ingestion: loaded %d sections from %d pdf files", len(documents), pdf_count)
    return documents


# ── Root BRS docx ─────────────────────────────────────────────────────────────

# Sections in the BRS document and how they map to topics/course_id.
# Key = lowercase substring that must appear in the section heading line.
_BRS_SECTION_MAP: list[tuple[str, str, str | None]] = [
    ("company knowledge base", "company_kb", None),
    ("operations knowledge base", "operations_kb", None),
    ("software testing course", "course_kb", "sw-testing-aug-2026"),
    ("software testing july 2026 campaign", "campaign_kb", "sw-testing-july-2026"),
    ("software testing", "campaign_kb", "sw-testing-july-2026"),
]

_BRS_SECTION_RE = re.compile(r"(?m)^(SECTION\s+\d+\s*[–—-][^\n]*)\n")
_BRS_DOCX_PATH = _ROOT / "TIMMINS TRAINING - Meta Lead sautomation.docx"


def _classify_brs_section(heading: str) -> tuple[str, str | None]:
    lower = heading.lower()
    for keyword, topic, slug in _BRS_SECTION_MAP:
        if keyword in lower:
            return topic, slug
    return "brs_general", None


def build_brs_documents() -> list[CorpusDocument]:
    """
    Parse the root BRS docx (business rules + company + operations + course Q&A)
    into per-Q&A CorpusDocuments, assigning course_id and topic per section.
    """
    if not _BRS_DOCX_PATH.exists():
        logger.warning("BRS docx not found: %s", _BRS_DOCX_PATH)
        return []

    text = _extract_docx_text(_BRS_DOCX_PATH)
    if not text.strip():
        return []

    # Build a list of (heading, body) pairs using finditer — keeps full title
    matches = list(_BRS_SECTION_RE.finditer(text))
    raw_sections: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        heading = m.group(1).strip()
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        raw_sections.append((heading, text[body_start:body_end].strip()))
    preamble_text = text[: matches[0].start()].strip() if matches else text.strip()

    documents: list[CorpusDocument] = []
    doc_counter = 0

    # Only the two general, client-approved Q&A sections belong in the general vector
    # knowledge base. Course-specific material comes from each course's own overview,
    # KB, flyer, and HRDC outline; dated campaign content is intentionally excluded.
    del preamble_text
    customer_safe_topics = {"company_kb", "operations_kb"}
    skipped_sections = 0

    for heading_line, section_text in raw_sections:
        section_text = section_text.strip()
        if not section_text:
            continue
        topic, slug = _classify_brs_section(heading_line)
        if topic not in customer_safe_topics:
            skipped_sections += 1
            continue

        # Split into Q&A items; fall back to whole section if no Q numbering
        qa_sections = _split_qa_sections(section_text)
        if not qa_sections:
            qa_sections = [section_text]

        for i, qa in enumerate(qa_sections):
            if len(qa.split()) < 5:
                continue
            first_line = qa.splitlines()[0].strip()
            title = (
                f"{heading_line.strip()} – {first_line[:80]}"
                if first_line
                else heading_line.strip()
            )
            doc_counter += 1
            documents.append(
                CorpusDocument(
                    document_id=f"brs-{topic}-q{doc_counter:03d}",
                    title=title[:120],
                    text=qa,
                    source_ref="TIMMINS TRAINING - Meta Lead sautomation.docx",
                    course_id=slug,
                    topic=topic,
                    metadata={
                        "authority": "approved_brs_docx",
                        "source_file": _BRS_DOCX_PATH.name,
                        "section": heading_line.strip(),
                        "section_index": i + 1,
                    },
                )
            )

    logger.info(
        "kb_ingestion: loaded %d customer-safe Q&A sections from BRS docx (%s); "
        "skipped_internal_sections=%d",
        len(documents),
        _BRS_DOCX_PATH.name,
        skipped_sections,
    )
    return documents


# ── Public entry ──────────────────────────────────────────────────────────────


def build_kb_documents() -> list[CorpusDocument]:
    """Return all KB documents from docx + pdf sources + root BRS."""
    docs = build_docx_documents() + build_pdf_documents() + build_brs_documents()
    logger.info("kb_ingestion: total external documents=%d", len(docs))
    return docs


def kb_content_version() -> tuple:
    """Fingerprint source files so changes invalidate the RAG index cache."""
    files: list[Path] = []
    if _KB_DIR.exists():
        files.extend(sorted(_KB_DIR.glob("*.docx")))
    if _PDF_DIR.exists():
        files.extend(sorted(_PDF_DIR.glob("*.pdf")))
    if _BRS_DOCX_PATH.exists():
        files.append(_BRS_DOCX_PATH)
    return tuple(
        (str(p.relative_to(_ROOT)), p.stat().st_mtime_ns, p.stat().st_size) for p in sorted(files)
    )
