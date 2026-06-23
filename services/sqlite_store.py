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
                last_message TEXT,
                last_reply TEXT,
                assigned_to TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
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
    last_message: str | None = None,
    last_reply: str | None = None,
    assigned_to: str | None = None,
    name: str | None = None,
    course: str | None = None,
) -> bool:
    phone = str(phone).strip()
    if not phone:
        return False

    init_db()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO leads (phone, name, course, status, last_message, last_reply, assigned_to, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(phone) DO UPDATE SET
                name = COALESCE(excluded.name, leads.name),
                course = COALESCE(excluded.course, leads.course),
                status = COALESCE(excluded.status, leads.status),
                last_message = COALESCE(excluded.last_message, leads.last_message),
                last_reply = COALESCE(excluded.last_reply, leads.last_reply),
                assigned_to = COALESCE(excluded.assigned_to, leads.assigned_to),
                updated_at = excluded.updated_at
            """,
            (
                phone,
                name,
                course,
                status,
                last_message,
                last_reply,
                assigned_to,
                _utc_now(),
            ),
        )
    return True


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
