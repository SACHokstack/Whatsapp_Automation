"""Pytest entry point for the shared test isolation.

The isolation itself lives in `tests/__init__.py` so that `unittest discover` — what CI runs —
gets it too. Importing the package here means pytest applies it at conftest time, before it
collects and imports any test module.
"""

import tests  # noqa: F401  (imported for its import-time isolation setup)
