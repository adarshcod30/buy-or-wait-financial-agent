"""Candidate payment plans, spending-change search, and the ranking rule from the spec.

Ranking when several eligible plans are safe (problem_statement.md, "Choosing Between Safe Plans"):
  1. completes the request by desired_completion_date
  2. needs no spending changes
  3. lowest total amount paid
  4. earlier first payment
  5. fewer payments
  6. lowest payment_option_id
"""
from __future__ import annotations

import datetime as dt
import itertools
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .data import PaymentOption, Profile, Request
from .forecast import Adjustments, amount_safe_to_pay, earliest_full_payment_date, is_safe, simulate
from .state import AdjustableSeries, Reconstruction

MAX_CHANGES = 3


@dataclass(frozen=True)
class Change:
    action: str            # stop | reduce_to
    series_key: str
    event_id: str
    description: str
    new_amount: float      # 0 for stop
    monthly_saving: float

    def render(self) -> str:
        if self.action == "stop":
            return f"stop:{self.event_id}"
        return f"reduce_to:{self.event_id}:{fmt_amount(self.new_amount)}"


@dataclass
class Proof:
    """Why a candidate plan passed or failed, kept for the audit trail and the interview."""
    label: str
    valid: bool
    reason: str = ""
    lowest_balance: Optional[float] = None
    first_failure_date: Optional[dt.date] = None
    required_minimum: Optional[float] = None
    changes: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"plan": self.label, "valid": self.valid, "reason": self.reason,
                "lowest_projected_balance": None if self.lowest_balance is None else round(self.lowest_balance, 2),
                "first_failure_date": self.first_failure_date.isoformat() if self.first_failure_date else None,
                "required_minimum": self.required_minimum, "spending_changes": self.changes}


@dataclass
class Plan:
    method: str                                  # full_payment | partial_payment | installments | wait
    payments: List[Tuple[dt.date, float]]
    changes: List[Change] = field(default_factory=list)
    option_id: Optional[str] = None
    option_number: int = 0
    reason: str = ""

    @property
    def total_paid(self) -> float:
        return round(sum(a for _, a in self.payments), 2)

    @property
    def first_date(self) -> dt.date:
        return min(d for d, _ in self.payments)

    @property
    def last_date(self) -> dt.date:
        return max(d for d, _ in self.payments)

    def completes_by(self, deadline: dt.date) -> bool:
        return self.last_date <= deadline

    def sort_key(self, deadline: dt.date):
        return (
            0 if self.completes_by(deadline) else 1,
            0 if not self.changes else 1,
            self.total_paid,
            self.first_date,
            len(self.payments),
            self.option_number,
        )


@dataclass
class Decision:
    amount_safe_to_pay: float
    earliest_full_payment: Optional[dt.date]
    status: str
    method: str
    plan: Optional[Plan]
    candidates: List[Plan]
    rejected: List[str]
    trough: float
    trough_date: dt.date
    proofs: List[Proof] = field(default_factory=list)


def fmt_amount(x: float) -> str:
    x = round(x + 1e-9, 2)
    return str(int(round(x))) if abs(x - round(x)) < 0.005 else f"{x:.2f}"


def adjustments_for(changes: List[Change]) -> Adjustments:
    return {c.series_key: c.new_amount for c in changes}


# --------------------------------------------------------------------------- spending changes

def allowed_changes(rec: Reconstruction, profile: Profile) -> List[Change]:
    """Every single change the user's preferences permit: stop a stoppable series in a
    willing_to_stop category, or reduce a reducible series to its minimum_allowed_amount in a
    willing_to_reduce category. Protected categories are never touched."""
    out: List[Change] = []
    for s in rec.adjustable:
        if s.category in profile.protect:
            continue
        occ_per_month = _occurrences_per_month(rec, s.key)
        if s.can_stop and s.category in profile.willing_to_stop:
            out.append(Change("stop", s.key, s.latest_event_id, s.description, 0.0,
                              round(s.per_occurrence * occ_per_month, 2)))
        if s.can_reduce and s.category in profile.willing_to_reduce and s.minimum_allowed_amount is not None \
                and s.minimum_allowed_amount < s.per_occurrence:
            out.append(Change("reduce_to", s.key, s.latest_event_id, s.description, s.minimum_allowed_amount,
                              round((s.per_occurrence - s.minimum_allowed_amount) * occ_per_month, 2)))
    return out


def _occurrences_per_month(rec: Reconstruction, key: str) -> float:
    n = sum(1 for f in rec.flows if f.series_key == key)
    return n / 3.0 if n else 0.0


def find_change_set(rec: Reconstruction, profile: Profile, payments: List[Tuple[dt.date, float]],
                    **kw) -> Optional[List[Change]]:
    """Smallest-disruption combination (up to three changes, one per series) that makes the
    payments safe. Disruption = total monthly saving, then fewer changes, then lowest event id."""
    singles = allowed_changes(rec, profile)
    if not singles:
        return None
    best: Optional[Tuple[tuple, List[Change]]] = None
    for k in range(1, MAX_CHANGES + 1):
        for combo in itertools.combinations(singles, k):
            keys = [c.series_key for c in combo]
            if len(set(keys)) != len(keys):
                continue  # stop and reduce on the same series are mutually exclusive
            if not is_safe(rec, payments, adjustments_for(list(combo)), **kw):
                continue
            score = (round(sum(c.monthly_saving for c in combo), 2), len(combo),
                     tuple(sorted(int(c.event_id.split("_")[1]) for c in combo)))
            if best is None or score < best[0]:
                best = (score, list(combo))
        if best is not None:
            break  # a k-change solution beats any (k+1)-change one on the disruption ordering below
    if best is None:
        return None
    # Re-check against larger sets only if they save less in total (rare); keep k minimal otherwise.
    return sorted(best[1], key=lambda c: int(c.event_id.split("_")[1]))


# --------------------------------------------------------------------------- candidates

def installment_eligible(opt: PaymentOption, profile: Profile) -> Tuple[bool, str]:
    if opt.payment_method != "installments":
        return False, "not an installment option"
    if "installments" not in profile.payment_methods:
        return False, "user does not consider installments"
    if profile.max_installment_months is None:
        return False, "user has no installment horizon"
    if opt.number_of_payments > profile.max_installment_months:
        return False, f"{opt.number_of_payments} payments exceed max_installment_months={profile.max_installment_months}"
    return True, ""


def schedule_is_usable(opt: PaymentOption, req: Request) -> Tuple[bool, str]:
    """A recommended installment plan must follow a supplied option exactly. An option whose
    first payment already fell before the evaluation date cannot be followed, so it is
    ineligible rather than editable. A schedule finishing after the deadline cannot complete
    the request in time."""
    if opt.first_payment_date < req.request_date:
        return False, f"schedule starts {opt.first_payment_date}, before the request date"
    last = max(d for d, _ in opt.schedule())
    if last > req.desired_completion_date:
        return False, f"schedule finishes {last}, after the deadline {req.desired_completion_date}"
    return True, ""


def build_candidates(rec: Reconstruction, req: Request, options: List[PaymentOption], safe: float,
                     earliest: Optional[dt.date], debits_before_credits: bool = False
                     ) -> Tuple[List[Plan], List[str], List[Proof]]:
    profile = rec.profile
    kw = {"debits_before_credits": debits_before_credits}
    cands: List[Plan] = []
    rejected: List[str] = []
    proofs: List[Proof] = []
    minimum = profile.minimum_balance_to_keep
    rd, amt = req.request_date, req.requested_amount

    def prove(label: str, payments, changes: Optional[List[Change]] = None) -> Proof:
        """Run the counterfactual forecast for one candidate and record where it fails."""
        path = simulate(rec, payments, adjustments_for(changes or []), **kw)
        ok = path.trough >= minimum - 0.005
        fail = None
        if not ok:
            for d, b in zip(path.dates, path.balances):
                if b < minimum - 0.005:
                    fail = d
                    break
        return Proof(label, ok, "" if ok else "projected balance falls below the minimum",
                     path.trough, fail, minimum, [c.render() for c in (changes or [])])

    # full payment today
    full_pay = [(rd, amt)]
    if "full_payment" in profile.payment_methods:
        pr = prove("full_payment today", full_pay)
        proofs.append(pr)
        if pr.valid:
            cands.append(Plan("full_payment", full_pay, reason="full amount safe today"))
        else:
            ch = find_change_set(rec, profile, full_pay, **kw)
            if ch:
                proofs.append(prove("full_payment today + spending changes", full_pay, ch))
                cands.append(Plan("full_payment", full_pay, ch, reason="full amount safe today after spending changes"))
            else:
                rejected.append("full_payment today: balance would fall below the minimum and no permitted spending change fixes it")
    else:
        rejected.append("full_payment: not in the user's accepted methods")

    # installments
    for opt in sorted(options, key=lambda o: o.option_number):
        ok, why = installment_eligible(opt, profile)
        if ok:
            ok, why = schedule_is_usable(opt, req)
        if not ok:
            if opt.payment_method == "installments":
                rejected.append(f"{opt.payment_option_id}: {why}")
            continue
        sched = list(opt.schedule())          # exactly as supplied, never shifted
        proofs.append(prove(f"installments {opt.payment_option_id}", sched))
        if is_safe(rec, sched, **kw):
            cands.append(Plan("installments", sched, option_id=opt.payment_option_id, option_number=opt.option_number,
                              reason=f"{opt.payment_option_id} keeps the minimum"))
        else:
            ch = find_change_set(rec, profile, sched, **kw)
            if ch:
                proofs.append(prove(f"installments {opt.payment_option_id} + spending changes", sched, ch))
                cands.append(Plan("installments", sched, ch, opt.payment_option_id, opt.option_number,
                                  reason=f"{opt.payment_option_id} safe after spending changes"))
            else:
                rejected.append(f"{opt.payment_option_id}: schedule breaches the minimum balance")

    # partial payment: exactly two payments, remainder on the earliest full-payment date
    if req.allows_partial_payment and "partial_payment" in profile.payment_methods:
        if 0 < safe < amt and earliest and earliest <= req.desired_completion_date and earliest > rd:
            pp = [(rd, safe), (earliest, round(amt - safe, 2))]
            pr = prove("partial_payment", pp)
            proofs.append(pr)
            if pr.valid:
                cands.append(Plan("partial_payment", pp, reason="safe part today, remainder on the earliest full-payment date"))
            else:
                # The first payment lowers the balance the second one draws on, so the pair has to
                # be simulated jointly. Baseline capacity and the baseline earliest date do not
                # prove the schedule; only the counterfactual forecast does.
                ch = find_change_set(rec, profile, pp, **kw)
                if ch:
                    proofs.append(prove("partial_payment + spending changes", pp, ch))
                    cands.append(Plan("partial_payment", pp, ch, reason="two payments safe after spending changes"))
                else:
                    rejected.append(f"partial_payment: the two payments are not jointly safe "
                                    f"(lowest projected balance {pr.lowest_balance:.2f} on {pr.first_failure_date})")
        else:
            rejected.append("partial_payment: conditions not met (0 < safe < requested and earliest date within the deadline)")

    # wait for the earliest full-payment date
    if "full_payment" in profile.payment_methods and earliest and earliest > rd:
        if earliest <= req.desired_completion_date:
            proofs.append(prove("wait for the earliest safe date", [(earliest, amt)]))
            cands.append(Plan("wait", [(earliest, amt)], reason="full amount becomes safe later"))
        else:
            rejected.append(f"wait: the earliest safe date {earliest} is after the deadline "
                            f"{req.desired_completion_date}, so the request cannot complete in time")
    elif "full_payment" in profile.payment_methods and earliest is None:
        rejected.append("wait: the full amount is never safe within the 90-day forecast")

    return cands, rejected, proofs


def decide(rec: Reconstruction, req: Request, options: List[PaymentOption],
           debits_before_credits: bool = False, capacity_horizon: str = "full") -> Decision:
    # `capacity_horizon` may be applied to the whole safety check, or only to the capacity
    # measure (amount_safe_to_pay and earliest_date), leaving recommended plans tested over the
    # full 90 days. Both are measured; see evaluation/policy_experiments.md.
    cap_end = rec.safety_end(capacity_horizon if capacity_horizon != "capacity_only" else "last_income")
    plan_end = rec.horizon_end if capacity_horizon in ("full", "capacity_only") else cap_end
    kw = {"debits_before_credits": debits_before_credits, "until": plan_end}
    cap_kw = {"debits_before_credits": debits_before_credits, "until": cap_end}
    safe, path = amount_safe_to_pay(rec, req.requested_amount, **cap_kw)
    earliest = earliest_full_payment_date(rec, req.requested_amount, **cap_kw)
    cands, rejected, proofs = build_candidates(rec, req, options, safe, earliest, debits_before_credits)
    if not cands:
        return Decision(safe, earliest, "not_affordable", "not_recommended", None, cands, rejected,
                        path.trough, path.trough_date, proofs)
    cands.sort(key=lambda p: p.sort_key(req.desired_completion_date))
    best = cands[0]
    if best.method == "full_payment" and not best.changes:
        status = "affordable_now"
    elif best.method == "wait":
        status = "affordable_later"
    else:
        status = "affordable_with_plan"
    return Decision(safe, earliest, status, best.method, best, cands, rejected, path.trough, path.trough_date, proofs)
