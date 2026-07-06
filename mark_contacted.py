"""
One-off script: marks all leads in the mixed sheet as CONTACTED
and upserts them into local SQLite so Render knows their course when they reply.
"""
from __future__ import annotations
import re
from dotenv import load_dotenv
load_dotenv()

from services.google_sheets import get_client
from services.course_loader import load_courses
from services.sqlite_store import upsert_lead

WORKBOOK = "New Lead Generation | Embedded Sw | July - August 26"
WORKSHEET = "Sheet1"


def normalize_phone(raw) -> str | None:
    s = re.sub(r"[^\d]", "", str(raw).split(".")[0])
    return s if len(s) >= 10 else None


def detect_course(row: dict, courses: dict) -> str | None:
    ad_name = str(row.get("ad_name", "") or "").lower()
    for slug, course in courses.items():
        for kw in (course.keywords or []):
            if kw.lower() in ad_name:
                return slug
    if "yoct" in ad_name or "y0ct" in ad_name:
        for slug in courses:
            if "yocto" in slug:
                return slug
    if "elsi" in ad_name:
        for slug in courses:
            if "internals" in slug:
                return slug
    return None


def main() -> None:
    courses = load_courses()
    wb = get_client().open(WORKBOOK)
    ws = wb.worksheet(WORKSHEET)
    records = ws.get_all_records()
    headers = [h.strip() for h in ws.row_values(1)]
    status_col = headers.index("lead_status") + 1

    print(f"Workbook : {WORKBOOK}")
    print(f"Rows     : {len(records)}\n")

    for i, record in enumerate(records, start=2):
        row = {k.strip(): v for k, v in record.items()}
        raw_phone = row.get("whatsapp_number") or row.get("phone", "")
        phone = normalize_phone(raw_phone)
        name = str(row.get("full_name", "") or "").strip()
        slug = detect_course(row, courses)

        if phone is None:
            print(f"  SKIP   invalid phone: {raw_phone!r}")
            continue

        ws.update_cell(i, status_col, "CONTACTED")
        print(f"  SHEET  {phone} ({name}) → CONTACTED")

        upsert_lead(
            phone,
            status="CONTACTED",
            course=slug,
            name=name,
            job_title=str(row.get("job_title", "") or "").strip() or None,
            company_name=str(row.get("company_name", "") or "").strip() or None,
            who_will_pay=str(row.get("who_will_pay?", "") or row.get("who_will_pay", "") or "").strip() or None,
            email=str(row.get("email", "") or "").strip() or None,
        )
        print(f"  SQLITE {phone} → course={slug}\n")

    print("Done.")


if __name__ == "__main__":
    main()
