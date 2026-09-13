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

**Confirmed against the gold troughs, and an earlier claim corrected.** An earlier version of this
note said sample request_09 was lost because freelance income is not projected. That was wrong, and
the subset-sum work in section 15 disproved it. Reconstructing what the gold trough implies for the
three users with no payroll shows the organizer projects no irregular income either: request_09's
gold trough is 766.61 against our 630.76, a gap of 136 over ninety days, whereas projecting that
user's 2,795 of freelance history would overshoot by an order of magnitude. The same holds for
request_10, a gig worker with four payout streams, and request_05, whose contract ended. The rule
is right; those rows are lost to accumulated spending over-projection, not to missing income.

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

---

## 11. Reverse-engineering the generator's spending model

The single largest source of error in the first working version was `amount_safe_to_pay`, and it
mattered far beyond its own column: the 90-day trough feeds a set of threshold tests, so a small
amount error flips a categorical field that is graded exactly. Four sample misses were traced to
exactly that. So the spending model was measured rather than assumed.

**What the data says.** Every series that carries a `minimum_allowed_amount` implies an exact
budget, because the ratio of minimum to mean is not noisy: it is 0.500 for dining, entertainment,
gym and streaming and 0.400 for shopping, with a p10 to p90 spread of only 0.47 to 0.53. Dividing
each observed amount by that implied budget recovers the generator's multiplier directly, across
2,907 rows:

| Category | Multiplier range | Implied half-width |
|---|---|---|
| gym, streaming | exactly 1.0000 | none, a fixed subscription |
| entertainment | 0.8809 to 1.1187 | ±12% |
| shopping | 0.8801 to 1.1196 | ±12% |
| dining | 0.7203 to 1.2800 | ±28% |

Every implied budget is an integer, and on the coarsest grid its currency allows: step 1 for EUR
and USD, 10 for INR, 100 for IDR. The published sample troughs are integers or sums of fixed
amounts (157.00, 452.00, 487.00, 624.00, and 539.10 = rent 254.10 + 285.00), which says the
generator projects *future* occurrences at exactly the budget rather than resampling the noise.
So the only estimation error the forecast carries is the error in recovering the budget.

**Estimator.** For `n` draws from `Uniform(b(1-w), b(1+w))` the sample mean converges as
`1/sqrt(n)`, but the order statistics bound `b` directly: `b >= max/(1+w)` and `b <= min/(1-w)`,
with likelihood proportional to `b**-n` inside that interval. `budget.py` returns the mean of
that posterior. The per-category half-width is itself measured from the corpus, using the fact
that `E[(max-min)/(max+min)] = w(n-1)/(n+1)`, which makes each series an unbiased estimate of `w`.
Validated against the 2,907 rows whose true budget is known, mean absolute error falls from 3.81%
to 2.20% for dining, 3.22% to 2.41% for entertainment and 2.21% to 1.84% for shopping.

**And it did not help.** Measured end to end on the samples, the better estimator left the trough
error unchanged and cost one categorical row. The reason is visible once stated: per-category
errors are near-unbiased and largely independent, so they average out in a sum over five or six
categories. The residual trough error is dominated not by how large each occurrence is but by
**how many occurrences fall before the trough**, which depends on cadence anchoring the data does
not pin down. Snapping the estimate to the integer grid was also tested; the feasible interval
still spans two or three grid points for a typical budget, so it recovers the exact value only
14% of the time. Both are kept as selectable estimators (`Policy.budget_method`) because the
ensemble in section 12 uses the disagreement between them, and the sample mean remains the
default because it measured best end to end.

The honest conclusion is that the amount field is approximate by construction and no amount of
estimator work closes it. What can be fixed is the damage it does to the fields around it.

## 12. Deciding under forecast uncertainty

`ensemble.py` re-runs the entire decision across fourteen scenarios: three central budget
estimators, four posterior quantiles, each under both intra-day orderings of same-day debits and
credits. Every scenario produces a complete, verified row.

Two things come out of it. The reported `amount_safe_to_pay` is the median across scenarios,
which is a better point estimate of the same quantity than any single forecast, and is skipped
where the amount is clamped at zero or the full request, or embedded in a partial-payment
schedule. And every row carries a **forecast confidence**, the share of scenarios reaching the
same categorical outcome. On the 250 evaluation requests mean confidence is 0.89 and 35 rows fall
below 0.6; those are exactly the knife-edge rows, and the audit records their full amount range.

**What was tried and rejected.** Letting the ensemble *decide* the categorical fields by majority,
rather than only estimating the amount, halved the sample amount error from 6.8% to 3.3% but cost
one sample its status, method and plan together. A robustness filter that admitted only plans
surviving a fixed share of scenarios changed nothing, because the candidate sets are stable even
when the amounts are not. Since the categorical fields are graded exactly and the amount is not,
the shipped configuration keeps the central deterministic forecast as the decision and uses the
ensemble for the amount and the confidence only. That is strictly no worse than the single
forecast on every sample field.

## 13. Two explanation templates, not one

The organizer uses two wordings for a `wait` recommendation, and the discriminator is deadline
slack, confirmed on all six wait samples:

| Condition | Wording |
|---|---|
| earliest date is before the deadline | "Wait until D, then pay X in full. Paying sooner would put the M minimum at risk." |
| earliest date equals the deadline | "Pay X in full on D. Paying earlier would take the balance below the M minimum." |

There are likewise two `not_affordable` wordings. The "Although X is available today" variant
fires exactly when the user accepts partial payment and nothing else, which is why request_14 and
request_24 use it while request_10, whose user also accepts installments, does not.

## 14. A property worth knowing about the earliest date

Across every sample that has one, `earliest_date_for_full_payment` is either the request date or a
day on which projected income lands, never an arbitrary day in between. Request_07 looks like a
counterexample at 23 October until its payroll message is applied, which moves that user's salary
to the 23rd. The 250-row output satisfies this property on all 184 non-empty dates without any
snapping rule, which is a useful independent check that the income projection is landing on the
right days.


## 15. Solving the gold troughs for integer budgets

Section 11 established that the generator projects future spending at exact integer budgets. That
makes each solved sample an equation rather than a data point. For a sample whose safe amount is
below the requested amount, the gold trough is pinned exactly, so

    gold_outflow = sum(exact flows in the window) + sum over noisy series of count * budget

with the budgets constrained to integers on the currency grid inside their feasible intervals. A
branch-and-bound search over that system was run on all 21 trough-pinning samples.

**What it settled.**

* The window is the day before the first projected income day, in 20 of 20 samples that have
  income. Between two income days the balance only falls, so the trough within a span is the last
  debit before the next credit.
* 19 of 21 residuals come out as exact integers once utilities and healthcare are moved from the
  "exact" side to the noisy side, where they belong: they are monthly but noisy, at ±12%.
  That is independent confirmation of the integer-budget model, since a wrong decomposition would
  almost never land on an integer.
* 8 of 20 samples are exactly solvable with the occurrence counts the current cadence produces.
  Request_22 solves uniquely, at dining 17, groceries 25, transport 13 and utilities 31.
* The global 90-day minimum is the right definition of the trough. Restricting it to the window
  before the first income was tested and is worse: it leaves request_13 at 40.6% error against
  3.3% for the global minimum.

**What it did not settle.** The remaining 12 samples need occurrence counts one different from
ours, and no simple placement rule explains which. Five hypotheses were tested by asking how many
samples become exactly solvable with no count slack: the current bucketed-median cadence gives 5
of 21, including the request day gives 5, cadence measured over the last 90 days gives 5, and the
unbucketed mean gap gives 6. Replaying each category's observed monthly rhythm forward, rather
than stepping by a cadence, was also tested and is worse (median trough error 3.52% against
2.88%). The exact placement rule remains unrecovered.

**What it did produce.** Two things worth having. First, a measurement that reframes the whole
amount question: the median trough error is 1.64%, not the ~7% that `amount_safe_to_pay` reports.
The reported field is `trough - minimum_balance_to_keep`, and on these deliberately marginal cases
the minimum absorbs most of the balance, so subtracting it leverages a 2% trough error into
roughly 8% on the difference. The forecast is considerably more accurate than the output column
suggests. Second, a real fix: over short windows the trough error is under 4%, but over full
90-day windows the projection over-spends by 9 to 17%, always in the same direction. Bucketing
gap medians to fixed cadences is the cause. A series that truly fires four times a month has a
mean gap near 7.6 days; bucketing it to 7 adds roughly an extra occurrence per 90 days. Switching
to the unbucketed mean gap (`Policy.cadence_mode`, now the default) cuts median trough error from
2.39% to 1.64% and moves no categorical field on the samples.

## 16. Cross-reading the evidence with a second model

`openai.gpt-oss-120b` is invocable on this account and matches the primary model at 14 of 14 on
the typed message templates, so all 215 messages were read a second time and compared against the
shipped extraction. Nine fact types disagreed, four of them cash-affecting. Three were the known
dual-fact template, where a message states both a regular salary and a one-time arrears line and
the two readers pick different halves; that case was already handled by keeping the pattern rule's
secondary fact alongside the model's primary one.

The fourth was a genuine defect. Message_174 is the Indonesian twin of message_117, both saying an
employer credit is a reimbursement already received with nothing further scheduled. The primary
model read the English one correctly as `already_settled_credit` and the Indonesian one as
`one_time_credit_confirmed`, which would add a future credit that does not exist. Both the second
model and the pattern rules read it correctly. It was inert in the shipped output because that
message carries no amount and the arrears path requires one, but it is the exact shape of error
that would move a decision if an amount were present, and it shows the Indonesian subset is the
weaker half of the corpus.

`evidence.py` now resolves this class of disagreement in favour of the settled reading, which is
what the challenge's own conflict rule prescribes: an explicit settlement first, then the
financially safer interpretation. The rule is deliberately narrow, firing only when a second
reader and the pattern rules agree that money has already arrived while the primary model would
schedule it in the future. It fires once on this corpus.


## 17. Proof traces, self-repair, and property-based tests

Three additions from a review of the architecture against an idealised "financial compiler" design.
Most of that design was already present, so this section records only what was genuinely missing,
and two of its recommendations that the data contradicts.

**Per-candidate proof.** `planner.Proof` records, for every candidate plan considered and not only
the winner, whether its counterfactual forecast held the minimum balance, the lowest projected
balance it reached, and the first date it breached. The run writes these to `runs/audit.jsonl`:
335 candidate proofs across the 250 requests, and every one of the 119 rejected candidates carries
a dated failure point. This turns "the plan was not safe" into "full payment today bottoms out at
730.41 on 13 January against an 800 minimum", which is what makes a rejection reviewable.

**Self-repair.** `pipeline.decide_request` now walks the ranked candidates and emits the first one
that passes contract verification, rather than emitting the top-ranked row with its problems
attached. A formatting defect scores zero on that row, whereas the second-best safe plan is still
a legitimate recommendation. It has never fired on this dataset (0 of 250), which is the desired
result: it is insurance against a hidden-set combination the samples do not contain.

**Property-based tests.** `tests/test_invariants.py` adds 19 metamorphic tests to the 24 example
tests. Matching 25 published samples proves the system is right at 25 points in the input space;
these assert properties that must hold everywhere, by perturbing a scenario and checking the
direction the answer is allowed to move. Increasing an expense cannot increase safe capacity;
removing a debit cannot decrease it; raising the minimum balance cannot increase it; adding income
cannot decrease it. Plus shape invariants (a partial plan is exactly two payments summing to the
request, an installment plan totals its option including financing fees, `not_recommended` implies
no plan), permission invariants (protected categories, unpermitted categories and fixed series are
never modified, `reduce_to` never goes below `minimum_allowed_amount`, stop and reduce never target
the same series, at most three changes), and the guarantee that every plan the planner returns is
genuinely safe under its own counterfactual forecast.

**Two recommendations rejected on evidence.**

*A Pareto frontier before applying the ranking.* The challenge's ranking is a lexicographic tuple
over six keys, which is a total order. Sorting by it already yields the unique optimum, so a
non-dominated frontier would be an extra step with no effect on the result.

*Solving for "the minimum reduction required" rather than reducing to `minimum_allowed_amount`.*
The samples contradict this. Both spending reductions in the solved set, `reduce_to:event_989:665950`
and `reduce_to:event_1816:23.50`, are exactly the event's `minimum_allowed_amount`. Computing a
smaller sufficient reduction would produce a number the organizer never emits.

Two further points from that review were checked and found already handled: installment options
can cost more than the requested amount because of financing fees, and ranking already uses the
option's total payable, so sample 2's plan is priced at 47,858,720.01 against a 46,018,000 request.
And `earliest_date_for_full_payment` is computed without optional spending changes, which is why
sample 6 correctly reports 15 January while the recommendation is to pay today after a cut.

**On Decimal versus float.** Money is held as `float` with a half-cent comparison epsilon. The
concern is legitimate in general; here it was measured rather than assumed. No value in the
250-row output carries more than two decimals, and the residual analysis in section 15 recovers
exact integers from sums of hundreds of projected flows, which would not happen if float drift
were material at this scale. A `Decimal` refactor was therefore not made: it would touch every
module for no measurable change in output.


## 18. Cracking the occurrence-placement rule: what was proved and what resisted

The remaining amount error traces to how many occurrences of each recurring series fall before the
trough. Reading the raw date sequences settled the first half of the question immediately:
**cadences are exact**. Every variable series in the corpus fires on a perfectly regular grid, with
gaps of exactly 7, 10, 14 or 21 days, or calendar-monthly. There is no jitter to model.

That found a real defect. The bucketed-median classifier mapped a genuine 5-day cadence into the
`5 <= med <= 8 -> 7` bucket, silently under-projecting a third of that user's transport spend. The
unbucketed mean gap (section 15) fixes it, which is the mechanism behind that measured improvement.

With exact cadences and a fixed anchor, the count inside a window depends only on the window
boundaries, so the rule space is tiny rather than open-ended. All of it was searched, scored by how
many gold troughs become exactly solvable in integer budgets:

| Window start | Window end | Exactly solvable |
|---|---|---|
| request date | day before first income | 5 of 21 |
| request date | first income day | 7 of 21 |
| request date + 1 | day before first income | 4 of 21 |
| request date + 1 | first income day | 4 of 21 |

Seven of 21 is the ceiling for any pure window rule, so the placement rule is not a window rule.
Exhaustive enumeration of feasible count vectors for individual samples shows why it resists: for
request_06 the residual is 235.00 and the minimum possible sum of its projected series is 273.78,
so gold must be projecting strictly fewer occurrences than the observed cadence implies, and the
enumeration admits several count vectors that would explain it with no way to choose between them
from one sample. Solving request_06, request_15 and request_18 jointly did not isolate a common
delta either.

**Status: unresolved, and recorded as such.** The structure is proved (exact cadences, integer
budgets, trough at the last debit before the next income) and the search space for a window rule is
exhausted. What remains is a placement rule that drops some occurrences the observed cadence
predicts, and neither hypothesis testing nor per-sample inversion identified it. It is the single
largest remaining source of amount error and the honest limit of this submission.

## 19. Four contract defects found by adversarial review

A structured review of the planner against the specification surfaced four issues. Three were real
and are fixed; one did not occur in this dataset but is now guarded.

**A partial plan was recommended without being simulated.** The two-payment schedule was built from
baseline capacity and the baseline earliest date, and its safety proof was computed but not used to
gate the candidate. The first payment lowers the balance the second draws on, so the pair has to be
forecast jointly. `build_candidates` now gates on the proof, and falls back to a spending-change
variant before rejecting. `test_a_partial_plan_is_only_offered_when_both_payments_are_jointly_safe`
pins it.

**A plan could finish after the deadline.** The ranking placed `completes_by_deadline` first, so a
late plan only won when nothing else completed, but "only when nothing else qualifies" is not the
same as ineligible. No solved sample ever recommends a plan finishing after the deadline, and two
of the 250 rows carried a `wait` plan one day past it. Missing the deadline is now a hard
ineligibility for both wait and installment candidates, and the output has zero such rows. Note
this applies to the *plan*, not to `earliest_date_for_full_payment`, which legitimately may fall
after the deadline: sample 6 reports 15 January against a 14 January deadline while recommending
payment today after a cut.

**A supplied installment schedule could be edited.** The code clamped each payment date with
`max(date, request_date)`. A recommended installment plan must follow a supplied option exactly, so
an option whose first payment already fell before the evaluation date is ineligible, not editable.
No option in this dataset starts before its request date, so this changed no output, but the clamp
was a silent correctness hazard and is gone.

**The requirements checker asserted a claim it never tested.** The FR9 line reported "16 images, 16
facts" as an unconditional pass. It now enumerates every blank event amount, confirms each has a
resolved fact with a positive amount, a currency and a provenance string, and reports the counts it
actually found. A verification script that cannot fail is not a verification script.

**Rejected from the same review.** A `Decimal` refactor: measured in section 17 as unnecessary here.
Requiring exact FX with no fallback: every foreign-currency scheduled row in this dataset already
resolves to an exact dated rate, and the fallbacks are recorded in the audit rather than silent, so
removing them would trade a logged approximation for a hard failure with no accuracy gain.


## 20. End-to-end audit: reading the whole specification, then auditing against it

A late re-read of `problem_statement.md` found that the first pass had missed a large middle
section. `evaluation/audit.py` now walks the specification sentence by sentence and asserts every
stated rule over all 250 rows, then looks for what the rules do not say. It reports 37 checks and
17 observations, and exits non-zero on any rule failure.

**The sentence that had been missed.** "`earliest_date_for_full_payment` measures financial capacity
independently of the user's payment-method preferences. It may equal `request_date` even when the
selected recommendation is installments because the user has chosen not to consider full payment."
The implementation already computed the field preference-independently, so no change was needed,
but 14 rows in the output rely on that reading and it is now asserted rather than assumed.

**Three failures the audit surfaced, and what each turned out to be.**

*Two were wrong assertions in the audit itself, which is worth recording.* The first compared a
partial plan's opening payment to `amount_safe_to_pay` as strings. The organizer's own convention
differs between the two fields: `amount_safe_to_pay` strips trailing zeros (603.3, 433.4, 462)
while plan amounts carry two decimals when fractional (620.40, 941.60, 3246.10). Our output follows
that convention exactly in both fields, verified across all 250 rows, so the check became a numeric
comparison. The second asserted that `not_affordable` implies an empty earliest date.

*The third is a genuine ambiguity, resolved using the specification's own conflict rule.* Four rows
reach a state the samples never show: capacity for a single full payment arrives inside the 90-day
forecast but after `desired_completion_date`. The specification pulls two ways. The eligibility
list for `wait` does not mention the deadline, but the 90-Day Safety Check says the plan "must
complete the request by `desired_completion_date`". The tie-break the specification supplies is
"the financially safer interpretation when the conflict cannot be resolved", and telling someone
the request is not affordable is safer than telling them to pay after their own deadline. So the
plan is withheld and the status is `not_affordable`, while the capacity date is still reported,
because the only stated rule for blanking that field is that the amount never becomes safe within
the forecast period, which is false here. All seven solved `not_affordable` samples have no capacity
at all within 90 days, so gold offers no evidence either way; the reasoning is recorded rather than
the choice being silent.

**What the audit cleared.** Six rows flagged as implausible, all `not_affordable` for users with
several times the request in headroom, turned out to be gig, freelance and seasonal workers with no
payroll and therefore no projected income across the full 90 days. The samples confirm the
organizer projects no irregular income either. Five rows carrying `affordable_with_plan` with an
empty earliest date, a combination gold never shows, are correct for the same reason sample 6 is:
the field measures capacity *without* optional spending changes, and in those five the full payment
only becomes safe after a change. Section 6b now proves that no request silently loses a live
payroll: all 27 rows with no projected income fall into a named category and none is unexplained.

**Coverage.** The 250 evaluation requests introduce no request type, currency or payment-method
combination that the 25 solved samples do not already contain, and the corpus has no request
without payment options, no user missing a profile or history, no duplicate event id, no negative
amount, no deadline before its request date and no deadline beyond the forecast horizon. The
predicted distribution tracks the sample distribution within about twelve points on every status
and method, and matches it exactly on spending-change frequency.

**Explanations.** All 250 match one of the organizer's template shapes, and we emit no shape absent
from the solved set. Thousands separators and long-form dates follow gold's formatting on every row.


## 21. Exhaustive policy search, and why the shipped configuration stands

The forecast contains four modelling choices the specification does not settle: how variable spend
is projected, how the per-occurrence budget is recovered, how cadence is measured, and how same-day
debits and credits are ordered. Rather than argue them, all 24 combinations were scored on the
solved samples and the selection was cross-validated. `evaluation/policy_experiments.py` reproduces
the table; `evaluation/policy_experiments.md` is its output.

**Six variable-spend models were built and measured** against the 21 trough-pinning samples, where
the gold trough is pinned exactly: discrete occurrences (shipped), daily accrual at the cadence
rate, daily accrual from a 90-day window, daily accrual from a 30-day window, a monthly block
charged on the anniversary, and a hybrid that keeps monthly series discrete while accruing
sub-monthly ones. The hybrid was motivated by a real observation: a monthly series fires on a known
calendar day so its timing is certain, while a weekly series' timing is not, and one mis-placed
occurrence moves the trough by a whole budget.

**A scratch harness said the hybrid won; the production pipeline said otherwise, and the pipeline
was right.** The harness had used the posterior budget estimator while production defaults to the
mean, so the two were not comparing the same thing. Re-run inside the real pipeline across the full
grid, `discrete` with the mean budget and the unbucketed mean cadence is the argmax on categorical
accuracy. The hybrid is retained as a tested option (`Policy.variable_model`) because it is a
legitimate alternative and its measurement is part of the record, but it is not the default. The
episode is recorded because the lesson generalises: a scratch harness that diverges from the
production path will mislead, and the production measurement is the one that counts.

**Leave-one-out cross-validation.** Choosing one of 24 configurations on 25 samples risks selecting
for those rows. For each held-out sample the winner was re-chosen using only the other 24:

| | categorical hits /150 | amount within 5% /25 |
|---|---|---|
| Shipped configuration, in-sample | 115 | 12 |
| Leave-one-out selection | 111 | 11 |

Selection optimism is 4 hits out of 150, about 2.7%, and **23 of the 25 folds independently choose
the shipped configuration**. The choice is stable rather than an artefact.

**The one genuine trade-off.** Clearing a day's debits before its credits cuts median amount error
from 6.43% to 3.91% and gains one row within five percent, but costs four categorical hits (method,
spending changes and explanation). Five of the six graded dimensions are categorical and scored
exactly while the amount is a magnitude, so the categorical ranking wins. The alternative stays
available behind `--debits-first` and the reasoning is on the record rather than buried in a default.

**A global bias correction was tested and rejected.** If our variable-spend estimate were biased by
a constant factor, a single calibrated multiplier would fix it. Measured, the ratio of gold's
variable spend to ours has median 1.03 and standard deviation 0.26, with only 10 of 21 samples
within ten percent of the median. There is no consistent bias to correct. The ratio does correlate
negatively with window length (r = -0.49): over 90-day horizons we over-project, over short windows
we sometimes under-project, which is the timing problem of section 18 rather than a scale problem.

**The explanation field is not independently improvable.** All ten explanation mismatches were
classified: eight are downstream of a categorical difference and two differ only in a number inside
otherwise identical sentences. Zero are wording defects. The renderer is correct; that field moves
only when the forecast does.
