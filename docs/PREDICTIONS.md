# Historical prediction notes

Copied from the original private learning experiment. These are hypotheses and recorded corrections, not guarantees or new predictions. The public repository starts with a fresh Git history, so this file cannot establish that predictions preceded the original calls. Some original assumptions about schema enforcement and provider pricing are intentionally retained as historical claims; the measured results and limits are in [RESULTS.md](RESULTS.md).

---

# Prediction: Schema-Constrained Generation vs. Prompt-Only JSON

## Hypothesis & Predicted Winner
- **Predicted Winner**: Schema-Constrained Arm
- **Target Metric**: Schema-Valid Output Rate (%) over >= 100 calls (120 calls: 30 cases × 4 passes)
- **Predicted Winner Rate**: 100.0%
- **Predicted Competitor Rate**: 95.0%
- **Predicted Margin**: +5.0 percentage points (band: [+3.0, +7.0] percentage points)

## Expected Divergence & Theoretical Boundary
- **JSON Schema Conformance (Layer 1 - Syntax & Grammar Masking)**:
  - **Schema-Constrained**: 100.0%. The provider's decoding engine enforces grammar/schema masking at the token generation level by setting logits of invalid tokens to $-\infty$. Markdown code blocks (````json ... ````), leading conversational preambles, and malformed syntax are mathematically forbidden from being sampled.
  - **Prompt-Only JSON**: ~95.0% (band: 93.0%–97.0%). Frontier models follow JSON formatting instructions reliably most of the time, but stochastic generation can emit occasional markdown backticks, conversational preamble, or trailing syntax anomalies across 120 calls.
  - **Predicted Winner**: Constrained outperforms Prompt-Only by +5.0 percentage points on raw JSON syntax parseability (`json.loads`).

- **TicketTriage Conformance (Layer 2 - Pydantic / Application Level)**:
  - **Failure Mode on `order_id`**:
    - The JSON schema sent to the provider's constrained decoder defines `order_id` as a nullable string (`{"type": ["string", "null"]}`), not the application-level regex `^ORD-\d{5}$` enforced by `normalize_order_id` in `schema.py`.
    - The provider's constrained decoder does **not** know about or enforce the `ORD-\d{5}` pattern; it only knows `order_id` must be a valid JSON string or null.
    - Whenever an adversarial or unconventional input ticket mentions a malformed or near-miss identifier (e.g., lowercase `ord-77211` or invoice reference `INV-44912`), the model can emit that string verbatim.
    - This output is 100% compliant with the provider's JSON schema, but Pydantic's `normalize_order_id` will reject it with a `ValueError`, causing application-level validation to fail.
    - Consequently, both arms are expected to exhibit roughly equivalent failure rates on `order_id` application validation on those specific tickets.

- **Semantic Accuracy (Layer 3 - Ground Truth Exact Match)**:
  - Parity across both arms (~no significant difference). Token-level grammar constraints guarantee syntactic formatting, not underlying reasoning capability or domain classification accuracy.

# Token Usage & Cost Predictions

## 1. Rate Card Constant
- **Model**: `claude-opus-5`
- **Input Rate**: $5.00 per 1,000,000 tokens ($0.000005 / token)
- **Output Rate**: $25.00 per 1,000,000 tokens ($0.000025 / token)
- **Source URL**: `https://www.anthropic.com/pricing`
- **Date Read**: `2026-09-27`

## 2. Hypothesis: Attempt 1 Input Token Consumption
- **Prediction**: The constrained arm will bill **more** input tokens than the prompt-only arm for the exact same ticket on Attempt 1.
- **Predicted Margin**: +50% to +75% higher input tokens for the constrained arm.
- **Mechanism**: The constrained arm serializes and passes `TicketTriage.model_json_schema()` to the provider's token-level masking engine, incurring upfront schema definition oken overhead (~100 tokens) that the prompt-only arm does not send in its system/user messages.

## 3. Total Arm Economics (USD per Valid Record)
- **Hypothesis**: The constrained arm will achieve a lower cost per valid record despite higher Attempt 1 input token counts.
- **Mechanism**: The prompt-only arm triggers repair retries (accumulating history + error tracebacks across turns), whereas constrained mode achieves 100% validity on Attempt 1 without incurring repair bills.

# Prompt Versioning Predictions

## 1. Template Identity & Hashing Specification
- **Template File**: `prompt_template.txt`
- **Trailing Newline**: Terminating `\n` (POSIX compliant)
- **Byte Count**: 406 bytes UTF-8
- **Hashing Algorithm**: SHA-256 (`hashlib.sha256`)
- **Expected Prompt Version ID**: `d39e35936223cc35711dddc9c2eb0efcf46a0be4cbcbb01d4a704d2e01c97ebc`
- **Short ID**: `d39e3593`

## 2. Legacy Records Rule (447 Historical Calls)
- **Recorded Scope**: 298 records in `recordings/responses.jsonl` + 149 records in `recordings/repair_run.jsonl` = **447 total legacy records**.
- **Immutability Invariant**: Historical files remain untouched. No synthetic backfilling (e.g. "no version" is not `v0`).
- **Schema Mapping**: `RecordedCall.prompt_version` defaults to `None`.
- **Replay Classification**: Replay labels these records as `unversioned` and reproduces the established rates.
- **Refusal Guarantee**: Replay fed records of more than one version (or a mix of versioned and unversioned records) must refuse execution with a non-zero exit code naming all conflicting versions.

## 3. Repair & Resume Provenance Invariant
- Retries and resumed chains must inherit the `prompt_version` of their originating chain, maintaining lineage with the `original_prompt` regardless of changes to `prompt_template.txt` on disk.

## 4. Empirical Outcome
Predicted d39e3593…: missed. The prediction included a trailing newline absent
from the preceding cost-accounting experiment's prompt. The byte-identical template is 405 bytes and hashes to
383d0bd9…. The original prediction remains above as pre-registered.
