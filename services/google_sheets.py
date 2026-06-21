from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Iterable

import gspread
import pandas as pd
from oauth2client.service_account import ServiceAccountCredentials


SCOPE = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]


@dataclass(slots=True)
class Lead:
    row_number: int
    name: str
    phone: str
    status: str


def _normalize_header(value: str) -> str:
    return value.strip().lower().replace(" ", "_")


def _first_existing(headers: Iterable[str], candidates: Iterable[str]) -> str:
    normalized = {_normalize_header(header): header for header in headers}
    for candidate in candidates:
        key = _normalize_header(candidate)
        if key in normalized:
            return normalized[key]
    raise KeyError(f"Missing required column. Tried: {', '.join(candidates)}")


class GoogleSheetsClient:
    def __init__(
        self,
        spreadsheet_id: str,
        worksheet_name: str = "Sheet1",
        credentials_file: str | None = None,
        spreadsheet_url: str | None = None,
    ) -> None:
        self.spreadsheet_id = spreadsheet_id.strip()
        self.spreadsheet_url = spreadsheet_url.strip() if spreadsheet_url else ""
        self.worksheet_name = worksheet_name.strip() or "Sheet1"
        self.credentials_file = credentials_file or os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "")
        if not self.credentials_file:
            raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_FILE is required")
        self._worksheet = None

    @classmethod
    def from_env(cls) -> "GoogleSheetsClient":
        spreadsheet_id = os.getenv("GOOGLE_SHEET_ID", "").strip()
        spreadsheet_url = os.getenv("GOOGLE_SHEET_URL", "").strip()
        if not spreadsheet_id and not spreadsheet_url:
            raise RuntimeError("GOOGLE_SHEET_ID or GOOGLE_SHEET_URL is required")
        worksheet_name = os.getenv("GOOGLE_WORKSHEET_NAME", "Sheet1")
        credentials_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "")
        return cls(
            spreadsheet_id=spreadsheet_id,
            worksheet_name=worksheet_name,
            credentials_file=credentials_file,
            spreadsheet_url=spreadsheet_url,
        )

    def _client(self) -> gspread.Client:
        credentials = ServiceAccountCredentials.from_json_keyfile_name(self.credentials_file, SCOPE)
        return gspread.authorize(credentials)

    def worksheet(self):
        if self._worksheet is not None:
            return self._worksheet

        client = self._client()
        if self.spreadsheet_url:
            spreadsheet = client.open_by_url(self.spreadsheet_url)
        else:
            spreadsheet = client.open_by_key(self.spreadsheet_id)
        self._worksheet = spreadsheet.worksheet(self.worksheet_name)
        return self._worksheet

    def get_pending_leads(self) -> list[Lead]:
        rows = self.worksheet().get_all_values()
        if not rows:
            return []

        headers = rows[0]
        df = pd.DataFrame(rows[1:], columns=headers)
        if df.empty:
            return []

        name_col = _first_existing(headers, ["Name", "Full Name", "full_name"])
        phone_col = _first_existing(headers, ["Phone", "Mobile", "phone"])
        status_col = _first_existing(headers, ["Status", "Lead Status", "status"])

        status_values = df[status_col].fillna("").astype(str).str.strip().str.lower()
        pending_df = df[status_values == "pending"].copy()
        if pending_df.empty:
            return []

        pending_rows: list[Lead] = []
        for df_index, row in pending_df.iterrows():
            row_number = int(df_index) + 2
            pending_rows.append(
                Lead(
                    row_number=row_number,
                    name=str(row.get(name_col, "")).strip(),
                    phone=str(row.get(phone_col, "")).strip(),
                    status=str(row.get(status_col, "")).strip(),
                )
            )
        return pending_rows

    def update_status(self, lead: Lead, status: str) -> None:
        self.mark_row_status(lead.row_number, status)

    def mark_row_status(self, row_number: int, status: str) -> None:
        worksheet = self.worksheet()
        headers = worksheet.row_values(1)
        status_col = _first_existing(headers, ["Status", "Lead Status", "status"])
        column_number = headers.index(status_col) + 1
        worksheet.update_cell(row_number, column_number, status)

    def mark_as_sent(self, lead: Lead) -> None:
        self.update_status(lead, "Sent")

    def mark_as_failed(self, lead: Lead, reason: str | None = None) -> None:
        self.update_status(lead, "Failed")
        if reason:
            self.append_note(lead.row_number, reason)

    def append_note(self, row_number: int, note: str) -> None:
        worksheet = self.worksheet()
        headers = worksheet.row_values(1)
        note_col = None
        for candidate in ("Notes", "notes", "Remark", "Remarks"):
            try:
                note_col = _first_existing(headers, [candidate])
                break
            except KeyError:
                continue
        if note_col is None:
            return
        column_number = headers.index(note_col) + 1
        existing = worksheet.cell(row_number, column_number).value or ""
        updated = f"{existing}\n{note}".strip() if existing else note
        worksheet.update_cell(row_number, column_number, updated)


def get_pending_leads() -> list[Lead]:
    return GoogleSheetsClient.from_env().get_pending_leads()

