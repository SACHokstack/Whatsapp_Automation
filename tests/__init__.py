"""Automated tests for the Timmins WhatsApp service.

Test isolation lives here rather than in `conftest.py` because conftest is a pytest feature and
CI runs `python -m unittest discover`. Both runners import this package before any test module,
so putting the setup here is what makes the two runners behave the same. Without it, unittest
runs against the real bot database and the real vector index, and roughly a dozen tests fail for
reasons that have nothing to do with the code under test.

The per-turn LLM interpreter is ON in production but must never make network calls during the
suite, so it defaults OFF here; `test_interpret.py` opts back in with a mocked client.
"""

import atexit
import os
import shutil
import tempfile
from pathlib import Path

os.environ.setdefault("USE_INTERPRETER", "false")

# Runtime modules read these paths during import. Set them before any test module is imported
# so tests can never rebuild the production index or write to the real bot database.
_TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="timmins-tests-"))
os.environ["RAG_V2_INDEX"] = str(_TEST_DATA_DIR / "rag.sqlite")
os.environ["WHATSAPP_DB_PATH"] = str(_TEST_DATA_DIR / "whatsapp.sqlite")
os.environ["RAG_TRACE_PATH"] = str(_TEST_DATA_DIR / "rag-traces.jsonl")
atexit.register(shutil.rmtree, _TEST_DATA_DIR, ignore_errors=True)
