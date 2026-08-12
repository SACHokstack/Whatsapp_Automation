from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise RuntimeError("DATABASE_URL is required for the PostgreSQL backend")
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


_initialized_url = ""
_init_lock = threading.Lock()


@contextmanager
def get_connection():
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(_database_url(), row_factory=dict_row) as connection:
        yield connection


_LEAD_FIELDS = (
    "status",
    "conversation_state",
    "qualification_step",
    "last_intent",
    "last_intent_reason",
    "needs_human",
    "human_reason",
    "human_status",
    "human_updated_at",
    "last_message",
    "last_reply",
    "assigned_to",
    "name",
    "course",
    "occupation",
    "experience",
    "budget",
    "availability",
    "lead_score",
    "job_title",
    "company_name",
    "who_will_pay",
    "email",
    "experience_years",
    "technologies",
    "motivation",
    "learning_goals",
    "funding_path",
)


def init_db() -> None:
    global _initialized_url
    database_url = _database_url()
    if _initialized_url == database_url:
        return
    with _init_lock:
        if _initialized_url == database_url:
            return
        _initialize_schema()
        _initialized_url = database_url


def _initialize_schema() -> None:
    columns = ",\n".join(
        f"{field} {'INTEGER' if field == 'lead_score' else 'TEXT'}" for field in _LEAD_FIELDS
    )
    with get_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            f"""
            CREATE TABLE IF NOT EXISTS leads (
                phone TEXT PRIMARY KEY,
                {columns},
                updated_at TIMESTAMPTZ NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id BIGSERIAL PRIMARY KEY,
                phone TEXT NOT NULL,
                direction TEXT NOT NULL,
                body TEXT NOT NULL,
                message_id TEXT,
                created_at TIMESTAMPTZ NOT NULL
            )
            """
        )
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS messages_message_id_uq "
            "ON messages(message_id) WHERE message_id IS NOT NULL"
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS messages_phone_id_idx ON messages(phone, id)")


def upsert_lead(phone: str, **kwargs) -> bool:
    phone = str(phone).strip()
    if not phone:
        return False
    values = {field: kwargs.get(field) for field in _LEAD_FIELDS if kwargs.get(field) is not None}
    columns = ["phone", *values, "updated_at"]
    parameters = [phone, *values.values(), _utc_now()]
    assignments = ", ".join(f"{field} = EXCLUDED.{field}" for field in values)
    assignments = (assignments + ", " if assignments else "") + "updated_at = EXCLUDED.updated_at"
    placeholders = ", ".join(["%s"] * len(columns))
    with get_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO leads ({', '.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT(phone) DO UPDATE SET {assignments}",
            parameters,
        )
    return True


def get_lead(phone: str) -> dict[str, str] | None:
    init_db()
    with get_connection() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT * FROM leads WHERE phone = %s", (str(phone).strip(),))
        row = cursor.fetchone()
    if row is None:
        return None
    return {key: "" if value is None else str(value) for key, value in row.items()}


def list_leads(
    *,
    course: str | None = None,
    statuses: tuple[str, ...] | None = None,
    limit: int | None = None,
) -> list[dict[str, str]]:
    """Leads filtered by course and status. A blank/NULL status matches when '' is in statuses."""
    init_db()
    clauses: list[str] = []
    params: list[object] = []
    if course:
        clauses.append("course = %s")
        params.append(course)
    if statuses is not None:
        clauses.append("UPPER(COALESCE(status, '')) = ANY(%s)")
        params.append([s.upper() for s in statuses])

    query = "SELECT * FROM leads"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY updated_at ASC"
    if limit is not None and limit > 0:
        query += " LIMIT %s"
        params.append(limit)

    with get_connection() as connection, connection.cursor() as cursor:
        cursor.execute(query, params)
        rows = cursor.fetchall()
    return [
        {key: "" if value is None else str(value) for key, value in row.items()} for row in rows
    ]


def add_message(phone: str, *, direction: str, body: str, message_id: str | None = None) -> bool:
    phone, body = str(phone).strip(), str(body).strip()
    if not phone or not body:
        return False
    init_db()
    with get_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO messages (phone, direction, body, message_id, created_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (message_id) WHERE message_id IS NOT NULL DO NOTHING
            """,
            (phone, direction, body, message_id, _utc_now()),
        )
        return cursor.rowcount > 0


def get_messages(phone: str, *, limit: int | None = None) -> list[dict[str, str]]:
    init_db()
    query = "SELECT phone, direction, body, message_id, created_at FROM messages WHERE phone = %s ORDER BY id ASC"
    params: tuple[object, ...] = (str(phone).strip(),)
    if limit is not None and limit > 0:
        query = """
            SELECT phone, direction, body, message_id, created_at FROM (
                SELECT id, phone, direction, body, message_id, created_at
                FROM messages WHERE phone = %s ORDER BY id DESC LIMIT %s
            ) recent ORDER BY id ASC
        """
        params = (str(phone).strip(), limit)
    with get_connection() as connection, connection.cursor() as cursor:
        cursor.execute(query, params)
        rows = cursor.fetchall()
    return [
        {key: "" if value is None else str(value) for key, value in row.items()} for row in rows
    ]


def get_conversation_history(phone: str, *, limit: int | None = None) -> list[dict[str, str]]:
    return get_messages(phone, limit=limit)


def get_dashboard_summary() -> dict[str, object]:
    init_db()
    with get_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT COALESCE(status, 'UNKNOWN') AS status, COUNT(*) AS count "
            "FROM leads GROUP BY COALESCE(status, 'UNKNOWN') ORDER BY count DESC, status"
        )
        lead_rows = cursor.fetchall()
        cursor.execute("SELECT COUNT(*) AS count FROM leads")
        total_leads = cursor.fetchone()["count"]
        cursor.execute("SELECT COUNT(*) AS count FROM messages")
        total_messages = cursor.fetchone()["count"]
    return {
        "total_leads": int(total_leads or 0),
        "total_messages": int(total_messages or 0),
        "leads_by_status": [
            {"status": str(row["status"]), "count": int(row["count"])} for row in lead_rows
        ],
    }
