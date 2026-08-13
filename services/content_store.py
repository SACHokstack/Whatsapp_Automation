"""Database-backed store for editable content and runtime settings.

The bot's course facts, company knowledge, and policies used to live in files
(`courses/<slug>/config.yaml` + `overview.md`, `knowledge/*.md`, `knowledge/policies.yaml`)
and be read at runtime. This module holds the same content as Postgres/SQLite rows so a
non-technical admin can edit it from the dashboard without a redeploy.

It is backend-aware: it reuses whichever store `services.persistence` selected (Postgres when
DATABASE_URL is set, SQLite otherwise) so there is exactly one database, and it mirrors the
existing "CREATE TABLE IF NOT EXISTS + idempotent init" pattern. JSON-shaped fields (fees,
keywords, outreach, the policies blob) are stored as TEXT in both backends — we always load a
whole row, never query inside the JSON, so JSONB buys nothing and TEXT keeps the two backends
byte-identical for the files-vs-db parity tests.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
from datetime import datetime, timezone

from services.persistence import backend_name

_schema_lock = threading.Lock()
_schema_ready = False


def _is_postgres() -> bool:
    return backend_name() == "postgresql"


def _get_connection():
    if _is_postgres():
        from services.postgres_store import get_connection
    else:
        from services.sqlite_store import get_connection
    return get_connection()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _q(sql: str) -> str:
    """Translate the `?` placeholders used here to `%s` for psycopg."""
    return sql.replace("?", "%s") if _is_postgres() else sql


def _run(conn, sql: str, params: tuple = (), *, fetch: str | None = None):
    """Execute a statement against either backend and optionally fetch rows as dicts."""
    query = _q(sql)
    if _is_postgres():
        with conn.cursor() as cur:
            cur.execute(query, params)
            if fetch == "one":
                row = cur.fetchone()
                return dict(row) if row else None
            if fetch == "all":
                return [dict(r) for r in cur.fetchall()]
            return None
    cur = conn.execute(query, params)
    if fetch == "one":
        row = cur.fetchone()
        return dict(row) if row else None
    if fetch == "all":
        return [dict(r) for r in cur.fetchall()]
    return None


# --- Schema -----------------------------------------------------------------


def _ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        _create_schema()
        _schema_ready = True


def _create_schema() -> None:
    postgres = _is_postgres()
    ts = "TIMESTAMPTZ" if postgres else "TEXT"
    autoincrement_id = "BIGSERIAL PRIMARY KEY" if postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
    bool_type = "BOOLEAN" if postgres else "INTEGER"
    blob_type = "BYTEA" if postgres else "BLOB"

    statements = [
        f"""
        CREATE TABLE IF NOT EXISTS courses (
            slug TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            active {bool_type} NOT NULL DEFAULT {"true" if postgres else "1"},
            dates TEXT,
            venue TEXT,
            fees_json TEXT,
            hrdc_deadline TEXT,
            payment_deadline TEXT,
            hot_budget_threshold INTEGER DEFAULT 4000,
            keywords_json TEXT,
            overview TEXT NOT NULL DEFAULT '',
            outreach_json TEXT,
            archived_at {ts},
            created_at {ts} NOT NULL,
            updated_at {ts} NOT NULL
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS knowledge (
            topic TEXT PRIMARY KEY,
            body TEXT NOT NULL,
            updated_at {ts} NOT NULL
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS policies (
            id INTEGER PRIMARY KEY,
            data_json TEXT NOT NULL,
            updated_at {ts} NOT NULL
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS documents (
            id {autoincrement_id},
            course_slug TEXT NOT NULL,
            filename TEXT NOT NULL,
            content_type TEXT,
            raw_bytes {blob_type},
            extracted_text TEXT,
            extraction_method TEXT,
            ingest_status TEXT NOT NULL DEFAULT 'pending',
            ingest_error TEXT,
            chunk_count INTEGER DEFAULT 0,
            uploaded_at {ts} NOT NULL,
            updated_at {ts} NOT NULL
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at {ts} NOT NULL
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS admin_users (
            id INTEGER PRIMARY KEY,
            password_hash TEXT NOT NULL,
            updated_at {ts} NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS documents_course_idx ON documents(course_slug)",
        "CREATE INDEX IF NOT EXISTS documents_status_idx ON documents(ingest_status)",
    ]
    with _get_connection() as conn:
        for statement in statements:
            _run(conn, statement)


def _to_bool(value) -> bool:
    return value in (True, 1, "1", "true", "True", "t")


# --- app_settings -----------------------------------------------------------


def get_setting(key: str, default: str | None = None) -> str | None:
    _ensure_schema()
    with _get_connection() as conn:
        row = _run(conn, "SELECT value FROM app_settings WHERE key = ?", (key,), fetch="one")
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    _ensure_schema()
    now = _utc_now()
    with _get_connection() as conn:
        if _is_postgres():
            _run(
                conn,
                "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, "
                "updated_at = EXCLUDED.updated_at",
                (key, str(value), now),
            )
        else:
            _run(
                conn,
                "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "updated_at = excluded.updated_at",
                (key, str(value), now),
            )


def all_settings() -> dict[str, str]:
    _ensure_schema()
    with _get_connection() as conn:
        rows = _run(conn, "SELECT key, value FROM app_settings", fetch="all") or []
    return {r["key"]: r["value"] for r in rows}


def content_version() -> int:
    """Monotone counter bumped on every content write; used as the reader-cache key."""
    raw = get_setting("content_version", "0")
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


def bump_content_version() -> int:
    new_value = content_version() + 1
    set_setting("content_version", str(new_value))
    return new_value


# --- admin auth -------------------------------------------------------------

_PBKDF2_ROUNDS = 200_000


def _hash_password(password: str, salt: bytes) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def set_admin_password(password: str) -> None:
    _ensure_schema()
    encoded = _hash_password(password, secrets.token_bytes(16))
    now = _utc_now()
    with _get_connection() as conn:
        if _is_postgres():
            _run(
                conn,
                "INSERT INTO admin_users (id, password_hash, updated_at) VALUES (1, ?, ?) "
                "ON CONFLICT (id) DO UPDATE SET password_hash = EXCLUDED.password_hash, "
                "updated_at = EXCLUDED.updated_at",
                (encoded, now),
            )
        else:
            _run(
                conn,
                "INSERT INTO admin_users (id, password_hash, updated_at) VALUES (1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET password_hash = excluded.password_hash, "
                "updated_at = excluded.updated_at",
                (encoded, now),
            )


def admin_password_is_set() -> bool:
    _ensure_schema()
    with _get_connection() as conn:
        row = _run(conn, "SELECT password_hash FROM admin_users WHERE id = 1", fetch="one")
    return bool(row and row["password_hash"])


def verify_admin_password(password: str) -> bool:
    _ensure_schema()
    with _get_connection() as conn:
        row = _run(conn, "SELECT password_hash FROM admin_users WHERE id = 1", fetch="one")
    if not row or not row["password_hash"]:
        return False
    try:
        _scheme, rounds, salt_hex, expected_hex = row["password_hash"].split("$")
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(rounds)
        )
    except (ValueError, TypeError):
        return False
    return secrets.compare_digest(digest.hex(), expected_hex)


# --- courses ----------------------------------------------------------------


def upsert_course(slug: str, fields: dict) -> None:
    """Insert or update a course row. `fields` uses the DB column names; the *_json
    columns accept either a JSON string or a Python object (dumped here)."""
    _ensure_schema()
    now = _utc_now()

    def _json(value):
        if value is None or isinstance(value, str):
            return value
        return json.dumps(value)

    row = {
        "name": fields.get("name", slug),
        "active": bool(fields.get("active", True)),
        "dates": fields.get("dates"),
        "venue": fields.get("venue"),
        "fees_json": _json(fields.get("fees_json", fields.get("fees"))),
        "hrdc_deadline": fields.get("hrdc_deadline"),
        "payment_deadline": fields.get("payment_deadline"),
        "hot_budget_threshold": int(fields.get("hot_budget_threshold") or 4000),
        "keywords_json": _json(fields.get("keywords_json", fields.get("keywords"))),
        "overview": fields.get("overview") or "",
        "outreach_json": _json(fields.get("outreach_json", fields.get("outreach"))),
    }
    active_val = row["active"] if _is_postgres() else (1 if row["active"] else 0)
    columns = list(row.keys())
    params = tuple(active_val if c == "active" else row[c] for c in columns)

    with _get_connection() as conn:
        existing = _run(conn, "SELECT slug FROM courses WHERE slug = ?", (slug,), fetch="one")
        if existing:
            assignments = ", ".join(f"{c} = ?" for c in columns)
            _run(
                conn,
                f"UPDATE courses SET {assignments}, updated_at = ? WHERE slug = ?",
                (*params, now, slug),
            )
        else:
            col_list = ", ".join(["slug", *columns, "created_at", "updated_at"])
            placeholders = ", ".join(["?"] * (len(columns) + 3))
            _run(
                conn,
                f"INSERT INTO courses ({col_list}) VALUES ({placeholders})",
                (slug, *params, now, now),
            )


def get_course_row(slug: str) -> dict | None:
    _ensure_schema()
    with _get_connection() as conn:
        row = _run(conn, "SELECT * FROM courses WHERE slug = ?", (slug,), fetch="one")
    if row is not None:
        row["active"] = _to_bool(row.get("active"))
    return row


def list_course_rows(*, active_only: bool = False) -> list[dict]:
    _ensure_schema()
    with _get_connection() as conn:
        rows = _run(conn, "SELECT * FROM courses ORDER BY slug", fetch="all") or []
    for row in rows:
        row["active"] = _to_bool(row.get("active"))
    if active_only:
        rows = [r for r in rows if r["active"]]
    return rows


def set_course_active(slug: str, active: bool) -> None:
    _ensure_schema()
    now = _utc_now()
    active_val = active if _is_postgres() else (1 if active else 0)
    archived = None if active else now
    with _get_connection() as conn:
        _run(
            conn,
            "UPDATE courses SET active = ?, archived_at = ?, updated_at = ? WHERE slug = ?",
            (active_val, archived, now, slug),
        )


def delete_course(slug: str) -> None:
    _ensure_schema()
    with _get_connection() as conn:
        _run(conn, "DELETE FROM documents WHERE course_slug = ?", (slug,))
        _run(conn, "DELETE FROM courses WHERE slug = ?", (slug,))


# --- knowledge --------------------------------------------------------------


def upsert_knowledge(topic: str, body: str) -> None:
    _ensure_schema()
    now = _utc_now()
    with _get_connection() as conn:
        if _is_postgres():
            _run(
                conn,
                "INSERT INTO knowledge (topic, body, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT (topic) DO UPDATE SET body = EXCLUDED.body, "
                "updated_at = EXCLUDED.updated_at",
                (topic, body, now),
            )
        else:
            _run(
                conn,
                "INSERT INTO knowledge (topic, body, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(topic) DO UPDATE SET body = excluded.body, "
                "updated_at = excluded.updated_at",
                (topic, body, now),
            )


def get_all_knowledge() -> dict[str, str]:
    _ensure_schema()
    with _get_connection() as conn:
        rows = _run(conn, "SELECT topic, body FROM knowledge", fetch="all") or []
    return {r["topic"]: r["body"] for r in rows}


def delete_knowledge(topic: str) -> None:
    _ensure_schema()
    with _get_connection() as conn:
        _run(conn, "DELETE FROM knowledge WHERE topic = ?", (topic,))


# --- policies (single row) --------------------------------------------------


def set_policies(data: dict) -> None:
    _ensure_schema()
    now = _utc_now()
    payload = json.dumps(data)
    with _get_connection() as conn:
        if _is_postgres():
            _run(
                conn,
                "INSERT INTO policies (id, data_json, updated_at) VALUES (1, ?, ?) "
                "ON CONFLICT (id) DO UPDATE SET data_json = EXCLUDED.data_json, "
                "updated_at = EXCLUDED.updated_at",
                (payload, now),
            )
        else:
            _run(
                conn,
                "INSERT INTO policies (id, data_json, updated_at) VALUES (1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET data_json = excluded.data_json, "
                "updated_at = excluded.updated_at",
                (payload, now),
            )


def list_indexed_documents() -> list[dict]:
    """Uploaded course docs that finished ingestion, for rebuilding the index from the DB.

    Returns id, course_slug, filename, extracted_text. Used by the RAG boot path in DB mode so a
    full rebuild reconstructs the same course_kb chunks the dashboard ingested (stable content-hash
    chunk_ids), instead of pruning them."""
    _ensure_schema()
    with _get_connection() as conn:
        rows = (
            _run(
                conn,
                "SELECT id, course_slug, filename, extracted_text FROM documents "
                "WHERE ingest_status = 'indexed'",
                fetch="all",
            )
            or []
        )
    return rows


def get_policies() -> dict | None:
    _ensure_schema()
    with _get_connection() as conn:
        row = _run(conn, "SELECT data_json FROM policies WHERE id = 1", fetch="one")
    if not row:
        return None
    try:
        return json.loads(row["data_json"])
    except (ValueError, TypeError):
        return None
