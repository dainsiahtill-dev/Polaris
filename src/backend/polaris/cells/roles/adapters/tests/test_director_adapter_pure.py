"""Unit tests for DirectorAdapter pure logic (no I/O, no LLM).

Covers:
- _select_execution_strategy
- _apply_intelligent_correction
- _build_director_message
- _build_materialized_metadata
- _resolve_execution_backend_request
- get_capabilities / role_id
"""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from polaris.cells.roles.adapters.internal.director.adapter import DirectorAdapter, _normalize_director_role_response
from polaris.cells.roles.adapters.internal.director.execute_method import (
    _apply_deterministic_missing_declared_target_repair,
    _apply_deterministic_patch_residue_cleanup,
    _apply_deterministic_scaffold_marker_cleanup,
    _apply_deterministic_typescript_reexport_repair,
    _build_existing_workspace_task_evidence,
    _build_substantive_node_test_script,
    _can_accept_existing_workspace_scope,
    _director_direct_text_patch_only_enabled,
    _director_existing_scope_preflight_enabled,
    _emit_director_adapter_cognitive_receipt,
    _finalize_claimed_execution,
    _is_overstrict_node_test_script_contract,
    _looks_like_typescript_reexport_failure,
    _remove_patch_residue_lines,
    _resolve_claim_external_task_id,
    _task_requires_fresh_materialization,
    _task_runtime_finalization_failed_result,
)
from polaris.cells.roles.adapters.internal.director.execution import DirectorPatchExecutor
from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1, RoleExecutionResultV1
from polaris.kernelone.quality import scan_workspace_artifact_quality

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter(tmp_path: Any, task_board: Any = None, task_runtime: Any = None) -> DirectorAdapter:
    """Create a DirectorAdapter with mocked heavy dependencies."""
    if task_board is None and task_runtime is None:
        adapter = DirectorAdapter(workspace=str(tmp_path))
    else:
        adapter = DirectorAdapter(workspace=str(tmp_path), task_board=task_board, task_runtime=task_runtime)
    return adapter


def test_validate_generated_output_allows_todo_status_enum_value(tmp_path: Any) -> None:
    executor = DirectorPatchExecutor(str(tmp_path))
    target = tmp_path / "src" / "models" / "task.model.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "export enum TaskStatus {\n"
        "  Todo = 'TODO',\n"
        "  InProgress = 'IN_PROGRESS',\n"
        "  Done = 'DONE',\n"
        "}\n\n"
        "export interface Task {\n"
        "  id: string;\n"
        "  tenant_id: string;\n"
        "  status: TaskStatus;\n"
        "  version: number;\n"
        "}\n",
        encoding="utf-8",
    )

    error = executor.validate_generated_output(
        {
            "subject": "Task model version status",
            "description": "Implement tenant task status and version model",
        },
        ["src/models/task.model.ts"],
    )

    assert error is None


def test_validate_generated_output_rejects_todo_comment(tmp_path: Any) -> None:
    executor = DirectorPatchExecutor(str(tmp_path))
    target = tmp_path / "src" / "models" / "task.model.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "// TODO implement task versioning\nexport interface Task {\n  tenant_id: string;\n  version: number;\n}\n",
        encoding="utf-8",
    )

    error = executor.validate_generated_output(
        {
            "subject": "Task model version status",
            "description": "Implement tenant task status and version model",
        },
        ["src/models/task.model.ts"],
    )

    assert error is not None
    assert "generic/placeholder content detected" in error


# ---------------------------------------------------------------------------
# Strategy selection
# ---------------------------------------------------------------------------


class TestSelectExecutionStrategy:
    """_select_execution_strategy is a pure function of directive + task + context."""

    def test_architect_concern_triggers_conservative(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        context = {
            "metadata": {
                "architect_constraints": [{"type": "concern", "detail": "risky"}],
            }
        }
        result = adapter._select_execution_strategy("do something", {}, context)
        assert result == "conservative"

    def test_large_scope_and_complex_directive_triggers_incremental(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        directive = "x" * 301
        task = {"target_files": ["a"] * 5, "scope_paths": ["b"] * 6}
        result = adapter._select_execution_strategy(directive, task, {})
        assert result == "incremental"

    def test_refactor_triggers_conservative(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._select_execution_strategy("refactor the module", {}, {})
        assert result == "conservative"

    def test_verify_triggers_focused(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._select_execution_strategy("verify the test suite", {}, {})
        assert result == "focused"

    def test_medium_scope_and_complex_triggers_aggressive(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        directive = "x" * 301
        task = {"target_files": ["a"] * 3, "scope_paths": ["b"] * 3}
        result = adapter._select_execution_strategy(directive, task, {})
        assert result == "aggressive"

    def test_simple_directive_returns_default(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._select_execution_strategy("fix bug", {}, {})
        assert result == "default"

    def test_refactor_zh_triggers_conservative(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._select_execution_strategy("重构代码", {}, {})
        assert result == "conservative"


class TestDirectorAdapterCognitiveRuntimeReceipt:
    """Director materialization must leave Cognitive Runtime evidence."""

    def test_role_runtime_metadata_requires_context_os_and_repo_intelligence(self) -> None:
        metadata = DirectorAdapter._build_role_runtime_metadata(
            {
                "run_id": "run-1",
                "task_id": "TASK-1",
                "metadata": {"source": "caller"},
            },
            max_retries=2,
        )

        assert metadata["role_runtime_required"] is True
        assert metadata["cognitive_runtime_required"] is True
        assert metadata["context_os_expected"] is True
        assert metadata["use_repo_intelligence"] is True
        assert metadata["repo_intel_max_files"] == 20
        assert metadata["repo_intel_max_symbols"] == 40
        assert metadata["run_id"] == "run-1"
        assert metadata["task_id"] == "TASK-1"
        assert metadata["source"] == "caller"
        assert metadata["cognitive_runtime_approval_mode"] == "auto_accept"
        assert metadata["cognitive_runtime_approval"] == {
            "mode": "auto_accept",
            "source": "roles.adapters.director",
            "scope": "director_execution_preflight",
            "approved_by": "director_adapter",
        }

    @pytest.mark.asyncio
    async def test_role_runtime_session_promotes_metadata_tool_receipts(
        self,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import polaris.cells.roles.runtime.public.service as runtime_service_module

        adapter = _make_adapter(tmp_path)
        receipt = {
            "results": [
                {
                    "tool_name": "write_file",
                    "status": "success",
                    "result": {"path": "src/app.ts"},
                }
            ]
        }
        tool_results = [
            {
                "tool": "write_file",
                "tool_name": "write_file",
                "success": True,
                "result": {"path": "src/app.ts"},
            }
        ]

        class FakeRuntimeService:
            async def execute_role_session(self, command: ExecuteRoleSessionCommandV1) -> RoleExecutionResultV1:
                assert command.role == "director"
                assert command.stream is False
                return RoleExecutionResultV1(
                    ok=True,
                    status="ok",
                    role="director",
                    workspace=str(tmp_path),
                    session_id=command.session_id,
                    task_id=command.task_id,
                    run_id=command.run_id,
                    output="done",
                    tool_calls=("write_file",),
                    metadata={
                        "batch_receipt": receipt,
                        "tool_results": tool_results,
                    },
                )

        monkeypatch.setattr(runtime_service_module, "RoleRuntimeService", FakeRuntimeService)

        result = await adapter._invoke_role_runtime_session(
            "write src/app.ts",
            context={"task_id": "TASK-1", "run_id": "RUN-1"},
            max_retries=1,
        )

        assert result["success"] is True
        assert result["batch_receipt"] == receipt
        assert result["tool_results"] == tool_results
        assert result["raw_response"]["batch_receipt"] == receipt

    def test_emit_cognitive_runtime_receipt_records_and_exports_handoff(
        self,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict[str, Any] = {}

        class _Service:
            def record_runtime_receipt(self, command: Any) -> Any:
                captured["receipt_command"] = command
                return SimpleNamespace(ok=True, receipt=SimpleNamespace(receipt_id="receipt-1"))

            def export_handoff_pack(self, command: Any) -> None:
                captured["handoff_command"] = command

            def close(self) -> None:
                captured["closed"] = True

        service = _Service()
        monkeypatch.setattr(
            "polaris.cells.factory.cognitive_runtime.public.service.get_cognitive_runtime_public_service",
            lambda: service,
        )
        adapter = SimpleNamespace(workspace=str(tmp_path))

        receipt = _emit_director_adapter_cognitive_receipt(
            adapter,
            task={"metadata": {"session_id": "session-1"}},
            target_task_id="TASK-1",
            run_id="run-1",
            context={"metadata": {"turn_envelope": {"turn_id": "turn-1"}}},
            receipt_type="director_adapter_materialization_completed",
            payload={"status": "completed", "changed_files": ["src/app.py"]},
            export_handoff=True,
        )

        assert receipt["ok"] is True
        assert receipt["receipt_id"] == "receipt-1"
        receipt_command = captured["receipt_command"]
        assert receipt_command.receipt_type == "director_adapter_materialization_completed"
        assert receipt_command.session_id == "session-1"
        assert receipt_command.run_id == "run-1"
        assert receipt_command.payload["source"] == "roles.adapters.director"
        assert receipt_command.payload["context_os_expected"] is True
        assert receipt_command.payload["changed_files"] == ["src/app.py"]
        handoff_command = captured["handoff_command"]
        assert handoff_command.session_id == "session-1"
        assert handoff_command.turn_envelope["receipt_ids"] == ["receipt-1"]
        assert captured["closed"] is True


# ---------------------------------------------------------------------------
# Intelligent correction
# ---------------------------------------------------------------------------


class TestApplyIntelligentCorrection:
    """_apply_intelligent_correction analyzes failure patterns."""

    def test_success_returns_unchanged(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._apply_intelligent_correction({"success": True}, [])
        assert result["success"] is True
        assert "_correction_hints" not in result

    def test_timeout_pattern_hint(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        previous = [
            {"error": "LLM timeout"},
            {"error": "timeout after 30s"},
        ]
        result = adapter._apply_intelligent_correction({"success": False}, previous)
        assert "_correction_hints" in result
        assert any("smaller steps" in h for h in result["_correction_hints"])

    def test_syntax_error_pattern_hint(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        previous = [
            {"error": "SyntaxError"},
            {"error": "语法错误"},
        ]
        result = adapter._apply_intelligent_correction({"success": False}, previous)
        assert any("syntax" in h.lower() for h in result["_correction_hints"])

    def test_missing_dependency_pattern_hint(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        previous = [
            {"error": "module not found"},
            {"error": "找不到文件"},
        ]
        result = adapter._apply_intelligent_correction({"success": False}, previous)
        assert any("dependencies" in h.lower() for h in result["_correction_hints"])

    def test_permission_pattern_hint(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        previous = [
            {"error": "permission denied"},
            {"error": "权限不足"},
        ]
        result = adapter._apply_intelligent_correction({"success": False}, previous)
        assert any("permissions" in h.lower() for h in result["_correction_hints"])

    def test_single_failure_no_hint(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        previous = [{"error": "timeout"}]
        result = adapter._apply_intelligent_correction({"success": False}, previous)
        assert "_correction_hints" not in result


# ---------------------------------------------------------------------------
# Message building
# ---------------------------------------------------------------------------


class TestBuildDirectorMessage:
    """_build_director_message constructs prompt text deterministically."""

    def test_includes_subject(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = adapter._build_director_message({"subject": "Fix login", "description": "Bug in auth"})
        assert "任务: Fix login" in msg
        assert "文本文件块格式" in msg

    def test_sanitizes_description(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = adapter._build_director_message({"subject": "T", "description": "# Header\n\nBody line"})
        assert "描述:" in msg

    def test_empty_description_omitted(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = adapter._build_director_message({"subject": "T", "description": ""})
        # The line "描述: " with empty content should still appear because implementation
        # does not filter it out; we just assert no crash.
        assert "任务: T" in msg

    def test_uses_real_scope_instead_of_placeholder_path(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = adapter._build_director_message(
            {
                "subject": "Scaffold app",
                "metadata": {
                    "goal": "Create a Vite app",
                    "scope": "package.json, src/main.tsx",
                    "steps": ["Create package manifest"],
                    "acceptance": ["npm test passes"],
                },
            }
        )
        assert "范围: package.json, src/main.tsx" in msg
        assert "- Create package manifest" in msg
        assert "- npm test passes" in msg
        assert "path/to/file.py" not in msg

    def test_includes_pm_contract_paths_checklist_and_acceptance(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = adapter._build_director_message(
            {
                "subject": "Implement Three.js client scene",
                "description": "Implement the client3d task",
                "metadata": {
                    "goal": "Add the missing client3d capability",
                    "scope_paths": ["src/client/three-scene.ts"],
                    "target_files": ["src/client/three-scene.ts"],
                    "execution_checklist": ["Modify the existing Three.js scene file"],
                    "acceptance_criteria": ["Run `npm run build` passes"],
                },
            }
        )

        assert "范围: src/client/three-scene.ts" in msg
        assert "目标文件: src/client/three-scene.ts" in msg
        assert "- Modify the existing Three.js scene file" in msg
        assert "- Run `npm run build` passes" in msg

    def test_includes_qa_rework_evidence(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = adapter._build_director_message(
            {
                "subject": "Fix QA findings",
                "metadata": {
                    "qa_rework_reason": "placeholder_content_detected",
                    "qa_rework_evidence": [
                        "src/backend/fashiongen_worker.py:\\bplaceholder\\b",
                        "src/main/providers.ts:\\bplaceholder\\b",
                    ],
                },
            }
        )

        assert "QA 返工要求" in msg
        assert "placeholder_content_detected" in msg
        assert "src/backend/fashiongen_worker.py" in msg
        assert "src/main/providers.ts" in msg


class TestDirectorFailureClosure:
    @pytest.fixture(autouse=True)
    def _enable_scaffold_synthesis(self, monkeypatch):
        # Synthesis became opt-in (CLAUDE.md §8 fix, 2026-06-12): these tests
        # cover the legacy capability, so they enable it explicitly. Default-
        # off behavior is covered by test_scaffold_synthesis_default_off.
        monkeypatch.setenv("KERNELONE_DIRECTOR_SCAFFOLD_SYNTHESIS", "1")

    """Runtime failures must fail the claimed task instead of leaving it running."""

    def test_finalize_claimed_execution_reports_terminal_transition_failure(self) -> None:
        class _Runtime:
            def complete_execution(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
                del args, kwargs
                raise RuntimeError("Cannot transition task from 'failed' to 'completed'")

        adapter = SimpleNamespace(task_runtime=_Runtime())

        finalize_result = _finalize_claimed_execution(
            adapter,
            target_task_id="task-1",
            session_id="session-1",
            outcome="completed",
            result_summary="done",
            metadata={"adapter_phase": "completed"},
        )
        result = _task_runtime_finalization_failed_result(
            target_task_id="task-1",
            requested_outcome="completed",
            finalize_result=finalize_result,
        )

        assert finalize_result["success"] is False
        assert finalize_result["reason"] == "task_runtime_terminal_transition_failed"
        assert result["success"] is False
        assert result["error_code"] == "director_task_runtime_finalization_failed"
        assert result["root_cause_hint"] == "task_runtime_terminal_transition_failed"

    def test_role_response_normalization_keeps_kernel_errors_failed(self) -> None:
        result = _normalize_director_role_response(
            {
                "response": "[ROLE_EXECUTION_ERROR] provider failed",
                "success": True,
                "provider": "anthropic_compat-test",
                "model": "kimi-for-coding",
            }
        )

        assert result["success"] is False
        assert "provider failed" in result["error"]
        assert result["provider"] == "anthropic_compat-test"
        assert result["model"] == "kimi-for-coding"

    def test_role_response_normalization_preserves_batch_receipt(self) -> None:
        receipt = {
            "results": [
                {
                    "tool_name": "write_file",
                    "status": "success",
                    "result": {"path": "src/app.ts"},
                }
            ]
        }
        result = _normalize_director_role_response(
            {
                "response": "done",
                "provider": "anthropic_compat-test",
                "model": "kimi-for-coding",
                "batch_receipt": receipt,
            }
        )

        assert result["success"] is True
        assert result["batch_receipt"] == receipt

    def test_role_response_normalization_promotes_runtime_metadata_receipts(self) -> None:
        receipt = {
            "results": [
                {
                    "tool_name": "write_file",
                    "status": "success",
                    "result": {"path": "src/app.ts"},
                }
            ]
        }
        tool_results = [
            {
                "tool": "write_file",
                "tool_name": "write_file",
                "success": True,
                "result": {"path": "src/app.ts"},
            }
        ]

        result = _normalize_director_role_response(
            {
                "response": "done",
                "success": True,
                "metadata": {
                    "batch_receipt": receipt,
                    "tool_results": tool_results,
                },
            }
        )

        assert result["success"] is True
        assert result["batch_receipt"] == receipt
        assert result["tool_results"] == tool_results

    def test_direct_text_patch_flag_resolves_from_context(self, tmp_path: Any) -> None:
        del tmp_path
        assert _director_direct_text_patch_only_enabled({"director_direct_text_patch_only": "true"}) is True
        assert _director_direct_text_patch_only_enabled({"director_direct_text_patch_only": "0"}) is False

    def test_existing_scope_preflight_defaults_enabled_and_can_disable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("KERNELONE_DIRECTOR_EXISTING_SCOPE_PREFLIGHT", raising=False)
        assert _director_existing_scope_preflight_enabled({}) is True
        assert _director_existing_scope_preflight_enabled({"director_existing_scope_preflight": "off"}) is False

    @pytest.mark.asyncio
    async def test_role_dialogue_runtime_error_returns_failed_payload(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)

        async def _boom_dialogue(message: str, *, context: dict[str, Any] | None) -> dict[str, Any]:
            del message, context
            raise RuntimeError("kernel contract retry failed")

        adapter._invoke_role_dialogue = _boom_dialogue  # type: ignore[method-assign]

        result = await adapter._invoke_role_dialogue_with_timeout(
            "write files",
            context={},
            timeout_seconds=1.0,
            stage_label="unit",
        )

        assert result["success"] is False
        assert "kernel contract retry failed" in str(result.get("error") or "")

    @pytest.mark.asyncio
    async def test_execute_fails_claimed_task_on_unhandled_runtime_error(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="实现核心模块",
            description="创建文件",
            metadata={"scope": "src/core.ts", "steps": ["写入核心文件"]},
        )

        async def _boom_call(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            raise RuntimeError("director kernel exploded")

        adapter._invoke_role_dialogue_with_timeout = _boom_call  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-fail-closed"},
        )

        assert result["success"] is False
        assert result["error_code"] == "director.runtime.exception"
        updated = adapter.task_board.get_task(str(task.id))
        assert updated is not None
        assert str(updated.get("status") or "").lower() == "failed"

    @pytest.mark.asyncio
    async def test_execute_rejects_workspace_diff_without_write_tool_receipt(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Repair failing TypeScript test",
            description="Apply the smallest code change and verify npm test behavior.",
            metadata={
                "scope": "src/types/domain.ts",
                "steps": ["Update the domain type contract"],
                "acceptance": ["The TypeScript test failure is repaired"],
            },
        )
        captured: dict[str, Any] = {}

        async def _mutating_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args
            captured["context"] = kwargs.get("context")
            target = tmp_path / "src" / "types" / "domain.ts"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("export type DomainState = 'ready';\n", encoding="utf-8")
            return {"content": "Applied directly by runtime provider.", "success": True}

        async def _unexpected_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            raise AssertionError("direct fallback should not run after ambiguous workspace diff evidence")

        adapter._invoke_role_dialogue_with_timeout = _mutating_dialogue  # type: ignore[method-assign]
        adapter._invoke_direct_runtime_provider = _unexpected_direct_fallback  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-diff-evidence"},
        )

        assert result["success"] is False
        assert result["error_code"] == "director_missing_write_receipt"
        assert result["materialization_mode"] == "workspace_diff_without_write_tool"
        assert captured["context"]["run_id"] == "run-director-diff-evidence"
        assert any(
            signal.get("code") == "director_missing_write_receipt"
            for signal in result.get("decision_signals", [])
            if isinstance(signal, dict)
        )
        updated = adapter.task_board.get_task(str(task.id))
        assert updated is not None
        assert str(updated.get("status") or "").lower() == "failed"
        raw_metadata = updated.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        raw_adapter_result = metadata.get("adapter_result")
        adapter_result: dict[str, Any] = raw_adapter_result if isinstance(raw_adapter_result, dict) else {}
        assert adapter_result.get("new_file_count") == 1
        assert adapter_result.get("write_tool_evidence") is False
        assert adapter_result.get("materialization_error") == "director_missing_write_receipt"

    @pytest.mark.asyncio
    async def test_execute_rejects_off_target_workspace_diff_as_materialization(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Implement browser networking client",
            description="Update the declared network client target file.",
            metadata={
                "target_files": ["src/client/network-client.ts"],
                "scope_paths": ["src"],
                "steps": ["Implement src/client/network-client.ts"],
                "acceptance": ["src/client/network-client.ts is changed"],
            },
        )

        async def _off_target_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            off_target = tmp_path / "src" / "server" / "moderation.ts"
            off_target.parent.mkdir(parents=True, exist_ok=True)
            off_target.write_text("export const moderationReady = true;\n", encoding="utf-8")
            return {"content": "Changed a different file.", "success": True}

        async def _empty_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {"content": "", "success": False, "error": "runtime_provider_unavailable"}

        adapter._invoke_role_dialogue_with_timeout = _off_target_dialogue  # type: ignore[method-assign]
        adapter._invoke_direct_runtime_provider = _empty_direct_fallback  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-off-target-diff"},
        )

        assert result["success"] is False
        assert result["error_code"] == "director_no_materialized_changes"
        updated = adapter.task_board.get_task(str(task.id))
        assert updated is not None
        raw_metadata = updated.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        raw_adapter_result = metadata.get("adapter_result")
        adapter_result: dict[str, Any] = raw_adapter_result if isinstance(raw_adapter_result, dict) else {}
        assert adapter_result.get("new_files") == []
        assert adapter_result.get("modified_files") == []

    @pytest.mark.asyncio
    async def test_execute_fails_when_changed_test_file_keeps_placeholder_arithmetic(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        test_file = tmp_path / "tests" / "unit" / "card-rules.test.ts"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text(
            "\n".join(f"test('case {idx}', () => expect({idx} + 1).toBe({idx + 1}));" for idx in range(4)) + "\n",
            encoding="utf-8",
        )
        task = adapter.task_board.create(
            subject="Replace placeholder Card3D unit tests",
            description="Remove trivial arithmetic placeholder tests and replace them with domain assertions.",
            metadata={
                "target_files": ["tests/unit/card-rules.test.ts"],
                "steps": ["Replace or remove existing trivial arithmetic placeholder tests"],
                "acceptance": ["No trivial arithmetic placeholder tests remain"],
            },
        )

        async def _append_only_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            with test_file.open("a", encoding="utf-8") as handle:
                handle.write("test('domain rule', () => expect(resolveCardRule()).toBeDefined());\n")
            return {
                "content": "Appended replacement tests.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "tests/unit/card-rules.test.ts"},
                    }
                ],
            }

        async def _unexpected_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            raise AssertionError("direct fallback should not run after workspace diff evidence")

        adapter._invoke_role_dialogue_with_timeout = _append_only_dialogue  # type: ignore[method-assign]
        adapter._invoke_direct_runtime_provider = _unexpected_direct_fallback  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-artifact-quality"},
        )

        assert result["success"] is False
        assert result["error_code"] == "director_materialization_quality_failed"
        assert any("tests/unit/card-rules.test.ts" in item for item in result["artifact_quality_errors"])
        updated = adapter.task_board.get_task(str(task.id))
        assert updated is not None
        assert str(updated.get("status") or "").lower() == "failed"
        raw_metadata = updated.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        raw_adapter_result = metadata.get("adapter_result")
        adapter_result: dict[str, Any] = raw_adapter_result if isinstance(raw_adapter_result, dict) else {}
        assert adapter_result.get("materialization_error") == "director_materialization_quality_failed"

    @pytest.mark.asyncio
    async def test_execute_repairs_npm_default_failing_test_script_before_failing_quality_gate(
        self,
        tmp_path: Any,
    ) -> None:
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Build web e2e testing workspace",
            description="Create a runnable web e2e workspace with source code and tests.",
            metadata={
                "target_files": [
                    "package.json",
                    "src/index.js",
                    "tests/index.test.js",
                    "scripts/test.mjs",
                ],
                "scope_paths": ["package.json", "src", "tests", "scripts"],
                "steps": ["Create package scripts", "Create source module", "Create executable tests"],
                "acceptance": ["npm test exits 0 and exercises the web e2e source module"],
            },
        )
        stage_labels: list[str] = []

        async def _gemma_like_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args
            stage_labels.append(str(kwargs.get("stage_label") or ""))
            package_json = tmp_path / "package.json"
            package_json.write_text(
                """
{
  "name": "web-e2e-workspace",
  "version": "1.0.0",
  "scripts": {
    "test": "echo \\"Error: no test specified\\" && exit 1",
    "start": "node src/index.js"
  }
}
""".strip()
                + "\n",
                encoding="utf-8",
            )
            if stage_labels[-1] == "quality_repair":
                package_json.write_text(
                    """
{
  "name": "web-e2e-workspace",
  "version": "1.0.0",
  "scripts": {
    "test": "node scripts/test.mjs",
    "start": "node src/index.js"
  }
}
""".strip()
                    + "\n",
                    encoding="utf-8",
                )
                src = tmp_path / "src" / "index.js"
                src.parent.mkdir(parents=True, exist_ok=True)
                src.write_text(
                    "export function createWebE2eStatus() {\n  return { name: 'web-e2e-workspace', ready: true };\n}\n",
                    encoding="utf-8",
                )
                tests = tmp_path / "tests" / "index.test.js"
                tests.parent.mkdir(parents=True, exist_ok=True)
                tests.write_text(
                    "import { createWebE2eStatus } from '../src/index.js';\n"
                    "export function runWebE2eChecks() {\n"
                    "  const status = createWebE2eStatus();\n"
                    "  if (!status.ready) throw new Error('web e2e status not ready');\n"
                    "}\n",
                    encoding="utf-8",
                )
                script = tmp_path / "scripts" / "test.mjs"
                script.parent.mkdir(parents=True, exist_ok=True)
                script.write_text(
                    "import { runWebE2eChecks } from '../tests/index.test.js';\n"
                    "runWebE2eChecks();\n"
                    "console.log('web e2e checks passed');\n",
                    encoding="utf-8",
                )
                changed = [
                    "package.json",
                    "src/index.js",
                    "tests/index.test.js",
                    "scripts/test.mjs",
                ]
            else:
                changed = ["package.json"]
            return {
                "content": "Wrote workspace files.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": path},
                    }
                    for path in changed
                ],
            }

        async def _unexpected_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            raise AssertionError("direct fallback should not run after authoritative write evidence")

        adapter._invoke_role_dialogue_with_timeout = _gemma_like_dialogue  # type: ignore[method-assign]
        adapter._invoke_direct_runtime_provider = _unexpected_direct_fallback  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-package-quality-repair"},
        )

        assert result["success"] is True
        assert stage_labels == ["first_call", "quality_repair"]
        assert result["tools_executed"] >= 5
        assert "package.json" in result["changed_files"]
        assert "Error: no test specified" not in (tmp_path / "package.json").read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_execute_repairs_npm_default_test_script_deterministically(
        self,
        tmp_path: Any,
    ) -> None:
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Create package manifest",
            description="Create a package.json with a runnable local test script.",
            metadata={
                "target_files": ["package.json"],
                "scope_paths": ["package.json"],
                "steps": ["Create package manifest"],
                "acceptance": ["npm test runs a local package manifest check"],
            },
        )
        stage_labels: list[str] = []

        async def _bad_package_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args
            stage_labels.append(str(kwargs.get("stage_label") or ""))
            package_json = tmp_path / "package.json"
            package_json.write_text(
                """
{
  "name": "web-e2e-workspace",
  "version": "1.0.0",
  "scripts": {
    "test": "echo \\"Error: no test specified\\" && exit 0"
  }
}
""".strip()
                + "\n",
                encoding="utf-8",
            )
            return {
                "content": "Wrote package manifest.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "package.json"},
                    }
                ],
            }

        adapter._invoke_role_dialogue_with_timeout = _bad_package_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-package-deterministic-test-script-repair"},
        )

        package_text = (tmp_path / "package.json").read_text(encoding="utf-8")
        test_run = subprocess.run(
            ["npm", "run", "test", "--", "--watch=false"],
            cwd=tmp_path,
            capture_output=True,
            encoding="utf-8",
            check=False,
        )
        assert result["success"] is True
        assert stage_labels == ["first_call"]
        assert "Error: no test specified" not in package_text
        assert "package manifest check passed" in package_text
        assert test_run.returncode == 0
        assert "package manifest check passed" in test_run.stdout
        assert "package.json" in result["changed_files"]

    @pytest.mark.asyncio
    async def test_execute_repairs_typescript_return_object_property_semicolon(
        self,
        tmp_path: Any,
    ) -> None:
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Create task model summary",
            description="Create a task model summary function with valid TypeScript syntax.",
            metadata={
                "target_files": ["src/models/task.ts"],
                "scope_paths": ["src/models/task.ts"],
                "steps": ["Create task model"],
                "acceptance": ["src/models/task.ts typechecks"],
            },
        )

        async def _bad_typescript_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            target = tmp_path / "src" / "models" / "task.ts"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                """
export function summary() {
  const lanes: Record<string, number> = {};
  return {
    total: 1,
    lanes;
  };
}
""".strip()
                + "\n",
                encoding="utf-8",
            )
            return {
                "content": "Wrote task model.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "src/models/task.ts"},
                    }
                ],
            }

        adapter._invoke_role_dialogue_with_timeout = _bad_typescript_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-typescript-return-object-semicolon-repair"},
        )

        repaired = (tmp_path / "src" / "models" / "task.ts").read_text(encoding="utf-8")
        assert result["success"] is True
        assert "    lanes,\n" in repaired
        assert "    lanes;\n" not in repaired
        assert "src/models/task.ts" in result["changed_files"]

    @pytest.mark.asyncio
    async def test_execute_declares_runtime_dependency_when_quality_repair_repeats_undeclared_import(
        self,
        tmp_path: Any,
    ) -> None:
        (tmp_path / "package.json").write_text(
            """
{
  "name": "tenant-workspace",
  "version": "1.0.0",
  "scripts": {
    "test": "node scripts/test.mjs"
  },
  "dependencies": {}
}
""".strip()
            + "\n",
            encoding="utf-8",
        )
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Define tenant model",
            description="Create the tenant model with runtime imports declared in package.json.",
            metadata={
                "target_files": ["src/models/tenant.model.ts"],
                "scope_paths": ["src/models/tenant.model.ts", "package.json"],
                "steps": ["Create tenant model"],
                "acceptance": ["No undeclared runtime imports remain"],
            },
        )
        stage_labels: list[str] = []

        async def _repeating_gemma_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args
            stage_labels.append(str(kwargs.get("stage_label") or ""))
            target = tmp_path / "src" / "models" / "tenant.model.ts"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                "import { Entity, OneToMany, PrimaryColumn } from 'typeorm';\n"
                "@Entity('tenants')\n"
                "export class TenantModel {\n"
                "  @PrimaryColumn()\n"
                "  id: string;\n"
                "\n"
                "  @OneToMany(() => Task, (task) => task.tenant)\n"
                "  tasks: Task[];\n"
                "}\n",
                encoding="utf-8",
            )
            return {
                "content": "Wrote tenant model.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "src/models/tenant.model.ts"},
                    }
                ],
            }

        adapter._invoke_role_dialogue_with_timeout = _repeating_gemma_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-undeclared-import-deterministic-repair"},
        )

        package_text = (tmp_path / "package.json").read_text(encoding="utf-8")
        tenant_text = (tmp_path / "src" / "models" / "tenant.model.ts").read_text(encoding="utf-8")
        assert result["success"] is True
        assert stage_labels == ["first_call"]
        assert '"typeorm":' in package_text
        assert "from 'typeorm'" not in tenant_text
        assert "@Entity" not in tenant_text
        assert "tasks: unknown[] = [];" in tenant_text
        assert "package.json" in result["changed_files"]
        assert "src/models/tenant.model.ts" in result["changed_files"]

    @pytest.mark.asyncio
    async def test_execute_declares_mongoose_runtime_dependency_for_audit_log_model(
        self,
        tmp_path: Any,
    ) -> None:
        (tmp_path / "package.json").write_text(
            """
{
  "name": "tenant-workspace",
  "version": "1.0.0",
  "scripts": {
    "test": "node scripts/test.mjs"
  },
  "dependencies": {}
}
""".strip()
            + "\n",
            encoding="utf-8",
        )
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Tenant Context & Audit Log Middleware",
            description="Implement immutable audit log model with tenant context.",
            metadata={
                "target_files": ["src/models/auditlog.ts"],
                "scope_paths": ["src/models/auditlog.ts", "package.json"],
                "steps": ["Create audit log model"],
                "acceptance": ["No undeclared runtime imports remain"],
            },
        )
        stage_labels: list[str] = []

        async def _mongoose_audit_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args
            stage_labels.append(str(kwargs.get("stage_label") or ""))
            target = tmp_path / "src" / "models" / "auditlog.ts"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                "import { Schema, model, Document } from 'mongoose';\n\n"
                "export interface IAuditLog extends Document {\n"
                "  actor_id: string;\n"
                "  tenant_id: string;\n"
                "  action: 'CREATE' | 'UPDATE' | 'DELETE';\n"
                "  target_entity: string;\n"
                "  delta: Record<string, unknown>;\n"
                "  timestamp: Date;\n"
                "}\n\n"
                "const AuditLogSchema = new Schema<IAuditLog>({\n"
                "  actor_id: { type: String, required: true },\n"
                "  tenant_id: { type: String, required: true },\n"
                "  action: { type: String, enum: ['CREATE', 'UPDATE', 'DELETE'], required: true },\n"
                "  target_entity: { type: String, required: true },\n"
                "  delta: { type: Object, required: true },\n"
                "  timestamp: { type: Date, default: Date.now },\n"
                "});\n\n"
                "export const AuditLog = model<IAuditLog>('AuditLog', AuditLogSchema);\n",
                encoding="utf-8",
            )
            return {
                "content": "Wrote audit log model.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "src/models/auditlog.ts"},
                    }
                ],
            }

        adapter._invoke_role_dialogue_with_timeout = _mongoose_audit_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-mongoose-runtime-dependency-repair"},
        )

        package_text = (tmp_path / "package.json").read_text(encoding="utf-8")
        quality_errors = scan_workspace_artifact_quality(
            str(tmp_path),
            relative_paths=["src/models/auditlog.ts", "package.json"],
        )
        source_tools: list[str] = []
        for item in result["tool_results"]:
            if not isinstance(item, dict):
                continue
            raw_tool_result = item.get("result")
            if isinstance(raw_tool_result, dict):
                source_tools.append(str(raw_tool_result.get("source_tool") or ""))

        assert result["success"] is True
        assert stage_labels == ["first_call"]
        assert quality_errors == []
        assert '"mongoose":' in package_text
        assert "deterministic_runtime_dependency_repair" in source_tools
        assert "package.json" in result["changed_files"]
        assert "src/models/auditlog.ts" in result["changed_files"]

    @pytest.mark.asyncio
    async def test_execute_declares_uuid_and_winston_runtime_dependencies_for_audit_log(
        self,
        tmp_path: Any,
    ) -> None:
        (tmp_path / "package.json").write_text(
            """
{
  "name": "workflow-audit-service",
  "version": "1.0.0",
  "scripts": {
    "test": "node scripts/test.mjs"
  },
  "dependencies": {}
}
""".strip()
            + "\n",
            encoding="utf-8",
        )
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Immutable Audit Logging Implementation",
            description="Create a TypeScript audit log service with stable event IDs and structured logging.",
            metadata={
                "target_files": ["src/services/auditlog.ts"],
                "scope_paths": ["src/services/auditlog.ts", "package.json"],
                "steps": ["Create the audit log service"],
                "acceptance": ["No undeclared runtime imports remain"],
            },
        )
        stage_labels: list[str] = []

        async def _audit_log_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args
            stage_labels.append(str(kwargs.get("stage_label") or ""))
            target = tmp_path / "src" / "services" / "auditlog.ts"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                "import { v4 as uuidv4 } from 'uuid';\n"
                "import winston from 'winston';\n\n"
                "export interface AuditEvent {\n"
                "  id: string;\n"
                "  action: string;\n"
                "  targetId: string;\n"
                "  createdAt: string;\n"
                "}\n\n"
                "const logger = winston.createLogger({\n"
                "  transports: [new winston.transports.Console()],\n"
                "});\n\n"
                "export function recordAuditEvent(action: string, targetId: string): AuditEvent {\n"
                "  const event = { id: uuidv4(), action, targetId, createdAt: new Date().toISOString() };\n"
                "  logger.info('audit.event', event);\n"
                "  return event;\n"
                "}\n",
                encoding="utf-8",
            )
            return {
                "content": "Wrote audit log service.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "src/services/auditlog.ts"},
                    }
                ],
            }

        adapter._invoke_role_dialogue_with_timeout = _audit_log_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-audit-log-runtime-dependency-repair"},
        )

        package_text = (tmp_path / "package.json").read_text(encoding="utf-8")
        quality_errors = scan_workspace_artifact_quality(
            str(tmp_path),
            relative_paths=["src/services/auditlog.ts", "package.json"],
        )
        source_tools: list[str] = []
        for item in result["tool_results"]:
            if not isinstance(item, dict):
                continue
            raw_tool_result = item.get("result")
            if isinstance(raw_tool_result, dict):
                source_tools.append(str(raw_tool_result.get("source_tool") or ""))

        assert result["success"] is True
        assert stage_labels == ["first_call"]
        assert quality_errors == []
        assert '"uuid":' in package_text
        assert '"winston":' in package_text
        assert "deterministic_runtime_dependency_repair" in source_tools
        assert "package.json" in result["changed_files"]
        assert "src/services/auditlog.ts" in result["changed_files"]

    @pytest.mark.asyncio
    async def test_execute_repairs_tenant_middleware_escaped_newline_and_node_types(
        self,
        tmp_path: Any,
    ) -> None:
        (tmp_path / "package.json").write_text(
            """
{
  "name": "tenant-workspace",
  "version": "1.0.0",
  "scripts": {
    "test": "node scripts/test.mjs"
  },
  "dependencies": {
    "express": "^4.18.2"
  }
}
""".strip()
            + "\n",
            encoding="utf-8",
        )
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Tenant Context Middleware",
            description="Create request-scoped tenant context middleware for an Express service.",
            metadata={
                "target_files": ["src/middleware/auth.ts"],
                "scope_paths": ["src/middleware/auth.ts", "package.json"],
                "steps": ["Create tenant middleware"],
                "acceptance": ["TypeScript exports remain reachable and Node builtin typings are declared"],
            },
        )
        stage_labels: list[str] = []

        async def _gemma_escaped_newline_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args
            stage_labels.append(str(kwargs.get("stage_label") or ""))
            target = tmp_path / "src" / "middleware" / "auth.ts"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                "import { Request, Response, NextFunction } from 'express';\n"
                "import { AsyncLocalStorage } from 'async_hooks';\n\n"
                "export interface TenantContext {\n"
                "  tenantId: string;\n"
                "}\n\n"
                "// Context for storing tenant information across the request lifecycle\\n"
                "export const tenantContext = new AsyncLocalStorage<TenantContext>();\n\n"
                "export function tenantMiddleware(req: Request, res: Response, next: NextFunction): void {\n"
                "  const tenantId = String(req.headers['x-tenant-id'] || 'default');\n"
                "  tenantContext.run({ tenantId }, () => next());\n"
                "}\n",
                encoding="utf-8",
            )
            return {
                "content": "Wrote tenant middleware.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "src/middleware/auth.ts"},
                    }
                ],
            }

        adapter._invoke_role_dialogue_with_timeout = _gemma_escaped_newline_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-tenant-middleware-escaped-newline-repair"},
        )

        package_payload = json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))
        repaired = (tmp_path / "src" / "middleware" / "auth.ts").read_text(encoding="utf-8")
        quality_errors = scan_workspace_artifact_quality(
            str(tmp_path),
            relative_paths=["src/middleware/auth.ts", "package.json"],
        )
        source_tools: list[str] = []
        for item in result["tool_results"]:
            if not isinstance(item, dict):
                continue
            raw_tool_result = item.get("result")
            if isinstance(raw_tool_result, dict):
                source_tools.append(str(raw_tool_result.get("source_tool") or ""))

        assert result["success"] is True
        assert stage_labels == ["first_call"]
        assert "lifecycle\\nexport const tenantContext" not in repaired
        assert "\nexport const tenantContext" in repaired
        assert package_payload["devDependencies"]["@types/node"] == "^22.10.0"
        assert quality_errors == []
        assert "deterministic_typescript_escaped_newline_repair" in source_tools
        assert "deterministic_runtime_dependency_repair" in source_tools
        assert "src/middleware/auth.ts" in result["changed_files"]
        assert "package.json" in result["changed_files"]

    @pytest.mark.asyncio
    async def test_execute_repairs_zod_type_class_name_collision(
        self,
        tmp_path: Any,
    ) -> None:
        (tmp_path / "package.json").write_text(
            """
{
  "name": "task-definition-workspace",
  "version": "1.0.0",
  "scripts": {
    "test": "node scripts/test.mjs"
  },
  "dependencies": {
    "zod": "^3.23.8"
  }
}
""".strip()
            + "\n",
            encoding="utf-8",
        )
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Task Definition Model",
            description="Create zod-backed task definition model.",
            metadata={
                "target_files": ["src/models/task_definition.ts"],
                "scope_paths": ["src/models/task_definition.ts", "package.json"],
                "steps": ["Create task definition schema and model"],
                "acceptance": ["TypeScript typecheck accepts schema and class exports"],
            },
        )

        async def _zod_collision_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            target = tmp_path / "src" / "models" / "task_definition.ts"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                "import { z } from 'zod';\n\n"
                "export const TaskDefinitionSchema = z.object({\n"
                "  id: z.string().uuid().optional(),\n"
                "  name: z.string().min(1),\n"
                "});\n\n"
                "type TaskDefinition = z.infer<typeof TaskDefinitionSchema>;\n\n"
                "export class TaskDefinition {\n"
                "  constructor(public data: TaskDefinition) {}\n\n"
                "  static validate(data: any): TaskDefinition {\n"
                "    const result = TaskDefinitionSchema.safeParse(data);\n"
                "    if (!result.success) throw new Error('Validation failed');\n"
                "    return new TaskDefinition(result.data);\n"
                "  }\n"
                "}\n",
                encoding="utf-8",
            )
            return {
                "content": "Wrote task definition model.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "src/models/task_definition.ts"},
                    }
                ],
            }

        adapter._invoke_role_dialogue_with_timeout = _zod_collision_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-zod-type-class-collision-repair"},
        )

        repaired = (tmp_path / "src" / "models" / "task_definition.ts").read_text(encoding="utf-8")
        quality_errors = scan_workspace_artifact_quality(
            str(tmp_path),
            relative_paths=["src/models/task_definition.ts", "package.json"],
        )
        source_tools = [
            str((item.get("result") if isinstance(item, dict) else {}).get("source_tool") or "")
            for item in result["tool_results"]
        ]

        assert result["success"] is True, result
        assert "type TaskDefinitionData = z.infer<typeof TaskDefinitionSchema>;" in repaired
        assert "constructor(public data: TaskDefinitionData)" in repaired
        assert quality_errors == []
        assert "deterministic_typescript_zod_type_class_collision_repair" in source_tools
        assert "src/models/task_definition.ts" in result["changed_files"]

    @pytest.mark.asyncio
    async def test_execute_repairs_framework_coupled_audit_service_contract(
        self,
        tmp_path: Any,
    ) -> None:
        (tmp_path / "package.json").write_text(
            """
{
  "name": "audit-workspace",
  "version": "1.0.0",
  "dependencies": {}
}
""".strip()
            + "\n",
            encoding="utf-8",
        )
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Immutable Audit Log Middleware",
            description="Create audit middleware and service for immutable task change logs.",
            metadata={
                "target_files": [
                    "src/middleware/audit.middleware.ts",
                    "src/services/audit.service.ts",
                ],
                "scope_paths": ["src"],
                "steps": ["Create audit middleware and service"],
                "acceptance": ["No unresolved relative imports or undeclared runtime imports remain"],
            },
        )

        async def _nest_audit_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            service_path = tmp_path / "src" / "services" / "audit.service.ts"
            middleware_path = tmp_path / "src" / "middleware" / "audit.middleware.ts"
            service_path.parent.mkdir(parents=True, exist_ok=True)
            middleware_path.parent.mkdir(parents=True, exist_ok=True)
            service_path.write_text(
                "import { Injectable } from '@nestjs/common';\n"
                "import { InjectRepository } from '@nestjs/typeorm';\n"
                "import { Repository } from 'typeorm';\n"
                "import { AuditLog } from './audit.entity';\n\n"
                "@Injectable()\n"
                "export class AuditService {\n"
                "  constructor(\n"
                "    @InjectRepository(AuditLog)\n"
                "    private auditRepository: Repository<AuditLog>,\n"
                "  ) {}\n\n"
                "  async createLog(action: string, entityType: string, entityId: string, diff: any): Promise<AuditLog> {\n"
                "    const log = this.auditRepository.create({ action, entityType, entityId, diff });\n"
                "    return await this.auditRepository.save(log);\n"
                "  }\n"
                "}\n",
                encoding="utf-8",
            )
            middleware_path.write_text(
                "export class AuditMiddleware {\n  id: string = '';\n}\n",
                encoding="utf-8",
            )
            return {
                "content": "Wrote framework-coupled audit service.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "src/services/audit.service.ts"},
                    },
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "src/middleware/audit.middleware.ts"},
                    },
                ],
            }

        adapter._invoke_role_dialogue_with_timeout = _nest_audit_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-audit-service-contract-repair"},
        )

        service_text = (tmp_path / "src" / "services" / "audit.service.ts").read_text(encoding="utf-8")
        middleware_text = (tmp_path / "src" / "middleware" / "audit.middleware.ts").read_text(encoding="utf-8")
        quality_errors = scan_workspace_artifact_quality(
            str(tmp_path),
            relative_paths=[
                "package.json",
                "src/services/audit.service.ts",
                "src/middleware/audit.middleware.ts",
            ],
        )
        source_tools: list[str] = []
        for item in result["tool_results"]:
            if not isinstance(item, dict):
                continue
            raw_tool_result = item.get("result")
            if isinstance(raw_tool_result, dict):
                source_tools.append(str(raw_tool_result.get("source_tool") or ""))

        assert result["success"] is True
        assert quality_errors == []
        assert "@nestjs" not in service_text
        assert "audit.entity" not in service_text
        assert "AuditLogEntry" in service_text
        assert "AuditService" in middleware_text
        assert "deterministic_audit_service_contract_repair" in source_tools
        assert "src/services/audit.service.ts" in result["changed_files"]
        assert "src/middleware/audit.middleware.ts" in result["changed_files"]

    @pytest.mark.asyncio
    async def test_execute_repairs_framework_coupled_task_service_contract(
        self,
        tmp_path: Any,
    ) -> None:
        (tmp_path / "package.json").write_text(
            """
{
  "name": "task-service-workspace",
  "version": "1.0.0",
  "dependencies": {}
}
""".strip()
            + "\n",
            encoding="utf-8",
        )
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="基础任务CRUD与Dry-run接口开发",
            description="Create task CRUD service and dry-run task controller.",
            metadata={
                "target_files": [
                    "src/server/task.controller.ts",
                    "src/services/task.service.ts",
                ],
                "scope_paths": ["src"],
                "steps": ["Create task service and controller"],
                "acceptance": ["No undeclared runtime imports remain"],
            },
        )

        async def _nest_task_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            service_path = tmp_path / "src" / "services" / "task.service.ts"
            controller_path = tmp_path / "src" / "server" / "task.controller.ts"
            service_path.parent.mkdir(parents=True, exist_ok=True)
            controller_path.parent.mkdir(parents=True, exist_ok=True)
            service_path.write_text(
                "import { Injectable, NotFoundException } from '@nestjs/common';\n\n"
                "export interface Task {\n"
                "  id?: string;\n"
                "  name: string = '';\n"
                "  status: unknown = null;\n"
                "}\n\n"
                "export class TaskService {\n"
                "  private tasks: Map<string, Task> = new Map();\n"
                "  async getTask(id: string): Promise<Task> {\n"
                "    const task = this.tasks.get(id);\n"
                "    if (!task) throw new NotFoundException(`Task with ID ${id} not found`);\n"
                "    return task;\n"
                "  }\n"
                "}\n",
                encoding="utf-8",
            )
            controller_path.write_text(
                "export class TaskController {\n  id: string = '';\n}\n",
                encoding="utf-8",
            )
            return {
                "content": "Wrote framework-coupled task service.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "src/services/task.service.ts"},
                    },
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "src/server/task.controller.ts"},
                    },
                ],
            }

        adapter._invoke_role_dialogue_with_timeout = _nest_task_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-task-service-contract-repair"},
        )

        service_text = (tmp_path / "src" / "services" / "task.service.ts").read_text(encoding="utf-8")
        controller_text = (tmp_path / "src" / "server" / "task.controller.ts").read_text(encoding="utf-8")
        quality_errors = scan_workspace_artifact_quality(
            str(tmp_path),
            relative_paths=[
                "package.json",
                "src/services/task.service.ts",
                "src/server/task.controller.ts",
            ],
        )
        source_tools: list[str] = []
        for item in result["tool_results"]:
            if not isinstance(item, dict):
                continue
            raw_tool_result = item.get("result")
            if isinstance(raw_tool_result, dict):
                source_tools.append(str(raw_tool_result.get("source_tool") or ""))

        assert result["success"] is True
        assert quality_errors == []
        assert "@nestjs" not in service_text
        assert "NotFoundException" not in service_text
        assert "name: string =" not in service_text
        assert "TaskRecord" in service_text
        assert "TaskService" in controller_text
        assert "deterministic_task_service_contract_repair" in source_tools
        assert "src/services/task.service.ts" in result["changed_files"]
        assert "src/server/task.controller.ts" in result["changed_files"]

    @pytest.mark.asyncio
    async def test_execute_synthesizes_node_test_file_when_test_runner_has_no_tests(
        self,
        tmp_path: Any,
    ) -> None:
        (tmp_path / "package.json").write_text(
            """
{
  "name": "typescript-project",
  "version": "1.0.0",
  "scripts": {
    "test": "vitest run"
  },
  "devDependencies": {
    "typescript": "^5.0.0",
    "vitest": "^1.6.1"
  }
}
""".strip()
            + "\n",
            encoding="utf-8",
        )
        (tmp_path / "tsconfig.json").write_text(
            '{"compilerOptions":{"module":"NodeNext","moduleResolution":"NodeNext","target":"ES2022","strict":true},'
            '"include":["src/**/*.ts","tests/**/*.ts"]}\n',
            encoding="utf-8",
        )
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Task Model and Graph Validation Implementation",
            description="Implement task model and graph validation for a TypeScript project.",
            metadata={
                "target_files": ["src/models/task.ts", "src/services/taskgraph.ts"],
                "scope_paths": ["src"],
                "steps": ["Create task model and graph validation service"],
                "acceptance": ["npm run test verifies graph validation"],
            },
        )

        async def _taskgraph_without_tests_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            model_path = tmp_path / "src" / "models" / "task.ts"
            graph_path = tmp_path / "src" / "services" / "taskgraph.ts"
            model_path.parent.mkdir(parents=True, exist_ok=True)
            graph_path.parent.mkdir(parents=True, exist_ok=True)
            model_path.write_text(
                "export interface TaskRecord {\n  id: string;\n  dependencies: string[];\n}\n",
                encoding="utf-8",
            )
            graph_path.write_text(
                "export interface TaskDependencyNode {\n"
                "  id: string;\n"
                "  dependencies: readonly string[];\n"
                "}\n\n"
                "export interface DagValidationResult {\n"
                "  valid: boolean;\n"
                "  errors: string[];\n"
                "}\n\n"
                "export class TaskGraph {\n"
                "  validate(nodes: readonly TaskDependencyNode[]): DagValidationResult {\n"
                "    const ids = new Set(nodes.map((node) => node.id));\n"
                "    const errors: string[] = [];\n"
                "    for (const node of nodes) {\n"
                "      for (const dependency of node.dependencies) {\n"
                "        if (!ids.has(dependency)) errors.push(`Missing dependency ${dependency}`);\n"
                "      }\n"
                "    }\n"
                "    const visiting = new Set<string>();\n"
                "    const visited = new Set<string>();\n"
                "    const byId = new Map(nodes.map((node) => [node.id, node]));\n"
                "    const visit = (id: string): boolean => {\n"
                "      if (visiting.has(id)) return true;\n"
                "      if (visited.has(id)) return false;\n"
                "      visiting.add(id);\n"
                "      for (const dependency of byId.get(id)?.dependencies ?? []) {\n"
                "        if (visit(dependency)) return true;\n"
                "      }\n"
                "      visiting.delete(id);\n"
                "      visited.add(id);\n"
                "      return false;\n"
                "    };\n"
                "    for (const node of nodes) {\n"
                "      if (visit(node.id)) errors.push('Circular dependency detected');\n"
                "    }\n"
                "    return { valid: errors.length === 0, errors };\n"
                "  }\n"
                "}\n",
                encoding="utf-8",
            )
            return {
                "content": "Wrote task model and graph.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "src/models/task.ts"},
                    },
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "src/services/taskgraph.ts"},
                    },
                ],
            }

        adapter._invoke_role_dialogue_with_timeout = _taskgraph_without_tests_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-node-test-file-repair"},
        )

        test_path = tmp_path / "tests" / "unit" / "taskgraph.test.ts"
        quality_errors = scan_workspace_artifact_quality(str(tmp_path))
        source_tools: list[str] = []
        assert result["success"] is True, result
        for item in result["tool_results"]:
            if not isinstance(item, dict):
                continue
            raw_tool_result = item.get("result")
            if isinstance(raw_tool_result, dict):
                source_tools.append(str(raw_tool_result.get("source_tool") or ""))

        assert test_path.is_file()
        assert "TaskGraph" in test_path.read_text(encoding="utf-8")
        assert quality_errors == []
        assert "deterministic_node_test_file_repair" in source_tools
        assert "tests/unit/taskgraph.test.ts" in result["changed_files"]

    @pytest.mark.asyncio
    async def test_execute_synthesizes_jest_compatible_test_file_when_jest_runner_has_no_tests(
        self,
        tmp_path: Any,
    ) -> None:
        (tmp_path / "package.json").write_text(
            """
{
  "name": "polaris-engine",
  "version": "1.0.0",
  "scripts": {
    "test": "jest"
  },
  "devDependencies": {
    "jest": "^29.7.0",
    "typescript": "^5.0.0"
  }
}
""".strip()
            + "\n",
            encoding="utf-8",
        )
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Task Graph Validation Logic",
            description="Implement task graph validation for a Jest-based TypeScript project.",
            metadata={
                "target_files": ["src/services/taskgraph.ts"],
                "scope_paths": ["src"],
                "steps": ["Create task graph validation"],
                "acceptance": ["npm run test verifies graph validation"],
            },
        )

        async def _taskgraph_without_tests_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            graph_path = tmp_path / "src" / "services" / "taskgraph.ts"
            graph_path.parent.mkdir(parents=True, exist_ok=True)
            graph_path.write_text(
                "export class TaskGraph {\n"
                "  validate(tasks: Array<{ id: string; dependencies?: string[] }>): { valid: boolean } {\n"
                "    return { valid: tasks.length > 0 };\n"
                "  }\n"
                "}\n",
                encoding="utf-8",
            )
            return {
                "content": "Wrote task graph.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "src/services/taskgraph.ts"},
                    }
                ],
            }

        adapter._invoke_role_dialogue_with_timeout = _taskgraph_without_tests_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-jest-node-test-file-repair"},
        )

        test_text = (tmp_path / "tests" / "unit" / "taskgraph.test.ts").read_text(encoding="utf-8")
        assert result["success"] is True, result
        assert "from 'vitest'" not in test_text
        assert "describe('TaskGraph'" in test_text
        assert "deterministic_node_test_file_repair" in [
            str((item.get("result") if isinstance(item, dict) else {}).get("source_tool") or "")
            for item in result["tool_results"]
        ]

    @pytest.mark.asyncio
    async def test_execute_synthesizes_declared_targets_when_llm_returns_no_write_tool(
        self,
        tmp_path: Any,
    ) -> None:
        (tmp_path / "package.json").write_text(
            """
{
  "name": "polaris-engine",
  "version": "1.0.0",
  "scripts": {
    "test": "node scripts/test.mjs"
  }
}
""".strip()
            + "\n",
            encoding="utf-8",
        )
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="搭建基础目录结构与模型定义",
            description="Create base model and repository contracts.",
            metadata={
                "phase": "implementation",
                "scope_paths": ["src"],
                "target_files": [
                    "src/models/base.model.ts",
                    "src/repositories/base.repository.ts",
                ],
                "steps": ["Create base model and repository"],
                "acceptance": ["verify src/models/base.model.ts exists"],
            },
        )

        async def _no_write_contract_violation(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {
                "content": "",
                "success": False,
                "error": (
                    "TransactionKernel execution failed: single_batch_contract_violation: "
                    "mutation requested but no write tool invocation in decision batch."
                ),
            }

        async def _empty_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {"content": "", "success": False, "error": "runtime_provider_unavailable"}

        adapter._invoke_role_dialogue_with_timeout = _no_write_contract_violation  # type: ignore[method-assign]
        adapter._invoke_direct_runtime_provider = _empty_direct_fallback  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-no-write-declared-target-repair"},
        )

        model_text = (tmp_path / "src" / "models" / "base.model.ts").read_text(encoding="utf-8")
        repository_text = (tmp_path / "src" / "repositories" / "base.repository.ts").read_text(encoding="utf-8")
        quality_errors = scan_workspace_artifact_quality(
            str(tmp_path),
            relative_paths=["src/models/base.model.ts", "src/repositories/base.repository.ts"],
        )
        source_tools = [
            str((item.get("result") if isinstance(item, dict) else {}).get("source_tool") or "")
            for item in result["tool_results"]
        ]

        assert result["success"] is True, result
        assert "BaseModel" in model_text
        assert "BaseRepository" in repository_text
        assert quality_errors == []
        assert "deterministic_missing_declared_target_repair" in source_tools
        assert "src/models/base.model.ts" in result["changed_files"]
        assert "src/repositories/base.repository.ts" in result["changed_files"]

    def test_missing_declared_target_repair_synthesizes_taskgraph_contract_targets(
        self,
        tmp_path: Any,
    ) -> None:
        adapter = _make_adapter(tmp_path)
        task = {
            "metadata": {
                "target_files": [
                    "src/services/taskgraph.ts",
                    "tests/unit/taskgraph.test.ts",
                ],
            }
        }

        results = _apply_deterministic_missing_declared_target_repair(
            adapter,
            task=task,
            task_id="taskgraph-target-repair",
            artifact_quality_errors=[
                "Artifact quality scan failed: declared target file missing 'src/services/taskgraph.ts'",
                "Artifact quality scan failed: declared target file missing 'tests/unit/taskgraph.test.ts'",
            ],
        )

        graph_text = (tmp_path / "src" / "services" / "taskgraph.ts").read_text(encoding="utf-8")
        test_text = (tmp_path / "tests" / "unit" / "taskgraph.test.ts").read_text(encoding="utf-8")
        quality_errors = scan_workspace_artifact_quality(
            str(tmp_path),
            relative_paths=["src/services/taskgraph.ts", "tests/unit/taskgraph.test.ts"],
        )

        assert len(results) == 2
        assert quality_errors == []
        assert "class TaskGraph" in graph_text
        assert "Circular dependency detected" in graph_text
        assert "new TaskGraph" in test_text

    @pytest.mark.asyncio
    async def test_execute_normalizes_declared_task_model_before_qa_typecheck(
        self,
        tmp_path: Any,
    ) -> None:
        (tmp_path / "package.json").write_text(
            """
{
  "name": "tenant-workspace",
  "version": "1.0.0",
  "scripts": {
    "test": "node scripts/test.mjs"
  },
  "dependencies": {
    "typeorm": "^0.3.20"
  }
}
""".strip()
            + "\n",
            encoding="utf-8",
        )
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Define task model",
            description="Create a strict TypeScript task model used by task services and DAG validation.",
            metadata={
                "target_files": ["src/models/task.model.ts"],
                "scope_paths": ["src/models"],
                "steps": ["Create the task model"],
                "acceptance": ["The model compiles under strict TypeScript and exposes dependency IDs"],
            },
        )

        async def _bad_task_model_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            target = tmp_path / "src" / "models" / "task.model.ts"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                "import { Entity, Column, PrimaryGeneratedColumn, CreateDateColumn, UpdateDateColumn } "
                "from 'typeorm';\n\n"
                "export enum TaskStatus {\n"
                "  PENDING = 'pending',\n"
                "  IN_PROGRESS = 'in_progress',\n"
                "  COMPLETED = 'completed',\n"
                "  CANCELLED = 'cancelled',\n"
                "}\n\n"
                "@Entity('tasks')\n"
                "export class Task {\n"
                "  @PrimaryGeneratedColumn('uuid')\n"
                "  id: string;\n\n"
                "  @Column()\n"
                "  title: string;\n\n"
                "  @Column({ type: 'text', nullable: true })\n"
                "  description: string;\n\n"
                "  @Column({ type: 'enum', enum: TaskStatus, default: TaskStatus.PENDING })\n"
                "  status: TaskStatus;\n\n"
                "  @Column({ default: 0 })\n"
                "  priority: number;\n\n"
                "  @Column({ nullable: true })\n"
                "  tenantId: string;\n\n"
                "  @CreateDateColumn()\n"
                "  createdAt: Date;\n\n"
                "  @UpdateDateColumn()\n"
                "  updatedAt: Date;\n"
                "}\n",
                encoding="utf-8",
            )
            return {
                "content": "Wrote task model.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "src/models/task.model.ts"},
                    }
                ],
            }

        adapter._invoke_role_dialogue_with_timeout = _bad_task_model_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-task-model-contract-repair"},
        )

        task_model_text = (tmp_path / "src" / "models" / "task.model.ts").read_text(encoding="utf-8")
        source_tools: list[str] = []
        for item in result["tool_results"]:
            if not isinstance(item, dict):
                continue
            raw_tool_result = item.get("result")
            if isinstance(raw_tool_result, dict):
                source_tools.append(str(raw_tool_result.get("source_tool") or ""))
        assert result["success"] is True
        assert "from 'typeorm'" not in task_model_text
        assert 'id: string = "";' in task_model_text
        assert "dependencies: string[] = [];" in task_model_text
        assert "predecessorIds: string[] = [];" in task_model_text
        assert "deterministic_task_model_contract_repair" in source_tools
        assert "src/models/task.model.ts" in result["changed_files"]

    @pytest.mark.asyncio
    async def test_execute_normalizes_declared_tenant_model_self_import_before_qa_typecheck(
        self,
        tmp_path: Any,
    ) -> None:
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Define tenant model",
            description="Create a strict TypeScript tenant model for multi-tenant task isolation.",
            metadata={
                "target_files": ["src/models/tenant.model.ts"],
                "scope_paths": ["src/models"],
                "steps": ["Create the tenant model"],
                "acceptance": ["The tenant model compiles under strict TypeScript"],
            },
        )

        async def _bad_tenant_model_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            target = tmp_path / "src" / "models" / "tenant.model.ts"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                "import { Tenant } from './tenant.model';\n\n"
                "export class Tenant {\n"
                '    id: string = "";\n'
                '    name: string = "";\n'
                '    description: string = "";\n'
                "    isActive: boolean = false;\n"
                "    tasks: unknown[] = [];\n"
                "    auditLogs: unknown[] = [];\n"
                "}\n",
                encoding="utf-8",
            )
            return {
                "content": "Wrote tenant model.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "src/models/tenant.model.ts"},
                    }
                ],
            }

        adapter._invoke_role_dialogue_with_timeout = _bad_tenant_model_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-tenant-model-contract-repair"},
        )

        tenant_model_text = (tmp_path / "src" / "models" / "tenant.model.ts").read_text(encoding="utf-8")
        source_tools: list[str] = []
        for item in result["tool_results"]:
            if not isinstance(item, dict):
                continue
            raw_tool_result = item.get("result")
            if isinstance(raw_tool_result, dict):
                source_tools.append(str(raw_tool_result.get("source_tool") or ""))
        assert result["success"] is True
        assert "from './tenant.model'" not in tenant_model_text
        assert "export class Tenant" in tenant_model_text
        assert "taskIds: string[] = [];" in tenant_model_text
        assert "auditLogIds: string[] = [];" in tenant_model_text
        assert "deterministic_tenant_model_contract_repair" in source_tools
        assert "src/models/tenant.model.ts" in result["changed_files"]

    @pytest.mark.asyncio
    async def test_execute_repairs_framework_coupled_dag_service_after_llm_quality_repair(
        self,
        tmp_path: Any,
    ) -> None:
        (tmp_path / "package.json").write_text(
            """
{
  "name": "tenant-workspace",
  "version": "1.0.0",
  "scripts": {
    "test": "node scripts/test.mjs"
  },
  "dependencies": {}
}
""".strip()
            + "\n",
            encoding="utf-8",
        )
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="DAG Validation Engine",
            description="Implement cycle detection and orphan predecessor validation for task dependency chains.",
            metadata={
                "target_files": ["src/services/dag.service.ts", "src/services/task.service.ts"],
                "scope_paths": ["src/services"],
                "steps": ["Implement the DAG service", "Implement task creation validation"],
                "acceptance": [
                    "Create a task with a circular dependency returns 400 error",
                    "Identify and report missing predecessor IDs as errors",
                    "Unit tests cover simple chain, branching, cycle detection, and orphan nodes",
                ],
            },
        )
        stage_labels: list[str] = []

        async def _gemma_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args
            stage_label = str(kwargs.get("stage_label") or "")
            stage_labels.append(stage_label)
            dag_target = tmp_path / "src" / "services" / "dag.service.ts"
            task_target = tmp_path / "src" / "services" / "task.service.ts"
            dag_target.parent.mkdir(parents=True, exist_ok=True)
            if stage_label == "first_call":
                dag_target.write_text(
                    "export const dagService = 'structural build passed';\n",
                    encoding="utf-8",
                )
                task_target.write_text(
                    "export class TaskService {\n  createTask(): string {\n    return 'pending';\n  }\n}\n",
                    encoding="utf-8",
                )
            else:
                dag_target.write_text(
                    "import { Injectable, BadRequestException } from '@nestjs/common';\n\n"
                    "@Injectable()\n"
                    "export class DagService {\n"
                    "  validateDag(): void {\n"
                    "    throw new BadRequestException('Circular dependency detected');\n"
                    "  }\n"
                    "}\n",
                    encoding="utf-8",
                )
                task_target.write_text(
                    "import { Injectable } from '@nestjs/common';\n"
                    "import { DagService } from './dag.service';\n"
                    "import { CreateTaskDto } from '../dto/create-task.dto';\n\n"
                    "@Injectable()\n"
                    "export class TaskService {\n"
                    "  constructor(private readonly dagService: DagService) {}\n"
                    "  createTask(dto: CreateTaskDto): void {\n"
                    "    this.dagService.validateDag();\n"
                    "  }\n"
                    "}\n",
                    encoding="utf-8",
                )
            return {
                "content": "Wrote service files.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "src/services/dag.service.ts"},
                    },
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "src/services/task.service.ts"},
                    },
                ],
            }

        adapter._invoke_role_dialogue_with_timeout = _gemma_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-framework-free-dag-repair"},
        )

        dag_text = (tmp_path / "src" / "services" / "dag.service.ts").read_text(encoding="utf-8")
        task_text = (tmp_path / "src" / "services" / "task.service.ts").read_text(encoding="utf-8")
        quality_errors = scan_workspace_artifact_quality(
            str(tmp_path),
            relative_paths=["src/services/dag.service.ts", "src/services/task.service.ts"],
        )
        source_tools: list[str] = []
        for item in result["tool_results"]:
            if not isinstance(item, dict):
                continue
            raw_tool_result = item.get("result")
            if isinstance(raw_tool_result, dict):
                source_tools.append(str(raw_tool_result.get("source_tool") or ""))
        assert result["success"] is True
        assert stage_labels == ["first_call", "quality_repair"]
        assert quality_errors == []
        assert "@nestjs/common" not in dag_text
        assert "@nestjs/common" not in task_text
        assert "CreateTaskDto" not in task_text
        assert "DagValidationError" in dag_text
        assert "statusCode = 400" in dag_text
        assert "predecessorIds" in task_text
        assert "deterministic_framework_free_service_repair" in source_tools

    @pytest.mark.asyncio
    async def test_execute_repairs_framework_coupled_dag_service_with_only_dag_target(
        self,
        tmp_path: Any,
    ) -> None:
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="DAG Dependency Validation Engine",
            description="Implement cycle detection and missing reference checks for task dependencies.",
            metadata={
                "target_files": ["src/services/dag.service.ts"],
                "scope_paths": ["src/services"],
                "steps": ["Implement the DAG service"],
                "acceptance": [
                    "Cycle detection returns 400 with descriptive error for circular refs",
                    "Missing reference check identifies undefined task IDs in a graph",
                    "Validation runs before any task persistence or execution",
                ],
            },
        )
        stage_labels: list[str] = []

        async def _gemma_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args
            stage_label = str(kwargs.get("stage_label") or "")
            stage_labels.append(stage_label)
            dag_target = tmp_path / "src" / "services" / "dag.service.ts"
            dag_target.parent.mkdir(parents=True, exist_ok=True)
            dag_target.write_text(
                "import { Injectable, BadRequestException } from '@nestjs/common';\n\n"
                "@Injectable()\n"
                "export class DagService {\n"
                "  validateDag(): void {\n"
                "    throw new BadRequestException('Circular dependency detected');\n"
                "  }\n"
                "}\n",
                encoding="utf-8",
            )
            return {
                "content": "Wrote dag service.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "src/services/dag.service.ts"},
                    }
                ],
            }

        adapter._invoke_role_dialogue_with_timeout = _gemma_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-framework-free-dag-only-repair"},
        )

        dag_text = (tmp_path / "src" / "services" / "dag.service.ts").read_text(encoding="utf-8")
        quality_errors = scan_workspace_artifact_quality(
            str(tmp_path),
            relative_paths=["src/services/dag.service.ts"],
        )
        source_tools: list[str] = []
        for item in result["tool_results"]:
            if not isinstance(item, dict):
                continue
            raw_tool_result = item.get("result")
            if isinstance(raw_tool_result, dict):
                source_tools.append(str(raw_tool_result.get("source_tool") or ""))

        assert result["success"] is True
        assert stage_labels == ["first_call"]
        assert quality_errors == []
        assert "@nestjs/common" not in dag_text
        assert "DagValidationError" in dag_text
        assert "statusCode = 400" in dag_text
        assert "deterministic_framework_free_service_repair" in source_tools

    @pytest.mark.asyncio
    async def test_execute_repairs_missing_declared_target_from_nearby_existing_module(
        self,
        tmp_path: Any,
    ) -> None:
        existing_task = tmp_path / "src" / "models" / "task.ts"
        existing_task.parent.mkdir(parents=True, exist_ok=True)
        existing_task.write_text(
            "export interface TaskModel {\n  id: string;\n  tenantId: string;\n  title: string;\n}\n",
            encoding="utf-8",
        )
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Define tenant and task model files",
            description="Create explicit tenant.model.ts and task.model.ts model files.",
            metadata={
                "target_files": ["src/models/tenant.model.ts", "src/models/task.model.ts"],
                "scope_paths": ["src/models"],
                "steps": ["Create tenant and task model files"],
                "acceptance": ["Both declared target model files exist"],
            },
        )

        async def _tenant_only_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            target = tmp_path / "src" / "models" / "tenant.model.ts"
            target.write_text(
                "export interface TenantModel {\n  id: string;\n  name: string;\n}\n",
                encoding="utf-8",
            )
            return {
                "content": "Wrote tenant model.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "src/models/tenant.model.ts"},
                    }
                ],
            }

        adapter._invoke_role_dialogue_with_timeout = _tenant_only_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-missing-target-nearby-repair"},
        )

        repaired_task = tmp_path / "src" / "models" / "task.model.ts"
        assert result["success"] is True
        assert repaired_task.read_text(encoding="utf-8") == existing_task.read_text(encoding="utf-8")
        assert "src/models/task.model.ts" in result["changed_files"]

    @pytest.mark.asyncio
    async def test_execute_does_not_copy_low_quality_nearby_declared_target_source(
        self,
        tmp_path: Any,
    ) -> None:
        existing_task = tmp_path / "src" / "models" / "task.ts"
        existing_task.parent.mkdir(parents=True, exist_ok=True)
        existing_task.write_text(
            "export const taskScenario = { tags: ['audit-seed'] };\n",
            encoding="utf-8",
        )
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Define tenant and task model files",
            description="Create explicit tenant.model.ts and task.model.ts model files.",
            metadata={
                "target_files": ["src/models/tenant.model.ts", "src/models/task.model.ts"],
                "scope_paths": ["src/models"],
                "steps": ["Create tenant and task model files"],
                "acceptance": ["Both declared target model files exist"],
            },
        )

        async def _tenant_only_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            target = tmp_path / "src" / "models" / "tenant.model.ts"
            target.write_text(
                "export interface TenantModel {\n  id: string;\n  tenantId: string;\n}\n",
                encoding="utf-8",
            )
            return {
                "content": "Wrote tenant model.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "src/models/tenant.model.ts"},
                    }
                ],
            }

        adapter._invoke_role_dialogue_with_timeout = _tenant_only_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-missing-target-dirty-source-repair"},
        )

        repaired_text = (tmp_path / "src" / "models" / "task.model.ts").read_text(encoding="utf-8")
        assert result["success"] is True
        assert "audit-seed" not in repaired_text
        assert "export class Task" in repaired_text
        assert "dependencies: string[] = [];" in repaired_text
        assert "src/models/task.model.ts" in result["changed_files"]

    @pytest.mark.asyncio
    async def test_execute_synthesizes_missing_declared_targets_without_nearby_source(
        self,
        tmp_path: Any,
    ) -> None:
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Design tenant user permission model files",
            description="Create explicit tenant, user, and permission model files.",
            metadata={
                "target_files": [
                    "src/models/tenant.ts",
                    "src/models/user.ts",
                    "src/models/permission.ts",
                ],
                "scope_paths": ["src/models"],
                "steps": ["Create tenant, user, and permission model files"],
                "acceptance": ["All declared model files exist"],
            },
        )

        async def _tenant_only_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            target = tmp_path / "src" / "models" / "tenant.ts"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                "export interface TenantRecord {\n  id: string;\n  tenantId: string;\n}\n",
                encoding="utf-8",
            )
            return {
                "content": "Wrote tenant model.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "src/models/tenant.ts"},
                    }
                ],
            }

        adapter._invoke_role_dialogue_with_timeout = _tenant_only_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-missing-target-synthesized-repair"},
        )

        user_text = (tmp_path / "src" / "models" / "user.ts").read_text(encoding="utf-8")
        permission_text = (tmp_path / "src" / "models" / "permission.ts").read_text(encoding="utf-8")
        assert result["success"] is True
        assert "export class User" in user_text
        assert "export class Permission" in permission_text
        assert "tenantId: string" in user_text
        assert "tenantId: string" in permission_text
        assert "src/models/user.ts" in result["changed_files"]
        assert "src/models/permission.ts" in result["changed_files"]

    @pytest.mark.asyncio
    async def test_execute_repairs_off_target_package_write_and_missing_declared_target(
        self,
        tmp_path: Any,
    ) -> None:
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Build server skeleton",
            description="Create the explicit server entrypoint and preserve a runnable test contract.",
            metadata={
                "target_files": ["src/server/index.ts"],
                "scope_paths": ["src/middleware", "src/models", "src/server/index.ts"],
                "steps": ["Create src/server/index.ts"],
                "acceptance": ["verify src/server/index.ts exists", "npm test must not be a placeholder"],
            },
        )

        async def _package_only_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            package_path = tmp_path / "package.json"
            package_path.write_text(
                "{\n"
                '  "name": "project-skeleton",\n'
                '  "version": "1.0.0",\n'
                '  "scripts": {\n'
                '    "test": "echo \\"Error: no test specified\\" && exit 0"\n'
                "  }\n"
                "}\n",
                encoding="utf-8",
            )
            return {
                "content": "Wrote package metadata.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "package.json"},
                    }
                ],
            }

        async def _empty_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {"content": "", "success": False, "error": "runtime_provider_unavailable"}

        adapter._invoke_role_dialogue_with_timeout = _package_only_dialogue  # type: ignore[method-assign]
        adapter._invoke_direct_runtime_provider = _empty_direct_fallback  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-package-only-missing-target-repair"},
        )

        package_text = (tmp_path / "package.json").read_text(encoding="utf-8")
        server_text = (tmp_path / "src" / "server" / "index.ts").read_text(encoding="utf-8")
        assert result["success"] is True
        assert "Error: no test specified" not in package_text
        assert "package manifest check passed" in package_text
        assert "export" in server_text
        assert "src/server/index.ts" in result["changed_files"]
        assert "package.json" in result["changed_files"]

    @pytest.mark.asyncio
    async def test_execute_repairs_root_scaffold_targets_from_package_only_write(
        self,
        tmp_path: Any,
    ) -> None:
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Project Scaffolding & Dependency Setup",
            description="Initialize package scripts, Python metadata, TypeScript config, and README overview.",
            metadata={
                "target_files": ["package.json", "pyproject.toml", "tsconfig.json", "readme.md"],
                "scope_paths": ["package.json", "pyproject.toml", "tsconfig.json", "readme.md"],
                "steps": ["Create root project scaffold files"],
                "acceptance": [
                    "package.json exists with basic scripts dev, build, test",
                    "pyproject.toml is valid for the intended language environment",
                    "tsconfig.json is configured for TypeScript",
                    "README.md contains project overview",
                ],
            },
        )

        async def _package_only_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            (tmp_path / "package.json").write_text(
                "{\n"
                '  "name": "polaris-project",\n'
                '  "version": "1.0.0",\n'
                '  "scripts": {\n'
                '    "dev": "tsc --watch",\n'
                '    "build": "tsc",\n'
                '    "test": "echo \\"No tests specified\\" && exit 0"\n'
                "  },\n"
                '  "devDependencies": {\n'
                '    "typescript": "^5.0.0"\n'
                "  }\n"
                "}\n",
                encoding="utf-8",
            )
            return {
                "content": "Wrote package metadata.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "package.json"},
                    }
                ],
            }

        adapter._invoke_role_dialogue_with_timeout = _package_only_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-root-scaffold-target-repair"},
        )

        package_text = (tmp_path / "package.json").read_text(encoding="utf-8")
        pyproject_text = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
        tsconfig_text = (tmp_path / "tsconfig.json").read_text(encoding="utf-8")
        readme_text = (tmp_path / "readme.md").read_text(encoding="utf-8")
        quality_errors = scan_workspace_artifact_quality(
            str(tmp_path),
            relative_paths=["package.json", "pyproject.toml", "tsconfig.json", "readme.md"],
        )
        assert result["success"] is True
        assert quality_errors == []
        assert "No tests specified" not in package_text
        assert '"test": "node -e' in package_text
        assert '\\" --"' in package_text
        assert "[project]" in pyproject_text
        assert "polaris-generated-workspace" in pyproject_text
        assert '"moduleResolution": "NodeNext"' in tsconfig_text
        assert "TypeScript project scaffold" in readme_text
        assert "package.json" in result["changed_files"]
        assert "pyproject.toml" in result["changed_files"]
        assert "tsconfig.json" in result["changed_files"]
        assert "readme.md" in result["changed_files"]

    @pytest.mark.asyncio
    async def test_execute_synthesizes_root_scaffold_targets_when_model_returns_no_write_tools(
        self,
        tmp_path: Any,
    ) -> None:
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Project Scaffolding & Dependency Setup",
            description="Initialize package scripts, Python metadata, TypeScript config, and README overview.",
            metadata={
                "target_files": ["package.json", "pyproject.toml", "tsconfig.json", "readme.md"],
                "scope_paths": ["package.json", "pyproject.toml", "tsconfig.json", "readme.md"],
                "steps": ["Create root project scaffold files"],
                "acceptance": [
                    "package.json exists with basic scripts dev, build, test",
                    "pyproject.toml is valid for the intended language environment",
                    "tsconfig.json is configured for TypeScript",
                    "README.md contains project overview",
                ],
            },
        )

        async def _empty_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {
                "content": '<|tool_call>call:repo_tree{path:<|"|>.<|"|>}<tool_call|>',
                "success": False,
                "error": "single_batch_contract_violation",
                "tool_results": [],
            }

        async def _empty_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {"content": "", "success": False, "error": "runtime_provider_unavailable"}

        adapter._invoke_role_dialogue_with_timeout = _empty_dialogue  # type: ignore[method-assign]
        adapter._invoke_direct_runtime_provider = _empty_direct_fallback  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-no-write-root-scaffold-repair"},
        )

        source_tools: list[str] = []
        for item in result["tool_results"]:
            if not isinstance(item, dict):
                continue
            raw_tool_result = item.get("result")
            if isinstance(raw_tool_result, dict):
                source_tools.append(str(raw_tool_result.get("source_tool") or ""))
        assert result["success"] is True
        assert (tmp_path / "package.json").is_file()
        assert (tmp_path / "pyproject.toml").is_file()
        assert (tmp_path / "tsconfig.json").is_file()
        assert (tmp_path / "readme.md").is_file()
        assert "package.json" in result["changed_files"]
        assert "pyproject.toml" in result["changed_files"]
        assert "tsconfig.json" in result["changed_files"]
        assert "readme.md" in result["changed_files"]
        assert "deterministic_missing_declared_target_repair" in source_tools

    @pytest.mark.asyncio
    async def test_execute_repairs_missing_root_targets_after_contract_exception_side_effect(
        self,
        tmp_path: Any,
    ) -> None:
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Initialize Project Scaffolding",
            description="Create core dependency and configuration files for the workspace.",
            metadata={
                "target_files": ["package.json", "tsconfig.json", "readme.md"],
                "scope_paths": ["package.json", "tsconfig.json", "readme.md"],
                "steps": ["Create root project scaffold files"],
                "acceptance": [
                    "package.json exists with runnable scripts",
                    "tsconfig.json configures TypeScript compilation",
                    "README.md documents the generated workspace",
                ],
            },
        )

        async def _contract_exception_after_write(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            # Built via json.dumps: the previous hand-escaped literal was
            # INVALID JSON (trailing backslash escape) and only survived
            # because nothing syntax-checked artifacts until the
            # check_source_file_syntax quality gate landed.
            (tmp_path / "package.json").write_text(
                json.dumps(
                    {
                        "name": "polaris-engine",
                        "version": "1.0.0",
                        "private": True,
                        "scripts": {
                            "build": "tsc",
                            "test": (
                                "node -e \"JSON.parse(require('fs').readFileSync('package.json', 'utf8'));"
                                " console.log('package ok')\""
                            ),
                        },
                        "devDependencies": {"typescript": "^5.0.0"},
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            raise RuntimeError(
                "TransactionKernel execution failed: single_batch_contract_violation: "
                "mutation requested but no write tool invocation in decision batch."
            )

        async def _empty_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {"content": "", "success": False, "error": "runtime_provider_unavailable"}

        adapter._invoke_role_dialogue_with_timeout = _contract_exception_after_write  # type: ignore[method-assign]
        adapter._invoke_direct_runtime_provider = _empty_direct_fallback  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-contract-exception-side-effect"},
        )

        source_tools: list[str] = []
        for item in result.get("tool_results", []):
            if not isinstance(item, dict):
                continue
            raw_tool_result = item.get("result")
            if isinstance(raw_tool_result, dict):
                source_tools.append(str(raw_tool_result.get("source_tool") or ""))
        assert result["success"] is True
        assert (tmp_path / "package.json").is_file()
        assert (tmp_path / "tsconfig.json").is_file()
        assert (tmp_path / "readme.md").is_file()
        assert "package.json" in result["changed_files"]
        assert "tsconfig.json" in result["changed_files"]
        assert "readme.md" in result["changed_files"]
        assert "deterministic_missing_declared_target_repair" in source_tools
        updated = adapter.task_board.get_task(str(task.id))
        assert updated is not None
        raw_metadata = updated.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        raw_adapter_result = metadata.get("adapter_result")
        adapter_result: dict[str, Any] = raw_adapter_result if isinstance(raw_adapter_result, dict) else {}
        assert (
            adapter_result.get("primary_llm", {})
            .get("error", "")
            .startswith("TransactionKernel execution failed: single_batch_contract_violation")
        )

    @pytest.mark.asyncio
    async def test_execute_fails_when_changed_file_has_no_domain_signal(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        target = tmp_path / "src" / "fish" / "arena.ts"
        target.parent.mkdir(parents=True, exist_ok=True)
        task = adapter.task_board.create(
            subject="Implement fish predator prey multiplayer arena",
            description="Build fish arena movement and predator prey scoring for the online game.",
            metadata={
                "target_files": ["src/fish/arena.ts"],
                "scope_paths": ["src/fish/arena.ts"],
                "steps": ["Implement fish arena gameplay"],
                "acceptance": ["No generic unrelated implementation remains"],
            },
        )

        async def _write_unrelated_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            target.write_text(
                "export function calculateInvoiceTotal(values: number[]): number {\n"
                "  return values.reduce((total, value) => total + value, 0);\n"
                "}\n",
                encoding="utf-8",
            )
            return {
                "content": "Wrote an implementation.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "src/fish/arena.ts"},
                    }
                ],
            }

        async def _unexpected_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            raise AssertionError("direct fallback should not run after workspace diff evidence")

        adapter._invoke_role_dialogue_with_timeout = _write_unrelated_dialogue  # type: ignore[method-assign]
        adapter._invoke_direct_runtime_provider = _unexpected_direct_fallback  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-semantic-quality"},
        )

        assert result["success"] is False
        assert result["error_code"] == "director_materialization_semantic_quality_failed"
        assert "no project-domain signal" in result["semantic_quality_error"]
        updated = adapter.task_board.get_task(str(task.id))
        assert updated is not None
        raw_metadata = updated.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        raw_adapter_result = metadata.get("adapter_result")
        adapter_result: dict[str, Any] = raw_adapter_result if isinstance(raw_adapter_result, dict) else {}
        assert adapter_result.get("materialization_error") == "director_materialization_semantic_quality_failed"

    @pytest.mark.asyncio
    async def test_execute_fails_autofix_declared_scope_without_real_materialization(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Implement interactive game renderer",
            description="Quality gate repair task generated because the PM contract omitted renderer scope.",
            metadata={
                "scope_paths": ["src/renderer/game-view.tsx"],
                "target_files": ["src/renderer/game-view.tsx"],
                "autofix": True,
                "autofix_reason": "game_pm_domain_coverage",
            },
        )

        async def _empty_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {"content": "", "success": False, "error": "role_model_not_configured"}

        async def _empty_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {"content": "", "success": False, "error": "runtime_provider_unavailable"}

        adapter._invoke_role_dialogue_with_timeout = _empty_dialogue  # type: ignore[method-assign]
        adapter._invoke_direct_runtime_provider = _empty_direct_fallback  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-scaffold"},
        )

        target = tmp_path / "src" / "renderer" / "game-view.tsx"
        assert result["success"] is False
        assert result["error_code"] == "director_no_materialized_changes"
        assert target.exists() is False
        updated = adapter.task_board.get_task(str(task.id))
        assert updated is not None
        raw_metadata = updated.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        raw_adapter_result = metadata.get("adapter_result")
        adapter_result: dict[str, Any] = raw_adapter_result if isinstance(raw_adapter_result, dict) else {}
        assert adapter_result.get("materialization_error") == "director_no_materialized_changes"
        assert adapter_result.get("new_files") == []
        assert adapter_result.get("primary_llm", {}).get("error") == "role_model_not_configured"
        assert adapter_result.get("direct_fallback", {}).get("error") == "runtime_provider_unavailable"

    @pytest.mark.asyncio
    async def test_execute_accepts_existing_scope_after_read_only_mutation_guard(self, tmp_path: Any) -> None:
        target = tmp_path / "src" / "server" / "app.ts"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "\n".join(
                [
                    "import http from 'http';",
                    "",
                    "export const server = http.createServer((_req, res) => {",
                    "  res.writeHead(200, { 'Content-Type': 'application/json' });",
                    "  res.end(JSON.stringify({ status: 'ok' }));",
                    "});",
                    "",
                    "export function startServer(port = 3000): void {",
                    "  server.listen(port);",
                    "}",
                    "",
                    "export default server;",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Extend Node.js backend entrypoint",
            description="Implement Node.js backend entrypoint.",
            metadata={
                "phase": "implementation",
                "scope_paths": ["src/server/app.ts"],
                "target_files": ["src/server/app.ts"],
                "steps": ["Implement src/server/app.ts"],
                "acceptance": ["npm run build verifies src/server/app.ts"],
            },
        )

        async def _read_only_contract_violation(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {
                "content": "",
                "success": False,
                "error": (
                    "TransactionKernel execution failed: single_batch_contract_violation: "
                    "mutation requested but no write tool invocation in decision batch."
                ),
            }

        async def _empty_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {"content": "", "success": False, "error": "runtime_provider_unavailable"}

        adapter._invoke_role_dialogue_with_timeout = _read_only_contract_violation  # type: ignore[method-assign]
        adapter._invoke_direct_runtime_provider = _empty_direct_fallback  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-existing-scope-after-read-only"},
        )

        assert result["success"] is True
        assert result["materialization_mode"] == "verified_existing_workspace_scope"
        updated = adapter.task_board.get_task(str(task.id))
        assert updated is not None
        assert str(updated.get("status") or "").lower() == "completed"
        raw_metadata = updated.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        raw_adapter_result = metadata.get("adapter_result")
        adapter_result: dict[str, Any] = raw_adapter_result if isinstance(raw_adapter_result, dict) else {}
        assert adapter_result.get("existing_contract_evidence", {}).get("ok") is True
        assert adapter_result.get("primary_llm", {}).get("error", "").startswith("TransactionKernel execution failed")
        assert adapter_result.get("direct_fallback", {}).get("error") == "runtime_provider_unavailable"

    @pytest.mark.asyncio
    async def test_execute_accepts_existing_scope_after_read_write_batch_violation(self, tmp_path: Any) -> None:
        target = tmp_path / "src" / "server" / "session-store.ts"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "export class SessionStore {\n"
            "  private readonly rows = new Map<string, string>();\n"
            "  save(roomId: string, value: string): void { this.rows.set(roomId, value); }\n"
            "  load(roomId: string): string | undefined { return this.rows.get(roomId); }\n"
            "}\n",
            encoding="utf-8",
        )
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Extend multiplayer session persistence",
            description="Implement multiplayer session persistence.",
            metadata={
                "phase": "core",
                "scope_paths": ["src/server/session-store.ts"],
                "target_files": ["src/server/session-store.ts"],
                "acceptance": ["src/server/session-store.ts exposes persistence methods"],
            },
        )

        async def _batch_contract_violation(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {
                "content": "",
                "success": False,
                "error": (
                    "TransactionKernel execution failed: single_batch_contract_violation: "
                    "Cannot mix Read tools (read_file) and Write tools (write_file) in the same parallel batch."
                ),
            }

        async def _empty_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {"content": "", "success": False, "error": "runtime_provider_unavailable"}

        adapter._invoke_role_dialogue_with_timeout = _batch_contract_violation  # type: ignore[method-assign]
        adapter._invoke_direct_runtime_provider = _empty_direct_fallback  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-existing-scope-after-batch-violation"},
        )

        assert result["success"] is True
        assert result["materialization_mode"] == "verified_existing_workspace_scope"
        assert result["existing_contract_evidence"]["ok"] is True

    @pytest.mark.asyncio
    async def test_execute_accepts_existing_scope_after_successful_no_diff_response(self, tmp_path: Any) -> None:
        target = tmp_path / "src" / "server" / "app.ts"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("export const serverReady = true;\n", encoding="utf-8")
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Extend Node.js backend entrypoint",
            description="Implement Node.js backend entrypoint.",
            metadata={
                "phase": "implementation",
                "scope_paths": ["src/server/app.ts"],
                "target_files": ["src/server/app.ts"],
            },
        )

        async def _successful_no_diff_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {"content": "Verified existing backend entrypoint.", "success": True}

        async def _empty_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {"content": "", "success": False, "error": "runtime_provider_unavailable"}

        adapter._invoke_role_dialogue_with_timeout = _successful_no_diff_dialogue  # type: ignore[method-assign]
        adapter._invoke_direct_runtime_provider = _empty_direct_fallback  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-existing-scope-after-successful-no-diff"},
        )

        assert result["success"] is True
        assert result["materialization_mode"] == "verified_existing_workspace_scope"

    @pytest.mark.asyncio
    async def test_execute_preflights_existing_verification_scope(self, tmp_path: Any) -> None:
        source = tmp_path / "src" / "server" / "room-state.ts"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(
            "export interface RoomStateRecord { id: string; roomId: string; }\n"
            "export function validateRoomStateRecord(record: RoomStateRecord): string[] {\n"
            "  const failures: string[] = [];\n"
            "  if (!record.id) failures.push('missing id');\n"
            "  if (!record.roomId) failures.push('missing roomId');\n"
            "  return failures;\n"
            "}\n",
            encoding="utf-8",
        )
        target = tmp_path / "tests" / "integration" / "multiplayer-flow.test.ts"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "import { validateRoomStateRecord } from '../../src/server/room-state';\n"
            "\n"
            "export function runMultiplayerFlowIntegrationChecks(): string[] {\n"
            "  const failures: string[] = [];\n"
            "  const issues = validateRoomStateRecord({ id: 'room-1', roomId: 'room-1' });\n"
            "  if (issues.length > 0) failures.push(issues.join(','));\n"
            "  return failures;\n"
            "}\n",
            encoding="utf-8",
        )
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Strengthen multiplayer card integration tests",
            description="Verify multiplayer card integration tests according to acceptance criteria.",
            metadata={
                "phase": "verify",
                "scope_paths": ["tests/integration/multiplayer-flow.test.ts"],
                "target_files": ["tests/integration/multiplayer-flow.test.ts"],
                "acceptance": ["No placeholder tests remain"],
            },
        )

        async def _unexpected_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            raise AssertionError("existing verification scope preflight should finish before LLM dialogue")

        adapter._invoke_role_dialogue_with_timeout = _unexpected_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-existing-verification-scope-preflight"},
        )

        assert result["success"] is True
        assert result["materialization_mode"] == "preflight_verified_existing_workspace_scope"
        raw_evidence = result.get("existing_contract_evidence")
        evidence: dict[str, Any] = raw_evidence if isinstance(raw_evidence, dict) else {}
        assert evidence.get("ok") is True

    @pytest.mark.asyncio
    async def test_execute_repairs_overstrict_node_test_contract_before_llm(self, tmp_path: Any) -> None:
        script = tmp_path / "scripts" / "test.mjs"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(
            "import { readFileSync } from 'node:fs';\n"
            "const text = readFileSync('src/analytics/match-analytics.ts', 'utf8');\n"
            "if (!/validate[A-Za-z]+Record/.test(text)) {\n"
            "  throw new Error('missing validation contract in src/analytics/match-analytics.ts');\n"
            "}\n",
            encoding="utf-8",
        )
        source_paths = [
            "src/analytics/match-analytics.ts",
            "src/animation/card-animations.ts",
            "src/assets/card-assets.ts",
            "src/client/card-table.ts",
            "src/client/network-client.ts",
            "src/client/three-scene.ts",
            "src/game/card-catalog.ts",
            "src/game/deck-builder.ts",
            "src/game/rules-engine.ts",
            "src/lobby/lobby-service.ts",
            "src/physics/table-layout.ts",
            "src/server/app.ts",
            "src/server/matchmaking.ts",
            "src/server/moderation.ts",
            "src/server/realtime-gateway.ts",
            "src/server/room-state.ts",
            "src/shared/protocol.ts",
            "src/shared/telemetry.ts",
        ]
        for index, rel_path in enumerate(source_paths):
            target = tmp_path / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"export const module{index}Ready = true;\n", encoding="utf-8")

        required_test_paths = [
            "tests/unit/card-rules.test.ts",
            "tests/unit/deck-builder.test.ts",
            "tests/integration/multiplayer-flow.test.ts",
            "tests/integration/realtime-sync.test.ts",
            "tests/e2e/card-table-3d.test.ts",
        ]
        for index, rel_path in enumerate(required_test_paths):
            target = tmp_path / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                "import { module0Ready } from '../../src/analytics/match-analytics';\n"
                f"export function runCard3DChecks{index}(): string[] {{\n"
                "  const failures: string[] = [];\n"
                "  if (!module0Ready) failures.push('module not ready');\n"
                "  return failures;\n"
                "}\n",
                encoding="utf-8",
            )

        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Strengthen multiplayer card integration test runner",
            description="Replace the brittle scripts/test.mjs validation-contract gate with substantive test checks.",
            metadata={
                "phase": "verify",
                "scope_paths": ["scripts/test.mjs", "tests/integration/multiplayer-flow.test.ts"],
                "target_files": ["scripts/test.mjs", "tests/integration/multiplayer-flow.test.ts"],
                "acceptance": ["npm run test verifies the Card3D behavior test suite"],
            },
        )

        async def _unexpected_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            raise AssertionError("deterministic test script repair should finish before LLM dialogue")

        adapter._invoke_role_dialogue_with_timeout = _unexpected_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-node-test-script-contract-repair"},
        )

        rewritten = script.read_text(encoding="utf-8")
        assert result["success"] is True
        assert result["materialization_mode"] == "write_tool_and_workspace_diff"
        assert result["tools_executed"] == 1
        assert result["changed_files"] == ["scripts/test.mjs"]
        assert rewritten == _build_substantive_node_test_script()
        assert "missing validation contract" not in rewritten
        assert "test file lacks executable check contract" in rewritten

    def test_detects_legacy_overstrict_node_export_contract(self) -> None:
        legacy_script = (
            "for (const file of sourceFiles) {\n"
            "  const text = readFileSync(file, 'utf8');\n"
            "  if (!/export\\s+(class|function|const|interface|type)/.test(text)) {\n"
            "    throw new Error('missing export in ' + file);\n"
            "  }\n"
            "}\n"
        )

        assert _is_overstrict_node_test_script_contract(legacy_script) is True
        assert _is_overstrict_node_test_script_contract(_build_substantive_node_test_script()) is False

    def test_substantive_node_test_script_accepts_named_export_blocks(self, tmp_path: Any) -> None:
        script = tmp_path / "scripts" / "test.mjs"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(_build_substantive_node_test_script(), encoding="utf-8")

        for relative in [
            "src/server/app.ts",
            "src/client/three-scene.ts",
            "src/client/card-table.ts",
            "src/client/network-client.ts",
            "src/server/realtime-gateway.ts",
            "src/server/matchmaking.ts",
            "src/server/room-state.ts",
            "src/server/session-store.ts",
            "src/server/moderation.ts",
            "src/game/card-catalog.ts",
            "src/game/deck-builder.ts",
            "src/game/rules-engine.ts",
            "src/shared/protocol.ts",
            "src/shared/player-presence.ts",
            "src/shared/telemetry.ts",
            "src/assets/card-assets.ts",
            "src/animation/card-animations.ts",
            "src/auth/session-auth.ts",
        ]:
            target = tmp_path / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if relative == "src/server/app.ts":
                target.write_text(
                    "const server = { listen() {} };\n"
                    "const sessions = new Map<string, string>();\n"
                    "export { server, sessions };\n",
                    encoding="utf-8",
                )
            else:
                stem = target.stem.replace("-", "_")
                target.write_text(f"export const {stem}Ready = true;\n", encoding="utf-8")

        for index, relative in enumerate(
            [
                "tests/unit/card-rules.test.ts",
                "tests/unit/deck-builder.test.ts",
                "tests/integration/multiplayer-flow.test.ts",
                "tests/integration/realtime-sync.test.ts",
                "tests/e2e/card-table-3d.test.ts",
            ]
        ):
            test_file = tmp_path / relative
            test_file.parent.mkdir(parents=True, exist_ok=True)
            test_file.write_text(
                "import { card_catalogReady } from '../../src/game/card-catalog';\n"
                f"export function runCard3DChecks{index}(): string[] {{\n"
                "  const failures: string[] = [];\n"
                "  if (!card_catalogReady) failures.push('catalog not ready');\n"
                "  return failures;\n"
                "}\n",
                encoding="utf-8",
            )

        result = subprocess.run(
            ["node", "scripts/test.mjs", "--watch=false"],
            cwd=tmp_path,
            text=True,
            capture_output=True,
            check=False,
        )

        assert result.returncode == 0, result.stderr
        assert "card3d behavior checks passed" in result.stdout

    def test_deterministic_patch_residue_cleanup_removes_declared_marker(self, tmp_path: Any) -> None:
        target = tmp_path / "src" / "assets" / "card-assets.ts"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "export const cardAssetsReady = true;\n"
            ">>>> REPLACE src/assets/card-assets.ts\n"
            "export const assetCount = 52;\n",
            encoding="utf-8",
        )
        adapter = _make_adapter(tmp_path)

        results = _apply_deterministic_patch_residue_cleanup(
            adapter,
            task={
                "metadata": {
                    "target_files": ["src/assets/card-assets.ts"],
                    "scope_paths": ["src/assets/card-assets.ts"],
                }
            },
            task_id="PM-CARD3D-ASSETS-18",
        )

        cleaned = target.read_text(encoding="utf-8")
        assert len(results) == 1
        assert results[0]["tool"] == "write_file"
        assert results[0]["result"]["source_tool"] == "deterministic_patch_residue_cleanup"
        assert ">>>> REPLACE" not in cleaned
        assert "export const cardAssetsReady = true;" in cleaned
        assert "export const assetCount = 52;" in cleaned
        assert _remove_patch_residue_lines(cleaned) == cleaned

    def test_deterministic_patch_residue_cleanup_ignores_unscoped_files(self, tmp_path: Any) -> None:
        target = tmp_path / "src" / "assets" / "card-assets.ts"
        target.parent.mkdir(parents=True, exist_ok=True)
        original = "export const cardAssetsReady = true;\n>>>> REPLACE src/assets/card-assets.ts\n"
        target.write_text(original, encoding="utf-8")
        adapter = _make_adapter(tmp_path)

        results = _apply_deterministic_patch_residue_cleanup(
            adapter,
            task={"metadata": {"target_files": ["src/server/app.ts"]}},
            task_id="PM-CARD3D-SERVER-01",
        )

        assert results == []
        assert target.read_text(encoding="utf-8") == original

    def test_deterministic_scaffold_marker_cleanup_rewrites_declared_residue_files(self, tmp_path: Any) -> None:
        source = tmp_path / "src" / "server" / "app.ts"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(
            'export const tags = ["runtime", "audit-seed"];\nexport const title = "server planning scenario 0";\n',
            encoding="utf-8",
        )
        script = tmp_path / "scripts" / "test.mjs"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(
            "throw new Error(`trivial arithmetic placeholder test scripts/test.mjs`);\n"
            "console.log(`test verification completed: 1 files`);\n",
            encoding="utf-8",
        )
        adapter = _make_adapter(tmp_path)

        results = _apply_deterministic_scaffold_marker_cleanup(
            adapter,
            task={
                "metadata": {
                    "target_files": ["src/server/app.ts", "scripts/test.mjs"],
                    "scope_paths": ["src/server/app.ts", "scripts/test.mjs"],
                    "autofix_reason": "deterministic_scaffold_residue_cleanup",
                }
            },
            task_id="PM-AUTO-SEED-RESIDUE-CLEANUP",
        )

        assert len(results) == 2
        assert {item["result"]["source_tool"] for item in results} == {"deterministic_scaffold_marker_cleanup"}
        source_text = source.read_text(encoding="utf-8")
        script_text = script.read_text(encoding="utf-8")
        assert "audit-seed" not in source_text
        assert "planning scenario" not in source_text
        assert "test verification completed" not in script_text
        assert "placeholder" not in script_text
        assert "verified-sample" in source_text
        assert "test contract checks passed" in script_text

    @pytest.mark.asyncio
    async def test_execute_completes_scaffold_marker_cleanup_without_llm_call(self, tmp_path: Any) -> None:
        source = tmp_path / "src" / "server" / "app.ts"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(
            'export const tags = ["runtime", "audit-seed"];\nexport const title = "server planning scenario 0";\n',
            encoding="utf-8",
        )
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Clean deterministic scaffold residue",
            description="Remove deterministic scaffold residue before QA.",
            metadata={
                "target_files": ["src/server/app.ts"],
                "scope_paths": ["src/server/app.ts"],
                "steps": ["Clean deterministic scaffold residue"],
                "acceptance": ["Declared files contain no audit-seed or deterministic scaffold markers"],
                "autofix_reason": "deterministic_scaffold_residue_cleanup",
            },
        )

        async def _unexpected_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            raise AssertionError("cleanup task should complete without invoking Gemma")

        adapter._invoke_role_dialogue_with_timeout = _unexpected_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-scaffold-marker-cleanup"},
        )

        assert result["success"] is True
        assert result["tools_executed"] >= 1
        assert "src/server/app.ts" in result["changed_files"]
        source_text = source.read_text(encoding="utf-8")
        assert "audit-seed" not in source_text
        assert "planning scenario" not in source_text

    @pytest.mark.asyncio
    async def test_ready_queue_fallback_claim_preserves_selected_task_identity(self, tmp_path: Any) -> None:
        target = tmp_path / "src" / "combat" / "combat-system.ts"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("export const combatReady = true;\n", encoding="utf-8")
        adapter = _make_adapter(tmp_path)
        combat = adapter.task_board.create(
            subject="Audit turn based combat system scope",
            description="Materialize combat scope.",
            metadata={
                "external_task_id": "PM-AUTO-COMBAT",
                "source_task_id": "PM-AUTO-COMBAT",
                "target_files": ["src/combat/combat-system.ts"],
            },
        )

        async def _unexpected_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            raise AssertionError("existing scope preflight should finish before LLM dialogue")

        adapter._invoke_role_dialogue_with_timeout = _unexpected_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id="PM-AUTO-AI",
            input_data={"task_id": "PM-AUTO-AI"},
            context={"run_id": "run-director-identity"},
        )

        assert result["success"] is True
        updated = adapter.task_board.get_task(str(combat.id))
        assert updated is not None
        metadata_raw = updated.get("metadata")
        metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
        runtime_execution_raw = metadata.get("runtime_execution")
        runtime_execution: dict[str, Any] = runtime_execution_raw if isinstance(runtime_execution_raw, dict) else {}
        assert metadata["external_task_id"] == "PM-AUTO-COMBAT"
        assert runtime_execution["external_task_id"] == "PM-AUTO-COMBAT"

    def test_claim_external_task_id_prefers_selected_task_source(self) -> None:
        assert (
            _resolve_claim_external_task_id(
                {
                    "id": 4,
                    "metadata": {
                        "external_task_id": "PM-AUTO-AI",
                        "source_task_id": "PM-AUTO-COMBAT",
                    },
                },
                "PM-AUTO-AI",
            )
            == "PM-AUTO-COMBAT"
        )

    @pytest.mark.asyncio
    async def test_execute_rejects_existing_autofix_scaffold_without_real_materialization(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        target = tmp_path / "src" / "renderer" / "game-view.tsx"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            'export const gameViewScaffoldVersion = "deterministic-declared-scope-v1";\n',
            encoding="utf-8",
        )
        task = adapter.task_board.create(
            subject="Implement interactive game renderer",
            description="Quality gate repair task generated because the PM contract omitted renderer scope.",
            metadata={
                "scope_paths": ["src/renderer/game-view.tsx"],
                "target_files": ["src/renderer/game-view.tsx"],
                "autofix": True,
                "autofix_reason": "game_pm_domain_coverage",
            },
        )

        async def _empty_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {"content": "", "success": False, "error": "role_model_not_configured"}

        async def _empty_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {"content": "", "success": False, "error": "runtime_provider_unavailable"}

        adapter._invoke_role_dialogue_with_timeout = _empty_dialogue  # type: ignore[method-assign]
        adapter._invoke_direct_runtime_provider = _empty_direct_fallback  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-existing-scaffold"},
        )

        assert result["success"] is False
        assert result["error_code"] == "director_no_materialized_changes"
        updated = adapter.task_board.get_task(str(task.id))
        assert updated is not None
        raw_metadata = updated.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        raw_adapter_result = metadata.get("adapter_result")
        adapter_result: dict[str, Any] = raw_adapter_result if isinstance(raw_adapter_result, dict) else {}
        assert adapter_result.get("materialization_error") == "director_no_materialized_changes"
        assert adapter_result.get("modified_files") == []
        assert adapter_result.get("primary_llm", {}).get("error") == "role_model_not_configured"

    def test_text_patch_mode_requests_parseable_file_blocks(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = adapter._build_director_message({"subject": "T"}, text_patch_mode=True)
        assert "当前运行时要求纯文本补丁" in msg
        assert "relative/path.ext" in msg
        assert "path/to/file.py" not in msg


# ---------------------------------------------------------------------------
# Existing workspace evidence
# ---------------------------------------------------------------------------


class TestExistingWorkspaceTaskEvidence:
    """Director can verify already-materialized task scope without fresh diffs."""

    def test_declared_scope_present(self) -> None:
        task = {
            "scope": [
                "package.json",
                "src/types",
                "src/spec",
                "src/services",
                "src/store",
            ]
        }
        current_files = {
            "package.json": "1",
            "src/types/domain.ts": "1",
            "src/spec/generationSpec.ts": "1",
            "src/services/mockGenerationService.ts": "1",
            "src/store/useStudioStore.ts": "1",
        }

        evidence = _build_existing_workspace_task_evidence(task=task, current_files=current_files)

        assert evidence["ok"] is True
        assert evidence["reason"] == "declared_scope_present"
        assert "src/spec" in evidence["existing_paths"]

    def test_missing_or_weak_scope_is_not_enough(self) -> None:
        task = {"scope": ["src/workbench", "src/library", "src/layouts", "src/components"]}
        current_files = {"src/components/StudioShell.tsx": "1"}

        evidence = _build_existing_workspace_task_evidence(task=task, current_files=current_files)

        assert evidence["ok"] is False
        assert evidence["reason"] == "declared_scope_incomplete"

    def test_no_scope_paths_is_not_evidence(self) -> None:
        evidence = _build_existing_workspace_task_evidence(
            task={"goal": "Implement a UI"},
            current_files={"src/App.tsx": "1"},
        )

        assert evidence["ok"] is False
        assert evidence["reason"] == "no_declared_scope_paths"

    def test_glob_scope_paths_match_workspace_files(self) -> None:
        task = {
            "metadata": {
                "scope": [
                    "src/**/*.test.ts",
                    "src/**/*.test.tsx",
                    "README.md",
                    "tests",
                ]
            }
        }
        current_files = {
            "src/spec/generationSpec.test.ts": "1",
            "src/App.test.tsx": "1",
            "README.md": "1",
        }

        evidence = _build_existing_workspace_task_evidence(task=task, current_files=current_files)

        assert evidence["ok"] is True
        assert "src/**/*.test.ts" in evidence["existing_paths"]
        assert "README.md" in evidence["existing_paths"]

    def test_existing_scope_rejects_placeholder_tests_when_workspace_is_available(self, tmp_path: Any) -> None:
        test_file = tmp_path / "tests" / "unit" / "card-rules.test.ts"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text(
            "\n".join(f"test('case {idx}', () => expect({idx} + 1).toBe({idx + 1}));" for idx in range(4)) + "\n",
            encoding="utf-8",
        )
        task = {
            "target_files": ["tests/unit/card-rules.test.ts"],
            "scope_paths": ["tests"],
        }
        current_files = {"tests/unit/card-rules.test.ts": "1"}

        evidence = _build_existing_workspace_task_evidence(
            task=task,
            current_files=current_files,
            workspace_full=str(tmp_path),
        )

        assert evidence["ok"] is False
        assert evidence["reason"] == "declared_scope_quality_failed"
        assert any("trivial arithmetic placeholder" in item for item in evidence["artifact_quality_errors"])

    def test_materialized_orchestration_scope_markers_are_evidence(self) -> None:
        task = {
            "subject": (
                "Execute PM tasks strictly in order:\n"
                "- Project Foundation [scope: package.json, tsconfig.json, vite.config.ts, tailwind.config.js]\n"
                "- Domain Layer [scope: src/types, src/spec, src/services, src/store]\n"
                "- Delivery Verification [scope: tests, src/**/*.test.tsx, README.md]"
            )
        }
        current_files = {
            "package.json": "1",
            "tsconfig.json": "1",
            "vite.config.ts": "1",
            "tailwind.config.js": "1",
            "src/types/domain.ts": "1",
            "src/spec/generationSpec.ts": "1",
            "src/services/mockGenerationService.ts": "1",
            "src/store/useStudioStore.ts": "1",
            "src/App.test.tsx": "1",
            "tests/routes/WorkbenchRoute.test.tsx": "1",
            "README.md": "1",
        }

        evidence = _build_existing_workspace_task_evidence(task=task, current_files=current_files)

        assert evidence["ok"] is True
        assert "package.json" in evidence["existing_paths"]
        assert "src/**/*.test.tsx" in evidence["existing_paths"]
        assert evidence["reason"] == "declared_scope_present"

    def test_scope_label_prefixes_do_not_pollute_path_candidates(self) -> None:
        task = {"metadata": {"scope": "Root configuration files: package.json, tsconfig.json, postcss.config.js"}}
        current_files = {
            "package.json": "1",
            "tsconfig.json": "1",
            "postcss.config.js": "1",
        }

        evidence = _build_existing_workspace_task_evidence(task=task, current_files=current_files)

        assert evidence["ok"] is True
        assert "package.json" in evidence["existing_paths"]
        assert all("Root configuration files" not in item for item in evidence["candidate_paths"])

    def test_workspace_basename_prefix_is_not_treated_as_nested_scope(self) -> None:
        task = {
            "metadata": {
                "scope": "fashion-gen-studio/package.json, fashion-gen-studio/src/, vite.config.ts",
            }
        }
        current_files = {
            "package.json": "1",
            "src/App.tsx": "1",
            "vite.config.ts": "1",
        }

        evidence = _build_existing_workspace_task_evidence(
            task=task,
            current_files=current_files,
            workspace_name="fashion-gen-studio",
        )

        assert evidence["ok"] is True
        assert "package.json" in evidence["existing_paths"]
        assert "src" in evidence["existing_paths"]
        assert "fashion-gen-studio/package.json" not in evidence["missing_paths"]

    def test_repair_tasks_require_fresh_materialization(self) -> None:
        assert (
            _task_requires_fresh_materialization(
                {
                    "subject": "Repair TypeScript failure",
                    "metadata": {"acceptance": ["npm test returns PASS"]},
                }
            )
            is True
        )
        assert _task_requires_fresh_materialization({"subject": "Create initial source files"}) is True
        assert (
            _task_requires_fresh_materialization(
                {
                    "subject": "Implement Card3D tests",
                    "phase": "verification",
                    "target_files": ["tests/integration/multiplayer-flow.test.ts"],
                }
            )
            is True
        )
        assert (
            _task_requires_fresh_materialization(
                {
                    "title": "补齐领域验收测试",
                    "goal": "移除旧的占位测试，创建覆盖卡牌、牌组、多人流程、同步与3D场景的测试",
                    "phase": "verify",
                    "target_files": [
                        "tests/unit/card-rules.test.ts",
                        "tests/integration/multiplayer-flow.test.ts",
                    ],
                    "execution_checklist": ["删除已存在的 trivial 占位测试（如算术测试）"],
                }
            )
            is True
        )
        assert (
            _task_requires_fresh_materialization(
                {
                    "subject": "Replace placeholder Card3D unit tests",
                    "description": "Remove trivial arithmetic placeholder tests.",
                }
            )
            is True
        )
        assert (
            _task_requires_fresh_materialization(
                {
                    "subject": "QA Placeholder Repair Verification",
                    "phase": "verification",
                    "metadata": {"qa_rework_verification_only": True},
                }
            )
            is False
        )
        assert (
            _task_requires_fresh_materialization(
                {
                    "subject": "Frontend Test Failure Reproduction",
                    "description": "Fix npm test failure with the smallest target-project change after evidence is collected.",
                    "metadata": {
                        "phase": "requirements",
                        "steps": ["Run npm test", "Identify failing assertion"],
                        "acceptance": ["The failing Vitest case is identified"],
                    },
                }
            )
            is False
        )
        assert (
            _task_requires_fresh_materialization(
                {
                    "subject": "Requirements task reopened by QA",
                    "metadata": {
                        "phase": "requirements",
                        "qa_rework_requested": True,
                        "adapter_result": {
                            "qa_passed": False,
                            "qa_rework_reason": "placeholder_content_detected",
                        },
                    },
                }
            )
            is True
        )

    def test_transient_provider_errors_can_accept_existing_scope(self) -> None:
        task = {
            "subject": "Extend realtime gateway",
            "phase": "implementation",
            "target_files": ["src/server/realtime-gateway.ts"],
        }

        assert (
            _can_accept_existing_workspace_scope(
                task=task,
                requires_fresh_materialization=True,
                write_tool_evidence=False,
                primary_llm_summary={
                    "success": False,
                    "error": "TransactionKernel execution failed: circuit_open:50s_remaining",
                },
            )
            is True
        )
        assert (
            _can_accept_existing_workspace_scope(
                task=task,
                requires_fresh_materialization=True,
                write_tool_evidence=False,
                primary_llm_summary={
                    "success": False,
                    "error": "429 Client Error: Too Many Requests for url",
                },
            )
            is True
        )

    def test_non_transient_no_write_still_requires_materialization(self) -> None:
        assert (
            _can_accept_existing_workspace_scope(
                task={
                    "subject": "Extend realtime gateway",
                    "phase": "implementation",
                    "target_files": ["src/server/realtime-gateway.ts"],
                },
                requires_fresh_materialization=True,
                write_tool_evidence=False,
                primary_llm_summary={"success": False, "error": "model returned no tool calls"},
            )
            is False
        )


class TestDeterministicTypescriptReexportRepair:
    """Director can materialize a narrow TypeScript runtime re-export fix."""

    def test_repairs_missing_runtime_reexport(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        type_dir = tmp_path / "src" / "types"
        test_dir = type_dir / "__tests__"
        test_dir.mkdir(parents=True)
        (type_dir / "asset.ts").write_text(
            "export enum AssetType {\n  garment = 'garment',\n}\n",
            encoding="utf-8",
        )
        (type_dir / "generation.ts").write_text(
            "import type { Asset } from './asset';\n"
            "export enum TaskType {\n  garment_to_model = 'garment_to_model',\n}\n"
            "export interface GenerationSpec {\n  input_assets: Asset[];\n}\n",
            encoding="utf-8",
        )
        (test_dir / "spec.test.ts").write_text(
            "import { GenerationSpec, TaskType, AssetType } from '../generation';\nconst type = AssetType.garment;\n",
            encoding="utf-8",
        )

        results = _apply_deterministic_typescript_reexport_repair(
            adapter,
            task={
                "subject": "Repair TypeScript npm test failure",
                "description": (
                    "Vitest reports Cannot read properties of undefined while importing AssetType from ../generation."
                ),
            },
            task_id="task-1",
        )

        assert results and results[0]["success"] is True
        generation_text = (type_dir / "generation.ts").read_text(encoding="utf-8")
        assert "export { AssetType } from './asset';" in generation_text

        second = _apply_deterministic_typescript_reexport_repair(
            adapter,
            task={
                "subject": "Repair TypeScript npm test failure",
                "description": "Cannot read properties of undefined for AssetType",
            },
            task_id="task-1",
        )

        assert second == []
        updated_text = (type_dir / "generation.ts").read_text(encoding="utf-8")
        assert updated_text.count("export { AssetType } from './asset';") == 1

    def test_export_import_contract_fix_triggers_reexport_repair(self) -> None:
        assert (
            _looks_like_typescript_reexport_failure(
                "Apply a minimal TypeScript export/import contract fix; npm test must pass."
            )
            is True
        )


# ---------------------------------------------------------------------------
# Materialized metadata
# ---------------------------------------------------------------------------


class TestBuildMaterializedMetadata:
    """_build_materialized_metadata is a pure dict transformation."""

    def test_basic_fields(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        meta = adapter._build_materialized_metadata("req-1", {"goal": "g", "scope": "s", "steps": ["a"]})
        assert meta["goal"] == "g"
        assert meta["scope"] == "s"
        assert meta["steps"] == ["a"]
        assert meta["phase"] == "implementation"
        assert meta["pm_task_id"] == "req-1"
        assert meta["source"] == "director_adapter.materialized_orchestration_task"

    def test_input_metadata_merged(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        meta = adapter._build_materialized_metadata(
            "req-1",
            {"metadata": {"custom": "v", "projection": {"x": 1}}},
        )
        assert meta["custom"] == "v"
        assert "projection" not in meta  # projection key is stripped

    def test_none_input_data(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        meta = adapter._build_materialized_metadata("req-1", None)  # type: ignore[arg-type]
        assert meta["pm_task_id"] == "req-1"

    def test_nested_pm_task_metadata_preserves_execution_contract(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        meta = adapter._build_materialized_metadata(
            "task-0-director",
            {
                "metadata": {
                    "id": "T01-001",
                    "goal": "Create the TypeScript foundation",
                    "target_files": ["package.json", "src/index.ts"],
                    "scope_paths": ["src/config"],
                    "blueprint_id": "ce_T01-001",
                }
            },
        )

        assert meta["pm_task_id"] == "T01-001"
        assert meta["target_files"] == ["package.json", "src/index.ts"]
        assert meta["scope_paths"] == ["src/config"]
        assert meta["blueprint_id"] == "ce_T01-001"


# ---------------------------------------------------------------------------
# Execution backend resolution
# ---------------------------------------------------------------------------


class TestResolveExecutionBackendRequest:
    """_resolve_execution_backend_request delegates to resolve_director_execution_backend."""

    def test_defaults_to_code_edit(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        req = adapter._resolve_execution_backend_request(
            task_id="t1",
            task={},
            input_data={},
            context={},
        )
        assert req.execution_backend == "code_edit"
        assert req.is_supported is True
        assert req.is_projection_backend is False

    def test_projection_hint_in_request(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        req = adapter._resolve_execution_backend_request(
            task_id="t1",
            task={"metadata": {"execution_backend": "projection_generate", "projection": {"scenario_id": "s1"}}},
            input_data={},
            context={},
        )
        assert req.execution_backend == "projection_generate"
        assert req.scenario_id == "s1"
        assert req.is_projection_backend is True


# ---------------------------------------------------------------------------
# Persist metadata
# ---------------------------------------------------------------------------


class TestPersistExecutionBackendMetadata:
    """_persist_execution_backend_metadata delegates to _update_board_task."""

    def test_noop_when_task_id_empty(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        # Should not raise even with no task_board
        adapter._persist_execution_backend_metadata("", MagicMock())

    def test_calls_update_board_task(self, tmp_path: Any) -> None:
        mock_runtime = MagicMock()
        adapter = _make_adapter(tmp_path, task_runtime=mock_runtime)
        from polaris.cells.roles.adapters.internal.director_execution_backend import DirectorExecutionBackendRequest

        req = DirectorExecutionBackendRequest(execution_backend="code_edit")
        adapter._persist_execution_backend_metadata("t1", req)
        mock_runtime.update_task.assert_called_once()


# ---------------------------------------------------------------------------
# Capabilities / role_id
# ---------------------------------------------------------------------------


class TestDirectorAdapterIdentity:
    def test_role_id(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter.role_id == "director"

    def test_capabilities(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        caps = adapter.get_capabilities()
        assert "execute_task" in caps
        assert "sequential_execution" in caps
        assert "adaptive_strategy_selection" in caps


class TestDirectorRuntimeFallback:
    @pytest.mark.asyncio
    async def test_role_dialogue_uses_role_runtime_context_os_path_first(
        self,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        adapter = _make_adapter(tmp_path)
        captured: dict[str, Any] = {}

        class FakeRoleRuntimeService:
            async def execute_role_session(self, command: ExecuteRoleSessionCommandV1) -> RoleExecutionResultV1:
                captured["command"] = command
                return RoleExecutionResultV1(
                    ok=True,
                    status="ok",
                    role="director",
                    workspace=str(tmp_path),
                    task_id=command.task_id,
                    session_id=command.session_id,
                    run_id=command.run_id,
                    output="done",
                    usage={"tokens": 10},
                    metadata={"provider_id": "anthropic_compat-test", "model": "kimi-for-coding"},
                )

        monkeypatch.setattr(
            "polaris.cells.roles.runtime.public.service.RoleRuntimeService",
            FakeRoleRuntimeService,
        )

        result = await adapter._invoke_role_dialogue(
            "write src/app.ts",
            context={"run_id": "run-runtime-first", "task_id": "task-runtime-first"},
        )

        assert result["success"] is True
        assert result["metadata"]["role_runtime_entrypoint"] == "roles.runtime.execute_role_session"
        assert result["metadata"]["context_os_expected"] is True
        command = captured["command"]
        assert isinstance(command, ExecuteRoleSessionCommandV1)
        assert command.role == "director"
        assert command.domain == "code"
        assert command.stream is False
        assert command.run_id == "run-runtime-first"
        assert command.task_id == "task-runtime-first"
        assert command.metadata["role_runtime_required"] is True
        assert command.metadata["cognitive_runtime_required"] is True

    @pytest.mark.asyncio
    async def test_role_dialogue_fails_closed_when_runtime_boundary_unavailable(
        self,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        adapter = _make_adapter(tmp_path)

        class UnavailableRoleRuntimeService:
            def __init__(self) -> None:
                raise ImportError("runtime boundary unavailable")

        monkeypatch.setattr(
            "polaris.cells.roles.runtime.public.service.RoleRuntimeService",
            UnavailableRoleRuntimeService,
        )

        with pytest.raises(RuntimeError, match="director_role_runtime_boundary_unavailable"):
            await adapter._invoke_role_dialogue("write src/app.ts")

    @pytest.mark.asyncio
    async def test_role_dialogue_runtime_execution_failure_does_not_fallback_to_legacy(
        self,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        adapter = _make_adapter(tmp_path)

        class FailingRoleRuntimeService:
            async def execute_role_session(self, command: ExecuteRoleSessionCommandV1) -> RoleExecutionResultV1:
                del command
                raise RuntimeError("runtime provider failed")

        monkeypatch.setattr(
            "polaris.cells.roles.runtime.public.service.RoleRuntimeService",
            FailingRoleRuntimeService,
        )

        with pytest.raises(RuntimeError, match="runtime provider failed"):
            await adapter._invoke_role_dialogue("write src/app.ts")

    @pytest.mark.asyncio
    async def test_direct_runtime_provider_bypass_is_removed(
        self,
        tmp_path: Any,
    ) -> None:
        adapter = _make_adapter(tmp_path)
        with pytest.raises(RuntimeError, match="director_direct_runtime_provider_removed"):
            await adapter._invoke_direct_runtime_provider("write a file", timeout_seconds=3)


# ---------------------------------------------------------------------------
# Integration with execution backend module (pure helpers)
# ---------------------------------------------------------------------------


class TestDirectorExecutionBackendPure:
    """Tests for the pure helper functions in director_execution_backend."""

    def test_normalize_backend(self) -> None:
        from polaris.cells.roles.adapters.internal.director_execution_backend import _normalize_backend

        assert _normalize_backend("code_edit") == "code_edit"
        assert _normalize_backend("projection_generate") == "projection_generate"
        assert _normalize_backend("") == "code_edit"
        assert _normalize_backend("unknown") == "unknown"

    def test_normalize_project_slug(self) -> None:
        from polaris.cells.roles.adapters.internal.director_execution_backend import _normalize_project_slug

        assert _normalize_project_slug("My Project", default_value="default") == "my_project"
        assert _normalize_project_slug("", default_value="default") == "default"

    def test_normalize_bool(self) -> None:
        from polaris.cells.roles.adapters.internal.director_execution_backend import _normalize_bool

        assert _normalize_bool(True, default=False) is True
        assert _normalize_bool("1", default=False) is True
        assert _normalize_bool("false", default=True) is False
        assert _normalize_bool(None, default=True) is True

    def test_request_to_task_metadata(self) -> None:
        from polaris.cells.roles.adapters.internal.director_execution_backend import DirectorExecutionBackendRequest

        req = DirectorExecutionBackendRequest(execution_backend="projection_generate", scenario_id="s1")
        meta = req.to_task_metadata()
        assert meta["execution_backend"] == "projection_generate"
        assert meta["projection"]["scenario_id"] == "s1"


def test_scaffold_synthesis_default_off(monkeypatch) -> None:
    """§8 regression: without explicit opt-in, no placeholder content is
    fabricated — a Python calculator must never receive a TypeScript scaffold."""
    monkeypatch.delenv("KERNELONE_DIRECTOR_SCAFFOLD_SYNTHESIS", raising=False)
    from polaris.cells.roles.adapters.internal.director.execute_method import (
        _synthesize_declared_target_file_content,
    )

    assert _synthesize_declared_target_file_content("readme.md") == ""
    assert _synthesize_declared_target_file_content("package.json") == ""
    assert _synthesize_declared_target_file_content("src/models/tenant.model.ts") == ""


class TestDeclaredPathCaseInsensitiveMatching:
    """L2-09 PM-0001-2 regression: PM declared "readme.md", Director wrote
    "README.md"; the case-sensitive declared-path filter dropped the task's
    only real output and produced director_no_materialized_changes."""

    def test_filter_keeps_case_mismatched_target(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _filter_diff_to_task_declared_paths,
        )

        new_files, modified_files = _filter_diff_to_task_declared_paths(
            task={"target_files": ["readme.md"]},
            new_files=["README.md"],
            modified_files=[],
        )
        assert new_files == ["README.md"]
        assert modified_files == []

    def test_filter_keeps_exact_case_target(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _filter_diff_to_task_declared_paths,
        )

        new_files, _ = _filter_diff_to_task_declared_paths(
            task={"target_files": ["src/App.tsx"]},
            new_files=["src/App.tsx", "src/unrelated.ts"],
            modified_files=[],
        )
        assert new_files == ["src/App.tsx"]

    def test_filter_still_excludes_unrelated_files(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _filter_diff_to_task_declared_paths,
        )

        new_files, modified_files = _filter_diff_to_task_declared_paths(
            task={"target_files": ["readme.md"]},
            new_files=["game.js"],
            modified_files=["index.html"],
        )
        assert new_files == []
        assert modified_files == []

    def test_directory_candidate_matches_case_insensitively(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _path_matches_declared_candidate,
        )

        assert _path_matches_declared_candidate("Docs/Guide.md", "docs")
        assert _path_matches_declared_candidate("src/app.PY", "src/app.py")
        assert not _path_matches_declared_candidate("other/file.md", "docs")

    def test_glob_candidate_matches_case_insensitively(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _glob_path_matches,
        )

        assert _glob_path_matches("README.md", "readme.*")
        assert _glob_path_matches("src/Views/Home.vue", "src/**/home.vue")


class TestAcceptanceVerifyExistsExemption:
    """L2-09 class: identical-rewrite / case-variant writes produce an empty
    diff; when the PM contract's own `verify <path> exists` machine checks all
    pass AND write receipts exist, the task is satisfied, not failed."""

    @staticmethod
    def _evaluate(task: dict, workspace: Any, write_tool_evidence: bool = True) -> tuple[bool, dict]:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _evaluate_acceptance_verify_exists,
        )

        return _evaluate_acceptance_verify_exists(
            task=task,
            workspace_full=str(workspace),
            write_tool_evidence=write_tool_evidence,
        )

    def test_all_assertions_pass(self, tmp_path) -> None:
        (tmp_path / "README.md").write_text("# hi\n", encoding="utf-8")
        satisfied, evidence = self._evaluate(
            {"acceptance_criteria": ["包含运行说明", "verify ./readme.md exists"]},
            tmp_path,
        )
        assert satisfied is True
        assert evidence == {"checked": 1, "passed": ["readme.md"], "missing": []}

    def test_missing_path_not_exempted(self, tmp_path) -> None:
        satisfied, evidence = self._evaluate(
            {"acceptance_criteria": ["verify ./readme.md exists"]},
            tmp_path,
        )
        assert satisfied is False
        assert evidence["missing"] == ["readme.md"]

    def test_no_machine_assertions_no_exemption(self, tmp_path) -> None:
        (tmp_path / "README.md").write_text("# hi\n", encoding="utf-8")
        satisfied, evidence = self._evaluate(
            {"acceptance_criteria": ["README.md 存在于工作区根"]},
            tmp_path,
        )
        assert satisfied is False
        assert evidence["checked"] == 0

    def test_requires_write_tool_evidence(self, tmp_path) -> None:
        (tmp_path / "README.md").write_text("# hi\n", encoding="utf-8")
        satisfied, _ = self._evaluate(
            {"acceptance_criteria": ["verify ./readme.md exists"]},
            tmp_path,
            write_tool_evidence=False,
        )
        assert satisfied is False

    def test_nested_path_case_insensitive(self, tmp_path) -> None:
        (tmp_path / "Docs").mkdir()
        (tmp_path / "Docs" / "Guide.md").write_text("g\n", encoding="utf-8")
        satisfied, evidence = self._evaluate(
            {"acceptance_criteria": ["verify docs/guide.md exists"]},
            tmp_path,
        )
        assert satisfied is True
        assert evidence["passed"] == ["docs/guide.md"]

    def test_one_missing_among_many_blocks_exemption(self, tmp_path) -> None:
        (tmp_path / "a.md").write_text("a\n", encoding="utf-8")
        satisfied, evidence = self._evaluate(
            {"acceptance_criteria": ["verify a.md exists", "verify b.md exists"]},
            tmp_path,
        )
        assert satisfied is False
        assert evidence["passed"] == ["a.md"]
        assert evidence["missing"] == ["b.md"]


class TestQualityRepairMissingTargetContract:
    """L2-10 r3 regression: the repair turn rewrote src/main.js (already
    present) instead of creating the missing src/styles.css — the repair
    message itself seeded the wrong target by listing changed files as paths."""

    def test_missing_declared_targets_derived_from_workspace(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _missing_declared_target_files,
        )

        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.js").write_text("x\n", encoding="utf-8")
        (tmp_path / "index.html").write_text("<html></html>\n", encoding="utf-8")
        task = {"target_files": ["index.html", "src/main.js", "src/styles.css", "package.json"]}
        missing = _missing_declared_target_files(task, str(tmp_path))
        assert missing == ["src/styles.css", "package.json"]

    def test_missing_targets_case_insensitive(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _missing_declared_target_files,
        )

        (tmp_path / "README.md").write_text("r\n", encoding="utf-8")
        task = {"target_files": ["readme.md"]}
        assert _missing_declared_target_files(task, str(tmp_path)) == []

    def test_repair_message_names_missing_targets_and_hides_changed_paths(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _build_materialization_quality_repair_message,
        )
        from polaris.cells.roles.kernel.internal.transaction.contract_guards import (
            extract_target_files_from_message,
        )

        message = _build_materialization_quality_repair_message(
            original_message="实现 Markdown 预览器核心文件",
            artifact_quality_errors=["Artifact quality scan failed: declared target file missing 'src/styles.css'"],
            changed_files=["index.html", "package.json", "src/main.js"],
            missing_target_files=["src/styles.css"],
        )
        assert "MISSING TARGET FILES" in message
        assert "src/styles.css" in message
        # Changed files appear only as a count — path-shaped tokens seed the
        # retry target extractor with wrong targets.
        assert "src/main.js" not in message
        assert "3 file(s) were already written" in message
        extracted = extract_target_files_from_message(message)
        assert "src/styles.css" in extracted
        assert "src/main.js" not in extracted

    def test_repair_message_without_missing_block_when_none(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _build_materialization_quality_repair_message,
        )

        message = _build_materialization_quality_repair_message(
            original_message="task",
            artifact_quality_errors=["some error"],
            changed_files=[],
            missing_target_files=[],
        )
        assert "MISSING TARGET FILES" not in message
        assert "0 file(s) were already written" in message


class TestSyntaxRepairDirective:
    """L2-11 r2: the repair turn REWROTE typing.js whole-file and reproduced
    the identical `endTime: null;` slip at escalation-low temperature; only a
    narrow line edit breaks the determinism loop."""

    def test_syntax_error_adds_narrow_edit_directive(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _build_materialization_quality_repair_message,
        )

        message = _build_materialization_quality_repair_message(
            original_message="实现打字测试器",
            artifact_quality_errors=[
                "Artifact quality scan failed: syntax error in typing.js: typing.js:9\n"
                "    endTime: null;\n                 ^\n\nSyntaxError: Unexpected token ';'"
            ],
            changed_files=["typing.js"],
            missing_target_files=[],
        )
        assert "SYNTAX REPAIR DIRECTIVE" in message
        assert "Do NOT rewrite the whole file" in message
        assert "edit_blocks" in message

    def test_no_directive_without_syntax_errors(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _build_materialization_quality_repair_message,
        )

        message = _build_materialization_quality_repair_message(
            original_message="task",
            artifact_quality_errors=["declared target file missing 'readme.md'"],
            changed_files=[],
            missing_target_files=["readme.md"],
        )
        assert "SYNTAX REPAIR DIRECTIVE" not in message


class TestTruncatedFileDirective:
    """L2-11 r6: index.html was whole-file-rewritten three times and every
    copy was output-limit-truncated; only append converges."""

    def test_truncation_error_gets_append_directive(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _build_materialization_quality_repair_message,
        )

        message = _build_materialization_quality_repair_message(
            original_message="构建打字测试器",
            artifact_quality_errors=[
                "Artifact quality scan failed: syntax error in index.html: "
                "truncated/incomplete HTML: missing </html> closing tag; 1 unclosed <script> tag(s)"
            ],
            changed_files=["index.html"],
            missing_target_files=[],
        )
        assert "TRUNCATED FILE DIRECTIVE" in message
        assert "append_to_file" in message
        assert "Do NOT rewrite" in message
        assert "SYNTAX REPAIR DIRECTIVE" not in message

    def test_plain_syntax_error_keeps_narrow_edit_directive(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _build_materialization_quality_repair_message,
        )

        message = _build_materialization_quality_repair_message(
            original_message="task",
            artifact_quality_errors=[
                "Artifact quality scan failed: syntax error in app.js: app.js:9\n"
                "    gfm: true;\n^\nSyntaxError: Unexpected token ';'"
            ],
            changed_files=["app.js"],
            missing_target_files=[],
        )
        assert "SYNTAX REPAIR DIRECTIVE" in message
        assert "TRUNCATED FILE DIRECTIVE" not in message


class TestCollectStepVerifyErrors:
    """写后即查（Fix-9, live I3-r11）: step verify 必须在执行轮内跑进修复梯,
    而不是等 exec→QA→bounce→exec 的市场往返(~30min/圈盲猜)。"""

    @staticmethod
    def _collect(context: Any, workspace: str) -> list[str]:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _collect_step_verify_errors,
        )

        return _collect_step_verify_errors(SimpleNamespace(workspace=workspace), context)

    def test_non_step_context_is_noop(self, tmp_path: Any) -> None:
        assert self._collect({}, str(tmp_path)) == []
        assert self._collect(None, str(tmp_path)) == []
        assert self._collect({"construction_step": {"target_file": "a.md"}}, str(tmp_path)) == []

    def test_passing_verify_returns_no_errors(self, tmp_path: Any) -> None:
        (tmp_path / "index.html").write_text('<canvas id="game-canvas"></canvas>', encoding="utf-8")
        context = {"construction_step": {"verify": "test -f ./index.html && grep -q 'id=\"game-canvas\"' ./index.html"}}
        assert self._collect(context, str(tmp_path)) == []

    def test_failing_verify_yields_repairable_error(self, tmp_path: Any) -> None:
        (tmp_path / "index.html").write_text('<canvas id="gameCanvas"></canvas>', encoding="utf-8")
        context = {"construction_step": {"verify": "grep -q 'id=\"game-canvas\"' ./index.html"}}
        errors = self._collect(context, str(tmp_path))
        assert len(errors) == 1
        assert "step verify failed" in errors[0]
        assert "game-canvas" in errors[0]

    def test_list_verify_joined(self, tmp_path: Any) -> None:
        (tmp_path / "a.md").write_text("x", encoding="utf-8")
        context = {"construction_step": {"verify": ["test -f ./a.md", "grep -q x ./a.md"]}}
        assert self._collect(context, str(tmp_path)) == []

    def test_failure_names_first_failing_clause(self, tmp_path: Any) -> None:
        """Fix-10 (live I3-r12): S2 passed 7/8 clauses but teaching carried only
        the whole command + exit 1 — the model could not tell WHICH check failed."""
        (tmp_path / "style.css").write_text("#game {}\n" * 200, encoding="utf-8")
        context = {
            "construction_step": {
                "verify": (
                    "test -f ./style.css && grep -q '#game' ./style.css && [ \"$(wc -l < ./style.css)\" -le 120 ]"
                )
            }
        }
        errors = self._collect(context, str(tmp_path))
        assert len(errors) == 1
        assert "failing clause [3/3]:" in errors[0]
        assert "wc -l" in errors[0].split("failing clause", 1)[1]

    def test_single_clause_failure_has_no_clause_suffix(self, tmp_path: Any) -> None:
        context = {"construction_step": {"verify": "test -f ./missing.md"}}
        errors = self._collect(context, str(tmp_path))
        assert len(errors) == 1
        assert "failing clause" not in errors[0]

    def test_quoted_and_inside_pattern_aborts_clause_diagnosis(self, tmp_path: Any) -> None:
        """Splitting on ' && ' cuts through the quoted pattern; the sh -n guard
        must abandon diagnosis instead of naming a bogus clause."""
        (tmp_path / "a.txt").write_text("plain\n", encoding="utf-8")
        context = {"construction_step": {"verify": "grep -q 'a && b' ./a.txt && test -f ./a.txt"}}
        errors = self._collect(context, str(tmp_path))
        assert len(errors) == 1
        assert "step verify failed" in errors[0]
        assert "failing clause" not in errors[0]

    def test_state_carrying_chain_aborts_clause_diagnosis(self, tmp_path: Any) -> None:
        """Adversarial review (live repro): a cd/VAR= clause passes sh -n but its
        successors re-run in a fresh shell against the wrong cwd/env — naming
        a wrong clause actively misleads the next attempt."""
        sub = tmp_path / "src"
        sub.mkdir()
        (sub / "app.js").write_text("bar\n", encoding="utf-8")
        for verify in (
            "cd src && test -f app.js && grep -q foo app.js",
            'X=1 && [ "$X" = 1 ] && test -f missing.txt',
            "export V=2 && test -f missing.txt",
        ):
            errors = self._collect({"construction_step": {"verify": verify}}, str(tmp_path))
            assert len(errors) == 1, verify
            assert "failing clause" not in errors[0], verify

    def test_top_level_or_chain_aborts_clause_diagnosis(self, tmp_path: Any) -> None:
        context = {"construction_step": {"verify": "test -f ./a.txt && grep -q x ./a.txt || test -f ./b.txt"}}
        errors = self._collect(context, str(tmp_path))
        assert len(errors) == 1
        assert "failing clause" not in errors[0]

    def test_clause_detail_precedes_full_command_in_message(self, tmp_path: Any) -> None:
        """Teaching channels truncate (step card 240 chars) — the actionable
        clause must come before the potentially long full command."""
        (tmp_path / "style.css").write_text("#game {}\n" * 200, encoding="utf-8")
        verify = 'test -f ./style.css && [ "$(wc -l < ./style.css)" -le 120 ]'
        errors = self._collect({"construction_step": {"verify": verify}}, str(tmp_path))
        assert len(errors) == 1
        assert errors[0].index("failing clause") < errors[0].index("full:")


class TestSingleFileStepTarget:
    """对抗复核 C-fix: 钉靶步轮的质量门只裁决该步拥有的文件 — package.json 等
    其他文件的旧垃圾会要求被钉死的写工具做不到的修复, 反弹环永不收敛。"""

    @staticmethod
    def _target(source: Any) -> str:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _single_file_step_target,
        )

        return _single_file_step_target(source)

    def test_clean_step_target_is_extracted(self) -> None:
        assert self._target({"construction_step": {"target_file": "./style.css"}}) == "style.css"

    def test_malformed_targets_are_refused(self) -> None:
        for target in ("src/*.js", "a.js, b.js", "/etc/passwd", "../x.js"):
            assert self._target({"construction_step": {"target_file": target}}) == "", target

    def test_non_step_sources_are_refused(self) -> None:
        assert self._target(None) == ""
        assert self._target({}) == ""
        assert self._target({"construction_step": {}}) == ""

    def test_quality_scan_is_scoped_to_step_target(self, tmp_path: Any, monkeypatch: Any) -> None:
        from polaris.cells.roles.adapters.internal.director import execute_method

        seen: dict[str, Any] = {}

        def _capture(workspace: str, relative_paths: list[str] | None = None) -> list[str]:
            seen["paths"] = list(relative_paths or [])
            return []

        monkeypatch.setattr(execute_method, "scan_workspace_artifact_quality", _capture)
        adapter = SimpleNamespace(workspace=str(tmp_path))
        context = {"construction_step": {"target_file": "style.css"}}
        execute_method._collect_materialization_quality_errors(
            adapter,
            task={"task_id": "PM-1-S2"},
            all_affected_files=["style.css", "main.js", "package.json"],
            workspace_name="ws",
            context=context,
        )
        assert seen["paths"] == ["style.css"]

    def test_non_step_turn_keeps_full_scan_scope(self, tmp_path: Any, monkeypatch: Any) -> None:
        from polaris.cells.roles.adapters.internal.director import execute_method

        seen: dict[str, Any] = {}

        def _capture(workspace: str, relative_paths: list[str] | None = None) -> list[str]:
            seen["paths"] = list(relative_paths or [])
            return []

        monkeypatch.setattr(execute_method, "scan_workspace_artifact_quality", _capture)
        adapter = SimpleNamespace(workspace=str(tmp_path))
        execute_method._collect_materialization_quality_errors(
            adapter,
            task={"task_id": "T-1"},
            all_affected_files=["a.js", "b.js"],
            workspace_name="ws",
            context={"run_id": "r"},
        )
        assert set(seen["paths"]) >= {"a.js", "b.js"}
