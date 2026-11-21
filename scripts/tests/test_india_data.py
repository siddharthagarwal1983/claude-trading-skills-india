"""Run the India data contract suite against the canonical source module.

The tests live in scripts/india_data/test_india_data_contract.py.tmpl so that the
canonical module and every vendored ``_india_data.py`` copy run the same suite.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_SOURCE = REPO_ROOT / "scripts" / "india_data" / "india_data.py"
_SUITE = REPO_ROOT / "scripts" / "india_data" / "test_india_data_contract.py.tmpl"

_spec = importlib.util.spec_from_file_location("_india_data", _SOURCE)
assert _spec is not None and _spec.loader is not None
_module = importlib.util.module_from_spec(_spec)
sys.modules["_india_data"] = _module
_spec.loader.exec_module(_module)

exec(compile(_SUITE.read_text(encoding="utf-8"), str(_SUITE), "exec"), globals())  # noqa: S102
