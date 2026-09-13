"""Deterministic verification of one output row against the submission contract.

This is the last gate before a row is written. It does not trust the planner: it re-checks the
invariants from problem_statement.md and AGENTS.md section 6.2 on the rendered strings.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Dict, List

from .data import Dataset, Request

STATUSES = {"affordable_now", "affordable_with_plan", "affordable_later", "not_affordable"}
METHODS = {"full_payment", "partial_payment", "installments", "wait", "not_recommended"}
OUTPUT_COLUMNS = ["request_id", "amount_safe_to_pay", "affordability_status", "recommended_payment_method",
                  "payment_plan", "earliest_date_for_full_payment", "spending_changes_needed", "decision_explanation"]
_PLAN_RE = re.compile(r"^\d{4}-\d{2}-\d{2}:\d+(\.\d+)?$")
_CHANGE_RE = re.compile(r"^(stop:event_\d+|reduce_to:event_\d+:\d+(\.\d+)?)$")


def parse_plan(s: str) -> List[tuple]:
    if s == "none":
        return []
    out = []
    for part in s.split("|"):
        if not _PLAN_RE.match(part):
            raise ValueError(f"bad plan entry {part!r}")
        d, a = part.split(":")
        out.append((dt.date.fromisoformat(d), float(a)))
    return out


def verify_row(row: Dict[str, str], req: Request, ds: Dataset) -> List[str]:
    problems: List[str] = []
    try:
        safe = float(row["amount_safe_to_pay"])
    except ValueError:
        return [f"{req.request_id}: amount_safe_to_pay not numeric"]
    if not (-0.005 <= safe <= req.requested_amount + 0.005):
        problems.append(f"{req.request_id}: amount_safe_to_pay {safe} outside [0, {req.requested_amount}]")
    status, method = row["affordability_status"], row["recommended_payment_method"]
    if status not in STATUSES:
        problems.append(f"{req.request_id}: bad status {status}")
    if method not in METHODS:
        problems.append(f"{req.request_id}: bad method {method}")
    try:
        plan = parse_plan(row["payment_plan"])
    except ValueError as e:
        problems.append(f"{req.request_id}: {e}")
        plan = []
    earliest = row["earliest_date_for_full_payment"]
    if status == "affordable_now" and earliest != req.request_date.isoformat():
        problems.append(f"{req.request_id}: affordable_now requires earliest == request_date")
    if status == "not_affordable" and (method != "not_recommended" or plan):
        problems.append(f"{req.request_id}: not_affordable must be not_recommended with plan none")
    if method != "not_recommended" and not plan:
        problems.append(f"{req.request_id}: {method} needs a payment plan")
    if plan != sorted(plan, key=lambda p: p[0]):
        problems.append(f"{req.request_id}: plan not chronological")
    if plan and abs(sum(a for _, a in plan) - req.requested_amount) > 0.011 and method in ("full_payment", "partial_payment", "wait"):
        problems.append(f"{req.request_id}: plan total {sum(a for _, a in plan)} != requested {req.requested_amount}")
    if method == "partial_payment":
        if status != "affordable_with_plan":
            problems.append(f"{req.request_id}: partial_payment requires affordable_with_plan")
        if len(plan) != 2 or plan[0][0] != req.request_date or abs(plan[0][1] - safe) > 0.011:
            problems.append(f"{req.request_id}: partial plan must be safe amount today then remainder")
        if not (0 < safe < req.requested_amount):
            problems.append(f"{req.request_id}: partial requires 0 < safe < requested")
        if not req.allows_partial_payment:
            problems.append(f"{req.request_id}: request does not allow partial payment")
        if plan and plan[-1][0] > req.desired_completion_date:
            problems.append(f"{req.request_id}: partial remainder after the deadline")
        if plan and earliest != plan[-1][0].isoformat():
            problems.append(f"{req.request_id}: partial remainder date must equal earliest_date_for_full_payment")
    if method == "installments":
        opts = ds.options_by_request.get(req.request_id, [])
        match = any(o.payment_method == "installments" and
                    [(max(d, req.request_date), round(a, 2)) for d, a in o.schedule()] == [(d, round(a, 2)) for d, a in plan]
                    for o in opts)
        if not match:
            problems.append(f"{req.request_id}: installment plan does not match a supplied option")
    if method == "wait" and status != "affordable_later":
        problems.append(f"{req.request_id}: wait requires affordable_later")
    if method == "full_payment" and status not in ("affordable_now", "affordable_with_plan"):
        problems.append(f"{req.request_id}: full_payment requires affordable_now or affordable_with_plan")
    prof = ds.profiles[req.user_id]
    if method != "not_recommended" and method not in prof.payment_methods and method != "wait":
        problems.append(f"{req.request_id}: {method} not accepted by the user")
    if method == "wait" and "full_payment" not in prof.payment_methods:
        problems.append(f"{req.request_id}: wait needs full_payment accepted")
    changes = row["spending_changes_needed"]
    if changes != "none":
        parts = changes.split("|")
        if len(parts) > 3:
            problems.append(f"{req.request_id}: more than three spending changes")
        seen = set()
        for p in parts:
            if not _CHANGE_RE.match(p):
                problems.append(f"{req.request_id}: bad change {p!r}")
                continue
            eid = p.split(":")[1]
            if eid in seen:
                problems.append(f"{req.request_id}: stop and reduce on the same event {eid}")
            seen.add(eid)
            ev = ds.events_by_id.get(eid)
            if ev is None or ev.user_id != req.user_id:
                problems.append(f"{req.request_id}: change references a foreign or unknown event {eid}")
                continue
            if ev.flexibility == "fixed":
                problems.append(f"{req.request_id}: change targets a fixed event {eid}")
            if ev.category in prof.protect:
                problems.append(f"{req.request_id}: change targets protected category {ev.category}")
            if p.startswith("stop:") and ("stoppable" not in ev.flexibility or ev.category not in prof.willing_to_stop):
                problems.append(f"{req.request_id}: stop not permitted for {eid}")
            if p.startswith("reduce_to:"):
                new_amt = float(p.split(":")[2])
                if "reducible" not in ev.flexibility or ev.category not in prof.willing_to_reduce:
                    problems.append(f"{req.request_id}: reduce not permitted for {eid}")
                if ev.minimum_allowed_amount is not None and new_amt < ev.minimum_allowed_amount - 0.005:
                    problems.append(f"{req.request_id}: reduce_to below minimum_allowed_amount for {eid}")
        if status != "affordable_with_plan":
            problems.append(f"{req.request_id}: spending changes require affordable_with_plan")
    if not row["decision_explanation"].strip():
        problems.append(f"{req.request_id}: empty explanation")
    return problems
