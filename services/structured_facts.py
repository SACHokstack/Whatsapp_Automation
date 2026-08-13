from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

import yaml

from services.course_loader import get_active_courses
from services.knowledge_base import load_knowledge_base

_POLICY_PATH = Path(__file__).resolve().parents[1] / "knowledge" / "policies.yaml"


def _content_source() -> str:
    return os.getenv("CONTENT_SOURCE", "files").strip().lower()


@lru_cache(maxsize=2)
def _load_policies_cached(_version) -> dict:
    if isinstance(_version, tuple) and _version and _version[0] == "db":
        from services.content_store import get_policies

        return get_policies() or {}
    with _POLICY_PATH.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_policies() -> dict:
    if _content_source() == "db":
        from services.content_store import content_version

        return _load_policies_cached(("db", content_version()))
    return _load_policies_cached(_POLICY_PATH.stat().st_mtime_ns)


def _course_duration_days(course) -> int | None:
    if course is None:
        return None
    text = f"{course.name}\n{course.overview}"
    match = re.search(
        r"\b(\d+)[- ]day\b|\b(\d+)\s+days\b|\((\d+)\s+days?\)",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    return int(next(group for group in match.groups() if group))


def _knowledge_text(topic: str) -> str | None:
    text = load_knowledge_base().get(topic)
    if not text:
        return None
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines and lines[0].startswith("#"):
        lines = lines[1:]
    return " ".join(line.lstrip("- ").strip() for line in lines).strip()


def _first_overview_paragraph(course) -> str:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", course.overview) if part.strip()]
    paragraph = next(
        (part for part in paragraphs if not part.startswith("#") and not part.endswith(":")),
        "",
    )
    paragraph = re.sub(r"\s*HRDC\s+Registration\s+No\s*:\s*\d+\.?", "", paragraph, flags=re.I)
    return re.sub(r"\s+", " ", paragraph).strip()


def _course_focus(course) -> str:
    paragraph = _first_overview_paragraph(course)
    focus = re.sub(
        rf"^{re.escape(course.name)}\s+is\s+",
        "",
        paragraph,
        flags=re.IGNORECASE,
    ).strip()
    return focus[:1].upper() + focus[1:] if focus else paragraph


def _section_bullets(course, *headings: str, limit: int = 3) -> list[str]:
    wanted = {heading.lower().rstrip(":") for heading in headings}
    collecting = False
    values: list[str] = []
    for raw_line in course.overview.splitlines():
        line = raw_line.strip()
        if not line:
            if collecting and values:
                break
            continue
        if line.lower().rstrip(":") in wanted:
            collecting = True
            continue
        if not collecting:
            continue
        if not line.startswith("-"):
            break
        values.append(line.lstrip("- ").strip().rstrip("."))
        if len(values) >= limit:
            break
    return values


def _qa_answer(course, question_fragment: str) -> str | None:
    blocks = re.finditer(
        r"^Q\d+\.\s*(?P<question>[^\n]+)\nAnswer:\s*\n(?P<answer>.*?)(?=^Q\d+\.|\Z)",
        course.overview,
        re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    fragment = question_fragment.lower()
    for block in blocks:
        if fragment not in block.group("question").lower():
            continue
        lines = [line.strip().lstrip("- ") for line in block.group("answer").splitlines()]
        return " ".join(line for line in lines if line).strip()
    return None


def _sentences(text: str | None, limit: int = 2) -> str:
    if not text:
        return ""
    parts = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", text).strip())
    return " ".join(parts[:limit]).strip()


def _mentioned_courses(message: str) -> list[object]:
    lower = " ".join(re.sub(r"[^a-z0-9]+", " ", message.lower()).split())
    found: list[object] = []
    for item in get_active_courses():
        name = " ".join(re.sub(r"[^a-z0-9]+", " ", item.name.lower()).split())
        aliases = {name}
        item_lower = item.name.lower()
        if "embedded c" in item_lower:
            aliases.update({"embedded c course", "embedded c"})
        if "system internals" in item_lower:
            aliases.update({"linux system internals", "system internals"})
        if "yocto" in item_lower:
            aliases.update({"yocto course", "embedded linux with yocto"})
        # "the embedded linux course" names a family, not one course. Matching it on the
        # embedded-Linux courses lets a comparison proceed with the closest one instead of
        # refusing; without this, "difference between this and embedded linux" resolved to
        # nothing and asked the customer to name both courses they had just named.
        if "embedded linux" in item_lower:
            aliases.update({"embedded linux", "embedded linux course"})
        if "linux kernel" in item_lower:
            aliases.update({"linux kernel course", "kernel programming"})
        if "python automation" in item_lower:
            aliases.update({"python automation course", "python course"})
        if "software testing" in item_lower:
            aliases.update({"software testing course", "modern software testing"})
        if "linux debugging" in item_lower:
            aliases.update({"linux debugging course", "linux debugging"})
        if any(re.search(rf"\b{re.escape(alias)}\b", lower) for alias in aliases):
            found.append(item)
    return found


def _documented_group_pricing(course, message: str) -> str:
    fees = course.fees or {}
    participant_count = None
    count_match = re.search(
        r"\bteam\s+of\s+(\d+)\b|\b(\d+)\s+(?:people|participants|pax)\b",
        message,
        re.IGNORECASE,
    )
    if count_match:
        participant_count = int(next(value for value in count_match.groups() if value))

    if participant_count is not None and participant_count >= 3 and fees.get("group_3_plus"):
        return (
            f"Yes. For a team of {participant_count}, the documented group rate for "
            f"{course.name} is RM{fees['group_3_plus']:,} per participant (the 3-or-more rate)."
        )
    if participant_count == 2 and fees.get("group_2"):
        return (
            f"Yes. For 2 participants, the documented group rate for {course.name} is "
            f"RM{fees['group_2']:,} per participant."
        )

    parts: list[str] = []
    if fees.get("group_2"):
        parts.append(f"RM{fees['group_2']:,} each for 2 participants")
    if fees.get("group_3_plus"):
        parts.append(f"RM{fees['group_3_plus']:,} each for 3 or more")
    if not parts:
        return (
            f"I don't have a documented group rate for {course.name}. "
            "I can ask a consultant to confirm whether a current promotion applies."
        )
    return f"Yes—{course.name} has documented group pricing: " + "; ".join(parts) + "."


def _course_value_answer(course, message: str) -> str:
    outcomes = _section_bullets(course, "Practical outcomes", limit=3)
    if not outcomes:
        outcomes = _section_bullets(course, "Key topics", limit=3)
    if "youtube" in message.lower() or "video" in message.lower():
        opening = (
            "YouTube can be useful for learning theory. The difference here is the structured, "
            f"hands-on practice in {course.name}:"
        )
    else:
        opening = (
            f"A fair way to judge whether {course.name} is worth it is by the practical outcomes, "
            "not just the number of topics:"
        )
    lines = [opening]
    lines.extend(f"• {item}" for item in outcomes)
    if not outcomes:
        overview = _first_overview_paragraph(course)
        if overview:
            lines.append(f"• {overview}")
    fees = course.fees or {}
    if fees.get("standard") and re.search(r"\b(?:worth|expensive|costly|rm\s*\d+)\b", message, re.I):
        rate = f"RM{fees['standard']:,} per participant"
        if fees.get("group_3_plus"):
            rate += f", or RM{fees['group_3_plus']:,} each for 3 or more"
        lines.append(f"The documented fee is {rate}.")
    lines.append(
        "If you mainly want an introduction, self-study may be enough; if you need guided practice "
        "on these outcomes, this course is designed for that. Tell me your current role and I can "
        "give you an honest fit check."
    )
    return "\n".join(lines)


def _course_comparison_answer(course, message: str) -> str:
    mentioned = _mentioned_courses(message)
    candidates = [item for item in mentioned if course is None or item.slug != course.slug]
    if course is None and len(mentioned) >= 2:
        left, right = mentioned[:2]
    elif course is not None and candidates:
        left, right = course, candidates[0]
    else:
        return (
            "I can compare them without guessing, but I need both course names. "
            "Which two courses would you like me to compare?"
        )

    lines = ["They serve different learning goals:"]
    for item in (left, right):
        overview = _course_focus(item)
        duration = _course_duration_days(item)
        duration_text = f" ({duration} days)" if duration else ""
        lines.append(f"• {item.name}{duration_text}: {overview}")

    left_prereqs = _section_bullets(left, "Prerequisites", "Recommended background", limit=2)
    right_prereqs = _section_bullets(right, "Prerequisites", "Recommended background", limit=2)
    if left_prereqs or right_prereqs:
        lines.append("The starting level is also different:")
        if left_prereqs:
            lines.append(f"• {left.name}: " + "; ".join(left_prereqs))
        if right_prereqs:
            lines.append(f"• {right.name}: " + "; ".join(right_prereqs))
    lines.append("Tell me your current background and goal, and I can recommend which one fits better.")
    return "\n".join(lines)


_TRAINER_FACT_PREFIXES = (
    "the course is delivered by ",
    "the trainer has ",
    "instructor-led guidance from ",
    "guided troubleshooting by ",
)


def _course_trainer_fact(course) -> str | None:
    return next(
        (
            line.strip().strip("- ").rstrip(".") + "."
            for line in course.overview.splitlines()
            if line.strip().strip("- ").lower().startswith(_TRAINER_FACT_PREFIXES)
        ),
        None,
    )


def _trainer_catalog_secondary_fields(message: str) -> set[str]:
    """Read additional asks without treating relative words as standalone intents."""
    lower = message.lower().replace("’", "'")
    fields: set[str] = set()
    if re.search(r"\b(?:fee|fees|price|pricing|cost|how much|investment)\b", lower):
        fields.add("fees")
    if re.search(r"\b(?:schedule|dates?|when|next batch)\b", lower):
        fields.add("schedule")
    if re.search(r"\b(?:duration|how many days|how long)\b", lower):
        fields.add("duration")
    if re.search(r"\b(?:hrdc|hrdf|claimable|grant)\b", lower):
        fields.add("hrdc")
    if re.search(
        r"\b(?:venue|location)\b|\bwhere\s+(?:is|are|will|would|does|do)\b|"
        r"\bwhere can (?:i|we) (?:attend|join|go|take)\b|\b(?:held|take place)\b",
        lower,
    ):
        fields.add("venue")
    return fields


def _trainer_catalog_details(course, fields: set[str]) -> str:
    details: list[str] = []
    if "schedule" in fields and course.dates:
        details.append(course.dates)
    if "venue" in fields and course.venue:
        details.append(course.venue)
    duration_days = _course_duration_days(course) if "duration" in fields else None
    if duration_days is not None:
        details.append(f"{duration_days} days")
    standard_fee = course.fees.get("standard") if course.fees else None
    if "fees" in fields and standard_fee is not None:
        details.append(f"RM{int(standard_fee):,} per participant")
    if "hrdc" in fields:
        details.append("HRDC claimable" if course.hrdc_deadline else "HRDC status not confirmed")
    return " Details: " + "; ".join(details) + "." if details else ""


def exact_answer(intent: str, course=None, *, message: str = "") -> str | None:
    policies = load_policies()
    if intent == "BOT_IDENTITY":
        return (
            "Yes—I'm Timmins' automated course assistant. I can help with course details, fees, "
            "schedules, HRDC, and registration. If you'd prefer a person, just say “human” and "
            "I'll connect you with a consultant."
        )

    if intent == "FRUSTRATION":
        return (
            "You're right to be frustrated—this hasn't been helpful enough. Tell me the one thing "
            "you need and I'll answer it directly, or say “human” and I'll connect you with a consultant."
        )

    if intent == "REPETITION_REPAIR":
        return (
            "You're right—I repeated information you had already asked about. Sorry about that. "
            "I won't repeat it; what would you like to know next?"
        )

    if intent == "PARTICIPANT_REPLACEMENT":
        return (
            "Participant replacement is generally allowed before the training starts. For a "
            "last-minute change, send Timmins the replacement participant's details as soon as "
            "possible so the registration and any HRDC paperwork can be confirmed."
        )

    if intent == "HRDC_DOCUMENTS":
        course_prefix = f"For {course.name}, " if course is not None else ""
        return (
            f"{course_prefix}Timmins can provide these documents/details for the HRDC grant application:\n"
            "• Official quotation\n"
            "• HRDC-registered course outline\n"
            "• Trainer profile\n"
            "• HRDC registration information\n\n"
            "Your company HR/L&D team submits the application through e-TRiS."
        )

    if intent == "DISCOUNT":
        if course is None:
            return "Group rates differ by course. Which course and how many participants are you considering?"
        return _documented_group_pricing(course, message)

    if intent == "COURSE_VALUE":
        if course is None:
            return "Which course are you evaluating? I'll give you an honest, practical value check."
        return _course_value_answer(course, message)

    if intent == "COURSE_COMPARISON":
        return _course_comparison_answer(course, message)

    if intent == "COURSE_INTRO":
        if course is None:
            return (
                "You're in the right place. Timmins runs technical courses in Embedded C, Embedded "
                "Linux, Python automation, and software testing. Which one did you mean?"
            )
        overview = _first_overview_paragraph(course)
        return (
            f"You're in the right place. {overview} "
            "I can explain it simply, or share the fees, dates, and course outline—which would help?"
        )

    if intent == "COURSE_CONFIRMATION":
        if course is None:
            return "I don't want to guess—which course name or topic do you remember?"
        lower_name = course.name.lower()
        if "embedded c" in lower_name:
            distinction = "This is the C course, covering C fundamentals, pointers, and GDB—not an Embedded Linux course."
        elif "linux" in lower_name:
            distinction = "This is one of the Linux courses, not the Embedded C fundamentals course."
        elif "python" in lower_name:
            distinction = "This is the Python automation course."
        elif "testing" in lower_name:
            distinction = "This is the software-testing and automation course."
        else:
            distinction = f"This conversation is about {course.name}."
        return f"You're currently asking about {course.name}. {distinction}"

    if intent == "BEGINNER_FIT":
        if course is None:
            return "I can check that against the approved prerequisites—which course do you mean?"
        lower = message.lower()
        beginner = _sentences(_qa_answer(course, "suitable for beginners"), limit=3)
        if "gdb" in lower:
            definition = _sentences(_qa_answer(course, "what is gdb"), limit=2)
            taught = _qa_answer(course, "gdb be taught from scratch") or ""
            lines = []
            if definition:
                lines.append(definition)
            if "no previous gdb experience is required" in taught.lower():
                lines.append("GDB is taught from scratch, and no previous GDB experience is required.")
            if beginner:
                lines.append(beginner)
            if re.search(r"\b(?:old|age)\b", lower):
                lines.append(
                    "The listed prerequisites focus on your programming background, not your age."
                )
            return " ".join(dict.fromkeys(line for line in lines if line)) or None
        if beginner:
            prefix = (
                "The listed prerequisites focus on your current knowledge, not your age. "
                if re.search(r"\b(?:old|age)\b", lower)
                else ""
            )
            return prefix + beginner
        return None

    if intent == "FREE_TUTORING":
        course_name = course.name if course is not None else "the course"
        reply = (
            f"I can explain {course_name} and share its syllabus or a learning roadmap, but I "
            "can't deliver the full training program inside this chat."
        )
        # Only surface a complimentary prep program if the approved course material actually
        # documents one (e.g. a 1-day live prep session for registered participants).
        if course is not None and "complimentary 1-day" in course.overview.lower():
            reply += (
                " The approved course material does include a complimentary 1-day live prep "
                "program for registered participants before the main course."
            )
        return reply

    if intent == "TRAINER_CATALOG":
        secondary_fields = _trainer_catalog_secondary_fields(message)
        detailed: list[tuple[object, str]] = []
        brief: list[tuple[object, str]] = []
        for item in get_active_courses():
            fact = _course_trainer_fact(item)
            if not fact:
                continue
            target = detailed if "years" in fact.lower() or "trained teams" in fact.lower() else brief
            target.append((item, fact))

        if not detailed and not brief:
            return (
                "I don't have a verified trainer profile for any current course. "
                "I can ask our consultant to share the relevant profile."
            )

        lines = ["Verified trainer information is available for:"]
        lines.extend(
            f"- {item.name}: {fact}{_trainer_catalog_details(item, secondary_fields)}"
            for item, fact in detailed
        )
        if brief:
            names = ", ".join(item.name for item, _ in brief)
            lines.append(
                "Brief trainer descriptions—not full profiles—are available for: " + names + "."
            )
        lines.append(
            "The approved materials do not give a trainer's name. A consultant can share the full relevant profile."
        )
        return "\n".join(lines)

    if intent == "CATALOG":
        lower = message.lower().strip()
        asks_fees = bool(
            re.search(r"\b(?:fee|fees|price|pricing|cost|how much|investment)\b", lower)
        )
        asks_hrdc = bool(re.search(r"\b(?:hrdc|hrdf|claimable|grant)\b", lower))
        if lower in {
            "this course",
            "that course",
            "how much for this course?",
            "how much is this course?",
        }:
            return (
                "I mentioned several courses, so I don't want to assume. "
                "Which course would you like details about?"
            )
        courses = get_active_courses()
        asks_same_duration = course is not None and (
            "same duration" in lower
            or "this duration" in lower
            or "in this duration" in lower
            or ("duration" in lower and "other" in lower and "course" in lower)
        )
        if asks_same_duration:
            target_days = _course_duration_days(course)
            if target_days is not None:
                courses = [
                    item
                    for item in courses
                    if item.slug != course.slug and _course_duration_days(item) == target_days
                ]
                if not courses:
                    return (
                        f"I don't see another active {target_days}-day course in the current schedule. "
                        "If you're open to a different duration, I can list the other active courses."
                    )
                lines = [f"Other active {target_days}-day courses:"]
                lines.extend(f"- {item.name}: {item.dates}, {item.venue}" for item in courses)
                lines.append("Which one would you like details about?")
                return "\n".join(lines)
        months = ("july", "august", "september", "october", "november", "december")
        month = next((value for value in months if value in lower), None)
        if month:
            courses = [item for item in courses if month in item.dates.lower()]
        if "embedded linux" in lower:
            courses = [item for item in courses if "embedded linux" in item.name.lower()]
        elif "embedded" in lower:
            courses = [
                item
                for item in courses
                if "embedded" in item.name.lower() or "linux kernel" in item.name.lower()
            ]
        if "other" in lower and course is not None:
            courses = [item for item in courses if item.slug != course.slug]
        if not courses:
            return "I don't see a matching active course in the current schedule."
        lines = ["These matching courses are currently scheduled:"]
        for item in courses:
            details = [item.dates, item.venue]
            standard_fee = item.fees.get("standard") if item.fees else None
            if asks_fees and standard_fee is not None:
                details.append(f"RM{int(standard_fee):,} per participant")
            if asks_hrdc:
                details.append("HRDC claimable" if item.hrdc_deadline else "HRDC not confirmed")
            lines.append(f"- {item.name}: " + ", ".join(value for value in details if value))
        lines.append("Which one would you like details about?")
        return "\n".join(lines)

    if intent == "RECOMMENDATION":
        lower = message.lower()
        courses = get_active_courses()

        def first_matching(*needles: str):
            return next(
                (
                    item
                    for item in courses
                    if any(needle in item.name.lower() for needle in needles)
                    or any(needle in keyword for keyword in item.keywords for needle in needles)
                ),
                None,
            )

        if any(term in lower for term in ("test", "qa", "selenium", "playwright", "automation")):
            match = first_matching("testing", "playwright", "selenium", "qa")
            if match:
                return (
                    f"Based on your testing background, I'd suggest {match.name}. "
                    f"It is scheduled for {match.dates} at {match.venue}. "
                    "It fits QA/testing professionals who want to move into automation, Playwright, API testing, and CI/CD."
                )

        if any(term in lower for term in ("embedded", "firmware", "linux", "kernel", "yocto", "c programming")):
            embedded = [
                item
                for item in courses
                if any(
                    term in item.name.lower()
                    for term in ("embedded", "linux kernel")
                )
            ][:3]
            if embedded:
                lines = ["For an embedded/Linux background, these are the strongest matches:"]
                lines.extend(f"- {item.name}: {item.dates}" for item in embedded)
                lines.append("Tell me your current tools and I can narrow it down.")
                return "\n".join(lines)

        return (
            "I can narrow it down by your background: testing/QA or test automation -> Modern Software "
            "Testing with AI-assisted Automation; embedded/firmware/Linux work -> the Embedded C, Embedded "
            "Linux System Internals, Yocto, or Linux Kernel courses. What is your current role or main toolset?"
        )

    if intent == "VENUE" and course is None:
        venues = sorted({item.venue for item in get_active_courses() if item.venue})
        if not venues:
            return None
        venue_list = "; ".join(venues)
        return (
            "Training venues depend on the course. Current scheduled public venues are "
            f"{venue_list}. If you mean a specific course, tell me the course name and "
            "I'll give the exact date and venue."
        )
    if intent in {"FEES", "SCHEDULE", "DURATION"} and course is None:
        return None
    if intent == "FEES":
        fees = course.fees
        parts = []
        if fees.get("standard"):
            parts.append(f"RM{fees['standard']:,} per participant")
        if fees.get("group_2"):
            parts.append(f"RM{fees['group_2']:,} each for 2 participants")
        if fees.get("group_3_plus"):
            parts.append(f"RM{fees['group_3_plus']:,} each for 3 or more")
        answer = f"The fee for {course.name} is " + "; ".join(parts) + "."
        lower = message.lower()
        if any(term in lower for term in ("date", "schedule", "when")):
            answer += f" It is scheduled for {course.dates}."
        if any(term in lower for term in ("venue", "where", "location")):
            answer += f" The venue is {course.venue}."
        if any(term in lower for term in ("duration", "how many days", "how long")):
            match = re.search(r"\b(\d+)[- ]day\b", course.overview, re.IGNORECASE)
            if match:
                answer += f" It is a {match.group(1)}-day course."
        return answer
    if intent == "ENROLLMENT":
        course_part = f" for {course.name}" if course is not None else ""
        return (
            f"To enroll{course_part}, share the participant details and billing/HRDC details "
            "with Timmins. We can issue the official quotation or invoice, provide the HRDC "
            "course outline and registration information if needed, and confirm the seat once "
            "payment, HRDC grant approval, PO, or Letter of Undertaking is received."
        )
    if intent == "SCHEDULE":
        answer = f"{course.name} is scheduled for {course.dates} at {course.venue}."
        lower = message.lower()
        if "how many days" in lower or "duration" in lower or "how long" in lower:
            match = re.search(r"\b(\d+)[- ]day\b", course.overview, re.IGNORECASE)
            if match:
                answer += f" It is a {match.group(1)}-day course."
        return answer
    if intent == "VENUE":
        return f"{course.name} will be held at {course.venue}."
    if intent == "DURATION":
        match = re.search(r"\b(\d+)[- ]day\b", course.overview, re.IGNORECASE)
        return f"{course.name} is a {match.group(1)}-day course." if match else None
    if intent == "HRDC":
        lower = message.lower()
        if re.search(
            r"\b(?:what is|what's|what does)\s+hrd\s*corp\b|"
            r"\b(?:what is|what's|what does)\s+hrdc(?:\s+mean)?\b|"
            r"\bhrdc\s+(?:meaning|full form)\b",
            lower,
        ):
            return (
                "HRD Corp is Malaysia's Human Resource Development Corporation. "
                "In this training context, registered employers can apply through e-TRiS "
                "to use eligible HRD levy funds for approved employee training. Timmins can "
                "provide the quotation, trainer profile, and course outline needed for the application."
            )
        if any(term in lower for term in ("how", "apply", "process", "portal", "document", "approval", "grant")):
            base = (
                "For HRDC, your company HR/L&D team submits the grant application through the HRDC portal. "
                "Timmins can provide the HRDC-registered course outline, trainer profile, official quotation, "
                "and HRDC registration information. Typical approval is 1 to 3 working days."
            )
            if course is not None:
                base = f"{course.name} is HRDC claimable. " + base
            return base
        if course is None:
            return _knowledge_text("hrdc")
        deadline = (
            f" The approval deadline is {course.hrdc_deadline}." if course.hrdc_deadline else ""
        )
        return f"{course.name} is HRDC claimable.{deadline}"
    if intent == "QUOTATION":
        course_part = f" for {course.name}" if course is not None else ""
        company = policies["company"]
        return (
            f"Yes — we can issue an official quotation or invoice{course_part}. I've flagged this to "
            "our consultant, who will prepare it and follow up with you directly. If you'd like to "
            f"reach us first, you can call or WhatsApp {company['phone']} or email {company['email']}. "
            "To speed things up, reply with the number of participants and your company/billing details "
            "(and HRDC info, if applicable). A seat is confirmed once payment, HRDC grant approval, a "
            "PO, or a Letter of Undertaking is received."
        )
    if intent == "PAYMENT":
        if "credit card" in message.lower() or "card payment" in message.lower():
            return (
                "Credit-card payment is not listed as a confirmed option. Bank transfer is the "
                "documented method. I can ask a consultant to verify whether card payment is "
                "possible, but I don't want to promise it."
            )
        return (
            "Payment is by bank transfer. Installments are not a standard confirmed option. "
            "If you need flexibility, I can ask a consultant whether an exception is possible, "
            "but I can't promise one."
        )
    if intent == "CANCELLATION":
        tiers = policies["cancellation"]["tiers"]
        return "Cancellation terms:\n" + "\n".join(
            f"- {tier['window']}: {tier['charge']}" for tier in tiers
        )
    if intent == "CERTIFICATION":
        return (
            "After successful participation, you’ll receive printed and electronic certificates "
            "of completion. The fee also includes PDF materials, refreshments, lunch, and "
            "hands-on exercises and labs."
        )
    if intent == "BATCH_SIZE":
        return (
            "Class sizes are kept small to maintain training quality. The exact number depends "
            "on the current intake, so I can ask a consultant to confirm the latest participant count."
        )
    if intent == "PLACEMENT":
        lower = message.lower()
        if any(term in lower for term in ("guarantee", "guaranteed", "confirm job", "sure job")):
            return (
                "We provide career guidance and post-training support, but I don't want to promise "
                "a job guarantee. A consultant can discuss available placement support with you."
            )
        return (
            "We provide career guidance and post-training support. A consultant can discuss "
            "placement opportunities and next steps with you after the programme."
        )
    if intent == "ONLINE":
        if course is not None and course.venue:
            return (
                f"The current scheduled venue for {course.name} is {course.venue}. "
                "If you need online or hybrid delivery, I can ask a consultant to confirm whether it is available."
            )
        return (
            "Delivery mode depends on the intake. I don't want to promise online or in-person format "
            "without confirmation, so a consultant can share the confirmed brochure details."
        )
    if intent == "REQUIREMENTS":
        lower = message.lower()
        if any(term in lower for term in ("laptop", "bring", "hardware")):
            return (
                "Participants may bring their own laptop or use a Timmins-provided laptop where available. "
                "For course-specific hardware or software setup, I can share the full brochure details."
            )
        if course is None:
            return _knowledge_text("requirements")
        return None
    if intent == "CONTACT":
        company = policies["company"]
        return f"You can contact Timmins at {company['email']} or {company['phone']}."
    if intent == "COMPANY":
        company = policies["company"]
        lower_msg = message.lower()
        if any(word in lower_msg for word in ("based", "malaysia", "location", "office", "where")):
            return (
                f"{company['name']} is a Malaysia-based corporate training and consulting company. "
                "We deliver public and in-house training programs across Malaysia and the Asia-Pacific region. "
                f"You can reach us at {company['email']} or {company['phone']}."
            )
        return (
            f"{company['name']} is {company['description']} "
            f"You can contact us at {company['email']} or {company['phone']}."
        )
    if intent == "COURSE_SELECTION" and course is not None:
        return (
            f"Got it — you're interested in {course.name}, scheduled for {course.dates}. "
            "Would you like the fees, course outline, or registration details?"
        )
    if intent == "TRAINER" and course is None:
        return (
            "Our trainers are experienced professionals in the technical subject they teach. "
            "We can arrange a trainer discussion or share the relevant trainer profile upon request."
        )
    if intent == "TRAINER" and course is not None:
        fact = _course_trainer_fact(course)
        if fact:
            return (
                f"{fact} I don't have the trainer's name or full profile here, but I can ask "
                "our consultant to share it."
            )
        return (
            "I don't have confirmed trainer details for this course. I can ask our consultant "
            "to share the relevant trainer profile."
        )
    return None
