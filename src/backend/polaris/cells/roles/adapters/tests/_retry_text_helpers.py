"""Shared retry-text fallback assertions for director adapter tests.

Recovered from test_director_adapter_pure.py (lost in the module split;
call sites moved to test_director_adapter_misc_2.py without the helper).
"""

from __future__ import annotations

from typing import Any


def assert_retry_text_fallback_is_non_authoritative(
    *,
    adapter: Any,
    task_id: str,
    result: dict[str, Any],
    summary_key: str,
) -> dict[str, Any]:
    """Assert retry text/file-block output did not bypass native tool execution."""
    assert result["success"] is False
    assert result["error_code"] == "incomplete_materialization"
    assert result["failure_class"] == "INCOMPLETE_MATERIALIZATION"

    updated = adapter.task_runtime.get_task(task_id)
    assert updated is not None
    raw_metadata = updated.get("metadata")
    metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
    raw_adapter_result = metadata.get("adapter_result")
    adapter_result: dict[str, Any] = raw_adapter_result if isinstance(raw_adapter_result, dict) else {}

    assert adapter_result.get("materialization_error") == "director_no_materialized_changes"
    assert adapter_result.get("materialization_error_code") == "incomplete_materialization"
    assert adapter_result.get("failure_class") == "INCOMPLETE_MATERIALIZATION"
    assert adapter_result.get("new_files") == []
    assert adapter_result.get("modified_files") == []
    retry_summary = adapter_result.get(summary_key)
    assert isinstance(retry_summary, dict)
    assert retry_summary.get("attempted") is True
    write_args = {tuple(item) for item in retry_summary.get("write_args", []) if isinstance(item, (list, tuple))}
    assert ("patch_apply", 0) in write_args
    return adapter_result
