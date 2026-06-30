from __future__ import annotations

import argparse
import re
import sys

from dotenv import load_dotenv

load_dotenv()

from services.course_loader import get_course
from services.google_sheets import get_rows_from, update_lead_in
from services.sqlite_store import add_message, upsert_lead
from services.whatsapp import send_template


def normalize_phone(raw) -> str | None:
    # Handles +60..., 60..., Excel float like 919444209374.0
    s = re.sub(r"[^\d]", "", str(raw).split(".")[0])
    return s if len(s) >= 10 else None


def _clean(row: dict, *keys: str) -> str:
    """Try each key in order, return first non-empty value. Handles 'who_will_pay?' variant."""
    for key in keys:
        val = str(row.get(key) or "").strip()
        if val:
            return val
    return ""


def main() -> None:
    parser = argparse.ArgumentParser(description="Bulk WhatsApp outreach for a course")
    parser.add_argument("--course", required=True, help="Course slug (e.g. sw-testing-july-2026)")
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

    print(f"Course  : {course.name}")
    print(f"Tab     : {course.worksheet_name}")
    print(f"Template: {course.outreach_template} ({course.outreach_language})")
    print(f"Mode    : {'DRY RUN' if args.dry_run else 'LIVE'}\n")

    rows = get_rows_from(course.worksheet_name)

    # Pick up CREATED leads (or rows with no status yet)
    candidates = [
        r for r in rows
        if str(r.get(course.status_col, "")).strip().upper() in ("CREATED", "")
    ]

    if args.limit:
        candidates = candidates[: args.limit]

    print(f"Found {len(candidates)} CREATED lead(s) to process.\n")

    sent = skipped = failed = 0

    for row in candidates:
        raw_phone = row.get(course.phone_col, "")
        phone = normalize_phone(raw_phone)
        name = _clean(row, course.name_col, "full_name", "name") or "there"

        if phone is None:
            print(f"  SKIP   invalid phone: {raw_phone!r}")
            skipped += 1
            continue

        # Extract Meta Lead Ads fields — handle 'who_will_pay?' column name variant
        job_title    = _clean(row, "job_title")
        company_name = _clean(row, "company_name")
        who_will_pay = _clean(row, "who_will_pay", "who_will_pay?")
        email        = _clean(row, "email")

        if args.dry_run:
            print(f"  [DRY RUN] would send to {phone} ({name})")
            if job_title:
                print(f"            job_title={job_title!r}  company={company_name!r}  who_pays={who_will_pay!r}")
            sent += 1
            continue

        response = send_template(
            phone,
            course.outreach_template,
            course.outreach_language,
            variables=[name],
        )

        if response.ok:
            # Mark CONTACTED in the sheet tab
            update_lead_in(phone, course.worksheet_name, **{course.status_col: "CONTACTED"})

            # Upsert ALL Meta fields into SQLite so the bot has them when the lead replies
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
            add_message(phone, direction="outbound", body=course.outreach_template, message_id=msg_id)
            print(f"  SENT   {phone} ({name})")
            sent += 1
        else:
            print(f"  FAIL   {phone} ({name}): HTTP {response.status_code} — {response.text[:120]}")
            failed += 1

    print(f"\nDone.  Sent: {sent} | Skipped: {skipped} | Failed: {failed}")


if __name__ == "__main__":
    main()
