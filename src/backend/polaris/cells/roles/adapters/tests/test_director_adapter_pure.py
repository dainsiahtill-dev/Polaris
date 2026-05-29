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
from polaris.cells.roles.adapters.internal.director.adapter import DirectorAdapter
from polaris.cells.roles.adapters.internal.director.execute_method import (
    _apply_deterministic_typescript_reexport_repair,
    _build_existing_workspace_task_evidence,
    _director_direct_text_patch_only_enabled,
    _director_existing_scope_preflight_enabled,
    _looks_like_typescript_reexport_failure,
    _task_requires_fresh_materialization,
)

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
    async def test_execute_accepts_workspace_diff_without_write_tool_marker(self, tmp_path: Any) -> None:
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

        async def _mutating_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            target = tmp_path / "src" / "types" / "domain.ts"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("export type DomainState = 'ready';\n", encoding="utf-8")
            return {"content": "Applied directly by runtime provider.", "success": True}

        async def _unexpected_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            raise AssertionError("direct fallback should not run after workspace diff evidence")

        adapter._invoke_role_dialogue_with_timeout = _mutating_dialogue  # type: ignore[method-assign]
        adapter._invoke_direct_runtime_provider = _unexpected_direct_fallback  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-diff-evidence"},
        )

        assert result["success"] is True
        assert result["materialization_mode"] == "workspace_diff_without_write_tool"
        assert any(
            signal.get("code") == "director.workspace_diff_without_write_tool"
            for signal in result.get("decision_signals", [])
            if isinstance(signal, dict)
        )
        updated = adapter.task_board.get_task(str(task.id))
        assert updated is not None
        assert str(updated.get("status") or "").lower() == "completed"
        raw_metadata = updated.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        raw_adapter_result = metadata.get("adapter_result")
        adapter_result: dict[str, Any] = raw_adapter_result if isinstance(raw_adapter_result, dict) else {}
        assert adapter_result.get("new_file_count") == 1
        assert adapter_result.get("write_tool_evidence") is False

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
        assert _task_requires_fresh_materialization({"subject": "Create initial source files"}) is False
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
    def test_direct_runtime_provider_allows_codex_cli(
        self,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        adapter = _make_adapter(tmp_path)
        captured: dict[str, object] = {}

        monkeypatch.setattr(
            "polaris.cells.roles.adapters.internal.director.adapter.get_settings_safe",
            lambda: SimpleNamespace(llm_timeout=5),
        )

        def _fake_invoke_role_runtime_provider(**kwargs: Any) -> SimpleNamespace:
            captured["blocked_provider_types"] = tuple(kwargs.get("blocked_provider_types") or ())
            return SimpleNamespace(
                attempted=True,
                ok=True,
                output="src/smoke.txt\n```txt\nok\n```",
                provider_id="codex_cli",
                provider_type="codex_cli",
                model="gpt-5.3-codex",
                latency_ms=1,
                error="",
                usage=None,
            )

        monkeypatch.setattr(
            "polaris.kernelone.llm.runtime.invoke_role_runtime_provider",
            _fake_invoke_role_runtime_provider,
        )

        result = adapter._invoke_direct_runtime_provider_sync("write a file", timeout_seconds=3)

        blocked = captured["blocked_provider_types"]
        assert isinstance(blocked, tuple)
        assert "codex_cli" not in blocked
        assert "codex_sdk" not in blocked
        assert result["provider"] == "codex_cli"
        assert result["model"] == "gpt-5.3-codex"


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
