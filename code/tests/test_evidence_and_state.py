import datetime as dt

import pytest

from buyorwait.data import Message
from buyorwait.evidence import regex_fact
from buyorwait.evidence_types import Fact
from buyorwait.state import cadence_days, is_recurring_income, is_terminal_income, occurrence_budget


def _msg(text, mid="m1"):
    return Message(mid, "u", "", "", "2026-01-01T00:00:00Z", "employer", text)


def test_regex_salary_change_with_date():
    f = regex_fact(_msg("Your monthly salary has increased to USD 2424. The change applies from 2026-07-15."))
    assert f.fact_type == "salary_amount_change" and f.amount == 2424 and f.effective_date == dt.date(2026, 7, 15)


def test_regex_indonesian_invoice_and_scam():
    f = regex_fact(_msg("Klien menyetujui pembayaran faktur sebesar IDR 30780000. Penyelesaian diperkirakan pada 2025-08-15;"))
    assert f.fact_type == "invoice_approved" and f.amount == 30780000 and f.effective_date == dt.date(2025, 8, 15)
    s = regex_fact(_msg("Congratulations! Pay the release charge today to receive the funds immediately."))
    assert s.fact_type == "scam_solicitation" and not s.is_cash_affecting()


def test_regex_pending_and_transfer_have_no_cash_effect():
    for t in ("The client approved an invoice payment is still pending approval.",
              "The matching debit and credit came from a transfer between your two accounts."):
        assert not regex_fact(_msg(t)).is_cash_affecting()


def test_income_classification_and_cadence():
    assert is_recurring_income("Payroll credit") and is_recurring_income("Second household income")
    assert not is_recurring_income("Delivery platform payout") and not is_recurring_income("Quarterly performance bonus")
    assert is_terminal_income("Final employer payroll")
    d0 = dt.date(2026, 1, 1)
    assert cadence_days([d0 + dt.timedelta(days=7 * i) for i in range(5)]) == 7
    assert cadence_days([dt.date(2026, m, 5) for m in range(1, 6)]) == "M"
    assert cadence_days([d0, d0 + dt.timedelta(days=40), d0 + dt.timedelta(days=100)]) is None


def test_budget_uses_minimum_allowed_ratio():
    assert occurrence_budget("dining", [40, 60], 24.5) == 49.0
    assert occurrence_budget("shopping", [100], 49.6) == pytest.approx(124.0)
    assert occurrence_budget("groceries", [40, 60], None) == 50.0


def test_sample_pipeline_contract(ds):
    from buyorwait.pipeline import decide_request
    for s in ds.samples:
        res = decide_request(ds, s.request, [])
        assert res.problems == [], (s.request.request_id, res.problems)
        assert 0 <= float(res.row["amount_safe_to_pay"]) <= s.request.requested_amount
