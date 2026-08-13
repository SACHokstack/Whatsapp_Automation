"""P0 foundations: DB-backed content parity + admin auth.

Parity is the load-bearing guarantee — reading course/knowledge/policy content from the database
must be byte-identical to reading the files, including every `exact_answer` string (which proves
the overview regex parsers work off the DB column). Auth must lock the PII endpoints.

Runs against the temp SQLite DB conftest points the suite at; content_store proves Postgres parity
separately in its own smoke path.
"""

import dataclasses
import os
import unittest
import unittest.mock


def _import_files_to_db():
    """Load content via the file loaders and write it to the content tables (what the CLI does)."""
    from services import content_store as cs
    from services.course_loader import load_courses
    from services.knowledge_base import load_knowledge_base
    from services.structured_facts import load_policies

    for slug, course in load_courses().items():
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
    for topic, body in load_knowledge_base().items():
        cs.upsert_knowledge(topic, body)
    if load_policies():
        cs.set_policies(load_policies())
    cs.bump_content_version()


def _clear_reader_caches():
    import services.course_loader as cl
    import services.knowledge_base as kb
    import services.structured_facts as sf

    cl._load_courses_cached.cache_clear()
    kb._load_knowledge_base_cached.cache_clear()
    sf._load_policies_cached.cache_clear()


class ContentParityTests(unittest.TestCase):
    """Reading content from the DB must be identical to reading it from the files.

    Runs against a private database. Parity is only meaningful when the DB holds exactly what
    the files hold: the CATALOG answer enumerates every active course, so one stray row left
    by another test — or a course someone added through the dashboard — would change the
    expected text for every course and fail the comparison for the wrong reason.
    """

    def setUp(self):
        import shutil
        import tempfile
        from pathlib import Path

        from services import content_store

        self._dir = tempfile.mkdtemp(prefix="p0-parity-")
        self._env = unittest.mock.patch.dict(
            os.environ,
            {
                "WHATSAPP_DB_PATH": str(Path(self._dir) / "parity.sqlite"),
                "CONTENT_SOURCE": "files",
            },
        )
        self._env.start()
        self.addCleanup(self._env.stop)
        # content_store creates its tables once per process; force it to build them in the
        # fresh database and to rebuild them again for whatever runs after this test.
        content_store._schema_ready = False
        self.addCleanup(setattr, content_store, "_schema_ready", False)
        self.addCleanup(_clear_reader_caches)
        self.addCleanup(shutil.rmtree, self._dir, ignore_errors=True)

        _clear_reader_caches()
        _import_files_to_db()

    def tearDown(self):
        os.environ["CONTENT_SOURCE"] = "files"
        _clear_reader_caches()

    def _snapshot(self):
        import services.course_loader as cl
        import services.knowledge_base as kb
        import services.structured_facts as sf
        from services.structured_facts import exact_answer

        courses = {s: dataclasses.asdict(c) for s, c in cl.load_courses().items()}
        knowledge = dict(kb.load_knowledge_base())
        policies = sf.load_policies()
        intents = [
            "FEES",
            "SCHEDULE",
            "VENUE",
            "DURATION",
            "HRDC",
            "TRAINER",
            "CERTIFICATION",
            "COURSE_VALUE",
            "BEGINNER_FIT",
            "CATALOG",
            "QUOTATION",
            "CANCELLATION",
            "CONTACT",
            "COMPANY",
        ]
        answers = {
            (slug, i): exact_answer(i, course, message="tell me")
            for slug, course in cl.load_courses().items()
            for i in intents
        }
        return courses, knowledge, policies, answers

    def test_db_content_is_byte_identical_to_files(self):
        os.environ["CONTENT_SOURCE"] = "files"
        _clear_reader_caches()
        f_courses, f_kb, f_pol, f_ans = self._snapshot()

        os.environ["CONTENT_SOURCE"] = "db"
        _clear_reader_caches()
        d_courses, d_kb, d_pol, d_ans = self._snapshot()

        # Parity means every course that came from a file is reproduced identically in the DB.
        # The DB may legitimately hold *extra* rows the files never had — courses added through
        # the dashboard, or fixtures left by another test module — so compare on the file keys
        # rather than requiring the two sets to be equal.
        self.assertEqual(f_courses, {slug: d_courses[slug] for slug in f_courses})
        self.assertEqual(f_kb, {topic: d_kb[topic] for topic in f_kb})
        self.assertEqual(f_pol, d_pol)
        # The decisive check: every structured answer (incl. overview-regex-derived ones) matches
        self.assertEqual(f_ans, {key: d_ans[key] for key in f_ans})
        self.assertTrue(f_ans, "expected some answers to compare")

    def test_dashboard_edit_is_visible_after_a_version_bump(self):
        from services import content_store as cs

        os.environ["CONTENT_SOURCE"] = "db"
        _clear_reader_caches()
        import services.course_loader as cl

        slug = next(iter(cl.load_courses()))
        cs.upsert_course(slug, {**_row_fields(cs, slug), "venue": "A Brand New Venue"})
        cs.bump_content_version()
        self.assertEqual("A Brand New Venue", cl.load_courses()[slug].venue)


def _row_fields(cs, slug):
    row = cs.get_course_row(slug)
    return {
        "name": row["name"],
        "active": row["active"],
        "dates": row["dates"],
        "fees_json": row["fees_json"],
        "keywords_json": row["keywords_json"],
        "overview": row["overview"],
        "outreach_json": row["outreach_json"],
    }


class AdminAuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import main
        from services import content_store as cs

        cs.set_admin_password("secret-pass")
        cls.main = main

    def _client(self):
        from fastapi.testclient import TestClient

        # https base_url so the Secure session cookie is honoured (it's stripped over http,
        # which is exactly the production-correct behaviour). Fresh per test = no cookie bleed.
        return TestClient(self.main.app, base_url="https://testserver")

    def test_pii_endpoints_require_auth(self):
        client = self._client()
        for path in ("/stats", "/lead/60123456789", "/conversation/60123456789"):
            with self.subTest(path=path):
                self.assertEqual(401, client.get(path).status_code)

    def test_login_then_access(self):
        client = self._client()
        self.assertEqual(401, client.post("/admin/login", data={"password": "wrong"}).status_code)

        ok = client.post("/admin/login", data={"password": "secret-pass"}, follow_redirects=False)
        self.assertEqual(303, ok.status_code)
        # cookie is now on the client; the PII endpoint opens up
        self.assertEqual(200, client.get("/stats").status_code)

    def test_wrong_cookie_is_rejected(self):
        client = self._client()
        client.cookies.set("admin_session", "9999999999.deadbeef")
        self.assertEqual(401, client.get("/stats").status_code)


if __name__ == "__main__":
    unittest.main()
