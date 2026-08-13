"""One-time importer: copy course/knowledge/policy content from files into the database.

Reads the repo files through the EXISTING file loaders (forced into files mode) and writes each
loaded object straight into the content tables. Importing the already-parsed objects — keywords
lowercased, overview notes stripped — guarantees that reading back in DB mode is byte-identical
to reading the files, which is what the parity tests assert.

Idempotent (UPSERT on primary key), so it is safe to re-run. Writes to whichever database
`DATABASE_URL` selects (Postgres on Railway, SQLite locally).

    python scripts/import_content_to_db.py            # import everything
    python scripts/import_content_to_db.py --dry-run  # show what would be written
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv()

# Read the source through the loaders in FILES mode regardless of the ambient setting.
os.environ["CONTENT_SOURCE"] = "files"

from services import content_store as cs
from services.course_loader import load_courses
from services.knowledge_base import load_knowledge_base
from services.structured_facts import load_policies


def main() -> None:
    parser = argparse.ArgumentParser(description="Import file content into the content tables")
    parser.add_argument("--dry-run", action="store_true", help="Report without writing")
    args = parser.parse_args()

    backend = cs.backend_name()
    print(f"Target DB : {backend}")
    print(f"Mode      : {'DRY RUN' if args.dry_run else 'LIVE'}\n")

    courses = load_courses()
    knowledge = load_knowledge_base()
    policies = load_policies()

    print(f"Courses   : {len(courses)}")
    for slug, course in sorted(courses.items()):
        flag = "active" if course.active else "archived"
        print(f"  - {slug} ({flag})")
        if not args.dry_run:
            cs.upsert_course(
                slug,
                {
                    "name": course.name,
                    "active": course.active,
                    "dates": course.dates,
                    "venue": course.venue,
                    "fees": course.fees,
                    "hrdc_deadline": course.hrdc_deadline,
                    "payment_deadline": course.payment_deadline,
                    "hot_budget_threshold": course.hot_budget_threshold,
                    "keywords": course.keywords,
                    "overview": course.overview,
                    "outreach": course.outreach,
                },
            )

    print(f"\nKnowledge : {len(knowledge)} topic(s)")
    for topic in sorted(knowledge):
        print(f"  - {topic}")
        if not args.dry_run:
            cs.upsert_knowledge(topic, knowledge[topic])

    print(f"\nPolicies  : {len(policies)} section(s): {', '.join(sorted(policies))}")
    if not args.dry_run and policies:
        cs.set_policies(policies)

    if not args.dry_run:
        version = cs.bump_content_version()
        print(
            f"\nDone. content_version = {version}. Set CONTENT_SOURCE=db to read from the database."
        )
    else:
        print("\nDry run complete. Nothing written.")


if __name__ == "__main__":
    if not sys.argv[0]:
        pass
    main()
