"""P3: company knowledge and policy editing.

The point of these endpoints is that an edit made in the dashboard is what the bot says next —
so the tests assert through the loaders the bot actually reads (`load_knowledge_base`,
`load_policies`), not just that a row was written.
"""

import os
import unittest


def _client(authed=True):
    from fastapi.testclient import TestClient

    import main
    from services import content_store as cs

    cs.set_admin_password("p3-secret")
    client = TestClient(main.app, base_url="https://testserver")
    if authed:
        client.post("/admin/login", data={"password": "p3-secret"}, follow_redirects=False)
    return client


def _db_mode():
    import services.knowledge_base as kb
    import services.structured_facts as sf

    os.environ["CONTENT_SOURCE"] = "db"
    kb._load_knowledge_base_cached.cache_clear()
    sf._load_policies_cached.cache_clear()


def _files_mode():
    import services.knowledge_base as kb
    import services.structured_facts as sf

    os.environ["CONTENT_SOURCE"] = "files"
    kb._load_knowledge_base_cached.cache_clear()
    sf._load_policies_cached.cache_clear()


class KnowledgeAuthTests(unittest.TestCase):
    def test_mutations_require_auth(self):
        client = _client(authed=False)
        self.assertEqual(
            401, client.post("/admin/api/knowledge", json={"topic": "x", "body": "y"}).status_code
        )
        self.assertEqual(401, client.delete("/admin/api/knowledge/x").status_code)
        self.assertEqual(401, client.post("/admin/api/policies", json={"policies": {}}).status_code)

    def test_mutations_are_refused_in_files_mode(self):
        _files_mode()
        client = _client()
        response = client.post("/admin/api/knowledge", json={"topic": "x", "body": "y"})
        self.assertEqual(409, response.status_code)
        self.assertIn("CONTENT_SOURCE=db", response.json()["detail"])


class KnowledgeEditTests(unittest.TestCase):
    def setUp(self):
        _db_mode()
        self.client = _client()

    def tearDown(self):
        from services import content_store as cs

        for topic in ("dashboard_test_topic", "messy_name"):
            try:
                cs.delete_knowledge(topic)
            except Exception:
                pass
        _files_mode()

    def test_saved_topic_is_visible_to_the_bots_loader(self):
        from services.knowledge_base import load_knowledge_base

        body = "# Refunds\n\nRefunds are processed within 14 working days."
        response = self.client.post(
            "/admin/api/knowledge", json={"topic": "dashboard_test_topic", "body": body}
        )
        self.assertEqual(200, response.status_code, response.text)

        # The decisive check: the loader the bot reads from returns the new text, no restart.
        self.assertIn("dashboard_test_topic", load_knowledge_base())
        self.assertIn("14 working days", load_knowledge_base()["dashboard_test_topic"])

    def test_editing_a_topic_replaces_its_body(self):
        from services.knowledge_base import load_knowledge_base

        self.client.post(
            "/admin/api/knowledge", json={"topic": "dashboard_test_topic", "body": "First version."}
        )
        self.client.post(
            "/admin/api/knowledge",
            json={"topic": "dashboard_test_topic", "body": "Second version."},
        )
        self.assertEqual("Second version.", load_knowledge_base()["dashboard_test_topic"])

    def test_deleting_a_topic_removes_it_from_the_loader(self):
        from services.knowledge_base import load_knowledge_base

        self.client.post(
            "/admin/api/knowledge", json={"topic": "dashboard_test_topic", "body": "Temporary."}
        )
        self.assertIn("dashboard_test_topic", load_knowledge_base())

        response = self.client.delete("/admin/api/knowledge/dashboard_test_topic")
        self.assertEqual(200, response.status_code)
        self.assertNotIn("dashboard_test_topic", load_knowledge_base())

    def test_topic_names_are_normalised(self):
        from services.knowledge_base import load_knowledge_base

        response = self.client.post(
            "/admin/api/knowledge", json={"topic": "  Messy Name!! ", "body": "text"}
        )
        self.assertEqual("messy_name", response.json()["topic"])
        self.assertIn("messy_name", load_knowledge_base())

    def test_empty_body_and_missing_topic_are_rejected(self):
        self.assertEqual(
            400,
            self.client.post(
                "/admin/api/knowledge", json={"topic": "a_topic", "body": "   "}
            ).status_code,
        )
        self.assertEqual(
            400,
            self.client.post("/admin/api/knowledge", json={"topic": "", "body": "x"}).status_code,
        )

    def test_deleting_an_unknown_topic_is_404(self):
        self.assertEqual(404, self.client.delete("/admin/api/knowledge/no_such_topic").status_code)


class PolicyEditTests(unittest.TestCase):
    def setUp(self):
        from services.structured_facts import load_policies

        _db_mode()
        self.client = _client()
        self.original = load_policies()

    def tearDown(self):
        from services import content_store as cs

        if self.original:
            cs.set_policies(self.original)
            cs.bump_content_version()
        _files_mode()

    def test_saved_policies_are_visible_to_the_bots_loader(self):
        from services.structured_facts import load_policies

        updated = {**(self.original or {}), "payment": {"terms": "Net 45 days"}}
        response = self.client.post("/admin/api/policies", json={"policies": updated})
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("Net 45 days", load_policies()["payment"]["terms"])

    def test_invalid_json_string_is_rejected(self):
        response = self.client.post("/admin/api/policies", json={"policies": "{not json"})
        self.assertEqual(400, response.status_code)

    def test_non_object_policies_are_rejected(self):
        response = self.client.post("/admin/api/policies", json={"policies": [1, 2, 3]})
        self.assertEqual(400, response.status_code)


if __name__ == "__main__":
    unittest.main()
