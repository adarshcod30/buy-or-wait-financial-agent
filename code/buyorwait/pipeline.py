"""End-to-end decision for one request: evidence -> reconstruction -> forecast -> plan -> row."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional

from .data import Dataset, Request
from .evidence_types import Fact
from .explain import check_consistency, render
from .planner import Decision, Plan, decide, fmt_amount
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


def _render_row(req: Request, dec: Decision, plan, prof) -> Dict[str, str]:
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
    return row


def decide_request(ds: Dataset, req: Request, facts: List[Fact], policy: Optional[Policy] = None) -> RowResult:
    """Decide one request. If the top-ranked plan fails contract verification, fall back to the
    next-ranked plan rather than emitting an invalid row: a formatting defect scores zero on that
    row, whereas the second-best safe plan is still a legitimate recommendation."""
    policy = policy or Policy()
    rec = reconstruct(ds, req.user_id, req.request_date, facts, policy)
    dec = decide(rec, req, ds.options_by_request.get(req.request_id, []), policy.debits_before_credits,
                 policy.capacity_horizon)
    prof = ds.profiles[req.user_id]

    attempts = list(dec.candidates) if dec.plan is not None else [None]
    attempts.sort(key=lambda p: p.sort_key(req.desired_completion_date) if p is not None else ())
    first_row = first_problems = None
    repaired_from = None
    for i, plan in enumerate(attempts):
        trial = replace(dec, plan=plan, method=(plan.method if plan else "not_recommended"),
                        status=_status_for(plan, dec))
        row = _render_row(req, trial, plan, prof)
        row["decision_explanation"] = render(trial, req, prof)
        problems = verify_row(row, req, ds) + check_consistency(row["decision_explanation"], row, req, prof)
        if first_row is None:
            first_row, first_problems = row, problems
        if not problems:
            if i > 0:
                repaired_from = attempts[0].method if attempts[0] else "not_recommended"
                rec.notes.append(f"top-ranked plan {repaired_from} failed verification "
                                 f"({'; '.join(first_problems)}); fell back to {plan.method if plan else 'not_recommended'}")
            return RowResult(req.request_id, row, trial, rec, [], rec.applied_facts)
    # Nothing verified: emit the top-ranked row with its problems attached so the failure is visible.
    return RowResult(req.request_id, first_row, dec, rec, first_problems, rec.applied_facts)


def _status_for(plan, dec: Decision) -> str:
    if plan is None:
        return "not_affordable"
    if plan.method == "full_payment" and not plan.changes:
        return "affordable_now"
    if plan.method == "wait":
        return "affordable_later"
    return "affordable_with_plan"
