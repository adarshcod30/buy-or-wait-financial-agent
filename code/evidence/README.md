# Extracted evidence cache

`facts.json` is the output of the evidence layer for this dataset: the 215 messages and 16 document
images resolved into 239 typed facts. It ships with the code so the submitted `output.csv` can be
reproduced exactly on a machine with no AWS credentials and at zero model cost.

It is a **cache of this system's own extraction**, not organizer data and not labels. Every fact
carries its `source_id` (the message or image it came from), its `provenance` (which model or the
regex fallback produced it), and the `raw` phrase it was read from, so any entry can be traced back
to the dataset row that justifies it. Nothing here comes from `sample_requests.csv`.

Regenerate it at any time with credentials configured:

```bash
python code/main.py evidence      # rewrites .cache/facts.json from the dataset
```

The run path prefers `.cache/facts.json` when present and falls back to this file otherwise, so a
fresh checkout reproduces the submission and a re-extraction transparently supersedes it.

Provenance breakdown across the 239 facts: `us.amazon.nova-pro-v1:0` (primary extraction),
`qwen.qwen3-vl-235b-a22b` and a two-model consensus label for image amounts, and `regex` where the
deterministic pattern rules produced or overrode the fact.
