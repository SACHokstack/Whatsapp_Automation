"""
Watch the lead source for new rows and push them into the leads database.

Two modes:
  once   — read the source and add anything not already in the database (default)
  watch  — keep doing that on an interval, so new rows land in Postgres without anyone
           remembering to run an import

The source is the Google Sheets leads workbook by default, or a local Excel export with
--file. Both are safe to re-read: a lead is only written when its phone isn't in the
database yet, so nothing is duplicated and no in-flight conversation is reset.

Usage:
  python sync_leads.py                             # one pass over the Google Sheet
  python sync_leads.py --file leads.xlsx           # one pass over an Excel export
  python sync_leads.py --file leads.xlsx --watch   # re-check that file every 60s
  python sync_leads.py --watch --interval 300      # poll the sheet every 5 minutes
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from services.lead_sync import sync_leads
from services.persistence import backend_name


def _stamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _event(kind: str, message: str) -> None:
    if kind in ("new", "updated", "invalid", "unmatched", "rebaselined"):
        print(f"  {kind.upper():9} {message}")


def _run_once(excel_path: Path | None, *, dry_run: bool, update: bool, verbose: bool) -> int:
    result = sync_leads(
        excel_path=excel_path,
        dry_run=dry_run,
        update=update,
        on_event=_event if verbose else None,
    )
    print(
        f"[{_stamp()}] scanned={result.scanned} new={result.added} "
        f"(pre-cutoff baseline={result.baseline}) rebaselined={result.rebaselined} "
        f"updated={result.updated} "
        f"already-known={result.skipped_existing} invalid={result.skipped_invalid} "
        f"unmatched={result.unmatched}"
    )
    return result.added


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sync new leads from Excel/Google Sheet into the DB"
    )
    parser.add_argument(
        "--file",
        default=None,
        help="Excel export to watch. Omit to read the Google Sheets leads workbook.",
    )
    parser.add_argument("--watch", action="store_true", help="Keep polling instead of one pass")
    parser.add_argument(
        "--interval", type=int, default=60, help="Seconds between passes in --watch (default: 60)"
    )
    parser.add_argument(
        "--update", action="store_true", help="Also refresh leads already in the DB"
    )
    parser.add_argument("--dry-run", action="store_true", help="Report new rows without writing")
    parser.add_argument("--quiet", action="store_true", help="Only print the per-pass summary")
    args = parser.parse_args()

    excel_path = Path(args.file) if args.file else None
    if excel_path and not excel_path.exists():
        print(f"Error: file not found: {excel_path}")
        sys.exit(1)

    print(f"Source   : {excel_path.name if excel_path else 'Google Sheets leads workbook'}")
    print(f"Database : {backend_name()}")
    print(
        f"Mode     : {'WATCH every ' + str(args.interval) + 's' if args.watch else 'ONE PASS'}"
        f"{' (DRY RUN)' if args.dry_run else ''}\n"
    )

    if not args.watch:
        _run_once(excel_path, dry_run=args.dry_run, update=args.update, verbose=not args.quiet)
        return

    try:
        while True:
            try:
                _run_once(
                    excel_path, dry_run=args.dry_run, update=args.update, verbose=not args.quiet
                )
            except Exception as exc:  # a bad pass shouldn't kill the watcher
                print(f"[{_stamp()}] sync failed: {exc}")
            time.sleep(max(5, args.interval))
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
