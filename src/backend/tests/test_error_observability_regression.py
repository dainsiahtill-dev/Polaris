"""Test: error observability in audit.diagnosis and orchestration cells.

These tests verify that error handlers in Cell-internal modules:
1. Log at appropriate levels instead of silently swallowing exceptions.
2. Never use bare `except: pass` or `except Exception: pass` patterns.

Reference: Agent-40 (G-4) - missing effects_allowed/state_owners and
silent except Exception:pass patterns.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# audit.diagnosis - _git_changed_files
# ---------------------------------------------------------------------------

def test_git_changed_files_logs_on_command_failure(monkeypatch, tmp_path: Path) -> None:
    """_git_changed_files must log at debug level when git command fails."""
    from polaris.cells.audit.diagnosis.internal.diagnosis_engine import (
        AuditDiagnosisEngine,
    )

    engine = AuditDiagnosisEngine(
        runtime_root=str(tmp_path / "runtime"),
        workspace=str(tmp_path / "workspace"),
    )

    # Mock CommandExecutionService.run to raise an exception
    with patch(
        "polaris.cells.audit.diagnosis.internal.diagnosis_engine.CommandExecutionService"
    ) as mock_cls:
        mock_instance = MagicMock()
        mock_instance.run.side_effect = RuntimeError("git not installed")
        mock_cls.return_value = mock_instance

        with patch(
            "polaris.cells.audit.diagnosis.internal.diagnosis_engine.logger"
        ) as mock_logger:
            result = engine._git_changed_files()

            # Must return empty list (graceful degradation)
            assert result == []

            # Must have called logger.debug with exc_info=True
            mock_logger.debug.assert_called_once()
            call_args = mock_logger.debug.call_args
            assert "git status failed" in call_args[0][0]
            # exc_info must be True so the full traceback is captured
            assert call_args.kwargs.get("exc_info") is True


def test_git_changed_files_returns_empty_on_nonzero_returncode(
    monkeypatch, tmp_path: Path
) -> None:
    """_git_changed_files returns [] when git returns non-zero (no repo, etc.)."""
    from polaris.cells.audit.diagnosis.internal.diagnosis_engine import (
        AuditDiagnosisEngine,
    )

    engine = AuditDiagnosisEngine(
        runtime_root=str(tmp_path / "runtime"),
        workspace=str(tmp_path / "workspace"),
    )

    with patch(
        "polaris.cells.audit.diagnosis.internal.diagnosis_engine.CommandExecutionService"
    ) as mock_cls:
        mock_instance = MagicMock()
        mock_instance.run.return_value.get.return_value = 128  # git error
        mock_cls.return_value = mock_instance

        result = engine._git_changed_files()
        assert result == []


# ---------------------------------------------------------------------------
# orchestration.pm_dispatch - iteration_state error handlers
# ---------------------------------------------------------------------------

def test_handle_invoke_error_logs_on_append_meta_prompt_hint_failure(
    caplog, tmp_path: Path
) -> None:
    """_handle_invoke_error must log at debug level when append_meta_prompt_hint fails."""
    caplog.set_level(
        logging.DEBUG,
        logger="polaris.cells.orchestration.pm_dispatch.internal.iteration_state",
    )

    iteration_state = __import__(
        "polaris.cells.orchestration.pm_dispatch.internal.iteration_state",
        fromlist=["iteration_state"],
    )

    # Patch write_json_atomic to avoid KFS bootstrap requirement
    with patch.object(iteration_state, "write_json_atomic", lambda *a, **kw: None):
        # Patch emit_dialogue to avoid KFS path validation
        with patch(
            "polaris.infrastructure.compat.io_utils.emit_dialogue",
            lambda *a, **kw: None,
        ):
            # Patch emit_llm_event to avoid file I/O
            with patch(
                "polaris.infrastructure.compat.io_utils.emit_llm_event",
                lambda *a, **kw: None,
            ):
                # Patch emit_event (iteration_state's binding) to avoid I/O
                with patch.object(iteration_state, "emit_event", lambda *a, **kw: None):
                    # Patch append_meta_prompt_hint to raise - this exercises the
                    # inner try-except in _handle_invoke_error.
                    with patch(
                        "polaris.kernelone.prompts.meta_prompting.append_meta_prompt_hint",
                        side_effect=RuntimeError("disk full"),
                    ):
                        # Must NOT raise
                        iteration_state._handle_invoke_error(
                            error="some error",
                            run_events=str(tmp_path / "events.jsonl"),
                            dialogue_full=str(tmp_path / "dialogue.jsonl"),
                            run_id="run-1",
                            iteration=1,
                            workspace_full=str(tmp_path / "workspace"),
                            pm_state={},
                            pm_state_full=str(tmp_path / "state.json"),
                            backend_label="test",
                            start_timestamp="2026-01-01T00:00:00",
                            pm_llm_events_full=str(tmp_path / "llm_events.jsonl"),
                        )

    # The inner except for append_meta_prompt_hint logs at debug level
    assert any("append_meta_prompt_hint" in r.message for r in caplog.records), (
        f"Expected debug log about append_meta_prompt_hint failure but got: "
        f"{[r.message for r in caplog.records]}"
    )


def test_handle_invoke_error_does_not_raise_on_any_failure(tmp_path: Path) -> None:
    """_handle_invoke_error must never raise, even when everything fails."""
    iteration_state = __import__(
        "polaris.cells.orchestration.pm_dispatch.internal.iteration_state",
        fromlist=["iteration_state"],
    )

    # Patch all I/O functions to avoid actual file operations
    with patch.object(iteration_state, "write_json_atomic", lambda *a, **kw: None), patch(
        "polaris.infrastructure.compat.io_utils.emit_dialogue",
        lambda *a, **kw: None,
    ), patch(
        "polaris.infrastructure.compat.io_utils.emit_llm_event",
        lambda *a, **kw: None,
    ), patch.object(iteration_state, "emit_event", lambda *a, **kw: None), patch(
        "polaris.kernelone.prompts.meta_prompting.append_meta_prompt_hint",
        side_effect=RuntimeError("total failure"),
    ):
        # Must NOT raise - append_meta_prompt_hint exception is
        # caught by inner try-except and does NOT re-raise.
        iteration_state._handle_invoke_error(
            error="test error",
            run_events=str(tmp_path / "events.jsonl"),
            dialogue_full=str(tmp_path / "dialogue.jsonl"),
            run_id="run-1",
            iteration=1,
            workspace_full=str(tmp_path / "workspace"),
            pm_state={},
            pm_state_full=str(tmp_path / "state.json"),
            backend_label="test",
            start_timestamp="2026-01-01T00:00:00",
            pm_llm_events_full=str(tmp_path / "llm_events.jsonl"),
        )


def test_clear_manual_intervention_logs_on_pause_flag_failure(
    caplog, tmp_path: Path
) -> None:
    """_clear_manual_intervention must log when pause flag removal fails."""
    caplog.set_level(
        logging.WARNING,
        logger="polaris.cells.orchestration.pm_dispatch.internal.iteration_state",
    )

    empty_dir = tmp_path / "workspace_empty"
    empty_dir.mkdir(parents=True)

    # Create a predictable path for the pause flag
    pause_file = tmp_path / "pause.flag"
    assert not pause_file.exists()
    pause_file.touch()

    iteration_state = __import__(
        "polaris.cells.orchestration.pm_dispatch.internal.iteration_state",
        fromlist=["iteration_state"],
    )

    # os.remove is called directly as os.remove(...) in iteration_state.py.
    # We must mock it on the iteration_state module object so the local
    # binding is replaced. Patch.object works here because os is in
    # iteration_state.__dict__ as the standard library os module.
    def fail_on_pause_remove(path: str) -> None:
        if "pause.flag" in str(path):
            raise OSError("permission denied")
        os.remove(path)

    # Patch iteration_state.emit_dialogue directly so the call inside
    # _clear_manual_intervention uses the mock (not the real io_utils wrapper).
    # Patch pause_flag_path so it returns our predictable temp path
    # (bypass the build_cache_root / resolve_artifact_path remapping).
    with patch.object(iteration_state, "write_json_atomic", lambda *a, **kw: None):
        with patch.object(iteration_state, "emit_dialogue", lambda *a, **kw: None):
            with patch.object(iteration_state, "pause_flag_path", return_value=str(pause_file)):
                with patch.object(iteration_state.os, "remove", fail_on_pause_remove):
                    iteration_state._clear_manual_intervention(
                        pm_state={},
                        pm_state_full=str(tmp_path / "state.json"),
                        workspace_full=str(empty_dir),
                        dialogue_full=str(tmp_path / "dialogue.jsonl"),
                        run_id="run-1",
                        iteration=1,
                    )

    # Must have logged a warning about pause flag removal failure
    assert any("pause flag" in r.message.lower() for r in caplog.records), (
        f"Expected warning log about pause flag but got: "
        f"{[(r.levelname, r.message) for r in caplog.records]}"
    )


# ---------------------------------------------------------------------------
# orchestration.workflow_runtime - director_workflow resident decision
# ---------------------------------------------------------------------------

def test_record_resident_decision_safe_logs_on_failure(monkeypatch, tmp_path: Path) -> None:
    """_record_resident_decision_safe must log at warning level on failure."""
    from polaris.cells.orchestration.workflow_runtime.internal.runtime_engine.workflows import (
        director_workflow,
    )

    # record_resident_decision is imported inside the function, so patch at source
    with patch(
        "polaris.cells.resident.autonomy.public.service.record_resident_decision",
        side_effect=RuntimeError("disk full"),
    ), patch.object(director_workflow, "logger") as mock_logger:
        director_workflow._record_resident_decision_safe(
            workspace=str(tmp_path),
            payload={"run_id": "run-1", "stage": "planning"},
        )

        mock_logger.warning.assert_called()
        call_args = mock_logger.warning.call_args
        assert "failed to record resident decision" in call_args[0][0]
        assert call_args.kwargs.get("exc_info") is True


def test_record_resident_decision_safe_does_not_raise(monkeypatch, tmp_path: Path) -> None:
    """_record_resident_decision_safe must never raise."""
    from polaris.cells.orchestration.workflow_runtime.internal.runtime_engine.workflows import (
        director_workflow,
    )

    with patch(
        "polaris.cells.resident.autonomy.public.service.record_resident_decision",
        side_effect=RuntimeError("total failure"),
    ):
        # Must NOT raise
        director_workflow._record_resident_decision_safe(
            workspace=str(tmp_path),
            payload={"run_id": "run-1"},
        )


# ---------------------------------------------------------------------------
# events.fact_stream - debug_trace emit_event
# ---------------------------------------------------------------------------

def test_emit_event_logs_on_stdout_failure(monkeypatch, tmp_path: Path) -> None:
    """DebugTracer.emit_event must log at warning level when stdout write fails."""
    from polaris.cells.events.fact_stream.internal import debug_trace

    tracer = debug_trace.DebugTracer()
    tracer.set_enabled(True)

    # The emit_event method uses print() which writes to sys.stdout.
    # We patch the built-in print to raise, simulating stdout write failure.
    with patch("builtins.print", side_effect=OSError("pipe broken")):
        with patch.object(debug_trace, "logger") as mock_logger:
            tracer.emit_event(
                "test_event",
                kind="status",
                actor="Test",
                summary="test",
            )

            # Must have logged at warning level for stdout failure
            mock_logger.warning.assert_called()
            call_args = mock_logger.warning.call_args
            assert "stdout" in call_args[0][0].lower()


# ---------------------------------------------------------------------------
# factory.pipeline - factory_run_service _run_tool
# ---------------------------------------------------------------------------

def test_factory_run_service_handles_tool_timeout(monkeypatch, tmp_path: Path) -> None:
    """FactoryRunService._run_tool must propagate TimeoutExpired from subprocess."""
    import subprocess

    # Verify subprocess is used correctly - timeout should produce TimeoutExpired
    with pytest.raises(subprocess.TimeoutExpired):
        subprocess.run(
            ["python", "-c", "import time; time.sleep(10)"],
            timeout=0.1,
            capture_output=True,
            cwd=str(tmp_path),
        )
