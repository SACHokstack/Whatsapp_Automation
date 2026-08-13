"""Keep the leads table in sync with the lead source.

The source is either the client's Meta Lead Ads Excel export (.xlsx) or the Google Sheets
leads workbook; both carry the same columns, so both funnel through the same parser and land
in the same `leads` table (Postgres when DATABASE_URL is set, SQLite otherwise).

The sync is **idempotent**: a lead is written only when its phone number isn't already in the
database. So "check for new rows" is just "re-read the source" — no row cursor, no watermark
file, nothing to drift out of step if a run dies halfway or the source is re-exported with the
old rows still in it.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from services.course_loader import CourseConfig, get_active_courses, load_courses
from services.persistence import get_lead, upsert_lead

logger = logging.getLogger(__name__)

# Leads that predate the automation cutoff. Deliberately NOT "CREATED", so the automatic
# outreach never picks them up — they were handled by hand before the bot went live.
BASELINE_STATUS = "PRE_LAUNCH"

# Statuses meaning "still waiting on first contact". Canonical here; auto_outreach reads it.
PENDING_STATUSES = ("CREATED", "")

# Columns we carry from the source row (also the Google Sheet tab's header order)
LEAD_COLUMNS = [
    "phone",
    "full_name",
    "email",
    "job_title",
    "company_name",
    "who_will_pay",
    "lead_status",
    "course_slug",
    "ad_id",
    "ad_name",
    "adset_id",
    "adset_name",
    "campaign_id",
    "campaign_name",
    "form_id",
    "form_name",
]


def normalize_phone(raw) -> str | None:
    """Digits only. Handles '+60...', Excel floats like 919444209374.0, and blanks."""
    digits = re.sub(r"[^\d]", "", str(raw or "").split(".")[0])
    return digits if len(digits) >= 10 else None


def cell(row: dict, *keys: str) -> str:
    """First non-empty value across the given column names (handles the 'who_will_pay?' variant)."""
    for key in keys:
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def detect_course_from_ad(ad_name: str, courses: dict[str, CourseConfig]) -> str | None:
    """Match an ad/adset/campaign name against course keywords. Returns a course slug or None."""
    ad_lower = str(ad_name or "").lower()
    if not ad_lower:
        return None
    for slug, course in courses.items():
        if not course.outreach_template:
            continue
        for keyword in course.keywords or []:
            if keyword.lower() in ad_lower:
                return slug

    # Fallback: abbreviations used in the Meta ad names that aren't course keywords
    abbreviations = [
        (("yoct", "y0ct"), "yocto"),
        (("elsi",), "internals"),
        (("eldb", "debug"), "debug"),
        (("embc", "embedded-c", "embedded c"), "embedded-c"),
        (("kernel",), "kernel"),
        (("python",), "python"),
        (("testing", "playwright", "qa"), "sw-testing"),
    ]
    for markers, slug_fragment in abbreviations:
        if any(marker in ad_lower for marker in markers):
            for slug in courses:
                if slug_fragment in slug:
                    return slug
    return None


def contact_cutoff() -> datetime | None:
    """LEAD_SYNC_CONTACT_CUTOFF — leads created before this are never auto-contacted.

    The leads that arrived before the bot went live were reached out to by hand. Marking
    them by *date* rather than just seeding the database means a rebuilt or restored
    database still can't message them a second time.
    """
    from services.settings import get_setting

    raw = (get_setting("LEAD_SYNC_CONTACT_CUTOFF", "") or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("event=lead_sync_bad_cutoff value=%r", raw)
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def row_created_at(row: dict) -> datetime | None:
    """The Meta export's created_time, e.g. '2026-07-04T06:24:13-05:00'."""
    raw = cell(row, "created_time", "created", "timestamp")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _predates_cutoff(row: dict, cutoff: datetime | None) -> bool:
    if cutoff is None:
        return False
    created = row_created_at(row)
    # No usable date on a row while a cutoff is set → treat it as old. Failing closed here
    # costs one un-sent message; failing open spams someone who was already called.
    return True if created is None else created < cutoff


def lead_fields(row: dict, slug: str) -> dict[str, str]:
    """Source row → the lead columns we persist."""
    return {
        "phone": normalize_phone(row.get("whatsapp_number") or row.get("phone", "")) or "",
        "full_name": cell(row, "full_name", "name"),
        "email": cell(row, "email"),
        "job_title": cell(row, "job_title"),
        "company_name": cell(row, "company_name"),
        "who_will_pay": cell(row, "who_will_pay?", "who_will_pay"),
        # Trust the source's own status — a row the client already marked CONTACTED must not
        # be re-imported as a fresh lead and messaged a second time.
        "lead_status": cell(row, "lead_status", "status").upper() or "CREATED",
        "course_slug": slug,
        "ad_id": cell(row, "ad_id"),
        "ad_name": cell(row, "ad_name"),
        "adset_id": cell(row, "adset_id"),
        "adset_name": cell(row, "adset_name"),
        "campaign_id": cell(row, "campaign_id"),
        "campaign_name": cell(row, "campaign_name"),
        "form_id": cell(row, "form_id"),
        "form_name": cell(row, "form_name"),
    }


# --- Sources ---------------------------------------------------------------


def rows_from_excel(file_path: str | Path) -> list[dict]:
    """Every data row of the first sheet, keyed by the header row."""
    import openpyxl

    workbook = openpyxl.load_workbook(str(file_path), read_only=True, data_only=True)
    sheet = workbook.active
    raw = list(sheet.iter_rows(values_only=True))
    workbook.close()
    if not raw:
        return []
    headers = [str(h).strip() if h else "" for h in raw[0]]
    return [{headers[i]: values[i] for i in range(len(headers))} for values in raw[1:]]


def _grouped_from_excel(file_path: str | Path, courses: dict) -> tuple[dict, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    unmatched: list[dict] = []
    for row in rows_from_excel(file_path):
        slug = detect_course_from_ad(row.get("ad_name", ""), courses)
        if slug:
            grouped.setdefault(slug, []).append(row)
        else:
            unmatched.append(row)
    return grouped, unmatched


def mixed_worksheet_name() -> str:
    """Tab holding the raw Meta export (all courses in one sheet). Empty = per-course tabs."""

    return os.getenv("LEAD_SYNC_WORKSHEET", "").strip()


def _grouped_from_mixed_sheet(worksheet: str, courses: dict) -> tuple[dict, list[dict]]:
    """One tab, every course mixed together — the ad_name column tells us which course.

    This is the shape Meta Lead Ads exports: one row per lead, ad_name like
    "Ads 8 - ELSI - Nona rollerblade song" or "Ads 1 - Y0CTO - Vid Engineer Pain-OBH".
    """
    from services.google_sheets import get_rows_from

    grouped: dict[str, list[dict]] = {}
    unmatched: list[dict] = []
    for row in get_rows_from(worksheet):
        ad_name = cell(row, "ad_name", "adset_name", "campaign_name")
        slug = detect_course_from_ad(ad_name, courses)
        if slug:
            grouped.setdefault(slug, []).append(row)
        else:
            unmatched.append(row)
    return grouped, unmatched


def _grouped_from_sheet(courses: dict) -> tuple[dict, list[dict]]:
    """Read the leads workbook — a single mixed tab if configured, else one tab per course."""
    from services.google_sheets import SHEET_ERRORS, get_rows_from

    worksheet = mixed_worksheet_name()
    if worksheet:
        return _grouped_from_mixed_sheet(worksheet, courses)

    grouped: dict[str, list[dict]] = {}
    for course in get_active_courses():
        if not course.outreach_template:
            continue
        try:
            rows = get_rows_from(course.worksheet_name)
        except SHEET_ERRORS:
            logger.warning("event=lead_sync_tab_unavailable worksheet=%s", course.worksheet_name)
            continue
        if rows:
            grouped.setdefault(course.slug, []).extend(rows)
    return grouped, []


# --- Sync ------------------------------------------------------------------


@dataclass
class SyncResult:
    source: str
    scanned: int = 0
    added: int = 0
    updated: int = 0
    skipped_existing: int = 0
    skipped_invalid: int = 0
    unmatched: int = 0
    baseline: int = 0  # imported pre-cutoff, so never auto-contacted
    rebaselined: int = 0  # already in the db awaiting contact, demoted by the cutoff
    added_phones: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "scanned": self.scanned,
            "added": self.added,
            "updated": self.updated,
            "skipped_existing": self.skipped_existing,
            "skipped_invalid": self.skipped_invalid,
            "unmatched": self.unmatched,
            "baseline": self.baseline,
            "rebaselined": self.rebaselined,
            "added_phones": self.added_phones,
        }


def sync_leads(
    *,
    excel_path: str | Path | None = None,
    dry_run: bool = False,
    update: bool = False,
    on_event=None,
) -> SyncResult:
    """Read the lead source and write any lead that isn't in the database yet.

    excel_path — read that .xlsx; otherwise read the Google Sheets workbook.
    update     — also refresh leads that already exist (default: leave them alone, so a
                 lead mid-conversation is never reset to CREATED).
    on_event   — optional callback(kind, message) for CLI progress output.
    """
    source = f"excel:{Path(excel_path).name}" if excel_path else "sheet"
    result = SyncResult(source=source)

    def emit(kind: str, message: str) -> None:
        if on_event:
            on_event(kind, message)

    courses = load_courses()
    cutoff = contact_cutoff()
    if excel_path:
        grouped, unmatched = _grouped_from_excel(excel_path, courses)
    else:
        grouped, unmatched = _grouped_from_sheet(courses)
    result.unmatched = len(unmatched)
    for row in unmatched:
        emit("unmatched", f"no course match: ad_name={row.get('ad_name')!r}")

    for slug, rows in grouped.items():
        course = courses.get(slug)
        emit("course", f"{course.name if course else slug} ({len(rows)} row(s))")

        for row in rows:
            result.scanned += 1
            lead = lead_fields(row, slug)
            phone, name = lead["phone"], lead["full_name"]

            if not phone:
                result.skipped_invalid += 1
                emit(
                    "invalid", f"invalid phone: {row.get('whatsapp_number') or row.get('phone')!r}"
                )
                continue

            # Pre-cutoff leads were contacted by hand before the bot went live
            baseline = _predates_cutoff(row, cutoff)
            new_status = BASELINE_STATUS if baseline else lead["lead_status"]

            existing = get_lead(phone)
            if existing and not update:
                # Self-healing: a pre-cutoff lead still awaiting first contact was imported
                # before the cutoff was configured. Demote it now, or turning outreach on
                # would message the whole pre-launch backlog. Only touches leads that have
                # never been contacted — CONTACTED/ENGAGED/in-flight ones are left alone.
                if baseline and existing.get("status", "").upper() in PENDING_STATUSES:
                    if not dry_run:
                        upsert_lead(phone, status=BASELINE_STATUS)
                    result.rebaselined += 1
                    emit("rebaselined", f"{phone} ({name}) → {BASELINE_STATUS}")
                result.skipped_existing += 1
                continue

            if dry_run:
                result.added += 1
                result.added_phones.append(phone)
                if baseline:
                    result.baseline += 1
                emit(
                    "new",
                    f"would add {phone} ({name}) course={slug} status={new_status}",
                )
                continue

            upsert_lead(
                phone,
                # Never reset an in-flight conversation on an --update pass; on a first import
                # carry the source's own status so an already-contacted row isn't messaged again
                status=None if existing else new_status,
                course=slug,
                name=name or None,
                email=lead["email"] or None,
                job_title=lead["job_title"] or None,
                company_name=lead["company_name"] or None,
                who_will_pay=lead["who_will_pay"] or None,
            )
            if existing:
                result.updated += 1
                emit("updated", f"updated {phone} ({name})")
            else:
                result.added += 1
                result.added_phones.append(phone)
                if baseline:
                    result.baseline += 1
                emit("new", f"added {phone} ({name}) status={new_status}")

    logger.info(
        "event=lead_sync source=%s scanned=%d added=%d updated=%d existing=%d invalid=%d unmatched=%d dry_run=%s",
        result.source,
        result.scanned,
        result.added,
        result.updated,
        result.skipped_existing,
        result.skipped_invalid,
        result.unmatched,
        dry_run,
    )
    return result
