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
    delete_setting,
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


def get_int(key: str, default: int) -> int:
    try:
        return int((get_setting(key, str(default)) or "").strip() or default)
    except ValueError:
        return default


def get_float(key: str, default: float) -> float:
    try:
        return float((get_setting(key, str(default)) or "").strip() or default)
    except ValueError:
        return default


# --- what the controls page may change ---------------------------------------
#
# Only settings that are re-read on every use belong here; anything read once at import or at
# startup would appear to change while doing nothing, which is worse than not offering it.
# Each entry carries the wording shown to a non-technical admin, so the page explains the
# consequence of a switch rather than just naming the variable.

EDITABLE_SETTINGS: tuple[dict, ...] = (
    {
        "key": "BOT_SAFE_MODE",
        "label": "Safe mode",
        "type": "bool",
        "default": "true",
        "help": (
            "On: the bot works out its reply and records it, but sends nothing to WhatsApp. "
            "Turn this off only when you are ready for customers to receive real messages."
        ),
    },
    {
        "key": "AUTO_OUTREACH_ENABLED",
        "label": "Message new leads automatically",
        "type": "bool",
        "default": "false",
        "help": (
            "On: new leads from your ads are sent the first WhatsApp message without anyone "
            "pressing anything. Off: leads are still collected, but nobody is contacted."
        ),
    },
    {
        "key": "AUTO_OUTREACH_HOURS",
        "label": "Sending hours",
        "type": "hours",
        "default": "9-19",
        "help": (
            "Local hours between which first messages may be sent, as start-end on a 24-hour "
            "clock. A cold message at 3am reads as spam and gets the number reported."
        ),
    },
    {
        "key": "AUTO_OUTREACH_TZ",
        "label": "Time zone for sending hours",
        "type": "text",
        "default": "Asia/Kuala_Lumpur",
        "help": "The time zone those hours are measured in.",
    },
    {
        "key": "AUTO_OUTREACH_MAX_PER_RUN",
        "label": "Most messages per run",
        "type": "int",
        "min": 0,
        "max": 500,
        "default": "25",
        "help": (
            "A ceiling on how many first messages one run may send. Keeps a bad import or a "
            "duplicated ad from messaging hundreds of people at once."
        ),
    },
    {
        "key": "AUTO_OUTREACH_DELAY_SECONDS",
        "label": "Pause between messages",
        "type": "int",
        "min": 0,
        "max": 120,
        "default": "3",
        "help": "Seconds to wait between first messages, so sending looks human rather than bulk.",
    },
    {
        "key": "LEAD_SYNC_CONTACT_CUTOFF",
        "label": "Never contact leads created before",
        "type": "datetime",
        "default": "",
        "help": (
            "Leads that arrived before this moment are never messaged automatically — they were "
            "handled by hand. Written as 2026-08-01T00:00:00+08:00. Leave empty for no cutoff."
        ),
    },
    {
        "key": "REPLY_DELAY_SECONDS",
        "label": "Pause before replying",
        "type": "float",
        "min": 0,
        "max": 30,
        "default": "2",
        "help": "Seconds the bot waits before answering, so replies do not feel instant.",
    },
    {
        "key": "OCR_PAGE_CAP",
        "label": "Most pages to read with OCR",
        "type": "int",
        "min": 0,
        "max": 300,
        "default": "40",
        "help": (
            "How many scanned pages of one document may be read by OCR. Reading a scan is slow, "
            "so this bounds the work a single upload can cause. 0 turns OCR off."
        ),
    },
)

EDITABLE_KEYS = frozenset(item["key"] for item in EDITABLE_SETTINGS)

# Shown on the controls page but not changeable there: each is read once, at import or when the
# background thread is scheduled, so writing it at runtime would look like it worked and do
# nothing. Secrets are reported as configured/not configured and never displayed.
RESTART_REQUIRED_SETTINGS: tuple[dict, ...] = (
    {"key": "CONTENT_SOURCE", "label": "Content source", "secret": False},
    {"key": "RAG_STORE", "label": "Search index storage", "secret": False},
    {"key": "LEAD_SYNC_INTERVAL_MINUTES", "label": "Minutes between lead checks", "secret": False},
    {"key": "ENABLE_GOOGLE_SHEETS", "label": "Read leads from Google Sheets", "secret": False},
    {"key": "RAG_V2_MIN_SCORE", "label": "Answer confidence threshold", "secret": False},
    {"key": "VERIFY_TOKEN", "label": "WhatsApp webhook verify token", "secret": True},
    {"key": "WHATSAPP_ACCESS_TOKEN", "label": "WhatsApp access token", "secret": True},
    {"key": "DATABASE_URL", "label": "Database", "secret": True},
)


class SettingError(ValueError):
    """A proposed setting value would not work."""


def validate_setting(key: str, value: str) -> str:
    """Check a value before it is stored, returning the normalised form.

    These settings drive money and messaging — an unparseable cutoff silently means "no cutoff",
    which would let the bot message people who were promised it wouldn't. So a bad value is
    refused at the point of entry rather than falling back to a default later.
    """
    spec = next((item for item in EDITABLE_SETTINGS if item["key"] == key), None)
    if spec is None:
        raise SettingError(f"{key} is not an editable setting")
    raw = (value or "").strip()
    kind = spec["type"]

    if kind == "bool":
        if raw.lower() not in _TRUE | {"0", "false", "no", "off"}:
            raise SettingError(f"{spec['label']} must be true or false")
        return "true" if raw.lower() in _TRUE else "false"

    if kind in {"int", "float"}:
        try:
            number = float(raw) if kind == "float" else int(raw)
        except ValueError as error:
            raise SettingError(f"{spec['label']} must be a number") from error
        if "min" in spec and number < spec["min"]:
            raise SettingError(f"{spec['label']} cannot be below {spec['min']}")
        if "max" in spec and number > spec["max"]:
            raise SettingError(f"{spec['label']} cannot be above {spec['max']}")
        return str(number)

    if kind == "hours":
        parts = raw.split("-", 1)
        try:
            start, end = int(parts[0]), int(parts[1])
        except (ValueError, IndexError) as error:
            raise SettingError(
                f"{spec['label']} must look like 9-19 (start and end hour)"
            ) from error
        if not (0 <= start <= 23 and 0 <= end <= 24 and start < end):
            raise SettingError(
                f"{spec['label']} must be two hours between 0 and 24, with the start earlier"
            )
        return f"{start}-{end}"

    if kind == "datetime":
        if not raw:
            return ""
        from datetime import datetime

        try:
            datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as error:
            raise SettingError(
                f"{spec['label']} must be a date and time like 2026-08-01T00:00:00+08:00"
            ) from error
        return raw

    if key == "AUTO_OUTREACH_TZ":
        from zoneinfo import ZoneInfo

        try:
            ZoneInfo(raw)
        except Exception as error:  # noqa: BLE001 - any unknown zone is a user error
            raise SettingError(f"{raw!r} is not a known time zone") from error
    return raw
