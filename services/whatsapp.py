from __future__ import annotations

from typing import Iterable

import requests


def _config() -> tuple[str, str, str]:
    import os

    token = os.getenv("WHATSAPP_ACCESS_TOKEN") or os.getenv("META_ACCESS_TOKEN", "")
    phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID") or os.getenv("META_PHONE_NUMBER_ID", "")
    api_version = os.getenv("WHATSAPP_API_VERSION") or os.getenv("META_API_VERSION", "v25.0")
    return token, phone_number_id, api_version


def _require_config() -> tuple[str, str, str]:
    token, phone_number_id, api_version = _config()
    if not token:
        raise RuntimeError("WHATSAPP_ACCESS_TOKEN or META_ACCESS_TOKEN is required")
    if not phone_number_id:
        raise RuntimeError("WHATSAPP_PHONE_NUMBER_ID or META_PHONE_NUMBER_ID is required")
    return token, phone_number_id, api_version


def send_message(phone: str, text: str) -> requests.Response:
    token, phone_number_id, api_version = _require_config()
    url = f"https://graph.facebook.com/{api_version}/{phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {
            "body": text,
        },
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    return requests.post(url, json=payload, headers=headers, timeout=30)


def send_template_message(
    phone: str,
    template_name: str,
    language: str = "en_US",
    variables: Iterable[str] | None = None,
) -> requests.Response:
    token, phone_number_id, api_version = _require_config()
    url = f"https://graph.facebook.com/{api_version}/{phone_number_id}/messages"
    components = []
    variable_list = list(variables or [])
    if variable_list:
        components.append(
            {
                "type": "body",
                "parameters": [{"type": "text", "text": value} for value in variable_list],
            }
        )

    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language},
        },
    }
    if components:
        payload["template"]["components"] = components

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    return requests.post(url, json=payload, headers=headers, timeout=30)
