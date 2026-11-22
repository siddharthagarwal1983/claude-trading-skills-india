"""Tests for the India exposure coach."""

import json
from datetime import date, datetime, timedelta, timezone

import _india_data as india
import calculate_india_exposure as coach
import pytest

IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 11, 21, 10, 0, tzinfo=IST)
END = "2026-11-20"


def sessions(start, end):
    out, day = [], start
    while day <= end:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def index(n=300, drift=0.001, start=20000.0, pe=20.0, pe_drift=0.0, end=date(2026, 11, 20)):
    days = sessions(end - timedelta(days=n * 2), end)[-n:]
    out, price = [], start
    for i, d in enumerate(days):
        price *= 1 + drift
        out.append({"date": d.isoformat(), "close": round(price, 2), "pe": pe + pe_drift * i})
    return out


class FakeCache:
    def __init__(self, nifty=None, vix=None):
        self.series = {"NIFTY 50": nifty, "INDIA VIX": vix}
        self._sessions_fn = sessions

    def index_bars(self, name, start, end):
        if self.series.get(name) is None:
            raise india.SymbolNotFoundError(f"no {name}")
        return {"bars": self.series[name]}


def write(tmp_path, name, payload):
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def breadth(score=75, p50=62, p200=66, as_of=END):
    return {
        "as_of": as_of,
        "composite": {"composite_score": score, "zone": "Healthy"},
        "latest": {"pct_above_50": p50, "pct_above_200": p200},
        "coverage": {"verdict": "complete"},
    }


def distribution(level="NORMAL", as_of=END):
    return {"market_distribution_state": {"as_of": as_of, "overall_risk_level": level}}


def ftd(state="NO_SIGNAL", total=0, as_of=END, gap=False):
    return {
        "metadata": {"expected_session": as_of},
        "market_state": {"combined_state": state},
        "quality_score": {"total_score": total},
        "data_gap": gap,
    }


def run(tmp_path, *extra, cache=None):
    cache = cache or FakeCache(index(), [{"date": d["date"], "close": 14.0} for d in index(30)])
    return coach.main(
        ["--output-dir", str(tmp_path / "out"), *extra], cache=cache, now_fn=lambda: NOW
    )


def result(tmp_path):
    return json.loads(
        next((tmp_path / "out").glob("india_exposure_posture_*.json")).read_text(encoding="utf-8")
    )


# --------------------------------------------------------------------------- extractors


def test_ftd_counts_only_during_a_correction():
    assert coach.extract_ftd(ftd("NO_SIGNAL"))[0] is None
    assert coach.extract_ftd(ftd("FTD_CONFIRMED", 82))[0] == 82
    assert coach.extract_ftd(ftd("RALLY_ATTEMPT"))[0] == 40
    assert coach.extract_ftd(ftd("FTD_CONFIRMED", 90, gap=True))[0] is None


def test_distribution_and_breadth_extractors():
    assert coach.extract_distribution(distribution("SEVERE"))[0] == 15
    assert coach.extract_distribution(distribution("UNKNOWN"))[0] is None
    score, detail = coach.extract_breadth(breadth(71.6))
    assert score == 72 and detail["pct_above_200"] == 66
    blocked = breadth()
    blocked["coverage"]["verdict"] = "not_evaluable"
    assert coach.extract_breadth(blocked)[0] is None


@pytest.mark.parametrize(
    "fii,dii,expected",
    [(100, 50, 80), (-50, 120, 60), (-100, -20, 20), (-200, 100, 40)],
)
def test_flow_scoring(fii, dii, expected):
    rows = [
        {"date": f"2026-11-{d:02d}", "FII_net_cr": fii / 10, "dii_net_cr": dii / 10}
        for d in range(1, 11)
    ]
    score, detail = coach.extract_flows({"rows": rows})
    assert score == expected and detail["sessions"] == 10


def test_flow_rows_accept_nested_shapes_and_skip_bad_rows():
    data = {
        "data": {
            "flows": [
                {"date": "2026-11-02T00:00", "fii_net": 1, "dii_net": 2},
                {"date": "x"},
                {"fii_net": 3},
            ]
        }
    }
    assert coach.flow_rows(data) == [{"date": "2026-11-02", "fii_net_cr": 1.0, "dii_net_cr": 2.0}]
    assert coach.extract_flows({"rows": []}) == (None, {})


def test_trend_volatility_valuation_scores():
    up = [100 + i for i in range(220)]
    assert coach.trend_score(up)[0] == 85
    assert coach.trend_score([300 - i for i in range(220)])[0] == 15
    assert coach.trend_score(up[:50])[0] is None
    assert coach.volatility_score([13.0] * 6)[0] == 75
    spike = coach.volatility_score([12.0] * 5 + [26.0])
    assert spike[0] == 0 and spike[1]["change_5d_pct"] > 100
    assert coach.valuation_score([20.0] * 100)[0] is None
    assert (
        coach.valuation_score([float(i) for i in range(1, 201)])[0] == 30
    )  # at the top of its range


def test_composite_haircut_and_decisions():
    composite, provided, missing = coach.calculate_composite({"breadth": 80, "trend": 80})
    assert composite == pytest.approx(70.0)  # distribution (critical) missing: -10
    assert "distribution" in missing and provided == ["breadth", "trend"]
    assert coach.calculate_composite({})[0] == 50.0
    assert coach.determine_recommendation(70, 15, 0) == "CASH_PRIORITY"
    assert coach.determine_recommendation(45, 85, 0) == "REDUCE_ONLY"
    assert coach.determine_recommendation(70, 85, 2) == "REDUCE_ONLY"
    assert coach.determine_recommendation(70, 85, 0) == "NEW_ENTRY_ALLOWED"
    assert [coach.determine_exposure_ceiling(x) for x in (90, 70, 55, 40, 25, 10)] == [
        100,
        76,
        56,
        36,
        16,
        5,
    ]
    assert coach.determine_participation({"pct_above_50": 60, "pct_above_200": 65}) == "BROAD"
    assert coach.determine_participation({"pct_above_50": 30, "pct_above_200": 45}) == "MODERATE"
    assert coach.determine_participation({}) == "UNKNOWN"


# --------------------------------------------------------------------------- CLI


def test_healthy_market_allows_new_entries(tmp_path, capsys):
    rc = run(
        tmp_path,
        "--breadth",
        write(tmp_path, "b.json", breadth()),
        "--distribution",
        write(tmp_path, "d.json", distribution()),
        "--ftd",
        write(tmp_path, "f.json", ftd()),
    )
    assert rc == 0
    out = result(tmp_path)
    assert out["recommendation"] == "NEW_ENTRY_ALLOWED"
    assert out["participation"] == "BROAD"
    assert out["components"]["ftd"]["score"] is None  # uptrend: FTD not applicable
    assert out["market_context"]["india_vix"] == 14.0
    assert out["confidence"] == "MEDIUM"
    md = next((tmp_path / "out").glob("*.md")).read_text(encoding="utf-8")
    assert "India Market Posture (NSE)" in md and "not a buy or sell signal" in md
    assert "NEW_ENTRY_ALLOWED" in capsys.readouterr().out


def test_severe_distribution_forces_cash_priority(tmp_path):
    run(
        tmp_path,
        "--breadth",
        write(tmp_path, "b.json", breadth(30, 20, 25)),
        "--distribution",
        write(tmp_path, "d.json", distribution("SEVERE")),
    )
    assert result(tmp_path)["recommendation"] == "CASH_PRIORITY"


def test_stale_inputs_are_ignored_and_reported(tmp_path, capsys):
    run(
        tmp_path,
        "--breadth",
        write(tmp_path, "b.json", breadth(as_of="2026-10-01")),
        "--distribution",
        write(tmp_path, "d.json", distribution(as_of="2026-12-01")),
    )
    out = result(tmp_path)
    assert out["stale_inputs"] == ["breadth (2026-10-01)", "distribution (2026-12-01)"]
    assert out["components"]["breadth"]["score"] is None
    assert "Stale inputs ignored" in capsys.readouterr().out


def test_glob_picks_newest_and_unreadable_input_warns(tmp_path, capsys):
    write(tmp_path, "india_market_breadth_2026-11-19_100000.json", breadth(20))
    write(tmp_path, "india_market_breadth_2026-11-20_200000.json", breadth(90))
    bad = tmp_path / "bad.json"
    bad.write_text("{oops", encoding="utf-8")
    run(
        tmp_path,
        "--breadth",
        str(tmp_path / "india_market_breadth_*.json"),
        "--distribution",
        str(bad),
    )
    assert result(tmp_path)["components"]["breadth"]["score"] == 90
    assert "could not read" in capsys.readouterr().err


def test_no_fetch_and_missing_context(tmp_path):
    run(tmp_path, "--no-fetch")
    out = result(tmp_path)
    assert out["components"]["trend"]["score"] is None and out["confidence"] == "LOW"
    run(tmp_path / "b", cache=FakeCache(None, None))
    ctx = result(tmp_path / "b")["market_context"]
    assert "NIFTY 50" in ctx["nifty_error"] and "INDIA VIX" in ctx["vix_error"]


def test_as_of_validation(tmp_path, capsys):
    assert run(tmp_path, "--as-of", "2026-12-25") == 1
    assert "after the latest" in capsys.readouterr().err
    assert run(tmp_path, "--as-of", "2026-11-15") == 0
    assert result(tmp_path)["as_of"] == "2026-11-13"
