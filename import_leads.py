"""
Import leads from a Meta Lead Ads Excel/CSV export into the Timmins Leads workbook.

Detects the course from the ad_name column, maps whatsapp_number → phone,
creates the worksheet tab if it doesn't exist, and appends new rows (skips
duplicates by phone number).

Usage:
  python import_leads.py --file "New Lead Generation _ Embedded Sw _ July - August 26.xlsx"
  python import_leads.py --file leads.xlsx --dry-run
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import openpyxl
from services.course_loader import load_courses
from services.google_sheets import get_client, LEADS_WORKBOOK

# Columns written to the Google Sheet tab (in order)
IMPORT_COLUMNS = [
    "phone",
    "full_name",
    "email",
    "job_title",
    "company_name",
    "who_will_pay",
    "lead_status",
]


def _normalize_phone(raw) -> str | None:
    s = re.sub(r"[^\d]", "", str(raw).split(".")[0])
    return s if len(s) >= 10 else None


def _detect_course_from_ad(ad_name: str, courses: dict) -> str | None:
    """Match ad_name keywords against course keyword lists. Returns course slug or None."""
    ad_lower = ad_name.lower()
    # Try direct keyword matching from each course's config
    for slug, course in courses.items():
        if not course.outreach_template:
            continue
        for kw in (course.keywords or []):
            if kw.lower() in ad_lower:
                return slug
    # Fallback: common ad abbreviations
    if "yoct" in ad_lower or "y0ct" in ad_lower:
        for slug in courses:
            if "yocto" in slug:
                return slug
    if "elsi" in ad_lower:
        for slug in courses:
            if "internals" in slug:
                return slug
    if "eldb" in ad_lower or "debug" in ad_lower:
        for slug in courses:
            if "debug" in slug:
                return slug
    if "embc" in ad_lower or "embedded-c" in ad_lower or "embedded c" in ad_lower:
        for slug in courses:
            if "embedded-c" in slug:
                return slug
    if "kernel" in ad_lower:
        for slug in courses:
            if "kernel" in slug:
                return slug
    if "python" in ad_lower:
        for slug in courses:
            if "python" in slug:
                return slug
    if "testing" in ad_lower or "playwright" in ad_lower or "qa" in ad_lower:
        for slug in courses:
            if "sw-testing" in slug:
                return slug
    return None


def _get_or_create_tab(workbook, tab_name: str, dry_run: bool):
    existing = {ws.title: ws for ws in workbook.worksheets()}
    if tab_name in existing:
        return existing[tab_name]
    if dry_run:
        print(f"  [DRY RUN] would create tab '{tab_name}'")
        return None
    ws = workbook.add_worksheet(title=tab_name, rows=1000, cols=len(IMPORT_COLUMNS) + 10)
    ws.update([IMPORT_COLUMNS], "A1")
    ws.format("1:1", {"textFormat": {"bold": True}})
    print(f"  Created tab '{tab_name}' with headers")
    return ws


def _ensure_headers(ws) -> list[str]:
    headers = [h.strip() for h in ws.row_values(1) if h.strip()]
    if not headers:
        ws.update([IMPORT_COLUMNS], "A1")
        headers = IMPORT_COLUMNS[:]
        print(f"  Added headers to existing tab '{ws.title}'")
    return headers


def _existing_phones(ws) -> set[str]:
    records = ws.get_all_records()
    phones = set()
    for r in records:
        p = _normalize_phone(r.get("phone", "") or r.get("whatsapp_number", ""))
        if p:
            phones.add(p)
    return phones


def main() -> None:
    parser = argparse.ArgumentParser(description="Import Meta Lead Ads export into Timmins Leads workbook")
    parser.add_argument("--file", required=True, help="Path to Excel (.xlsx) file")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be written without touching the sheet")
    args = parser.parse_args()

    file_path = Path(args.file)
    if not file_path.exists():
        print(f"Error: file not found: {file_path}")
        sys.exit(1)

    print(f"File     : {file_path.name}")
    print(f"Workbook : {LEADS_WORKBOOK}")
    print(f"Mode     : {'DRY RUN' if args.dry_run else 'LIVE'}\n")

    # Load Excel
    wb = openpyxl.load_workbook(str(file_path))
    ws_xl = wb.active
    rows_raw = list(ws_xl.iter_rows(values_only=True))
    headers_xl = [str(h).strip() if h else "" for h in rows_raw[0]]
    data_rows = rows_raw[1:]
    print(f"Excel rows: {len(data_rows)}\n")

    courses = load_courses()

    # Open Google Sheets workbook
    try:
        gsheet_wb = get_client().open(LEADS_WORKBOOK)
    except Exception as e:
        print(f"ERROR: could not open '{LEADS_WORKBOOK}': {e}")
        sys.exit(1)

    # Group rows by detected course
    by_course: dict[str, list[dict]] = {}
    unmatched = []

    for row_values in data_rows:
        row = {headers_xl[i]: row_values[i] for i in range(len(headers_xl))}
        ad_name = str(row.get("ad_name", "") or "")
        slug = _detect_course_from_ad(ad_name, courses)
        if slug:
            by_course.setdefault(slug, []).append(row)
        else:
            unmatched.append(row)

    if unmatched:
        print(f"WARNING: {len(unmatched)} row(s) could not be matched to a course:")
        for r in unmatched:
            print(f"  ad_name={r.get('ad_name')!r}  name={r.get('full_name')!r}")
        print()

    total_written = total_skipped = 0

    for slug, rows in by_course.items():
        course = courses[slug]
        tab_name = course.worksheet_name
        print(f"Course   : {course.name}")
        print(f"Tab      : {tab_name}  ({len(rows)} row(s))")

        if args.dry_run:
            for row in rows:
                phone_raw = row.get("whatsapp_number") or row.get("phone", "")
                phone = _normalize_phone(phone_raw)
                name = row.get("full_name", "")
                print(f"  [DRY RUN] would import {phone} ({name})")
            print()
            total_written += len(rows)
            continue

        # Get or create tab
        gws = _get_or_create_tab(gsheet_wb, tab_name, dry_run=False)
        if gws is None:
            continue

        headers = _ensure_headers(gws)
        existing_phones = _existing_phones(gws)

        for row in rows:
            phone_raw = row.get("whatsapp_number") or row.get("phone", "")
            phone = _normalize_phone(phone_raw)
            name = str(row.get("full_name", "") or "").strip()

            if phone is None:
                print(f"  SKIP  invalid phone: {phone_raw!r}")
                total_skipped += 1
                continue

            if phone in existing_phones:
                print(f"  SKIP  duplicate phone: {phone} ({name})")
                total_skipped += 1
                continue

            new_row = {
                "phone": phone,
                "full_name": name,
                "email": str(row.get("email", "") or "").strip(),
                "job_title": str(row.get("job_title", "") or "").strip(),
                "company_name": str(row.get("company_name", "") or "").strip(),
                "who_will_pay": str(row.get("who_will_pay?", "") or row.get("who_will_pay", "") or "").strip(),
                "lead_status": "CREATED",
            }

            # Build row in header order; blank for any column not in our map
            sheet_row = [new_row.get(h, "") for h in headers]

            # If headers don't include our columns yet, append them
            for col in IMPORT_COLUMNS:
                if col not in headers:
                    headers.append(col)
                    gws.update_cell(1, len(headers), col)

            # Re-build row with updated headers
            sheet_row = [new_row.get(h, "") for h in headers]
            gws.append_row(sheet_row, value_input_option="RAW")
            existing_phones.add(phone)
            print(f"  ADDED {phone} ({name})")
            total_written += 1

        print()

    print(f"Done.  Added: {total_written} | Skipped: {total_skipped}")


if __name__ == "__main__":
    main()
