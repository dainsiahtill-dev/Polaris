"""Core ContextOS hardening regression tests.

These tests pin the production contracts requested by the 2026-06-15
ContextOS hardening pass: fail-closed input validation, typed pipeline
fallback, and public contract graph declarations.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest
from polaris.kernelone.context.context_os.pipeline import PipelineInput, PipelineRunner
from polaris.kernelone.context.context_os.policies import (
    InputValidationPolicy,
    StateFirstContextOSPolicy,
)
from polaris.kernelone.context.context_os.runtime import StateFirstContextOS
from polaris.kernelone.errors import ValidationError


def _policy_with_input_limits(
    *,
    max_messages: int = 1000,
    max_message_size: int = 100_000,
    max_total_input_size: int = 10_000_000,
) -> StateFirstContextOSPolicy:
    return dataclasses.replace(
        StateFirstContextOSPolicy(),
        input_validation=InputValidationPolicy(
            max_messages=max_messages,
            max_message_size=max_message_size,
            max_total_input_size=max_total_input_size,
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("messages", "policy", "expected_constraint"),
    [
        (
            [{"role": "user", "content": "one"}, {"role": "user", "content": "two"}],
            _policy_with_input_limits(max_messages=1),
            "max_messages",
        ),
        (
            [{"role": "user", "content": "x" * 128}],
            _policy_with_input_limits(max_message_size=40, max_total_input_size=1000),
            "max_message_size",
        ),
        (
            [{"role": "user", "content": "x" * 32}, {"role": "assistant", "content": "y" * 32}],
            _policy_with_input_limits(max_message_size=1000, max_total_input_size=80),
            "max_total_input_size",
        ),
    ],
)
async def test_project_rejects_oversized_input_before_pipeline_and_records_error_receipt(
    messages: list[dict[str, Any]],
    policy: StateFirstContextOSPolicy,
    expected_constraint: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context_os = StateFirstContextOS(policy=policy, workspace=str(tmp_path))

    def _pipeline_must_not_run() -> None:
        raise AssertionError("input validation must run before pipeline creation")

    monkeypatch.setattr(context_os, "_get_pipeline_runner", _pipeline_must_not_run)

    with pytest.raises(ValidationError) as excinfo:
        await context_os.project(messages=messages)

    assert excinfo.value.constraint == expected_constraint
    report = context_os.get_last_projection_report()
    assert report is not None
    assert report["status"] == "error"
    assert report["error"]["constraint"] == expected_constraint
    assert report["projection_id"].startswith("ctxproj_error_")
    assert report["context_result_id"].startswith("ctxres_error_")

    receipt_refs = report["receipt_refs"]
    assert len(receipt_refs) == 1
    receipt_id = receipt_refs[0].removeprefix("contextos_error:")
    receipt_payload = context_os._receipt_store.get(receipt_id)
    assert receipt_payload is not None
    decoded = json.loads(receipt_payload)
    assert decoded["error"]["constraint"] == expected_constraint
    assert decoded["projection_id"] == report["projection_id"]
    assert decoded["context_result_id"] == report["context_result_id"]


def test_pipeline_stage_failure_degrades_to_typed_minimal_projection() -> None:
    class _BrokenCanonicalizer:
        def process(self, *_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("canonicalizer exploded")

    runner = PipelineRunner(policy=StateFirstContextOSPolicy(), resolved_context_window=16_000)
    runner._canonicalizer = _BrokenCanonicalizer()

    projection, report = runner.project(
        PipelineInput(messages=[{"role": "user", "content": "keep this event", "sequence": "0"}])
    )

    assert projection.snapshot is not None
    assert projection.snapshot.budget_plan is not None
    assert projection.run_card is not None
    assert report.projection_id.startswith("ctxproj_")
    assert report.context_result_id.startswith("ctxres_")
    assert report.fallback_count == 1
    assert report.stage_fallbacks == ("Canonicalizer",)
    assert any(decision.reason == "pipeline_stage_fallback" for decision in report.decisions)


def test_kernelone_core_graph_declares_contextos_public_contracts() -> None:
    cell_yaml = Path("src/backend/polaris/cells/kernelone/core/cell.yaml").read_text(encoding="utf-8")
    for contract_name in (
        "ProjectionRequest",
        "ProjectionResult",
        "ProjectionReport",
        "ReceiptRef",
        "ContextWindowPolicy",
    ):
        assert contract_name in cell_yaml
