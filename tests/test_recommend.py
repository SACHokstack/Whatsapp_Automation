"""Tests for the LLM-grounded course recommender (services/recommend.py).

These verify GROUNDING and rendering — that the model may only surface real courses, that
facts come from the catalogue, and that disabled/ambiguous cases degrade sensibly. They do
NOT assert the model's reasoning (which course fits which background); that is the model's
job and is exactly what we stopped hard-coding.
"""

import json
import os
import unittest
from unittest.mock import patch

import services.bedrock as bedrock
from services.recommend import recommend_courses

_LLM_ENV = {"USE_INTERPRETER": "true", "INTERPRETER_PROVIDER": "bedrock", "BEDROCK_REGION": "ap-south-1"}


def _mock_llm(payload):
    return patch.object(bedrock, "converse_json", lambda **kwargs: json.dumps(payload))


class RecommendTests(unittest.TestCase):
    def test_price_superlative_is_deterministic(self):
        # A fact, not a judgement — answered from fees without the model.
        cheapest = recommend_courses("which is the cheapest")
        self.assertIn("most affordable", cheapest.lower())
        self.assertIn("RM3,200", cheapest)
        priciest = recommend_courses("which is the most expensive")
        self.assertIn("most expensive", priciest.lower())
        self.assertIn("RM7,500", priciest)

    def test_fallback_when_llm_disabled(self):
        with patch.dict(os.environ, {"USE_INTERPRETER": "false"}, clear=False):
            reply = recommend_courses("i know python and c, what fits me")
        # No silent single pick — asks for background and lists real courses.
        self.assertIn("your background", reply.lower())
        self.assertIn("Modern Software Testing with AI-Assisted Automation & CI/CD Integration", reply)

    def test_llm_pick_renders_grounded_facts(self):
        payload = {
            "recommended": ["sw-testing-aug-2026", "embedded-c-july-2026"],
            "reason": "Your Python/Java suit test automation and your C/C++ suit embedded.",
            "clarify": "Which are you aiming for — test automation or embedded/firmware?",
        }
        with patch.dict(os.environ, _LLM_ENV, clear=False), _mock_llm(payload):
            reply = recommend_courses("i know python java c cpp which is relevant")
        self.assertIn("Modern Software Testing with AI-Assisted Automation & CI/CD Integration", reply)
        self.assertIn("Embedded C Programming and GDB Debugging", reply)
        # Facts (dates, fee) come from the real config, not the model.
        self.assertIn("30–31 July 2026", reply)
        self.assertIn("RM3,200", reply)
        self.assertIn("test automation or embedded/firmware", reply)

    def test_hallucinated_slug_is_dropped(self):
        payload = {
            "recommended": ["ai-bootcamp-2027", "embedded-c-july-2026"],
            "reason": "C fits embedded.",
            "clarify": "",
        }
        with patch.dict(os.environ, _LLM_ENV, clear=False), _mock_llm(payload):
            reply = recommend_courses("i know c")
        self.assertIn("Embedded C Programming and GDB Debugging", reply)
        self.assertNotIn("ai-bootcamp", reply.lower())
        self.assertNotIn("bootcamp", reply.lower())

    def test_offcatalog_topic_is_declined_with_alternatives_and_contact(self):
        payload = {
            "recommended": [],
            "reason": "",
            "clarify": "",
            "unavailable_topic": "cyber security",
        }
        with patch.dict(os.environ, _LLM_ENV, clear=False), _mock_llm(payload):
            reply = recommend_courses("do you have a cyber security course")
        self.assertIn("don't currently offer", reply.lower())
        self.assertIn("cyber security", reply)
        # Shows what we DO run and hands the consultant contact.
        self.assertIn("Modern Software Testing with AI-Assisted Automation & CI/CD Integration", reply)
        self.assertIn("consultant", reply.lower())
        self.assertRegex(reply, r"\+?\d[\d\s-]{6,}")  # a phone number is present

    def test_empty_pick_uses_clarifying_question(self):
        payload = {"recommended": [], "reason": "", "clarify": "What is your main language and target domain?"}
        with patch.dict(os.environ, _LLM_ENV, clear=False), _mock_llm(payload):
            reply = recommend_courses("suggest something")
        self.assertEqual("What is your main language and target domain?", reply)

    def test_openrouter_provider_is_supported(self):
        import json as _json
        from unittest.mock import MagicMock

        payload = {"recommended": ["embedded-c-july-2026"], "reason": "C fits embedded.",
                   "clarify": "", "unavailable_topic": ""}
        fake = MagicMock()
        fake.raise_for_status = lambda: None
        fake.json = lambda: {"choices": [{"message": {"content": _json.dumps(payload)}}]}
        env = {"USE_INTERPRETER": "true", "INTERPRETER_PROVIDER": "openrouter", "OPENROUTER_API_KEY": "sk-x"}
        with patch.dict(os.environ, env, clear=False), patch("requests.post", return_value=fake):
            reply = recommend_courses("i know c and firmware")
        self.assertIn("Embedded C Programming and GDB Debugging", reply)
        self.assertNotIn("Tell me a bit about your background", reply)  # not the disabled fallback

    def test_llm_error_falls_back(self):
        def _boom(**kwargs):
            raise RuntimeError("bedrock down")

        with patch.dict(os.environ, _LLM_ENV, clear=False), patch.object(bedrock, "converse_json", _boom):
            reply = recommend_courses("i know python, what fits me")
        self.assertIn("your background", reply.lower())


if __name__ == "__main__":
    unittest.main()
