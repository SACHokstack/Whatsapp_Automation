"""
Import leads from a Meta Lead Ads Excel export into the leads database.

Reads the client's .xlsx export, detects the course from the ad_name column, maps
whatsapp_number → phone, and upserts each lead into the `leads` table — Postgres when
DATABASE_URL is set (Railway), SQLite otherwise. Leads already in the database are left
alone unless --update is passed, so re-running is safe and only picks up new rows.

The Google Sheets workbook is still supported as a destination (--dest sheet) for the
human-facing view; it needs the service-account credentials.

For continuous syncing rather than a one-off import, see sync_leads.py.

Usage:
  python import_leads.py --file "New Lead Generation _ Embedded Sw _ July - August 26.xlsx"
  python import_leads.py --file leads.xlsx --dry-run
  python import_leads.py --file leads.xlsx --dest both
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from services.course_loader import load_courses
from services.lead_sync import (
    LEAD_COLUMNS,
    detect_course_from_ad,
    lead_fields,
    normalize_phone,
    rows_from_excel,
    sync_leads,
)
from services.persistence import backend_name

# Kept for backwards compatibility with anything importing this name
IMPORT_COLUMNS = LEAD_COLUMNS


def _print_event(kind: str, message: str) -> None:
    prefix = {
        "course": "\nCourse   :",
        "new": "  ADDED  ",
        "updated": "  UPDATED",
        "invalid": "  SKIP   ",
        "unmatched": "  WARN   ",
    }.get(kind, "  ")
    print(f"{prefix} {message}")


# --- Destination: Google Sheets workbook (human-facing view) ---


def _get_or_create_tab(workbook, tab_name: str, dry_run: bool):
    existing = {ws.title: ws for ws in workbook.worksheets()}
    if tab_name in existing:
        return existing[tab_name]
    if dry_run:
        print(f"  [DRY RUN] would create tab '{tab_name}'")
        return None
    ws = workbook.add_worksheet(title=tab_name, rows=1000, cols=len(LEAD_COLUMNS) + 10)
    ws.update([LEAD_COLUMNS], "A1")
    ws.format("1:1", {"textFormat": {"bold": True}})
    print(f"  Created tab '{tab_name}' with headers")
    return ws


def _ensure_headers(ws) -> list[str]:
    headers = [h.strip() for h in ws.row_values(1) if h.strip()]
    if not headers:
        ws.update([LEAD_COLUMNS], "A1")
        headers = LEAD_COLUMNS[:]
        print(f"  Added headers to existing tab '{ws.title}'")
    return headers


def _existing_phones(ws) -> set[str]:
    phones = set()
    for record in ws.get_all_records():
        phone = normalize_phone(record.get("phone", "") or record.get("whatsapp_number", ""))
        if phone:
            phones.add(phone)
    return phones


def _import_to_sheet(file_path: Path, *, dry_run: bool) -> tuple[int, int]:
    from services.google_sheets import LEADS_WORKBOOK, _open_workbook, get_client

    courses = load_courses()
    grouped: dict[str, list[dict]] = {}
    for row in rows_from_excel(file_path):
        slug = detect_course_from_ad(row.get("ad_name", ""), courses)
        if slug:
            grouped.setdefault(slug, []).append(row)

    try:
        gsheet_wb = _open_workbook(get_client(), LEADS_WORKBOOK)
    except Exception as exc:
        print(f"ERROR: could not open the leads workbook: {exc}")
        sys.exit(1)

    written = skipped = 0
    for slug, rows in grouped.items():
        course = courses[slug]
        tab_name = course.worksheet_name
        print(f"\nCourse   : {course.name}")
        print(f"Tab      : {tab_name}  ({len(rows)} row(s))")

        gws = _get_or_create_tab(gsheet_wb, tab_name, dry_run=dry_run)
        if gws is None:
            written += len(rows)
            continue

        headers = _ensure_headers(gws)
        existing_phones = _existing_phones(gws)

        for row in rows:
            lead = lead_fields(row, slug)
            phone, name = lead["phone"], lead["full_name"]

            if not phone:
                print(f"  SKIP  invalid phone: {row.get('whatsapp_number') or row.get('phone')!r}")
                skipped += 1
                continue
            if phone in existing_phones:
                print(f"  SKIP  duplicate phone: {phone} ({name})")
                skipped += 1
                continue
            if dry_run:
                print(f"  [DRY RUN] would append {phone} ({name})")
                written += 1
                continue

            # Add any of our columns the tab doesn't have yet, then write in header order
            for col in LEAD_COLUMNS:
                if col not in headers:
                    headers.append(col)
                    gws.update_cell(1, len(headers), col)
            gws.append_row([lead.get(h, "") for h in headers], value_input_option="RAW")
            existing_phones.add(phone)
            print(f"  ADDED {phone} ({name})")
            written += 1
    return written, skipped


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import a Meta Lead Ads Excel export into the leads database"
    )
    parser.add_argument("--file", required=True, help="Path to Excel (.xlsx) file")
    parser.add_argument(
        "--dest",
        choices=("db", "sheet", "both"),
        default="db",
        help="Where to write the leads (default: db)",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Refresh leads already in the database instead of skipping them (db only)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be written without touching the database or sheet",
    )
    args = parser.parse_args()

    file_path = Path(args.file)
    if not file_path.exists():
        print(f"Error: file not found: {file_path}")
        sys.exit(1)

    print(f"File     : {file_path.name}")
    print(f"Dest     : {args.dest}{' (' + backend_name() + ')' if args.dest != 'sheet' else ''}")
    print(f"Mode     : {'DRY RUN' if args.dry_run else 'LIVE'}")

    written = skipped = 0
    if args.dest in ("db", "both"):
        result = sync_leads(
            excel_path=file_path,
            dry_run=args.dry_run,
            update=args.update,
            on_event=_print_event,
        )
        written += result.added + result.updated
        skipped += result.skipped_existing + result.skipped_invalid
        print(f"\nRows scanned: {result.scanned} | already in db: {result.skipped_existing}")
    if args.dest in ("sheet", "both"):
        w, s = _import_to_sheet(file_path, dry_run=args.dry_run)
        written, skipped = written + w, skipped + s

    print(f"\nDone.  Added: {written} | Skipped: {skipped}")


if __name__ == "__main__":
    main()
