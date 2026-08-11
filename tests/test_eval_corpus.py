"""Real-corpus evaluation of the understanding + routing pipeline.

Unlike the mocked unit tests, this drives the LIVE interpreter (Groq) and the REAL
rag_v2 index, and asserts the *decision* for each turn — route (plan intents/modes),
scope (course the retrieval was confined to), source chunk (which corpus file grounded
the answer), and requested-field coverage — rather than brittle response substrings.
Expected values are derived from the corpus (course config/overview), never hard-coded.

Gated: runs only when RUN_CORPUS_EVAL=1 and GROQ_API_KEY are set (and, for full docx/pdf
coverage, an environment with python-docx installed). It builds its own fresh index and a
temp trace file, so it never touches a running server.

Run:  RUN_CORPUS_EVAL=1 ./venv/bin/python -m pytest tests/test_eval_corpus.py -v
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from pathlib import Path

try:  # make GROQ_API_KEY from .env visible before the gate is evaluated
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass

GATE = bool(os.getenv("RUN_CORPUS_EVAL")) and bool(os.getenv("GROQ_API_KEY"))

# Topics that mean "grounded in this course's own approved material".
COURSE_TOPICS = {
    "course_overview",
    "course_facts",
    "course_kb",
    "course_hrdc_outline",
    "course_flyer",
}
ABSTAIN = "don't have enough verified information"


@unittest.skipUnless(GATE, "set RUN_CORPUS_EVAL=1 and GROQ_API_KEY to run the real-corpus eval")
class CorpusEval(unittest.TestCase):
    results: dict = {}

    @classmethod
    def setUpClass(cls):
        os.environ["USE_INTERPRETER"] = "true"
        os.environ.setdefault("WHATSAPP_DB_PATH", tempfile.mktemp(suffix=".sqlite"))
        os.environ.setdefault("RAG_V2_INDEX", tempfile.mktemp(suffix=".sqlite"))
        cls.trace_path = Path(tempfile.mktemp(suffix=".jsonl"))
        os.environ["RAG_TRACE_PATH"] = str(cls.trace_path)

        from fastapi.testclient import TestClient

        import main
        from services.course_loader import get_active_courses, get_course

        cls.get_course = staticmethod(get_course)
        cls.active = get_active_courses()
        cls.yocto = next(c for c in cls.active if "yocto" in c.slug)
        cls.testing = next(c for c in cls.active if "software testing" in c.name.lower())

        # Capture the exact plan the pipeline used on each turn.
        cls._plans: list = []
        _orig = main.understand

        def _cap(msg, **kw):
            plan = _orig(msg, **kw)
            cls._plans.append(plan)
            return plan

        main.understand = _cap
        cls.client = TestClient(main.app)

        # Run every case ONCE (one LLM call per turn); assertions read stored results.
        cls.results = {}
        cls._case("day_by_day", cls.yocto, ["can i get the day by day breakdown"])
        cls._case("day_one", cls.yocto, ["what is on day one"])
        cls._case("syllabus_and_price", cls.yocto, ["can you share the syllabus and the price"])
        cls._case("catalog", cls.yocto, ["what other courses are available"])
        cls._case("hrdc", cls.yocto, ["is this hrdc claimable"])
        cls._case("misspell_fee", cls.testing, ["how mch is teh course"])
        cls._case("misspell_venue", cls.yocto, ["wats teh venue"])
        cls._case(
            "followup_dates", cls.yocto, ["how much is it", "and the dates?"]
        )  # short follow-up

    @classmethod
    def _trace_count(cls) -> int:
        return sum(1 for _ in cls.trace_path.open()) if cls.trace_path.exists() else 0

    @classmethod
    def _new_selected(cls, since: int) -> list:
        if not cls.trace_path.exists():
            return []
        rows = [json.loads(line) for line in cls.trace_path.open() if line.strip()][since:]
        selected = []
        for row in rows:
            selected.extend(row.get("selected") or [])
        return selected

    @classmethod
    def _case(cls, name: str, course, messages: list[str]):
        """Send a (possibly multi-turn) case; store the FINAL turn's decision + evidence."""
        session = f"eval-{name}"
        result = {}
        for message in messages:
            before = cls._trace_count()
            cls._plans.clear()
            resp = cls.client.post(
                "/simulate/message",
                json={
                    "session_id": session,
                    "message": message,
                    "course": course.slug,
                    "name": "eval",
                },
            ).json()
            result = {
                "message": message,
                "reply": resp["reply"],
                "lead": resp.get("lead") or {},
                "plan": cls._plans[-1] if cls._plans else None,
                "selected": cls._new_selected(before),
                "course": course,
            }
        cls.results[name] = result

    # ---- helpers ---------------------------------------------------------
    def _pairs(self, plan):
        self.assertIsNotNone(plan, "interpreter returned no plan (Groq unavailable?)")
        return {(r.intent, r.mode) for r in plan.requests}

    def _selected_course_ids(self, selected):
        return {s.get("course_id") for s in selected}

    def _fee_str(self, course):
        return f"{course.fees['standard']:,}"

    def _month_year(self, course):
        m = re.search(r"([A-Za-z]+)\s+(\d{4})", course.dates or "")
        return (m.group(1), m.group(2)) if m else (None, None)

    # ---- ROUTE / SCOPE / SOURCE / FIELD-COVERAGE (must pass) --------------
    def test_day_by_day_route_scope_source(self):
        r = self.results["day_by_day"]
        pairs = self._pairs(r["plan"])
        # route: a descriptive curriculum retrieve, NOT the DURATION exact bucket
        self.assertIn(("COURSE_CONTENT", "retrieve"), pairs)
        self.assertFalse(any(i == "DURATION" for i, _ in pairs), f"misrouted to DURATION: {pairs}")
        # scope: retrieval confined to the Yocto course
        self.assertTrue(r["selected"], "no chunks retrieved")
        self.assertEqual(self._selected_course_ids(r["selected"]), {self.yocto.slug})
        # source: top chunk is this course's own approved material
        self.assertIn(r["selected"][0]["topic"], COURSE_TOPICS)

    def test_day_one_route_scope_source(self):
        r = self.results["day_one"]
        pairs = self._pairs(r["plan"])
        self.assertIn(("COURSE_CONTENT", "retrieve"), pairs)
        self.assertFalse(any(i in {"DURATION", "SCHEDULE"} for i, _ in pairs))
        self.assertTrue(r["selected"], "no chunks retrieved")
        self.assertEqual(self._selected_course_ids(r["selected"]), {self.yocto.slug})
        self.assertIn(r["selected"][0]["topic"], COURSE_TOPICS)

    def test_syllabus_and_price_requested_field_coverage(self):
        r = self.results["syllabus_and_price"]
        pairs = self._pairs(r["plan"])
        # requested-field coverage AT THE DECISION LEVEL: both a curriculum ask and a fee ask
        self.assertIn(("COURSE_CONTENT", "retrieve"), pairs)
        self.assertIn(("FEES", "exact"), pairs)
        # the descriptive part is scoped to the course
        self.assertTrue(r["selected"])
        self.assertEqual(self._selected_course_ids(r["selected"]), {self.yocto.slug})

    def test_catalog_route_and_scope(self):
        r = self.results["catalog"]
        pairs = self._pairs(r["plan"])
        # route: catalog-wide intent (not a single-course lookup)
        self.assertTrue(any(i == "CATALOG" for i, _ in pairs), f"not routed to CATALOG: {pairs}")

    def test_hrdc_route_and_source_of_truth(self):
        r = self.results["hrdc"]
        pairs = self._pairs(r["plan"])
        self.assertIn(("HRDC", "exact"), pairs)
        # source: the exact answer is grounded in the course's real HRDC facts
        self.assertIn("HRDC", r["reply"])
        self.assertIn("claimable", r["reply"].lower())
        if self.yocto.hrdc_deadline:
            self.assertIn(self.yocto.hrdc_deadline, r["reply"])

    def test_misspelled_fee_routes_to_fees(self):
        r = self.results["misspell_fee"]
        pairs = self._pairs(r["plan"])
        self.assertIn(("FEES", "exact"), pairs)
        self.assertIn(self._fee_str(self.testing), r["reply"])  # corpus-derived fee

    def test_misspelled_venue_routes_to_venue(self):
        r = self.results["misspell_venue"]
        pairs = self._pairs(r["plan"])
        self.assertIn(("VENUE", "exact"), pairs)
        self.assertIn(self.yocto.venue, r["reply"])

    def test_short_followup_keeps_scope_and_switches_field(self):
        r = self.results["followup_dates"]
        pairs = self._pairs(r["plan"])
        # "and the dates?" after a fee question → SCHEDULE, still the SAME course
        self.assertIn(("SCHEDULE", "exact"), pairs)
        slugs = {r_.course_slug for r_ in r["plan"].requests if r_.course_slug}
        self.assertIn(self.yocto.slug, slugs or {self.yocto.slug})
        month, year = self._month_year(self.yocto)
        if month:
            self.assertIn(month, r["reply"])
            self.assertIn(year, r["reply"])

    # ---- ANSWER-QUALITY field coverage (solid cases pass) ----------------
    def test_day_by_day_answer_covers_multiple_days(self):
        reply = self.results["day_by_day"]["reply"].lower()
        self.assertNotIn(ABSTAIN, reply)
        tokens = [
            "yocto",
            "bitbake",
            "kernel",
            "bootloader",
            "rootfs",
            "root filesystem",
            "beaglebone",
            "layer",
            "recipe",
            "bsp",
            "image",
        ]
        hits = sum(1 for t in tokens if t in reply)
        self.assertGreaterEqual(hits, 2, f"curriculum tokens found: {hits}")

    def test_catalog_answer_lists_multiple_courses(self):
        reply = self.results["catalog"]["reply"]
        named = sum(1 for c in self.active if c.name in reply)
        self.assertGreaterEqual(named, 3, f"only {named} course names present")

    def test_syllabus_and_price_answer_has_fee(self):
        reply = self.results["syllabus_and_price"]["reply"]
        self.assertIn(self._fee_str(self.yocto), reply)  # the exact half must always land

    def test_day_one_answer_covers_day_one_content(self):
        reply = self.results["day_one"]["reply"].lower()
        self.assertNotIn(ABSTAIN, reply)
        self.assertTrue(re.search(r"day\s*1|day\s*one", reply), "no day-1 content")


if __name__ == "__main__":
    unittest.main()
