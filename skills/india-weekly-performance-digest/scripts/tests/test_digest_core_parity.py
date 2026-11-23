"""digest_core must stay a verbatim copy of the US weekly digest metrics."""

from __future__ import annotations

import ast
from pathlib import Path

import digest_core
import pytest

US_SCRIPT = (
    Path(__file__).resolve().parents[3]
    / "weekly-performance-digest"
    / "scripts"
    / "generate_weekly_digest.py"
)


def _functions(path: Path) -> dict:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    return {
        node.name: ast.get_source_segment(source, node)
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }


@pytest.mark.skipif(not US_SCRIPT.exists(), reason="US skill not installed alongside")
@pytest.mark.parametrize("name", digest_core.COPIED_FUNCTIONS)
def test_function_matches_us_source(name):
    us = _functions(US_SCRIPT)
    ours = _functions(Path(digest_core.__file__))
    assert name in us, f"{name} no longer exists in the US digest"
    assert ours[name] == us[name], f"{name} drifted from the US digest; re-copy it"


def test_source_skill_dimension_reads_resolved_signal_source():
    dims = dict(digest_core._PATTERN_DIMENSIONS)
    assert dims["by_source_skill"] == ("_india", "signal_source")
    thesis = {
        "_india": {"signal_source": "india-vcp-screener"},
        "origin": {"skill": "india-paper-ledger"},
        "outcome": {"pnl_dollars": 10.0},
    }
    patterns = digest_core.analyze_patterns([thesis])
    assert list(patterns["by_source_skill"]) == ["india-vcp-screener"]
