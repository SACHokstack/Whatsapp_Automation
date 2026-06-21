from __future__ import annotations

import argparse
import json

from dotenv import load_dotenv

load_dotenv()

from services.outreach import run_outreach


def main() -> None:
    parser = argparse.ArgumentParser(description="Run WhatsApp outreach from Google Sheets.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    result = run_outreach(limit=args.limit, dry_run=args.dry_run)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
