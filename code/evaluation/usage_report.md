# Token usage and cost report

Final full-dataset run that produced `output.csv`: started 2026-09-13T04:17:24, 250 requests, mode `agent`, elapsed 55.1 s.

Provider: Amazon Bedrock (us-east-1), Converse API, temperature 0. Prices are Bedrock on-demand list prices per 1M tokens as configured in `code/buyorwait/llm/bedrock.py`; cached calls re-use a stored response and are counted with their original token usage so the totals reflect the whole run.

| Model | Calls | Live | Cached | Input tokens | Output tokens | Errors | Est. cost (USD) |
|---|---|---|---|---|---|---|---|
| us.amazon.nova-pro-v1:0 | 731 | 25 | 706 | 822,839 | 81,217 | 0 | 0.9182 |
| qwen.qwen3-vl-235b-a22b | 16 | 0 | 16 | 24,363 | 1,057 | 0 | 0.0157 |

## Overall

- Total model calls: 747
- Total input tokens: 847,202
- Total output tokens: 82,274
- Total tokens: 929,476
- Average tokens per request: 3,717.9
- Estimated total cost: USD 0.9339
- Estimated cost per request: USD 0.003736

## What the calls were for

- `message_fact`: one call per message (215) mapping untrusted text to a typed fact.
- `image_amount`: one call per image (16) reading the blank ledger amount; a second-opinion call on the fallback vision model is made only when the reading falls outside the user's plausible range.
- `agent_turn`: the agent loop per request (analyze, optional inspect, submit).

Agent loop: 250 requests, average 2.0 turns, 246 model choices agreed with the deterministic arbiter, 0 fallbacks.
