"""Producer diagnostics survive strict receipt transport without granting effects."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

import pytest
from polaris.cells.roles.kernel.internal.transaction.tool_batch_executor._helpers import (
    annotate_autofilled_write_receipts,
    diff_write_file_autofill_evidence,
    fill_content_only_write_file_from_remaining_targets,
    split_write_file_duplicate_content_rejections,
)
from polaris.cells.roles.kernel.public.turn_contracts import BatchReceipt, ToolExecutionResult
from pydantic import ValidationError


def _batch(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "batch_id": "unit-observation",
        "turn_id": "unit-observation",
        "results": rows,
        "raw_results": copy.deepcopy(rows),
        "success_count": sum(row["status"] == "success" for row in rows),
        "failure_count": sum(row["status"] == "error" for row in rows),
        "effect_receipts": [],
    }


@pytest.mark.parametrize("status", ["success", "error"])
def test_real_autofill_producer_diagnostic_roundtrips_success_and_failed_rows(status: str) -> None:
    before = [
        {"call_id": "first", "tool_name": "write_file", "arguments": {"file": "first.py", "content": "first"}},
        {"call_id": "filled", "tool_name": "write_file", "arguments": {"content": "second"}},
    ]
    after = fill_content_only_write_file_from_remaining_targets(before, target_files=("first.py", "second.py"))
    evidence = diff_write_file_autofill_evidence(before, after)
    rows = [
        {"call_id": "first", "tool_name": "write_file", "status": "success", "effect_receipt": None},
        {
            "call_id": "filled",
            "tool_name": "write_file",
            "status": status,
            "effect_receipt": None,
            "error": "syntax rejected" if status == "error" else None,
        },
    ]
    batch = _batch(rows)
    annotate_autofilled_write_receipts([batch], evidence)
    original = copy.deepcopy(batch)
    validated = BatchReceipt.model_validate(batch)
    assert validated.model_dump(mode="json", exclude_unset=True) == original
    restored = BatchReceipt.model_validate_json(validated.model_dump_json(exclude_unset=True))
    assert restored.model_dump(mode="json", exclude_unset=True) == original
    assert restored.results[1].status == status
    assert restored.results[1].effect_receipt is None
    assert restored.effect_receipts == []
    assert batch == original


def test_duplicate_rejection_producer_roundtrips_without_effect_or_success() -> None:
    before = [
        {"call_id": "first", "tool_name": "write_file", "arguments": {"file": "first.py", "content": "same"}},
        {"call_id": "duplicate", "tool_name": "write_file", "arguments": {"content": "same"}},
    ]
    filled = fill_content_only_write_file_from_remaining_targets(before, target_files=("first.py", "second.py"))
    dispatchable, rejected = split_write_file_duplicate_content_rejections(filled)
    assert [row["call_id"] for row in dispatchable] == ["first"]
    batch = _batch(rejected)
    validated = BatchReceipt.model_validate_json(BatchReceipt.model_validate(batch).model_dump_json(exclude_unset=True))
    assert validated.model_dump(mode="json", exclude_unset=True) == batch
    assert validated.success_count == 0
    assert validated.failure_count == 1
    assert validated.results[0].status == "error"
    assert validated.results[0].effect_receipt is None
    assert validated.effect_receipts == []
    assert "Nothing was written" in str(validated.results[0].error)


@pytest.mark.parametrize(
    "field,path_key,literal_key,literal",
    [
        ("write_file_target_autofilled", "assigned_path", "basis", "sole_remaining_contract_target"),
        (
            "write_file_duplicate_content_rejection",
            "duplicate_of",
            "reason",
            "duplicate_content_write_file_without_file_argument",
        ),
    ],
)
@pytest.mark.parametrize(
    "invalid", ["unknown_nested", "number", "boolean", "empty_path", "wrong_literal", "not_mapping"]
)
def test_typed_diagnostic_rejects_unknown_fields_and_invalid_types(
    field: str, path_key: str, literal_key: str, literal: str, invalid: str
) -> None:
    diagnostic: Any = {path_key: "first.py", literal_key: literal}
    if invalid == "unknown_nested":
        diagnostic["authority"] = "never accepted"
    elif invalid == "number":
        diagnostic[path_key] = 7
    elif invalid == "boolean":
        diagnostic[path_key] = True
    elif invalid == "empty_path":
        diagnostic[path_key] = ""
    elif invalid == "wrong_literal":
        diagnostic[literal_key] = "grant_write_permission"
    else:
        diagnostic = True
    batch = _batch([{"call_id": "rejected", "tool_name": "write_file", "status": "error", field: diagnostic}])
    with pytest.raises(ValidationError):
        BatchReceipt.model_validate(batch)


def test_unknown_result_extra_remains_forbidden() -> None:
    batch = _batch([{"call_id": "unknown", "tool_name": "write_file", "status": "error", "unknown_authority": True}])
    with pytest.raises(ValidationError) as raised:
        BatchReceipt.model_validate(batch)
    assert raised.value.errors(include_input=False)[0]["type"] == "extra_forbidden"


def test_unannotated_legacy_result_serialization_and_hash_stay_identical() -> None:
    legacy = {
        "call_id": "legacy",
        "tool_name": "write_file",
        "status": "error",
        "result": None,
        "error": "rejected",
        "execution_time_ms": 0,
        "effect_receipt": None,
        "effect_receipt_commit": None,
        "directed_effect_mutation_status": None,
        "directed_effect_claim_status": None,
    }
    result = ToolExecutionResult.model_validate(legacy)
    assert result.to_dict() == legacy
    assert result.model_dump(mode="json") == legacy
    original_hash = hashlib.sha256(json.dumps(legacy, sort_keys=True).encode("utf-8")).hexdigest()
    serialized_hash = hashlib.sha256(json.dumps(result.to_dict(), sort_keys=True).encode("utf-8")).hexdigest()
    assert serialized_hash == original_hash
    batch = BatchReceipt.model_validate(_batch([legacy]))
    assert batch.model_dump(mode="json")["results"] == [legacy]
