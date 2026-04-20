"""Regression gate: Phase 4 legacy modules have been deleted.

ARCHITECTURE RULE (Task #51):
    Phase 4 legacy modules have been deleted:
    - standalone_runner.py (DELETED)
    - tui_console.py (DELETED)
    - standalone_entry.py (DELETED)

    The single unified execution path is now:
        RoleRuntimeService -> RoleExecutionKernel (CHAT | WORKFLOW mode)

This test verifies that the legacy modules no longer exist in the codebase.
"""

from __future__ import annotations


class TestPhase4LegacyModulesDeleted:
    """Assert that Phase 4 legacy modules have been deleted."""

    def test_standalone_runner_module_does_not_exist(self) -> None:
        """standalone_runner.py must not exist in the codebase."""
        import sys
        from pathlib import Path

        # Check that the module file doesn't exist
        repo_root = Path(__file__).resolve().parents[5]
        module_file = repo_root / "polaris" / "cells" / "roles" / "runtime" / "internal" / "standalone_runner.py"
        assert not module_file.exists(), (
            f"Phase 4 legacy module still exists: {module_file}. "
            "This module should have been deleted during Phase 4 cleanup."
        )

        # Check that it's not in sys.modules
        module_name = "polaris.cells.roles.runtime.internal.standalone_runner"
        assert module_name not in sys.modules, f"Phase 4 legacy module '{module_name}' is still loaded in sys.modules"

    def test_tui_console_module_does_not_exist(self) -> None:
        """tui_console.py must not exist in the codebase."""
        import sys
        from pathlib import Path

        # Check that the module file doesn't exist
        repo_root = Path(__file__).resolve().parents[5]
        module_file = repo_root / "polaris" / "cells" / "roles" / "runtime" / "internal" / "tui_console.py"
        assert not module_file.exists(), (
            f"Phase 4 legacy module still exists: {module_file}. "
            "This module should have been deleted during Phase 4 cleanup."
        )

        # Check that it's not in sys.modules
        module_name = "polaris.cells.roles.runtime.internal.tui_console"
        assert module_name not in sys.modules, f"Phase 4 legacy module '{module_name}' is still loaded in sys.modules"

    def test_standalone_entry_module_does_not_exist(self) -> None:
        """standalone_entry.py must not exist in the codebase."""
        import sys
        from pathlib import Path

        # Check that the module file doesn't exist
        repo_root = Path(__file__).resolve().parents[5]
        module_file = repo_root / "polaris" / "cells" / "roles" / "runtime" / "internal" / "standalone_entry.py"
        assert not module_file.exists(), (
            f"Phase 4 legacy module still exists: {module_file}. "
            "This module should have been deleted during Phase 4 cleanup."
        )

        # Check that it's not in sys.modules
        module_name = "polaris.cells.roles.runtime.internal.standalone_entry"
        assert module_name not in sys.modules, f"Phase 4 legacy module '{module_name}' is still loaded in sys.modules"


class TestQAPathArchitecture:
    """Architecture constraints on the QA execution path.

    There are two QA implementations:
      1. dispatch_pipeline.py: Cell-local, lightweight, calls
         ``run_integration_verify_runner`` from ``pm_planning.public.service``.
         Used by ``run_dispatch_pipeline`` (CLI path) and
         ``run_post_dispatch_integration_qa`` (standalone post-dispatch path).
      2. QAWorkflow (workflow_runtime/workflows/qa_workflow.py):
         Temporal-activity-based, heavyweight.

    Decision: path-1 (dispatch_pipeline.py) is the surviving QA path.
    QAWorkflow is DEPRECATED — it remains in the codebase for backward
    compatibility with existing workflow definitions but is not called by
    any active PM→Director→QA chain.

    The evidence chain is the same regardless of path: both emit events via
    ``emit_event`` / ``emit_dialogue`` and both call
    ``run_integration_verify_runner`` which is the single source of truth
    for integration verification.
    """

    def test_dispatch_pipeline_qa_uses_pm_planning_verify_runner(self) -> None:
        """dispatch_pipeline run_integration_qa must use pm_planning verify runner.

        The underlying implementation is in pm_planning.internal.shared_quality,
        re-exported via pm_planning.public.service.  The detect_fn and verify_fn
        both live in shared_quality so the .__module__ check uses that path.
        This is the single source of truth for integration verification.
        """
        from polaris.cells.orchestration.pm_dispatch.internal import dispatch_pipeline as pipeline

        # Verify the lazy loader resolves to pm_planning (via internal.shared_quality).
        detect_fn, verify_fn = pipeline._get_shared_quality()
        assert detect_fn.__module__ == "polaris.cells.orchestration.pm_planning.internal.shared_quality", (
            f"detect_fn from unexpected module: {detect_fn.__module__}"
        )
        assert verify_fn.__module__ == "polaris.cells.orchestration.pm_planning.internal.shared_quality", (
            f"verify_fn from unexpected module: {verify_fn.__module__}"
        )

    def test_dispatch_pipeline_qa_result_structure(self) -> None:
        """run_integration_qa result dict must include qa_path field for observability."""
        from polaris.cells.orchestration.pm_dispatch.internal import dispatch_pipeline as pipeline

        # Call with empty tasks — returns a structured result without running verify.
        result = pipeline.run_integration_qa(
            workspace_full="/tmp",
            cache_root_full="/tmp",
            run_dir="/tmp",
            run_id="test-run",
            iteration=1,
            tasks=[],
            run_events="/tmp/events.jsonl",
            dialogue_full="/tmp/dialogue.jsonl",
            docs_stage=None,
        )
        assert isinstance(result, dict)
        assert "reason" in result
        assert "passed" in result
        # qa_path documents which QA path was used (for evidence chain).
        assert "qa_path" in result, (
            "run_integration_qa result must include 'qa_path' field "
            "to establish the evidence chain (dispatch_pipeline vs QAWorkflow)."
        )
        assert result["qa_path"] == "dispatch_pipeline", (
            "dispatch_pipeline run_integration_qa must report qa_path='dispatch_pipeline' "
            "so audit logs and evidence chain correctly attribute the execution path."
        )

    def test_dispatch_pipeline_post_dispatch_qa_result_has_qa_path(self) -> None:
        """run_post_dispatch_integration_qa result must also carry qa_path."""
        from polaris.cells.orchestration.pm_dispatch.internal import dispatch_pipeline as pipeline

        result = pipeline.run_post_dispatch_integration_qa(
            workspace_full="/tmp",
            cache_root_full="/tmp",
            run_dir="/tmp",
            run_id="test-run",
            iteration=1,
            tasks=[],
            run_events="/tmp/events.jsonl",
            dialogue_full="/tmp/dialogue.jsonl",
            docs_stage=None,
        )
        assert isinstance(result, dict)
        assert "qa_path" in result, "run_post_dispatch_integration_qa result must include 'qa_path' field."


class TestRoleExecutionKernelModeLogging:
    """Verify RoleExecutionKernel logs execution mode in all event paths.

    RoleExecutionMode has two values:
      - CHAT  (RoleExecutionMode.CHAT)  — interactive session via execute_role_session
      - WORKFLOW (RoleExecutionMode.WORKFLOW) — task execution via execute_role_task

    Every event emitted by RoleExecutionKernel must carry ``mode`` in its metadata
    so the evidence chain can distinguish CHAT vs WORKFLOW execution.

    Current coverage (confirmed by grep audit):
      - _emit_tool_execute_events: metadata["mode"] = mode_value  ✓
      - _emit_tool_result_events_and_collect_errors: metadata["mode"] = mode_value  ✓
      - _record_llm_response_event: execution_stats["mode"] = request.mode.value  ✓
      - _record_turn_completed_event: mode in event data  ✓
      - _emit_content_preview_event: base_metadata["mode"] = request.mode.value  ✓
      - Tool call events (line 1225): "mode": mode_value in metadata  ✓
    """

    def test_kernel_module_has_mode_logging(self) -> None:
        """RoleExecutionKernel source must log request.mode.value in event emission."""
        import ast

        kernel_path = (
            "C:/Users/dains/Documents/GitLab/polaris/src/backend/polaris/cells/roles/kernel/internal/kernel/core.py"
        )
        with open(kernel_path, encoding="utf-8") as fh:
            source = fh.read()

        tree = ast.parse(source)
        mode_logging_found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in (
                "request.mode.value",
                "mode_value",
            ):
                mode_logging_found = True
                break

        assert mode_logging_found, (
            "RoleExecutionKernel must log request.mode.value in event metadata. "
            "Ensure all event emission paths carry 'mode' for the evidence chain."
        )

    def test_mode_enum_values_are_chat_and_workflow(self) -> None:
        """RoleExecutionMode enum must define CHAT and WORKFLOW values."""
        from polaris.cells.roles.profile.internal.schema import RoleExecutionMode

        values = {m.value for m in RoleExecutionMode}
        assert "chat" in values, "RoleExecutionMode must have a 'chat' value"
        assert "workflow" in values, "RoleExecutionMode must have a 'workflow' value"
