# `code/` — the Buy or Wait? implementation

The full project story (problem, architecture diagrams, results, benchmarks, limitations) lives in
the [repository README](../README.md). This file covers what is in this directory and how to run
it, so the two cannot drift apart.

## Run it

From the **repository root**, not from here:

```bash
python code/main.py run
```

Without AWS credentials, using the extraction bundled in `evidence/facts.json`:

```bash
python code/main.py run --mode deterministic
```

| Command | What it does |
|---|---|
| `python code/main.py run` | Agent mode: cached evidence, tool loop per request, verified rows |
| `python code/main.py run --mode deterministic` | No model calls; bundled or regex evidence |
| `python code/main.py run --limit 20` | First 20 requests only |
| `python code/main.py run --debits-first` | Clear a day's debits before its credits |
| `python code/main.py evidence` | Interpret all messages and images once; writes `.cache/facts.json` |
| `python code/main.py evaluate [--show request_08]` | Score the 25 solved samples field by field |
| `python code/main.py validate` | Re-check `output.csv` against the submission contract |
| `python code/evaluation/audit.py` | 37 specification checks over all 250 output rows |
| `python code/evaluation/requirements_check.py` | PASS/FAIL per stated requirement |
| `python code/evaluation/policy_experiments.py` | 72-configuration grid with leave-one-out |
| `python -m pytest code/tests -q` | 47 tests |

## Layout

```text
buyorwait/           the library; nothing here reads a sample label
├── config.py        paths, FORECAST_DAYS = 90, MONEY_EPS
├── data.py          typed loader; blank amounts stay None, never zero
├── fx.py            dated conversion; a missing rate raises rather than guessing
├── evidence_types.py  Fact, 18 fact types, the 8 that may move money
├── evidence.py      model + regex extraction, two-reader image consensus, coverage guard
├── budget.py        budget recovery: ratio, mean and order-statistics posterior
├── state.py         reconstruction into dated flows; Policy (12 settings)
├── forecast.py      90-day simulation, safe amount, earliest full-payment date
├── planner.py       candidates, spending changes, ranking, per-candidate proof
├── verify.py        submission-contract verifier
├── explain.py       six templates + numeric consistency check
├── pipeline.py      one request end to end, with self-repair
├── ensemble.py      14-scenario forecast, amount and per-row confidence
├── agent.py         Bedrock tool loop + arbiter
└── llm/bedrock.py   Converse client: retries, cache, usage accounting

prompts/             message_facts.md, image_amount.md, agent_system.md
evidence/facts.json  239 extracted facts so a cold run reproduces output.csv exactly
evaluation/          scorer, spec audit, requirements check, policy grid, usage report
tests/               47 tests: 24 example-based, 23 metamorphic
DESIGN_NOTES.md      24 sections: every decision, its evidence, what was rejected
package.sh           builds submission/{code.zip, output.csv, log.txt}
```

## Two things worth knowing before reading the code

**Where the AI is, and is not.** The model classifies messages, reads images, and picks among
candidate plans the engine has already proved safe. It cannot invent a plan: `submit_decision`
only accepts a candidate id the tools returned, and an arbiter re-applies the ranking afterwards.
No model output reaches a graded column. The `decision_explanation` field is rendered by
`explain.py` from the decision object; the model's rationale is audit-only.

**Which budget branch actually runs.** `budget.estimate` computes an order-statistics posterior,
but that is usually not what the forecast uses. `state._budget` returns early on
`BudgetEstimate.exact` (59.2% of series) and otherwise returns the sample mean under the shipped
`Policy.budget_method="mean"` (the other 40.8%), discarding the posterior. This is deliberate and
measured: the posterior is the better estimator of an individual budget and scores 118/150
categorical hits against 125/150 for the mean. See the module docstring in `budget.py` and
section 15 of `DESIGN_NOTES.md`.
