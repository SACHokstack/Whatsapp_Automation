from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from xlsx_workbook import read_first_sheet, write_workbook


DEFAULT_WORKBOOK = "whatsapp_outreach_leads_v2.xlsx"
SHEET_NAME = "whatsapp_outreach_leads"
DEFAULT_TEMPLATE = "july_2026_intake_followup"
DEFAULT_LANGUAGE = "en"
DEFAULT_API_VERSION = "v20.0"


def load_env(path: Path = Path(".env")) -> None:
    if not path.exists():
        fallback = Path(".env.example")
        if not fallback.exists():
            return
        path = fallback
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def get_cell(row: list[str], indexes: dict[str, int], column: str) -> str:
    idx = indexes[column]
    return row[idx] if idx < len(row) else ""


def set_cell(row: list[str], indexes: dict[str, int], column: str, value: str) -> None:
    idx = indexes[column]
    while len(row) <= idx:
        row.append("")
    row[idx] = value


def make_payload(to: str, full_name: str, template_name: str, language: str) -> dict:
    return {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language},
            "components": [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": full_name}],
                }
            ],
        },
    }


def send_template(payload: dict, access_token: str, phone_number_id: str, api_version: str) -> tuple[bool, str]:
    url = f"https://graph.facebook.com/{api_version}/{phone_number_id}/messages"
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_text = exc.read().decode("utf-8", errors="replace")
        return False, error_text[:500]
    except Exception as exc:
        return False, str(exc)

    message_id = ""
    messages = data.get("messages") if isinstance(data, dict) else None
    if messages and isinstance(messages, list):
        message_id = str(messages[0].get("id", ""))
    return bool(message_id), message_id or json.dumps(data)[:500]


def eligible_rows(rows: list[list[str]], indexes: dict[str, int]) -> list[tuple[int, list[str]]]:
    selected = []
    for row_number, row in enumerate(rows[1:], start=2):
        status = get_cell(row, indexes, "whatsapp_status")
        normalized_phone = get_cell(row, indexes, "normalized_phone")
        message_id = get_cell(row, indexes, "template_message_id")
        if status == "ready_to_send" and normalized_phone and not message_id:
            selected.append((row_number, row))
    return selected


def main() -> None:
    parser = argparse.ArgumentParser(description="Dry-run or send the July 2026 WhatsApp template.")
    parser.add_argument("--workbook", default=DEFAULT_WORKBOOK)
    parser.add_argument("--send", action="store_true", help="Actually call Meta WhatsApp API.")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--test-to", help="Send or preview one test number, digits or +country format. Does not update the workbook.")
    parser.add_argument("--test-name", default="Test Lead", help="Name used for {{1}} with --test-to.")
    parser.add_argument("--template-name", default=os.getenv("WHATSAPP_TEMPLATE_NAME", DEFAULT_TEMPLATE))
    parser.add_argument("--language", default=os.getenv("WHATSAPP_TEMPLATE_LANGUAGE", DEFAULT_LANGUAGE))
    args = parser.parse_args()

    load_env()

    if args.test_to:
        normalized_test_to = "".join(ch for ch in args.test_to if ch.isdigit())
        if not normalized_test_to:
            raise SystemExit("--test-to must include a country code and phone number")

        payload = make_payload(normalized_test_to, args.test_name, args.template_name, args.language)
        print(f"mode={'live-send' if args.send else 'dry-run'}")
        print("test_send=true")
        print(f"to={normalized_test_to} name={args.test_name}")
        print(json.dumps(payload, indent=2))

        if not args.send:
            print("Dry-run only. No API call made and workbook was not updated.")
            return

        access_token = os.getenv("WHATSAPP_ACCESS_TOKEN") or os.getenv("META_ACCESS_TOKEN", "")
        phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID") or os.getenv("META_PHONE_NUMBER_ID", "")
        api_version = os.getenv("WHATSAPP_API_VERSION") or os.getenv("META_API_VERSION", DEFAULT_API_VERSION)
        if not access_token or not phone_number_id:
            raise SystemExit("WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID are required for --send")

        ok, result = send_template(payload, access_token, phone_number_id, api_version)
        if ok:
            print(f"sent test message_id={result}")
        else:
            raise SystemExit(f"failed test send: {result}")
        return

    _, rows = read_first_sheet(args.workbook)
    if not rows:
        raise SystemExit(f"No rows found in {args.workbook}")

    headers = rows[0]
    indexes = {header: idx for idx, header in enumerate(headers)}
    required = [
        "full_name",
        "normalized_phone",
        "whatsapp_status",
        "template_sent_at",
        "template_name",
        "template_message_id",
        "automation_notes",
    ]
    missing = [column for column in required if column not in indexes]
    if missing:
        raise SystemExit(f"Missing required columns: {', '.join(missing)}")

    selected = eligible_rows(rows, indexes)[: max(args.limit, 0)]
    print(f"mode={'live-send' if args.send else 'dry-run'}")
    print(f"eligible_preview_count={len(selected)}")

    if args.send:
        access_token = os.getenv("WHATSAPP_ACCESS_TOKEN") or os.getenv("META_ACCESS_TOKEN", "")
        phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID") or os.getenv("META_PHONE_NUMBER_ID", "")
        api_version = os.getenv("WHATSAPP_API_VERSION") or os.getenv("META_API_VERSION", DEFAULT_API_VERSION)
        if not access_token or not phone_number_id:
            raise SystemExit("WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID are required for --send")
    else:
        access_token = ""
        phone_number_id = ""
        api_version = os.getenv("META_API_VERSION", DEFAULT_API_VERSION)

    for row_number, row in selected:
        full_name = get_cell(row, indexes, "full_name").strip() or "there"
        normalized_phone = get_cell(row, indexes, "normalized_phone")
        payload = make_payload(normalized_phone, full_name, args.template_name, args.language)

        print(f"row={row_number} to={normalized_phone} name={full_name}")
        print(json.dumps(payload, indent=2))

        if not args.send:
            continue

        ok, result = send_template(payload, access_token, phone_number_id, api_version)
        if ok:
            set_cell(row, indexes, "whatsapp_status", "template_sent")
            set_cell(row, indexes, "template_name", args.template_name)
            set_cell(row, indexes, "template_sent_at", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
            set_cell(row, indexes, "template_message_id", result)
            set_cell(row, indexes, "automation_notes", "template sent")
            print(f"sent row={row_number} message_id={result}")
        else:
            set_cell(row, indexes, "whatsapp_status", "failed")
            set_cell(row, indexes, "automation_notes", f"Meta error: {result}")
            print(f"failed row={row_number} error={result}")

    if args.send and selected:
        write_workbook(args.workbook, SHEET_NAME, headers, rows[1:])
        print(f"updated={args.workbook}")
    elif not selected:
        print("No eligible rows found.")
    else:
        print("Dry-run only. No API call made and workbook was not updated.")


if __name__ == "__main__":
    main()
