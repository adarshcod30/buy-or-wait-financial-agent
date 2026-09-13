# Token usage and cost report

Final full-dataset run that produced `output.csv`: started 2026-09-13T13:28:16, 250 requests, mode `agent`, elapsed 1.8 s.

Provider: Amazon Bedrock (us-east-1), Converse API, temperature 0. Prices are Bedrock on-demand list prices per 1M tokens as configured in `code/buyorwait/llm/bedrock.py`; cached calls re-use a stored response and are counted with their original token usage so the totals reflect the whole run.

| Model | Calls | Live | Cached | Input tokens | Output tokens | Errors | Est. cost (USD) |
|---|---|---|---|---|---|---|---|
| us.amazon.nova-pro-v1:0 | 500 | 0 | 500 | 660,007 | 60,378 | 0 | 0.7212 |

## Overall

- Total model calls: 500
- Total input tokens: 660,007
- Total output tokens: 60,378
- Total tokens: 720,385
- Average tokens per request: 2,881.5
- Estimated total cost: USD 0.7212
- Estimated cost per request: USD 0.002885

## What the calls were for

- `message_fact`: one call per message (215) mapping untrusted text to a typed fact.
- `image_amount`: one call per image (16) reading the blank ledger amount; a second-opinion call on the fallback vision model is made only when the reading falls outside the user's plausible range.
- `agent_turn`: the agent loop per request (analyze, optional inspect, submit).

Agent loop: 250 requests, average 2.0 turns, 244 model choices agreed with the deterministic arbiter, 0 fallbacks.
