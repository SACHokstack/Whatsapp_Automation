from __future__ import annotations

import os


def backend_name() -> str:
    url = os.getenv("DATABASE_URL", "").strip().lower()
    return "postgresql" if url.startswith(("postgres://", "postgresql://")) else "sqlite"


if backend_name() == "postgresql":
    from services.postgres_store import (  # noqa: F401
        add_message,
        get_conversation_history,
        get_dashboard_summary,
        get_lead,
        get_messages,
        init_db,
        upsert_lead,
    )
else:
    from services.sqlite_store import (  # noqa: F401
        add_message,
        get_conversation_history,
        get_dashboard_summary,
        get_lead,
        get_messages,
        init_db,
        upsert_lead,
    )
