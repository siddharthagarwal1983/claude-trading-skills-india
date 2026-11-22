"""Tests for India dividend parsing, adjustment, metrics, scoring and the CLI."""

import json
from datetime import date, datetime, timedelta, timezone

import _india_data as india
import dividend_calculator as dc
import pytest
import screen_india_dividends as cli

IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 11, 21, 10, 0, tzinfo=IST)
AS_OF = date(2026, 11, 20)


@pytest.mark.parametrize(
    "purpose,expected",
    [
        ("Dividend - Rs 6.75 Per Share /Special Dividend - Rs 2.75 Per Share", (6.75, 2.75)),
        ("Interim Dividend - Rs  6 Per Share", (6.0, 0.0)),
        ("Interim Dividend - Rs 9 Per Sh", (9.0, 0.0)),
        ("Interim Dividend - Re 1 Per Share", (1.0, 0.0)),
        (
            "Annual General Meeting/Special Dividend - Rs 8 Per Share /Dividend - Rs 20 Per Share",
            (20.0, 8.0),
        ),
        ("Interim Dividend- Rs.8 Per Share", (8.0, 0.0)),
        ("Buyback", (0.0, 0.0)),
    ],
)
def test_parse_dividend_amounts(purpose, expected):
    assert dc.parse_dividend_amounts(purpose) == expected


def test_fiscal_year_runs_april_to_march():
    assert dc.fiscal_year(date(2026, 3, 31)) == 2026
    assert dc.fiscal_year(date(2026, 4, 1)) == 2027


def act(day, kind, purpose="", factor=1.0):
    return {"ex_date": day, "action_type": kind, "purpose": purpose, "factor": factor}


def test_dividends_are_restated_after_splits_and_bonuses():
    events = dc.adjusted_dividends(
        [
            act("2026-01-21", "DIVIDEND", "Interim Dividend - Rs 23 Per Share"),
            act("2026-02-26", "SPLIT", "Face Value Split - From Rs 10/- To Re 1/-", 0.1),
            act("2026-03-27", "DIVIDEND", "Interim Dividend - Rs 1.75 Per Share"),
            act("2026-04-01", "OTHER", "Buyback"),
        ]
    )
    assert [(e["ex_date"], e["regular"], e["adjustment"]) for e in events] == [
        ("2026-01-21", 2.3, pytest.approx(0.1)),
        ("2026-03-27", 1.75, 1),
    ]
    assert events[0]["as_announced"] == {"regular": 23.0, "special": 0.0}


def test_metrics_yield_consistency_growth_and_cuts():
    events = [
        {"ex_date": "2022-07-01", "regular": 10.0, "special": 0.0},
        {"ex_date": "2023-07-01", "regular": 12.0, "special": 0.0},
        {"ex_date": "2024-07-01", "regular": 11.0, "special": 0.0},  # cut
        {"ex_date": "2025-07-01", "regular": 14.0, "special": 5.0},
        {"ex_date": "2026-07-01", "regular": 15.0, "special": 0.0},
    ]
    m = dc.dividend_metrics(events, price=300.0, as_of=AS_OF, history_start=date(2021, 11, 20))
    assert m["fiscal_year_totals"] == {
        "FY2023": 10.0,
        "FY2024": 12.0,
        "FY2025": 11.0,
        "FY2026": 14.0,
    }
    assert m["complete_fiscal_years"] == 4 and m["paying_years"] == 4
    assert m["dividend_cuts"] == 1 and m["consecutive_increases"] == 1
    assert m["dividend_cagr_pct"] == pytest.approx(11.87, abs=0.01)
    assert m["ttm_regular_dividend"] == 15.0 and m["regular_yield_pct"] == 5.0
    assert m["total_yield_pct"] == 5.0  # the special was paid more than a year ago
    empty = dc.dividend_metrics([], price=100.0, as_of=AS_OF, history_start=date(2021, 4, 1))
    assert empty["dividend_cagr_pct"] is None and empty["complete_fiscal_years"] == 5


def test_rsi():
    assert dc.rsi([1.0] * 10) is None
    assert dc.rsi([float(i) for i in range(30)]) == 100.0
    falling = dc.rsi([100.0 - i for i in range(30)])
    assert falling == 0.0
    mixed = dc.rsi([100 + (i % 2) * 2 - i * 0.1 for i in range(40)])
    assert 0 < mixed < 100


def test_score_rescales_without_valuation():
    metrics = {
        "regular_yield_pct": 5.2,
        "complete_fiscal_years": 4,
        "paying_years": 4,
        "dividend_cagr_pct": 12.0,
        "dividend_cuts": 0,
    }
    with_pe = dc.score(metrics, 35.0, 10.0)
    assert with_pe["components"] == {
        "yield": 30,
        "consistency": 25,
        "growth": 20,
        "pullback": 10,
        "valuation": 15,
    }
    assert with_pe["score"] == 100.0 and with_pe["grade"] == "A" and with_pe["valuation_scored"]
    without = dc.score(metrics, 60.0, None)
    assert without["score"] == pytest.approx(88.2, abs=0.1) and not without["valuation_scored"]
    weak = dc.score(
        {
            **metrics,
            "regular_yield_pct": 0.5,
            "paying_years": 2,
            "dividend_cagr_pct": -3,
            "dividend_cuts": 2,
        },
        None,
        40.0,
    )
    assert weak["grade"] == "D"


# --------------------------------------------------------------------------- CLI


def sessions(start, end):
    out, day = [], start
    while day <= end:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def bars(price=300.0, n=260):
    days = sessions(AS_OF - timedelta(days=n * 2), AS_OF)[-n:]
    return [
        {
            "date": d.isoformat(),
            "open": price,
            "high": price * 1.01,
            "low": price * 0.99,
            "close": price * (1 + 0.001 * (i % 5)),
            "volume": 1000,
        }
        for i, d in enumerate(days)
    ]


ACTIONS = {
    "PAYER": [
        act(f"{y}-07-01", "DIVIDEND", f"Dividend - Rs {10 + y - 2022} Per Share")
        for y in range(2022, 2027)
    ],
    "SKIPPER": [
        act("2022-07-01", "DIVIDEND", "Dividend - Rs 30 Per Share"),
        act("2026-07-01", "DIVIDEND", "Dividend - Rs 30 Per Share"),
    ],
    "STINGY": [act("2026-07-01", "DIVIDEND", "Dividend - Rs 1 Per Share")],
}


class FakeCache:
    _sessions_fn = staticmethod(sessions)

    def __init__(self):
        self.client = None

    def stock_bars(self, symbol, start, end, adjusted=True):
        if symbol == "NOPRICE":
            raise india.SymbolNotFoundError("gone")
        return {"bars": bars(10.0, 20) if symbol == "YOUNG" else bars()}


class FakeClient:
    def __init__(self):
        self.calls = []

    def call_tool(self, name, args):
        self.calls.append(args["symbol"])
        return {
            "actions": [
                {
                    "exDate": a["ex_date"],
                    "actionType": a["action_type"],
                    "purpose": a["purpose"],
                    "adjustmentFactor": a["factor"],
                }
                for a in ACTIONS.get(args["symbol"], [])
            ]
        }


def run(tmp_path, *argv, client=None):
    client = client or FakeClient()
    rc = cli.main(
        ["--cache-dir", str(tmp_path / "cache"), "--output-dir", str(tmp_path / "out"), *argv],
        cache=FakeCache(),
        client=client,
        now_fn=lambda: NOW,
        universe_loader=lambda key, root: [{"symbol": s} for s in ("PAYER", "SKIPPER", "STINGY")],
    )
    return rc, client


def report(tmp_path):
    return json.loads(
        sorted((tmp_path / "out").glob("india_dividend_screener_*.json"))[-1].read_text(
            encoding="utf-8"
        )
    )


def test_universe_mode_filters_and_caches_actions(tmp_path):
    rc, client = run(tmp_path, "--universe", "nifty50")
    assert rc == 0
    data = report(tmp_path)
    assert [r["symbol"] for r in data["results"]] == ["PAYER"]
    assert data["skipped"] == {"below_min_yield": ["STINGY"], "inconsistent_payer": ["SKIPPER"]}
    payer = data["results"][0]
    assert (
        payer["regular_yield_pct"] == pytest.approx(4.66, abs=0.05)
        and not payer["valuation_scored"]
    )
    assert sorted(client.calls) == ["PAYER", "SKIPPER", "STINGY"]
    rc, second = run(tmp_path, "--universe", "nifty50")
    assert rc == 0 and second.calls == []  # served from the 7-day actions cache
    md = sorted((tmp_path / "out").glob("*.md"))[-1].read_text(encoding="utf-8")
    assert "India Dividend Screener (NSE)" in md and "slab rate" in md


def test_screen_json_mode_carries_fundamentals(tmp_path):
    screen = tmp_path / "screen.json"
    screen.write_text(
        json.dumps(
            {
                "data": {
                    "results": [
                        {
                            "nse_symbol": "PAYER",
                            "name": "Payer Ltd",
                            "pe": 9.5,
                            "roe": 0.21,
                            "dividend_yield": 0.05,
                        },
                        {"nse_symbol": "NOPRICE"},
                        {"nse_symbol": "YOUNG"},
                        {"symbol": "../bad"},
                        {"name": "no symbol"},
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    rc, _ = run(tmp_path, "--screen-json", str(screen))
    assert rc == 0
    data = report(tmp_path)
    payer = data["results"][0]
    assert payer["fundamentals"]["pe"] == 9.5 and payer["valuation_scored"]
    assert data["skipped"] == {
        "data_unavailable": ["NOPRICE"],
        "insufficient_price_history": ["YOUNG"],
    }
    assert data["candidate_source"].startswith("DalalOS screen")


def test_symbols_mode_and_min_yield(tmp_path):
    rc, _ = run(tmp_path, "--symbols", "stingy", "--min-yield", "0.1", "--min-paying-years", "0")
    assert rc == 0 and report(tmp_path)["results"][0]["symbol"] == "STINGY"


def test_errors(tmp_path, capsys):
    assert run(tmp_path, "--screen-json", str(tmp_path / "missing.json"))[0] == 1
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"data": {"results": []}}), encoding="utf-8")
    assert run(tmp_path, "--screen-json", str(empty))[0] == 1
    assert run(tmp_path, "--universe", "nifty50", "--as-of", "2026-12-31")[0] == 1
    err = capsys.readouterr().err
    assert "no screen file" in err and "no NSE rows" in err and "after the latest" in err
