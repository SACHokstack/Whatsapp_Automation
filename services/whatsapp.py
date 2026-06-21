from __future__ import annotations

import os

import requests


session = requests.Session()


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
    phone_number_id = _phone_number_id()
    access_token = _access_token()
    response = session.post(
        f"https://graph.facebook.com/v25.0/{phone_number_id}/messages",
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
) -> requests.Response:
    phone_number_id = _phone_number_id()
    access_token = _access_token()
    components = []
    if variables:
        components.append(
            {
                "type": "body",
                "parameters": [{"type": "text", "text": value} for value in variables],
            }
        )
    response = session.post(
        f"https://graph.facebook.com/v25.0/{phone_number_id}/messages",
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

            update_lead(to, status="CONTACTED", last_message=template_name)
        except Exception:
            pass
    return response
