from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _postgres() -> bool:
    return os.getenv("DATABASE_URL", "").lower().startswith(("postgres://", "postgresql://"))


def _sqlite_path() -> Path:
    return Path(
        os.getenv("WHATSAPP_DB_PATH") or os.getenv("WHATSAPP_SQLITE_PATH") or "whatsapp_bot.db"
    )


_initialized_key = ""
_init_lock = threading.Lock()


def init_queue() -> None:
    global _initialized_key
    key = os.getenv("DATABASE_URL", "").strip() if _postgres() else str(_sqlite_path().resolve())
    if _initialized_key == key:
        return
    with _init_lock:
        if _initialized_key == key:
            return
        _initialize_queue_schema()
        _initialized_key = key


def _initialize_queue_schema() -> None:
    if _postgres():
        import psycopg

        with (
            psycopg.connect(os.environ["DATABASE_URL"]) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS inbound_events (
                    event_id TEXT PRIMARY KEY,
                    sender TEXT NOT NULL,
                    payload JSONB NOT NULL,
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    available_at TIMESTAMPTZ NOT NULL,
                    locked_at TIMESTAMPTZ,
                    last_error TEXT,
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS inbound_events_claim_idx "
                "ON inbound_events(status, available_at, created_at)"
            )
        return

    connection = sqlite3.connect(_sqlite_path())
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS inbound_events (
                event_id TEXT PRIMARY KEY,
                sender TEXT NOT NULL,
                payload TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                attempts INTEGER NOT NULL DEFAULT 0,
                available_at TEXT NOT NULL,
                locked_at TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS inbound_events_claim_idx "
            "ON inbound_events(status, available_at, created_at)"
        )
        connection.commit()
    finally:
        connection.close()


def enqueue_event(event_id: str, sender: str, payload: dict[str, Any]) -> bool:
    init_queue()
    now = _utc_now()
    if _postgres():
        import psycopg

        with (
            psycopg.connect(os.environ["DATABASE_URL"]) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                """
                INSERT INTO inbound_events
                    (event_id, sender, payload, status, available_at, created_at, updated_at)
                VALUES (%s, %s, %s::jsonb, 'PENDING', %s, %s, %s)
                ON CONFLICT(event_id) DO NOTHING
                """,
                (event_id, sender, json.dumps(payload), now, now, now),
            )
            return cursor.rowcount > 0

    connection = sqlite3.connect(_sqlite_path())
    try:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO inbound_events
                (event_id, sender, payload, status, available_at, created_at, updated_at)
            VALUES (?, ?, ?, 'PENDING', ?, ?, ?)
            """,
            (
                event_id,
                sender,
                json.dumps(payload),
                now.isoformat(),
                now.isoformat(),
                now.isoformat(),
            ),
        )
        connection.commit()
        return cursor.rowcount > 0
    finally:
        connection.close()


def enqueue_webhook_body(body: dict) -> int:
    queued = 0
    for entry in body.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            for message in value.get("messages") or []:
                event_id = str(message.get("id") or "")
                sender = str(message.get("from") or "")
                if not event_id or not sender:
                    continue
                payload = dict(value)
                payload["messages"] = [message]
                payload.pop("statuses", None)
                queued += int(enqueue_event(f"message:{event_id}", sender, payload))
            for status in value.get("statuses") or []:
                status_id = str(status.get("id") or "")
                status_name = str(status.get("status") or "")
                timestamp = str(status.get("timestamp") or "")
                if not status_id:
                    continue
                payload = dict(value)
                payload["statuses"] = [status]
                payload.pop("messages", None)
                event_id = f"status:{status_id}:{status_name}:{timestamp}"
                sender = str(status.get("recipient_id") or "status")
                queued += int(enqueue_event(event_id, sender, payload))
    return queued


def claim_event(*, lease_seconds: int = 120) -> dict[str, Any] | None:
    init_queue()
    now = _utc_now()
    stale = now - timedelta(seconds=lease_seconds)
    if _postgres():
        import psycopg
        from psycopg.rows import dict_row

        with (
            psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                """
                SELECT * FROM inbound_events
                WHERE (status = 'PENDING' AND available_at <= %s)
                   OR (status = 'PROCESSING' AND locked_at <= %s)
                ORDER BY created_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """,
                (now, stale),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            cursor.execute(
                "UPDATE inbound_events SET status='PROCESSING', attempts=attempts+1, "
                "locked_at=%s, updated_at=%s WHERE event_id=%s",
                (now, now, row["event_id"]),
            )
            result = dict(row)
            result["payload"] = (
                result["payload"]
                if isinstance(result["payload"], dict)
                else json.loads(result["payload"])
            )
            return result

    connection = sqlite3.connect(_sqlite_path(), isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT * FROM inbound_events
            WHERE (status = 'PENDING' AND available_at <= ?)
               OR (status = 'PROCESSING' AND locked_at <= ?)
            ORDER BY created_at LIMIT 1
            """,
            (now.isoformat(), stale.isoformat()),
        ).fetchone()
        if row is None:
            connection.commit()
            return None
        connection.execute(
            "UPDATE inbound_events SET status='PROCESSING', attempts=attempts+1, "
            "locked_at=?, updated_at=? WHERE event_id=?",
            (now.isoformat(), now.isoformat(), row["event_id"]),
        )
        connection.commit()
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        return result
    finally:
        connection.close()


def complete_event(event_id: str) -> None:
    _update_event(event_id, status="DONE", error=None, delay_seconds=0)


def fail_event(event_id: str, error: str, *, attempts: int, max_attempts: int = 8) -> None:
    if attempts >= max_attempts:
        _update_event(event_id, status="FAILED", error=error[:500], delay_seconds=0)
        return
    delay = min(300, 2 ** min(max(attempts, 1), 8))
    _update_event(event_id, status="PENDING", error=error[:500], delay_seconds=delay)


def _update_event(event_id: str, *, status: str, error: str | None, delay_seconds: int) -> None:
    now = _utc_now()
    available = now + timedelta(seconds=delay_seconds)
    if _postgres():
        import psycopg

        with (
            psycopg.connect(os.environ["DATABASE_URL"]) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "UPDATE inbound_events SET status=%s, available_at=%s, locked_at=NULL, "
                "last_error=%s, updated_at=%s WHERE event_id=%s",
                (status, available, error, now, event_id),
            )
        return
    connection = sqlite3.connect(_sqlite_path())
    try:
        connection.execute(
            "UPDATE inbound_events SET status=?, available_at=?, locked_at=NULL, "
            "last_error=?, updated_at=? WHERE event_id=?",
            (status, available.isoformat(), error, now.isoformat(), event_id),
        )
        connection.commit()
    finally:
        connection.close()


def queue_stats() -> dict[str, int]:
    init_queue()
    if _postgres():
        import psycopg

        with (
            psycopg.connect(os.environ["DATABASE_URL"]) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute("SELECT status, COUNT(*) FROM inbound_events GROUP BY status")
            return {str(status): int(count) for status, count in cursor.fetchall()}
    connection = sqlite3.connect(_sqlite_path())
    try:
        rows = connection.execute(
            "SELECT status, COUNT(*) FROM inbound_events GROUP BY status"
        ).fetchall()
        return {str(status): int(count) for status, count in rows}
    finally:
        connection.close()
