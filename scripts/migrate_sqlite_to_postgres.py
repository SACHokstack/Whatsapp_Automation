"""Copy local leads and messages into the configured production PostgreSQL database."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services import postgres_store  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite", default="whatsapp_bot.db")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    source = Path(args.sqlite)
    if not source.exists():
        print(f"SQLite database not found: {source}")
        return 1
    if not os.getenv("DATABASE_URL", "").startswith(("postgres://", "postgresql://")):
        print("DATABASE_URL must point to PostgreSQL")
        return 1

    connection = sqlite3.connect(source)
    connection.row_factory = sqlite3.Row
    leads = connection.execute("SELECT * FROM leads ORDER BY phone").fetchall()
    messages = connection.execute("SELECT * FROM messages ORDER BY id").fetchall()
    print(f"Leads: {len(leads)} | Messages: {len(messages)}")
    if args.dry_run:
        return 0

    postgres_store.init_db()
    for row in leads:
        data = dict(row)
        phone = str(data.pop("phone"))
        data.pop("updated_at", None)
        postgres_store.upsert_lead(phone, **data)
    for row in messages:
        data = dict(row)
        postgres_store.add_message(
            str(data["phone"]),
            direction=str(data["direction"]),
            body=str(data["body"]),
            message_id=data.get("message_id"),
        )
    print("Migration complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
