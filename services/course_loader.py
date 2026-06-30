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


@lru_cache(maxsize=1)
def load_courses() -> dict[str, CourseConfig]:
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


def get_active_courses() -> list[CourseConfig]:
    return [c for c in load_courses().values() if c.active]


def get_course(slug: str) -> CourseConfig | None:
    return load_courses().get(slug)


def detect_course(message: str) -> CourseConfig | None:
    active = get_active_courses()
    if not active:
        return None
    if len(active) == 1:
        return active[0]

    msg_lower = message.lower()
    best: CourseConfig | None = None
    best_score = 0
    for course in active:
        score = sum(1 for kw in course.keywords if kw in msg_lower)
        if score > best_score:
            best_score = score
            best = course

    return best  # None if no keywords matched — caller must handle


def course_context_text(course: CourseConfig) -> str:
    fees = course.fees
    lines = [f"[{course.name.upper()}]"]
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
