"""
One-time setup script — run once to prepare the Timmins Leads workbook.

What it does:
  1. For every course tab that exists in the workbook, adds any missing
     qualification columns to the right of the existing headers.
  2. Creates the "Hot Leads" tab with its full header row (if not present).

Usage:
  python setup_sheet.py [--dry-run]
"""
from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

load_dotenv()

from services.course_loader import load_courses
from services.google_sheets import get_client, LEADS_WORKBOOK, HOT_LEADS_TAB, _HOT_LEAD_COLUMNS

# Columns to ensure exist in every course tab (in addition to what Meta already provides)
QUALIFICATION_COLUMNS = [
    "experience_years",
    "technologies",
    "motivation",
    "learning_goals",
    "funding_path",
    "availability",
    "lead_score",
    "status",
]


def setup_course_tab(ws, tab_name: str, dry_run: bool) -> None:
    existing = [h.strip() for h in ws.row_values(1) if h.strip()]
    missing = [c for c in QUALIFICATION_COLUMNS if c not in existing]

    if not missing:
        print(f"  {tab_name}: all columns present, nothing to do")
        return

    if dry_run:
        print(f"  {tab_name}: would add columns → {missing}")
        return

    next_col = len(existing) + 1
    for col_name in missing:
        ws.update_cell(1, next_col, col_name)
        next_col += 1

    print(f"  {tab_name}: added {len(missing)} column(s) → {missing}")


def setup_hot_leads_tab(workbook, dry_run: bool) -> None:
    existing_tabs = [ws.title for ws in workbook.worksheets()]

    if HOT_LEADS_TAB in existing_tabs:
        ws = workbook.worksheet(HOT_LEADS_TAB)
        existing_headers = [h.strip() for h in ws.row_values(1) if h.strip()]
        if existing_headers == _HOT_LEAD_COLUMNS:
            print(f"  {HOT_LEADS_TAB}: already set up, nothing to do")
            return
        missing = [c for c in _HOT_LEAD_COLUMNS if c not in existing_headers]
        if dry_run:
            print(f"  {HOT_LEADS_TAB}: tab exists, would add missing headers → {missing}")
            return
        # Rewrite full header row to ensure correct order
        ws.update([_HOT_LEAD_COLUMNS], "A1")
        print(f"  {HOT_LEADS_TAB}: header row updated")
        return

    if dry_run:
        print(f"  {HOT_LEADS_TAB}: would create tab with headers → {_HOT_LEAD_COLUMNS}")
        return

    ws = workbook.add_worksheet(title=HOT_LEADS_TAB, rows=1000, cols=len(_HOT_LEAD_COLUMNS))
    ws.update([_HOT_LEAD_COLUMNS], "A1")
    # Bold the header row
    ws.format("1:1", {"textFormat": {"bold": True}})
    print(f"  {HOT_LEADS_TAB}: created with {len(_HOT_LEAD_COLUMNS)} columns")


def main() -> None:
    parser = argparse.ArgumentParser(description="Set up Timmins Leads Google Sheet structure")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without writing")
    args = parser.parse_args()

    mode = "DRY RUN" if args.dry_run else "LIVE"
    print(f"Workbook : {LEADS_WORKBOOK}")
    print(f"Mode     : {mode}\n")

    try:
        workbook = get_client().open(LEADS_WORKBOOK)
    except Exception as e:
        print(f"ERROR: could not open workbook '{LEADS_WORKBOOK}': {e}")
        sys.exit(1)

    existing_tabs = {ws.title: ws for ws in workbook.worksheets()}
    courses = load_courses()

    print("Course tabs:")
    for slug, course in courses.items():
        tab = course.worksheet_name
        if tab in existing_tabs:
            setup_course_tab(existing_tabs[tab], tab, args.dry_run)
        else:
            print(f"  {tab}: tab not found in workbook — create it first")

    print("\nHot Leads tab:")
    setup_hot_leads_tab(workbook, args.dry_run)

    print("\nDone.")


if __name__ == "__main__":
    main()
