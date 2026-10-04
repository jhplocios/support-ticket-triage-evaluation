# Results and limitations

## Dataset and scoring

The dataset contains 30 hand-labelled support-ticket examples. The original private experiment records the cases as committed before the first model call. This fresh-history snapshot does not provide independent evidence of that ordering. Labels cover category, priority, order ID, and whether a human is needed. The majority baseline has 0/30 whole-record matches on these cases.

The labels implement this lab's rules, not a universal support policy. For example, lowercase `ord-77211` is labelled as an invalid identifier. A model normalising it to uppercase can satisfy the application schema while disagreeing with the label.

## Earlier output and repair experiment

Four passes over the same 30 tickets produce 120 initial calls per arm. The constrained arm has 120 valid initial outputs; the prompt-only arm has 62. The other 58 prompt-only outputs are fenced JSON and fail the experiment's direct JSON parse.

Before repair, whole-record exact match is 47/120 for constrained output and 18/120 for prompt-only output. Restricting the latter to its parseable initial outputs gives 18/62 (29.0%); that conditional denominator should not replace the end-to-end score.

The saved repair attempts make all 120 prompt-only chains valid after one retry, but whole-record agreement is 39/120 (32.5%). Constrained agreement remains 47/120 (39.2%). The complete recording contains 298 calls: 240 initial calls and 58 repair calls.

The current replay scores the final outcome of each chain, so it reports the post-repair numbers. It does not print the original pre-repair table as a separate report.

## Later token-cost experiment

Two passes over the same tickets produce 60 initial calls per arm. The prompt-only arm adds 29 repairs. All 149 recorded calls have usage payloads.

| Metric | Constrained | Prompt-only with repair |
|---|---:|---:|
| Recorded calls | 60 | 89 |
| Valid final outputs | 60 | 60 |
| Input tokens | 40,602 | 21,499 |
| Output tokens | 2,263 | 7,354 |
| Estimated total USD | $0.25958 | $0.29134 |
| Estimated USD per valid output | $0.00433 | $0.00486 |

Estimates use $5 per million input tokens and $25 per million output tokens, the constants retained in the experiment. Prompt-only totals $0.291345 before rounding; the replay displays $0.29134 using Decimal's rounding.

“Valid output” means an output passed validation, not that every field matched its expected label. These figures are not cost per correct answer, measured invoice amounts, or current provider prices.

## Provenance and interpretation

- Historical predictions and missed expectations are copied into `PREDICTIONS.md`; the original commit history remains private.
- Historical recordings contain timestamps, prompts, and raw responses; they lack provider-returned model IDs, request IDs, and prompt versions. The model name comes from source configuration.
- Prompt versioning is demonstrated through local tests. All committed historical recordings are unversioned, and remain unchanged.
- Replaying saved responses proves the scoring can be reproduced from those files. It does not independently authenticate the original API calls or guarantee fresh runs will match.
- Repeated passes reuse 30 tickets. No confidence intervals, independent held-out corpus, or significance test are provided.
- The prompt-only comparison treats markdown fences as a parse failure. It is a comparison of these two implementations, not every possible JSON parsing strategy.
- No deployment, production traffic, safety evaluation, tenant-isolation claim, or latency result is established by this repository.

See [PROVENANCE.md](PROVENANCE.md) for the snapshot boundary and [PREDICTIONS.md](PREDICTIONS.md) for historical hypotheses. Those hypotheses include assumptions that later proved wrong; this document defines the public results and their limits.
