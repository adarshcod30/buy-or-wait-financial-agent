"""End-to-end decision for one request: evidence -> reconstruction -> forecast -> plan -> row."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .data import Dataset, Request
from .evidence_types import Fact
from .explain import check_consistency, render
from .planner import Decision, decide, fmt_amount
from .state import Policy, Reconstruction, reconstruct
from .verify import OUTPUT_COLUMNS, verify_row


@dataclass
class RowResult:
    request_id: str
    row: Dict[str, str]
    decision: Decision
    reconstruction: Reconstruction
    problems: List[str] = field(default_factory=list)
    facts_used: List[str] = field(default_factory=list)


def safe_repr(x: float) -> str:
    x = round(x + 1e-9, 2)
    s = f"{x:.2f}".rstrip("0").rstrip(".")
    return s if s else "0"


def decide_request(ds: Dataset, req: Request, facts: List[Fact], policy: Optional[Policy] = None) -> RowResult:
    policy = policy or Policy()
    rec = reconstruct(ds, req.user_id, req.request_date, facts, policy)
    dec = decide(rec, req, ds.options_by_request.get(req.request_id, []), policy.debits_before_credits)
    prof = ds.profiles[req.user_id]
    plan = dec.plan
    row = {
        "request_id": req.request_id,
        "amount_safe_to_pay": safe_repr(dec.amount_safe_to_pay),
        "affordability_status": dec.status,
        "recommended_payment_method": dec.method,
        "payment_plan": "|".join(f"{d.isoformat()}:{fmt_amount(a)}" for d, a in plan.payments) if plan else "none",
        "earliest_date_for_full_payment": dec.earliest_full_payment.isoformat() if dec.earliest_full_payment else "",
        "spending_changes_needed": "|".join(c.render() for c in plan.changes) if plan and plan.changes else "none",
        "decision_explanation": "",
    }
    row["decision_explanation"] = render(dec, req, prof)
    problems = verify_row(row, req, ds) + check_consistency(row["decision_explanation"], row, req, prof)
    return RowResult(req.request_id, row, dec, rec, problems, rec.applied_facts)
