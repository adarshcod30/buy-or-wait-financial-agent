# Best-solution research and execution plan

## Executive verdict

The current solution is a credible prototype, but it is not ready to maximize
the challenge result. Its public-sample categorical performance is promising,
but its numerical forecast is materially inaccurate—the field most directly
driving `amount_safe_to_pay`, payment feasibility and early/full-payment dates.

The winning direction is an **evidence-backed financial constraint solver**:

```text
raw data and PNGs
  -> resolved event lifecycle ledger
  -> exact 90-day cash-flow forecast
  -> all eligible payment-plan candidates
  -> deterministic financial safety proof and ranking
  -> Bedrock-assisted evidence extraction and trace-derived explanation
```

The language model should extract and verify unstructured facts. The
deterministic engine must remain authoritative for every graded financial field.

## Source hierarchy

1. The September problem statement and repository `AGENTS.md` are the rules of
   record.
2. The supplied dataset is the only allowed source for predictions.
3. Public solved samples are regression and policy-calibration evidence, not a
   lookup table for evaluation requests.
4. Official AWS documentation is authoritative for Bedrock API and model
   capabilities.
5. Public repos/posts are only competitive signals. They must not be copied and
   must never override the challenge specification.

The official repository requires a 90-day safe forecast, exact output schema,
strict installment/partial-payment rules, fixed dated FX, flexible-spending
limits and untrusted-evidence handling. It does not publish a September-specific
numeric scoring formula beyond the listed correctness dimensions.

Sources:

* [September problem statement](https://raw.githubusercontent.com/interviewstreet/hackerrank-orchestrate-september26/main/problem_statement.md)
* [September repository instructions](https://raw.githubusercontent.com/interviewstreet/hackerrank-orchestrate-september26/main/AGENTS.md)
* [September README](https://raw.githubusercontent.com/interviewstreet/hackerrank-orchestrate-september26/main/README.md)
* [Bedrock structured outputs](https://docs.aws.amazon.com/bedrock/latest/userguide/structured-output.html)
* [Bedrock tool use](https://docs.aws.amazon.com/bedrock/latest/userguide/tool-use.html)
* [Bedrock model availability and compatibility](https://docs.aws.amazon.com/bedrock/latest/userguide/models.html)

## Measured current baseline

The following was measured locally on 2026-09-13 with:

```text
python3 -m pytest code/tests -q
python3 code/main.py evaluate --facts .cache/facts.json
python3 code/main.py validate
```

| Metric | Result |
|---|---:|
| Unit tests | 24/24 passed |
| Output contract on current 250-row file | valid, 0 reported problems |
| Exact `amount_safe_to_pay` on 25 public samples | 2/25 |
| Median absolute percentage error of safe amount | 6.43% |
| Safe amount within 5% | 12/25 |
| `affordability_status` | 20/25 |
| `recommended_payment_method` | 21/25 |
| `payment_plan` | 20/25 |
| `earliest_date_for_full_payment` | 18/25 |
| `spending_changes_needed` | 21/25 |
| Explanation exact match | 15/25 |

The existing uncertainty ensemble did not improve the public sample results:
its categorical scores were unchanged, exact safe amounts remained 2/25, and
median amount error moved to 6.67%. It should not be used in the final decision
path unless a later experiment demonstrates a real improvement.

Cached evidence facts improve the base result relative to no facts (status
20/25 versus 19/25; method 21/25 versus 20/25), but forecast construction—not
model choice—is the dominant error source.

## Immediate correctness defects

These are specification risks, not cosmetic improvements.

### P0 — hard-contract fixes

1. **Use `Decimal` end-to-end.** `data.py`, state reconstruction, forecast,
   planner, output formatting and validation currently use `float`. Financial
   output must not depend on binary floating-point artifacts.

2. **Reject any plan that misses `desired_completion_date`.** The current
   planner can add installment or wait candidates without first rejecting a
   completion date after the deadline. The specification requires a recommended
   plan to complete by the desired date.

3. **Simulate every partial plan before recommending it.** The current planner
   constructs a partial plan from baseline safe capacity and baseline earliest
   full-payment date but does not prove the two payments are jointly safe. The
   first payment changes the balance available for the second one.

4. **Do not alter a supplied installment schedule.** Current logic can replace
   an option's payment date with the request date. A valid installment plan must
   exactly match a supplied option; an option that starts before evaluation is
   ineligible rather than editable.

5. **Require exact supplied FX.** The current converter can use earlier,
   inverse or two-hop rates. The task says to match the supplied rate date and
   currency pair, and says all required rates are provided. Missing exact FX
   should be an explicit data error, not an invented conversion.

6. **Make the requirements checker prove its claims.** It currently records
   image-amount resolution as passed unconditionally. Replace this with a
   check that every blank event amount has a resolved, linked fact with valid
   currency and provenance.

### P1 — forecast and plan correctness

7. **Separate series identity from category.** The current variable-spend model
   aggregates a category into one mutable series and renders a change against
   the latest event. This can make a change to one `event_id` affect multiple
   expenses. Maintain a series-to-event mapping and prove exactly which future
   occurrences each `stop` or `reduce_to` action changes.

8. **Solve reductions continuously.** Current spending changes are only `stop`
   or `reduce_to:minimum_allowed_amount`. The engine should calculate the least
   feasible allowed reduction for each event combination, then choose the
   least-disruptive valid plan deterministically.

9. **Make recurrence an explicit, testable model.** Replace ad hoc category
   cadence rules with a recurrence classifier that records evidence, cadence,
   next date and confidence. It must distinguish fixed monthly commitments,
   regular variable spend, irregular one-offs and payroll.

10. **Preserve day-order policy as a documented scenario, not a hidden default.**
    The current samples do not prove all same-day debit/credit cases. Expose a
    clear policy and test it against any directly evidenced ordering.

## Forecast-calibration strategy

The public samples reveal target baseline troughs for requests where the safe
amount is below the request amount:

```text
target_baseline_trough = minimum_balance_to_keep + amount_safe_to_pay
```

Use this fact to evaluate generic forecast policies—not to add request-specific
answers.

### Model selection protocol

1. Define a finite policy family before looking at results:
   recurrence detector, cadence estimator, variable-spend estimator, salary
   continuation policy and same-day ordering.
2. Run every policy against the 25 solved examples using only the input fields
   available to the actual prediction path.
3. Select policy settings through grouped cross-validation by user/currency and
   request type. Never tune a separate setting for a request ID.
4. Optimize lexicographically: valid rows first, categorical accuracy second,
   median/mean amount error third, and explanation consistency last.
5. Persist the selected policy and its experiment table in `evaluation/`.

### Better variable-spend estimator

For each recurring variable series, calculate candidate budgets from:

* last settled value;
* arithmetic mean and median of a trailing window;
* a robust trimmed mean;
* a budget implied by `minimum_allowed_amount` when its relationship is
  independently supported by the corpus;
* historical calendar-phase occurrence patterns.

Then choose between these generic estimators through grouped sample evaluation.
Do not preserve hard-coded category ratios unless data analysis and
cross-validation demonstrate that they generalize.

### Why the present ensemble is not the answer

Averaging multiple uncertain forecasts cannot repair incorrect event identity,
wrong recurrence count or an omitted flow. First correct the ledger and
calendar model. Use scenario analysis only as an audit signal or conservative
fallback after it improves measured performance.

## Bedrock design

### Roles for AI

| Role | Output | Must not do |
|---|---|---|
| Message fact extractor | typed amendment/cancellation/income/date fact | decide affordability |
| Image amount extractor | amount, currency, document type, date, evidence text | invent an unavailable amount |
| Evidence reviewer | compare two conflicting factual readings | modify financial rules |
| Explanation polisher | rewrite an already verified explanation | change a number, plan or status |

### Strict schema and model benchmark

Use Bedrock Converse structured outputs or strict tool definitions. The current
manual JSON-regex parser is weaker than a service-level schema contract.

Benchmark 2–3 models only after confirming current account access and model
availability. Score each model on a fixed evidence test set:

```text
all blank image amounts
all high-impact message templates
English and Indonesian text
cancellation, settlement, date change and pending-credit cases
strict JSON acceptance
median/p95 latency, retries and measured token cost
```

Select the primary model by exact factual extraction, then schema reliability,
then latency/cost. Use temperature zero and explicit `maxTokens` on every call.
Keep static prompt/tool content before dynamic evidence to enable supported
prompt caching, but record actual cache-read usage rather than assuming a hit.

### Current blocker

The installed AWS CLI is current (2.36.30), but its active AWS session is
expired. Model selection must wait for a confirmed `aws login`, after which the
runtime model list and inference profiles will be fetched before testing. No
model name should be declared “best” until that benchmark runs in this account.

## Recommended code structure

```text
code/
├── main.py
├── buyorwait/
│   ├── domain/             # Decimal money, typed entities, output contracts
│   ├── ingest/             # schema loading, exact dated FX
│   ├── evidence/           # Bedrock extraction, schema validation, provenance
│   ├── ledger/             # event lifecycle resolution and recurrence inference
│   ├── forecast/           # daily cash engine and capacity envelope
│   ├── planning/           # candidates, reduction solver, official ranking
│   ├── validation/         # financial and rendered-row proofs
│   ├── explain/            # trace-derived language
│   └── observability/      # decision traces, metrics, usage reporting
├── evaluation/
│   ├── public_samples.py
│   ├── policy_experiments.py
│   ├── invariants.py
│   ├── adversarial_cases.py
│   └── usage_report.md
└── tests/
```

This is a refactoring target, not a justification to rewrite working code all
at once. Apply it incrementally behind test coverage.

## Execution order and acceptance gates

| Order | Work | Done when |
|---:|---|---|
| 1 | P0 contract fixes | New unit tests reproduce each former invalid case; complete output passes validator. |
| 2 | Traceable ledger | Every forecast flow has source event/fact and recurrence reason. |
| 3 | Correct plan solver | All plans are simulated; deadline/schedule/changes have executable proofs. |
| 4 | Forecast experiments | Selected global policy improves public median amount error and does not regress categorical score. |
| 5 | Evidence benchmark | Primary/fallback models selected from measured result file. |
| 6 | Regression + adversarial suite | Public samples, metamorphic tests and clean rerun all pass. |
| 7 | Release | Fresh full-data run produces output, audit, validation report and exact usage report. |

## Interview-ready explanation

The strongest defensible explanation is:

> “The model extracts unstructured evidence into a typed, validated ledger.
> A deterministic 90-day cash-flow engine evaluates every eligible plan and
> proves the balance never violates the user’s minimum. We used public samples
> only to choose among general forecasting policies, tested invariants that
> hidden rows must satisfy, and recorded a trace for every recommendation.”

Do not claim unmeasured leaderboard positions, hidden-ground-truth accuracy,
model benchmark results, or provider availability. Keep those statements tied
to reproducible local artifacts.
