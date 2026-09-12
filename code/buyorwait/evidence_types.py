"""Typed facts produced by the evidence layer (messages and images).

The model never touches money directly: it maps untrusted text or pixels to one of these
closed fact types, and the deterministic reconstruction decides what a fact is allowed to
change. Anything not representable here has no effect on the forecast.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import List, Optional

FACT_TYPES = (
    "salary_amount_change",        # raised, reduced, temporary pay, resumes at an amount
    "salary_date_change",          # next payroll lands on a different date
    "salary_confirmed",            # first or confirmed salary with amount and date
    "income_ended",                # employment, seasonal contract or one household income ended
    "income_pending_not_counted",  # bonus, commission, payout, prize, refund or dispute still pending
    "invoice_approved",            # one specific approved credit with an expected settlement date
    "one_time_credit_confirmed",   # arrears or one-off adjustment paid with the next salary
    "rent_increase_pct",           # lease renewal raises rent by a percentage
    "new_recurring_debit",         # a new recurring expense begins (amount usually unknown)
    "internal_transfer",           # matching debit and credit between the user's own accounts
    "unrealized_value_change",     # portfolio value moved, no cash
    "already_settled_credit",      # proceeds or reimbursement already received, nothing further
    "failed_debit_retry",          # a failed bill debit will be retried
    "fx_settlement_note",          # foreign salary converts at the settlement-date rate
    "card_minimums_separate",      # two card minimums are separate obligations
    "scam_solicitation",           # pay-a-fee-to-receive-prize; must have no cash effect
    "blank_amount_resolved",       # amount read from an image for a blank event row
    "other",
)

# Fact types that are allowed to change the projected cash flows.
CASH_AFFECTING = {
    "salary_amount_change", "salary_date_change", "salary_confirmed", "income_ended",
    "invoice_approved", "one_time_credit_confirmed", "rent_increase_pct", "blank_amount_resolved",
}


@dataclass
class Fact:
    fact_type: str
    source_id: str                      # message_id or image_id
    source_kind: str                    # message | image
    related_event_id: str = ""
    request_id: str = ""
    amount: Optional[float] = None
    currency: Optional[str] = None
    effective_date: Optional[dt.date] = None
    percent: Optional[float] = None
    confidence: float = 1.0
    provenance: str = ""                # model id or "regex"
    raw: str = ""                       # short quote of the evidence line

    def is_cash_affecting(self) -> bool:
        return self.fact_type in CASH_AFFECTING


@dataclass
class EvidenceBundle:
    facts: List[Fact] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def resolved_amounts(self) -> dict:
        return {f.related_event_id: f.amount for f in self.facts
                if f.fact_type == "blank_amount_resolved" and f.related_event_id and f.amount is not None}

    def for_user(self, request_id: str) -> List[Fact]:
        """Facts that apply to a request: request-specific ones plus user-level ones."""
        return [f for f in self.facts if not f.request_id or f.request_id == request_id]
