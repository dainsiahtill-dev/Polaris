"""Unit tests for PMAdapter pure logic (no I/O, no LLM).

Covers:
- _build_pm_message / _build_pm_retry_message
- _extract_task_contracts / _extract_tasks_from_payload / _extract_json_payload
- _extract_tasks_from_sections / _extract_tasks_from_bullets
- _normalize_task_contract / _normalize_list
- _infer_scope_from_title / _derive_domain_token / _extract_domain_keywords
- _analyze_directive_complexity / _apply_meta_planning_hints
- _normalize_projection_project_slug / _extract_projection_contract_hint
- _apply_projection_contract_hint / _build_projection_hint_contracts
- _synthesize_task_contracts_from_directive
- _canonical_text / _build_task_identity_signature / _pick_preferred_task_id / _find_existing_task_match
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, cast

from polaris.cells.roles.adapters.internal.pm.synthesis import (
    _extract_deterministic_checks_from_directive,
)
from polaris.cells.roles.adapters.internal.pm_adapter import PMAdapter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter(tmp_path: Any) -> PMAdapter:
    return PMAdapter(workspace=str(tmp_path))


class _RowProjectionOnlyTaskBoard:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = [dict(row) for row in rows]

    def list_observable_task_rows(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self._rows]

    def list_task_rows(self) -> list[dict[str, Any]]:
        raise AssertionError("PM read-model consumers must use list_observable_task_rows()")

    def list_all(self) -> list[Any]:
        raise AssertionError("PM read-model consumers must use list_observable_task_rows()")

    def task_exists(self, task_id: Any) -> bool:
        return any(str(row.get("id") or "") == str(task_id or "") for row in self._rows)

    def update_task(
        self,
        task_id: Any,
        *,
        status: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        for row in self._rows:
            if str(row.get("id") or "") != str(task_id or ""):
                continue
            if status is not None:
                row["status"] = status
            if metadata is not None:
                current_metadata = row.get("metadata")
                merged_metadata = dict(current_metadata) if isinstance(current_metadata, dict) else {}
                merged_metadata.update(metadata)
                row["metadata"] = merged_metadata

    def fail_task_row_from_role_adapter(
        self,
        task_id: Any,
        *,
        reason: str,
        metadata: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any] | None:
        for row in self._rows:
            if str(row.get("id") or "") != str(task_id or ""):
                continue
            row["status"] = "failed"
            current_metadata = row.get("metadata")
            merged_metadata = dict(current_metadata) if isinstance(current_metadata, dict) else {}
            merged_metadata["failure_reason"] = str(reason or "").strip()
            if metadata is not None:
                merged_metadata.update(metadata)
            row["metadata"] = merged_metadata
            return dict(row)
        return None


class _RowWriteOnlyTaskRuntime:
    def __init__(self) -> None:
        self._next_id = 1
        self._rows: list[dict[str, Any]] = []

    def list_observable_task_rows(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self._rows]

    def list_task_rows(self) -> list[dict[str, Any]]:
        raise AssertionError("PM read-model consumers must use list_observable_task_rows()")

    def create_task_row(
        self,
        *,
        subject: str,
        description: str = "",
        blocked_by: list[int] | None = None,
        metadata: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        row = {
            "id": self._next_id,
            "subject": subject,
            "description": description,
            "status": "pending",
            "blocked_by": list(blocked_by or []),
            "metadata": dict(metadata or {}),
        }
        self._next_id += 1
        self._rows.append(row)
        return dict(row)

    def update_task_row(
        self,
        task_id: Any,
        *,
        status: str | None = None,
        blocked_by: list[int] | None = None,
        metadata: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any] | None:
        for row in self._rows:
            if str(row.get("id") or "") != str(task_id or ""):
                continue
            if status is not None:
                row["status"] = status
            if blocked_by is not None:
                row["blocked_by"] = list(blocked_by)
            if metadata is not None:
                current_metadata = row.get("metadata")
                merged_metadata = dict(current_metadata) if isinstance(current_metadata, dict) else {}
                merged_metadata.update(metadata)
                row["metadata"] = merged_metadata
            return dict(row)
        return None

    def get_task(self, task_id: Any) -> dict[str, Any] | None:
        for row in self._rows:
            if str(row.get("id") or "") == str(task_id or ""):
                return dict(row)
        return None

    def task_exists(self, task_id: Any) -> bool:
        return self.get_task(task_id) is not None

    def cancel_task_row_for_deduplication(
        self,
        task_id: Any,
        *,
        primary_task_id: int,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
        source: str = "",
    ) -> dict[str, Any] | None:
        for row in self._rows:
            if str(row.get("id") or "") != str(task_id or ""):
                continue
            row["status"] = "cancelled"
            current_metadata = row.get("metadata")
            merged_metadata = dict(current_metadata) if isinstance(current_metadata, dict) else {}
            merged_metadata.update(dict(metadata or {}))
            merged_metadata["dedup_merged_into"] = int(primary_task_id or 0)
            merged_metadata["dedup_reason"] = str(reason or "").strip()
            merged_metadata["dedup_source"] = str(source or "").strip()
            row["metadata"] = merged_metadata
            return dict(row)
        return None

    def create(self, **_kwargs: Any) -> None:
        raise AssertionError("PM task creation must use create_task_row()")

    def update(self, *_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("PM task updates must use update_task_row()")

    def get(self, *_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("PM task reads must use get_task()")

    def list_all(self) -> list[Any]:
        raise AssertionError("PM read-model consumers must use list_observable_task_rows()")


class _DependencyUpdateMissingTaskRuntime(_RowWriteOnlyTaskRuntime):
    def update_task_row(
        self,
        task_id: Any,
        *,
        status: str | None = None,
        blocked_by: list[int] | None = None,
        metadata: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any] | None:
        if blocked_by is not None:
            return None
        return super().update_task_row(task_id, status=status, blocked_by=blocked_by, metadata=metadata, **_kwargs)


class _ExecutionEventFailureTaskRuntime(_RowWriteOnlyTaskRuntime):
    def __init__(self, *, fail_action: str) -> None:
        super().__init__()
        self.fail_action = fail_action

    def create_task_row(
        self,
        *,
        subject: str,
        description: str = "",
        blocked_by: list[int] | None = None,
        metadata: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        result = self._with_execution_event_failure(
            "create_board_task",
            super().create_task_row(
                subject=subject,
                description=description,
                blocked_by=blocked_by,
                metadata=metadata,
                **_kwargs,
            ),
        )
        if result is None:
            raise AssertionError("create_task_row must return a row projection")
        return result

    def update_task_row(
        self,
        task_id: Any,
        *,
        status: str | None = None,
        blocked_by: list[int] | None = None,
        metadata: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any] | None:
        action = "resolve_dependencies" if blocked_by is not None else "deduplicate_contract_match"
        return self._with_execution_event_failure(
            action,
            super().update_task_row(
                task_id,
                status=status,
                blocked_by=blocked_by,
                metadata=metadata,
                **_kwargs,
            ),
        )

    def _with_execution_event_failure(
        self,
        action: str,
        result: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if action != self.fail_action or result is None:
            return result
        failed_result = dict(result)
        failed_result["execution_event"] = {
            "ok": False,
            "event_type": "task_runtime.execution",
            "error_code": "append_failed",
        }
        return failed_result


class _DedupCancelExecutionEventFailureTaskRuntime(_RowWriteOnlyTaskRuntime):
    def __init__(self, *, missing_row: bool = False, failed_event: bool = True) -> None:
        super().__init__()
        self.missing_row = missing_row
        self.failed_event = failed_event

    def cancel_task_row_for_deduplication(
        self,
        task_id: Any,
        *,
        primary_task_id: int,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
        source: str = "",
    ) -> dict[str, Any] | None:
        result = super().cancel_task_row_for_deduplication(
            task_id,
            primary_task_id=primary_task_id,
            reason=reason,
            metadata=metadata,
            source=source,
        )
        if self.missing_row:
            return None
        if not self.failed_event or result is None:
            return result
        failed_result = dict(result)
        failed_result["execution_event"] = {
            "ok": False,
            "event_type": "task_runtime.execution",
            "error_code": "append_failed",
        }
        return failed_result


# ---------------------------------------------------------------------------
# Message building
# ---------------------------------------------------------------------------




class TestApplyProjectionContractHint:
    def test_no_hint_returns_unchanged(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        contracts = [{"title": "T"}]
        assert adapter._apply_projection_contract_hint(contracts, projection_hint=None) == contracts

    def test_first_task_gets_projection_generate(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        hint = {
            "execution_backend": "projection_generate",
            "projection": {"scenario_id": "s1"},
        }
        contracts = [{"title": "T1"}, {"title": "T2"}]
        result = adapter._apply_projection_contract_hint(contracts, projection_hint=hint)
        assert result[0]["execution_backend"] == "projection_generate"
        assert result[0]["metadata"]["projection"]["scenario_id"] == "s1"
        assert result[1]["execution_backend"] == "code_edit"

    def test_preserves_existing_projection_generate(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        hint = {
            "execution_backend": "projection_generate",
            "projection": {"scenario_id": "s1"},
        }
        contracts = [{"title": "T1", "execution_backend": "projection_generate"}]
        result = adapter._apply_projection_contract_hint(contracts, projection_hint=hint)
        assert result[0]["execution_backend"] == "projection_generate"

    def test_later_task_projection_generate_is_demoted_to_code_edit(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        hint = {
            "execution_backend": "projection_generate",
            "projection": {"scenario_id": "s1"},
        }
        contracts = [
            {"title": "T1", "execution_backend": "projection_generate"},
            {
                "title": "T2",
                "phase": "verification",
                "target_files": ["tests/test_guess_number.py"],
                "metadata": {"execution_backend": "projection_generate"},
            },
        ]
        result = adapter._apply_projection_contract_hint(contracts, projection_hint=hint)
        assert result[0]["execution_backend"] == "projection_generate"
        assert result[1]["execution_backend"] == "code_edit"
        assert result[1]["metadata"]["execution_backend"] == "code_edit"


class TestNormalizeTaskContractProjectionBackend:
    def test_later_raw_task_projection_generate_is_demoted_to_code_edit(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._normalize_task_contract(
            {
                "id": "TASK-3",
                "title": "实现功能验证与 QA 闭环",
                "goal": "创建测试并验证交付结果",
                "phase": "verification",
                "target_files": ["guess_number.py", "tests/test_guess_number.py"],
                "metadata": {"execution_backend": "projection_generate"},
            },
            index=3,
            directive="实现命令行猜数字游戏",
        )
        assert result["metadata"]["execution_backend"] == "code_edit"


class TestBuildProjectionHintContracts:
    def test_returns_three_tasks(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        hint = {"projection": {"scenario_id": "s1", "project_slug": "lab"}}
        result = adapter._build_projection_hint_contracts(directive="req", projection_hint=hint)
        assert len(result) == 3
        assert result[0]["execution_backend"] == "projection_generate"
        assert result[1]["execution_backend"] == "code_edit"
        assert result[2]["execution_backend"] == "code_edit"


class TestSynthesizeTaskContractsFromDirective:
    def test_without_hint_returns_three_tasks(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._synthesize_task_contracts_from_directive(directive="Implement payment module")
        assert len(result) == 3
        assert all(isinstance(c, dict) for c in result)

    def test_with_hint_uses_projection(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        hint = {"projection": {"scenario_id": "s1"}}
        result = adapter._synthesize_task_contracts_from_directive(directive="req", projection_hint=hint)
        assert len(result) == 3
        assert result[0]["metadata"]["execution_backend"] == "projection_generate"


# ---------------------------------------------------------------------------
# Deduplication helpers
# ---------------------------------------------------------------------------


class TestListBoardTaskRows:
    def test_missing_runtime_task_row_projection_returns_empty_rows(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)

        class LegacyBoard:
            def list_all(self) -> list[object]:
                raise AssertionError("PM read-model consumers must not use legacy list_all")

        adapter._task_runtime = cast(Any, LegacyBoard())

        assert adapter._list_board_task_rows() == []

    def test_prefers_runtime_task_row_projection(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        adapter._task_runtime = cast(
            Any,
            _RowProjectionOnlyTaskBoard(
                [{"id": 1, "subject": "Existing", "status": "pending", "metadata": {"goal": "Ship"}}]
            ),
        )

        rows = adapter._list_board_task_rows()

        assert rows == [{"id": 1, "subject": "Existing", "status": "pending", "metadata": {"goal": "Ship"}}]

    def test_pm_stage_prompt_snapshot_uses_runtime_task_rows(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        adapter._task_runtime = cast(
            Any,
            _RowProjectionOnlyTaskBoard(
                [{"id": 1, "subject": "Existing", "status": "pending", "metadata": {"goal": "Ship"}}]
            ),
        )
        captured: dict[str, Any] = {}

        def fake_build_pm_message(
            tasks: list[dict[str, Any]],
            directive: str,
            *,
            projection_hint: dict[str, Any] | None = None,
            directive_analysis: dict[str, Any] | None = None,
        ) -> str:
            captured["tasks"] = tasks
            return "message"

        def fake_synthesize(
            *,
            directive: str,
            projection_hint: dict[str, Any] | None = None,
        ) -> list[dict[str, Any]]:
            return [{"id": "TASK-1", "title": "Bad task", "goal": "Bad task"}]

        def fake_quality(
            contracts: list[dict[str, Any]],
            *,
            directive: str = "",
            context: dict[str, Any] | None = None,
        ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
            return contracts, {
                "ok": False,
                "score": 10,
                "critical_issues": ["missing_target_files"],
                "warnings": [],
                "summary": "blocked",
            }

        adapter._build_pm_message = fake_build_pm_message
        adapter._synthesize_task_contracts_from_directive = fake_synthesize
        adapter._evaluate_contract_quality = fake_quality

        result = asyncio.run(
            adapter._run_pm_stage(
                "pm-task",
                "Create implementation tasks",
                {"deterministic_pm_contracts": True},
                {"deterministic_pm_contracts": True},
            )
        )

        assert captured["tasks"] == [
            {"id": 1, "subject": "Existing", "status": "pending", "metadata": {"goal": "Ship"}}
        ]
        assert result["success"] is False
        assert result["tasks_created"] == 0


class TestCreateBoardTasksRows:
    def test_creates_and_links_tasks_through_runtime_row_api(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        runtime = _RowWriteOnlyTaskRuntime()
        adapter._task_runtime = cast(Any, runtime)

        created = adapter._create_board_tasks(
            [
                {
                    "id": "TASK-1",
                    "title": "Create model",
                    "description": "Create the domain model",
                    "goal": "model",
                    "scope": "src/model.py",
                    "scope_paths": ["src/model.py"],
                    "target_files": ["src/model.py"],
                    "steps": ["write model"],
                    "acceptance": ["model exists"],
                    "depends_on": [],
                },
                {
                    "id": "TASK-2",
                    "title": "Create engine",
                    "description": "Create the engine",
                    "goal": "engine",
                    "scope": "src/engine.py",
                    "scope_paths": ["src/engine.py"],
                    "target_files": ["src/engine.py"],
                    "steps": ["write engine"],
                    "acceptance": ["engine exists"],
                    "depends_on": ["TASK-1"],
                },
            ]
        )

        assert [row["id"] for row in created] == [1, 2]
        assert created[1]["blocked_by"] == [1]
        assert created[1]["metadata"]["resolved_depends_on_task_ids"] == [1]

    def test_dependency_update_failure_is_returned_as_transition_evidence(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        runtime = _DependencyUpdateMissingTaskRuntime()
        adapter._task_runtime = cast(Any, runtime)

        created = adapter._create_board_tasks(
            [
                {
                    "id": "TASK-1",
                    "title": "Create model",
                    "description": "Create the domain model",
                    "goal": "model",
                    "scope": "src/model.py",
                    "scope_paths": ["src/model.py"],
                    "target_files": ["src/model.py"],
                    "steps": ["write model"],
                    "acceptance": ["model exists"],
                    "depends_on": [],
                },
                {
                    "id": "TASK-2",
                    "title": "Create engine",
                    "description": "Create the engine",
                    "goal": "engine",
                    "scope": "src/engine.py",
                    "scope_paths": ["src/engine.py"],
                    "target_files": ["src/engine.py"],
                    "steps": ["write engine"],
                    "acceptance": ["engine exists"],
                    "depends_on": ["TASK-1"],
                },
            ]
        )

        assert created[1]["blocked_by"] == []
        assert "resolved_depends_on_task_ids" not in created[1]["metadata"]
        assert created[1]["task_runtime_transition_failures"] == [
            {
                "success": False,
                "task_id": 2,
                "action": "resolve_dependencies",
                "reason": "task_runtime_dependency_update_missing_row",
                "blocked_by": [1],
                "transition_result": {},
            }
        ]

    def test_create_execution_event_failure_is_returned_as_transition_evidence(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        runtime = _ExecutionEventFailureTaskRuntime(fail_action="create_board_task")
        adapter._task_runtime = cast(Any, runtime)

        created = adapter._create_board_tasks(
            [
                {
                    "id": "TASK-1",
                    "title": "Create model",
                    "description": "Create the domain model",
                    "goal": "model",
                    "scope": "src/model.py",
                    "scope_paths": ["src/model.py"],
                    "target_files": ["src/model.py"],
                    "steps": ["write model"],
                    "acceptance": ["model exists"],
                    "depends_on": [],
                }
            ]
        )

        assert created[0]["task_runtime_transition_failures"] == [
            {
                "success": False,
                "task_id": 1,
                "action": "create_board_task",
                "reason": "task_runtime_execution_event_append_failed",
                "blocked_by": [],
                "transition_result": {
                    "ok": False,
                    "event_type": "task_runtime.execution",
                    "error_code": "append_failed",
                },
            }
        ]

    def test_dedup_match_execution_event_failure_is_returned_as_transition_evidence(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        runtime = _ExecutionEventFailureTaskRuntime(fail_action="deduplicate_contract_match")
        runtime.create_task_row(
            subject="Create model",
            description="Create the domain model",
            metadata={"goal": "model"},
        )
        adapter._task_runtime = cast(Any, runtime)

        created = adapter._create_board_tasks(
            [
                {
                    "id": "TASK-1",
                    "title": "Create model",
                    "description": "Create the domain model",
                    "goal": "model",
                    "scope": "src/model.py",
                    "scope_paths": ["src/model.py"],
                    "target_files": ["src/model.py"],
                    "steps": ["write model"],
                    "acceptance": ["model exists"],
                    "depends_on": [],
                }
            ]
        )

        assert created[0]["id"] == 1
        assert created[0]["task_runtime_transition_failures"][0]["action"] == "deduplicate_contract_match"
        assert created[0]["task_runtime_transition_failures"][0]["reason"] == (
            "task_runtime_execution_event_append_failed"
        )

    def test_dependency_execution_event_failure_is_returned_as_transition_evidence(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        runtime = _ExecutionEventFailureTaskRuntime(fail_action="resolve_dependencies")
        adapter._task_runtime = cast(Any, runtime)

        created = adapter._create_board_tasks(
            [
                {
                    "id": "TASK-1",
                    "title": "Create model",
                    "description": "Create the domain model",
                    "goal": "model",
                    "scope": "src/model.py",
                    "scope_paths": ["src/model.py"],
                    "target_files": ["src/model.py"],
                    "steps": ["write model"],
                    "acceptance": ["model exists"],
                    "depends_on": [],
                },
                {
                    "id": "TASK-2",
                    "title": "Create engine",
                    "description": "Create the engine",
                    "goal": "engine",
                    "scope": "src/engine.py",
                    "scope_paths": ["src/engine.py"],
                    "target_files": ["src/engine.py"],
                    "steps": ["write engine"],
                    "acceptance": ["engine exists"],
                    "depends_on": ["TASK-1"],
                },
            ]
        )

        assert created[1]["blocked_by"] == [1]
        assert created[1]["task_runtime_transition_failures"] == [
            {
                "success": False,
                "task_id": 2,
                "action": "resolve_dependencies",
                "reason": "task_runtime_execution_event_append_failed",
                "blocked_by": [1],
                "transition_result": {
                    "ok": False,
                    "event_type": "task_runtime.execution",
                    "error_code": "append_failed",
                },
            }
        ]

    def test_dedup_cancel_execution_event_failure_attaches_to_primary_row(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        runtime = _DedupCancelExecutionEventFailureTaskRuntime()
        runtime.create_task_row(subject="Create model", description="old", metadata={"goal": "model"})
        runtime.create_task_row(subject="Create model", description="new", metadata={"goal": "model"})
        adapter._task_runtime = cast(Any, runtime)

        created = adapter._create_board_tasks(
            [
                {
                    "id": "TASK-1",
                    "title": "Create model",
                    "description": "Create the domain model",
                    "goal": "model",
                    "scope": "src/model.py",
                    "scope_paths": ["src/model.py"],
                    "target_files": ["src/model.py"],
                    "steps": ["write model"],
                    "acceptance": ["model exists"],
                    "depends_on": [],
                }
            ]
        )

        assert created[0]["id"] == 2
        assert created[0]["task_runtime_transition_failures"] == [
            {
                "success": False,
                "task_id": 1,
                "action": "deduplicate_cancel",
                "reason": "task_runtime_execution_event_append_failed",
                "blocked_by": [],
                "transition_result": {
                    "ok": False,
                    "event_type": "task_runtime.execution",
                    "error_code": "append_failed",
                },
            }
        ]

    def test_dedup_cancel_missing_row_attaches_to_primary_row(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        runtime = _DedupCancelExecutionEventFailureTaskRuntime(missing_row=True)
        runtime.create_task_row(subject="Create model", description="old", metadata={"goal": "model"})
        runtime.create_task_row(subject="Create model", description="new", metadata={"goal": "model"})
        adapter._task_runtime = cast(Any, runtime)

        created = adapter._create_board_tasks(
            [
                {
                    "id": "TASK-1",
                    "title": "Create model",
                    "description": "Create the domain model",
                    "goal": "model",
                    "scope": "src/model.py",
                    "scope_paths": ["src/model.py"],
                    "target_files": ["src/model.py"],
                    "steps": ["write model"],
                    "acceptance": ["model exists"],
                    "depends_on": [],
                }
            ]
        )

        assert created[0]["id"] == 2
        assert created[0]["task_runtime_transition_failures"] == [
            {
                "success": False,
                "task_id": 1,
                "action": "deduplicate_cancel",
                "reason": "task_runtime_dedup_cancel_returned_none",
                "blocked_by": [],
                "transition_result": {},
            }
        ]

    def test_dedup_cancel_success_does_not_attach_transition_failure(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        runtime = _DedupCancelExecutionEventFailureTaskRuntime(failed_event=False)
        runtime.create_task_row(subject="Create model", description="old", metadata={"goal": "model"})
        runtime.create_task_row(subject="Create model", description="new", metadata={"goal": "model"})
        adapter._task_runtime = cast(Any, runtime)

        created = adapter._create_board_tasks(
            [
                {
                    "id": "TASK-1",
                    "title": "Create model",
                    "description": "Create the domain model",
                    "goal": "model",
                    "scope": "src/model.py",
                    "scope_paths": ["src/model.py"],
                    "target_files": ["src/model.py"],
                    "steps": ["write model"],
                    "acceptance": ["model exists"],
                    "depends_on": [],
                }
            ]
        )

        assert created[0]["id"] == 2
        assert "task_runtime_transition_failures" not in created[0]

    def test_pm_stage_surfaces_dedup_cancel_execution_event_failure_signal(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        runtime = _DedupCancelExecutionEventFailureTaskRuntime()
        runtime.create_task_row(subject="Create model", description="old", metadata={"goal": "model"})
        runtime.create_task_row(subject="Create model", description="new", metadata={"goal": "model"})
        adapter._task_runtime = cast(Any, runtime)

        async def fake_call_role_llm(_message: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
            return {
                "response": json.dumps(
                    {
                        "tasks": [
                            {
                                "id": "TASK-1",
                                "title": "Create model",
                                "description": "Create the domain model",
                                "goal": "model",
                                "scope": "src/model.py",
                                "scope_paths": ["src/model.py"],
                                "target_files": ["src/model.py"],
                                "steps": ["write model"],
                                "acceptance": ["model exists"],
                                "depends_on": [],
                            }
                        ]
                    }
                )
            }

        adapter._call_role_llm = fake_call_role_llm

        result = asyncio.run(
            adapter._run_pm_stage(
                "pm-task",
                "Create model",
                {},
                {},
            )
        )

        signals = result["quality_gate"]["signals"]
        assert any(
            item["code"] == "pm.dedup.cancelled_rows" and item["detail"] == "cancelled_rows=1" for item in signals
        )
        failure_signals = [item for item in signals if item["code"] == "pm.dedup.execution_event_failure"]
        assert failure_signals == [
            {
                "code": "pm.dedup.execution_event_failure",
                "severity": "warning",
                "detail": "task_id=1; reason=task_runtime_execution_event_append_failed",
                "task_id": 1,
                "transition_result": {
                    "ok": False,
                    "event_type": "task_runtime.execution",
                    "error_code": "append_failed",
                },
            }
        ]


class TestCanonicalText:
    def test_strips_noise(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._canonical_text("Hello, World!") == "helloworld"
        assert adapter._canonical_text("") == ""


class TestBuildTaskIdentitySignature:
    def test_combines_title_and_goal(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._build_task_identity_signature(title="Fix bug", goal="make it work") == "fixbug::makeitwork"
        assert adapter._build_task_identity_signature(title="", goal="x") == "x"
        assert adapter._build_task_identity_signature(title="x", goal="") == "x"


class TestPickPreferredTaskId:
    def test_prefers_in_progress(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        candidates = [
            {"id": 1, "status": "pending"},
            {"id": 2, "status": "in_progress"},
        ]
        assert adapter._pick_preferred_task_id(candidates) == 2

    def test_prefers_higher_id_on_same_status(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        candidates = [
            {"id": 1, "status": "pending"},
            {"id": 3, "status": "pending"},
        ]
        assert adapter._pick_preferred_task_id(candidates) == 3

    def test_empty_returns_none(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._pick_preferred_task_id([]) is None


class TestFindExistingTaskMatch:
    def test_signature_match(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        sig_index: dict[str, list[dict[str, Any]]] = {"fixbug::makeitwork": [{"id": 7, "status": "pending"}]}
        title_index: dict[str, list[dict[str, Any]]] = {}
        assert (
            adapter._find_existing_task_match(
                subject="Fix bug", goal="make it work", signature_index=sig_index, title_index=title_index
            )
            == 7
        )

    def test_title_match(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        sig_index: dict[str, list[dict[str, Any]]] = {}
        title_index: dict[str, list[dict[str, Any]]] = {"fixbug": [{"id": 8, "status": "pending"}]}
        assert (
            adapter._find_existing_task_match(
                subject="Fix bug", goal="", signature_index=sig_index, title_index=title_index
            )
            == 8
        )

    def test_fuzzy_match_above_threshold(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        sig_index: dict[str, list[dict[str, Any]]] = {}
        title_index: dict[str, list[dict[str, Any]]] = {"fixbug": [{"id": 9, "status": "pending"}]}
        # "fixbugs" vs "fixbug" ratio is ~0.923, below 0.93 threshold
        assert (
            adapter._find_existing_task_match(
                subject="Fix bugs", goal="", signature_index=sig_index, title_index=title_index
            )
            is None
        )

    def test_no_match_returns_none(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._find_existing_task_match(subject="X", goal="Y", signature_index={}, title_index={}) is None


# ---------------------------------------------------------------------------
# Adapter identity
# ---------------------------------------------------------------------------


class TestPmAdapterIdentity:
    def test_role_id(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter.role_id == "pm"

    def test_capabilities(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        caps = adapter.get_capabilities()
        assert "analyze_requirements" in caps
        assert "generate_tasks" in caps
