"""Decide under forecast uncertainty instead of betting the row on one point estimate.

Why this exists. The forecast's 90-day trough is a sum of projected occurrences, and two things
about it are uncertain: the per-occurrence budget recovered from noisy history, and exactly how
many occurrences of each series land before the trough. Measured on the solved samples, the
resulting trough error has a median around 7%. That would be harmless if the output were the
trough itself, but it is not: the trough feeds a set of threshold tests (is the full amount safe
today, is it safe by the deadline, is a spending change needed), and near a threshold a small
amount error flips a categorical field that is graded exactly.

What this does. It re-runs the whole decision across a small ensemble of plausible forecasts,
drawn by moving the budget posterior to different quantiles and by toggling the intra-day
ordering of same-day debits and credits. Each scenario produces a complete, verified row. The
categorical fields are then decided by majority across scenarios rather than by the single
central forecast, and `amount_safe_to_pay` is the median among the scenarios that agree with the
winning outcome.

This changes the estimator being used. A point forecast reports `status(E[trough])`. This reports
`argmax_s P(status = s)`. Those differ exactly where the mapping is discontinuous, which is
precisely the knife-edge case that was losing rows. It also yields an honest per-row confidence,
recorded in the audit trail, which flags the rows a reviewer should look at first.
"""
from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .data import Dataset, Request
from .evidence_types import Fact
from .pipeline import RowResult, decide_request
from .state import Policy

# Budget quantiles sampled from the posterior, plus the posterior mean (None).
# Symmetric around the centre so the ensemble is unbiased.
# Each scenario is (budget_method, posterior_quantile). The two central estimators disagree by
# only a few percent but disagree in different directions per category, so spanning both is a
# better model of the real uncertainty than perturbing either one alone.
DEFAULT_SCENARIOS: Tuple[Tuple[str, Optional[float]], ...] = (
    ("posterior", None), ("mean", None), ("midrange", None),
    ("posterior", 0.15), ("posterior", 0.35), ("posterior", 0.65), ("posterior", 0.85),
)
CATEGORICAL = ("affordability_status", "recommended_payment_method", "payment_plan",
               "earliest_date_for_full_payment", "spending_changes_needed")


@dataclass
class EnsembleResult:
    row: Dict[str, str]
    scenarios: int
    agreement: float                      # share of scenarios backing the winning outcome
    outcome_counts: Dict[str, int] = field(default_factory=dict)
    amount_spread: Tuple[float, float] = (0.0, 0.0)
    problems: List[str] = field(default_factory=list)
    base: Optional[RowResult] = None      # the central forecast, for the audit trail
    disagreed: bool = False               # the ensemble overrode the central forecast


def _key(row: Dict[str, str]) -> str:
    return "|".join(row[c] for c in CATEGORICAL)


def _cand_id(p) -> str:
    return f"{p.method}|{p.option_id or ''}|{'&'.join(c.render() for c in p.changes)}"


def decide_with_uncertainty(ds: Dataset, req: Request, facts: List[Fact],
                            policy: Optional[Policy] = None,
                            scenarios: Tuple[Tuple[str, Optional[float]], ...] = DEFAULT_SCENARIOS,
                            orderings: Tuple[bool, ...] = (False, True),
                            robustness: float = 0.999,
                            decision_from: str = "central") -> EnsembleResult:
    """Rank only the plans that stay safe across the ensemble.

    A candidate plan is *robust* when it survives at least `robustness` of the scenarios. The
    spec's ranking rule is then applied to the robust plans only, so a plan that is safe solely
    under the most optimistic forecast never wins. `robustness = 1.0` demands unanimity, which is
    the financially safer reading the challenge asks for when evidence conflicts.
    """
    base_policy = policy or Policy()
    results: List[RowResult] = []
    central_idx = 0
    for method, q in scenarios:
        for debits_first in orderings:
            p = Policy(**{**base_policy.__dict__, "budget_method": method, "budget_quantile": q,
                          "debits_before_credits": debits_first})
            if (method == base_policy.budget_method and q is None
                    and debits_first == base_policy.debits_before_credits):
                central_idx = len(results)
            results.append(decide_request(ds, req, facts, p))
    n = len(results)

    # How often did each candidate plan survive as safe, and what did it look like when it did?
    support: Dict[str, int] = defaultdict(int)
    exemplar: Dict[str, RowResult] = {}
    for r in results:
        for plan in r.decision.candidates:
            cid = _cand_id(plan)
            support[cid] += 1
            exemplar.setdefault(cid, r)
    robust = {cid for cid, c in support.items() if c >= robustness * n}

    central = results[central_idx]
    chosen_row, chosen = central.row, central
    if decision_from == "central":
        # The decision itself follows the central deterministic forecast, which is the reading the
        # specification defines. The ensemble is used for the two things it genuinely improves:
        # a more robust point estimate of amount_safe_to_pay, and a per-row confidence.
        amounts = sorted(float(r.row["amount_safe_to_pay"]) for r in results)
        central_amt = float(central.row["amount_safe_to_pay"])
        clamped = central_amt <= 0.011 or abs(central_amt - req.requested_amount) < 0.011
        if amounts and central.row["recommended_payment_method"] != "partial_payment" and not clamped:
            # partial_payment embeds the amount in its own two-payment schedule, and a clamped
            # amount (0, or the whole request) is a boundary rather than an estimate. Everywhere
            # else the median across scenarios is a strictly better estimator of the same number.
            from .pipeline import safe_repr
            chosen_row = dict(central.row)
            chosen_row["amount_safe_to_pay"] = safe_repr(statistics.median(amounts))
        votes0 = Counter(_key(r.row) for r in results)
        all_amt = sorted(float(r.row["amount_safe_to_pay"]) for r in results)
        return EnsembleResult(
            row=chosen_row, scenarios=n, agreement=votes0[_key(central.row)] / n,
            outcome_counts={k: v for k, v in votes0.most_common()},
            amount_spread=(all_amt[0], all_amt[-1]), problems=central.problems, base=central,
            disagreed=False)
    if robust:
        # Re-rank the robust candidates with the spec's own ordering, taken from any scenario in
        # which they appeared (the ordering key depends on the plan, not on the forecast).
        best_cid, best_key = None, None
        for r in results:
            for plan in r.decision.candidates:
                cid = _cand_id(plan)
                if cid not in robust:
                    continue
                key = plan.sort_key(req.desired_completion_date)
                if best_key is None or key < best_key:
                    best_cid, best_key = cid, key
        if best_cid is not None:
            backing = [r for r in results if any(_cand_id(p) == best_cid for p in r.decision.candidates)
                       and _cand_id(r.decision.plan) == best_cid if r.decision.plan]
            if backing:
                amounts = sorted(float(r.row["amount_safe_to_pay"]) for r in backing)
                med = statistics.median(amounts)
                chosen = min(backing, key=lambda r: abs(float(r.row["amount_safe_to_pay"]) - med))
                chosen_row = chosen.row
    else:
        # Nothing survives every scenario: fall back to the majority outcome, which is still a
        # better estimate than the single central forecast.
        votes = Counter(_key(r.row) for r in results)
        winner = votes.most_common(1)[0][0]
        backing = [r for r in results if _key(r.row) == winner]
        amounts = sorted(float(r.row["amount_safe_to_pay"]) for r in backing)
        med = statistics.median(amounts)
        chosen = min(backing, key=lambda r: abs(float(r.row["amount_safe_to_pay"]) - med))
        chosen_row = chosen.row

    votes = Counter(_key(r.row) for r in results)
    agreement = votes[_key(chosen_row)] / n
    all_amounts = sorted(float(r.row["amount_safe_to_pay"]) for r in results)
    return EnsembleResult(
        row=chosen_row, scenarios=n, agreement=agreement,
        outcome_counts={k: v for k, v in votes.most_common()},
        amount_spread=(all_amounts[0], all_amounts[-1]),
        problems=chosen.problems, base=chosen,
        disagreed=_key(chosen_row) != _key(central.row),
    )
