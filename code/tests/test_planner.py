import datetime as dt

from buyorwait.data import PaymentOption, Profile, Request
from buyorwait.planner import Plan, decide, fmt_amount
from buyorwait.state import AdjustableSeries, Flow, Reconstruction


def _req(amount=500.0, allows_partial=True, deadline=dt.date(2026, 2, 20)):
    return Request("r1", "u", dt.date(2026, 1, 1), "purchase", amount, deadline, allows_partial, "text")


def _rec(methods, balance=1000.0, minimum=200.0, flows=None, adjustable=None, max_months=None,
         stop=(), reduce=()):
    prof = Profile("u", "EUR", balance, minimum, [], ["rent"], list(reduce), list(stop), methods, max_months)
    return Reconstruction(prof, dt.date(2026, 1, 1), balance, flows or [], adjustable or [])


def test_fmt_amount_matches_sample_style():
    assert fmt_amount(620.4) == "620.40" and fmt_amount(25256) == "25256" and fmt_amount(15952906.666) == "15952906.67"


def test_affordable_now_when_full_is_safe_and_accepted():
    d = decide(_rec(["full_payment"]), _req(), [])
    assert d.status == "affordable_now" and d.method == "full_payment" and d.earliest_full_payment == dt.date(2026, 1, 1)


def test_installments_when_full_not_accepted():
    opt = PaymentOption("payment_option_2", "r1", "installments", 250, 2, dt.date(2026, 1, 10), 30, 0, 500)
    d = decide(_rec(["installments"], max_months=6), _req(), [opt])
    assert d.method == "installments" and d.status == "affordable_with_plan"
    assert d.plan.payments == [(dt.date(2026, 1, 10), 250), (dt.date(2026, 2, 9), 250)]


def test_installments_rejected_beyond_max_months():
    opt = PaymentOption("payment_option_2", "r1", "installments", 50, 10, dt.date(2026, 1, 10), 30, 0, 500)
    d = decide(_rec(["installments"], max_months=6), _req(), [opt])
    assert d.method == "not_recommended" and any("exceed" in r for r in d.rejected)


def test_wait_when_full_becomes_safe_later():
    flows = [Flow(dt.date(2026, 1, 15), 900, "income", "salary")]
    d = decide(_rec(["full_payment"], balance=600, flows=flows), _req(amount=500), [])
    assert d.method == "wait" and d.status == "affordable_later" and d.plan.payments == [(dt.date(2026, 1, 15), 500)]


def test_partial_beats_wait_because_it_starts_earlier():
    flows = [Flow(dt.date(2026, 1, 15), 900, "income", "salary")]
    d = decide(_rec(["full_payment", "partial_payment"], balance=600, flows=flows), _req(amount=500), [])
    assert d.method == "partial_payment"
    assert d.plan.payments == [(dt.date(2026, 1, 1), 400.0), (dt.date(2026, 1, 15), 100.0)]


def test_spending_change_completes_by_deadline_and_beats_late_wait():
    flows = [Flow(dt.date(2026, 1, 10), -50, "fixed", "Family streaming plan", series_key="desc:stream", flexibility="stoppable"),
             Flow(dt.date(2026, 3, 15), 900, "income", "salary")]
    adj = [AdjustableSeries("desc:stream", "Family streaming plan", "streaming", "stoppable", 50, None, "event_9", True, False)]
    rec = _rec(["full_payment"], balance=740, flows=flows, adjustable=adj, stop=["streaming"])
    d = decide(rec, _req(amount=500, deadline=dt.date(2026, 1, 31)), [])
    assert d.status == "affordable_with_plan" and d.method == "full_payment"
    assert [c.render() for c in d.plan.changes] == ["stop:event_9"]


def test_not_affordable_when_nothing_is_eligible():
    d = decide(_rec(["partial_payment"], balance=300), _req(amount=500, allows_partial=False), [])
    assert d.status == "not_affordable" and d.method == "not_recommended" and d.plan is None
