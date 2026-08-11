from __future__ import annotations

import logging
import os

import requests

session = requests.Session()
logger = logging.getLogger(__name__)


def _api_version() -> str:
    return os.getenv("WHATSAPP_API_VERSION", "v25.0").strip() or "v25.0"


def _messages_url() -> str:
    return f"https://graph.facebook.com/{_api_version()}/{_phone_number_id()}/messages"


# Simulate human typing: wait this many seconds before sending a reply.
# Configurable via REPLY_DELAY_SECONDS env var (default 2).
def _reply_delay() -> float:
    try:
        return min(max(float(os.getenv("REPLY_DELAY_SECONDS", "2")), 0.0), 30.0)
    except ValueError:
        return 2.0


def mark_read(message_id: str) -> None:
    """Send read receipt so the lead sees blue ticks immediately."""
    access_token = _access_token()
    try:
        session.post(
            _messages_url(),
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json={
                "messaging_product": "whatsapp",
                "status": "read",
                "message_id": message_id,
            },
            timeout=10,
        )
    except requests.RequestException:
        logger.exception("event=whatsapp_mark_read_failed")


def _phone_number_id() -> str:
    return (
        os.getenv("PHONE_NUMBER_ID")
        or os.getenv("WHATSAPP_PHONE_NUMBER_ID")
        or os.getenv("META_PHONE_NUMBER_ID", "")
    )


def _access_token() -> str:
    return (
        os.getenv("ACCESS_TOKEN")
        or os.getenv("WHATSAPP_TOKEN")
        or os.getenv("WHATSAPP_ACCESS_TOKEN")
        or os.getenv("META_ACCESS_TOKEN", "")
    )


def send_text(to: str, body: str) -> requests.Response:
    access_token = _access_token()
    response = session.post(
        _messages_url(),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json={
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": body},
        },
        timeout=30,
    )
    return response


def send_template(
    to: str,
    template_name: str,
    language_code: str = "en_US",
    variables: list[str] | None = None,
    named_variables: dict[str, str] | None = None,
) -> requests.Response:
    access_token = _access_token()
    components = []
    if named_variables:
        components.append(
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "parameter_name": k, "text": v}
                    for k, v in named_variables.items()
                ],
            }
        )
    elif variables:
        components.append(
            {
                "type": "body",
                "parameters": [{"type": "text", "text": value} for value in variables],
            }
        )
    response = session.post(
        _messages_url(),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json={
            "messaging_product": "whatsapp",
            "to": to,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language_code},
                **({"components": components} if components else {}),
            },
        },
        timeout=30,
    )
    logger.info(
        "event=whatsapp_template_sent status_code=%s ok=%s", response.status_code, response.ok
    )
    return response


def send_template_and_mark_contacted(
    to: str,
    template_name: str,
    *,
    language_code: str = "en_US",
) -> requests.Response:
    response = send_template(to, template_name, language_code=language_code)
    if response.ok:
        try:
            from services.google_sheets import update_lead
            from services.persistence import add_message, upsert_lead

            update_lead(to, status="CONTACTED", last_reply=template_name)
            upsert_lead(to, status="CONTACTED", last_reply=template_name)
            add_message(
                to,
                direction="outbound",
                body=template_name,
                message_id=response.json().get("messages", [{}])[0].get("id")
                if response.headers.get("content-type", "").startswith("application/json")
                else None,
            )
        except (ValueError, KeyError, requests.RequestException):
            logger.exception("event=whatsapp_contact_state_update_failed")
    return response
