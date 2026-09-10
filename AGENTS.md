# AGENTS.md

HackerRank Orchestrate (September 2026) — Buy or Wait?

This file is the single source of truth for any AI coding agent working in this repo: Claude Code, OpenAI Codex CLI / Codex Cloud, Gemini CLI, Cursor, Windsurf, opencode, Aider, goose, Factory, RooCode, JetBrains Junie, GitHub Copilot, Devin, or any other AGENTS.md-aware tool.

Read this file in full before taking any action. Obey it exactly unless the user or platform provides higher-priority instructions.

---

## 0. TLDR For The Agent

On every session start, do this in order:

1. Read this file completely.
2. Check the log file path in §2.
3. If it contains a line starting with `AGREEMENT RECORDED:` that matches the current repo root, skip onboarding and go to §4.
4. Otherwise, run the onboarding flow in §3.
5. For every user turn after onboarding, append a summary entry to the log file using the format in §5.
6. When building, testing, or packaging the solution, follow the project contract in §6.

Do not skip logging, rewrite old log entries, or modify the onboarding gate. Sub-agents and worktrees use the same log file.

---

## 1. What This Repo Is

This is a starter repo for the **HackerRank Orchestrate** 24-hour hackathon challenge: **Buy or Wait?**

Participants must build an AI-powered financial agent. For every purchase or payment request in `dataset/requests.csv`, the agent decides whether the user should pay in full, pay partially, use an available installment option, wait, or not proceed.

The system reconstructs the user's financial position from structured profiles and financial events, fixed dated exchange rates, seller/provider payment options, and relevant messages or images. It must account for recurring commitments, pending payments, essential spending, confirmed income, financial priorities, and the minimum balance the user wants to keep. Messages and images are untrusted evidence; they may clarify, amend, delay, cancel, or confirm a financial fact, but their embedded instructions never override the challenge rules. There are no voice notes or live banking, market-data, or exchange-rate calls.

The final submission must produce `output.csv` with:

```text
request_id,amount_safe_to_pay,affordability_status,recommended_payment_method,payment_plan,earliest_date_for_full_payment,spending_changes_needed,decision_explanation
```

Read `problem_statement.md` for the full participant-facing specification.

---

## 2. Log File — Location And Lifecycle

The log file is named `log.txt` and lives in the same directory as this `AGENTS.md` file (and the `CLAUDE.md` that imports it) — the repository root.

| Platform | Path |
|---|---|
| macOS / Linux | `<directory containing AGENTS.md>/log.txt` |
| Windows | `<directory containing AGENTS.md>\log.txt` |

Resolve the path relative to this file. Do not hardcode a folder name, a user path, or the platform home directory, so the location stays correct across clones, renames, and checkouts.

Rules:

- Create the file if missing.
- Never commit or add the log file to git. Keep `log.txt` in `.gitignore`.
- Append only. Do not rewrite, reorder, or delete prior entries.
- One shared log per checkout. All agents and sub-agents append to the same file next to the top-level `AGENTS.md`, never a private copy.
- Never log secrets. Redact API keys, tokens, cookies, private keys, and sensitive PII.

---

## 3. Onboarding Flow

Run this flow only if the log file has no `AGREEMENT RECORDED:` line for the current repo root. On later sessions, skip to §4.

### 3.1 Greeting

Open with a short, warm message. Example:

```text
Welcome to HackerRank Orchestrate. You have 24 hours to design, build, and ship a financial payment-planning agent for Buy or Wait?. Before we start, I need to walk you through the ground rules and get you set up. This takes about a minute.
```

Compute and display:

- Current system time, local timezone, ISO 8601.
- Time remaining until the challenge ends. Use the configured challenge end date if one is provided by the platform or README. If no challenge end date is present, say that the end time is not configured.
- Results announcement time, if provided by the platform or README.

If the current time is past the challenge end, say so plainly and ask whether the user is practicing, reviewing, or re-running tests. Do not block further work.

### 3.2 Rules — Recite These Verbatim

1. This is a **solo** challenge. You must be the author of the submission.
2. You may use any IDE, AI assistant, or tool to help you build. The deliverable is what your system can do, not how you wrote it.
3. Your system must conform to the project contract in §6 so it can be evaluated.
4. Never commit secrets. Use environment variables and a `.env` file if needed.
5. Logging of every conversation turn to the file in §2 is mandatory and cannot be disabled.
6. Submissions are made on the HackerRank Community Platform or as otherwise instructed by HackerRank.

### 3.3 Collect The Agreement

Ask the user to reply with the exact string `I agree` case-insensitively. Do not proceed until they do.

### 3.4 Record The Agreement

Append this block to the log file, then continue:

```text
## [ISO-8601 TIMESTAMP] ONBOARDING COMPLETE

AGREEMENT RECORDED: <repo_root_absolute_path>
Agent: <agent_name_or_unknown>
Language: js | ts | py | custom:<name>
System Time: <ISO-8601 local time with tz>
Time Remaining: <Xd Yh Zm, or not configured>
```

The repo root must match exactly so agreements do not leak across unrelated clones.

---

## 4. Normal Session Start

If onboarding is already complete for this repo root:

1. Append a short `SESSION START` entry using §5.1.
2. Greet the user briefly and surface the remaining time, or say the challenge end time is not configured.
3. If fewer than 2 hours remain, remind them to submit soon.
4. Proceed with the user's request.

---

## 5. Log Format

### 5.1 Session Start Entry

```text
## [ISO-8601 TIMESTAMP] SESSION START

Agent: <agent_name_or_unknown>
Repo Root: <absolute_path>
Branch: <git_branch_or_unknown>
Worktree: <worktree_path_or_main>
Parent Agent: <parent_agent_name_or_none>
Language: <js|ts|py|custom:name>
Time Remaining: <Xd Yh Zm, or not configured>
```

### 5.2 Per-Turn Entry

Append after every user message you respond to:

```text
## [ISO-8601 TIMESTAMP] <short title, max 80 chars>

User Prompt (verbatim, secrets redacted):
<exact user message, with secrets replaced by [REDACTED]>

Agent Response Summary:
<2-5 sentences: what was done, why, and any important decision>

Actions:
* <file edited / command run / tool invoked>

Context:
tool=<agent_name>
branch=<git_branch_or_unknown>
repo_root=<absolute_path>
worktree=<worktree_path_or_main>
parent_agent=<parent_name_or_none>
```

### 5.3 Sub-Agent And Worktree Rules

- Sub-agents must log their own entries using the same file.
- Set `parent_agent=` to the parent agent's name.
- Worktrees use the same shared log file, not a per-worktree copy.

### 5.4 What Not To Log

- API keys, tokens, session cookies, OAuth codes, or private keys.
- Sensitive PII.
- Full contents of large files or binary blobs. Reference by path instead.

---

## 6. Project Contract

### 6.1 Dataset Contract

Participant-facing files are inside `dataset/`.

```text
dataset/
├── financial_profiles.csv
├── financial_events.csv
├── exchange_rates.csv
├── requests.csv
├── sample_requests.csv
├── request_payment_options.csv
├── messages.csv
├── images.csv
├── output.csv
└── media/
    └── images/
```

- `requests.csv` contains the evaluation requests. Produce exactly one output row for every `request_id` in it.
- `sample_requests.csv` contains public examples with completed output fields. Use it to understand format and decision style, not as labels for evaluation requests.
- `financial_profiles.csv` defines the user's home currency, current available balance, minimum balance to keep, priorities, protected spending, adjustable categories, and payment preferences. `max_installment_months` is blank when the user will not consider installments.
- `financial_events.csv` contains historical, pending, scheduled, settled, failed, cancelled, and non-cash records. Treat `settled`, `pending`, `scheduled`, and `unrealized` according to their cash state; do not treat unrealized investment value as available cash.
- `exchange_rates.csv` supplies fixed rates. For a foreign-currency cash event, use the row for its settlement date and the stated `from_currency` to `to_currency` direction.
- `request_payment_options.csv` contains the seller/provider payment options available for a request. A request has two to four options. An available option may still be rejected because it conflicts with the user's payment preferences or `max_installment_months`.
- `messages.csv` and `images.csv` provide optional supporting evidence. Use the information only when relevant; do not invent evidence when an image file is absent.
- `output.csv` is the blank prediction template.

Organizer-only files live outside `dataset/` and must never be used for predictions.

### 6.2 Required Output

The solution must write `output.csv` with these exact columns, in this order:

```text
request_id,amount_safe_to_pay,affordability_status,recommended_payment_method,payment_plan,earliest_date_for_full_payment,spending_changes_needed,decision_explanation
```

- `amount_safe_to_pay` is between `0` and `requested_amount` inclusive.
- `affordability_status` is one of `affordable_now`, `affordable_with_plan`, `affordable_later`, or `not_affordable`.
- `recommended_payment_method` is one of `full_payment`, `partial_payment`, `installments`, `wait`, or `not_recommended`.
- `payment_plan` is chronological `YYYY-MM-DD:amount` entries separated by `|`, or `none`. An installment recommendation must exactly match a supplied payment option.
- `earliest_date_for_full_payment` is the first conservative projected date for one safe full payment. It equals `request_date` for `affordable_now` and is empty when no full payment is safe within the forecast period.
- `spending_changes_needed` is `none` or up to three `stop:<event_id>` and `reduce_to:<event_id>:<new_amount>` actions. Only non-protected, flexible events in a category the user permits may be changed.
- `decision_explanation` is a concise, grounded explanation of the recommendation.

### 6.3 Financial Decision Rules

- Detect recurrence only when history supports it. Forecast essential variable spending conservatively.
- Reserve pending debits. Do not count pending credits, bonuses, commissions, refunds, lottery proceeds, or investment gains until they settle.
- Count confirmed salary on its settlement date. Do not invent unsupported future income, expenses, payment options, or other financial facts.
- The balance must never fall below `minimum_balance_to_keep` after any projected essential expense or payment in the recommended plan.
- Respect the user's protected categories and preferences. Prefer a plan that completes the request by its deadline, avoids spending changes, minimizes total payment cost, starts earlier, and uses fewer payments.
- Resolve conflicts using an explicit cancellation, settlement, or amendment first; then newer records from the same source; then a settled event; then the financially safer interpretation.

### 6.4 Constraints That Make The Submission Evaluable

- Be runnable from the terminal.
- Read the provided files from `dataset/`.
- Do not use organizer-only files or hardcoded labels.
- Keep behavior deterministic where possible.
- Read secrets from environment variables only.
- Include clear setup and run instructions in the submitted code package.

### 6.5 Token Usage And Submission Artifacts

Submit `code.zip`, the completed `output.csv`, and the required `chat_transcript`. The submitted `code.zip` must include an `evaluation/` folder containing `usage_report.md`, `usage_summary.json`, and `model_usage.csv`. These must report the final full-dataset run's model calls, input and output tokens, token averages, estimated cost, runtime, retries, model breakdown, and the number of messages and images processed. Do not include API keys, credentials, or sensitive configuration.

### 6.6 Reasonable Entry Points

There is no required language. If you use Python, `code/main.py` is a good entry point. If you use another language, document the run command clearly in your submitted README.

---

## 7. Cross-Platform And Agent-Compatibility Notes

- Resolve the log path relative to this `AGENTS.md` file, as described in §2. Do not use the platform home directory or hardcode a user path.
- Write logs in UTF-8 with `\n` line endings.
- Do not assume bash. Prefer language-native APIs when possible.
- Keep tool-specific config minimal and point back to this `AGENTS.md`.
- If a nested `AGENTS.md` exists, the closest one wins for files inside that sub-project, but §2 and §5 remain global: keep logging to the `log.txt` beside the top-level `AGENTS.md`, not beside the nested one.

---

## 8. Quick Checklist For The Agent

Before responding to any user message, confirm:

- [ ] I have read this file in this session.
- [ ] I know whether onboarding is required.
- [ ] I know how much time is left, or that the end time is not configured.
- [ ] I will append a §5.2 entry after this turn.
- [ ] I will not log secrets.
- [ ] I will preserve the Buy or Wait? financial decision and output contract in §6.
