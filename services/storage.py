from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DB_PATH = Path(os.getenv("WHATSAPP_SQLITE_PATH", "whatsapp_bot.db"))


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db() -> None:
    with get_connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS leads (
                phone TEXT PRIMARY KEY,
                name TEXT,
                source_status TEXT,
                last_seen_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone TEXT NOT NULL,
                direction TEXT NOT NULL,
                whatsapp_message_id TEXT,
                message_type TEXT,
                message_status TEXT,
                body TEXT,
                raw_json TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(phone) REFERENCES leads(phone)
            );

            CREATE TABLE IF NOT EXISTS replies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone TEXT NOT NULL,
                reply_text TEXT NOT NULL,
                intent TEXT,
                raw_json TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(phone) REFERENCES leads(phone)
            );
            """
        )


def upsert_lead(phone: str, name: str | None = None, source_status: str | None = None) -> None:
    if not phone:
        return
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO leads (phone, name, source_status, last_seen_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(phone) DO UPDATE SET
                name = COALESCE(excluded.name, leads.name),
                source_status = COALESCE(excluded.source_status, leads.source_status),
                last_seen_at = excluded.last_seen_at
            """,
            (phone, name, source_status, _now()),
        )


def save_message(
    phone: str,
    direction: str,
    whatsapp_message_id: str | None = None,
    message_type: str | None = None,
    message_status: str | None = None,
    body: str | None = None,
    raw_json: dict[str, Any] | list[Any] | None = None,
) -> None:
    if not phone:
        return
    upsert_lead(phone=phone)
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO messages (
                phone, direction, whatsapp_message_id, message_type, message_status, body, raw_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                phone,
                direction,
                whatsapp_message_id,
                message_type,
                message_status,
                body,
                json.dumps(raw_json, ensure_ascii=False) if raw_json is not None else None,
                _now(),
            ),
        )


def save_reply(phone: str, reply_text: str, intent: str | None = None, raw_json: dict[str, Any] | None = None) -> None:
    if not phone or not reply_text:
        return
    upsert_lead(phone=phone)
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO replies (phone, reply_text, intent, raw_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                phone,
                reply_text,
                intent,
                json.dumps(raw_json, ensure_ascii=False) if raw_json is not None else None,
                _now(),
            ),
        )


def get_stats() -> dict[str, int]:
    with get_connection() as connection:
        lead_count = connection.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        message_count = connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        reply_count = connection.execute("SELECT COUNT(*) FROM replies").fetchone()[0]
        inbound_count = connection.execute(
            "SELECT COUNT(*) FROM messages WHERE direction = 'inbound'"
        ).fetchone()[0]
        outbound_count = connection.execute(
            "SELECT COUNT(*) FROM messages WHERE direction = 'outbound'"
        ).fetchone()[0]
    return {
        "leads": int(lead_count),
        "messages": int(message_count),
        "replies": int(reply_count),
        "inbound_messages": int(inbound_count),
        "outbound_messages": int(outbound_count),
    }
