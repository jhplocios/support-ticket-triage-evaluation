import argparse
from collections import Counter, defaultdict
from collections.abc import Callable
from decimal import Decimal
import json
from pathlib import Path
import sys
from typing import Any
from pydantic import TypeAdapter, ValidationError
from predict import CLAUDE_OPUS_5_INPUT_PER_MILLION, CLAUDE_OPUS_5_OUTPUT_PER_MILLION, RecordedCall, repair_triage
from schema import Category, Priority, TicketTriage

cat_adapter = TypeAdapter(Category)
pri_adapter = TypeAdapter(Priority)


def validate_order_id(raw_val: Any) -> str | None:
    return TicketTriage.normalize_order_id(raw_val)


def load_cases(path: str | Path = "cases/tickets.jsonl") -> list[dict]:
    p = Path(path)
    if not p.exists():
      raise FileNotFoundError(f"Evaluation dataset not found at {p.resolve()}")
    return [
        json.loads(line) for line in p.read_text().splitlines() if line.strip()
    ]


def predictor_oracle(case: dict) -> dict:
    return dict(case["expected"])


def predictor_corrupted(case: dict) -> dict:
    pred = dict(case["expected"])
    pred["priority"] = "low" if pred["priority"] != "low" else "urgent"
    return pred


def predictor_malformed(case: dict, index: int) -> dict:
    pred = dict(case["expected"])
    if index % 2 == 0:
      pred.pop("order_id", None)
    else:
      pred["category"] = "hardware_failure"
    return pred


def compute_baseline_profile(cases: list[dict]) -> dict:
    cat_mode = Counter(
        c["expected"]["category"] for c in cases
    ).most_common(1)[0][0]
    pri_mode = Counter(
        c["expected"]["priority"] for c in cases
    ).most_common(1)[0][0]
    ord_mode = Counter(
        c["expected"]["order_id"] for c in cases
    ).most_common(1)[0][0]
    nh_mode = Counter(
        c["expected"]["needs_human"] for c in cases
    ).most_common(1)[0][0]
    return {
        "category": cat_mode,
        "priority": pri_mode,
        "order_id": ord_mode,
        "needs_human": nh_mode,
    }


def evaluate(
    cases: list[dict],
    predictor: str | Callable[[dict], dict]
) -> dict[str, float]:
    n = len(cases)
    if n == 0:
        raise ValueError("No cases to evaluate.")

    baseline_payload = (
        compute_baseline_profile(cases) if predictor == "baseline" else {}
    )

    cat_hits = 0
    pri_hits = 0
    ord_hits = 0
    nh_hits = 0
    exact_hits = 0

    for i, case in enumerate(cases):
        gt_raw = case["expected"]
        try:
            gt = TicketTriage.model_validate(gt_raw)
        except ValidationError as e:
            raise ValueError(f"Ground truth case {case.get('id')} failed schema: {e}")

        if callable(predictor):
            raw_pred = predictor(case)
        elif predictor == "oracle":
            raw_pred = predictor_oracle(case)
        elif predictor == "corrupted":
            raw_pred = predictor_corrupted(case)
        elif predictor == "baseline":
            raw_pred = baseline_payload
        elif predictor == "malformed":
            raw_pred = predictor_malformed(case, i)
        else:
            raise ValueError(f"Unknown predictor: {predictor}")

        # Field 1: Category
        try:
            cat_val = cat_adapter.validate_python(raw_pred.get("category"))
            cat_match = cat_val == gt.category
        except (ValidationError, ValueError, TypeError):
            cat_match = False

        # Field 2: Priority
        try:
            pri_val = pri_adapter.validate_python(raw_pred.get("priority"))
            pri_match = pri_val == gt.priority
        except (ValidationError, ValueError, TypeError):
            pri_match = False

        # Field 3: Order ID
        if "order_id" not in raw_pred:
            ord_match = False
        else:
            try:
                ord_val = validate_order_id(raw_pred.get("order_id"))
                ord_match = ord_val == gt.order_id
            except (ValidationError, ValueError, TypeError):
                ord_match = False

        # Field 4: Needs Human
        try:
            raw_nh = raw_pred.get("needs_human")
            if not isinstance(raw_nh, bool):
                raise ValueError("needs_human must be a bool")
            nh_match = raw_nh == gt.needs_human
        except (ValidationError, ValueError, TypeError):
            nh_match = False

        cat_hits += int(cat_match)
        pri_hits += int(pri_match)
        ord_hits += int(ord_match)
        nh_hits += int(nh_match)
        exact_hits += int(cat_match and pri_match and ord_match and nh_match)

    return {
        "category": (cat_hits / n) * 100,
        "priority": (pri_hits / n) * 100,
        "order_id": (ord_hits / n) * 100,
        "needs_human": (nh_hits / n) * 100,
        "exact": (exact_hits / n) * 100,
    }


def calculate_call_cost(input_tokens: int, output_tokens: int) -> Decimal:
    input_cost = (Decimal(input_tokens) / Decimal(1_000_000)) * CLAUDE_OPUS_5_INPUT_PER_MILLION
    output_cost = (Decimal(output_tokens) / Decimal(1_000_000)) * CLAUDE_OPUS_5_OUTPUT_PER_MILLION
    return input_cost + output_cost


def replay_benchmark(recordings_path: str | Path) -> None:
    rec_file = Path(recordings_path)
    if not rec_file.exists():
        raise FileNotFoundError(f"Recordings file not found at {rec_file.resolve()}")

    cases = load_cases("cases/tickets.jsonl")
    
    with rec_file.open("r", encoding="utf-8") as f:
        records = [RecordedCall.model_validate_json(line) for line in f if line.strip()]

    distinct_versions = {r.prompt_version for r in records}
    if len(distinct_versions) > 1:
        sorted_versions = sorted(
            (str(v) for v in distinct_versions),
            key=lambda x: (x == "None", x)
        )
        sys.exit(f"Error: Mixed prompt versions detected in recordings: {sorted_versions}. Replay refused.")

    active_version = next(iter(distinct_versions))
    version_label = f"v{active_version[:8]}" if active_version else "unversioned"

    groups: dict[tuple[str, str, int], list[RecordedCall]] = defaultdict(list)
    for r in records:
        groups[(r.arm, r.case_id, r.pass_idx)].append(r)

    for key in groups:
        groups[key].sort(key=lambda x: x.attempt)

    # Dynamically determine pass count and file type
    num_passes = max((r.pass_idx for r in records), default=-1) + 1
    is_metered = any(r.usage is not None for r in records)
    target_valid = 30 * num_passes

    for arm in ["constrained", "prompt_only"]:
        print(f"\n=================== Arm: {arm} [prompt: {version_label}] ===================")
        stats = {"entered": 0, "repaired@2": 0, "repaired@3": 0, "gave_up": 0, "valid": 0}
        resolved_payloads: dict[tuple[str, int], dict] = {}
        
        total_calls = 0
        total_in_tokens = 0
        total_out_tokens = 0
        total_cost = Decimal("0")

        for pass_idx in range(num_passes):
            for case in cases:
                case_id = case["id"]
                chain = groups.get((arm, case_id, pass_idx), [])
                if not chain:
                    resolved_payloads[(case_id, pass_idx)] = {}
                    continue

                for rec in chain:
                    total_calls += 1
                    if is_metered and rec.usage is None:
                        raise ValueError(f"Missing usage payload for {case_id} attempt {rec.attempt}")
                    
                    if rec.usage:
                        in_t = rec.usage.get("input_tokens", 0)
                        out_t = rec.usage.get("output_tokens", 0)
                        total_in_tokens += in_t
                        total_out_tokens += out_t
                        total_cost += calculate_call_cost(in_t, out_t)

                first_call = chain[0]
                retry_responses = iter([r.raw_response for r in chain[1:]])

                outcome = repair_triage(
                    case_id=case_id, pass_idx=pass_idx, arm=arm,
                    original_prompt=first_call.prompt,
                    initial_raw=(first_call.raw_response, first_call.usage or {}),
                    caller=lambda prompt: (next(retry_responses, ""), {}),
                    max_attempts=3,
                )

                if outcome.attempts > 1:
                    stats["entered"] += 1

                if outcome.valid and outcome.triage is not None:
                    stats["valid"] += 1
                    if outcome.attempts == 2:
                        stats["repaired@2"] += 1
                    elif outcome.attempts == 3:
                        stats["repaired@3"] += 1
                    resolved_payloads[(case_id, pass_idx)] = outcome.triage.model_dump()
                else:
                    stats["gave_up"] += 1
                    resolved_payloads[(case_id, pass_idx)] = {}

        print(
            f"entered: {stats['entered']:2d}   "
            f"repaired@2: {stats['repaired@2']:2d}   "
            f"repaired@3: {stats['repaired@3']:2d}   "
            f"gave up: {stats['gave_up']:2d}   "
            f"final valid: {stats['valid']}/{target_valid}"
        )
        
        if is_metered:
            usd_per_call = total_cost / total_calls if total_calls else Decimal("0")
            usd_per_valid = total_cost / stats["valid"] if stats["valid"] else Decimal("0")
            print(f"Total API Calls: {total_calls} | Input Tokens: {total_in_tokens} | Output Tokens: {total_out_tokens}")
            print(f"Invoice Cost: ${total_cost:.5f} | USD/Call: ${usd_per_call:.5f} | USD/Valid: ${usd_per_valid:.5f}")

        # Build expanded cases: 30 cases × num_passes evaluations per arm
        expanded_cases = [
            {
                "id": c["id"],
                "expected": c["expected"],
                "pass_idx": pass_idx,
            }
            for pass_idx in range(num_passes)
            for c in cases
        ]

        def replay_predictor(case: dict) -> dict:
            return resolved_payloads.get((case["id"], case["pass_idx"]), {})

        results = evaluate(expanded_cases, replay_predictor)
        print(f"\nSemantic Accuracy (N={len(expanded_cases)}):")
        for field, score in results.items():
            print(f"  {field:<12}: {score:5.1f}%")       


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate ticket triage predictors.")
    parser.add_argument(
        "--predictor",
        choices=["oracle", "corrupted", "baseline", "malformed"],
        required=False,
        help="Predictor strategy to benchmark.",
    )
    parser.add_argument(
        "--replay",
        action="store_true",
        help="Replay and score recorded model responses offline.",
    )
    parser.add_argument(
        "--recordings",
        type=Path,
        default=Path("recordings/repair_run.jsonl"),
        help="Path to recordings JSONL file for offline replay.",
    )
    args = parser.parse_args()

    if args.replay or ("--recordings" in sys.argv):
        replay_benchmark(args.recordings)
    elif args.predictor:
        cases = load_cases()
        results = evaluate(cases, args.predictor)
        print(f"Results for --predictor {args.predictor} (N={len(cases)}):")
        for field, score in results.items():
            print(f"  {field:<12}: {score:5.1f}%")
    else:
        parser.error("Must specify either --predictor or --replay")
