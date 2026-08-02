from __future__ import annotations

from types import SimpleNamespace

from polaris.cells.roles.kernel.public.deferred_repair_commit_service import (
    _forward_target_paths_from_tool_result,
    _normalize_capability_token,
    _partition_non_conflicting_deferred_tool_results,
)
from polaris.cells.runtime.task_runtime.public import TaskRuntimeExecutionAttemptIdentityV1


def _execution_attempt() -> TaskRuntimeExecutionAttemptIdentityV1:
    return TaskRuntimeExecutionAttemptIdentityV1(
        workspace="/workspace",
        task_id=1,
        external_task_id="TASK-1",
        session_id="session-1",
        attempt=1,
        role_id="director",
        worker_id="director-worker",
        run_id="run-1",
        lease_expires_at="2030-01-01T00:00:00+00:00",
    )


def _deferred_tool(source_tool: str, *paths: str) -> dict[str, object]:
    effects = tuple(SimpleNamespace(contingency_kind="forward", target_path=path) for path in paths)
    request = SimpleNamespace(
        plan=SimpleNamespace(effects=effects),
        allowed_paths=tuple(paths),
    )
    return {
        "tool": "deferred_director_repair",
        "success": True,
        "result": {
            "status": "deferred_repair_effects_pending",
            "source_tool": source_tool,
            "deferred_request": request,
        },
    }


def test_partition_non_conflicting_deferred_keeps_smoke_separate_from_main_ts_conflicts() -> None:
    """R174/M06: main.ts multi-fix + tests/smoke must not share a conflict wave."""

    tools = [
        _deferred_tool("deterministic_typescript_missing_export_repair", "src/models/index.ts"),
        _deferred_tool("deterministic_typescript_unused_local_repair", "src/main.ts"),
        _deferred_tool("deterministic_typescript_unused_import_repair", "src/main.ts"),
        _deferred_tool("deterministic_typescript_identifier_suggestion_repair", "src/main.ts"),
        _deferred_tool("deterministic_typescript_json_as_source_repair", "tests/verify.test.ts"),
    ]

    waves = _partition_non_conflicting_deferred_tool_results(tools)
    assert len(waves) >= 3  # three main.ts owners cannot share one wave
    smoke_wave = next(
        wave
        for wave in waves
        if any(
            str((item.get("result") or {}).get("source_tool") or "") == "deterministic_typescript_json_as_source_repair"
            for item in wave
        )
    )
    smoke_paths = set()
    for item in smoke_wave:
        smoke_paths |= set(_forward_target_paths_from_tool_result(item))
    assert "tests/verify.test.ts" in smoke_paths
    # Smoke wave must not also carry a second main.ts-only repair that would
    # have been batched into the fail-all conflict set.
    main_only = [
        item for item in smoke_wave if _forward_target_paths_from_tool_result(item) == frozenset({"src/main.ts"})
    ]
    assert len(main_only) <= 1


def test_capability_token_cannot_be_synthesized_from_execution_attempt() -> None:
    attempt = _execution_attempt()

    assert _normalize_capability_token(None, execution_attempt=attempt) == {}
    assert (
        _normalize_capability_token(
            {
                "token_id": "job-1",
                "capability_audit": {"ok": True},
            },
            execution_attempt=attempt,
        )
        == {}
    )


def test_audited_capability_token_with_envelope_is_preserved() -> None:
    attempt = _execution_attempt()

    normalized = _normalize_capability_token(
        {
            "token_id": "job-1",
            "execution_envelope_hash": "a" * 64,
            "capability_audit": {"ok": True},
            "allowed_commands": ["cargo check"],
            "source": "control_plane.job_token",
        },
        execution_attempt=attempt,
    )

    assert normalized == {
        "token_id": "job-1",
        "execution_envelope_hash": "a" * 64,
        "capability_audit_ok": True,
        "allowed_commands": ("cargo check",),
        "source": "control_plane.job_token",
        "run_id": "run-1",
        "stage": "director_materialization_deferred",
    }
