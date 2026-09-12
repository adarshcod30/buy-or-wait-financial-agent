"""Dated currency conversion using only the supplied exchange_rates.csv.

Rules (AGENTS.md 6.1): use the row for the settlement date and the stated from->to direction.
When the exact date is missing we fall back to the nearest earlier rate, then to the inverse
pair, then to a two-hop route through USD or EUR. Every fallback is reported so the caller can
log it; nothing is invented.
"""
from __future__ import annotations

import datetime as dt
from typing import Dict, Optional, Tuple

HUBS = ("USD", "EUR")


class FxError(ValueError):
    pass


class Converter:
    def __init__(self, rates: Dict[Tuple[dt.date, str, str], float]):
        self.rates = rates
        self._by_pair: Dict[Tuple[str, str], list] = {}
        for (d, f, t), r in rates.items():
            self._by_pair.setdefault((f, t), []).append((d, r))
        for v in self._by_pair.values():
            v.sort()

    def _lookup(self, f: str, t: str, on: dt.date) -> Optional[Tuple[float, str]]:
        if f == t:
            return 1.0, "identity"
        r = self.rates.get((on, f, t))
        if r is not None:
            return r, "exact"
        inv = self.rates.get((on, t, f))
        if inv:
            return 1.0 / inv, "exact-inverse"
        series = self._by_pair.get((f, t))
        if series:
            prev = [x for x in series if x[0] <= on]
            if prev:
                return prev[-1][1], f"nearest-earlier:{prev[-1][0]}"
        series = self._by_pair.get((t, f))
        if series:
            prev = [x for x in series if x[0] <= on]
            if prev:
                return 1.0 / prev[-1][1], f"nearest-earlier-inverse:{prev[-1][0]}"
        return None

    def convert(self, amount: float, f: str, t: str, on: dt.date) -> Tuple[float, str]:
        hit = self._lookup(f, t, on)
        if hit:
            return amount * hit[0], hit[1]
        for hub in HUBS:
            a = self._lookup(f, hub, on)
            b = self._lookup(hub, t, on)
            if a and b:
                return amount * a[0] * b[0], f"via-{hub}({a[1]},{b[1]})"
        raise FxError(f"no rate for {f}->{t} on {on}")
