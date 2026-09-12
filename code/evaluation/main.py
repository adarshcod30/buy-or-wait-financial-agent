#!/usr/bin/env python3
"""Score the pipeline against dataset/sample_requests.csv, field by field.

    python code/evaluation/main.py                # deterministic path, no model calls
    python code/evaluation/main.py --show request_08   # dump flows for one sample
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from buyorwait.data import load_dataset  # noqa: E402
from buyorwait.pipeline import decide_request  # noqa: E402
from buyorwait.state import Policy  # noqa: E402


def evaluate(ds, facts_by_request, policy, show=None, quiet=False):
    fields = ["amount_safe_to_pay", "affordability_status", "recommended_payment_method", "payment_plan",
              "earliest_date_for_full_payment", "spending_changes_needed", "decision_explanation"]
    hits = {f: 0 for f in fields}
    rel_err = []
    rows = []
    for s in ds.samples:
        req = s.request
        res = decide_request(ds, req, facts_by_request.get(req.request_id, []), policy)
        gold = {f: getattr(s, f) for f in fields}
        got = res.row
        for f in fields:
            g, o = str(gold[f]), str(got[f])
            if f == "amount_safe_to_pay":
                ok = abs(float(g) - float(o)) < 0.011
                denom = max(1.0, float(g))
                rel_err.append(abs(float(g) - float(o)) / denom)
            else:
                ok = g == o
            hits[f] += ok
        rows.append((req.request_id, gold, got, res))
        if not quiet:
            marks = "".join("." if (abs(float(gold[f]) - float(got[f])) < 0.011 if f == "amount_safe_to_pay" else str(gold[f]) == str(got[f])) else "X" for f in fields)
            print(f"{req.request_id} [{marks}] safe gold={gold['amount_safe_to_pay']} got={got['amount_safe_to_pay']} | "
                  f"{gold['affordability_status']}/{gold['recommended_payment_method']} -> {got['affordability_status']}/{got['recommended_payment_method']} | "
                  f"earliest {gold['earliest_date_for_full_payment'] or '-'} -> {got['earliest_date_for_full_payment'] or '-'} | changes {gold['spending_changes_needed']} -> {got['spending_changes_needed']}"
                  + (f" | PROBLEMS {res.problems}" if res.problems else ""))
        if show and req.request_id == show:
            rec = res.reconstruction
            print(f"  opening {rec.opening_balance} min {rec.profile.minimum_balance_to_keep} trough {res.decision.trough:.2f} on {res.decision.trough_date}")
            for f in rec.flows:
                if f.date <= res.decision.trough_date:
                    print(f"    {f.date} {f.amount:14.2f} {f.kind:9s} {f.label:40s} {f.series_key}")
            for n in rec.notes:
                print("    NOTE", n)
            for a in rec.applied_facts:
                print("    FACT", a)
            for r in res.decision.rejected:
                print("    REJECTED", r)
            for c in res.decision.candidates:
                print("    CAND", c.method, c.option_id, [(d.isoformat(), a) for d, a in c.payments], [x.render() for x in c.changes])
            print("    GOLD explanation:", gold["decision_explanation"])
            print("    OURS explanation:", got["decision_explanation"])
    n = len(ds.samples)
    summary = {f: f"{hits[f]}/{n}" for f in fields}
    summary["amount_rel_err_median"] = round(statistics.median(rel_err), 4)
    summary["amount_rel_err_mean"] = round(statistics.fmean(rel_err), 4)
    summary["amount_within_5pct"] = sum(1 for e in rel_err if e <= 0.05)
    return summary, rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", default=None)
    ap.add_argument("--facts", default=None, help="JSON file of facts by request (from the evidence layer)")
    ap.add_argument("--debits-first", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    ds = load_dataset()
    facts = {}
    if args.facts:
        from buyorwait.evidence import load_facts_file
        facts = load_facts_file(Path(args.facts))
    policy = Policy(debits_before_credits=args.debits_first)
    summary, _ = evaluate(ds, facts, policy, show=args.show, quiet=args.quiet)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
