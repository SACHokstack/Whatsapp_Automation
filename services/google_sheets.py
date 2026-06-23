from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

import gspread
from google.oauth2.service_account import Credentials


SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
DEFAULT_SHEET_NAME = "Lead _ SW Testing July 2026 Intake"
DEFAULT_WORKSHEET_NAME = "Sheet1"
DEFAULT_CREDENTIALS_FILE = "credentials.json"


@dataclass(slots=True)
class LeadRow:
    row_number: int
    data: dict[str, str]


def _normalize_phone(phone: str) -> str:
    return "".join(ch for ch in str(phone or "") if ch.isdigit())


def _sheet_name() -> str:
    return os.getenv("GOOGLE_SHEET_NAME", DEFAULT_SHEET_NAME)


def _worksheet_name() -> str:
    return os.getenv("GOOGLE_WORKSHEET_NAME", DEFAULT_WORKSHEET_NAME)


def _credentials_file() -> str:
    return os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", DEFAULT_CREDENTIALS_FILE)


@lru_cache(maxsize=1)
def get_client() -> gspread.Client:
    creds = Credentials.from_service_account_file(
        _credentials_file(),
        scopes=SCOPES,
    )
    return gspread.authorize(creds)


@lru_cache(maxsize=1)
def get_sheet():
    return get_client().open(_sheet_name()).worksheet(_worksheet_name())


def get_rows() -> list[dict[str, str]]:
    sheet = get_sheet()
    return sheet.get_all_records()


def find_row_by_phone(phone: str) -> LeadRow | None:
    sheet = get_sheet()
    records = sheet.get_all_records()
    headers = sheet.row_values(1)
    phone_digits = _normalize_phone(phone)

    for index, record in enumerate(records, start=2):
        row_phone = _normalize_phone(record.get("phone", ""))
        if row_phone and row_phone == phone_digits:
            return LeadRow(row_number=index, data={key: str(value) for key, value in record.items()})

    if not headers:
        return None
    return None


def _header_index(sheet, column_name: str) -> int | None:
    headers = sheet.row_values(1)
    try:
        return headers.index(column_name) + 1
    except ValueError:
        return None


def update_lead(
    phone: str,
    *,
    status: str | None = None,
    qualification_step: str | None = None,
    last_message: str | None = None,
    last_reply: str | None = None,
    assigned_to: str | None = None,
    occupation: str | None = None,
    experience: str | None = None,
    budget: str | None = None,
    availability: str | None = None,
    lead_score: int | None = None,
) -> bool:
    sheet = get_sheet()
    row = find_row_by_phone(phone)
    if row is None:
        return False

    updates: list[tuple[str, str]] = []
    if status is not None:
        updates.append(("status", status))
    if qualification_step is not None:
        updates.append(("qualification_step", qualification_step))
    if last_message is not None:
        updates.append(("last_message", last_message))
    if last_reply is not None:
        updates.append(("last_reply", last_reply))
    if assigned_to is not None:
        updates.append(("assigned_to", assigned_to))
    if occupation is not None:
        updates.append(("occupation", occupation))
    if experience is not None:
        updates.append(("experience", experience))
    if budget is not None:
        updates.append(("budget", budget))
    if availability is not None:
        updates.append(("availability", availability))
    if lead_score is not None:
        updates.append(("lead_score", str(lead_score)))

    headers = sheet.row_values(1)
    for column_name, value in updates:
        try:
            col_index = headers.index(column_name) + 1
        except ValueError:
            continue
        sheet.update_cell(row.row_number, col_index, value)

    return True


def print_rows() -> None:
    rows = get_rows()
    print(rows)
