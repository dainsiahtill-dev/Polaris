"""A platform context rejection is not evidence of model/provider failure."""

from polaris.cells.control_plane.run_ledger.public import FailureClassV1
from polaris.cells.roles.adapters.internal.director.execute_method._phases_failure import (
    _primary_llm_provider_failure_payload,
)
from polaris.cells.roles.kernel.internal.llm_caller.error_handling import classify_error, is_retryable_error


def test_context_coverage_rejection_is_nonretryable_platform_failure() -> None:
    error = "LLM call failed: Final provider request evidence coverage failed; missing_required_refs=framework_best_practices"
    category = classify_error(error)
    assert category == "validation"
    assert not is_retryable_error(category)


def test_primary_call_preserves_context_failure_even_with_legacy_provider_category() -> None:
    payload = _primary_llm_provider_failure_payload(
        {
            "success": False,
            "error": "Final provider request evidence coverage failed; missing_required_refs=pm_contract",
            "error_category": "provider",
            "llm_calls": 0,
        }
    )
    assert payload is not None
    assert payload["error_code"] == "final_request_context_evidence_missing"
    assert payload["failure_class"] == FailureClassV1.EXECUTION_EVIDENCE_MISSING.value
    assert payload["responsible_layer"] == "execution_control_plane"


def test_provider_overload_is_retryable_transport_failure() -> None:
    error = '529 Server Error from provider: {"type":"error","error":{"type":"overloaded_error"}}'
    category = classify_error(error)
    assert category == "network"
    assert is_retryable_error(category)
