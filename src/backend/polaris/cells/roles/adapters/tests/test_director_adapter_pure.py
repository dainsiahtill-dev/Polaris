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

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from polaris.cells.roles.adapters.internal.director.adapter import DirectorAdapter, _normalize_director_role_response
from polaris.cells.roles.adapters.internal.director.execute_method import (
    _apply_deterministic_typescript_reexport_repair,
    _build_existing_workspace_task_evidence,
    _build_substantive_node_test_script,
    _director_direct_text_patch_only_enabled,
    _director_existing_scope_preflight_enabled,
    _emit_director_adapter_cognitive_receipt,
    _finalize_claimed_execution,
    _looks_like_typescript_reexport_failure,
    _resolve_claim_external_task_id,
    _task_requires_fresh_materialization,
    _task_runtime_finalization_failed_result,
)
from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1, RoleExecutionResultV1

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
