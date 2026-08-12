from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

load_dotenv()

from services.auto_outreach import dispatch_outreach
from services.course_loader import get_course, load_courses
from services.lead_sync import cell as _clean
from services.lead_sync import detect_course_from_ad, normalize_phone
from services.persistence import add_message, backend_name, upsert_lead
from services.whatsapp import send_template


def _detect_course_slug_from_row(row: dict, courses: dict) -> str | None:
    """Detect course slug from the ad_name, adset_name, or campaign_name column."""
    ad_name = _clean(row, "ad_name", "adset_name", "campaign_name")
    return detect_course_from_ad(ad_name, courses)


def _candidates_from_sheet(course, args, worksheet: str, workbook: str | None) -> list[dict]:
    from services.google_sheets import get_rows_from

    rows = get_rows_from(worksheet, workbook)

    # In mixed-sheet mode, filter rows to this course based on ad_name detection
    if args.worksheet or args.workbook:
        all_courses = load_courses()
        original_count = len(rows)
        rows = [r for r in rows if _detect_course_slug_from_row(r, all_courses) == args.course]
        print(f"Rows in sheet : {original_count}")
        print(f"Matching course: {len(rows)}\n")

    # Pick up CREATED leads (or rows with no status yet)
    rows = [r for r in rows if str(r.get(course.status_col, "")).strip().upper() in ("CREATED", "")]
    if args.limit:
        rows = rows[: args.limit]

    return [
        {
            # Support both 'phone' and 'whatsapp_number' column names
            "phone": normalize_phone(row.get(course.phone_col) or row.get("whatsapp_number", "")),
            "name": _clean(row, course.name_col, "full_name", "name") or "there",
            "job_title": _clean(row, "job_title"),
            "company_name": _clean(row, "company_name"),
            # Handle the 'who_will_pay?' column name variant
            "who_will_pay": _clean(row, "who_will_pay", "who_will_pay?"),
            "email": _clean(row, "email"),
            "raw_phone": row.get(course.phone_col) or row.get("whatsapp_number", ""),
        }
        for row in rows
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Bulk WhatsApp outreach for a course")
    parser.add_argument("--course", required=True, help="Course slug (e.g. sw-testing-july-2026)")
    parser.add_argument(
        "--source",
        choices=("db", "sheet"),
        default="db",
        help="Read leads from the leads database (default) or the Google Sheet",
    )
    parser.add_argument(
        "--worksheet",
        default=None,
        help="Worksheet tab name to read from (for shared mixed sheets, e.g. 'Sheet1'). "
        "Defaults to the course's own tab.",
    )
    parser.add_argument(
        "--workbook",
        default=None,
        help="Google Sheet workbook name (overrides TIMMINS_LEADS_WORKBOOK env var).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print actions without sending")
    parser.add_argument("--limit", type=int, default=None, help="Max leads to process")
    args = parser.parse_args()

    course = get_course(args.course)
    if course is None:
        print(f'Error: Course "{args.course}" not found.')
        sys.exit(1)

    if not course.outreach_template:
        print(f'Error: Course "{args.course}" has no outreach.template_name in config.yaml.')
        sys.exit(1)

    # Determine where to read leads from
    worksheet = args.worksheet or course.worksheet_name
    workbook = args.workbook or None
    mixed_mode = bool(args.worksheet or args.workbook)

    print(f"Course   : {course.name}")
    if args.source == "db":
        print(f"Source   : leads database ({backend_name()})")
    else:
        print(f"Workbook : {workbook or '(default Timmins Leads)'}")
        print(f"Tab      : {worksheet}{' [mixed — filtering by course]' if mixed_mode else ''}")
    print(f"Template : {course.outreach_template} ({course.outreach_language})")
    print(f"Mode     : {'DRY RUN' if args.dry_run else 'LIVE'}\n")

    if args.source == "db":
        # Same dispatcher the scheduler uses — force=True because a human asked for it,
        # so it ignores AUTO_OUTREACH_ENABLED and the send window.
        result = dispatch_outreach(
            course_slug=course.slug,
            limit=args.limit,
            dry_run=args.dry_run,
            force=True,
            on_event=lambda kind, message: print(f"  {kind.upper():10} {message}"),
        )
        print(
            f"\nDone.  Sent: {result.sent} | Skipped: {result.skipped} | "
            f"Failed: {result.failed} | Still pending: {result.pending}"
        )
        return

    candidates = _candidates_from_sheet(course, args, worksheet, workbook)

    print(f"Found {len(candidates)} CREATED lead(s) to process.\n")

    sent = skipped = failed = 0

    for row in candidates:
        phone = row["phone"]
        name = row["name"]
        job_title = row["job_title"]
        company_name = row["company_name"]
        who_will_pay = row["who_will_pay"]
        email = row["email"]

        if phone is None:
            print(f"  SKIP   invalid phone: {row['raw_phone']!r}")
            skipped += 1
            continue

        if args.dry_run:
            print(f"  [DRY RUN] would send to {phone} ({name})")
            if job_title:
                print(
                    f"            job_title={job_title!r}  company={company_name!r}  who_pays={who_will_pay!r}"
                )
            sent += 1
            continue

        response = send_template(
            phone,
            course.outreach_template,
            course.outreach_language,
            variables=[name],
        )

        if response.ok:
            if args.source == "sheet":
                # Mark CONTACTED in the same sheet/tab we read from
                from services.google_sheets import update_lead_in

                update_lead_in(phone, worksheet, workbook, **{course.status_col: "CONTACTED"})

            # Upsert ALL Meta fields into the leads db so the bot has them when the lead replies
            upsert_lead(
                phone,
                status="CONTACTED",
                course=course.slug,
                name=name,
                job_title=job_title or None,
                company_name=company_name or None,
                who_will_pay=who_will_pay or None,
                email=email or None,
            )

            try:
                msg_id = response.json().get("messages", [{}])[0].get("id")
            except Exception:
                msg_id = None
            add_message(
                phone, direction="outbound", body=course.outreach_template, message_id=msg_id
            )
            print(f"  SENT   {phone} ({name})")
            sent += 1
        else:
            print(f"  FAIL   {phone} ({name}): HTTP {response.status_code} — {response.text[:120]}")
            failed += 1

    print(f"\nDone.  Sent: {sent} | Skipped: {skipped} | Failed: {failed}")


if __name__ == "__main__":
    main()
