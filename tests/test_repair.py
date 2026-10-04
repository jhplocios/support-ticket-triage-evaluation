import json
from decimal import Decimal
from pathlib import Path
from predict import (
    PROMPT_VERSION, RecordedCall, build_prompt, load_prompt_template, repair_triage,
)
from score import calculate_call_cost


CASE_003_RAW_FENCED = """```json\n{\n  "category": "billing",\n  "priority": "urgent",\n  "order_id": "ORD-88192",\n  "needs_human": true\n}\n```"""
VALID_CASE_003_RAW = """{\n  "category": "billing",\n  "priority": "urgent",\n  "order_id": "ORD-88192",\n  "needs_human": true\n}"""

def test_repair_succeeds_on_attempt_2_via_stub():
    recorded: list[RecordedCall] = []

    def stub_caller(prompt: str) -> tuple[str, dict]:
        return VALID_CASE_003_RAW, {"input_tokens": 150, "output_tokens": 50}

    outcome = repair_triage(
        case_id="case_003",
        pass_idx=0,
        arm="prompt_only",
        original_prompt="triage this ticket",
        initial_raw=(CASE_003_RAW_FENCED, {"input_tokens": 100, "output_tokens": 40}),
        caller=stub_caller,
        on_record=recorded.append,
    )

    assert outcome.valid is True
    assert outcome.attempts == 2
    assert len(recorded) == 2
    assert recorded[0].usage["input_tokens"] == 100 # type: ignore
    assert recorded[1].usage["input_tokens"] == 150 # type: ignore

def test_repair_gives_up_after_exact_ceiling_via_stub():
    recorded: list[RecordedCall] = []

    def stub_caller_always_bad(prompt: str) -> tuple[str, dict]:
        return CASE_003_RAW_FENCED, {"input_tokens": 200, "output_tokens": 40}

    outcome = repair_triage(
        case_id="case_003",
        pass_idx=0,
        arm="prompt_only",
        original_prompt="triage this ticket",
        initial_raw=(CASE_003_RAW_FENCED, {"input_tokens": 100, "output_tokens": 40}),
        caller=stub_caller_always_bad,
        on_record=recorded.append,
        max_attempts=3,
    )

    assert outcome.valid is False
    assert outcome.attempts == 3
    assert len(recorded) == 3
    
    total_input = sum(r.usage["input_tokens"] for r in recorded if r.usage)
    total_output = sum(r.usage["output_tokens"] for r in recorded if r.usage)
    assert total_input == 100 + 200 + 200
    assert total_output == 40 + 40 + 40

    # Verify fixed-point pricing logic accounts for the whole chain independently
    total_invoice = sum(
        calculate_call_cost(r.usage["input_tokens"], r.usage["output_tokens"])
        for r in recorded if r.usage
    )
    
    # Attempt 1: 100 in, 40 out = $0.00150
    # Attempt 2: 200 in, 40 out = $0.00200
    # Attempt 3: 200 in, 40 out = $0.00200
    # Expected sum = $0.00550
    expected_invoice = Decimal("0.00550")
    
    assert total_invoice == expected_invoice
    assert total_invoice > Decimal("0")


def test_repair_stamps_prompt_version_across_attempts():
    recorded: list[RecordedCall] = []

    def stub_caller(prompt: str) -> tuple[str, dict]:
        return VALID_CASE_003_RAW, {"input_tokens": 150, "output_tokens": 50}

    outcome = repair_triage(
        case_id="case_003",
        pass_idx=0,
        arm="prompt_only",
        original_prompt="triage this ticket",
        initial_raw=(CASE_003_RAW_FENCED, {"input_tokens": 100, "output_tokens": 40}),
        caller=stub_caller,
        on_record=recorded.append,
    )

    assert outcome.valid is True
    assert len(recorded) == 2
    # Verify 100% stamped over total records
    assert all(r.prompt_version == PROMPT_VERSION for r in recorded)


def test_resume_preserves_origin_prompt_version():
    recorded: list[RecordedCall] = []
    origin_version = "historical_version_hash_12345"

    def stub_caller(prompt: str) -> tuple[str, dict]:
        return VALID_CASE_003_RAW, {"input_tokens": 150, "output_tokens": 50}

    outcome = repair_triage(
        case_id="case_003",
        pass_idx=0,
        arm="prompt_only",
        original_prompt="triage this ticket",
        initial_raw=(CASE_003_RAW_FENCED, {"input_tokens": 100, "output_tokens": 40}),
        caller=stub_caller,
        on_record=recorded.append,
        starting_attempt=2,
        record_initial=False,
        prompt_version=origin_version,
    )

    assert outcome.valid is True
    assert len(recorded) == 1
    assert recorded[0].prompt_version == origin_version


def test_resume_preserves_legacy_unversioned():
    recorded: list[RecordedCall] = []

    def stub_caller(prompt: str) -> tuple[str, dict]:
        return VALID_CASE_003_RAW, {"input_tokens": 150, "output_tokens": 50}

    outcome = repair_triage(
        case_id="case_003",
        pass_idx=0,
        arm="prompt_only",
        original_prompt="triage this ticket",
        initial_raw=(CASE_003_RAW_FENCED, {"input_tokens": 100, "output_tokens": 40}),
        caller=stub_caller,
        on_record=recorded.append,
        starting_attempt=2,
        record_initial=False,
        prompt_version=None,
    )

    assert outcome.valid is True
    assert len(recorded) == 1
    # Verifies legacy resume remains unversioned (0/1 stamped, 1/1 preserved)
    assert recorded[0].prompt_version is None


def test_one_character_mutation_changes_loader_hash(tmp_path):
    temp_template = tmp_path / "prompt_template.txt"
    # Canonical 405 bytes without trailing newline
    canonical_content = (
        "You are a customer support ticket triage system.\n"
        "Given the ticket below, extract the triage information into a JSON object with:\n"
        "- category: \"billing\", \"bug\", \"account_access\", \"feature_request\", or \"other\"\n"
        "- priority: \"low\", \"normal\", or \"urgent\"\n"
        "- order_id: string matching \"ORD-XXXXX\" or null if no valid order ID\n"
        "- needs_human: boolean (true/false)\n\n"
        "Output only the JSON object.\n\n"
        "Ticket:\n{ticket_text}"
    )
    temp_template.write_text(canonical_content, encoding="utf-8")
    _, v1 = load_prompt_template(temp_template)
    assert v1 == "383d0bd9a8010a73bb245fc6829abc071fddca213115b48a01c5d71c72e9c603"

    # 1-character addition: append trailing \n (406 bytes)
    temp_template.write_text(canonical_content + "\n", encoding="utf-8")
    _, v2 = load_prompt_template(temp_template)
    assert v2 == "d39e35936223cc35711dddc9c2eb0efcf46a0be4cbcbb01d4a704d2e01c97ebc"
    assert v1 != v2

    # Revert: restores canonical ID
    temp_template.write_text(canonical_content, encoding="utf-8")
    _, v3 = load_prompt_template(temp_template)
    assert v3 == v1


def test_build_prompt_matches_legacy_attempt_1_prompt():
    cases_path = Path("cases/tickets.jsonl")
    first_case = json.loads(cases_path.read_text(encoding="utf-8").splitlines()[0])

    with open("recordings/responses.jsonl", encoding="utf-8") as f:
        legacy_rec = json.loads(f.readline())

    rendered = build_prompt(first_case["ticket_text"])
    assert rendered == legacy_rec["prompt"]
    assert len(rendered) == len(legacy_rec["prompt"])