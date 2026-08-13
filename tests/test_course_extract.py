"""Creating a course from its brochure.

The model's output is treated as untrusted input: it is normalised into the shapes the course
form and CourseConfig expect, and it is only ever *proposed* — the endpoint saves nothing, so a
misread fee reaches a human before it reaches a customer.
"""

import os
import unittest
import unittest.mock

from services.course_extract import ExtractionUnavailable, _normalise, propose_course_fields

BROCHURE = """Linux Kernel Programming

A 2-day hands-on course held at Timmins Training Center, Penang on 20-21 August 2026.
Investment: RM 3,200 per participant. RM 3,040 each for 2 pax, RM 2,944 each for 3 or more.
HRDC registration closes 15 August 2026. Payment due 14 August 2026.
"""


class NormalisationTests(unittest.TestCase):
    """Whatever the model returns has to end up in the shape CourseConfig expects."""

    def test_currency_strings_become_integers(self):
        fields = _normalise({"name": "X", "fees": {"standard": "RM 3,200", "group_2": 3040}})
        self.assertEqual({"standard": 3200, "group_2": 3040}, fields["fees"])

    def test_unparseable_fee_is_dropped_rather_than_guessed(self):
        fields = _normalise({"name": "X", "fees": {"standard": "on request", "group_2": "2,944"}})
        self.assertEqual({"group_2": 2944}, fields["fees"])

    def test_keywords_accept_a_list_or_a_comma_string_and_deduplicate(self):
        self.assertEqual(
            ["kernel", "driver"], _normalise({"keywords": "Kernel, driver, KERNEL"})["keywords"]
        )
        self.assertEqual(["kernel"], _normalise({"keywords": ["Kernel", " kernel "]})["keywords"])

    def test_keyword_count_is_capped(self):
        fields = _normalise({"keywords": [f"kw{i}" for i in range(50)]})
        self.assertEqual(20, len(fields["keywords"]))

    def test_missing_fields_come_back_empty_not_absent(self):
        fields = _normalise({})
        for key in ("name", "dates", "venue", "hrdc_deadline", "payment_deadline"):
            self.assertEqual("", fields[key])
        self.assertEqual({}, fields["fees"])
        self.assertEqual([], fields["keywords"])

    def test_junk_types_do_not_crash_the_form(self):
        fields = _normalise({"name": 123, "fees": "not a dict", "keywords": {"a": 1}})
        self.assertEqual("123", fields["name"])
        self.assertEqual({}, fields["fees"])
        self.assertEqual([], fields["keywords"])


class ProposalTests(unittest.TestCase):
    def _propose(self, payload, text=BROCHURE):
        with unittest.mock.patch("services.course_extract._ask_model", return_value=payload):
            return propose_course_fields(text)

    def test_overview_is_the_document_text_not_a_summary(self):
        fields = self._propose({"name": "Linux Kernel Programming"})
        # The bot answers from the overview, so every detail in the document must survive.
        self.assertIn("RM 3,200 per participant", fields["overview"])
        self.assertIn("HRDC registration closes 15 August 2026", fields["overview"])

    def test_overview_gets_the_heading_the_fact_parsers_expect(self):
        fields = self._propose({"name": "Linux Kernel Programming"})
        self.assertTrue(fields["overview"].startswith("# Course Overview"))

    def test_an_existing_heading_is_left_alone(self):
        fields = self._propose({"name": "X"}, text="# Course Overview\n\nAlready formatted.")
        self.assertEqual("# Course Overview\n\nAlready formatted.", fields["overview"])

    def test_empty_document_is_refused(self):
        with self.assertRaises(ExtractionUnavailable):
            propose_course_fields("   ")

    def test_no_model_configured_is_reported_not_crashed(self):
        with unittest.mock.patch(
            "services.interpret._interpreter_settings", return_value=("groq", "", "model")
        ):
            with self.assertRaises(ExtractionUnavailable) as caught:
                propose_course_fields(BROCHURE)
        self.assertIn("no language model", str(caught.exception))

    def test_provider_failure_becomes_extraction_unavailable(self):
        with unittest.mock.patch(
            "services.interpret._interpreter_settings", return_value=("groq", "key", "model")
        ):
            with unittest.mock.patch("groq.Groq", side_effect=RuntimeError("boom")):
                with self.assertRaises(ExtractionUnavailable):
                    propose_course_fields(BROCHURE)


class ExtractEndpointTests(unittest.TestCase):
    def _client(self, authed=True):
        from fastapi.testclient import TestClient

        import main
        from services import content_store as cs

        cs.set_admin_password("extract-secret")
        client = TestClient(main.app, base_url="https://testserver")
        if authed:
            client.post("/admin/login", data={"password": "extract-secret"}, follow_redirects=False)
        return client

    def tearDown(self):
        os.environ["CONTENT_SOURCE"] = "files"

    def test_requires_auth(self):
        os.environ["CONTENT_SOURCE"] = "db"
        response = self._client(authed=False).post(
            "/admin/api/courses/extract", files={"file": ("a.txt", b"text", "text/plain")}
        )
        self.assertEqual(401, response.status_code)

    def test_refused_in_files_mode(self):
        os.environ["CONTENT_SOURCE"] = "files"
        response = self._client().post(
            "/admin/api/courses/extract", files={"file": ("a.txt", b"text", "text/plain")}
        )
        self.assertEqual(409, response.status_code)

    def test_proposes_fields_without_saving_anything(self):
        from services import content_store as cs

        os.environ["CONTENT_SOURCE"] = "db"
        before = {row["slug"] for row in cs.list_course_rows()}
        payload = {
            "name": "Linux Kernel Programming",
            "dates": "20-21 August 2026",
            "venue": "Timmins Training Center, Penang",
            "fees": {"standard": "RM 3,200"},
            "keywords": ["kernel"],
        }
        with unittest.mock.patch("services.course_extract._ask_model", return_value=payload):
            response = self._client().post(
                "/admin/api/courses/extract",
                files={"file": ("brochure.txt", BROCHURE.encode(), "text/plain")},
            )
        self.assertEqual(200, response.status_code, response.text)
        fields = response.json()["fields"]
        self.assertEqual("Linux Kernel Programming", fields["name"])
        self.assertEqual({"standard": 3200}, fields["fees"])
        # The decisive property: reading a brochure must not create anything.
        self.assertEqual(before, {row["slug"] for row in cs.list_course_rows()})

    def test_empty_upload_is_rejected(self):
        os.environ["CONTENT_SOURCE"] = "db"
        response = self._client().post(
            "/admin/api/courses/extract", files={"file": ("a.txt", b"", "text/plain")}
        )
        self.assertEqual(400, response.status_code)

    def test_unreadable_document_reports_422(self):
        os.environ["CONTENT_SOURCE"] = "db"
        response = self._client().post(
            "/admin/api/courses/extract", files={"file": ("a.xyz", b"\x00\x01", "application/x")}
        )
        self.assertEqual(422, response.status_code)

    def test_model_unavailable_reports_503_and_points_at_manual_entry(self):
        os.environ["CONTENT_SOURCE"] = "db"
        with unittest.mock.patch(
            "services.course_extract._ask_model",
            side_effect=ExtractionUnavailable("no language model is configured"),
        ):
            response = self._client().post(
                "/admin/api/courses/extract",
                files={"file": ("brochure.txt", BROCHURE.encode(), "text/plain")},
            )
        self.assertEqual(503, response.status_code)
        self.assertIn("by hand", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
