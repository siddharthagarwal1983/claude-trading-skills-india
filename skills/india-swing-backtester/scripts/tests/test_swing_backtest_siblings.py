"""Sibling skill loading."""

import pytest
import swing_backtest_siblings as sib


def test_siblings_load():
    screener, planner, paper = sib.load_all()
    assert hasattr(screener, "run_funnel") and hasattr(screener, "prepare_history")
    assert hasattr(planner, "generate_plans") and hasattr(planner, "MAX_RISK_PCT")
    assert hasattr(paper, "entry_fill") and hasattr(paper, "exit_fill")


def test_missing_sibling(monkeypatch, tmp_path):
    monkeypatch.setenv("INDIA_PAPER_LEDGER_DIR", str(tmp_path))
    monkeypatch.delitem(sib._LOADED, "paper", raising=False)
    with pytest.raises(sib.SiblingMissingError, match="INDIA_PAPER_LEDGER_DIR"):
        sib.load("paper")
    assert sib.sibling_dir("paper") == tmp_path
