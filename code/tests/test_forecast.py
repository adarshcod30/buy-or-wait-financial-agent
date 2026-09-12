import datetime as dt

from buyorwait.data import Profile
from buyorwait.forecast import amount_safe_to_pay, earliest_full_payment_date, is_safe, simulate
from buyorwait.state import Flow, Reconstruction


def _rec(balance=1000.0, minimum=200.0, flows=None):
    prof = Profile("u", "EUR", balance, minimum, [], [], [], [], ["full_payment"], None)
    return Reconstruction(prof, dt.date(2026, 1, 1), balance, flows or [], [])


def test_trough_and_safe_amount_with_salary():
    flows = [Flow(dt.date(2026, 1, 5), -300, "fixed", "rent"), Flow(dt.date(2026, 1, 15), 900, "income", "salary"),
             Flow(dt.date(2026, 2, 5), -300, "fixed", "rent")]
    rec = _rec(flows=flows)
    p = simulate(rec)
    assert p.trough == 700 and p.trough_date == dt.date(2026, 1, 5)
    safe, _ = amount_safe_to_pay(rec, 10_000)
    assert safe == 500.0
    assert earliest_full_payment_date(rec, 600) == dt.date(2026, 1, 15)
    assert earliest_full_payment_date(rec, 5000) is None


def test_debits_before_credits_exposes_salary_day_shortfall():
    flows = [Flow(dt.date(2026, 1, 15), -900, "fixed", "rent"), Flow(dt.date(2026, 1, 15), 900, "income", "salary")]
    rec = _rec(flows=flows)
    assert simulate(rec).trough == 1000
    assert simulate(rec, debits_before_credits=True).trough == 100


def test_adjustments_replace_series_amount():
    flows = [Flow(dt.date(2026, 1, 10), -100, "fixed", "streaming", series_key="desc:streaming")]
    rec = _rec(flows=flows)
    assert not is_safe(rec, [(dt.date(2026, 1, 1), 750)])
    assert is_safe(rec, [(dt.date(2026, 1, 1), 750)], adjustments={"desc:streaming": 0.0})
