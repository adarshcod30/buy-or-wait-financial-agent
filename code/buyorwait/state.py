"""Financial-state reconstruction.

Turns a user's raw ledger plus interpreted evidence into dated cash flows from request_date:

* known future rows (scheduled debits and credits, pending debits reserved immediately),
* recurring fixed commitments detected per description (rent, loans, subscriptions, bills),
* recurring income projected only for payroll-type descriptions,
* essential variable spending projected per category on its observed cadence with a
  per-occurrence budget (2 x minimum_allowed_amount for dining/streaming/entertainment/gym,
  minimum / 0.4 for shopping, otherwise the history mean),
* evidence facts that amend, confirm, delay or stop the above.

Every projected flow carries its provenance so the audit trail and explanation can cite it.
"""
from __future__ import annotations

import calendar
import datetime as dt
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .config import FORECAST_DAYS
from .budget import BudgetEstimate, estimate as estimate_budget, measure_widths
from .data import Dataset, Event, Profile
from .evidence_types import Fact
from .fx import Converter

VARIABLE_CATEGORIES = {"groceries", "transport", "dining", "shopping", "entertainment"}
BUDGET_RATIO = {"dining": 0.5, "entertainment": 0.5, "gym": 0.5, "streaming": 0.5, "shopping": 0.4}
RECURRING_INCOME_MARKERS = ("payroll", "salary", "household")
TERMINAL_INCOME_MARKERS = ("final",)
IGNORED_STATUSES = {"cancelled", "failed", "unrealized"}


@dataclass
class Flow:
    date: dt.date
    amount: float                     # positive credit, negative debit, home currency
    kind: str                         # scheduled | pending | fixed | variable | income | evidence
    label: str
    series_key: str = ""              # groups projected occurrences of one commitment
    source_event_id: str = ""
    category: str = ""
    flexibility: str = "fixed"
    source_fact: str = ""


@dataclass
class AdjustableSeries:
    """A recurring expense the user may stop or reduce."""
    key: str
    description: str
    category: str
    flexibility: str
    per_occurrence: float
    minimum_allowed_amount: Optional[float]
    latest_event_id: str
    can_stop: bool
    can_reduce: bool


@dataclass
class Policy:
    horizon_days: int = FORECAST_DAYS
    budget_quantile: Optional[float] = None   # None = the method's central value; else the posterior quantile
    budget_method: str = "mean"               # mean | posterior | midrange (see budget.py)
    variable_window_days: int = 180
    debits_before_credits: bool = False
    pending_debits_immediate: bool = True
    min_occurrences: int = 2
    stale_income_days: int = 45


@dataclass
class Reconstruction:
    profile: Profile
    request_date: dt.date
    opening_balance: float
    flows: List[Flow]
    adjustable: List[AdjustableSeries]
    notes: List[str] = field(default_factory=list)
    applied_facts: List[str] = field(default_factory=list)

    @property
    def horizon_end(self) -> dt.date:
        return self.request_date + dt.timedelta(days=FORECAST_DAYS)


# --------------------------------------------------------------------------- helpers

def add_months(d: dt.date, n: int) -> dt.date:
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    return dt.date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def cadence_days(dates: List[dt.date]) -> Optional[object]:
    """'M' for monthly, an int for a fixed number of days, None when irregular."""
    if len(dates) < 2:
        return None
    gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
    med = statistics.median(gaps)
    if 26 <= med <= 33:
        return "M"
    if 19 <= med <= 23:
        return 21
    if 12 <= med <= 16:
        return 14
    if 9 <= med <= 11:
        return 10
    if 5 <= med <= 8:
        return 7
    if 3 <= med < 5:
        return int(round(med))
    return None


def next_after(d: dt.date, cad) -> dt.date:
    return add_months(d, 1) if cad == "M" else d + dt.timedelta(days=int(cad))


def is_recurring_income(description: str) -> bool:
    s = description.lower()
    return any(m in s for m in RECURRING_INCOME_MARKERS)


def is_terminal_income(description: str) -> bool:
    return any(m in description.lower() for m in TERMINAL_INCOME_MARKERS)


def occurrence_budget(category: str, amounts: List[float], minimum_allowed: Optional[float]) -> float:
    ratio = BUDGET_RATIO.get(category)
    if minimum_allowed and ratio:
        return minimum_allowed / ratio
    return statistics.fmean(amounts)


# --------------------------------------------------------------------------- main

_WIDTHS: Dict[int, Dict[str, float]] = {}


def widths_for(ds: Dataset) -> Dict[str, float]:
    """Per-category noise half-widths, measured once from the corpus and memoised."""
    key = id(ds)
    if key not in _WIDTHS:
        _WIDTHS[key] = measure_widths(ds.events)
    return _WIDTHS[key]


def _budget(amounts: List[float], category: str, widths: Dict[str, float],
            minimum_allowed: Optional[float], policy: Policy) -> float:
    est = estimate_budget(amounts, category, widths.get(category, 0.0), minimum_allowed)
    if est.exact:
        return est.value
    if policy.budget_quantile is not None:
        from .budget import sample as sample_budget
        return sample_budget(est, policy.budget_quantile)
    if policy.budget_method == "mean":
        return statistics.fmean(amounts)
    if policy.budget_method == "midrange":
        return (min(amounts) + max(amounts)) / 2
    return est.value


def reconstruct(ds: Dataset, user_id: str, request_date: dt.date, facts: List[Fact],
                policy: Optional[Policy] = None) -> Reconstruction:
    policy = policy or Policy()
    profile = ds.profiles[user_id]
    widths = widths_for(ds)
    home = profile.home_currency
    conv = Converter(ds.rates)
    notes: List[str] = []
    applied: List[str] = []
    end = request_date + dt.timedelta(days=policy.horizon_days)

    resolved = {f.related_event_id: f.amount for f in facts
                if f.fact_type == "blank_amount_resolved" and f.related_event_id and f.amount is not None}

    def to_home(amount: float, cur: str, on: dt.date, label: str) -> float:
        if cur == home:
            return amount
        v, how = conv.convert(amount, cur, home, on)
        if how != "exact":
            notes.append(f"fx {label}: {cur}->{home} on {on} used {how}")
        return v

    events: List[Event] = []
    for e in ds.events_by_user.get(user_id, []):
        if e.amount is None:
            if e.event_id in resolved:
                e = Event(**{**e.__dict__, "amount": resolved[e.event_id]})
                applied.append(f"{e.event_id} amount {e.amount} from image")
            else:
                notes.append(f"blank amount {e.event_id} ({e.description}) unresolved; row excluded")
                continue
        events.append(e)

    flows: List[Flow] = []

    # ---- 1. known future rows -------------------------------------------------------------
    scheduled_salary: Optional[Flow] = None
    for e in events:
        if e.direction == "non_cash" or e.status in IGNORED_STATUSES:
            continue
        on = e.settlement_date
        if e.status == "settled" and on <= request_date:
            continue  # already reflected in the available balance
        if on < request_date and e.status != "pending":
            continue
        if on > end:
            continue
        amt = to_home(e.amount, e.currency, on, e.event_id)
        if e.direction == "debit":
            if e.status == "pending" and policy.pending_debits_immediate:
                on = request_date
            flows.append(Flow(on, -amt, e.status, e.description, f"event:{e.event_id}", e.event_id,
                              e.category, e.flexibility))
        else:
            if e.status == "pending":
                notes.append(f"pending credit {e.event_id} ({e.description}) not counted until it settles")
                continue
            f = Flow(on, amt, e.status, e.description, f"event:{e.event_id}", e.event_id, e.category)
            flows.append(f)
            if e.status == "scheduled" and e.category == "salary" and scheduled_salary is None:
                scheduled_salary = f

    history = [e for e in events if e.status == "settled" and e.settlement_date <= request_date
               and e.direction in ("debit", "credit")]

    # ---- 2. recurring income (payroll-type descriptions only, one series per description) --
    salary_hist = [e for e in history if e.category == "salary" and is_recurring_income(e.description)]
    by_income: Dict[str, List[Event]] = defaultdict(list)
    for e in salary_hist:
        by_income[e.description].append(e)
    ended_facts = [f for f in facts if f.fact_type == "income_ended"]
    confirmed = [f for f in facts if f.fact_type == "salary_confirmed" and f.amount]
    amount_changes = [f for f in facts if f.fact_type == "salary_amount_change" and f.amount]
    date_changes = [f for f in facts if f.fact_type == "salary_date_change" and f.effective_date]
    arrears = [f for f in facts if f.fact_type == "one_time_credit_confirmed" and f.amount]

    series: List[Tuple[str, dt.date, float, str]] = []   # (description, first_date, amount, anchor_event_id)
    latest_salary = max(salary_hist, key=lambda e: e.settlement_date) if salary_hist else None
    if latest_salary is not None and is_terminal_income(latest_salary.description):
        notes.append(f"{latest_salary.event_id} '{latest_salary.description}' is a final payroll; no salary projected")
        by_income = {}
    for desc, g in by_income.items():
        g.sort(key=lambda e: e.settlement_date)
        last = g[-1]
        if is_terminal_income(desc):
            notes.append(f"{last.event_id} '{desc}' is a final payroll; nothing projected from it")
            continue
        if len(g) < policy.min_occurrences:
            twin = [x for x in salary_hist if x.description != desc and abs(x.amount - last.amount) < 0.01
                    and abs((x.settlement_date - last.settlement_date).days) <= 7]
            if twin:
                notes.append(f"'{desc}' ({last.event_id}) duplicates {twin[0].event_id} in the same pay period; not projected separately")
                continue
            supported = any(abs(f.amount - last.amount) < 0.01 for f in facts
                            if f.fact_type in ("salary_amount_change", "salary_confirmed") and f.amount)
            if not supported:
                notes.append(f"'{desc}' seen once ({last.event_id}); recurrence not supported by history or evidence, not projected")
                continue
            notes.append(f"'{desc}' seen once ({last.event_id}) but confirmed by a message; projected")
        if (request_date - last.settlement_date).days > policy.stale_income_days:
            notes.append(f"'{desc}' last paid {last.settlement_date}, more than {policy.stale_income_days} days ago; treated as ended")
            continue
        amount = to_home(last.amount, last.currency, last.settlement_date, last.event_id)
        first = add_months(last.settlement_date, 1)
        while first <= request_date:
            first = add_months(first, 1)
        series.append((desc, first, amount, last.event_id))
    if not series and scheduled_salary is not None:
        series.append(("Next confirmed salary", add_months(scheduled_salary.date, 1), scheduled_salary.amount,
                       scheduled_salary.source_event_id))
    if not series:
        for f in (confirmed + amount_changes):
            if f.effective_date and f.effective_date > request_date:
                series.append((f"confirmed salary ({f.source_id})", f.effective_date,
                               to_home(f.amount, f.currency or home, f.effective_date, f.source_id), ""))
                applied.append(f"{f.source_id}: salary {f.amount} confirmed for {f.effective_date}")
                break
    # The scheduled "Next confirmed salary" row replaces the first projected occurrence of the series
    # it belongs to (matched by amount, else the largest series).
    series.sort(key=lambda t: -t[2])
    if series and scheduled_salary is not None:
        idx = next((i for i, t in enumerate(series) if abs(t[2] - scheduled_salary.amount) < 0.01), 0)
        desc, first, amount, anchor = series[idx]
        series[idx] = (desc, add_months(scheduled_salary.date, 1), scheduled_salary.amount, anchor)
    ended = bool(ended_facts) or (not series and bool(salary_hist))
    if ended_facts:
        f = ended_facts[-1]
        if f.amount:
            # "One household income has ended; the remaining confirmed monthly salary is X".
            keep = to_home(f.amount, f.currency or home, request_date, f.source_id)
            series = [(d, fd, keep, a) for d, fd, _, a in series[:1]]
            applied.append(f"{f.source_id}: income partly ended, remaining salary {keep:.2f}")
            ended = False
        else:
            applied.append(f"{f.source_id}: income ended, no salary projected")
            series = []
    if series:
        desc, first, amount, anchor = series[0]
        if date_changes:
            f = date_changes[-1]
            if f.effective_date > request_date:
                first = f.effective_date
                applied.append(f"{f.source_id}: next salary moved to {first}")
        eff, new_amt = first, amount
        if amount_changes:
            f = amount_changes[-1]
            new_amt = to_home(f.amount, f.currency or home, first, f.source_id)
            eff = f.effective_date if f.effective_date and f.effective_date > request_date else first
            applied.append(f"{f.source_id}: salary {amount:.2f} -> {new_amt:.2f} from {eff}")
        if confirmed and not amount_changes:
            f = confirmed[-1]
            if f.effective_date and f.effective_date > request_date and f.effective_date != first:
                first = f.effective_date
                applied.append(f"{f.source_id}: confirmed salary date {first}")
        series[0] = (desc, first, amount, anchor)
        d, i = first, 0
        while d <= end:
            a = new_amt if d >= eff else amount
            if scheduled_salary is not None and d == scheduled_salary.date:
                if abs(a - scheduled_salary.amount) > 0.005:
                    flows.append(Flow(d, a - scheduled_salary.amount, "evidence", "Salary adjustment", "income:salary",
                                      "", "salary", source_fact=amount_changes[-1].source_id if amount_changes else ""))
            else:
                flows.append(Flow(d, a, "income", f"Projected {desc.lower()}", "income:salary", anchor, "salary"))
            if i == 0:
                for f in arrears:
                    v = to_home(f.amount, f.currency or home, d, f.source_id)
                    flows.append(Flow(d, v, "evidence", "One-time arrears adjustment", f"fact:{f.source_id}",
                                      "", "salary", source_fact=f.source_id))
                    applied.append(f"{f.source_id}: arrears {v:.2f} with the next salary")
            d, i = add_months(d, 1), i + 1
        for desc, first, amount, anchor in series[1:]:
            d = first
            while d <= end:
                if not (scheduled_salary is not None and d == scheduled_salary.date):
                    flows.append(Flow(d, amount, "income", f"Projected {desc.lower()}", f"income:{desc}", anchor, "salary"))
                d = add_months(d, 1)

    for f in facts:
        if f.fact_type == "invoice_approved" and f.amount and f.effective_date:
            if request_date < f.effective_date <= end:
                v = to_home(f.amount, f.currency or home, f.effective_date, f.source_id)
                flows.append(Flow(f.effective_date, v, "evidence", "Approved invoice payment",
                                  f"fact:{f.source_id}", "", "salary", source_fact=f.source_id))
                applied.append(f"{f.source_id}: approved invoice {v:.2f} on {f.effective_date}")

    # ---- 3. recurring debits ---------------------------------------------------------------
    rent_pct = next((f.percent for f in facts if f.fact_type == "rent_increase_pct" and f.percent), None)
    debits = [e for e in history if e.direction == "debit"]
    adjustable: List[AdjustableSeries] = []
    by_cat: Dict[str, List[Event]] = defaultdict(list)
    for e in debits:
        by_cat[e.category].append(e)

    for cat, group in by_cat.items():
        group.sort(key=lambda e: (e.event_date, e.event_id))
        if cat in VARIABLE_CATEGORIES:
            recent = [e for e in group if e.event_date >= request_date - dt.timedelta(days=policy.variable_window_days)] or group
            cad = cadence_days([e.event_date for e in recent])
            amounts = [to_home(e.amount, e.currency, e.settlement_date, e.event_id) for e in recent]
            latest = group[-1]
            budget = _budget(amounts, cat, widths, latest.minimum_allowed_amount, policy)
            key = f"cat:{cat}"
            flex = latest.flexibility
            if flex != "fixed":
                adjustable.append(AdjustableSeries(key, latest.description, cat, flex, budget,
                                                   latest.minimum_allowed_amount, latest.event_id,
                                                   "stoppable" in flex, "reducible" in flex))
            if cad is None:
                span = max(1, (request_date - recent[0].event_date).days)
                daily = sum(amounts) / span
                d = request_date + dt.timedelta(days=1)
                while d <= end:
                    flows.append(Flow(d, -daily, "variable", f"{cat} (daily average)", key, latest.event_id, cat, flex))
                    d += dt.timedelta(days=1)
                continue
            d = latest.event_date
            while True:
                d = next_after(d, cad)
                if d > end:
                    break
                if d <= request_date:
                    continue
                flows.append(Flow(d, -budget, "variable", f"{cat} ({latest.description})", key, latest.event_id, cat, flex))
        else:
            by_desc: Dict[str, List[Event]] = defaultdict(list)
            for e in group:
                by_desc[e.description].append(e)
            for desc, g in by_desc.items():
                g.sort(key=lambda e: (e.event_date, e.event_id))
                if len(g) < policy.min_occurrences:
                    continue
                cad = cadence_days([e.event_date for e in g])
                if cad is None:
                    continue
                amounts = [to_home(e.amount, e.currency, e.settlement_date, e.event_id) for e in g]
                latest = g[-1]
                amt = (amounts[-1] if len(set(round(a, 2) for a in amounts)) == 1
                       else _budget(amounts, cat, widths, latest.minimum_allowed_amount, policy))
                if cat == "rent" and rent_pct:
                    amt = round(amt * (1 + rent_pct / 100.0), 2)
                    applied.append(f"rent {desc} raised {rent_pct}% to {amt:.2f}")
                key = f"desc:{desc}"
                flex = latest.flexibility
                if flex != "fixed":
                    adjustable.append(AdjustableSeries(key, desc, cat, flex, amt, latest.minimum_allowed_amount,
                                                       latest.event_id, "stoppable" in flex, "reducible" in flex))
                d = latest.event_date
                while True:
                    d = next_after(d, cad)
                    if d > end:
                        break
                    if d < request_date:
                        continue
                    flows.append(Flow(d, -amt, "fixed", desc, key, latest.event_id, cat, flex))

    flows.sort(key=lambda f: (f.date, f.amount))
    return Reconstruction(profile, request_date, profile.current_available_balance, flows, adjustable, notes, applied)
