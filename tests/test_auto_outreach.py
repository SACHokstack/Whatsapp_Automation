"""Automatic first contact.

These cover the guards, because the failure mode here is messaging a real lead twice —
or messaging a whole sheet at 3am. Every send is mocked; nothing leaves the process.
"""

import unittest
import unittest.mock
from datetime import datetime
from zoneinfo import ZoneInfo

from services.auto_outreach import FAILED_STATUS, dispatch_outreach, within_send_window
from services.course_loader import get_active_courses
from services.persistence import get_lead, upsert_lead

_ON = {
    "AUTO_OUTREACH_ENABLED": "true",
    "AUTO_OUTREACH_DELAY_SECONDS": "0",
    "AUTO_OUTREACH_HOURS": "0-24",
}


def _course_with_template():
    for course in get_active_courses():
        if course.outreach_template:
            return course
    raise unittest.SkipTest("no active course configures an outreach template")


class _Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._payload = payload or {"messages": [{"id": "wamid.TEST"}]}
        self.text = "error body"

    def json(self):
        return self._payload


class SendWindowTests(unittest.TestCase):
    def test_window_blocks_the_small_hours_and_allows_daytime(self):
        env = {"AUTO_OUTREACH_HOURS": "9-19", "AUTO_OUTREACH_TZ": "Asia/Kuala_Lumpur"}
        tz = ZoneInfo("Asia/Kuala_Lumpur")
        with unittest.mock.patch.dict("os.environ", env):
            self.assertFalse(within_send_window(datetime(2026, 8, 12, 3, 0, tzinfo=tz)))
            self.assertTrue(within_send_window(datetime(2026, 8, 12, 10, 0, tzinfo=tz)))
            self.assertFalse(within_send_window(datetime(2026, 8, 12, 22, 0, tzinfo=tz)))

    def test_malformed_window_falls_back_to_daytime_default(self):
        with unittest.mock.patch.dict("os.environ", {"AUTO_OUTREACH_HOURS": "not-a-range"}):
            tz = ZoneInfo("Asia/Kuala_Lumpur")
            self.assertTrue(within_send_window(datetime(2026, 8, 12, 10, 0, tzinfo=tz)))
            self.assertFalse(within_send_window(datetime(2026, 8, 12, 3, 0, tzinfo=tz)))


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.course = _course_with_template()
        self.phone = "60111222333"
        upsert_lead(self.phone, status="CREATED", course=self.course.slug, name="Ada")

    def tearDown(self):
        upsert_lead(self.phone, status="DONE")

    def _dispatch(self, response=None, env=None, **kwargs):
        patched = dict(_ON)
        patched.update(env or {})
        with (
            unittest.mock.patch.dict("os.environ", patched),
            unittest.mock.patch(
                "services.auto_outreach.send_template", return_value=response or _Response()
            ) as send,
        ):
            result = dispatch_outreach(course_slug=self.course.slug, **kwargs)
        return result, send

    def test_disabled_by_default_sends_nothing(self):
        with unittest.mock.patch.dict("os.environ", {"AUTO_OUTREACH_ENABLED": "false"}):
            with unittest.mock.patch("services.auto_outreach.send_template") as send:
                result = dispatch_outreach(course_slug=self.course.slug)
        send.assert_not_called()
        self.assertEqual(0, result.sent)
        self.assertIn("disabled", result.reason)

    def test_outside_the_window_sends_nothing(self):
        with (
            unittest.mock.patch.dict("os.environ", {"AUTO_OUTREACH_ENABLED": "true"}),
            unittest.mock.patch("services.auto_outreach.within_send_window", return_value=False),
            unittest.mock.patch("services.auto_outreach.send_template") as send,
        ):
            result = dispatch_outreach(course_slug=self.course.slug)
        send.assert_not_called()
        self.assertIn("window", result.reason)

    def test_force_overrides_both_guards(self):
        with (
            unittest.mock.patch.dict(
                "os.environ", {"AUTO_OUTREACH_ENABLED": "false", "AUTO_OUTREACH_DELAY_SECONDS": "0"}
            ),
            unittest.mock.patch("services.auto_outreach.within_send_window", return_value=False),
            unittest.mock.patch(
                "services.auto_outreach.send_template", return_value=_Response()
            ) as send,
        ):
            result = dispatch_outreach(course_slug=self.course.slug, force=True)
        send.assert_called_once()
        self.assertEqual(1, result.sent)

    def test_send_marks_contacted_so_the_next_pass_skips_the_lead(self):
        result, send = self._dispatch()
        self.assertEqual(1, result.sent)
        send.assert_called_once()
        self.assertEqual("CONTACTED", get_lead(self.phone)["status"])

        second, send_again = self._dispatch()
        self.assertEqual(0, second.sent)
        send_again.assert_not_called()

    def test_lead_name_is_passed_to_the_template(self):
        _, send = self._dispatch()
        self.assertEqual(["Ada"], send.call_args.kwargs["variables"])

    def test_permanent_failure_stops_the_lead_being_retried_forever(self):
        result, _ = self._dispatch(response=_Response(status_code=400))
        self.assertEqual(1, result.failed)
        self.assertEqual(FAILED_STATUS, get_lead(self.phone)["status"])

    def test_rate_limit_leaves_the_lead_pending_for_the_next_pass(self):
        result, _ = self._dispatch(response=_Response(status_code=429))
        self.assertEqual(1, result.failed)
        self.assertEqual("CREATED", get_lead(self.phone)["status"])

    def test_dry_run_reports_without_sending_or_writing(self):
        result, send = self._dispatch(dry_run=True)
        send.assert_not_called()
        self.assertEqual(1, result.sent)
        self.assertEqual("CREATED", get_lead(self.phone)["status"])

    def test_per_run_cap_limits_the_blast_radius(self):
        for index in range(3):
            upsert_lead(f"6011999000{index}", status="CREATED", course=self.course.slug, name="X")
        try:
            result, send = self._dispatch(env={"AUTO_OUTREACH_MAX_PER_RUN": "2"})
            self.assertEqual(2, result.sent)
            self.assertEqual(2, send.call_count)
            self.assertGreaterEqual(result.pending, 1)
        finally:
            for index in range(3):
                upsert_lead(f"6011999000{index}", status="DONE")


if __name__ == "__main__":
    unittest.main()
