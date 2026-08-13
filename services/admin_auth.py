"""Single shared-password admin auth for the dashboard.

The session is a stateless HMAC-signed cookie: `expiry.signature`, verified against a secret that
is auto-generated once and persisted in `app_settings` (so logins survive restarts without the
operator setting an env var, and there is no server-side session store to share across workers).
The password itself is stored hashed in `admin_users` (see `content_store`), seeded once from the
`ADMIN_PASSWORD` env var and changeable from the UI thereafter.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time

from services import content_store as cs

logger = logging.getLogger(__name__)

COOKIE_NAME = "admin_session"
SESSION_TTL_SECONDS = 7 * 24 * 3600  # a week; re-login is cheap


def bootstrap_admin_password() -> None:
    """Seed the admin password from ADMIN_PASSWORD if none is set yet. Called at startup."""
    try:
        if cs.admin_password_is_set():
            return
        initial = os.getenv("ADMIN_PASSWORD", "").strip()
        if initial:
            cs.set_admin_password(initial)
            logger.info("event=admin_password_seeded")
    except Exception:
        logger.exception("event=admin_password_bootstrap_failed")


def _session_secret() -> bytes:
    secret = cs.get_setting("session_secret", None)
    if not secret:
        secret = secrets.token_hex(32)
        cs.set_setting("session_secret", secret)
    return secret.encode("utf-8")


def _sign(payload: str) -> str:
    return hmac.new(_session_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def create_session_token() -> str:
    expiry = str(int(time.time()) + SESSION_TTL_SECONDS)
    return f"{expiry}.{_sign(expiry)}"


def verify_session_token(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    expiry, _, signature = token.partition(".")
    if not hmac.compare_digest(signature, _sign(expiry)):
        return False
    try:
        return int(expiry) > int(time.time())
    except (TypeError, ValueError):
        return False


def is_authenticated(request) -> bool:
    return verify_session_token(request.cookies.get(COOKIE_NAME))


def check_password(password: str) -> bool:
    return cs.verify_admin_password(password or "")
