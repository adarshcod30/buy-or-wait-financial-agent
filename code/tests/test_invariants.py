"""Metamorphic and invariant tests.

Matching the 25 published samples only proves the system reproduces 25 points in the input space.
These tests assert properties that must hold for *every* input, including the hidden ones, by
perturbing a scenario and asserting the direction the answer is allowed to move. They catch the
class of failure that sample matching cannot: a rule that happens to be right on the samples and
wrong on a combination the samples never exercise.
"""
from __future__ import annotations

import datetime as dt

import pytest

from buyorwait.data import PaymentOption, Profile, Request
from buyorwait.forecast import amount_safe_to_pay, simulate
from buyorwait.planner import decide
from buyorwait.state import AdjustableSeries, Flow, Reconstruction

RD = dt.date(2026, 1, 1)


def _profile(minimum=200.0, balance=1000.0, methods=("full_payment",), protect=(), reduce=(), stop=(),
             max_months=None):
    return Profile("u", "EUR", balance, minimum, [], list(protect), list(reduce), list(stop),
                   list(methods), max_months)


def _rec(flows=None, adjustable=None, **kw):
    p = _profile(**kw)
    return Reconstruction(p, RD, p.current_available_balance, list(flows or []), list(adjustable or []))


def _req(amount=500.0, allows_partial=True, deadline=dt.date(2026, 3, 1)):
    return Request("r1", "u", RD, "purchase", amount, deadline, allows_partial, "")


BASE_FLOWS = [
    Flow(dt.date(2026, 1, 8), -120.0, "fixed", "Rent", "desc:Rent", "event_1", "rent"),
    Flow(dt.date(2026, 1, 20), 400.0, "income", "Projected salary", "income:salary", "event_2", "salary"),
    Flow(dt.date(2026, 2, 8), -120.0, "fixed", "Rent", "desc:Rent", "event_1", "rent"),
]


def _capacity(flows=None, **kw):
    return amount_safe_to_pay(_rec(flows or BASE_FLOWS, **kw), 10_000)[0]


# --------------------------------------------------------------- monotonicity of safe capacity

def test_increasing_an_expense_cannot_increase_capacity():
    bigger = [Flow(f.date, f.amount * 2 if f.amount < 0 else f.amount, f.kind, f.label, f.series_key,
                   f.source_event_id, f.category) for f in BASE_FLOWS]
    assert _capacity(bigger) <= _capacity()


def test_removing_a_debit_cannot_decrease_capacity():
    without_rent = [f for f in BASE_FLOWS if f.category != "rent"]
    assert _capacity(without_rent) >= _capacity()


def test_raising_the_minimum_balance_cannot_increase_capacity():
    assert _capacity(minimum=400.0) <= _capacity(minimum=200.0)


def test_adding_income_cannot_decrease_capacity():
    with_bonus = BASE_FLOWS + [Flow(dt.date(2026, 1, 5), 250.0, "income", "Extra", "income:x", "e", "salary")]
    assert _capacity(with_bonus) >= _capacity()


def test_a_larger_opening_balance_cannot_decrease_capacity():
    assert _capacity(balance=2000.0) >= _capacity(balance=1000.0)


def test_capacity_never_exceeds_the_request_and_is_never_negative():
    rec = _rec(BASE_FLOWS, balance=100.0, minimum=500.0)     # already below the minimum
    safe, _ = amount_safe_to_pay(rec, 500.0)
    assert safe == 0.0
    rich, _ = amount_safe_to_pay(_rec(BASE_FLOWS, balance=10 ** 6), 500.0)
    assert rich == 500.0


def test_a_non_cash_flow_never_reaches_the_forecast():
    """An unrealized investment gain must not raise capacity. `state.reconstruct` drops non_cash
    rows, so the invariant is that no such Flow can exist; this pins the forecast side of it."""
    inflated = BASE_FLOWS + [Flow(dt.date(2026, 1, 3), 5000.0, "unrealized", "Portfolio", "x", "e", "investment")]
    assert _capacity(inflated) > _capacity()          # it *would* inflate capacity if admitted
    from buyorwait.state import IGNORED_STATUSES
    assert "unrealized" in IGNORED_STATUSES           # which is why reconstruct never admits it


# ------------------------------------------------------------------------- plan-shape invariants

def _decide(options=(), **kw):
    return decide(_rec(BASE_FLOWS, **kw), _req(), list(options))


def test_not_recommended_implies_no_plan():
    d = decide(_rec(BASE_FLOWS, balance=300.0, methods=("full_payment",)), _req(amount=5000.0), [])
    assert d.method == "not_recommended" and d.plan is None and d.status == "not_affordable"


def test_partial_plan_is_exactly_two_payments_summing_to_the_request():
    d = decide(_rec(BASE_FLOWS, balance=600.0, methods=("full_payment", "partial_payment")), _req(amount=500.0), [])
    if d.method == "partial_payment":
        assert len(d.plan.payments) == 2
        assert abs(sum(a for _, a in d.plan.payments) - 500.0) < 0.011
        assert d.plan.payments[0][0] == RD


def test_installment_plan_totals_the_supplied_option_including_fees():
    opt = PaymentOption("payment_option_1", "r1", "installments", 200.0, 3, dt.date(2026, 1, 5), 30, 100.0, 600.0)
    d = decide(_rec(BASE_FLOWS, balance=5000.0, methods=("installments",), max_months=6), _req(amount=500.0), [opt])
    assert d.method == "installments"
    assert abs(d.plan.total_paid - opt.total_payable_amount) < 0.011
    assert d.plan.total_paid > 500.0                 # financing fees make it cost more than requested


def test_a_plan_missing_the_deadline_loses_to_one_that_meets_it():
    early = decide(_rec(BASE_FLOWS, balance=5000.0, methods=("full_payment",)),
                   _req(amount=100.0, deadline=dt.date(2026, 1, 2)), [])
    assert early.plan.completes_by(dt.date(2026, 1, 2))


def test_every_returned_plan_is_actually_safe():
    """The counterfactual forecast for each candidate must hold the minimum on every date."""
    opt = PaymentOption("payment_option_1", "r1", "installments", 100.0, 3, dt.date(2026, 1, 5), 30, 0.0, 300.0)
    rec = _rec(BASE_FLOWS, balance=1200.0, methods=("full_payment", "partial_payment", "installments"), max_months=6)
    d = decide(rec, _req(amount=300.0), [opt])
    for plan in d.candidates:
        adj = {c.series_key: c.new_amount for c in plan.changes}
        path = simulate(rec, plan.payments, adj)
        assert path.trough >= rec.profile.minimum_balance_to_keep - 0.005, plan.method


# ----------------------------------------------------------------- spending-change permissions

def _adjustable_rec(category, flexibility, protect=(), reduce=(), stop=(), minimum_allowed=None):
    flows = BASE_FLOWS + [Flow(dt.date(2026, 1, 6), -300.0, "fixed", "Plan", f"desc:{category}", "event_9",
                               category, flexibility)]
    adj = [AdjustableSeries(f"desc:{category}", "Plan", category, flexibility, 300.0, minimum_allowed,
                            "event_9", "stoppable" in flexibility, "reducible" in flexibility)]
    return _rec(flows, adj, balance=800.0, minimum=200.0, methods=("full_payment",),
                protect=protect, reduce=reduce, stop=stop)


def test_a_protected_category_is_never_modified():
    rec = _adjustable_rec("streaming", "stoppable", protect=("streaming",), stop=("streaming",))
    d = decide(rec, _req(amount=500.0), [])
    assert all(c.series_key != "desc:streaming" for p in d.candidates for c in p.changes)


def test_a_category_the_user_did_not_permit_is_never_modified():
    rec = _adjustable_rec("streaming", "stoppable", stop=())        # user permits nothing
    d = decide(rec, _req(amount=500.0), [])
    assert all(not p.changes for p in d.candidates)


def test_a_fixed_series_is_never_modified():
    rec = _adjustable_rec("streaming", "fixed", stop=("streaming",), reduce=("streaming",))
    d = decide(rec, _req(amount=500.0), [])
    assert all(not p.changes for p in d.candidates)


def test_reduce_never_goes_below_the_minimum_allowed_amount():
    rec = _adjustable_rec("dining", "reducible", reduce=("dining",), minimum_allowed=150.0)
    d = decide(rec, _req(amount=500.0), [])
    for p in d.candidates:
        for c in p.changes:
            if c.action == "reduce_to":
                assert c.new_amount >= 150.0 - 0.005


def test_stop_and_reduce_never_target_the_same_series():
    rec = _adjustable_rec("streaming", "reducible_or_stoppable", reduce=("streaming",), stop=("streaming",),
                          minimum_allowed=100.0)
    d = decide(rec, _req(amount=500.0), [])
    for p in d.candidates:
        keys = [c.series_key for c in p.changes]
        assert len(keys) == len(set(keys))


def test_at_most_three_spending_changes():
    flows = list(BASE_FLOWS)
    adj = []
    for i in range(6):
        cat = f"cat{i}"
        flows.append(Flow(dt.date(2026, 1, 6), -50.0, "fixed", f"S{i}", f"desc:{cat}", f"event_{i}", cat, "stoppable"))
        adj.append(AdjustableSeries(f"desc:{cat}", f"S{i}", cat, "stoppable", 50.0, None, f"event_{i}", True, False))
    rec = _rec(flows, adj, balance=700.0, minimum=200.0, methods=("full_payment",),
               stop=[f"cat{i}" for i in range(6)])
    d = decide(rec, _req(amount=500.0), [])
    for p in d.candidates:
        assert len(p.changes) <= 3


# ------------------------------------------------------------------------------ proof integrity

def test_every_rejected_plan_has_a_failure_date_inside_the_horizon():
    rec = _rec(BASE_FLOWS, balance=400.0, methods=("full_payment",))
    d = decide(rec, _req(amount=500.0), [])
    for pr in d.proofs:
        if not pr.valid:
            assert pr.first_failure_date is not None
            assert RD <= pr.first_failure_date <= rec.horizon_end
            assert pr.lowest_balance < rec.profile.minimum_balance_to_keep
