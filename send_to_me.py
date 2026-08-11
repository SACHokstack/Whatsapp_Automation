from __future__ import annotations

import json
import os
from pathlib import Path

import requests


def load_env(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


if __name__ == "__main__":
    load_env()

    to = "919894052658"
    template_name = "timmins_software_testing_intro"
    language_code = "en"
    variables = ["KCM"]

    phone_number_id = (
        os.getenv("PHONE_NUMBER_ID")
        or os.getenv("WHATSAPP_PHONE_NUMBER_ID")
        or os.getenv("META_PHONE_NUMBER_ID", "")
    )
    access_token = (
        os.getenv("ACCESS_TOKEN")
        or os.getenv("WHATSAPP_TOKEN")
        or os.getenv("WHATSAPP_ACCESS_TOKEN")
        or os.getenv("META_ACCESS_TOKEN", "")
    )

    components = []
    if variables:
        components.append(
            {
                "type": "body",
                "parameters": [{"type": "text", "text": value} for value in variables],
            }
        )

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language_code},
            **({"components": components} if components else {}),
        },
    }

    print(json.dumps(payload, indent=2))

    response = requests.post(
        f"https://graph.facebook.com/v25.0/{phone_number_id}/messages",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )

    print(response.status_code)
    print(response.text)
