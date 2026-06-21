from __future__ import annotations

import argparse
import re
from pathlib import Path

from xlsx_workbook import read_first_sheet, write_workbook


DEFAULT_INPUT = "whatsapp_outreach_leads.xlsx"
DEFAULT_OUTPUT = "whatsapp_outreach_leads_v2.xlsx"
SHEET_NAME = "whatsapp_outreach_leads"
ADDED_COLUMNS = ["normalized_phone", "automation_notes", "template_name", "template_message_id"]


def normalize_phone(raw_phone: str) -> tuple[str, str | None]:
    original = (raw_phone or "").strip()
    if original.startswith("p:"):
        original = original[2:].strip()

    digits = re.sub(r"\D", "", original)
    if not digits:
        return "", "missing country code"

    has_explicit_international_prefix = original.startswith("+") or original.startswith("00")
    if digits.startswith("00"):
        digits = digits[2:]

    if digits.startswith("0") and not has_explicit_international_prefix:
        return digits, "missing country code"

    if not re.fullmatch(r"[1-9]\d{7,14}", digits):
        return digits, "invalid phone number"

    return digits, None


def row_value(row: list[str], indexes: dict[str, int], column: str) -> str:
    idx = indexes[column]
    return row[idx] if idx < len(row) else ""


def build_v2(input_path: Path, output_path: Path) -> tuple[int, int, int]:
    _, rows = read_first_sheet(input_path)
    if not rows:
        raise SystemExit(f"No rows found in {input_path}")

    headers = rows[0]
    indexes = {header: idx for idx, header in enumerate(headers)}
    required = ["phone", "whatsapp_status"]
    missing = [column for column in required if column not in indexes]
    if missing:
        raise SystemExit(f"Missing required columns: {', '.join(missing)}")

    output_headers = [header for header in headers if header not in ADDED_COLUMNS] + ADDED_COLUMNS
    output_rows: list[list[str]] = []
    ready_count = 0
    invalid_count = 0

    for row in rows[1:]:
        if not any(cell.strip() for cell in row):
            continue

        record = {header: (row[indexes[header]] if indexes[header] < len(row) else "") for header in headers}
        normalized_phone, error = normalize_phone(row_value(row, indexes, "phone"))

        if error:
            record["whatsapp_status"] = "invalid_number"
            record["automation_notes"] = error
            invalid_count += 1
        else:
            if record.get("whatsapp_status") in {"", "new", "invalid_number", "ready_to_send"}:
                record["whatsapp_status"] = "ready_to_send"
            record["automation_notes"] = "phone normalized"
            ready_count += 1

        record["normalized_phone"] = normalized_phone
        record.setdefault("template_name", "")
        record.setdefault("template_message_id", "")

        output_rows.append([record.get(header, "") for header in output_headers])

    write_workbook(output_path, SHEET_NAME, output_headers, output_rows)
    return len(output_rows), ready_count, invalid_count


def main() -> None:
    parser = argparse.ArgumentParser(description="Create normalized WhatsApp outreach workbook.")
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    total, ready, invalid = build_v2(Path(args.input), Path(args.output))
    print(f"created={args.output}")
    print(f"rows={total}")
    print(f"ready_to_send={ready}")
    print(f"invalid_number={invalid}")


if __name__ == "__main__":
    main()
