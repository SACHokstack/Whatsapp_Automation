from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

import gspread
from google.oauth2.service_account import Credentials


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
DEFAULT_SHEET_NAME = "Lead _ SW Testing July 2026 Intake"
DEFAULT_WORKSHEET_NAME = "Sheet1"
DEFAULT_CREDENTIALS_FILE = "credentials.json"
LEADS_WORKBOOK = os.getenv("TIMMINS_LEADS_WORKBOOK", "Timmins Leads")


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
    conversation_state: str | None = None,
    qualification_step: str | None = None,
    last_intent: str | None = None,
    last_intent_reason: str | None = None,
    needs_human: str | None = None,
    human_reason: str | None = None,
    human_status: str | None = None,
    human_updated_at: str | None = None,
    last_message: str | None = None,
    last_reply: str | None = None,
    assigned_to: str | None = None,
    occupation: str | None = None,
    experience: str | None = None,
    budget: str | None = None,
    availability: str | None = None,
    lead_score: int | None = None,
    # Meta Lead Ads fields
    job_title: str | None = None,
    company_name: str | None = None,
    who_will_pay: str | None = None,
    email: str | None = None,
    # Qualification conversation fields
    experience_years: str | None = None,
    technologies: str | None = None,
    motivation: str | None = None,
    learning_goals: str | None = None,
    funding_path: str | None = None,
) -> bool:
    sheet = get_sheet()
    row = find_row_by_phone(phone)
    if row is None:
        return False

    updates: list[tuple[str, str]] = []
    for col, val in [
        ("status", status),
        ("conversation_state", conversation_state),
        ("qualification_step", qualification_step),
        ("last_intent", last_intent),
        ("last_intent_reason", last_intent_reason),
        ("needs_human", needs_human),
        ("human_reason", human_reason),
        ("human_status", human_status),
        ("human_updated_at", human_updated_at),
        ("last_message", last_message),
        ("last_reply", last_reply),
        ("assigned_to", assigned_to),
        ("occupation", occupation),
        ("experience", experience),
        ("budget", budget),
        ("availability", availability),
        ("lead_score", str(lead_score) if lead_score is not None else None),
        ("job_title", job_title),
        ("company_name", company_name),
        ("who_will_pay", who_will_pay),
        ("email", email),
        ("experience_years", experience_years),
        ("technologies", technologies),
        ("motivation", motivation),
        ("learning_goals", learning_goals),
        ("funding_path", funding_path),
    ]:
        if val is not None:
            updates.append((col, val))

    return _update_worksheet(sheet, phone, updates)


def print_rows() -> None:
    rows = get_rows()
    print(rows)


# --- Multi-course workbook helpers (outreach runner) ---

def get_worksheet(worksheet_name: str):
    """Open a named tab from the Timmins Leads workbook."""
    return get_client().open(LEADS_WORKBOOK).worksheet(worksheet_name)


def get_rows_from(worksheet_name: str) -> list[dict]:
    rows = get_worksheet(worksheet_name).get_all_records()
    # Strip whitespace from header keys and string values (Google Sheets often has trailing spaces)
    return [
        {k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in row.items()}
        for row in rows
    ]


def _update_worksheet(ws, phone: str, updates: list[tuple[str, str]]) -> bool:
    """Write column updates for a phone number into any worksheet."""
    records = ws.get_all_records()
    headers = ws.row_values(1)
    phone_digits = _normalize_phone(phone)

    row_number: int | None = None
    for index, record in enumerate(records, start=2):
        if _normalize_phone(record.get("phone", "")) == phone_digits:
            row_number = index
            break

    if row_number is None:
        return False

    for column_name, value in updates:
        try:
            col_index = headers.index(column_name) + 1
        except ValueError:
            continue
        ws.update_cell(row_number, col_index, value)

    return True


def update_lead_in(phone: str, worksheet_name: str, **kwargs) -> bool:
    """Update a lead's columns in a specific course tab of the Timmins Leads workbook."""
    ws = get_worksheet(worksheet_name)
    updates = [(k, str(v)) for k, v in kwargs.items() if v is not None]
    if not updates:
        return False
    return _update_worksheet(ws, phone, updates)


HOT_LEADS_TAB = "Hot Leads"

# Ordered columns written to the Hot Leads tab — must match the tab's header row exactly
_HOT_LEAD_COLUMNS = [
    "timestamp",
    "name",
    "phone",
    "course",
    "job_title",
    "company_name",
    "experience_years",
    "technologies",
    "motivation",
    "learning_goals",
    "funding_path",
    "availability",
    "lead_score",
    "status",
    "assigned_to",
]


def append_hot_lead(phone: str, lead: dict, updates: dict) -> bool:
    """Append a row to the Hot Leads tab when a lead scores HOT."""
    from datetime import datetime, timezone

    def _v(key: str) -> str:
        return str(updates.get(key) or lead.get(key) or "").strip()

    row = [
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        _v("name"),
        phone,
        _v("course"),
        _v("job_title"),
        _v("company_name"),
        _v("experience_years"),
        _v("technologies"),
        _v("motivation"),
        _v("learning_goals"),
        _v("funding_path") or _v("who_will_pay"),
        _v("availability"),
        _v("lead_score"),
        "HOT",
        _v("assigned_to") or "Raj",
    ]

    try:
        ws = get_worksheet(HOT_LEADS_TAB)
        ws.append_row(row, value_input_option="USER_ENTERED")
        return True
    except Exception as e:
        print("HOT LEADS TAB ERROR:", e)
        return False
