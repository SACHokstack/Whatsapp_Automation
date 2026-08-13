"""Automatic first contact.

Leads arrive from the Meta ads with status CREATED (see services/lead_sync.py). This module
sends each of them their course's approved WhatsApp template and flips them to CONTACTED —
the same transition outreach.py has always done by hand, but on a timer so no one has to
remember. Once the lead replies, the normal webhook path takes over and the conversation
continues as usual; nothing here touches replies.

Because this sends real messages to real people it is **off unless AUTO_OUTREACH_ENABLED is
set**, capped per run, paced between sends, and restricted to daytime hours in the leads'
timezone. The CREATED → CONTACTED transition is what stops a lead being messaged twice.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from services.course_loader import get_active_courses, get_course
from services.lead_sync import PENDING_STATUSES  # noqa: F401  (re-exported for callers)
from services.persistence import add_message, list_leads, upsert_lead
from services.whatsapp import send_template

logger = logging.getLogger(__name__)

FAILED_STATUS = "OUTREACH_FAILED"


def _flag(name: str, default: str = "false") -> bool:
    """Dashboard value if one is stored, else the environment. See services.settings."""
    from services.settings import get_bool

    return get_bool(name, default.strip().lower() in {"1", "true", "yes", "on"})


def _int_env(name: str, default: int) -> int:
    from services.settings import get_int

    return get_int(name, default)


def enabled() -> bool:
    return _flag("AUTO_OUTREACH_ENABLED")


def within_send_window(now: datetime | None = None) -> bool:
    """True inside AUTO_OUTREACH_HOURS (default 09-19) in AUTO_OUTREACH_TZ.

    A cold outreach template at 3am reads as spam and gets the number reported, so the
    scheduler simply waits for morning rather than sending.
    """
    window = os.getenv("AUTO_OUTREACH_HOURS", "9-19").strip()
    try:
        start_hour, end_hour = (int(part) for part in window.split("-", 1))
    except ValueError:
        start_hour, end_hour = 9, 19
    tz_name = os.getenv("AUTO_OUTREACH_TZ", "Asia/Kuala_Lumpur").strip() or "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
    hour = (now or datetime.now(tz)).astimezone(tz).hour
    return start_hour <= hour < end_hour


@dataclass
class OutreachResult:
    sent: int = 0
    failed: int = 0
    skipped: int = 0
    pending: int = 0
    reason: str = ""
    sent_phones: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "sent": self.sent,
            "failed": self.failed,
            "skipped": self.skipped,
            "pending": self.pending,
            "reason": self.reason,
            "sent_phones": self.sent_phones,
        }


def _courses_to_contact(course_slug: str | None):
    if course_slug:
        course = get_course(course_slug)
        return [course] if course and course.outreach_template else []
    return [c for c in get_active_courses() if c.outreach_template]


def _permanent_failure(response: requests.Response) -> bool:
    """4xx other than rate limiting means this number/template will never work — stop retrying."""
    return 400 <= response.status_code < 500 and response.status_code != 429


def _mark_contacted_in_sheet(phone: str, course, status: str = "CONTACTED") -> None:
    """Mirror the status into the source sheet so the client sees it there too.

    Best-effort: the database is the source of truth for what's been sent, so a Sheets
    outage must never stop or repeat an outreach run.
    """
    if not _flag("ENABLE_GOOGLE_SHEETS"):
        return
    try:
        from services.google_sheets import update_lead_in
        from services.lead_sync import mixed_worksheet_name

        worksheet = mixed_worksheet_name() or course.worksheet_name
        update_lead_in(phone, worksheet, None, **{course.status_col: status})
    except Exception:
        logger.warning("event=auto_outreach_sheet_writeback_failed course=%s", course.slug)


def dispatch_outreach(
    *,
    course_slug: str | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    force: bool = False,
    on_event=None,
) -> OutreachResult:
    """Send the first-contact template to leads still sitting at CREATED.

    force — bypass the AUTO_OUTREACH_ENABLED flag and the send window (a human ran this
            deliberately from the CLI). The scheduler never sets it.
    """
    result = OutreachResult()

    def emit(kind: str, message: str) -> None:
        if on_event:
            on_event(kind, message)

    if not force and not enabled():
        result.reason = "auto outreach disabled (AUTO_OUTREACH_ENABLED)"
        return result
    if not force and not within_send_window():
        result.reason = "outside the send window (AUTO_OUTREACH_HOURS)"
        return result

    remaining = limit if limit is not None else _int_env("AUTO_OUTREACH_MAX_PER_RUN", 25)
    delay = max(0.0, float(_int_env("AUTO_OUTREACH_DELAY_SECONDS", 3)))

    for course in _courses_to_contact(course_slug):
        if remaining <= 0:
            break
        leads = list_leads(course=course.slug, statuses=PENDING_STATUSES, limit=remaining)
        if not leads:
            continue
        emit("course", f"{course.name}: {len(leads)} lead(s) awaiting first contact")

        for lead in leads:
            if remaining <= 0:
                break
            phone = str(lead.get("phone") or "").strip()
            name = str(lead.get("name") or "").strip() or "there"
            if not phone:
                result.skipped += 1
                continue

            if dry_run:
                emit("would_send", f"{phone} ({name}) → {course.outreach_template}")
                result.sent += 1
                result.sent_phones.append(phone)
                remaining -= 1
                continue

            try:
                response = send_template(
                    phone,
                    course.outreach_template,
                    course.outreach_language,
                    variables=[name],
                )
            except requests.RequestException:
                logger.exception("event=auto_outreach_send_error course=%s", course.slug)
                result.failed += 1
                continue

            if response.ok:
                message_id = None
                try:
                    message_id = response.json().get("messages", [{}])[0].get("id")
                except (ValueError, KeyError, IndexError):
                    pass
                upsert_lead(
                    phone,
                    status="CONTACTED",
                    course=course.slug,
                    last_reply=course.outreach_template,
                )
                add_message(
                    phone,
                    direction="outbound",
                    body=course.outreach_template,
                    message_id=message_id,
                )
                _mark_contacted_in_sheet(phone, course)
                result.sent += 1
                result.sent_phones.append(phone)
                remaining -= 1
                emit("sent", f"{phone} ({name})")
                logger.info(
                    "event=auto_outreach_sent course=%s template=%s",
                    course.slug,
                    course.outreach_template,
                )
            else:
                result.failed += 1
                emit("failed", f"{phone} ({name}): HTTP {response.status_code}")
                logger.warning(
                    "event=auto_outreach_failed course=%s status=%s body=%s",
                    course.slug,
                    response.status_code,
                    response.text[:200],
                )
                # A bad number or rejected template would otherwise be retried every pass
                if _permanent_failure(response):
                    upsert_lead(
                        phone,
                        status=FAILED_STATUS,
                        human_reason=f"outreach failed HTTP {response.status_code}",
                    )

            if delay:
                time.sleep(delay)

    # How many are still waiting (cap reached, window closed, or failures)
    result.pending = sum(
        len(list_leads(course=course.slug, statuses=PENDING_STATUSES))
        for course in _courses_to_contact(course_slug)
    )
    logger.info(
        "event=auto_outreach sent=%d failed=%d skipped=%d pending=%d dry_run=%s",
        result.sent,
        result.failed,
        result.skipped,
        result.pending,
        dry_run,
    )
    return result
