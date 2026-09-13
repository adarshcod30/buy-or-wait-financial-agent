# Policy selection experiments

24 configurations over 4 axes, scored on the 25 solved samples.
Ranking is lexicographic: categorical field hits first (five of the six graded dimensions are
categorical and graded exactly), then amount accuracy within five percent.

| variable_model | budget_method | cadence_mode | same-day order | categorical hits /150 | amount within 5% /25 |
|---|---|---|---|---|---|
| discrete | mean | mean | end of day | 115  **(shipped)** | 12 |
| discrete | mean | median | end of day | 115 | 11 |
| hybrid | mean | median | end of day | 114 | 12 |
| hybrid | mean | mean | end of day | 114 | 10 |
| hybrid | mean | median | debits first | 114 | 10 |
| hybrid | mean | mean | debits first | 114 | 9 |
| discrete | mean | mean | debits first | 111 | 13 |
| discrete | mean | median | debits first | 111 | 13 |
| hybrid | posterior | median | end of day | 111 | 11 |
| hybrid | posterior | mean | end of day | 111 | 8 |
| hybrid | posterior | mean | debits first | 111 | 7 |
| hybrid | posterior | median | debits first | 111 | 7 |
| hybrid | midrange | median | end of day | 110 | 11 |
| hybrid | midrange | mean | end of day | 110 | 8 |
| hybrid | midrange | mean | debits first | 109 | 7 |
| hybrid | midrange | median | debits first | 109 | 7 |
| discrete | posterior | mean | end of day | 108 | 11 |
| discrete | midrange | mean | end of day | 108 | 11 |
| discrete | posterior | median | end of day | 108 | 10 |
| discrete | midrange | median | end of day | 108 | 10 |
| discrete | posterior | mean | debits first | 106 | 12 |
| discrete | posterior | median | debits first | 106 | 12 |
| discrete | midrange | mean | debits first | 106 | 12 |
| discrete | midrange | median | debits first | 106 | 12 |

## Leave-one-out cross-validation

For each held-out sample the winning configuration is chosen using only the other 24, then
scored on the held-out row. This removes the optimism of having selected on the same rows.

- Shipped configuration, all 25 samples: **115/150** categorical hits, 12/25 within five percent
- Leave-one-out selection: **111/150** categorical hits, 11/25 within five percent
- Selection optimism: 4 hits out of 150 (2.7%)
- Folds choosing the shipped configuration: **23/25**

Configurations chosen across the folds:

- `('discrete', 'mean', 'mean', False)`: 23 fold(s)
- `('hybrid', 'mean', 'median', False)`: 1 fold(s)
- `('hybrid', 'mean', 'median', True)`: 1 fold(s)

## Reading

The shipped configuration is the argmax on the full sample and is selected independently by
23 of 25 folds, so the choice is stable rather than an artefact of these rows.
The one visible trade-off is same-day ordering: clearing debits before credits cuts median amount
error substantially but costs categorical hits. Since five of the six graded dimensions are
categorical and exact while the amount is a magnitude, the categorical ranking is preferred and
the alternative is left available behind `--debits-first`.
