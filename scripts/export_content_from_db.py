"""Write the database's content back out as the original files.

The mirror image of `import_content_to_db.py`, and the reason moving content into the database
is not a one-way door. It rebuilds `courses/<slug>/config.yaml` + `overview.md`,
`knowledge/*.md` and `knowledge/policies.yaml` from the content tables, so:

  * there is a plain-text, diffable, git-committable backup of everything the client has edited;
  * the bot can be put back on files (CONTENT_SOURCE=files) if the database is ever unavailable;
  * nobody is locked into this deployment to get their own content out.

Writes to `content_export/` by default rather than over the repo's own files, so a bad export
cannot destroy the originals. Point it at `.` deliberately when you mean to refresh them.

    DATABASE_URL='postgresql://...' python scripts/export_content_from_db.py
    DATABASE_URL='postgresql://...' python scripts/export_content_from_db.py --out .
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml
from dotenv import load_dotenv

load_dotenv()

from services import content_store as cs

# Column order that matches the hand-written config files, so a re-export produces a small,
# readable diff instead of reordering every key.
_CONFIG_ORDER = (
    "name",
    "slug",
    "active",
    "dates",
    "venue",
    "fees",
    "hrdc_deadline",
    "payment_deadline",
    "hot_budget_threshold",
    "keywords",
    "outreach",
)


def _loads(value, fallback):
    if value in (None, ""):
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _course_config(row: dict) -> dict:
    config = {
        "name": row.get("name") or row["slug"],
        "slug": row["slug"],
        "active": bool(row.get("active")),
        "dates": row.get("dates") or "",
        "venue": row.get("venue") or "",
        "fees": _loads(row.get("fees_json"), {}),
        "hrdc_deadline": row.get("hrdc_deadline") or "",
        "payment_deadline": row.get("payment_deadline") or "",
        "hot_budget_threshold": int(row.get("hot_budget_threshold") or 4000),
        "keywords": _loads(row.get("keywords_json"), []),
        "outreach": _loads(row.get("outreach_json"), {}),
    }
    return {key: config[key] for key in _CONFIG_ORDER if key in config}


def main() -> None:
    parser = argparse.ArgumentParser(description="Export DB content back to files")
    parser.add_argument(
        "--out",
        default="content_export",
        help="Directory to write into (default: content_export). Use '.' to refresh the repo.",
    )
    args = parser.parse_args()

    out = Path(args.out).resolve()
    print(f"Source DB : {'postgresql' if os.getenv('DATABASE_URL') else 'sqlite'}")
    print(f"Writing to: {out}\n")

    courses = cs.list_course_rows()
    knowledge = cs.get_all_knowledge()
    policies = cs.get_policies() or {}
    if not courses and not knowledge:
        print("The database holds no content — nothing to export.", file=sys.stderr)
        raise SystemExit(1)

    print(f"Courses   : {len(courses)}")
    for row in courses:
        slug = row["slug"]
        directory = out / "courses" / slug
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "config.yaml").write_text(
            yaml.safe_dump(_course_config(row), sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        (directory / "overview.md").write_text(row.get("overview") or "", encoding="utf-8")
        print(f"  - {slug} ({'active' if row.get('active') else 'archived'})")

    print(f"\nKnowledge : {len(knowledge)} topic(s)")
    knowledge_dir = out / "knowledge"
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    for topic, body in sorted(knowledge.items()):
        (knowledge_dir / f"{topic}.md").write_text(body, encoding="utf-8")
        print(f"  - {topic}")

    if policies:
        (knowledge_dir / "policies.yaml").write_text(
            yaml.safe_dump(policies, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        print(f"\nPolicies  : {len(policies)} section(s): {', '.join(sorted(policies))}")

    print(f"\nDone. {out} now mirrors the database.")


if __name__ == "__main__":
    main()
