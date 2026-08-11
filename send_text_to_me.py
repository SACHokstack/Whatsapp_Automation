from __future__ import annotations

import os
from pathlib import Path

from services.whatsapp import send_text


def load_env(path: Path = Path(".env")) -> None:
    if not path.exists():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue

        key, value = stripped.split("=", 1)
        os.environ.setdefault(
            key.strip(),
            value.strip().strip('"').strip("'"),
        )


if __name__ == "__main__":
    load_env()

    message = """Hi Sachiv,

You previously expressed interest in software testing opportunities.

We are reaching out to share information about our upcoming Software Testing intake and answer any questions you may have.

If you would like more details about the program, fees, schedule, or career opportunities, simply reply to this message.

Thank you,
Timmins
"""

    response = send_text(
        "919444209374",
        message,
    )

    print(response.status_code)
    print(response.text)