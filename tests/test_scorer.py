import subprocess
import sys
import pytest
from predict import RecordedCall
from score import evaluate, load_cases, replay_benchmark


@pytest.fixture
def eval_cases():
    return load_cases("cases/tickets.jsonl")


def test_oracle_reports_100_percent(eval_cases):
    results = evaluate(eval_cases, "oracle")
    assert results["category"] == 100.0
    assert results["priority"] == 100.0
    assert results["order_id"] == 100.0
    assert results["needs_human"] == 100.0
    assert results["exact"] == 100.0


def test_corrupted_drops_broken_field_to_zero(eval_cases):
    results = evaluate(eval_cases, "corrupted")
    assert results["priority"] == 0.0
    assert results["category"] == 100.0
    assert results["order_id"] == 100.0
    assert results["needs_human"] == 100.0
    assert results["exact"] == 0.0


def test_baseline_exact_match_under_twenty_five_percent(eval_cases):
    results = evaluate(eval_cases, "baseline")
    assert results["exact"] < 25.0


def test_malformed_order_id_scores_as_isolated_miss(eval_cases):
    def predictor_bad_order_id(case: dict) -> dict:
      pred = dict(case["expected"])
      pred["order_id"] = "ord-12345"  # Fails regex normalization
      return pred

    results = evaluate(eval_cases, predictor_bad_order_id)
    assert results["order_id"] == 0.0
    assert results["category"] == 100.0
    assert results["priority"] == 100.0
    assert results["needs_human"] == 100.0
    assert results["exact"] == 0.0


def test_malformed_category_scores_as_isolated_miss(eval_cases):
    def predictor_bad_category(case: dict) -> dict:
      pred = dict(case["expected"])
      pred["category"] = "hardware_failure"  # Out-of-enum
      return pred

    results = evaluate(eval_cases, predictor_bad_category)
    assert results["category"] == 0.0
    assert results["priority"] == 100.0
    assert results["order_id"] == 100.0
    assert results["needs_human"] == 100.0
    assert results["exact"] == 0.0


def test_omitted_needs_human_scores_as_isolated_miss(eval_cases):
    def predictor_missing_needs_human(case: dict) -> dict:
      pred = dict(case["expected"])
      pred.pop("needs_human", None)  # Omitted required field
      return pred

    results = evaluate(eval_cases, predictor_missing_needs_human)
    assert results["needs_human"] == 0.0
    assert results["category"] == 100.0
    assert results["priority"] == 100.0
    assert results["order_id"] == 100.0
    assert results["exact"] == 0.0


def test_malformed_predictor_per_field_reporting(eval_cases):
    results = evaluate(eval_cases, "malformed")
    # Priority and needs_human are never touched by predictor_malformed
    assert results["priority"] == 100.0
    assert results["needs_human"] == 100.0
    # Every row has at least one flaw (even rows omit order_id, odd rows break category)
    assert results["exact"] == 0.0


def test_integer_boolean_needs_human_rejected_as_miss(eval_cases):
    def predictor_int_bool(case: dict) -> dict:
      pred = dict(case["expected"])
      # Emit 1 instead of True, 0 instead of False
      pred["needs_human"] = 1 if pred["needs_human"] else 0
      return pred

    results = evaluate(eval_cases, predictor_int_bool)
    # Guard ensures int is not coerced to bool, scoring 0.0%
    assert results["needs_human"] == 0.0
    assert results["category"] == 100.0
    assert results["priority"] == 100.0
    assert results["order_id"] == 100.0
    assert results["exact"] == 0.0


def test_whitespace_padded_order_id_normalized_to_hit(eval_cases):
    def predictor_padded_order_id(case: dict) -> dict:
      pred = dict(case["expected"])
      if pred["order_id"] is not None:
        pred["order_id"] = f"  {pred['order_id']}  "
      return pred

    results = evaluate(eval_cases, predictor_padded_order_id)
    # Trimming normalisation ensures whitespace-padded valid IDs still match 100.0%
    assert results["order_id"] == 100.0
    assert results["exact"] == 100.0


def test_replay_refuses_mixed_prompt_versions(tmp_path):
    mixed_file = tmp_path / "mixed_recordings.jsonl"
    rec1 = RecordedCall(
        arm="prompt_only",
        case_id="case_001",
        pass_idx=0,
        prompt="prompt a",
        raw_response='{"category": "billing", "priority": "normal", "order_id": null, "needs_human": false}',
        timestamp="2026-09-28T00:00:00Z",
        attempt=1,
        prompt_version="hash_aaa111",
    )
    rec2 = RecordedCall(
        arm="prompt_only",
        case_id="case_002",
        pass_idx=0,
        prompt="prompt b",
        raw_response='{"category": "bug", "priority": "urgent", "order_id": null, "needs_human": false}',
        timestamp="2026-09-28T00:00:01Z",
        attempt=1,
        prompt_version="hash_bbb222",
    )
    mixed_file.write_text(f"{rec1.model_dump_json()}\n{rec2.model_dump_json()}\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "score.py", "--recordings", str(mixed_file)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "hash_aaa111" in result.stderr
    assert "hash_bbb222" in result.stderr