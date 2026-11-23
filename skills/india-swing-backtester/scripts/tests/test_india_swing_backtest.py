"""CLI, data loading, index backfill and live-screener parity tests (all offline)."""

from __future__ import annotations

import json
import math
import sys
from datetime import date, datetime, timedelta, timezone

import _india_data as india
import india_swing_backtest as cli
import pytest
import swing_backtest_engine as engine
from swing_backtest_siblings import load_all

SCREENER, PLANNER, PAPER = load_all()
IST = timezone(timedelta(hours=5, minutes=30))
AS_OF = date(2026, 3, 31)


def weekdays(start, end):
    out, day = [], start
    while day <= end:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def make_bars(n=300, start_price=200.0, drift=0.003, volume=1_000_000, wiggle=0.012, phase=0.0):
    days = weekdays(AS_OF - timedelta(days=n * 2), AS_OF)[-n:]
    bars, price = [], start_price
    for i, day in enumerate(days):
        price *= 1 + drift + wiggle * math.sin(i / 3.0 + phase) * 0.5
        bars.append(
            {
                "date": day.isoformat(),
                "open": round(price * 0.995, 2),
                "high": round(price * 1.012, 2),
                "low": round(price * 0.985, 2),
                "close": round(price, 2),
                "volume": volume,
            }
        )
    return bars


STOCKS = {
    "LEADER": make_bars(),
    "SECOND": make_bars(start_price=300, drift=0.0025, phase=1.0),
    "THIRD": make_bars(start_price=150, drift=0.0035, wiggle=0.02, phase=2.0),
    "LAGGARD": make_bars(start_price=900, drift=-0.003),
}
BENCH = make_bars(start_price=20000, drift=0.001, volume=0)


@pytest.fixture(autouse=True)
def offline_universe(monkeypatch):
    def fake_load(key, cache_root, **kwargs):
        raise india.IndiaDataError(f"offline: {key}")

    monkeypatch.setattr(india, "load_universe", fake_load)


class FakeCache:
    _sessions_fn = staticmethod(weekdays)

    def stock_bars(self, symbol, start, end, adjusted=True):
        bars = [b for b in STOCKS[symbol] if start.isoformat() <= b["date"] <= end.isoformat()]
        return {"symbol": symbol, "bars": bars, "warnings": [], "actions": []}

    def index_bars(self, name, start, end):
        return {
            "index": name,
            "bars": [b for b in BENCH if start.isoformat() <= b["date"] <= end.isoformat()],
        }


# --------------------------------------------------------------------------- parity


@pytest.mark.parametrize("day", [AS_OF, AS_OF - timedelta(days=7)])  # two trading days
def test_screen_day_matches_live_screener(tmp_path, day):
    """The backtest's screen of a past day equals india-vcp-screener run on that day."""
    now = datetime.combine(day, datetime.min.time(), tzinfo=IST) + timedelta(hours=20)
    symbols = sorted(STOCKS)
    assert (
        SCREENER.main(
            [
                "--symbols",
                *symbols,
                "--output-dir",
                str(tmp_path),
                "--cache-dir",
                str(tmp_path / "c"),
            ],
            cache=FakeCache(),
            now_fn=lambda: now,
        )
        == 0
    )
    live = json.loads(next(tmp_path.glob("india_vcp_screener_*.json")).read_text())
    assert live["results"], "fixture should produce candidates"
    sargs = SCREENER.parse_arguments(["--symbols", *symbols])
    rows, _ = SCREENER.load_universe_rows(sargs, tmp_path / "c")
    data = engine.MarketData(
        rows, {s: engine.Series(b) for s, b in STOCKS.items()}, engine.Series(BENCH), "NIFTY 500"
    )
    ours = engine.screen_day(day.isoformat(), data, SCREENER, sargs, top=20)
    assert ours["results"] == [engine.slim_result(r) for r in live["results"]]
    assert ours["funnel"]["vcp_candidates"] == live["metadata"]["funnel"]["vcp_candidates"]


# --------------------------------------------------------------------------- CLI


def write_cache(root, stocks=STOCKS, bench=BENCH):
    for sym, bars in stocks.items():
        path = root / "stocks" / f"{sym}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "bars": bars,
                    "actions": [],
                    "coverage_start": bars[0]["date"],
                    "coverage_end": bars[-1]["date"],
                }
            )
        )
    path = root / "indices" / "NIFTY_500.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"bars": bench, "coverage_start": bench[0]["date"], "coverage_end": bench[-1]["date"]}
        )
    )


def run_cli(tmp_path, *extra, data_start="2025-01-01"):
    root = tmp_path / "cache"
    write_cache(root)
    out = tmp_path / "out"
    code = cli.main(
        [
            "--symbols",
            *sorted(STOCKS),
            "--no-fetch",
            "--cache-dir",
            str(root),
            "--data-start",
            data_start,
            "--end",
            AS_OF.isoformat(),
            "--output-dir",
            str(out),
            *extra,
        ],
        now_fn=lambda: datetime(2026, 4, 1, 12, tzinfo=timezone.utc),
    )
    return code, out, root


def test_cli_end_to_end(tmp_path, capsys):
    code, out, root = run_cli(tmp_path, "--sweep", "target_r_multiple=2,3", "max_risk_pct=8,12")
    assert code == 0
    report = json.loads(next(out.glob("india_swing_backtest_*.json")).read_text())
    assert report["skill"] == "india-swing-backtester"
    assert report["window"]["sessions"] > 10
    assert len(report["sweep"]) == 4
    assert report["base"]["summary"]["benchmark"]["name"] == "NIFTY 500"
    md = next(out.glob("india_swing_backtest_*.md")).read_text()
    assert md.startswith("# India VCP Breakout Backtest") and "Parameter sweep" in md
    assert "(live planner rules)" in md
    assert next(out.glob("*_equity.csv")).read_text().startswith("date,equity")
    assert len(list((root / "backtest").glob("vcp_screens_*.jsonl"))) == 1
    assert "India VCP Breakout Backtest" in capsys.readouterr().out
    # second run reuses the screen cache
    assert run_cli(tmp_path)[0] == 0


def test_cli_notes_missing_index_backfill(tmp_path):
    code, out, _ = run_cli(tmp_path, data_start="2024-12-01")
    assert code == 0
    report = json.loads(next(out.glob("india_swing_backtest_*.json")).read_text())
    assert any("NIFTY 500 history starts" in n for n in report["data_notes"])


def test_cli_research_variant_is_labelled(tmp_path):
    code, out, _ = run_cli(tmp_path, "--max-risk-pct", "12", "--min-score", "65")
    assert code == 0
    assert "research variant" in next(out.glob("india_swing_backtest_*.md")).read_text()


@pytest.mark.parametrize(
    "extra, message",
    [
        (["--max-risk-pct", "0"], "max_risk_pct"),
        (["--sweep", "min_score=50"], "min_score"),
        (["--sweep", "bogus=1"], "cannot sweep"),
        (["--capital", "0"], "capital"),
        (["--start", "2027-01-01"], "no NSE sessions"),
    ],
)
def test_cli_rejects_bad_input(tmp_path, capsys, extra, message):
    code, _, _ = run_cli(tmp_path, *extra)
    assert code == 1
    assert message in capsys.readouterr().err


def test_cli_needs_enough_history(tmp_path, capsys):
    root = tmp_path / "cache"
    write_cache(root, {k: v[-100:] for k, v in STOCKS.items()}, BENCH[-100:])
    code = cli.main(
        [
            "--symbols",
            "LEADER",
            "--no-fetch",
            "--cache-dir",
            str(root),
            "--end",
            AS_OF.isoformat(),
            "--output-dir",
            str(tmp_path),
        ]
    )
    assert code == 1
    assert "need more history" in capsys.readouterr().err


def test_missing_symbol_is_reported(tmp_path):
    root = tmp_path / "cache"
    write_cache(root)
    data, missing = cli.load_market_data(
        [{"symbol": "LEADER"}, {"symbol": "NOPE"}],
        "NIFTY 500",
        "2025-01-01",
        AS_OF.isoformat(),
        root,
        no_fetch=True,
    )
    assert set(data.stocks) == {"LEADER"}
    assert any(m.startswith("NOPE") for m in missing)


def test_cache_only_adjusts_splits(tmp_path):
    root = tmp_path / "cache"
    bars = [
        {"date": "2026-01-05", "open": 200, "high": 200, "low": 200, "close": 200, "volume": 10},
        {"date": "2026-01-06", "open": 100, "high": 100, "low": 100, "close": 100, "volume": 20},
    ]
    path = root / "stocks" / "SPLIT.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "bars": bars,
                "coverage_end": "2026-01-06",
                "actions": [{"ex_date": "2026-01-06", "action_type": "SPLIT", "factor": 0.5}],
            }
        )
    )
    series = cli._cache_only_series(root, "stocks", "SPLIT", "2026-01-01", "2026-01-31")
    assert [b["close"] for b in series.bars] == [100, 100]
    assert series.warnings == []


# --------------------------------------------------------------------------- index backfill


NSE = [
    {"date": d, "close": 100.0 + i} for i, d in enumerate(f"2025-01-{k:02d}" for k in range(1, 29))
]


def fake_downloader(shift=0.0):
    def download(ticker, start, end):
        early = [
            {
                "date": f"2024-12-{k:02d}",
                "open": 90,
                "high": 91,
                "low": 89,
                "close": 90.0,
                "volume": 0,
            }
            for k in range(2, 32)
        ]
        return early + [
            {
                **b,
                "open": b["close"],
                "high": b["close"],
                "low": b["close"],
                "close": b["close"] * (1 + shift),
                "volume": 0,
            }
            for b in NSE
        ]

    return download


def test_backfill_verified_and_cached(tmp_path):
    bars = cli.index_backfill(
        "NIFTY 500", "2024-12-01", NSE, tmp_path, no_fetch=False, downloader=fake_downloader()
    )
    assert bars and all(b["date"] < "2025-01-01" for b in bars)
    cached = json.loads((tmp_path / "backtest" / "index_backfill_NIFTY_500.json").read_text())
    assert cached["ticker"] == "^CRSLDX" and cached["verified_sessions"] == len(NSE)
    again = cli.index_backfill("NIFTY 500", "2024-12-01", NSE, tmp_path, no_fetch=True)
    assert again == bars


@pytest.mark.parametrize(
    "benchmark, downloader, no_fetch, message",
    [
        ("NIFTY 500", fake_downloader(shift=0.01), False, "differs from NSE"),
        ("NIFTY 500", None, True, "no cached"),
        ("NIFTY IT", None, False, "no Yahoo Finance ticker"),
        ("NIFTY 500", lambda *a: [], False, "only 0 overlapping"),
    ],
)
def test_backfill_refusals(tmp_path, benchmark, downloader, no_fetch, message):
    with pytest.raises(cli.BackfillUnavailable, match=message):
        cli.index_backfill(
            benchmark, "2024-12-01", NSE, tmp_path, no_fetch=no_fetch, downloader=downloader
        )


def test_without_yfinance_the_window_starts_later(monkeypatch):
    monkeypatch.setitem(sys.modules, "yfinance", None)
    with pytest.raises(cli.BackfillUnavailable, match="yfinance is not installed"):
        cli._yf_download("^CRSLDX", "2024-01-01", "2025-02-01")


def test_format_inr():
    assert cli.format_inr(1017329.06) == "₹10,17,329.06"
    assert cli.format_inr(-500) == "-₹500.00"
    assert cli.format_inr(None) == "—"
