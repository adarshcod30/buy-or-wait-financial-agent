"""90-day balance simulation and the two capacity numbers derived from it.

amount_safe_to_pay     = clamp(min projected balance - minimum_balance_to_keep, 0, requested)
earliest_full_payment  = first day d in the horizon where paying the full amount on d keeps the
                         balance at or above the minimum on every later day
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .config import MONEY_EPS
from .state import Reconstruction

Adjustments = Dict[str, float]   # series_key -> replacement per-occurrence amount (0 = stopped)


@dataclass
class Path:
    dates: List[dt.date]
    balances: List[float]
    trough: float
    trough_date: dt.date


def simulate(rec: Reconstruction, payments: Optional[List[Tuple[dt.date, float]]] = None,
             adjustments: Optional[Adjustments] = None, debits_before_credits: bool = False) -> Path:
    """Day-by-day balance. `payments` are (date, amount) debits for the plan under test.
    `adjustments` replace the per-occurrence amount of an adjustable series."""
    start, end = rec.request_date, rec.horizon_end
    debits: Dict[dt.date, float] = {}
    credits: Dict[dt.date, float] = {}
    for f in rec.flows:
        amt = f.amount
        if adjustments and f.series_key in adjustments and amt < 0:
            # Adjustments are expressed per occurrence; an accrued flow carries the scale that
            # converts a per-occurrence amount into its daily share.
            amt = -adjustments[f.series_key] * f.scale
        tgt = credits if amt > 0 else debits
        tgt[f.date] = tgt.get(f.date, 0.0) + amt
    for d, a in payments or []:
        d = max(d, start)
        debits[d] = debits.get(d, 0.0) - abs(a)
    bal = rec.opening_balance
    dates: List[dt.date] = []
    bals: List[float] = []
    trough, trough_date = bal, start
    d = start
    while d <= end:
        if debits_before_credits:
            bal += debits.get(d, 0.0)
            if bal < trough - MONEY_EPS:
                trough, trough_date = bal, d
            bal += credits.get(d, 0.0)
        else:
            bal += debits.get(d, 0.0) + credits.get(d, 0.0)
            if bal < trough - MONEY_EPS:
                trough, trough_date = bal, d
        dates.append(d)
        bals.append(bal)
        d += dt.timedelta(days=1)
    return Path(dates, bals, trough, trough_date)


def is_safe(rec: Reconstruction, payments: List[Tuple[dt.date, float]],
            adjustments: Optional[Adjustments] = None, **kw) -> bool:
    p = simulate(rec, payments, adjustments, **kw)
    return p.trough >= rec.profile.minimum_balance_to_keep - MONEY_EPS


def amount_safe_to_pay(rec: Reconstruction, requested: float, **kw) -> Tuple[float, Path]:
    p = simulate(rec, **kw)
    room = p.trough - rec.profile.minimum_balance_to_keep
    return round(max(0.0, min(requested, room)) + 1e-9, 2), p


def earliest_full_payment_date(rec: Reconstruction, requested: float,
                               adjustments: Optional[Adjustments] = None, **kw) -> Optional[dt.date]:
    p = simulate(rec, adjustments=adjustments, **kw)
    minimum = rec.profile.minimum_balance_to_keep
    suffix_min = [0.0] * len(p.balances)
    m = float("inf")
    for i in range(len(p.balances) - 1, -1, -1):
        m = min(m, p.balances[i])
        suffix_min[i] = m
    for i, d in enumerate(p.dates):
        if suffix_min[i] - requested >= minimum - MONEY_EPS:
            return d
    return None
