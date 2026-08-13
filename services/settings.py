"""Runtime settings, resolved DB-first then environment then a default.

A value set from the dashboard (stored in `app_settings` via `content_store`) wins over the
matching environment variable, so an admin can flip behaviour — safe mode, auto-outreach, the
contact cutoff — without a redeploy. When nothing is stored, the existing `os.getenv` default
still applies, so behaviour is unchanged until someone actually changes a setting.

Some settings can't be made live this way and are intentionally NOT routed through here:
`LEAD_SYNC_INTERVAL_MINUTES` (read once to schedule the poller thread at startup) and
`VERIFY_TOKEN` / `ENABLE_GOOGLE_SHEETS` (read at import). Those stay env-only and are surfaced
read-only on the controls page with a "requires restart/redeploy" note.
"""

from __future__ import annotations

import os

from services.content_store import (  # noqa: F401  (re-exported as the settings API)
    all_settings,
    bump_content_version,
    content_version,
    set_setting,
)
from services.content_store import (
    get_setting as _db_setting,
)

_TRUE = {"1", "true", "yes", "on"}


def get_setting(key: str, default: str | None = None) -> str | None:
    """DB value if an admin set one, else the environment, else `default`."""
    try:
        stored = _db_setting(key, None)
    except Exception:
        stored = None
    if stored is not None:
        return stored
    env = os.getenv(key)
    return env if env is not None else default


def get_bool(key: str, default: bool) -> bool:
    value = get_setting(key, None)
    if value is None:
        return default
    return value.strip().lower() in _TRUE
