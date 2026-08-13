"""P4: runtime controls.

Two things have to hold. A value saved from the dashboard must actually change what the code
does — not merely land in a table — so these tests assert through the functions that read it
(`auto_outreach.enabled`, `lead_sync.contact_cutoff`, `whatsapp._reply_delay`). And a bad value
must be refused at the point of entry: an unparseable cutoff silently means "no cutoff", which
would let the bot message the very people who were promised it wouldn't.
"""

import os
import unittest
import unittest.mock

from services.settings import (
    EDITABLE_KEYS,
    EDITABLE_SETTINGS,
    RESTART_REQUIRED_SETTINGS,
    SettingError,
    validate_setting,
)


def _clear(*keys):
    """Unset stored values so the environment applies again — not the same as storing ""."""
    from services import content_store as cs

    for key in keys:
        try:
            cs.delete_setting(key)
        except Exception:
            pass


class ValidationTests(unittest.TestCase):
    def test_booleans_are_normalised(self):
        self.assertEqual("true", validate_setting("BOT_SAFE_MODE", "ON"))
        self.assertEqual("false", validate_setting("BOT_SAFE_MODE", "No"))

    def test_rubbish_boolean_is_refused(self):
        with self.assertRaises(SettingError):
            validate_setting("BOT_SAFE_MODE", "maybe")

    def test_numbers_are_range_checked(self):
        self.assertEqual("25", validate_setting("AUTO_OUTREACH_MAX_PER_RUN", "25"))
        with self.assertRaises(SettingError):
            validate_setting("AUTO_OUTREACH_MAX_PER_RUN", "-1")
        with self.assertRaises(SettingError):
            validate_setting("AUTO_OUTREACH_MAX_PER_RUN", "100000")
        with self.assertRaises(SettingError):
            validate_setting("AUTO_OUTREACH_MAX_PER_RUN", "lots")

    def test_sending_hours_must_be_a_sane_window(self):
        self.assertEqual("9-19", validate_setting("AUTO_OUTREACH_HOURS", " 9 - 19 "))
        for bad in ("19-9", "9", "9-30", "morning"):
            with self.subTest(bad=bad), self.assertRaises(SettingError):
                validate_setting("AUTO_OUTREACH_HOURS", bad)

    def test_cutoff_must_parse_or_be_empty(self):
        self.assertEqual("", validate_setting("LEAD_SYNC_CONTACT_CUTOFF", "  "))
        self.assertEqual(
            "2026-08-01T00:00:00+08:00",
            validate_setting("LEAD_SYNC_CONTACT_CUTOFF", "2026-08-01T00:00:00+08:00"),
        )
        # The dangerous case: garbage would otherwise be read later as "no cutoff at all".
        with self.assertRaises(SettingError):
            validate_setting("LEAD_SYNC_CONTACT_CUTOFF", "1st August")

    def test_unknown_timezone_is_refused(self):
        with self.assertRaises(SettingError):
            validate_setting("AUTO_OUTREACH_TZ", "Mars/Olympus")
        self.assertEqual(
            "Asia/Kuala_Lumpur", validate_setting("AUTO_OUTREACH_TZ", "Asia/Kuala_Lumpur")
        )

    def test_settings_not_in_the_registry_are_refused(self):
        with self.assertRaises(SettingError):
            validate_setting("DATABASE_URL", "postgres://somewhere-else")

    def test_registry_is_coherent(self):
        for spec in EDITABLE_SETTINGS:
            for field in ("key", "label", "type", "help"):
                self.assertIn(field, spec, spec)
        # Nothing may be both live-editable and advertised as needing a restart.
        fixed = {item["key"] for item in RESTART_REQUIRED_SETTINGS}
        self.assertEqual(set(), EDITABLE_KEYS & fixed)


class SettingsTakeEffectTests(unittest.TestCase):
    """A saved setting has to change behaviour, not just sit in a table."""

    def tearDown(self):
        _clear("AUTO_OUTREACH_ENABLED", "LEAD_SYNC_CONTACT_CUTOFF", "REPLY_DELAY_SECONDS")

    def test_outreach_switch_overrides_the_environment(self):
        from services import auto_outreach
        from services.settings import set_setting

        with unittest.mock.patch.dict(os.environ, {"AUTO_OUTREACH_ENABLED": "false"}):
            self.assertFalse(auto_outreach.enabled())
            set_setting("AUTO_OUTREACH_ENABLED", "true")
            self.assertTrue(auto_outreach.enabled())

    def test_contact_cutoff_is_read_from_the_dashboard(self):
        from services.lead_sync import contact_cutoff
        from services.settings import set_setting

        _clear("LEAD_SYNC_CONTACT_CUTOFF")
        with unittest.mock.patch.dict(os.environ, {"LEAD_SYNC_CONTACT_CUTOFF": ""}):
            self.assertIsNone(contact_cutoff())
            set_setting("LEAD_SYNC_CONTACT_CUTOFF", "2026-08-01T00:00:00+08:00")
            cutoff = contact_cutoff()
        self.assertIsNotNone(cutoff)
        self.assertEqual(2026, cutoff.year)

    def test_reply_delay_is_read_from_the_dashboard_and_clamped(self):
        from services.settings import set_setting
        from services.whatsapp import _reply_delay

        set_setting("REPLY_DELAY_SECONDS", "5")
        self.assertEqual(5.0, _reply_delay())
        # Stored values still cannot push the delay somewhere absurd.
        set_setting("REPLY_DELAY_SECONDS", "9999")
        self.assertEqual(30.0, _reply_delay())

    def test_environment_still_applies_when_nothing_is_stored(self):
        from services import auto_outreach

        _clear("AUTO_OUTREACH_ENABLED")
        with unittest.mock.patch.dict(os.environ, {"AUTO_OUTREACH_ENABLED": "true"}):
            self.assertTrue(auto_outreach.enabled())

    def test_clearing_a_cutoff_overrides_the_environment_but_unsetting_restores_it(self):
        """An admin clearing the cutoff must actually clear it, not fall back to the env value."""
        from services.lead_sync import contact_cutoff
        from services.settings import set_setting

        with unittest.mock.patch.dict(
            os.environ, {"LEAD_SYNC_CONTACT_CUTOFF": "2020-01-01T00:00:00+00:00"}
        ):
            set_setting("LEAD_SYNC_CONTACT_CUTOFF", "")
            self.assertIsNone(contact_cutoff(), "an emptied setting must win over the environment")

            _clear("LEAD_SYNC_CONTACT_CUTOFF")
            restored = contact_cutoff()
        self.assertIsNotNone(restored, "unsetting should fall back to the environment")
        self.assertEqual(2020, restored.year)


class ControlsApiTests(unittest.TestCase):
    def _client(self, authed=True, password="p4-secret"):
        from fastapi.testclient import TestClient

        import main
        from services import content_store as cs

        cs.set_admin_password(password)
        client = TestClient(main.app, base_url="https://testserver")
        if authed:
            client.post("/admin/login", data={"password": password}, follow_redirects=False)
        return client

    def tearDown(self):
        from services import content_store as cs

        cs.set_admin_password("p4-secret")
        _clear("BOT_SAFE_MODE", "AUTO_OUTREACH_MAX_PER_RUN")

    def test_everything_requires_auth(self):
        client = self._client(authed=False)
        self.assertEqual(401, client.get("/admin/api/settings").status_code)
        self.assertEqual(401, client.post("/admin/api/settings", json={"settings": {}}).status_code)
        self.assertEqual(401, client.post("/admin/api/password", json={}).status_code)
        self.assertEqual(303, client.get("/admin/controls", follow_redirects=False).status_code)

    def test_page_renders(self):
        response = self._client().get("/admin/controls")
        self.assertEqual(200, response.status_code)
        self.assertIn("Controls", response.text)

    def test_listing_never_reveals_a_secret(self):
        with unittest.mock.patch.dict(os.environ, {"VERIFY_TOKEN": "super-secret-value"}):
            body = self._client().get("/admin/api/settings").json()
        self.assertNotIn("super-secret-value", str(body))
        token = next(r for r in body["restart_required"] if r["key"] == "VERIFY_TOKEN")
        self.assertEqual("configured", token["value"])

    def test_saving_applies_the_value(self):
        client = self._client()
        response = client.post("/admin/api/settings", json={"settings": {"BOT_SAFE_MODE": "false"}})
        self.assertEqual(200, response.status_code, response.text)
        from services.settings import get_bool

        self.assertFalse(get_bool("BOT_SAFE_MODE", True))

    def test_a_rejected_value_leaves_nothing_half_applied(self):
        from services.settings import get_int

        client = self._client()
        client.post("/admin/api/settings", json={"settings": {"AUTO_OUTREACH_MAX_PER_RUN": "10"}})
        response = client.post(
            "/admin/api/settings",
            json={"settings": {"AUTO_OUTREACH_MAX_PER_RUN": "50", "AUTO_OUTREACH_HOURS": "nope"}},
        )
        self.assertEqual(400, response.status_code)
        # The valid half of the batch must not have been written.
        self.assertEqual(10, get_int("AUTO_OUTREACH_MAX_PER_RUN", 25))

    def test_settings_outside_the_registry_are_refused(self):
        response = self._client().post(
            "/admin/api/settings", json={"settings": {"DATABASE_URL": "postgres://elsewhere"}}
        )
        self.assertEqual(400, response.status_code)
        self.assertIn("not editable", response.json()["detail"])


class PasswordChangeTests(unittest.TestCase):
    def setUp(self):
        from services import content_store as cs

        cs.set_admin_password("original-password")

    def tearDown(self):
        from services import content_store as cs

        cs.set_admin_password("p4-secret")

    def _client(self, password="original-password"):
        from fastapi.testclient import TestClient

        import main

        client = TestClient(main.app, base_url="https://testserver")
        client.post("/admin/login", data={"password": password}, follow_redirects=False)
        return client

    def test_wrong_current_password_is_rejected(self):
        response = self._client().post(
            "/admin/api/password", json={"current": "not-it", "new": "a-long-new-password"}
        )
        self.assertEqual(401, response.status_code)

    def test_short_password_is_rejected(self):
        response = self._client().post(
            "/admin/api/password", json={"current": "original-password", "new": "short"}
        )
        self.assertEqual(400, response.status_code)

    def test_changing_the_password_takes_effect(self):
        from services.admin_auth import check_password

        response = self._client().post(
            "/admin/api/password",
            json={"current": "original-password", "new": "a-much-longer-password"},
        )
        self.assertEqual(200, response.status_code, response.text)
        self.assertTrue(check_password("a-much-longer-password"))
        self.assertFalse(check_password("original-password"))


if __name__ == "__main__":
    unittest.main()
