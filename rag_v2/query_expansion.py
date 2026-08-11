from __future__ import annotations

import re

_WORD_FIXES = {
    "whiat": "what",
    "waht": "what",
    "wats": "what",
    "wat": "what",
    "teh": "the",
    "mch": "much",
    "sylabus": "syllabus",
    "syalbus": "syllabus",
    "traniers": "trainers",
    "patymnet": "payment",
    "cancellatoin": "cancellation",
    # Common mobile-keyboard variants seen in catalog questions.
    "availablel": "available",
    "availabel": "available",
    "avaialble": "available",
    "avialable": "available",
    "availble": "available",
}
_ORDINALS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}
_POSITIONAL_ORDINALS = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
}

_PHRASE_EXPANSIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("how much", ("fee", "fees", "price", "cost", "pricing")),
    ("claimable", ("HRDC", "grant", "claim", "claimable")),
    ("hrdf", ("HRDC", "grant")),
    ("h r d c", ("HRDC", "grant")),
    ("trainer", ("instructor", "facilitator", "trainer profile")),
    ("traniers", ("trainer", "instructor", "trainer profile")),
    ("patymnet", ("payment", "bank transfer", "invoice", "quotation")),
    (
        "installment",
        ("payment", "bank transfer", "invoice", "quotation", "payment plan", "installments"),
    ),
    ("instalment", ("payment", "bank transfer", "invoice", "quotation", "payment plan")),
    ("cancellatoin", ("cancellation", "refund", "reschedule")),
    ("batchsize", ("batch size", "class size", "participants")),
    ("enrol", ("enroll", "registration", "sign up")),
    ("enrollment", ("registration", "seat confirmation", "enroll")),
    ("placement", ("career support", "job support", "post-training support")),
    (
        "syllabus",
        (
            "course content",
            "learning outcomes",
            "skills",
            "tools",
            "practical exercises",
            "key topics",
            "course structure",
            "modules",
        ),
    ),
    (
        "curriculum",
        ("course content", "covered topics", "modules", "learning outcomes", "practical labs", "key topics"),
    ),
    ("software testing", ("sw testing", "QA", "Playwright", "automation testing")),
    ("sw testing", ("software testing", "QA", "Playwright", "automation testing")),
    ("embedded c", ("C programming", "GDB", "firmware")),
    ("gdb", ("debugging", "GNU debugger")),
    ("elsi", ("embedded linux system internals", "Buildroot", "BeagleBone Black")),
    ("yocto", ("BitBake", "BSP", "layers", "embedded linux")),
    ("lkp", ("linux kernel programming", "kernel module", "device driver")),
)

_HRDC_NUMBERS: dict[str, tuple[str, ...]] = {
    "10001676654": ("Embedded C Programming and GDB Debugging", "embedded c", "GDB"),
    "10001688034": ("Embedded Linux System Internals", "Buildroot", "BeagleBone Black"),
    "10001697599": ("Modern Software Testing", "Playwright", "CI/CD", "QA"),
    "10001697764": ("Linux Kernel Programming", "kernel module", "device driver"),
    "10001702890": ("Embedded Linux with Yocto", "Yocto", "BitBake", "BSP"),
}


def normalize_query(query: str) -> str:
    """Normalize common chat typos and schedule ordinals for retrieval only."""
    normalized = query.lower().replace("’", "'")
    for wrong, right in _WORD_FIXES.items():
        normalized = re.sub(rf"\b{re.escape(wrong)}\b", right, normalized)
    for word, number in _ORDINALS.items():
        normalized = re.sub(rf"\bday[ -]+{word}\b", f"day {number}", normalized)
        normalized = re.sub(rf"\bsession[ -]+{word}\b", f"session {number}", normalized)
    for word, number in _POSITIONAL_ORDINALS.items():
        normalized = re.sub(rf"\b{word}\s+day\b", f"day {number}", normalized)
        normalized = re.sub(rf"\b{word}\s+session\b", f"session {number}", normalized)
    normalized = re.sub(r"\bday\s*-\s*(\d{1,2})\b", r"day \1", normalized)
    normalized = re.sub(r"\bsession\s*-\s*(\d{1,2})\b", r"session \1", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def requested_day(query: str) -> int | None:
    match = re.search(r"\bday\s+(\d{1,2})\b", normalize_query(query))
    return int(match.group(1)) if match else None


def requested_session(query: str) -> int | None:
    match = re.search(r"\bsession\s+(\d{1,2})\b", normalize_query(query))
    return int(match.group(1)) if match else None


def expand_query(query: str) -> tuple[str, tuple[str, ...]]:
    """Return query text enriched with canonical domain terms for retrieval only."""
    normalized_query = normalize_query(query)
    lower = normalized_query.lower()
    additions: list[str] = []
    for phrase, expansions in _PHRASE_EXPANSIONS:
        if phrase in lower:
            additions.extend(expansions)

    digits = re.sub(r"\D", "", query)
    for hrdc_number, expansions in _HRDC_NUMBERS.items():
        if hrdc_number in digits:
            additions.extend(("HRDC registration", hrdc_number, *expansions))

    day = requested_day(normalized_query)
    session = requested_session(normalized_query)
    if day is not None:
        additions.extend((f"day {day}", "course structure", "agenda", "covered topics"))
    if session is not None:
        additions.extend(
            (f"session {session}", "course outline", "agenda", "covered topics", "practical exercise")
        )
    if day is None and re.search(r"\bday[ -]by[ -]day\b|\bdaily breakdown\b", lower):
        additions.extend(
            ("course structure", "agenda", "day 1", "day 2", "day 3", "day 4", "day 5")
        )

    deduped = tuple(dict.fromkeys(term for term in additions if term and term.lower() not in lower))
    if not deduped:
        return normalized_query, ()
    return f"{normalized_query}\nExpanded terms: {'; '.join(deduped)}", deduped
