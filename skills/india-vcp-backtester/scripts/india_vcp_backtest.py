#!/usr/bin/env python3
"""India VCP Backtester: replay the VCP breakout workflow over past NSE sessions.

For each session in the test window the india-vcp-screener funnel runs on bars
up to that day, india-breakout-trade-planner turns the result into stop-limit
orders for the simulated account, and the india-paper-ledger fill rules fill
them and their stop/target exits on the following sessions, with Indian
delivery charges. Screens are cached (they are the slow part), so parameter
sweeps over the planner and exit settings are quick.

Outputs india_vcp_backtest_<start>_<end>.json / .md plus trade and equity CSVs.
Exit codes: 0 success, 1 invalid input or data, 2 missing dependency.
"""

from __future__ import annotations

import argparse
import csv
import json
import shlex
import sys
import time as clock
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import _india_data as india  # noqa: E402
import vcp_backtest_engine as engine  # noqa: E402
import vcp_backtest_metrics as metrics  # noqa: E402
from vcp_backtest_siblings import SiblingMissingError, load_all  # noqa: E402

SKILL_ID = "india-vcp-backtester"
DEFAULT_DATA_START = "2024-01-01"
WARMUP_SESSIONS = 252  # a full 52-week window before the first screened session
NSE_INDEX_FIRST = "2025-01-01"  # NSE bhavcopy index history starts here
YF_TICKERS = {"NIFTY 500": "^CRSLDX", "NIFTY 50": "^NSEI"}
BACKFILL_TOLERANCE = 0.0005  # 0.05% on closes


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def format_inr(value) -> str:
    if value is None:
        return "—"
    sign = "-" if value < 0 else ""
    whole, frac = f"{abs(float(value)):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return f"{sign}₹{whole}.{frac}"


# --------------------------------------------------------------------------- data


def _cache_only_series(cache_root: Path, kind: str, name: str, start: str, end: str):
    path = (
        cache_root
        / kind
        / (f"{india.safe_symbol(name)}.json" if kind == "stocks" else f"{india._slug(name)}.json")
    )
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise india.IndiaDataError(f"no cached bars for {name} (run without --no-fetch)") from exc
    bars = [b for b in entry["bars"] if start <= b["date"] <= end]
    if kind == "indices":
        return engine.Series(bars)
    cutoff = entry.get("coverage_end", end)
    actions = [a for a in entry.get("actions") or [] if a["ex_date"] <= cutoff]
    adjusted = india.adjust_bars(bars, actions)
    return engine.Series(adjusted, india.find_unexplained_jumps(adjusted, actions), actions)


def load_market_data(
    rows: list,
    benchmark: str,
    data_start: str,
    end: str,
    cache_root: Path,
    *,
    no_fetch: bool,
    cache=None,
) -> tuple:
    """(MarketData, missing symbols) from the shared NSE bar cache."""
    if not no_fetch and cache is None:
        cache = india.BarCache(cache_root)
    stocks, missing = {}, []
    start_d, end_d = date.fromisoformat(data_start), date.fromisoformat(end)
    for n, row in enumerate(rows, 1):
        sym = row["symbol"]
        try:
            if no_fetch:
                stocks[sym] = _cache_only_series(cache_root, "stocks", sym, data_start, end)
            else:
                data = cache.stock_bars(sym, start_d, end_d)
                stocks[sym] = engine.Series(
                    data["bars"], data.get("warnings") or [], data["actions"]
                )
        except (india.IndiaDataError, ValueError) as exc:
            missing.append(f"{sym}: {exc}")
        if n % 100 == 0:
            log(f"  loaded {n}/{len(rows)} symbols")
    nse_start = max(data_start, NSE_INDEX_FIRST)
    if no_fetch:
        nse_bars = _cache_only_series(cache_root, "indices", benchmark, nse_start, end).bars
    else:
        nse_bars = cache.index_bars(benchmark, date.fromisoformat(nse_start), end_d)["bars"]
    notes = []
    bars = list(nse_bars)
    if data_start < NSE_INDEX_FIRST and nse_bars:
        try:
            early = index_backfill(benchmark, data_start, nse_bars, cache_root, no_fetch=no_fetch)
            bars = early + bars
            notes.append(
                f"{benchmark} before {nse_bars[0]['date']} from Yahoo Finance "
                f"({YF_TICKERS[benchmark]}), checked against NSE on the overlap"
            )
        except BackfillUnavailable as exc:
            notes.append(f"{benchmark} history starts {nse_bars[0]['date']}: {exc}")
    bench = engine.Series(bars)
    return engine.MarketData(rows, stocks, bench, benchmark), missing + [f"NOTE {n}" for n in notes]


class BackfillUnavailable(RuntimeError):
    """Older index history could not be filled in."""


def _load_yfinance():
    """yfinance if installed, else None (optional dependency)."""
    try:
        import yfinance
    except ImportError:
        yfinance = None
    return yfinance


def _yf_download(ticker: str, start: str, end: str) -> list:
    """Daily OHLC from Yahoo Finance (optional dependency)."""
    yf = _load_yfinance()
    if yf is None:
        raise BackfillUnavailable(
            "yfinance is not installed (optional; pip install yfinance), so the test window "
            "starts once 252 NSE index sessions exist"
        )
    frame = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=False, timeout=30)
    if frame is None or frame.empty:
        raise BackfillUnavailable(f"Yahoo Finance returned no data for {ticker}")
    if hasattr(frame.columns, "levels"):
        frame.columns = frame.columns.get_level_values(0)
    return [
        {
            "date": ts.date().isoformat(),
            "open": round(float(row["Open"]), 2),
            "high": round(float(row["High"]), 2),
            "low": round(float(row["Low"]), 2),
            "close": round(float(row["Close"]), 2),
            "volume": 0,
        }
        for ts, row in frame.iterrows()
    ]


def index_backfill(
    benchmark: str,
    data_start: str,
    nse_bars: list,
    cache_root: Path,
    *,
    no_fetch: bool,
    downloader=None,
) -> list:
    """Index bars before NSE's history starts, verified against NSE on the overlap."""
    ticker = YF_TICKERS.get(benchmark)
    if ticker is None:
        raise BackfillUnavailable(f"no Yahoo Finance ticker known for {benchmark}")
    first = nse_bars[0]["date"]
    path = cache_root / "backtest" / f"index_backfill_{india._slug(benchmark)}.json"
    cached = None
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cached = None
    if not cached or cached.get("start", "9999") > data_start or cached.get("nse_first") != first:
        if no_fetch:
            raise BackfillUnavailable("no cached Yahoo Finance backfill (run without --no-fetch)")
        overlap_end = (date.fromisoformat(first) + timedelta(days=45)).isoformat()
        rows = (downloader or _yf_download)(ticker, data_start, overlap_end)
        nse = {b["date"]: b["close"] for b in nse_bars}
        shared = [r for r in rows if r["date"] in nse]
        if len(shared) < 10:
            raise BackfillUnavailable(f"only {len(shared)} overlapping sessions to verify {ticker}")
        worst = max(abs(r["close"] / nse[r["date"]] - 1) for r in shared)
        if worst > BACKFILL_TOLERANCE:
            raise BackfillUnavailable(
                f"{ticker} differs from NSE by up to {worst:.4%} on the overlap; not used"
            )
        cached = {
            "ticker": ticker,
            "start": data_start,
            "nse_first": first,
            "verified_sessions": len(shared),
            "max_close_diff": worst,
            "bars": [r for r in rows if r["date"] < first],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cached), encoding="utf-8")
    return [b for b in cached["bars"] if data_start <= b["date"] < first]


# --------------------------------------------------------------------------- reports


def _pct(v) -> str:
    return "—" if v is None else f"{v:+.2f}%"


def _num(v, d=2) -> str:
    return "—" if v is None else f"{v:.{d}f}"


def render_markdown(report: dict) -> str:
    s = report["base"]["summary"]
    st, bm, ts = s["strategy"], s["benchmark"], s["trade_stats"]
    ev = s["events"]
    w = report["window"]
    lines = [
        "# India VCP Breakout Backtest",
        "",
        f"**Window:** {w['start']} to {w['end']} ({w['sessions']} NSE sessions) · "
        f"universe {report['universe']} · generated {report['generated_at']}",
        "",
        "Replays india-vcp-screener → india-breakout-trade-planner → india-paper-ledger fill rules "
        "on official NSE end-of-day bars, with delivery charges. Historical research only.",
        "",
        "## Result",
        "",
        "| | Strategy | " + bm["name"] + " |",
        "|---|---:|---:|",
        f"| Total return | {_pct(st['total_return_pct'])} | {_pct(bm['total_return_pct'])} |",
        f"| CAGR | {_pct(st['cagr_pct'])} | {_pct(bm['cagr_pct'])} |",
        f"| Max drawdown | {_pct(st['max_drawdown_pct'])} | {_pct(bm['max_drawdown_pct'])} |",
        f"| Volatility (annual) | {_num(st['volatility_pct'])}% | {_num(bm['volatility_pct'])}% |",
        f"| Sharpe (rf 0) | {_num(st['sharpe'])} | {_num(bm['sharpe'])} |",
        f"| End value | {format_inr(st['end_value'])} | — |",
        "",
        f"Average exposure {_num(s['avg_exposure_pct'])}% of equity; at most {s['max_positions']} "
        f"positions at once; {s['open_positions']} still open at the end (marked to market).",
        "",
        "## Trades",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Closed trades | {ts['trades']} |",
        f"| Win rate | {_num((ts['win_rate'] or 0) * 100, 1)}% |",
        f"| Avg win / avg loss | {_pct(ts['avg_win_pct'])} / {_pct(ts['avg_loss_pct'])} |",
        f"| Profit factor | {_num(ts['profit_factor'])} |",
        f"| Avg R / median R | {_num(ts['avg_r'], 3)} / {_num(ts['median_r'], 3)} |",
        f"| Expectancy per trade | {format_inr(ts['expectancy_inr'])} |",
        f"| Net P&L on closed trades | {format_inr(ts['total_net_pnl'])} |",
        f"| Charges | {format_inr(ts['total_charges'])} ({_num(ts['charges_pct_of_gross_profit'])}% of gross profit) |",
        f"| Avg sessions held | {_num(ts['avg_sessions_held'], 1)} |",
        f"| Exit reasons | {', '.join(f'{k} {v}' for k, v in ts['exit_reasons'].items()) or '—'} |",
        "",
        "## Order flow",
        "",
        f"- Sessions screened: {ev['screened_sessions']}; actionable plans: {ev['actionable_plans']}; "
        f"deferred by the heat cap: {ev['deferred_heat']}; zero-share plans: {ev['constrained']}",
        f"- Orders filled {ev['orders_filled']}, expired untriggered {ev['orders_expired']} "
        f"(of which gapped past the limit {ev['orders_gapped_past_limit']}), rejected for cash "
        f"{ev['orders_rejected_cash']}, circuit-locked {ev['orders_locked']}",
        f"- Dividends credited: {format_inr(ev['dividends'])}",
        "",
        "## Stability",
        "",
        "| Period | Strategy | Benchmark | Trades | Win rate | Avg R |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    halves = s["halves"]
    if halves:
        for key, label in (
            ("first", f"to {halves['split_date']}"),
            ("second", f"from {halves['split_date']}"),
        ):
            h = halves[key]
            lines.append(
                f"| {label} | {_pct(h['strategy']['total_return_pct'])} | "
                f"{_pct(h['benchmark']['total_return_pct'])} | {h['trades']['trades']} | "
                f"{_num((h['trades']['win_rate'] or 0) * 100, 1)}% | {_num(h['trades']['avg_r'], 3)} |"
            )
    for year, y in s["by_year"].items():
        lines.append(
            f"| {year} | {_pct(y['strategy_return_pct'])} | {_pct(y['benchmark_return_pct'])} | "
            f"{y['trades']} | | |"
        )
    for title, key in (
        ("By rating band", "by_rating_band"),
        ("By market regime at signal", "by_regime_at_entry"),
    ):
        lines += [
            "",
            f"### {title}",
            "",
            "| Bucket | Trades | Win rate | Avg R | Profit factor |",
            "|---|---:|---:|---:|---:|",
        ]
        for name, g in s[key].items():
            lines.append(
                f"| {name} | {g['trades']} | {_num((g['win_rate'] or 0) * 100, 1)}% | "
                f"{_num(g['avg_r'], 3)} | {_num(g['profit_factor'])} |"
            )
    if report.get("sweep"):
        names = sorted({k for row in report["sweep"] for k in row["overrides"]})
        lines += [
            "",
            "## Parameter sweep",
            "",
            "| " + " | ".join(names) + " | Return | Max DD | Trades | Win rate | Avg R | PF |",
            "|" + "---|" * (len(names) + 6),
        ]
        for row in report["sweep"]:
            m = row["summary"]
            lines.append(
                "| "
                + " | ".join(str(row["overrides"].get(n)) for n in names)
                + f" | {_pct(m['strategy']['total_return_pct'])} | {_pct(m['strategy']['max_drawdown_pct'])}"
                f" | {m['trade_stats']['trades']} | {_num((m['trade_stats']['win_rate'] or 0) * 100, 1)}%"
                f" | {_num(m['trade_stats']['avg_r'], 3)} | {_num(m['trade_stats']['profit_factor'])} |"
            )
    p = report["base"]["params"]
    lines += [
        "",
        "## Settings and caveats",
        "",
        f"- Capital {format_inr(p['capital'])}; risk {p['risk_pct']}% per trade (scaled by rating band); "
        f"heat cap {p['max_portfolio_heat_pct']}%; position cap {p['max_position_pct']}%; target "
        f"{p['target_r_multiple']}R; stop buffer {p['stop_buffer_pct']}%; max chase {p['max_chase_pct']}%; "
        f"orders valid {p['valid_sessions']} session(s); slippage {p['slippage_bps']} bps on stop "
        f"entries and stop exits; time stop {p['max_hold_sessions'] or 'none'}.",
        f"- Planner rules: worst-case risk cap {p['max_risk_pct']}%, minimum score {p['min_score']}"
        + (
            f", scores {p['min_score']:g}-69 sized at {p['developing_multiplier']}x"
            if p["min_score"] < 70
            else ""
        )
        + (
            " — **research variant, differs from the live planner (8%, 70+)**"
            if (p["max_risk_pct"], p["min_score"]) != (8.0, 70.0)
            else " (live planner rules)"
        )
        + ".",
        f"- Screener settings: {report['screener_settings']}.",
        "- **Survivorship bias:** the universe is today's index membership; stocks that left the "
        "index (often after falling) are missing, which flatters results.",
        "- Bars are adjusted for later splits/bonuses, so a few prices and tick sizes differ from "
        "what was quoted at the time; rupee P&L is unaffected.",
        "- One daily bar cannot show intraday order: a bar touching both stop and target counts "
        "as a stop. Late-breakout revalidation plans are not traded. Taxes are not modelled.",
        "- Past results on ~2 years of one market are weak evidence; not investment advice.",
        "",
    ]
    for note in report.get("data_notes") or []:
        lines += [f"- Data: {note}."]
    if report.get("missing_symbols"):
        lines += [
            f"- {len(report['missing_symbols'])} symbols had no usable data and were skipped.",
            "",
        ]
    return "\n".join(lines)


def write_csv(path: Path, rows: list) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


# --------------------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    defaults = engine.SimParams()
    p = argparse.ArgumentParser(
        description="Backtest the India VCP breakout workflow on past NSE sessions."
    )
    p.add_argument("--universe", default="nifty500", help="Nifty universe (default: nifty500)")
    p.add_argument("--symbols", nargs="+", help="Explicit NSE symbols instead of --universe")
    p.add_argument("--benchmark", default="NIFTY 500", help="RS and comparison index")
    p.add_argument("--start", help="First test session (default: after a 252-session warm-up)")
    p.add_argument("--end", help="Last test session (default: latest completed session)")
    p.add_argument("--data-start", default=DEFAULT_DATA_START, help="First bar to load")
    p.add_argument("--top", type=int, default=20, help="Screener results passed to the planner")
    p.add_argument(
        "--screener-args", default="", help='Extra screener flags, e.g. "--trend-min-score 80"'
    )
    p.add_argument("--capital", type=float, default=defaults.capital)
    for name in engine.SimParams.PLAN_FIELDS + ("slippage_bps",):
        p.add_argument(
            "--" + name.replace("_", "-"),
            type=int if name == "valid_sessions" else float,
            default=getattr(defaults, name),
        )
    p.add_argument("--max-hold-sessions", type=int, default=None, help="Time stop (default: none)")
    p.add_argument(
        "--max-risk-pct",
        type=float,
        default=defaults.max_risk_pct,
        help="Research: worst-entry-to-stop cap (live planner: 8)",
    )
    p.add_argument(
        "--min-score",
        type=float,
        default=defaults.min_score,
        help="Research: lowest composite score planned (live planner: 70)",
    )
    p.add_argument(
        "--developing-multiplier",
        type=float,
        default=defaults.developing_multiplier,
        help="Research: size multiplier for scores 60-69 when --min-score < 70 (good = 0.75)",
    )
    p.add_argument("--sweep", nargs="+", help="name=v1,v2 ... e.g. target_r_multiple=1.5,2,3")
    p.add_argument("--cache-dir", help="NSE bar cache (default: .cache/india-data)")
    p.add_argument("--no-fetch", action="store_true", help="Use cached bars only")
    p.add_argument("--rescreen", action="store_true", help="Ignore cached screens")
    p.add_argument("--output-dir", default="reports/")
    return p


def main(argv: list | None = None, *, now_fn=None, cache=None) -> int:
    args = build_parser().parse_args(argv)
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))
    try:
        screener, planner, paper = load_all()
    except SiblingMissingError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    screener._log = lambda _message: None  # per-session funnel chatter
    cache_root = Path(args.cache_dir) if args.cache_dir else india.default_cache_dir()
    try:
        screener_argv = shlex.split(args.screener_args)
        screener_argv += ["--benchmark", args.benchmark, "--top", str(args.top)]
        if args.symbols:
            screener_argv += ["--symbols", *args.symbols]
        else:
            screener_argv += ["--universe", args.universe]
        sargs = screener.parse_arguments(screener_argv)
        sweep = engine.parse_sweep(args.sweep)
        base = engine.SimParams(
            capital=args.capital,
            max_hold_sessions=args.max_hold_sessions,
            max_risk_pct=args.max_risk_pct,
            min_score=args.min_score,
            developing_multiplier=args.developing_multiplier,
            **{n: getattr(args, n) for n in engine.SimParams.PLAN_FIELDS + ("slippage_bps",)},
        )
        if not base.capital > 0:
            raise ValueError("--capital must be positive")
        engine.validate_params(base)
        for overrides in sweep:
            engine.validate_params(engine.apply_overrides(base, overrides))
        end = args.end or india.latest_completed_session(now_fn()).isoformat()
        rows, universe_desc = screener.load_universe_rows(sargs, cache_root)
        log(f"Loading {len(rows)} symbols and {args.benchmark} from {args.data_start} to {end}")
        data, missing = load_market_data(
            rows,
            args.benchmark,
            args.data_start,
            end,
            cache_root,
            no_fetch=args.no_fetch,
            cache=cache,
        )
    except india.CalendarDependencyMissingError as exc:
        print(f"Error: missing dependency. {exc}", file=sys.stderr)
        return 2
    except (india.IndiaDataError, ValueError, OSError, SystemExit) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    all_sessions = [d for d in data.sessions if d <= end]
    if len(all_sessions) <= WARMUP_SESSIONS + 1:
        print(
            f"Error: only {len(all_sessions)} sessions of {args.benchmark}; need more history",
            file=sys.stderr,
        )
        return 1
    start = args.start or all_sessions[WARMUP_SESSIONS]
    sessions = [d for d in all_sessions if start <= d <= end]
    if len(sessions) < 2:
        print(f"Error: no NSE sessions between {start} and {end}", file=sys.stderr)
        return 1

    key = engine.screen_key(universe_desc, args.benchmark, sargs, args.top, data)
    cache_path = cache_root / "backtest" / f"vcp_screens_{key}.jsonl"
    if args.rescreen and cache_path.exists():
        cache_path.unlink()
    screen_cache = engine.ScreenCache(cache_path)
    todo = sum(1 for d in sessions if d not in screen_cache.days)
    log(f"Screening {todo} of {len(sessions)} sessions ({cache_path.name})")
    t0 = clock.time()
    screens = engine.screen_range(sessions, data, screener, sargs, args.top, screen_cache, log)
    log(f"Screens ready in {clock.time() - t0:.0f}s; simulating")

    bench_points = [
        (d, data.benchmark.by_date[d]["close"]) for d in sessions if d in data.benchmark.by_date
    ]

    def simulate(params):
        run = engine.Simulator(data, screens, params, planner, paper).run(sessions)
        return run, metrics.summarize_run(run, bench_points, args.benchmark)

    base_run, base_summary = simulate(base)
    sweep_rows = []
    for overrides in sweep:
        _, summary = simulate(engine.apply_overrides(base, overrides))
        sweep_rows.append({"overrides": overrides, "summary": summary})

    report = {
        "schema_version": "1.0",
        "skill": SKILL_ID,
        "exchange": india.EXCHANGE,
        "currency": india.CURRENCY,
        "generated_at": now_fn().astimezone(india.IST).isoformat(timespec="seconds"),
        "universe": universe_desc,
        "window": {
            "start": sessions[0],
            "end": sessions[-1],
            "sessions": len(sessions),
            "data_start": args.data_start,
        },
        "screener_settings": " ".join(screener_argv),
        "screen_cache": str(cache_path),
        "missing_symbols": [m for m in missing if not m.startswith("NOTE ")],
        "data_notes": [m[5:] for m in missing if m.startswith("NOTE ")],
        "base": {
            "params": asdict(base),
            "summary": base_summary,
            "open_positions": base_run["open_positions"],
        },
        "sweep": sweep_rows,
    }
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = out / f"india_vcp_backtest_{sessions[0]}_{sessions[-1]}"
    stem.with_suffix(".json").write_text(
        json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8"
    )
    stem.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    write_csv(Path(f"{stem}_trades.csv"), base_run["trades"])
    write_csv(Path(f"{stem}_equity.csv"), base_run["curve"])
    st, ts = base_summary["strategy"], base_summary["trade_stats"]
    print(
        f"India VCP backtest {sessions[0]} to {sessions[-1]}: return {_pct(st['total_return_pct'])} "
        f"vs {args.benchmark} {_pct(base_summary['benchmark']['total_return_pct'])}, max drawdown "
        f"{_pct(st['max_drawdown_pct'])}, {ts['trades']} closed trades, win rate "
        f"{_num((ts['win_rate'] or 0) * 100, 1)}%, avg R {_num(ts['avg_r'], 3)}"
    )
    print(f"JSON: {stem}.json")
    print(f"Markdown: {stem}.md")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
