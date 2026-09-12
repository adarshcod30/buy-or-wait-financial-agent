import datetime as dt

from buyorwait.data import Profile, Request
from buyorwait.explain import check_consistency, money
from buyorwait.verify import verify_row


class _DS:
    def __init__(self):
        self.profiles = {"u": Profile("u", "EUR", 1000, 200, [], [], ["dining"], ["streaming"], ["full_payment", "partial_payment"], None)}
        self.events_by_id = {}
        self.options_by_request = {}


def _row(**kw):
    base = {"request_id": "r1", "amount_safe_to_pay": "500", "affordability_status": "affordable_now",
            "recommended_payment_method": "full_payment", "payment_plan": "2026-01-01:500",
            "earliest_date_for_full_payment": "2026-01-01", "spending_changes_needed": "none",
            "decision_explanation": "Pay EUR 500 today. This leaves at least EUR 200 available over the next 90 days."}
    base.update(kw)
    return base


REQ = Request("r1", "u", dt.date(2026, 1, 1), "purchase", 500.0, dt.date(2026, 2, 1), True, "")


def test_valid_row_passes():
    assert verify_row(_row(), REQ, _DS()) == []


def test_bounds_and_status_rules():
    assert any("outside" in p for p in verify_row(_row(amount_safe_to_pay="600"), REQ, _DS()))
    assert any("earliest" in p for p in verify_row(_row(earliest_date_for_full_payment="2026-01-05"), REQ, _DS()))
    bad = _row(affordability_status="not_affordable", recommended_payment_method="not_recommended")
    assert any("plan none" in p for p in verify_row(bad, REQ, _DS()))


def test_partial_rules():
    row = _row(amount_safe_to_pay="300", affordability_status="affordable_with_plan",
               recommended_payment_method="partial_payment", payment_plan="2026-01-01:300|2026-01-15:200",
               earliest_date_for_full_payment="2026-01-15",
               decision_explanation="Pay EUR 300 today and the remaining EUR 200 on 15 January 2026. This completes the full request and keeps the EUR 200 minimum protected.")
    assert verify_row(row, REQ, _DS()) == []
    assert check_consistency(row["decision_explanation"], row, REQ, _DS().profiles["u"]) == []
    row["payment_plan"] = "2026-01-01:300|2026-03-15:200"
    assert any("deadline" in p for p in verify_row(row, REQ, _DS()))


def test_money_formatting_follows_samples():
    assert money("EUR", 620.4) == "EUR 620.40" and money("ZAR", 25256) == "ZAR 25,256" and money("IDR", 15952906.67) == "IDR 15,952,906.67"


def test_consistency_rejects_wrong_numbers():
    row = _row(decision_explanation="Pay EUR 999 today. This leaves at least EUR 200 available over the next 90 days.")
    assert check_consistency(row["decision_explanation"], row, REQ, _DS().profiles["u"])
