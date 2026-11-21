"""Tests for the India (NSE) Stockbee momentum burst screener."""

import json
from datetime import date, datetime, timedelta, timezone

import _india_data as india
import pytest
import screen_india_momentum_burst as mb

IST = timezone(timedelta(hours=5, minutes=30))
AS_OF = date(2026, 11, 20)  # Friday
NOW = datetime(2026, 11, 21, 10, 0, tzinfo=IST)


def weekdays(start, end):
    out, day = [], start
    while day <= end:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


@pytest.fixture(autouse=True)
def fake_calendar(monkeypatch):
    monkeypatch.setattr(mb, "SESSIONS_FN", weekdays)
    mb._SESSION_CACHE.clear()


def make_series(
    base=500.0,
    n=40,
    gain_pct=5.0,
    volume=300_000,
    trigger_volume=750_000,
    close_at_high=False,
    end=AS_OF,
):
    """Oldest-first bars: a tight base, then one trigger day."""
    days = weekdays(end - timedelta(days=n * 2), end)[-n:]
    rows = []
    for i, day in enumerate(days[:-1]):
        close = base * (1 + 0.002 * ((-1) ** i))
        rows.append(
            {
                "date": day.isoformat(),
                "open": close,
                "high": close * 1.005,
                "low": close * 0.995,
                "close": close,
                "volume": volume,
            }
        )
    prev = rows[-1]["close"]
    close = prev * (1 + gain_pct / 100)
    open_ = prev * 1.001
    rows.append(
        {
            "date": days[-1].isoformat(),
            "open": open_,
            "high": close if close_at_high else close * 1.003,
            "low": open_ * 0.995,
            "close": close,
            "volume": trigger_volume,
        }
    )
    return rows


def args_for(**overrides):
    args = mb.parse_arguments([])
    args._expected_session = AS_OF
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


def analyze(rows, **overrides):
    return mb.analyze_symbol("DEMO", mb.normalize_bars(rows), args_for(**overrides))


# --------------------------------------------------------------------------- calendar


def test_expected_session_uses_ist_cutoff():
    assert mb.resolve_expected_session(None, now=NOW) == AS_OF
    friday_afternoon = datetime(2026, 11, 20, 16, 0, tzinfo=IST)
    assert mb.resolve_expected_session(None, now=friday_afternoon) == date(2026, 11, 19)
    assert mb.resolve_expected_session("2026-11-15", now=NOW) == date(2026, 11, 13)  # Sunday -> Fri
    with pytest.raises(ValueError, match="NSE"):
        mb.resolve_expected_session("2026-11-23", now=NOW)


def test_bar_age_counts_nse_sessions():
    assert mb._bar_age(AS_OF, AS_OF) == 0
    assert mb._bar_age(date(2026, 11, 18), AS_OF) == 2
    assert mb._bar_age(date(2026, 11, 15), AS_OF) is None  # Sunday is not a session


# --------------------------------------------------------------------------- triggers


def test_four_pct_breakout_with_tight_base_is_a_candidate():
    result = analyze(make_series())
    assert "4pct_breakout" in result["trigger_tags"]
    assert result["state"] != "REJECTED", result["reject_reasons"]
    assert result["prior_base_days"] >= 3
    assert result["turnover_cr"] > 2


def test_rupee_breakout_needs_rupees_and_percent():
    cheap = analyze(make_series(base=500.0, gain_pct=6.0))
    assert "rupee_breakout" in cheap["trigger_tags"]  # ≈₹30 and ≈6%
    pricey = analyze(
        make_series(base=10_000.0, gain_pct=0.5, volume=200_000, trigger_volume=300_000)
    )
    assert "rupee_breakout" not in pricey["trigger_tags"]  # ≈₹40 but only 0.4% of open


def test_high_volume_alone_is_not_executable():
    result = analyze(make_series(gain_pct=0.5, trigger_volume=6_000_000, volume=6_000_000))
    assert "high_volume" in result["trigger_tags"]
    assert "no_momentum_burst_trigger" in result["reject_reasons"]


def test_turnover_gate_rejects_illiquid_names():
    result = analyze(make_series(base=60.0, volume=150_000, trigger_volume=200_000))
    assert "below_min_turnover" in result["reject_reasons"]
    assert result["state"] == "REJECTED"


@pytest.mark.parametrize(
    "close,high,gain,expected",
    [
        (110, 110, 10.0, True),
        (105, 105, 4.97, True),
        (107, 107, 7.0, False),
        (109, 110, 10.0, False),
    ],
)
def test_possible_upper_circuit(close, high, gain, expected):
    bar = mb.Bar(date="2026-11-20", open=100, high=high, low=99, close=close, volume=1)
    assert mb.possible_upper_circuit(bar, gain) is expected


def test_upper_circuit_close_is_never_actionable_day1():
    result = analyze(make_series(gain_pct=10.0, close_at_high=True), market_gate="allowed")
    assert "possible_upper_circuit" in result["soft_failure_tags"]
    assert result["possible_upper_circuit"] is True
    assert result["state"] != "ACTIONABLE_DAY1"


def test_stale_bars_are_rejected():
    result = analyze(make_series(end=AS_OF - timedelta(days=7)))
    assert result["reject_reasons"] == ["stale_price_data"]


def test_downstream_points_to_india_position_sizer():
    assert "india-position-sizer" in mb.downstream_action("ACTIONABLE_DAY1", "A")


# --------------------------------------------------------------------------- main


class FakeCache:
    def __init__(self, stocks):
        self.stocks = stocks

    def stock_bars(self, symbol, start, end, adjusted=True):
        value = self.stocks[symbol]
        if isinstance(value, Exception):
            raise value
        return {"symbol": symbol, "bars": value, "warnings": []}


def test_main_scans_symbols_and_records_fetch_errors(tmp_path, capsys):
    cache = FakeCache(
        {"BURST": make_series(), "GONE": india.SymbolNotFoundError("no history for GONE")}
    )
    rc = mb.main(
        [
            "--symbols",
            "BURST",
            "GONE",
            "--output-dir",
            str(tmp_path),
            "--cache-dir",
            str(tmp_path / "c"),
        ],
        cache=cache,
        now_fn=lambda: NOW,
    )
    assert rc == 0
    report = json.loads(
        next(tmp_path.glob("india_stockbee_momentum_burst_*.json")).read_text(encoding="utf-8")
    )
    assert report["skill"] == "india-stockbee-momentum-burst-screener"
    assert report["metadata"]["expected_session"] == AS_OF.isoformat()
    assert list(report["metadata"]["fetch_errors"]) == ["GONE"]
    assert [c["symbol"] for c in report["candidates"]] == ["BURST"]
    assert any("could not be loaded" in w for w in report["warnings"])
    md = next(tmp_path.glob("*.md")).read_text(encoding="utf-8")
    assert "NSE" in md and "₹" in md and "$" not in md


def test_main_offline_prices_json(tmp_path):
    prices = tmp_path / "prices.json"
    prices.write_text(json.dumps({"BURST": make_series()}), encoding="utf-8")
    rc = mb.main(
        ["--prices-json", str(prices), "--output-dir", str(tmp_path / "out")],
        cache=FakeCache({}),
        now_fn=lambda: NOW,
    )
    assert rc == 0
    report = json.loads(next((tmp_path / "out").glob("*.json")).read_text(encoding="utf-8"))
    assert report["metadata"]["input_mode"] == "prices_json"
    assert report["candidates"][0]["symbol"] == "BURST"


def test_main_without_inputs_is_an_error(tmp_path, capsys):
    rc = mb.main(["--output-dir", str(tmp_path)], cache=FakeCache({}), now_fn=lambda: NOW)
    assert rc == 1
    assert "No symbols" in capsys.readouterr().err
