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
    # Handles +60..., 60..., Excel scientific notation like 6.01234e+10
    s = re.sub(r"[^\d]", "", str(raw).split(".")[0])
    return s if len(s) >= 10 else None


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

    # Only contact leads that are CREATED (or have no status yet)
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
        name = str(row.get(course.name_col, "") or "").strip() or "there"

        if phone is None:
            print(f"  SKIP   invalid phone: {raw_phone!r}")
            skipped += 1
            continue

        if args.dry_run:
            print(f"  [DRY RUN] would send to {phone} ({name})")
            sent += 1
            continue

        response = send_template(
            phone,
            course.outreach_template,
            course.outreach_language,
            variables=[name],
        )

        if response.ok:
            update_lead_in(phone, course.worksheet_name, **{course.status_col: "CONTACTED"})
            upsert_lead(phone, status="CONTACTED", course=course.slug, name=name)
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
