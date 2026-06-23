from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_DB_PATH = "whatsapp_bot.db"


def _db_path() -> Path:
    return Path(os.getenv("WHATSAPP_DB_PATH", DEFAULT_DB_PATH))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


@contextmanager
def get_connection():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS leads (
                phone TEXT PRIMARY KEY,
                name TEXT,
                course TEXT,
                status TEXT,
                conversation_state TEXT,
                qualification_step TEXT,
                last_intent TEXT,
                last_intent_reason TEXT,
                needs_human TEXT,
                human_reason TEXT,
                human_status TEXT,
                human_updated_at TEXT,
                last_message TEXT,
                last_reply TEXT,
                assigned_to TEXT,
                occupation TEXT,
                experience TEXT,
                budget TEXT,
                availability TEXT,
                lead_score INTEGER,
                updated_at TEXT NOT NULL
            )
            """
        )
        existing_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(leads)").fetchall()
        }
        for column_sql, column_name in [
            ("ALTER TABLE leads ADD COLUMN conversation_state TEXT", "conversation_state"),
            ("ALTER TABLE leads ADD COLUMN qualification_step TEXT", "qualification_step"),
            ("ALTER TABLE leads ADD COLUMN last_intent TEXT", "last_intent"),
            ("ALTER TABLE leads ADD COLUMN last_intent_reason TEXT", "last_intent_reason"),
            ("ALTER TABLE leads ADD COLUMN needs_human TEXT", "needs_human"),
            ("ALTER TABLE leads ADD COLUMN human_reason TEXT", "human_reason"),
            ("ALTER TABLE leads ADD COLUMN human_status TEXT", "human_status"),
            ("ALTER TABLE leads ADD COLUMN human_updated_at TEXT", "human_updated_at"),
            ("ALTER TABLE leads ADD COLUMN occupation TEXT", "occupation"),
            ("ALTER TABLE leads ADD COLUMN experience TEXT", "experience"),
            ("ALTER TABLE leads ADD COLUMN budget TEXT", "budget"),
            ("ALTER TABLE leads ADD COLUMN availability TEXT", "availability"),
            ("ALTER TABLE leads ADD COLUMN lead_score INTEGER", "lead_score"),
        ]:
            if column_name not in existing_columns:
                conn.execute(column_sql)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone TEXT NOT NULL,
                direction TEXT NOT NULL,
                body TEXT NOT NULL,
                message_id TEXT,
                created_at TEXT NOT NULL
            )
            """
        )


def upsert_lead(
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
    name: str | None = None,
    course: str | None = None,
    occupation: str | None = None,
    experience: str | None = None,
    budget: str | None = None,
    availability: str | None = None,
    lead_score: int | None = None,
) -> bool:
    phone = str(phone).strip()
    if not phone:
        return False

    init_db()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO leads (
                phone, name, course, status, conversation_state, qualification_step, last_intent, last_intent_reason,
                needs_human, human_reason, human_status, human_updated_at, last_message, last_reply, assigned_to,
                occupation, experience, budget, availability, lead_score, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(phone) DO UPDATE SET
                name = COALESCE(excluded.name, leads.name),
                course = COALESCE(excluded.course, leads.course),
                status = COALESCE(excluded.status, leads.status),
                conversation_state = COALESCE(excluded.conversation_state, leads.conversation_state),
                qualification_step = COALESCE(excluded.qualification_step, leads.qualification_step),
                last_intent = COALESCE(excluded.last_intent, leads.last_intent),
                last_intent_reason = COALESCE(excluded.last_intent_reason, leads.last_intent_reason),
                needs_human = COALESCE(excluded.needs_human, leads.needs_human),
                human_reason = COALESCE(excluded.human_reason, leads.human_reason),
                human_status = COALESCE(excluded.human_status, leads.human_status),
                human_updated_at = COALESCE(excluded.human_updated_at, leads.human_updated_at),
                last_message = COALESCE(excluded.last_message, leads.last_message),
                last_reply = COALESCE(excluded.last_reply, leads.last_reply),
                assigned_to = COALESCE(excluded.assigned_to, leads.assigned_to),
                occupation = COALESCE(excluded.occupation, leads.occupation),
                experience = COALESCE(excluded.experience, leads.experience),
                budget = COALESCE(excluded.budget, leads.budget),
                availability = COALESCE(excluded.availability, leads.availability),
                lead_score = COALESCE(excluded.lead_score, leads.lead_score),
                updated_at = excluded.updated_at
            """,
            (
                phone,
                name,
                course,
                status,
                conversation_state,
                qualification_step,
                last_intent,
                last_intent_reason,
                needs_human,
                human_reason,
                human_status,
                human_updated_at,
                last_message,
                last_reply,
                assigned_to,
                occupation,
                experience,
                budget,
                availability,
                lead_score,
                _utc_now(),
            ),
        )
    return True


def get_lead(phone: str) -> dict[str, str] | None:
    phone = str(phone).strip()
    if not phone:
        return None

    init_db()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM leads WHERE phone = ?",
            (phone,),
        ).fetchone()

    if row is None:
        return None
    return {key: ("" if value is None else str(value)) for key, value in dict(row).items()}


def add_message(
    phone: str,
    *,
    direction: str,
    body: str,
    message_id: str | None = None,
) -> bool:
    phone = str(phone).strip()
    body = str(body).strip()
    if not phone or not body:
        return False

    init_db()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO messages (phone, direction, body, message_id, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (phone, direction, body, message_id, _utc_now()),
        )
    return True
