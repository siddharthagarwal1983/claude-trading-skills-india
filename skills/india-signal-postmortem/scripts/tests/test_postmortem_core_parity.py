"""postmortem_core must stay a verbatim copy of the US signal-postmortem logic."""

from __future__ import annotations

import ast
from pathlib import Path

import postmortem_core
import pytest

US_DIR = Path(__file__).resolve().parents[3] / "signal-postmortem" / "scripts"


def _functions(path: Path) -> dict:
    source = path.read_text(encoding="utf-8")
    return {
        node.name: ast.get_source_segment(source, node)
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef)
    }


CASES = [("postmortem_recorder.py", n) for n in postmortem_core.RECORDER_FUNCTIONS] + [
    ("postmortem_analyzer.py", n) for n in postmortem_core.ANALYZER_FUNCTIONS
]


@pytest.mark.skipif(not US_DIR.exists(), reason="US skill not installed alongside")
@pytest.mark.parametrize("filename, name", CASES)
def test_function_matches_us_source(filename, name):
    us = _functions(US_DIR / filename)
    ours = _functions(Path(postmortem_core.__file__))
    assert name in us, f"{name} no longer exists in {filename}"
    assert ours[name] == us[name], f"{name} drifted from {filename}; re-copy it"


@pytest.mark.parametrize(
    "ret, signal_regime, exit_regime, expected",
    [
        (0.03, "UNKNOWN", "UNKNOWN", "TRUE_POSITIVE"),
        (-0.01, "UNKNOWN", "UNKNOWN", "FALSE_POSITIVE"),
        (-0.03, "UNKNOWN", "UNKNOWN", "FALSE_POSITIVE_SEVERE"),
        (0.002, "UNKNOWN", "UNKNOWN", "NEUTRAL"),
        (-0.03, "NEW_ENTRY_ALLOWED", "CASH_PRIORITY", "REGIME_MISMATCH"),
    ],
)
def test_classification_thresholds(ret, signal_regime, exit_regime, expected):
    assert postmortem_core.classify_outcome("LONG", ret, signal_regime, exit_regime) == expected
