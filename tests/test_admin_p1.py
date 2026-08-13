"""P1 read-only dashboard: pages render for an authed admin, and every /admin page and
/admin/api/* endpoint refuses an unauthenticated caller (pages redirect, APIs 401)."""

import unittest


def _client(authed=False):
    from fastapi.testclient import TestClient

    import main
    from services import content_store as cs

    cs.set_admin_password("p1-secret")
    # https base_url so the Secure session cookie survives; fresh client per call = no bleed.
    client = TestClient(main.app, base_url="https://testserver")
    if authed:
        client.post("/admin/login", data={"password": "p1-secret"}, follow_redirects=False)
    return client


PAGES = ("/admin", "/admin/leads", "/admin/courses", "/admin/knowledge")
APIS = (
    "/admin/api/summary",
    "/admin/api/meta",
    "/admin/api/leads",
    "/admin/api/courses",
    "/admin/api/knowledge",
)


class AdminP1AuthTests(unittest.TestCase):
    def test_pages_redirect_when_unauthed(self):
        client = _client()
        for path in PAGES:
            with self.subTest(path=path):
                self.assertEqual(303, client.get(path, follow_redirects=False).status_code)

    def test_apis_401_when_unauthed(self):
        client = _client()
        for path in APIS:
            with self.subTest(path=path):
                self.assertEqual(401, client.get(path).status_code)


class AdminP1ReadTests(unittest.TestCase):
    def test_pages_render_for_admin(self):
        client = _client(authed=True)
        for path in PAGES:
            with self.subTest(path=path):
                r = client.get(path)
                self.assertEqual(200, r.status_code)
                self.assertIn("Timmins Admin", r.text)

    def test_summary_reports_course_and_knowledge_counts(self):
        client = _client(authed=True)
        s = client.get("/admin/api/summary").json()
        for key in (
            "total_leads",
            "total_messages",
            "total_courses",
            "active_courses",
            "knowledge_topics",
        ):
            self.assertIn(key, s)
        self.assertGreaterEqual(s["total_courses"], s["active_courses"])

    def test_courses_api_shapes_each_course(self):
        client = _client(authed=True)
        courses = client.get("/admin/api/courses").json()["courses"]
        self.assertTrue(courses)
        for c in courses:
            for key in ("slug", "name", "active", "keywords", "overview", "fees"):
                self.assertIn(key, c)

    def test_knowledge_api_returns_topics_and_policies(self):
        client = _client(authed=True)
        d = client.get("/admin/api/knowledge").json()
        self.assertIn("topics", d)
        self.assertIn("policies", d)


if __name__ == "__main__":
    unittest.main()
