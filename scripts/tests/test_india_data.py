"""Tests for the India market data foundation (scripts/india_data/india_data.py).

Network is never touched: an injected fake opener plays the NSE bhavcopy MCP
server, using response shapes captured from the live endpoint on 2026-11-21.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import urllib.error
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load():
    path = REPO_ROOT / "scripts" / "india_data" / "india_data.py"
    spec = importlib.util.spec_from_file_location("canonical_india_data", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["canonical_india_data"] = module
    spec.loader.exec_module(module)
    return module


ind = _load()
IST = timezone(timedelta(hours=5, minutes=30))


# --------------------------------------------------------------------------- fakes


class FakeResponse:
    def __init__(self, body: str, content_type: str, session_id: str | None = None):
        self._body = body.encode()
        self.headers = {"Content-Type": content_type}
        if session_id:
            self.headers["Mcp-Session-Id"] = session_id
        self.status = 200

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def sse(message: dict) -> str:
    return f"event:message\ndata:{json.dumps(message)}\n\n"


def tool_message(req_id, payload, *, is_error=False):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {"content": [{"type": "text", "text": text}], "isError": is_error},
    }


def stock_bar(day: str, close: float, *, volume: int = 1000, series: str = "EQ", prev=None):
    return {
        "symbol": "TEST",
        "series": series,
        "open": close,
        "high": close + 1,
        "low": close - 1,
        "close": close,
        "prevClose": prev if prev is not None else close,
        "volume": volume,
        "totalTradedValue": close * volume,
        "ch52WeekHigh": 0.0,
        "ch52WeekLow": 0.0,
        "deliveryQty": 0,
        "date": day,
    }


class FakeServer:
    """Callable opener: ``opener(request, timeout=...)`` like urllib's urlopen."""

    def __init__(self, tools=None, failures=None):
        self.tools = tools or {}
        self.failures = list(failures or [])
        self.requests: list[dict] = []
        self.tool_calls: list[tuple[str, dict]] = []
        self.timeouts: list[float] = []

    def __call__(self, request, timeout=None):
        self.timeouts.append(timeout)
        body = json.loads(request.data.decode())
        headers = {k.lower(): v for k, v in request.header_items()}
        self.requests.append({"body": body, "headers": headers})
        method = body.get("method")
        if method == "tools/call" and self.failures:
            failure = self.failures.pop(0)
            if failure is not None:
                raise failure
        if method == "initialize":
            msg = {"jsonrpc": "2.0", "id": body["id"], "result": {"protocolVersion": "2025-03-26"}}
            return FakeResponse(json.dumps(msg), "application/json", session_id="sess-1")
        if method == "notifications/initialized":
            return FakeResponse("", "application/json")
        assert method == "tools/call"
        name = body["params"]["name"]
        args = body["params"]["arguments"]
        self.tool_calls.append((name, args))
        handler = self.tools[name]
        payload = handler(args)
        return FakeResponse(
            sse(tool_message(body["id"], payload)), "text/event-stream;charset=UTF-8"
        )


def http_error(code: int):
    return urllib.error.HTTPError("https://example.invalid", code, "err", {}, io.BytesIO(b""))


def make_client(server, **kwargs):
    kwargs.setdefault("sleep", lambda _s: None)
    kwargs.setdefault("min_interval_seconds", 0.0)
    return ind.McpHttpClient("https://example.invalid/mcp", opener=server, **kwargs)


def history_tool(bars_by_date: dict):
    """Serve stock history like NSE: window = (endDate - months, endDate]."""

    def handler(args):
        end = date.fromisoformat(args["endDate"])
        start = ind._months_back(end, args["months"])
        rows = [b for d, b in sorted(bars_by_date.items()) if start <= date.fromisoformat(d) <= end]
        if not rows:
            return {
                "hint": "Use search_symbols tool",
                "error": f"Symbol not found: {args['symbol']}",
            }
        return {
            "symbol": args["symbol"],
            "from_date": rows[0]["date"],
            "to_date": rows[-1]["date"],
            "data": rows,
            "chunk_months": args["months"],
            "next_end_date": (start - timedelta(days=1)).isoformat(),
        }

    return handler


# --------------------------------------------------------------------------- MCP client


def test_parse_sse_and_json_bodies():
    msg = {"jsonrpc": "2.0", "id": 3, "result": {"ok": True}}
    assert ind.parse_mcp_body(sse(msg), "text/event-stream;charset=UTF-8") == msg
    assert ind.parse_mcp_body(json.dumps(msg), "application/json") == msg


def test_parse_sse_picks_message_matching_id():
    note = {"jsonrpc": "2.0", "method": "notifications/progress"}
    msg = {"jsonrpc": "2.0", "id": 7, "result": {}}
    body = sse(note) + sse(msg)
    assert ind.parse_mcp_body(body, "text/event-stream", request_id=7) == msg


def test_client_initializes_once_and_sends_session_id():
    server = FakeServer(tools={"ping": lambda a: {"pong": True}})
    client = make_client(server)
    assert client.call_tool("ping", {}) == {"pong": True}
    assert client.call_tool("ping", {}) == {"pong": True}
    methods = [r["body"]["method"] for r in server.requests]
    assert methods.count("initialize") == 1
    tool_requests = [r for r in server.requests if r["body"]["method"] == "tools/call"]
    assert all(r["headers"]["mcp-session-id"] == "sess-1" for r in tool_requests)
    assert "text/event-stream" in tool_requests[0]["headers"]["accept"]
    assert all(t is not None and t > 0 for t in server.timeouts)


def test_client_retries_transient_failures():
    server = FakeServer(
        tools={"ping": lambda a: {"pong": True}},
        failures=[http_error(502), urllib.error.URLError("reset")],
    )
    slept = []
    client = make_client(server, sleep=slept.append, backoff_seconds=0.5)
    assert client.call_tool("ping", {}) == {"pong": True}
    assert slept == [0.5, 1.0]


def test_client_gives_up_after_max_retries():
    server = FakeServer(tools={"ping": lambda a: {}}, failures=[http_error(503)] * 5)
    client = make_client(server, max_retries=2)
    with pytest.raises(ind.McpTransportError):
        client.call_tool("ping", {})


def test_client_does_not_retry_client_errors():
    server = FakeServer(tools={"ping": lambda a: {}}, failures=[http_error(400)])
    client = make_client(server, max_retries=3)
    with pytest.raises(ind.McpTransportError):
        client.call_tool("ping", {})
    assert len(server.requests) == 3  # initialize, initialized notification, one call


def test_tool_error_payloads_raise():
    server = FakeServer(
        tools={
            "missing": lambda a: {"error": "Symbol not found: XYZ"},
            "broken": lambda a: {"error": "Internal failure"},
        }
    )
    client = make_client(server)
    with pytest.raises(ind.SymbolNotFoundError):
        client.call_tool("missing", {})
    with pytest.raises(ind.McpToolError):
        client.call_tool("broken", {})


def test_is_error_result_raises_tool_error():
    class ErrServer(FakeServer):
        def __call__(self, request, timeout=None):
            body = json.loads(request.data.decode())
            if body.get("method") == "tools/call":
                msg = tool_message(body["id"], "bad args", is_error=True)
                return FakeResponse(sse(msg), "text/event-stream")
            return super().__call__(request, timeout)

    client = make_client(ErrServer())
    with pytest.raises(ind.McpToolError, match="bad args"):
        client.call_tool("x", {})


# --------------------------------------------------------------------------- history


def test_months_back_clamps_day():
    assert ind._months_back(date(2026, 5, 31), 3) == date(2026, 2, 28)
    assert ind._months_back(date(2026, 3, 15), 3) == date(2025, 12, 15)


def test_fetch_stock_history_chunks_and_skips_gaps():
    bars = {
        "2022-12-29": stock_bar("2022-12-29", 100),
        "2022-12-30": stock_bar("2022-12-30", 101),
        # 2023-01 .. 2023-11 missing entirely (a provider gap, not end of history)
        "2023-12-28": stock_bar("2023-12-28", 110),
        "2024-01-02": stock_bar("2024-01-02", 111),
        "2024-11-20": stock_bar("2024-11-20", 120),
    }
    server = FakeServer(tools={"get_stock_history": history_tool(bars)})
    out = ind.fetch_stock_history(
        make_client(server), "test", date(2022, 12, 1), date(2024, 11, 20)
    )
    assert [b["date"] for b in out] == sorted(bars)
    assert all(call[1]["symbol"] == "TEST" for call in server.tool_calls)
    assert all(call[1]["months"] <= 3 for call in server.tool_calls)
    first = out[0]
    assert first == {
        "date": "2022-12-29",
        "open": 100,
        "high": 101,
        "low": 99,
        "close": 100,
        "prev_close": 100,
        "volume": 1000,
        "value": 100000,
        "series": "EQ",
    }


def test_fetch_stock_history_raises_when_nothing_found():
    server = FakeServer(tools={"get_stock_history": history_tool({})})
    with pytest.raises(ind.SymbolNotFoundError):
        ind.fetch_stock_history(make_client(server), "NOPE", date(2024, 1, 1), date(2024, 6, 30))


def test_duplicate_dates_prefer_eq_series():
    rows = [stock_bar("2024-03-01", 50, series="BE"), stock_bar("2024-03-01", 51, series="EQ")]

    def handler(args):
        return {"data": rows, "next_end_date": "2000-01-01"}

    server = FakeServer(tools={"get_stock_history": handler})
    out = ind.fetch_stock_history(make_client(server), "X", date(2024, 3, 1), date(2024, 3, 1))
    assert len(out) == 1 and out[0]["series"] == "EQ" and out[0]["close"] == 51


def test_invalid_bars_are_dropped_not_zeroed():
    good = stock_bar("2024-03-04", 10)
    bad = stock_bar("2024-03-05", 10)
    bad["close"] = 0.0
    nonnum = stock_bar("2024-03-06", 10)
    nonnum["open"] = "n/a"

    server = FakeServer(tools={"get_stock_history": lambda a: {"data": [good, bad, nonnum]}})
    out = ind.fetch_stock_history(make_client(server), "X", date(2024, 3, 4), date(2024, 3, 6))
    assert [b["date"] for b in out] == ["2024-03-04"]


def test_fetch_index_history_normalizes_fields():
    row = {
        "indexName": "Nifty 50",
        "open": 23016.0,
        "high": 23120.9,
        "low": 22649.9,
        "close": 22811.35,
        "pointsChange": -77.6,
        "pctChange": -0.34,
        "volume": 324842141,
        "turnoverCr": 28920.19,
        "pe": 19.26,
        "pb": 2.8,
        "divYield": 1.49,
        "date": "2026-10-21",
    }
    server = FakeServer(tools={"get_index_history": lambda a: {"data": [row]}})
    out = ind.fetch_index_history(
        make_client(server), "NIFTY 50", date(2026, 10, 21), date(2026, 10, 21)
    )
    assert out == [
        {
            "date": "2026-10-21",
            "open": 23016.0,
            "high": 23120.9,
            "low": 22649.9,
            "close": 22811.35,
            "volume": 324842141,
            "turnover_cr": 28920.19,
            "pe": 19.26,
            "pb": 2.8,
            "div_yield": 1.49,
        }
    ]
    assert server.tool_calls[0][1]["indexName"] == "NIFTY 50"


# --------------------------------------------------------------------------- corporate actions


RELIANCE_ACTIONS_PAYLOAD = {
    "symbol": "RELIANCE",
    "count": 2,
    "actions": [
        {
            "exDate": "2024-08-19",
            "actionType": "DIVIDEND",
            "purpose": "Dividend - Rs 10",
            "adjustmentFactor": 1.0,
        },
        {
            "exDate": "2024-10-28",
            "actionType": "BONUS",
            "purpose": "Bonus 1:1",
            "adjustmentFactor": 0.5,
        },
    ],
}


def test_fetch_corporate_actions():
    server = FakeServer(tools={"get_corporate_actions": lambda a: RELIANCE_ACTIONS_PAYLOAD})
    out = ind.fetch_corporate_actions(
        make_client(server), "reliance", date(2024, 1, 1), date(2026, 11, 21)
    )
    assert out[1] == {
        "ex_date": "2024-10-28",
        "action_type": "BONUS",
        "purpose": "Bonus 1:1",
        "factor": 0.5,
    }
    assert server.tool_calls[0][1] == {
        "symbol": "RELIANCE",
        "fromDate": "2024-01-01",
        "toDate": "2026-11-21",
    }


def test_adjustment_factors_multiply_same_day_and_skip_neutral():
    actions = [
        {"ex_date": "2025-06-16", "action_type": "BONUS", "purpose": "", "factor": 0.2},
        {"ex_date": "2025-06-16", "action_type": "SPLIT", "purpose": "", "factor": 0.5},
        {"ex_date": "2025-05-01", "action_type": "DIVIDEND", "purpose": "", "factor": 1.0},
        {"ex_date": "2025-04-01", "action_type": "OTHER", "purpose": "", "factor": "bad"},
        {"ex_date": "2025-03-01", "action_type": "OTHER", "purpose": "", "factor": -1},
    ]
    assert ind.adjustment_factors(actions) == [("2025-06-16", pytest.approx(0.1))]


def test_adjust_bars_rescales_price_and_volume_before_ex_date():
    bars = [
        ind.normalize_stock_bar(stock_bar("2024-10-25", 2655.7, volume=9_298_748)),
        ind.normalize_stock_bar(stock_bar("2024-10-28", 1334.35, volume=20_000_000, prev=1327.85)),
    ]
    actions = [
        {"ex_date": "2024-10-28", "action_type": "BONUS", "purpose": "", "factor": 0.5},
    ]
    out = ind.adjust_bars(bars, actions)
    before, after = out
    assert before["close"] == pytest.approx(1327.85)
    assert before["volume"] == 18_597_496  # matches Kite's adjusted volume
    assert before["adj_factor"] == 0.5
    assert after["close"] == 1334.35 and after["volume"] == 20_000_000
    assert after["prev_close"] == 1327.85  # NSE already adjusts prevClose on the ex-date
    assert after["adj_factor"] == 1.0
    assert bars[0]["close"] == 2655.7  # input not mutated


def test_unexplained_jumps_flag_unadjusted_moves_only():
    bars = [
        ind.normalize_stock_bar(stock_bar("2023-07-19", 2800)),
        ind.normalize_stock_bar(stock_bar("2023-07-20", 2580)),  # -7.9%: normal
        ind.normalize_stock_bar(stock_bar("2023-07-21", 1900)),  # -26%: demerger, factor 1.0
        ind.normalize_stock_bar(stock_bar("2023-07-24", 1910)),
    ]
    actions = [
        {"ex_date": "2023-07-21", "action_type": "OTHER", "purpose": "Demerger", "factor": 1.0},
    ]
    warnings = ind.find_unexplained_jumps(bars, actions, threshold=0.20)
    assert len(warnings) == 1
    w = warnings[0]
    assert w["date"] == "2023-07-21"
    assert w["change_pct"] == pytest.approx(-26.36, abs=0.01)
    assert "OTHER" in w["note"]

    explained = [{"ex_date": "2023-07-21", "action_type": "SPLIT", "purpose": "", "factor": 0.5}]
    adjusted = ind.adjust_bars(
        [
            ind.normalize_stock_bar(stock_bar("2023-07-20", 2000)),
            ind.normalize_stock_bar(stock_bar("2023-07-21", 1000)),
        ],
        explained,
    )
    assert ind.find_unexplained_jumps(adjusted, explained) == []


# --------------------------------------------------------------------------- calendar


def fake_sessions(start: date, end: date) -> list[date]:
    days = []
    d = start
    while d <= end:
        if d.weekday() < 5 and d != date(2026, 11, 24):
            days.append(d)
        d += timedelta(days=1)
    return days


def test_latest_completed_session_respects_cutoff_and_holidays():
    f = ind.latest_completed_session
    # Friday 2026-11-20 18:59 IST -> bhavcopy not yet assumed final -> Thursday
    assert f(datetime(2026, 11, 20, 18, 59, tzinfo=IST), sessions_fn=fake_sessions) == date(
        2026, 11, 19
    )
    assert f(datetime(2026, 11, 20, 19, 0, tzinfo=IST), sessions_fn=fake_sessions) == date(
        2026, 11, 20
    )
    # Saturday -> Friday
    assert f(datetime(2026, 11, 21, 10, 0, tzinfo=IST), sessions_fn=fake_sessions) == date(
        2026, 11, 20
    )
    # Holiday Tuesday 2026-11-24 evening -> Monday
    assert f(datetime(2026, 11, 24, 21, 0, tzinfo=IST), sessions_fn=fake_sessions) == date(
        2026, 11, 23
    )
    # UTC input is converted to IST: 2026-11-20 13:40 UTC == 19:10 IST
    assert f(
        datetime(2026, 11, 20, 13, 40, tzinfo=timezone.utc), sessions_fn=fake_sessions
    ) == date(2026, 11, 20)


def test_latest_completed_session_requires_aware_datetime():
    with pytest.raises(ValueError):
        ind.latest_completed_session(datetime(2026, 11, 20, 20, 0), sessions_fn=fake_sessions)


def test_nse_sessions_uses_xbom_plus_muhurat():
    pytest.importorskip("pandas_market_calendars")
    sessions = ind.nse_sessions(date(2024, 10, 28), date(2024, 11, 5))
    assert date(2024, 11, 1) in sessions  # Muhurat trading (Diwali)
    assert date(2024, 11, 2) not in sessions  # Saturday
    nov = ind.nse_sessions(date(2026, 11, 23), date(2026, 11, 25))
    assert nov == [date(2026, 11, 23), date(2026, 11, 25)]  # 2026-11-24 holiday


# --------------------------------------------------------------------------- bar cache


def weekday_bars(start: date, end: date, base=100.0):
    out = {}
    d = start
    price = base
    while d <= end:
        if d.weekday() < 5 and d != date(2026, 11, 24):
            out[d.isoformat()] = stock_bar(d.isoformat(), price)
            price += 1
        d += timedelta(days=1)
    return out


def make_cache(tmp_path, server, now):
    return ind.BarCache(
        tmp_path,
        make_client(server),
        sessions_fn=fake_sessions,
        now_fn=lambda: now,
    )


def test_bar_cache_fetches_once_then_serves_from_disk(tmp_path):
    bars = weekday_bars(date(2026, 9, 1), date(2026, 11, 20))
    server = FakeServer(
        tools={
            "get_stock_history": history_tool(bars),
            "get_corporate_actions": lambda a: {"actions": []},
        }
    )
    now = datetime(2026, 11, 21, 10, 0, tzinfo=IST)
    cache = make_cache(tmp_path, server, now)
    first = cache.stock_bars("TEST", date(2026, 9, 1), date(2026, 11, 21))
    calls_after_first = len(server.tool_calls)
    assert first["bars"][-1]["date"] == "2026-11-20"
    assert first["currency"] == "INR" and first["adjusted"] is True
    second = cache.stock_bars("TEST", date(2026, 10, 1), date(2026, 11, 20))
    assert len(server.tool_calls) == calls_after_first
    assert second["bars"][0]["date"] == "2026-10-01"
    stored = json.loads((tmp_path / "stocks" / "TEST.json").read_text())
    assert stored["coverage_end"] == "2026-11-20"


def test_bar_cache_refreshes_tail_and_actions_after_new_session(tmp_path):
    bars = weekday_bars(date(2026, 9, 1), date(2026, 11, 23))
    actions = {"actions": []}
    server = FakeServer(
        tools={
            "get_stock_history": history_tool(bars),
            "get_corporate_actions": lambda a: actions,
        }
    )
    friday_night = datetime(2026, 11, 21, 10, 0, tzinfo=IST)
    cache = make_cache(tmp_path, server, friday_night)
    cache.stock_bars("TEST", date(2026, 9, 1), date(2026, 11, 30))

    actions["actions"] = [
        {"exDate": "2026-11-23", "actionType": "SPLIT", "purpose": "Split", "adjustmentFactor": 0.5}
    ]
    monday_night = datetime(2026, 11, 23, 20, 0, tzinfo=IST)
    cache2 = make_cache(tmp_path, server, monday_night)
    server.tool_calls.clear()
    out = cache2.stock_bars("TEST", date(2026, 9, 1), date(2026, 11, 30))
    names = [c[0] for c in server.tool_calls]
    assert names.count("get_stock_history") == 1  # one short tail call
    assert server.tool_calls[0][1]["months"] == 1
    assert "get_corporate_actions" in names
    assert out["bars"][-1]["date"] == "2026-11-23"
    assert out["bars"][-2]["adj_factor"] == 0.5  # history re-adjusted by new split

    raw = cache2.stock_bars("TEST", date(2026, 9, 1), date(2026, 11, 30), adjusted=False)
    assert raw["bars"][-2]["adj_factor"] == 1.0


def test_bar_cache_backfills_older_start(tmp_path):
    bars = weekday_bars(date(2026, 1, 1), date(2026, 11, 20))
    server = FakeServer(
        tools={
            "get_stock_history": history_tool(bars),
            "get_corporate_actions": lambda a: {"actions": []},
        }
    )
    now = datetime(2026, 11, 21, 10, 0, tzinfo=IST)
    cache = make_cache(tmp_path, server, now)
    cache.stock_bars("TEST", date(2026, 10, 1), date(2026, 11, 20))
    out = cache.stock_bars("TEST", date(2026, 6, 1), date(2026, 11, 20))
    assert out["bars"][0]["date"] == "2026-06-01"
    stored = json.loads((tmp_path / "stocks" / "TEST.json").read_text())
    assert stored["coverage_start"] == "2026-06-01"
    assert len({b["date"] for b in stored["bars"]}) == len(stored["bars"])


def test_bar_cache_rejects_unsafe_symbol(tmp_path):
    cache = make_cache(tmp_path, FakeServer(), datetime(2026, 11, 21, tzinfo=IST))
    with pytest.raises(ValueError):
        cache.stock_bars("../etc", date(2026, 1, 1), date(2026, 1, 2))


def test_symbols_with_special_characters_are_allowed(tmp_path):
    assert ind.safe_symbol("m&m") == "M&M"
    assert ind.safe_symbol("BAJAJ-AUTO") == "BAJAJ-AUTO"


def index_row(day: str, close: float) -> dict:
    return {
        "indexName": "Nifty 50",
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1,
        "turnoverCr": 1.0,
        "pe": 20.0,
        "pb": 3.0,
        "divYield": 1.2,
        "date": day,
    }


def test_index_cache_round_trip(tmp_path):
    row = index_row
    server = FakeServer(
        tools={
            "get_index_history": lambda a: {
                "data": [row("2026-11-19", 1.0), row("2026-11-20", 2.0)]
            }
        }
    )
    cache = make_cache(tmp_path, server, datetime(2026, 11, 21, 10, 0, tzinfo=IST))
    out = cache.index_bars("NIFTY 50", date(2026, 11, 19), date(2026, 11, 20))
    assert [b["close"] for b in out["bars"]] == [1.0, 2.0]
    cache.index_bars("NIFTY 50", date(2026, 11, 19), date(2026, 11, 20))
    assert len(server.tool_calls) == 1
    assert (tmp_path / "indices" / "NIFTY_50.json").exists()


# --------------------------------------------------------------------------- universe


CSV_TEXT = (
    "Company Name,Industry,Symbol,Series,ISIN Code\n"
    "Reliance Industries Ltd.,Oil Gas & Consumable Fuels,RELIANCE,EQ,INE002A01018\n"
    "Mahindra & Mahindra Ltd.,Automobile and Auto Components,M&M,EQ,INE101A01026\n"
)


def test_parse_constituents_csv():
    rows = ind.parse_constituents_csv(CSV_TEXT)
    assert rows[1] == {
        "symbol": "M&M",
        "company": "Mahindra & Mahindra Ltd.",
        "industry": "Automobile and Auto Components",
        "series": "EQ",
        "isin": "INE101A01026",
    }


def test_load_universe_downloads_then_uses_cache(tmp_path):
    seen = []

    def opener(request, timeout=None):
        seen.append((request.full_url, timeout))
        return FakeResponse(CSV_TEXT, "text/csv")

    now = datetime(2026, 11, 21, tzinfo=timezone.utc)
    rows = ind.load_universe("nifty50", tmp_path, opener=opener, now_fn=lambda: now)
    assert [r["symbol"] for r in rows] == ["RELIANCE", "M&M"]
    assert seen[0][0].endswith("/ind_nifty50list.csv") and seen[0][1]
    ind.load_universe("nifty50", tmp_path, opener=opener, now_fn=lambda: now + timedelta(days=3))
    assert len(seen) == 1
    ind.load_universe("nifty50", tmp_path, opener=opener, now_fn=lambda: now + timedelta(days=8))
    assert len(seen) == 2


def test_load_universe_rejects_unknown_key(tmp_path):
    with pytest.raises(ValueError, match="nifty500"):
        ind.load_universe("sensex", tmp_path, opener=lambda *a, **k: None)


# --------------------------------------------------------------------------- credits


def test_credit_ledger_monthly_and_lifetime_budgets(tmp_path):
    ledger = ind.CreditLedger(tmp_path / "ledger.jsonl")
    nov = datetime(2026, 11, 21, tzinfo=IST)
    ledger.record("dalalos", "get_financials", 390, when=nov)
    ledger.record("dalalos", "get_financials", 10, when=nov - timedelta(days=30))  # October
    ledger.record("drishti", "get_block_deals", 805, when=nov)
    status = ledger.status(now=nov)
    assert status["dalalos"]["used"] == 390
    assert status["dalalos"]["remaining"] == 110
    assert status["dalalos"]["period"] == "2026-11"
    assert status["drishti"]["used"] == 805 and status["drishti"]["remaining"] == 195
    assert not any("dalalos" in w for w in status["warnings"])  # 78%: below the 80% alert
    assert any("drishti" in w for w in status["warnings"])  # under 200 lifetime credits left
    ledger.record("dalalos", "get_stock_events", 10, when=nov)
    assert any("80%" in w for w in ledger.status(now=nov)["warnings"])


def test_credit_ledger_validates_inputs(tmp_path):
    ledger = ind.CreditLedger(tmp_path / "ledger.jsonl")
    with pytest.raises(ValueError):
        ledger.record("unknown", "x", 1)
    with pytest.raises(ValueError):
        ledger.record("dalalos", "x", -1)


def test_metered_cache_ttl_and_key_order(tmp_path):
    cache = ind.MeteredCache(tmp_path)
    now = datetime(2026, 11, 21, tzinfo=timezone.utc)
    cache.put(
        "dalalos", "get_financials", {"query": "TCS", "limit": 8}, {"rev": 1}, ttl_days=30, now=now
    )
    assert cache.get("dalalos", "get_financials", {"limit": 8, "query": "TCS"}, now=now) == {
        "rev": 1
    }
    assert cache.get("dalalos", "get_financials", {"query": "INFY"}, now=now) is None
    assert (
        cache.get(
            "dalalos", "get_financials", {"query": "TCS", "limit": 8}, now=now + timedelta(days=31)
        )
        is None
    )


# --------------------------------------------------------------------------- CLI


def test_cli_bars_outputs_json(tmp_path, capsys):
    bars = weekday_bars(date(2026, 11, 2), date(2026, 11, 20))
    server = FakeServer(
        tools={
            "get_stock_history": history_tool(bars),
            "get_corporate_actions": lambda a: {"actions": []},
        }
    )
    rc = ind.main(
        [
            "--cache-dir",
            str(tmp_path),
            "bars",
            "TEST",
            "--start",
            "2026-11-16",
            "--end",
            "2026-11-20",
        ],
        client=make_client(server),
        sessions_fn=fake_sessions,
        now_fn=lambda: datetime(2026, 11, 21, 10, 0, tzinfo=IST),
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["symbol"] == "TEST" and len(out["bars"]) == 5


def test_cli_reports_unknown_symbol_on_stderr(tmp_path, capsys):
    server = FakeServer(
        tools={
            "get_stock_history": history_tool({}),
            "get_corporate_actions": lambda a: {"actions": []},
        }
    )
    rc = ind.main(
        [
            "--cache-dir",
            str(tmp_path),
            "bars",
            "NOPE",
            "--start",
            "2026-11-16",
            "--end",
            "2026-11-20",
        ],
        client=make_client(server),
        sessions_fn=fake_sessions,
        now_fn=lambda: datetime(2026, 11, 21, 10, 0, tzinfo=IST),
    )
    captured = capsys.readouterr()
    assert rc == 1
    assert "Error" in captured.err and "NOPE" in captured.err


def test_cli_ledger_record_and_status(tmp_path, capsys):
    base = ["--cache-dir", str(tmp_path)]
    assert (
        ind.main(
            base
            + ["ledger", "record", "--source", "drishti", "--tool", "get_news", "--credits", "1"]
        )
        == 0
    )
    capsys.readouterr()
    assert ind.main(base + ["ledger", "status"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["drishti"]["used"] == 1
