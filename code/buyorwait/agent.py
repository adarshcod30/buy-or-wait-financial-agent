"""Agent loop: a Bedrock model drives deterministic tools and submits a decision.

The tools are the only source of numbers. The model chooses among verified candidate plans and
writes a short rationale; the arbiter re-applies the ranking rule so a model mistake can never
change a graded field silently. Every deviation is recorded in the audit trail.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .data import Dataset, Request
from .evidence_types import Fact
from .explain import check_consistency, render
from .llm.bedrock import BedrockClient
from .pipeline import RowResult, decide_request
from .planner import Plan, fmt_amount
from .state import Policy
from .verify import verify_row

PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"
MAX_TURNS = 6

TOOLS = [
    {"toolSpec": {"name": "analyze_request", "description":
        "Reconstruct the user's finances, apply evidence facts, run the 90-day forecast, and enumerate "
        "every candidate payment plan with its safety verdict. Returns the request, profile, applied facts, "
        "forecast numbers, candidates (each with candidate_id) and rejected options with reasons.",
        "inputSchema": {"json": {"type": "object", "properties": {"request_id": {"type": "string"}},
                                 "required": ["request_id"]}}}},
    {"toolSpec": {"name": "inspect_flows", "description":
        "List the projected cash flows up to the forecast trough date (date, amount, kind, label) so the "
        "trough and applied facts can be checked against the evidence.",
        "inputSchema": {"json": {"type": "object", "properties": {"request_id": {"type": "string"},
                                                                  "limit": {"type": "integer"}},
                                 "required": ["request_id"]}}}},
    {"toolSpec": {"name": "submit_decision", "description":
        "Submit the final decision: the candidate_id chosen by the ranking rule (or 'none'), and a short "
        "rationale citing the forecast numbers. The row is rendered and verified deterministically.",
        "inputSchema": {"json": {"type": "object", "properties": {
            "request_id": {"type": "string"}, "candidate_id": {"type": "string"},
            "rationale": {"type": "string"}}, "required": ["request_id", "candidate_id", "rationale"]}}}},
]


@dataclass
class AgentTrace:
    request_id: str
    turns: int = 0
    tool_calls: List[str] = field(default_factory=list)
    model_choice: Optional[str] = None
    arbiter_choice: Optional[str] = None
    agreed: Optional[bool] = None
    rationale: str = ""
    fallback: str = ""          # why the deterministic path was used instead
    notes: List[str] = field(default_factory=list)


def _candidate_id(p: Plan, i: int) -> str:
    return f"{p.method}#{p.option_id or i}"


def _candidates_payload(res: RowResult) -> List[dict]:
    out = []
    for i, p in enumerate(res.decision.candidates):
        out.append({
            "candidate_id": _candidate_id(p, i), "method": p.method, "option_id": p.option_id,
            "payments": [[d.isoformat(), fmt_amount(a)] for d, a in p.payments],
            "spending_changes": [c.render() for c in p.changes], "total_paid": p.total_paid,
            "completes_by_deadline": p.completes_by(res.decision.plan.completes_by if False else res.decision.candidates[0].payments[0][0]) if False else None,
        })
    return out


def _analysis(ds: Dataset, req: Request, res: RowResult) -> dict:
    prof = ds.profiles[req.user_id]
    dec = res.decision
    cands = []
    for i, p in enumerate(dec.candidates):
        cands.append({
            "candidate_id": _candidate_id(p, i), "method": p.method, "option_id": p.option_id,
            "payments": [[d.isoformat(), fmt_amount(a)] for d, a in p.payments],
            "spending_changes": [c.render() for c in p.changes], "total_paid": p.total_paid,
            "completes_by_deadline": p.completes_by(req.desired_completion_date),
            "ranking_key": list(map(str, p.sort_key(req.desired_completion_date))),
        })
    return {
        "request": {"request_id": req.request_id, "user_id": req.user_id, "request_date": req.request_date.isoformat(),
                    "request_type": req.request_type, "requested_amount": req.requested_amount,
                    "desired_completion_date": req.desired_completion_date.isoformat(),
                    "allows_partial_payment": req.allows_partial_payment, "request_text": req.request_text},
        "profile": {"home_currency": prof.home_currency, "current_available_balance": prof.current_available_balance,
                    "minimum_balance_to_keep": prof.minimum_balance_to_keep, "payment_methods": prof.payment_methods,
                    "max_installment_months": prof.max_installment_months, "protect": prof.protect,
                    "willing_to_reduce": prof.willing_to_reduce, "willing_to_stop": prof.willing_to_stop},
        "applied_facts": res.reconstruction.applied_facts,
        "notes": res.reconstruction.notes,
        "forecast": {"amount_safe_to_pay": dec.amount_safe_to_pay,
                     "earliest_date_for_full_payment": dec.earliest_full_payment.isoformat() if dec.earliest_full_payment else None,
                     "trough_balance": round(dec.trough, 2), "trough_date": dec.trough_date.isoformat()},
        "candidates": cands,
        "rejected": dec.rejected,
        "arbiter_choice": _candidate_id(dec.plan, dec.candidates.index(dec.plan)) if dec.plan else "none",
    }


def _flows(res: RowResult, limit: int = 60) -> dict:
    rec = res.reconstruction
    rows = [{"date": f.date.isoformat(), "amount": round(f.amount, 2), "kind": f.kind, "label": f.label}
            for f in rec.flows if f.date <= res.decision.trough_date][:limit]
    return {"opening_balance": rec.opening_balance, "minimum_balance_to_keep": rec.profile.minimum_balance_to_keep,
            "trough_date": res.decision.trough_date.isoformat(), "flows": rows}


def run_agent(ds: Dataset, req: Request, facts: List[Fact], client: Optional[BedrockClient],
              policy: Optional[Policy] = None) -> tuple[RowResult, AgentTrace]:
    """Returns the final row (always deterministic-verified) and the agent trace."""
    res = decide_request(ds, req, facts, policy)
    trace = AgentTrace(req.request_id, arbiter_choice=_analysis(ds, req, res)["arbiter_choice"])
    if client is None or not client.enabled:
        trace.fallback = "model disabled; deterministic path"
        return res, trace
    system = (PROMPT_DIR / "agent_system.md").read_text()
    messages: List[dict] = [{"role": "user", "content": [{"text":
        f"Decide request {req.request_id} for user {req.user_id}. Start with analyze_request."}]}]
    submitted: Optional[dict] = None
    for turn in range(MAX_TURNS):
        trace.turns = turn + 1
        resp = client.converse(messages, purpose="agent_turn", system=system,
                               tool_config={"tools": TOOLS, "toolChoice": {"any": {}}} if turn == 0 else {"tools": TOOLS},
                               max_tokens=600)
        if resp is None:
            trace.fallback = f"provider unavailable: {client.unavailable_reason}"
            break
        msg = resp["output"]["message"]
        messages.append(msg)
        tool_uses = [c["toolUse"] for c in msg.get("content", []) if "toolUse" in c]
        if not tool_uses:
            if resp.get("stopReason") == "end_turn":
                trace.notes.append("model ended without submitting")
                break
            continue
        results = []
        for tu in tool_uses:
            name, inp = tu["name"], tu.get("input", {}) or {}
            trace.tool_calls.append(name)
            if name == "analyze_request":
                payload = _analysis(ds, req, res)
            elif name == "inspect_flows":
                payload = _flows(res, int(inp.get("limit", 60) or 60))
            elif name == "submit_decision":
                submitted = inp
                payload = {"accepted": True}
            else:
                payload = {"error": f"unknown tool {name}"}
            results.append({"toolResult": {"toolUseId": tu["toolUseId"], "content": [{"json": payload}]}})
        messages.append({"role": "user", "content": results})
        if submitted is not None:
            break
    if submitted is None and not trace.fallback:
        trace.fallback = "model did not submit within the turn budget"
    if submitted is not None:
        trace.model_choice = str(submitted.get("candidate_id", "none"))
        trace.rationale = str(submitted.get("rationale", ""))[:600]
        trace.agreed = trace.model_choice == trace.arbiter_choice
        if not trace.agreed:
            trace.notes.append(f"model chose {trace.model_choice}, arbiter ranking keeps {trace.arbiter_choice}")
    return res, trace
