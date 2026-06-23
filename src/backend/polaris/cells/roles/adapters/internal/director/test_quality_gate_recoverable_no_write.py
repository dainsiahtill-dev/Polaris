"""Regression tests for Director recoverable no-write mutation contract gates."""

from __future__ import annotations

from polaris.cells.roles.adapters.internal.director.quality_gate import (
    _stage_summary_has_recoverable_no_write_mutation_contract_exception,
)


def test_stage_summary_detects_nested_primary_llm_no_write_contract_error() -> None:
    summary = {
        "adapter_result": {
            "primary_llm": {
                "error": (
                    "TransactionKernel execution failed: single_batch_contract_violation: "
                    "mutation requested but no write tool invocation in decision batch."
                )
            }
        }
    }

    assert _stage_summary_has_recoverable_no_write_mutation_contract_exception(summary) is True


def test_stage_summary_rejects_unsafe_no_write_contract_error() -> None:
    summary = {
        "primary_llm": {
            "error": (
                "single_batch_contract_violation: mutation requested but no write tool invocation; "
                "unauthorized path traversal"
            )
        }
    }

    assert _stage_summary_has_recoverable_no_write_mutation_contract_exception(summary) is False
