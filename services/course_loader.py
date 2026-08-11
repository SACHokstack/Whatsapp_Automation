from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[1]
_COURSES_DIR = _ROOT / "courses"


@dataclass
class CourseConfig:
    slug: str
    name: str
    active: bool
    dates: str
    venue: str
    fees: dict
    hrdc_deadline: str
    payment_deadline: str
    hot_budget_threshold: int
    keywords: list[str]
    overview: str
    outreach: dict = field(default_factory=dict)

    @property
    def worksheet_name(self) -> str:
        return self.outreach.get("worksheet_name", self.name)

    @property
    def outreach_template(self) -> str:
        return self.outreach.get("template_name", "")

    @property
    def outreach_language(self) -> str:
        return self.outreach.get("template_language", "en_US")

    @property
    def name_col(self) -> str:
        return self.outreach.get("name_column", "full_name")

    @property
    def phone_col(self) -> str:
        return self.outreach.get("phone_column", "phone")

    @property
    def status_col(self) -> str:
        return self.outreach.get("status_column", "lead_status")


def _strip_notes(text: str) -> str:
    parts = re.split(r"^\s*---\s*$", text, maxsplit=1, flags=re.MULTILINE)
    return parts[0].strip()


def course_content_version() -> tuple[tuple[str, int, int], ...]:
    """Fingerprint course files so edits invalidate the process-local cache."""
    if not _COURSES_DIR.exists():
        return ()
    files = sorted(_COURSES_DIR.glob("*/config.yaml")) + sorted(_COURSES_DIR.glob("*/overview.md"))
    return tuple(
        (str(path.relative_to(_ROOT)), path.stat().st_mtime_ns, path.stat().st_size)
        for path in files
    )


@lru_cache(maxsize=4)
def _load_courses_cached(_version) -> dict[str, CourseConfig]:
    courses: dict[str, CourseConfig] = {}
    if not _COURSES_DIR.exists():
        return courses

    for config_path in sorted(_COURSES_DIR.glob("*/config.yaml")):
        slug = config_path.parent.name
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        overview = ""
        overview_path = config_path.parent / "overview.md"
        if overview_path.exists():
            overview = _strip_notes(overview_path.read_text(encoding="utf-8").strip())

        courses[slug] = CourseConfig(
            slug=slug,
            name=data.get("name", slug),
            active=bool(data.get("active", True)),
            dates=str(data.get("dates", "")),
            venue=str(data.get("venue", "")),
            fees=data.get("fees") or {},
            hrdc_deadline=str(data.get("hrdc_deadline", "")),
            payment_deadline=str(data.get("payment_deadline", "")),
            hot_budget_threshold=int(data.get("hot_budget_threshold", 4000)),
            keywords=[str(k).lower() for k in (data.get("keywords") or [])],
            overview=overview,
            outreach=data.get("outreach") or {},
        )

    return courses


def load_courses() -> dict[str, CourseConfig]:
    return _load_courses_cached(course_content_version())


def get_active_courses() -> list[CourseConfig]:
    return [c for c in load_courses().values() if c.active]


def get_course(slug: str) -> CourseConfig | None:
    return load_courses().get(slug)


def refresh_courses() -> dict[str, CourseConfig]:
    """Reload course YAML and overview files without restarting the process."""
    _load_courses_cached.cache_clear()
    return load_courses()


def _normalized_reference(value: str) -> str:
    normalized = re.sub(r"(\d+)(?:st|nd|rd|th)\b", r"\1", value.lower())
    return " ".join(re.sub(r"[^a-z0-9]+", " ", normalized).split())


def _hrdc_registration_no(course: CourseConfig) -> str:
    configured = str(course.outreach.get("hrdc_registration_no") or "")
    if configured:
        return re.sub(r"\D", "", configured)
    match = re.search(r"HRDC\s+Registration\s+No\s*:\s*(\d+)", course.overview, re.IGNORECASE)
    return match.group(1) if match else ""


def detect_explicit_course(message: str) -> CourseConfig | None:
    """Resolve strong identifiers without guessing from broad technology words."""
    active = get_active_courses()
    normalized = _normalized_reference(message)
    digits = re.sub(r"\D", "", message)

    for course in active:
        registration_no = _hrdc_registration_no(course)
        if registration_no and registration_no in digits:
            return course
        if _normalized_reference(course.name) in normalized:
            return course

    months = (
        "january",
        "february",
        "march",
        "april",
        "may",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
    )
    month_abbrev = {
        "jan": "january",
        "feb": "february",
        "mar": "march",
        "apr": "april",
        "jun": "june",
        "jul": "july",
        "aug": "august",
        "sep": "september",
        "oct": "october",
        "nov": "november",
        "dec": "december",
    }
    date_matches = []
    message_numbers = {int(value) for value in re.findall(r"\b\d{1,2}\b", normalized)}
    for course in active:
        date_text = _normalized_reference(course.dates)
        month = next((value for value in months if value in date_text), None)
        if month is None:
            continue
        month_in_msg = month in normalized
        if not month_in_msg:
            abbrev = next((abbr for abbr, full in month_abbrev.items() if full == month), None)
            if abbrev and abbrev in normalized:
                month_in_msg = True
        days = [int(value) for value in re.findall(r"\b\d{1,2}\b", date_text)[:2]]
        if month_in_msg and len(days) == 2 and set(days) <= message_numbers:
            date_matches.append(course)
    return date_matches[0] if len(date_matches) == 1 else None


# Generic content/logistics words that describe ANY course question, not a specific
# course. They must never let keyword scoring "identify" a course — otherwise a bare
# "what is the syllabus?" (no course named) falsely resolves to whichever config happens
# to list that word, silently answering about the wrong course.
_GENERIC_KEYWORDS = frozenset(
    {
        "course content", "curriculum", "syllabus", "outline", "course outline",
        "who should attend", "topics", "course structure", "structure", "agenda",
        "breakdown", "content", "fees", "fee", "price", "cost", "schedule", "dates",
        "duration", "certification", "certificate", "hrdc", "venue", "location",
    }
)


def detect_course(message: str) -> CourseConfig | None:
    active = get_active_courses()
    if not active:
        return None
    if len(active) == 1:
        return active[0]

    explicit = detect_explicit_course(message)
    if explicit:
        return explicit

    msg_lower = message.lower()
    scored: list[tuple[int, CourseConfig]] = []
    for course in active:
        score = sum(
            1 for kw in course.keywords if kw not in _GENERIC_KEYWORDS and kw in msg_lower
        )
        if score:
            scored.append((score, course))

    if not scored:
        return None
    best_score = max(score for score, _ in scored)
    winners = [course for score, course in scored if score == best_score]
    return winners[0] if len(winners) == 1 else None


def course_context_text(course: CourseConfig) -> str:
    fees = course.fees
    lines = [f"[{course.name.upper()}]"]
    if not course.active:
        lines.append("Status: This intake is no longer active; confirm the next available intake.")
    if course.dates:
        lines.append(f"Dates: {course.dates}")
    if course.venue:
        lines.append(f"Venue: {course.venue}")
    if fees:
        lines.append("Fees:")
        if "standard" in fees:
            lines.append(f"- Standard: RM{fees['standard']:,} per participant")
        if "group_2" in fees:
            lines.append(f"- Group of 2: RM{fees['group_2']:,} per participant")
        if "group_3_plus" in fees:
            lines.append(f"- Group of 3 or more: RM{fees['group_3_plus']:,} per participant")
    if course.hrdc_deadline:
        lines.append(f"HRDC Approval Deadline: {course.hrdc_deadline}")
    if course.payment_deadline:
        lines.append(f"Payment Deadline: {course.payment_deadline}")
    return "\n".join(lines)
