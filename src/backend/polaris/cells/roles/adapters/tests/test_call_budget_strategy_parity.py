"""A scoped Director call ceiling survives typed tasking reconstruction.

These tests run the actual adapter preparation/profile/strategy method chain;
they never invoke a provider, claim a task, or modify a target workspace.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.director.tasking.public.execution_guidance import resolve_task_execution_profile
from polaris.cells.roles.adapters.internal.director.adapter._core import DirectorAdapter
from polaris.cells.roles.adapters.internal.director.adapter._timeout_budget import _prepare_role_dialogue_context
from polaris.kernelone.llm.engine.invocation_budget import (
    bind_provider_invocation_budget,
    get_provider_invocation_budget,
)


def _prepare_and_rebuild(
    tmp_path: Path,
    *,
    phase: str,
    limit: int,
    extra: dict[str, Any] | None = None,
    stage: str = "first_call",
    task_type: str = "write_code",
) -> tuple[dict[str, Any], dict[str, Any], float]:
    profile = resolve_task_execution_profile(
        subject="Implement the Python service",
        metadata={"task_type": task_type, "phase": phase},
        target_files=["src/service.py"],
        scope_paths=["src/service.py"],
        workspace=str(tmp_path),
    )
    original: dict[str, Any] = {
        "workspace": str(tmp_path),
        "task_id": "TASK-1",
        "run_id": "factory-budget-parity",
        "target_files": ["src/service.py"],
        "scope_paths": ["src/service.py"],
        "director_execution_profile": profile.to_dict(),
        "task_execution_profile": profile.to_dict(),
        "llm_max_tokens": limit,
        "max_output_tokens": limit,
        "request_timeout_seconds": 31.0,
        **(extra or {}),
    }
    before = deepcopy(original)
    context, timeout = _prepare_role_dialogue_context(original, timeout_seconds=42.0, stage_label=stage)
    metadata = DirectorAdapter._build_role_runtime_metadata(context, max_retries=0)
    DirectorAdapter._ensure_director_execution_profile(
        message="Implement the Python service", context=context, metadata=metadata, workspace=str(tmp_path)
    )
    assert original == before
    return context, metadata, timeout


def _assert_call_budget_facts(source: dict[str, Any], expected: int) -> None:
    assert source["llm_max_tokens"] == expected
    assert source["max_output_tokens"] == expected
    for key in ("director_execution_strategy", "task_execution_strategy"):
        assert source[key]["output_budget_tokens"] == expected, key
    for key in ("director_execution_contract", "task_execution_contract"):
        assert source[key]["context_budget"]["output_budget_tokens"] == expected, key
    for key in ("director_execution_envelope", "task_execution_envelope"):
        assert source[key]["budget_policy"]["output_budget_tokens"] == expected, key
        assert source[key]["model_policy"]["max_tokens"] == expected, key
    assert source["cognitive_strategy_override"]["cognitive_runtime"]["output_budget_tokens"] == expected


@pytest.mark.parametrize("phase", ["requirements", "implementation"])
@pytest.mark.parametrize(("limit", "expected"), [(128000, 7000), (3000, 3000), (256, 256)])
def test_primary_write_call_projects_effective_budget_into_all_current_typed_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str, limit: int, expected: int
) -> None:
    """Catch scalar cap being shadowed by regenerated 128k typed facts."""
    monkeypatch.setenv("KERNELONE_DIRECTOR_FORCED_WRITE_OUTPUT_TOKENS", "7000")
    context, metadata, timeout = _prepare_and_rebuild(tmp_path, phase=phase, limit=limit)
    _assert_call_budget_facts(context, expected)
    _assert_call_budget_facts(metadata, expected)
    assert timeout == 31.0
    assert context["request_timeout_seconds"] == 31.0
    assert metadata["max_retries"] == 0
    assert context["target_files"] == ["src/service.py"]
    assert context["scope_paths"] == ["src/service.py"]
    assert metadata["director_execution_profile"]["phase"] == phase


def test_adapter_preparation_never_raises_an_existing_subfloor_call_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KERNELONE_DIRECTOR_FORCED_WRITE_OUTPUT_TOKENS", "7000")
    context, _ = _prepare_role_dialogue_context(
        {"llm_max_tokens": 256}, timeout_seconds=31.0, stage_label="empty_write_content_retry"
    )
    assert context["llm_max_tokens"] == 256
    assert context["director_forced_write_output_budget"]["max_tokens"] == 256


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        ({"metadata": {"max_tokens": 1400}}, 1400),
        ({"task_execution_contract": {"context_budget": {"output_budget_tokens": 1150}}}, 1150),
        ({"director_execution_strategy": {"output_budget_tokens": 1250}}, 1250),
    ],
)
def test_reconstruction_retains_lower_inherited_call_ceilings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra: dict[str, Any], expected: int
) -> None:
    monkeypatch.setenv("KERNELONE_DIRECTOR_FORCED_WRITE_OUTPUT_TOKENS", "7000")
    context, metadata, _ = _prepare_and_rebuild(tmp_path, phase="implementation", limit=128000, extra=extra)
    _assert_call_budget_facts(context, expected)
    _assert_call_budget_facts(metadata, expected)
    assert context["director_forced_write_output_budget"]["max_tokens"] == expected
    if "max_tokens" in metadata:
        assert metadata["max_tokens"] == expected


def test_review_first_call_does_not_receive_a_forced_write_budget(tmp_path: Path) -> None:
    context, metadata, _ = _prepare_and_rebuild(tmp_path, phase="requirements", limit=128000, task_type="code_review")
    _assert_call_budget_facts(context, 60000)
    _assert_call_budget_facts(metadata, 60000)
    assert "director_forced_write_output_budget" not in context


@pytest.mark.parametrize("stage", ["first_call", "empty_write_content_retry"])
def test_readonly_typed_profile_precedes_stale_forced_write_markers(stage: str) -> None:
    """An inherited retry marker cannot turn a read-only review into a write call."""
    profile = resolve_task_execution_profile(
        subject="Review service", metadata={"task_type": "code_review"}, target_files=["src/service.py"]
    )
    original: dict[str, Any] = {
        "task_execution_profile": profile.to_dict(),
        "director_execution_profile": profile.to_dict(),
        "target_files": ["src/service.py"],
        "director_empty_write_retry": {},
        "llm_max_tokens": 128000,
    }
    before = deepcopy(original)
    context, _ = _prepare_role_dialogue_context(original, timeout_seconds=31.0, stage_label=stage)
    assert context["llm_max_tokens"] == 128000
    assert "director_forced_write_output_budget" not in context
    assert original == before


@pytest.mark.parametrize(("limit", "expected"), [(128000, 128000), (3000, 3000)])
def test_finalization_ignores_inherited_forced_write_markers_without_expanding_ceiling(
    tmp_path: Path, limit: int, expected: int
) -> None:
    context, metadata, _ = _prepare_and_rebuild(
        tmp_path,
        phase="implementation",
        limit=limit,
        stage="empty_write_content_retry",
        extra={
            "director_empty_write_retry": {"target_files": ["src/service.py"]},
            "_transaction_kernel_forced_tool_definitions": [],
            "_transaction_kernel_forced_tool_choice": "none",
        },
    )
    _assert_call_budget_facts(context, expected)
    _assert_call_budget_facts(metadata, expected)
    assert "director_forced_write_output_budget" not in context


def test_existing_invocation_contextvar_is_neither_replaced_nor_extended(tmp_path: Path) -> None:
    with bind_provider_invocation_budget(2.0) as admitted:
        deadline = admitted.deadline
        remaining = admitted.remaining()
        context, metadata, _ = _prepare_and_rebuild(tmp_path, phase="requirements", limit=3000)
        _assert_call_budget_facts(context, 3000)
        _assert_call_budget_facts(metadata, 3000)
        assert get_provider_invocation_budget() is admitted
        assert admitted.deadline == deadline
        assert admitted.remaining() <= remaining


def test_inherited_alias_and_nested_metadata_are_current_call_facts_not_task_defaults(tmp_path: Path) -> None:
    """No legacy alias may continue advertising 128k after a 3000 call admission."""
    context, metadata, _ = _prepare_and_rebuild(
        tmp_path,
        phase="implementation",
        limit=3000,
        extra={
            "execution_strategy": {"output_budget_tokens": 128000},
            "metadata": {
                "llm_max_tokens": 128000,
                "max_tokens": 128000,
                "director_execution_strategy": {"output_budget_tokens": 128000},
                "task_execution_contract": {"context_budget": {"output_budget_tokens": 128000}},
            },
        },
    )
    _assert_call_budget_facts(context, 3000)
    _assert_call_budget_facts(metadata, 3000)
    assert context["execution_strategy"]["output_budget_tokens"] == 3000
    nested = context["metadata"]
    assert nested["llm_max_tokens"] == 3000
    assert nested["max_tokens"] == 3000
    assert nested["director_execution_strategy"]["output_budget_tokens"] == 3000
    assert nested["task_execution_contract"]["context_budget"]["output_budget_tokens"] == 3000
