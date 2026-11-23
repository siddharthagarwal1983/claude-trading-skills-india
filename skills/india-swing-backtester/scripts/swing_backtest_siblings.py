"""Load the sibling India skills the backtest replays (screeners, planner, paper fills).

The backtest must place exactly the trades the live workflow would, so it
imports the code instead of copying it:

* india-vcp-screener ``screen_india_vcp`` — ``prepare_history`` / ``run_funnel``
* india-stockbee-momentum-burst-screener ``screen_india_momentum_burst`` — ``analyze_symbol``
* india-breakout-trade-planner ``plan_india_breakout_trades`` — ``generate_plans``
* india-paper-ledger ``paper_engine`` — fill rules, slippage and delivery charges

Each skill must be installed next to this one (``skills/<id>/scripts``) or its
scripts directory given in the matching environment variable. The sibling
directories are appended to ``sys.path`` after this skill's own directory, so
their ``import _india_data`` resolves to this skill's vendored copy, which the
contract test keeps identical to theirs.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

SKILLS_ROOT = Path(__file__).resolve().parents[2]
SIBLINGS = {
    "screener": ("INDIA_VCP_SCREENER_DIR", "india-vcp-screener", "screen_india_vcp"),
    "planner": (
        "INDIA_BREAKOUT_PLANNER_DIR",
        "india-breakout-trade-planner",
        "plan_india_breakout_trades",
    ),
    "paper": ("INDIA_PAPER_LEDGER_DIR", "india-paper-ledger", "paper_engine"),
    "momentum": (
        "INDIA_MOMENTUM_BURST_DIR",
        "india-stockbee-momentum-burst-screener",
        "screen_india_momentum_burst",
    ),
}
_LOADED: dict = {}


class SiblingMissingError(RuntimeError):
    """A sibling India skill the backtest needs is not installed."""


def sibling_dir(name: str) -> Path:
    env, skill, _module = SIBLINGS[name]
    override = os.environ.get(env)
    return Path(override) if override else SKILLS_ROOT / skill / "scripts"


def load(name: str):
    """Import and cache one sibling module ('screener', 'planner', 'paper' or 'momentum')."""
    if name in _LOADED:
        return _LOADED[name]
    env, skill, module_name = SIBLINGS[name]
    directory = sibling_dir(name)
    if not (directory / f"{module_name}.py").exists():
        raise SiblingMissingError(
            f"{skill} not found at {directory}. Install it next to india-swing-backtester "
            f"or set {env} to its scripts directory."
        )
    own = str(Path(__file__).resolve().parent)
    if own not in sys.path:
        sys.path.insert(0, own)
    if str(directory) not in sys.path:
        sys.path.append(str(directory))
    module = importlib.import_module(module_name)
    _LOADED[name] = module
    return module


def load_all() -> tuple:
    return load("screener"), load("planner"), load("paper")
