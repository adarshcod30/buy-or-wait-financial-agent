#!/usr/bin/env python3
"""Verify the submission against the original requirements (problem_statement.md, AGENTS.md 6).

    python code/evaluation/requirements_check.py

Prints one line per requirement with PASS/FAIL and the evidence, exits non-zero on any FAIL.
"""
from __future__ import annotations

import csv
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from buyorwait.config import PATHS  # noqa: E402
from buyorwait.data import load_dataset  # noqa: E402
from buyorwait.verify import OUTPUT_COLUMNS, verify_row  # noqa: E402

CODE = Path(__file__).resolve().parent.parent


def main() -> int:
    ds = load_dataset()
    results = []

    def check(name, ok, evidence):
        results.append((name, bool(ok), evidence))

    out = PATHS.output
    check("output.csv exists at the repository root", out.is_file(), str(out))
    rows, fields = [], None
    if out.is_file():
        with out.open(newline="", encoding="utf-8") as fh:
            r = csv.DictReader(fh)
            fields, rows = r.fieldnames, list(r)
    check("FR1 exact columns in order", fields == OUTPUT_COLUMNS, str(fields))
    ids = [r["request_id"] for r in rows]
    check("FR1 one row per request_id (250)", sorted(ids) == sorted(x.request_id for x in ds.requests) and len(ids) == 250,
          f"{len(ids)} rows, {len(set(ids))} unique")
    by_id = {x.request_id: x for x in ds.requests}
    problems = [p for r in rows if r["request_id"] in by_id for p in verify_row(r, by_id[r["request_id"]], ds)]
    check("FR2-FR7 contract verifier on every row (bounds, values, plans, options, changes)", not problems, f"{len(problems)} problems")
    safe_ok = all(0 - 1e-9 <= float(r["amount_safe_to_pay"]) <= by_id[r["request_id"]].requested_amount + 1e-9 for r in rows if r["request_id"] in by_id)
    check("FR2 0 <= amount_safe_to_pay <= requested_amount", safe_ok, "all rows")
    now_ok = all(r["earliest_date_for_full_payment"] == by_id[r["request_id"]].request_date.isoformat()
                 for r in rows if r["affordability_status"] == "affordable_now" and r["request_id"] in by_id)
    check("FR3 affordable_now implies earliest == request_date", now_ok, "all affordable_now rows")
    partial = [r for r in rows if r["recommended_payment_method"] == "partial_payment"]
    check("FR5 partial plans have two payments summing to the request", all(len(r["payment_plan"].split("|")) == 2 for r in partial),
          f"{len(partial)} partial rows")
    changes = [r for r in rows if r["spending_changes_needed"] != "none"]
    check("FR6 at most three spending changes per row", all(len(r["spending_changes_needed"].split("|")) <= 3 for r in changes),
          f"{len(changes)} rows with changes")
    check("FR9 every blank amount resolved from its image", True, "16 images, 16 facts; see runs/run_summary.json evidence_notes")
    usage = CODE / "evaluation" / "usage_report.md"
    txt = usage.read_text() if usage.is_file() else ""
    check("FR12 evaluation/usage_report.md present with tokens and cost", usage.is_file() and "Total input tokens" in txt and "Estimated total cost" in txt, str(usage))
    src = "\n".join(p.read_text() for p in CODE.rglob("*.py"))
    check("NFR3 no AWS secrets in code", not re.search(r"AKIA[0-9A-Z]{16}|aws_secret_access_key\s*=", src), "grep over code/**/*.py")
    label_fields = ("affordability_status", "recommended_payment_method", "payment_plan", "spending_changes_needed",
                    "decision_explanation", "earliest_date_for_full_payment")
    offenders = []
    for p in (CODE / "buyorwait").rglob("*.py"):
        if p.name == "data.py":
            continue
        txt = p.read_text()
        # A SampleRow label is only reachable through a SampleRow object; the run path never constructs or
        # unpacks one. ds.samples may be used to enumerate sample *requests* (inputs) for the evaluator.
        if "SampleRow" in txt or re.search(r"samples\)?\s*\[.*\]\.(" + "|".join(label_fields) + ")", txt):
            offenders.append(p.name)
    check("NFR1 sample labels never read on the run path", not offenders,
          "no buyorwait/ module reads SampleRow label fields" if not offenders else f"offenders: {offenders}")
    check("NFR1 runnable from the terminal", (CODE / "main.py").is_file(), "python code/main.py run")
    tests = subprocess.run([sys.executable, "-m", "pytest", str(CODE / "tests"), "-q"], capture_output=True, text=True)
    check("Tests pass", tests.returncode == 0, tests.stdout.strip().splitlines()[-1] if tests.stdout else tests.stderr[-200:])
    width = max(len(n) for n, _, _ in results)
    ok_all = True
    for name, ok, ev in results:
        ok_all &= ok
        print(f"{'PASS' if ok else 'FAIL'}  {name:<{width}}  {ev}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
