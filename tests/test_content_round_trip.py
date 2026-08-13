"""Files -> database -> files must come back to the same content.

This is the guarantee that moving content into the database is not a one-way door. If the
export can be re-imported to identical content, then the database can always be turned back
into the plain files the bot originally read — which is the disaster recovery story, the backup
format, and the reason nobody is locked into this deployment to get their own content out.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _clear_reader_caches():
    import services.course_loader as cl
    import services.knowledge_base as kb
    import services.structured_facts as sf

    cl._load_courses_cached.cache_clear()
    kb._load_knowledge_base_cached.cache_clear()
    sf._load_policies_cached.cache_clear()


class RoundTripTests(unittest.TestCase):
    def setUp(self):
        from services import content_store

        self.dir = tempfile.mkdtemp(prefix="round-trip-")
        self._env = unittest.mock.patch.dict(
            os.environ,
            {"WHATSAPP_DB_PATH": str(Path(self.dir) / "content.sqlite"), "CONTENT_SOURCE": "files"},
        )
        self._env.start()
        self.addCleanup(self._env.stop)
        content_store._schema_ready = False
        self.addCleanup(setattr, content_store, "_schema_ready", False)
        self.addCleanup(_clear_reader_caches)
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        _clear_reader_caches()

    def _snapshot(self):
        """What the bot reads, under whatever CONTENT_SOURCE is currently set."""
        import dataclasses

        import services.course_loader as cl
        import services.knowledge_base as kb
        import services.structured_facts as sf

        _clear_reader_caches()
        return (
            {slug: dataclasses.asdict(c) for slug, c in cl.load_courses().items()},
            dict(kb.load_knowledge_base()),
            sf.load_policies(),
        )

    def _import_from(self, root: Path):
        """Run the file loaders against `root` and write what they produce into the DB."""
        from services import content_store as cs

        # _ROOT is patched as well because the loader fingerprints its cache key on paths
        # relative to it, which fails outright for a directory outside the repo.
        with unittest.mock.patch.dict(os.environ, {"CONTENT_SOURCE": "files"}):
            with (
                unittest.mock.patch("services.course_loader._ROOT", root),
                unittest.mock.patch("services.course_loader._COURSES_DIR", root / "courses"),
            ):
                with unittest.mock.patch(
                    "services.knowledge_base._KNOWLEDGE_DIR", root / "knowledge"
                ):
                    with unittest.mock.patch(
                        "services.structured_facts._POLICY_PATH",
                        root / "knowledge" / "policies.yaml",
                    ):
                        _clear_reader_caches()
                        snapshot = self._snapshot()
                        courses, knowledge, policies = snapshot
                        for slug, course in courses.items():
                            cs.upsert_course(slug, course)
                        for topic, body in knowledge.items():
                            cs.upsert_knowledge(topic, body)
                        if policies:
                            cs.set_policies(policies)
                        cs.bump_content_version()
                        return snapshot

    def test_export_can_be_reimported_to_identical_content(self):
        original = self._import_from(REPO)

        export_dir = Path(self.dir) / "export"
        result = subprocess.run(
            [
                sys.executable,
                str(REPO / "scripts" / "export_content_from_db.py"),
                "--out",
                str(export_dir),
            ],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": str(REPO)},
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue((export_dir / "courses").is_dir())
        self.assertTrue((export_dir / "knowledge" / "policies.yaml").is_file())

        # Re-read from the exported files: they must describe the same content as the originals.
        reimported = self._import_from(export_dir)
        self.assertEqual(original[0], reimported[0], "courses changed across the round trip")
        self.assertEqual(original[1], reimported[1], "knowledge changed across the round trip")
        self.assertEqual(original[2], reimported[2], "policies changed across the round trip")

    def test_export_refuses_when_there_is_nothing_to_export(self):
        result = subprocess.run(
            [
                sys.executable,
                str(REPO / "scripts" / "export_content_from_db.py"),
                "--out",
                str(Path(self.dir) / "empty"),
            ],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": str(REPO)},
        )
        self.assertEqual(1, result.returncode)
        self.assertIn("no content", result.stderr)


class ImporterGuardTests(unittest.TestCase):
    """The importer must not quietly write to a local file when Postgres was intended."""

    def test_refuses_sqlite_unless_asked(self):
        env = {**os.environ, "PYTHONPATH": str(REPO)}
        env.pop("DATABASE_URL", None)
        result = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "import_content_to_db.py"), "--dry-run"],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(2, result.returncode)
        self.assertIn("Refusing to run", result.stderr)

    def test_target_description_never_prints_credentials(self):
        sys.path.insert(0, str(REPO))
        with unittest.mock.patch.dict(
            os.environ, {"DATABASE_URL": "postgresql://user:hunter2@db.example.com:5432/railway"}
        ):
            import importlib

            module = importlib.import_module("scripts.import_content_to_db")
            described = module._describe_target()
        self.assertNotIn("hunter2", described)
        self.assertIn("db.example.com", described)


if __name__ == "__main__":
    unittest.main()
