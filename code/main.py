#!/usr/bin/env python3
"""Buy or Wait? command-line entry point.

    python code/main.py run                      # agent mode: Bedrock model drives the tools (default)
    python code/main.py run --mode deterministic # no model calls; evidence from cache or regex fallback
    python code/main.py evidence                 # interpret all messages and images once (cached)
    python code/main.py evaluate                 # score against the 25 solved samples
    python code/main.py validate                 # check output.csv against the submission contract

Run from the repository root. Secrets come only from the AWS credential chain (environment,
profile, or SSO); nothing is read from the repo. See code/README.md for configuration.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from buyorwait.config import PATHS  # noqa: E402
from buyorwait.data import load_dataset  # noqa: E402
from buyorwait.evidence import build_evidence, facts_by_request, load_facts_file, save_facts_file  # noqa: E402
from buyorwait.llm.bedrock import BedrockClient, DEFAULT_MODEL  # noqa: E402
from buyorwait.pipeline import decide_request  # noqa: E402
from buyorwait.state import Policy  # noqa: E402
from buyorwait.verify import OUTPUT_COLUMNS, verify_row  # noqa: E402

log = logging.getLogger("buyorwait")
FACTS_PATH = PATHS.cache / "facts.json"


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def _client(args) -> BedrockClient | None:
    if getattr(args, "mode", "agent") == "deterministic":
        return None
    return BedrockClient(model_id=args.model)


def cmd_evidence(args) -> int:
    ds = load_dataset()
    client = _client(args)
    bundle = build_evidence(ds, client)
    save_facts_file(bundle, FACTS_PATH)
    log.info("facts: %d (%s)", len(bundle.facts), FACTS_PATH)
    for n in bundle.notes:
        log.info("note: %s", n)
    if client:
        log.info("usage: %s", json.dumps(client.usage.summary()["total"]))
    return 0


def cmd_run(args) -> int:
    from buyorwait.agent import run_agent
    started = time.time()
    ds = load_dataset()
    log.info("dataset: %s", json.dumps(ds.stats))
    client = _client(args)
    if args.facts and Path(args.facts).is_file():
        facts = load_facts_file(Path(args.facts))
        evidence_notes = json.loads(Path(args.facts).read_text()).get("notes", [])
    else:
        bundle = build_evidence(ds, client)
        save_facts_file(bundle, FACTS_PATH)
        facts = facts_by_request(ds, bundle)
        evidence_notes = bundle.notes
    policy = Policy(debits_before_credits=args.debits_first)
    requests = ds.requests[: args.limit] if args.limit else ds.requests
    PATHS.runs.mkdir(parents=True, exist_ok=True)
    audit_path = PATHS.runs / "audit.jsonl"
    rows, problems_total, traces = [], 0, []
    with audit_path.open("w", encoding="utf-8") as audit:
        for i, req in enumerate(requests, 1):
            rf = facts.get(req.request_id, [])
            if args.no_ensemble:
                ens = None
                if client is not None:
                    res, trace = run_agent(ds, req, rf, client, policy)
                else:
                    res, trace = decide_request(ds, req, rf, policy), None
            else:
                from buyorwait.ensemble import decide_with_uncertainty
                ens = decide_with_uncertainty(ds, req, rf, policy)
                if client is not None:
                    res, trace = run_agent(ds, req, rf, client, policy)
                    res.row["amount_safe_to_pay"] = ens.row["amount_safe_to_pay"]
                else:
                    res, trace = ens.base, None
                    res.row["amount_safe_to_pay"] = ens.row["amount_safe_to_pay"]
            rows.append(res.row)
            problems_total += len(res.problems)
            rec = {"request_id": req.request_id, "row": res.row, "problems": res.problems,
                   "applied_facts": res.reconstruction.applied_facts, "notes": res.reconstruction.notes,
                   "trough": round(res.decision.trough, 2), "trough_date": res.decision.trough_date.isoformat(),
                   "candidates": [{"method": p.method, "option_id": p.option_id, "changes": [c.render() for c in p.changes]}
                                  for p in res.decision.candidates],
                   "rejected": res.decision.rejected,
                   "proof": [pf.as_dict() for pf in res.decision.proofs]}
            if ens is not None:
                rec["forecast_confidence"] = round(ens.agreement, 3)
                rec["amount_spread"] = [round(x, 2) for x in ens.amount_spread]
                rec["scenarios"] = ens.scenarios
                if len(ens.outcome_counts) > 1:
                    rec["alternative_outcomes"] = ens.outcome_counts
            if trace is not None:
                rec["agent"] = trace.__dict__
                traces.append(trace)
            audit.write(json.dumps(rec, default=str) + "\n")
            if i % 25 == 0 or i == len(requests):
                log.info("%d/%d decided", i, len(requests))
    out = Path(args.output) if args.output else PATHS.output
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=OUTPUT_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    log.info("wrote %s (%d rows)", out, len(rows))
    summary = {
        "started_at": dt.datetime.fromtimestamp(started).isoformat(timespec="seconds"),
        "elapsed_s": round(time.time() - started, 1), "mode": args.mode, "model": args.model if client else None,
        "requests": len(rows), "rows_with_problems": sum(1 for r in rows if False) + problems_total,
        "status_counts": _counts(rows, "affordability_status"), "method_counts": _counts(rows, "recommended_payment_method"),
        "agent": {"requests": len(traces), "agreed_with_arbiter": sum(1 for t in traces if t.agreed),
                  "fallbacks": sum(1 for t in traces if t.fallback),
                  "avg_turns": round(sum(t.turns for t in traces) / len(traces), 2) if traces else None},
        "evidence_notes": evidence_notes,
        "usage": client.usage.summary() if client else {"by_model": {}, "total": {"calls": 0, "input_tokens": 0,
                                                                                    "output_tokens": 0, "estimated_cost_usd": 0.0}},
    }
    (PATHS.runs / "run_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    write_usage_report(summary, Path(__file__).resolve().parent / "evaluation" / "usage_report.md")
    ok = _validate(out, ds, partial=bool(args.limit))
    log.info("run summary: %s", json.dumps({k: summary[k] for k in ("elapsed_s", "requests", "status_counts", "method_counts", "agent")}, default=str))
    return 0 if ok else 1


def _counts(rows, key):
    c = {}
    for r in rows:
        c[r[key]] = c.get(r[key], 0) + 1
    return dict(sorted(c.items()))


def write_usage_report(summary: dict, path: Path) -> None:
    u = summary["usage"]
    n = max(1, summary["requests"])
    lines = ["# Token usage and cost report", "",
             f"Final full-dataset run that produced `output.csv`: started {summary['started_at']}, "
             f"{summary['requests']} requests, mode `{summary['mode']}`, elapsed {summary['elapsed_s']} s.", "",
             "Provider: Amazon Bedrock (us-east-1), Converse API, temperature 0. Prices are Bedrock on-demand list prices "
             "per 1M tokens as configured in `code/buyorwait/llm/bedrock.py`; cached calls re-use a stored response and are counted "
             "with their original token usage so the totals reflect the whole run.", "",
             "| Model | Calls | Live | Cached | Input tokens | Output tokens | Errors | Est. cost (USD) |",
             "|---|---|---|---|---|---|---|---|"]
    for m, v in u["by_model"].items():
        lines.append(f"| {m} | {v['calls']} | {v['live_calls']} | {v['cached_calls']} | {v['input_tokens']:,} | "
                     f"{v['output_tokens']:,} | {v['errors']} | {v['estimated_cost_usd']:.4f} |")
    t = u["total"]
    lines += ["", "## Overall", "",
              f"- Total model calls: {t['calls']}",
              f"- Total input tokens: {t['input_tokens']:,}",
              f"- Total output tokens: {t['output_tokens']:,}",
              f"- Total tokens: {t['input_tokens'] + t['output_tokens']:,}",
              f"- Average tokens per request: {(t['input_tokens'] + t['output_tokens']) / n:,.1f}",
              f"- Estimated total cost: USD {t['estimated_cost_usd']:.4f}",
              f"- Estimated cost per request: USD {t['estimated_cost_usd'] / n:.6f}", "",
              "## What the calls were for", "",
              "- `message_fact`: one call per message (215) mapping untrusted text to a typed fact.",
              "- `image_amount`: one call per image (16) reading the blank ledger amount; a second-opinion call on the fallback "
              "vision model is made only when the reading falls outside the user's plausible range.",
              "- `agent_turn`: the agent loop per request (analyze, optional inspect, submit).", ""]
    if summary.get("agent", {}).get("requests"):
        a = summary["agent"]
        lines += [f"Agent loop: {a['requests']} requests, average {a['avg_turns']} turns, "
                  f"{a['agreed_with_arbiter']} model choices agreed with the deterministic arbiter, {a['fallbacks']} fallbacks.", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))


def _validate(path: Path, ds, partial: bool = False) -> bool:
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames != OUTPUT_COLUMNS:
            log.error("VALIDATION: columns %s != %s", reader.fieldnames, OUTPUT_COLUMNS)
            return False
        rows = {r["request_id"]: r for r in reader}
    expected = [r.request_id for r in ds.requests]
    ok = True
    missing = [x for x in expected if x not in rows]
    extra = [x for x in rows if x not in set(expected)]
    if (missing and not partial) or extra:
        log.error("VALIDATION: missing %s extra %s", missing[:5], extra[:5])
        ok = False
    by_id = {r.request_id: r for r in ds.requests}
    n_problems = 0
    for rid, row in rows.items():
        if rid in by_id:
            for p in verify_row(row, by_id[rid], ds):
                log.error("VALIDATION: %s", p)
                n_problems += 1
    if n_problems:
        ok = False
    log.info("VALIDATION: %s (%d rows, %d problems)", "output.csv satisfies the submission contract" if ok else "FAILED", len(rows), n_problems)
    return ok


def cmd_validate(args) -> int:
    ds = load_dataset()
    return 0 if _validate(Path(args.output) if args.output else PATHS.output, ds) else 1


def cmd_evaluate(args) -> int:
    from evaluation.main import evaluate
    ds = load_dataset()
    facts = {}
    fp = Path(args.facts) if args.facts else FACTS_PATH
    if fp.is_file():
        facts = load_facts_file(fp)
    else:
        log.warning("no facts file at %s; evaluating without message or image evidence", fp)
    summary, _ = evaluate(ds, facts, Policy(debits_before_credits=args.debits_first), show=args.show)
    print(json.dumps(summary, indent=2))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "evidence", "evaluate", "validate"):
        p = sub.add_parser(name)
        p.add_argument("--mode", choices=["agent", "deterministic"], default="agent")
        p.add_argument("--model", default=DEFAULT_MODEL)
        p.add_argument("--facts", default=None, help="reuse a facts JSON instead of calling the model")
        p.add_argument("--output", default=None)
        p.add_argument("--limit", type=int, default=0)
        p.add_argument("--debits-first", action="store_true", help="clear a day's debits before its credits")
        p.add_argument("--no-ensemble", action="store_true",
                       help="single central forecast only; skip the uncertainty ensemble and its confidence")
        p.add_argument("--show", default=None)
    args = ap.parse_args(argv)
    _setup_logging(args.verbose)
    return {"run": cmd_run, "evidence": cmd_evidence, "evaluate": cmd_evaluate, "validate": cmd_validate}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
