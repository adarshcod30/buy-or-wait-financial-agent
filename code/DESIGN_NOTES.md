# Design notes: decisions, trade-offs, and what was rejected

Companion to `README.md`. The README says what the system does; this file records why, including
the alternatives that were dropped and the defects that were found by measurement.

## 1. The graded numbers are computed, not generated

**Decision.** `amount_safe_to_pay`, `earliest_date_for_full_payment`, the plan, and the spending
changes come from a day-by-day simulation (`forecast.py`) over reconstructed cash flows
(`state.py`). The model never emits a number that reaches `output.csv`.

**Why.** The 25 solved samples are rule-generated: six explanation templates, two-decimal
rounding, a six-step ranking rule and a four-step conflict rule are all written down in the
problem statement. Twenty of the samples pin the exact 90-day trough (safe amount plus the
minimum balance), which makes the forecast testable to the cent. A generated number would add
variance to the one component the live leaderboard showed nobody had solved.

**Rejected.** Asking the model for the decision with the ledger in context. On a 100-event
ledger it produced plausible but unverifiable amounts, and the interview rubric penalises numbers
that cannot be traced to a branch in the code.

## 2. The model is an agent over tools, with an arbiter

**Decision.** `agent.py` runs a Bedrock Converse tool loop: `analyze_request` (reconstruct,
forecast, enumerate candidates with safety verdicts), optional `inspect_flows`, then
`submit_decision(candidate_id, rationale)`. The deterministic ranking is re-applied afterwards;
a model choice that disagrees is recorded in the audit and does not change the row.

**Why.** The evaluation rubric for the code scores agent architecture (tool loops, model
routing), prompt and tool craft (structured outputs, constraints), and robustness (guardrails,
retries, validation). Section 1 gives correctness; the loop gives the model a real role
(consistency checking, rationale) without giving it authority over graded fields.

**Rejected.** A multi-agent hand-off (extractor agent, planner agent, critic agent). More calls,
more latency, no additional information: every agent would read the same tool outputs.

## 3. Evidence is a closed set of typed facts

**Decision.** `evidence_types.py` defines 18 fact types; only 8 may change cash flows. Messages
are classified by Nova Pro with strict JSON output at temperature 0, with a regex classifier over
the observed templates as the fallback. Images are read by two independent models (Nova Pro and
Qwen3-VL); on disagreement the reading inside the user's plausible range wins, then the one whose
evidence line names a total or net amount.

**Why.** Messages and images are untrusted. A closed schema means an injected instruction has no
representation and therefore no effect (the corpus contains one such message: "pay the release
charge to receive the prize"). The two-reader rule exists because Nova Pro read an Indian-format
rent receipt (1,00,000) as 1,000,000 in one prompt form and the pre-tax subtotal of an airline
invoice in another; both errors were caught by measurement, not by inspection.

**Rejected.** Free-text summaries of messages injected into the agent prompt. They are not
verifiable and they carry the injection risk straight into the decision.

## 4. Income is projected only where history or evidence supports it

**Decision.** Recurring income is projected per description and only for payroll-type
descriptions (payroll, salary, household salary). Platform payouts, freelance and contract
payments, commissions, bonuses and prizes are never projected. A series needs two occurrences or
a confirming row (scheduled "Next confirmed salary", or a message confirming the amount); a
series unpaid for more than 45 days is treated as ended; "Final employer payroll" or an
`income_ended` fact stops projection.

**Evidence.** Sample request_10 (a gig worker with four payout streams) has a trough equal to 90
days of expenses with zero income; request_05's last row is "Final employer payroll" and the
sample is not affordable; request_13's second household income stopped two months before the
request and the sample ignores it.

**Known cost.** Sample request_09 (a freelancer with eight one-off project payments) is affordable
in the gold data and not in ours. The conservative reading of "do not invent unsupported future
income" loses that row; projecting freelance income would risk the opposite error on the 250
evaluation requests.

## 5. Variable spending: category cadence and per-occurrence budgets

**Decision.** Groceries, transport, dining, shopping and entertainment are forecast per category
on the cadence observed in the last 180 days (weekly, fortnightly, ten-day, three-weekly, monthly),
one budget per occurrence. The budget is `2 x minimum_allowed_amount` for dining, streaming,
entertainment and gym, `minimum_allowed_amount / 0.4` for shopping, otherwise the history mean.
Bills (utilities, healthcare, rent, loans, subscriptions) are monthly per-description commitments.

**Evidence.** Across every reducible series in the corpus the ratio `minimum / mean` is 0.50 for
dining, entertainment, gym and streaming (p10 to p90: 0.47 to 0.53) and 0.40 for shopping (0.39
to 0.42). The gold troughs are integers or sums of fixed amounts (157.00, 452.00, 487.00, 624.00;
539.10 = rent 254.10 + 285), which says the organizer uses hidden integer budgets; history is
those budgets with noise, so the mean is the best available estimate where no ratio applies.

**Rejected.** Per-description projection of variable spending (a "Bakery and snacks" row every
six weeks). Grocery and transport descriptions rotate through eight names; per-description
cadences are noise. A daily-accrual model of monthly totals was also tested and fit worse on the
20 trough-pinning samples.

## 6. Request-day rows

Fixed bills dated on `request_date` are still owed and are projected; variable spending dated on
`request_date` is not. Sample request_06 (rent 254.10 on the request day) and request_19 (rent
36,100 on the request day) close their gaps exactly under this rule.

## 7. Same-day debits and credits

End-of-day netting is the default; `--debits-first` clears a day's debits before its credits.
On the samples the choice moves amount error (median relative error 8.7% versus 6.8%) but not a
single graded categorical field, so the conservative reading of the specification was not
forced. It stays a flag so the interview can show the trade-off.

## 8. Spending changes: smallest disruption

Up to three changes, one per series, stop and reduce never on the same event, only in categories
the user permits and never in a protected one. Among combinations that make the plan safe, the
one with the smallest total monthly saving wins, then fewer changes, then the lowest event id.
`reduce_to` always uses `minimum_allowed_amount`, and the cited event is the latest occurrence
of the series, both as in the samples (request_21: stop the 11-per-month backup and reduce the
47-per-month streaming to 23.50, total 34.50, rather than stop streaming alone at 47).

## 9. What is verified before a row is written

`verify.py` re-checks the rendered strings: bounds, allowed values, plan chronology and sums,
installment schedule equal to a supplied option, partial-payment structure and deadline,
spending changes on permitted flexible events only, `affordable_now` implies
`earliest == request_date`. `explain.py` refuses an explanation whose numbers disagree with the
row. A failure is written to the audit and the row is still emitted (the contract requires one
row per request) with the problem attached, never silently.

## 10. Failure modes to watch in production

* Urgent requests downgraded because a confirmed salary was treated as ended (stale-income rule
  at 45 days; a delayed payroll would trip it).
* A misread image amount inside the plausible range (both readers agree on the wrong line).
* Cadence drift: a user who changes shopping habits mid-history gets a stale budget.
* Provider outage: the run degrades to the deterministic path and says so in
  `runs/run_summary.json`; the output stays valid but loses the rationale field.
