from __future__ import annotations

import os
from pathlib import Path

from services.whatsapp import send_template


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
    response = send_template(
        to="919444209374",
        template_name="timmins_software_testing_intro",
        language_code=os.getenv("TEMPLATE_LANGUAGE", "en"),
        variables=["Sachiv"],
    )
    print(response.status_code)
    print(response.text)
