# Support-ticket triage: evaluation and prompt versioning

A personal Python learning project exploring how to measure an LLM-backed feature. It compares prompt-only JSON with schema-constrained output, adds bounded repair, estimates token costs, and identifies prompts by their content.

The problem: a support ticket can produce valid JSON and still receive the wrong category or priority. This lab measures output validity separately from agreement with hand-written labels.

This is an experiment with 30 hand-labelled examples and saved model responses. It is not a deployed support service.

## What it demonstrates

- A fixed evaluation set and labelling rules, retained from the original experiment.
- Separate measurements for output validity, each field, and whole-record exact match.
- A repair ceiling of three attempts, with saved responses for offline replay.
- Cost estimates from recorded input/output token usage, including retries.
- SHA-256 prompt identity and refusal to pool recordings from different prompt versions.

## Run the saved experiment

Requires Python 3.13+ and [uv](https://docs.astral.sh/uv/). Run commands from the repository root.

```bash
git clone https://github.com/jhplocios/support-ticket-triage-evaluation.git
cd support-ticket-triage-evaluation
uv sync --frozen

# The SDK client is constructed at import time, even in replay mode.
# This dummy value allows offline commands to initialise; it is not a credential.
export ANTHROPIC_API_KEY=offline-replay-not-a-real-key

uv run --frozen python score.py --replay
uv run --frozen python score.py --replay --recordings recordings/responses.jsonl
uv run --frozen python score.py --predictor baseline
uv run --frozen pytest -q
```

Dependency installation needs network access. The commands after installation use saved responses or local test doubles and need no model API calls or paid API key.

The default replay uses `recordings/repair_run.jsonl` (149 calls across 60 examples per arm). The older file contains 298 calls, including repair attempts across 120 examples per arm. Repeated passes use the same 30 tickets; they are not independent new test cases.

## Recorded findings

| Observation | Schema-constrained | Prompt-only |
|---|---:|---:|
| Initial valid outputs, earlier experiment | 120/120 | 62/120 |
| Whole-record exact match before repair | 47/120 (39.2%) | 18/120 (15.0%) |
| Valid outputs after repair | 120/120 | 120/120 |
| Whole-record exact match after repair | 47/120 (39.2%) | 39/120 (32.5%) |
| Calls in the later metered experiment | 60 | 89 |
| Estimated USD per valid output in that experiment | $0.00433 | $0.00486 |

These are separate experiments, not one pooled benchmark. See the [results and limitations](docs/RESULTS.md) for denominators and interpretation, and [provenance](docs/PROVENANCE.md) for what this snapshot preserves.

The main lesson: fixing the shape of an answer does not establish that the answer is correct. One near-miss order ID was rewritten into a schema-valid ID that disagreed with the labelling rules.

## Prompt provenance

The template is hashed as raw UTF-8 bytes. A trailing newline changes the hash. The current 405-byte template has SHA-256 `383d0bd9a8010a73bb245fc6829abc071fddca213115b48a01c5d71c72e9c603`.

The 447 historical recordings predate prompt versioning and remain unversioned. They have not been retrospectively assigned an identity. Mixed versioned/unversioned data, or multiple prompt versions, are refused by replay. The test suite exercises this behaviour; the historical recordings do not demonstrate a live versioned run.

The [historical prediction notes](docs/PREDICTIONS.md) preserves the initial, incorrect newline assumption and the later correction.

## Repository guide

| File | Purpose |
|---|---|
| `cases/tickets.jsonl` | 30 labelled ticket examples |
| `cases/LABELLING.md` | Category, priority, ID, and escalation rules |
| `schema.py` | Application schema and validation |
| `predict.py` | Provider calls, bounded repair, recording, and prompt identity |
| `score.py` | Field scoring, baselines, replay, and cost estimates |
| `prompt_template.txt` | Content-addressed prompt template |
| `recordings/` | Saved responses and usage for replay |
| `tests/` | Local tests using saved data and test doubles |
| `docs/` | Results, limitations, historical predictions, and snapshot provenance |

## Scope and limitations

Small hand-labelled dataset; no production deployment, user study, latency benchmark, or statistical significance claim. Results describe this task and configuration, not general model quality.

The source config identifies the historical model as `claude-opus-5`, but recordings do not include provider-returned model IDs or request IDs. Cost is calculated from the experiment's fixed $5/$25 per million input/output token assumptions, not verified billing or a current price quote.

Live calls are outside the quick start: `predict.py` makes paid requests and can append to the committed recordings. Use a separate working copy, check the provider's current API/model/pricing, and choose a new recording destination before collecting new data.

Known CLI limitation: pass `--recordings PATH` together with `--replay`; do not rely on `--recordings=PATH` alone selecting replay.

## Learning context

Built through a sequence of small exercises in Gedanken, my personal learning workspace, with AI-assisted guidance and review. This repository is a curated snapshot with a fresh Git history. The original learning history remains private; its commit timing cannot be independently checked here. The prediction notes are historical copies, not newly pre-registered experiments.
