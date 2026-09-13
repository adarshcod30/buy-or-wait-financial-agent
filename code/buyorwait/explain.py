"""Explanation rendering.

The wording follows the six patterns in sample_requests.csv exactly; every number is taken from
the decision object, never typed by a model. A consistency check (`check_consistency`) refuses
an explanation whose numbers disagree with the row it describes.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import List, Optional

from .data import Profile, Request
from .planner import Decision, Plan, fmt_amount


def money(cur: str, x: float) -> str:
    x = round(x + 1e-9, 2)
    if abs(x - round(x)) < 0.005:
        return f"{cur} {int(round(x)):,}"
    return f"{cur} {x:,.2f}"


def longdate(d: dt.date) -> str:
    return f"{d.day} {d.strftime('%B %Y')}"


def _changes_clause(plan: Plan, cur: str) -> str:
    parts = []
    for c in plan.changes:
        desc = c.description[0].lower() + c.description[1:]
        if c.action == "stop":
            parts.append(f"Stop the {desc}")
        else:
            parts.append(f"reduce the {desc} to {money(cur, c.new_amount)}")
    if not parts:
        return ""
    text = parts[0]
    if len(parts) > 1:
        text = parts[0] + " and " + " and ".join(parts[1:])
    return text[0].upper() + text[1:]


def render(dec: Decision, req: Request, prof: Profile) -> str:
    cur = prof.home_currency
    minimum = prof.minimum_balance_to_keep
    amt = req.requested_amount
    plan = dec.plan
    if dec.status == "not_affordable" or plan is None:
        partial_route = (req.allows_partial_payment and "partial_payment" in prof.payment_methods
                         and "installments" not in prof.payment_methods and "full_payment" not in prof.payment_methods)
        if partial_route and dec.amount_safe_to_pay > 0 and dec.earliest_full_payment is None:
            return (f"Do not proceed with the {money(cur, amt)} request. Although {money(cur, dec.amount_safe_to_pay)} "
                    f"is available today, the full amount cannot be completed safely within 90 days.")
        return (f"Do not make this payment by {longdate(req.desired_completion_date)}. "
                f"None of the available options keeps the {money(cur, minimum)} minimum protected.")
    if plan.method == "full_payment" and not plan.changes:
        return f"Pay {money(cur, amt)} today. This leaves at least {money(cur, minimum)} available over the next 90 days."
    if plan.method == "full_payment":
        return f"{_changes_clause(plan, cur)}, then pay {money(cur, amt)} today. This leaves at least {money(cur, minimum)} available."
    if plan.method == "installments":
        n = len(plan.payments)
        lead = f"Use {n} installments of {money(cur, plan.payments[0][1])}, starting {longdate(plan.payments[0][0])}."
        if plan.changes:
            lead = f"{_changes_clause(plan, cur)}, then use {n} installments of {money(cur, plan.payments[0][1])}, starting {longdate(plan.payments[0][0])}."
        return f"{lead} This leaves at least {money(cur, minimum)} available."
    if plan.method == "partial_payment":
        first, second = plan.payments
        return (f"Pay {money(cur, first[1])} today and the remaining {money(cur, second[1])} on {longdate(second[0])}. "
                f"This completes the full request and keeps the {money(cur, minimum)} minimum protected.")
    if plan.method == "wait":
        pay_date = plan.payments[0][0]
        if pay_date < req.desired_completion_date:
            # There is slack before the deadline, so the recommendation is framed as waiting.
            return (f"Wait until {longdate(pay_date)}, then pay {money(cur, amt)} in full. "
                    f"Paying sooner would put the {money(cur, minimum)} minimum at risk.")
        return (f"Pay {money(cur, amt)} in full on {longdate(pay_date)}. "
                f"Paying earlier would take the balance below the {money(cur, minimum)} minimum.")
    raise ValueError(plan.method)


_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def check_consistency(text: str, row: dict, req: Request, prof: Profile) -> List[str]:
    """Every amount that the row commits to must appear in the explanation, and the explanation
    must not name a payment method the row does not recommend."""
    problems = []
    nums = {float(n.replace(",", "")) for n in _NUM.findall(text)}
    def has(x: float) -> bool:
        return any(abs(n - x) < 0.006 for n in nums)
    method = row["recommended_payment_method"]
    if method in ("full_payment", "wait") and not has(req.requested_amount):
        problems.append("explanation omits the requested amount")
    if method == "partial_payment":
        parts = [float(p.split(":")[1]) for p in row["payment_plan"].split("|")]
        if not all(has(x) for x in parts):
            problems.append("explanation omits one of the two partial payments")
    if method == "installments":
        first = row["payment_plan"].split("|")[0].split(":")[1]
        if not has(float(first)):
            problems.append("explanation omits the installment amount")
    if method != "not_recommended" and not has(prof.minimum_balance_to_keep) and "minimum" not in text:
        problems.append("explanation omits the protected minimum")
    words = {"installments": "installments", "wait": "in full on", "partial_payment": "remaining"}
    for m, w in words.items():
        if m != method and w in text and not (method == "not_recommended"):
            problems.append(f"explanation mentions {m} wording but method is {method}")
    return problems
