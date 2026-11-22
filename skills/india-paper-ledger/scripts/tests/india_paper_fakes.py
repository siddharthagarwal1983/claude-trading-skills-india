"""Fake NSE bar source shared by the India paper ledger tests."""

import paper_ledger as pl


def bar(day, o, h, l, c):  # noqa: E741
    return {"date": day, "open": o, "high": h, "low": l, "close": c, "volume": 1000}


class FakeBars:
    """bars_fn stand-in: symbol -> {"bars": [...], "actions": [...]}."""

    def __init__(self, data=None):
        self.data = data or {}
        self.calls = []

    def __call__(self, symbol, start, end):
        self.calls.append((symbol, start, end))
        if symbol not in self.data:
            raise pl.SymbolNotFoundError(symbol)
        entry = self.data[symbol]
        bars = [b for b in entry["bars"] if start.isoformat() <= b["date"] <= end.isoformat()]
        return {"bars": bars, "actions": entry.get("actions", [])}
