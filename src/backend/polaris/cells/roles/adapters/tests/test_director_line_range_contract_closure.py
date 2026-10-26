"""Final normalized range inputs traverse actual policy/admission/physical owners."""

from __future__ import annotations

import hashlib

import pytest
from polaris.cells.roles.adapters.internal.director.execution_tools import _create_director_tool_executor
from polaris.cells.roles.kernel.tests.adapters_kernel_boundary_scenarios import observe_line_range_execution
from polaris.kernelone.editing.contracts import EditPrewriteRejectionV1, hash_edit_prewrite_arguments


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "replacement,expected", [("BETA\n", "alpha\nBETA\ngamma\ndelta\n"), ("", "alpha\ngamma\ndelta\n")]
)
async def test_real_director_line_range_commit(tmp_path, monkeypatch, replacement, expected):
    observation = await observe_line_range_execution(
        tmp_path,
        monkeypatch,
        [
            (
                "range",
                "edit_blocks",
                {
                    "file": "owned.txt",
                    "startLine": 2,
                    "endLine": 2,
                    "newText": replacement,
                },
            )
        ],
    )
    result = observation["result"]
    assert observation["content"] == expected
    row = result["batch_receipt"]["results"][0]
    assert row["result"]["effect_receipt_commit"]["state"] == "RECEIPT_COMMITTED"
    assert row["result"]["before_sha256"] == hashlib.sha256(observation["original_content"].encode("utf-8")).hexdigest()
    assert row["result"]["after_sha256"] == hashlib.sha256(expected.encode("utf-8")).hexdigest()
    assert len(observation["commits"]) == 1


@pytest.mark.asyncio
async def test_invalid_range_fails_before_operation_admission(tmp_path, monkeypatch):
    observation = await observe_line_range_execution(
        tmp_path,
        monkeypatch,
        [("range", "edit_blocks", {"file": "owned.txt", "start": 2, "end": 2})],
        tolerate_runtime_error=True,
    )
    result = observation["result"]
    assert observation["content"] == observation["original_content"]
    assert observation["operations"] == []
    if result is not None:
        assert result["batch_receipt"]["failure_count"] == 1


def test_typed_prewrite_rejection_hashes_actual_input_and_unchanged_bytes(tmp_path):
    target = tmp_path / "owned.txt"
    target.write_text("alpha\nbeta\ngamma\ndelta\n", encoding="utf-8")
    executor = _create_director_tool_executor(str(tmp_path))
    executor._bind_authorized_scope(["owned.txt"], effect_target="owned.txt")
    arguments = {"file": "owned.txt", "blocks": "invalid block content"}
    result = executor.execute_tool("edit_blocks", arguments, prewrite_operation_hash="a" * 64)
    assert type(result) is EditPrewriteRejectionV1
    assert result.reason_code == "edit_blocks_invalid_blocks"
    assert result.arguments_hash == hash_edit_prewrite_arguments(arguments)
    assert result.before_sha256 == result.after_sha256 == hashlib.sha256(target.read_bytes()).hexdigest()
    assert result.normalized_operation_hash == "a" * 64
    assert result.write_started is False
    without_binding = executor.execute_tool("edit_blocks", arguments)
    assert isinstance(without_binding, dict) and without_binding.get("ok") is False
