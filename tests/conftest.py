"""Test isolation defaults.

The per-turn LLM interpreter is ON in production but must never make network calls
during the test suite. Default it OFF so existing tests exercise the deterministic
fallback path; `test_interpret.py` opts back in with a mocked Groq client.
"""

import atexit
import os
import shutil
import tempfile
from pathlib import Path

os.environ.setdefault("USE_INTERPRETER", "false")

# Runtime modules read these paths during import. Set them before test collection so
# tests can never rebuild the production index or write to the real bot database.
_TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="timmins-tests-"))
os.environ["RAG_V2_INDEX"] = str(_TEST_DATA_DIR / "rag.sqlite")
os.environ["WHATSAPP_DB_PATH"] = str(_TEST_DATA_DIR / "whatsapp.sqlite")
os.environ["RAG_TRACE_PATH"] = str(_TEST_DATA_DIR / "rag-traces.jsonl")
atexit.register(shutil.rmtree, _TEST_DATA_DIR, ignore_errors=True)
