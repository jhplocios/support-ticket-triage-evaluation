from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal
import json
import hashlib
from pathlib import Path
from typing import Any
import anthropic
from anthropic.types import TextBlock
import dotenv
from pydantic import BaseModel, Field, ValidationError
from schema import TicketTriage

# 1. Environment & Config
dotenv.load_dotenv()

# Rate card retrieved 2026-09-27 from https://www.anthropic.com/pricing
CLAUDE_OPUS_5_INPUT_PER_MILLION: Decimal = Decimal("5.00")
CLAUDE_OPUS_5_OUTPUT_PER_MILLION: Decimal = Decimal("25.00")
PRICING_SOURCE_URL: str = "https://www.anthropic.com/pricing"
PRICING_DATE_READ: str = "2026-09-27"

MODEL_NAME: str = "claude-opus-5"
RECORDINGS_PATH = Path("recordings/responses.jsonl")
CASES_PATH = Path("cases/tickets.jsonl")
MAX_ATTEMPTS: int = 3

REPAIR_RUN_PATH = Path("recordings/repair_run.jsonl")


# 2. Strict JSON Schema for constrained grammar decoding
schema_dict = TicketTriage.model_json_schema()
schema_dict["additionalProperties"] = False

client = anthropic.Anthropic()

class UsageStats(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

# 3. Persistence & Outcome Schemas
class RecordedCall(BaseModel):
    arm: str               # "constrained" or "prompt_only"
    case_id: str           # case["id"]
    pass_idx: int          # 0..3
    prompt: str            # Identical prompt sent to model
    raw_response: str      # Verbatim text returned by model
    timestamp: str         # ISO 8601
    attempt: int = 1       # 1 for initial, 2..3 for retries
    usage: dict[str, Any] | None = None  # None for legacy recordings without usage; exact provider dict when present
    prompt_version: str | None = None    # None for legacy recordings; SHA-256 content hash of prompt template

class RepairOutcome(BaseModel):
    valid: bool
    triage: TicketTriage | None
    attempts: int          # 1, 2, or 3
    final_raw: str
    error_message: str | None = None


def persist_call(record: RecordedCall) -> None:
    RECORDINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RECORDINGS_PATH.open("a", encoding="utf-8") as f:
        f.write(record.model_dump_json() + "\n")


# 4. Prompt Template & Version Identity
PROMPT_TEMPLATE_PATH = Path("prompt_template.txt")

def load_prompt_template(path: Path = PROMPT_TEMPLATE_PATH) -> tuple[str, str]:
    raw_bytes = path.read_bytes()
    version_id = hashlib.sha256(raw_bytes).hexdigest()
    return raw_bytes.decode("utf-8"), version_id

PROMPT_TEMPLATE, PROMPT_VERSION = load_prompt_template()


def build_prompt(ticket_text: str) -> str:
    return PROMPT_TEMPLATE.format(ticket_text=ticket_text)


def run_prompt_only(prompt: str) -> tuple[str, dict[str, Any]]:
    """Arm A: Standard prompt-only completion."""
    response = client.messages.create(
        model=MODEL_NAME,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    for block in response.content:
        if isinstance(block, TextBlock):
            return block.text, response.usage.model_dump()
    raise RuntimeError("No TextBlock found in prompt-only response")


def run_constrained(prompt: str) -> tuple[str, dict[str, Any]]:
    """Arm B: Constrained decoding via native structured output."""
    response = client.messages.create(
        model=MODEL_NAME,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
        output_config={
            "format": {
                "type": "json_schema",
                "schema": schema_dict,
            }
        },
    )
    for block in response.content:
        if isinstance(block, TextBlock):
            return block.text, response.usage.model_dump()
    raise RuntimeError("No TextBlock found in constrained response")


# 5. Boundary Validation & Repair Architecture
def validate_triage_payload(raw_text: str) -> TicketTriage:
    """Boundary validator. Raises json.JSONDecodeError or pydantic.ValidationError."""
    parsed = json.loads(raw_text)
    return TicketTriage.model_validate(parsed)


def build_repair_prompt(original_prompt: str, bad_response: str, error: Exception) -> str:
    return (
        f"{original_prompt}\n\n"
        f"--- PREVIOUS FAILED ATTEMPT ---\n"
        f"{bad_response}\n\n"
        f"--- ERROR ---\n"
        f"{type(error).__name__}: {error}\n\n"
        f"Fix the output above. Output ONLY the raw JSON object. "
        f"Do NOT wrap your response in markdown code fences (no ```json or ```) and do NOT provide introductory text."
    )


def _unpack_call(res: str | tuple[str, dict[str, Any]]) -> tuple[str, dict[str, Any] | None]:
    """Safely extracts text and usage whether the caller returns a string (stubs) or a tuple (live)."""
    if isinstance(res, str):
        return res, None
    return res[0], res[1]

_UNSET = object()

def repair_triage(
    case_id: str,
    pass_idx: int,
    arm: str,
    original_prompt: str,
    initial_raw: str | tuple[str, dict[str, Any]],
    caller: Callable[[str], str | tuple[str, dict[str, Any]]] | None = None,
    on_record: Callable[[RecordedCall], None] | None = None,
    max_attempts: int = MAX_ATTEMPTS,
    starting_attempt: int = 1,
    record_initial: bool = True,
    prompt_version: Any = _UNSET,
) -> RepairOutcome:
    """
    Records initial_raw before validation. 
    If invalid, retries up to max_attempts.
    Calls on_record(record) for EVERY attempt BEFORE parsing.
    """
    current_raw, current_usage = _unpack_call(initial_raw)
    attempt = starting_attempt
    last_error: Exception | None = None
    active_version = PROMPT_VERSION if prompt_version is _UNSET else prompt_version

    # Persist initial attempt before any schema parsing happens if record_initial is True
    if record_initial and on_record is not None:
        on_record(
            RecordedCall(
                arm=arm, 
                case_id=case_id, 
                pass_idx=pass_idx, 
                prompt=original_prompt,
                raw_response=current_raw, 
                timestamp=datetime.now(timezone.utc).isoformat(),
                attempt=attempt, 
                usage=current_usage,
                prompt_version=active_version,
            )
        )

    while attempt <= max_attempts:
        try:
            triage = validate_triage_payload(current_raw)
            return RepairOutcome(
                valid=True,
                triage=triage,
                attempts=attempt,
                final_raw=current_raw,
                error_message=None,
            )
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = exc
            if attempt >= max_attempts:
                break

            attempt += 1
            if caller is None:
                raise RuntimeError("Retry needed but no caller provided")

            repair_prompt = build_repair_prompt(original_prompt, current_raw, exc)
            
            # Gracefully handle string stubs or live tuple returns
            call_res = caller(repair_prompt)
            current_raw, current_usage = _unpack_call(call_res)

            if on_record is not None:
                on_record(
                    RecordedCall(
                        arm=arm,
                        case_id=case_id,
                        pass_idx=pass_idx,
                        prompt=repair_prompt,
                        raw_response=current_raw,
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        attempt=attempt,
                        usage=current_usage,
                        prompt_version=active_version,
                    )
                )

    return RepairOutcome(
        valid=False,
        triage=None,
        attempts=attempt,
        final_raw=current_raw,
        error_message=str(last_error),
    )


def run_repair_benchmark() -> None:
    if not RECORDINGS_PATH.exists():
        raise FileNotFoundError(f"Recordings not found at {RECORDINGS_PATH}")

    with RECORDINGS_PATH.open("r", encoding="utf-8") as f:
        all_records = [RecordedCall.model_validate_json(line) for line in f if line.strip()]

    # Track already attempted retries to maintain idempotency
    already_retried = {
        (rec.case_id, rec.pass_idx) for rec in all_records if rec.arm == "prompt_only" and rec.attempt > 1
    }

    # Discover attempt 1 failures
    candidates: list[RecordedCall] = []
    for rec in all_records:
        if rec.arm == "prompt_only" and rec.attempt == 1:
            if (rec.case_id, rec.pass_idx) in already_retried:
                continue
            try:
                validate_triage_payload(rec.raw_response)
            except (json.JSONDecodeError, ValidationError):
                candidates.append(rec)

    entered = len(candidates)
    repaired_2 = 0
    repaired_3 = 0
    gave_up = 0

    print(f"=== Starting Repair Benchmark ===")
    print(f"Discovered {entered} candidate attempt 1 failures to process.\n")

    for idx, fail_rec in enumerate(candidates, 1):
        if fail_rec.prompt_version is None:
            raise ValueError(
                f"Cannot repair {fail_rec.case_id} (pass {fail_rec.pass_idx}): source record is unversioned. "
                "A repair path cannot make new calls without established prompt lineage."
            )
        print(f"[{idx:02d}/{entered}] Repairing {fail_rec.case_id} (pass {fail_rec.pass_idx})...", end=" ", flush=True)

        outcome = repair_triage(
            case_id=fail_rec.case_id,
            pass_idx=fail_rec.pass_idx,
            arm=fail_rec.arm,
            original_prompt=fail_rec.prompt,
            initial_raw=fail_rec.raw_response,
            caller=run_prompt_only,
            on_record=persist_call,
            max_attempts=MAX_ATTEMPTS,
            prompt_version=fail_rec.prompt_version,
        )

        if outcome.valid:
            if outcome.attempts == 2:
                repaired_2 += 1
                print("Repaired @ Attempt 2")
            elif outcome.attempts == 3:
                repaired_3 += 1
                print("Repaired @ Attempt 3")
        else:
            gave_up += 1
            print(f"Gave up after {outcome.attempts} attempts ({outcome.error_message})")

    # Calculate baseline valid calls dynamically from Attempt 1 prompt_only records
    total_attempt_1_prompt = sum(
        1 for rec in all_records if rec.arm == "prompt_only" and rec.attempt == 1
    )
    initial_valid = total_attempt_1_prompt - entered
    final_valid = initial_valid + repaired_2 + repaired_3

    print("\n=== Repair Benchmark Summary ===")
    print(
        f"entered: {entered}   "
        f"repaired@2: {repaired_2}   "
        f"repaired@3: {repaired_3}   "
        f"gave up: {gave_up}   "
        f"final valid: {final_valid}/120"
    )


def run_benchmark(num_passes: int = 2) -> None:
    """
    Runs multi-pass matrix benchmark across prompt_only and constrained arms.
    Resume logic: skips completed chains and resumes unfinished repair attempts from their last recorded attempt using the original base prompt.
    """
    if not CASES_PATH.exists():
        raise FileNotFoundError(f"Cases not found at {CASES_PATH}")
        
    cases = [json.loads(line) for line in CASES_PATH.read_text().splitlines() if line.strip()]
    
    # 1. Idempotency Check: evaluate terminal state of each chain
    chains = defaultdict(list)
    if REPAIR_RUN_PATH.exists():
        with REPAIR_RUN_PATH.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    rec = RecordedCall.model_validate_json(line)
                    chains[(rec.prompt_version, rec.pass_idx, rec.case_id, rec.arm)].append(rec)
        
    def on_record(record: RecordedCall) -> None:
        REPAIR_RUN_PATH.parent.mkdir(parents=True, exist_ok=True)
        with REPAIR_RUN_PATH.open("a", encoding="utf-8") as f:
            f.write(record.model_dump_json() + "\n")
            
    print(f"=== Starting Repair Matrix Benchmark ({num_passes} passes) ===")
    
    for pass_idx in range(num_passes):
        for case in cases:
            case_id = case["id"]
            prompt = build_prompt(case["ticket_text"])
            
            for arm in ["prompt_only", "constrained"]:
                chain = chains.get((PROMPT_VERSION, pass_idx, case_id, arm), [])
                chain.sort(key=lambda x: x.attempt)
                
                is_finished = False
                if chain:
                    last_rec = chain[-1]
                    try:
                        validate_triage_payload(last_rec.raw_response)
                        is_finished = True
                    except (json.JSONDecodeError, ValidationError):
                        if last_rec.attempt >= MAX_ATTEMPTS:
                            is_finished = True
                
                if is_finished:
                    continue
                    
                print(f"[{pass_idx+1}/{num_passes}] {case_id} ({arm})...", end=" ", flush=True)
                caller_func = run_prompt_only if arm == "prompt_only" else run_constrained

                if chain:
                    # Resume unfinished chain from the last recorded attempt
                    last_rec = chain[-1]
                    repair_triage(
                        case_id=case_id, pass_idx=pass_idx, arm=arm,
                        original_prompt=chain[0].prompt,
                        initial_raw=(last_rec.raw_response, last_rec.usage or {}),
                        caller=caller_func, on_record=on_record,
                        starting_attempt=last_rec.attempt,
                        record_initial=False,
                        prompt_version=chain[0].prompt_version,
                    )
                    print(f"Resumed from attempt {last_rec.attempt}.")
                else:
                    # Start fresh chain
                    init_res = caller_func(prompt)
                    repair_triage(
                        case_id=case_id, pass_idx=pass_idx, arm=arm,
                        original_prompt=prompt, initial_raw=init_res,
                        caller=caller_func, on_record=on_record,
                        starting_attempt=1, record_initial=True,
                        prompt_version=PROMPT_VERSION,
                    )
                    print("Done.")

if __name__ == "__main__":
    run_benchmark(num_passes=2)
