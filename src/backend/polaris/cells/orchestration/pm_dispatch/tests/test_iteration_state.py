"""Unit tests for orchestration.pm_dispatch internal iteration_state.

Tests the pure/isolatable helpers and state-mutation functions that don't
require full infrastructure: _clear_manual_intervention (pause-flag),
record_stop, handle_spin_guard, and finalize_iteration (with mocks).
"""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

from polaris.cells.orchestration.pm_dispatch.internal.iteration_state import (
    _clear_manual_intervention,
    _handle_spin_guard,
    _record_stop,
    clear_manual_intervention,
    finalize_iteration,
    handle_spin_guard,
    record_stop,
)

# ---------------------------------------------------------------------------
# _handle_spin_guard
# ---------------------------------------------------------------------------


class TestHandleSpinGuard:
    def test_appends_to_report(self, tmp_path) -> None:
        report_path = tmp_path / "pm_report.md"
        pm_state: dict = {"pm_no_progress_count": 2}
        args = argparse.Namespace(max_spin_rounds=5)

        with (
            patch("polaris.cells.orchestration.pm_dispatch.internal.iteration_state.emit_event"),
            patch("polaris.cells.orchestration.pm_dispatch.internal.iteration_state.emit_dialogue"),
        ):
            result = _handle_spin_guard(
                pm_state=pm_state,
                reason="no progress",
                pm_report_full=str(report_path),
                run_events=str(tmp_path / "events.jsonl"),
                dialogue_full=str(tmp_path / "dialogue.jsonl"),
                run_id="run-1",
                iteration=3,
                args=args,
            )

        assert result is True
        content = report_path.read_text(encoding="utf-8")
        assert "PM_SPIN_GUARD_ACTIVE" in content
        assert "no progress" in content

    def test_emits_events(self, tmp_path) -> None:
        report_path = tmp_path / "pm_report.md"
        pm_state: dict = {}
        args = argparse.Namespace(max_spin_rounds=3)
        mock_emit = MagicMock()
        mock_dialogue = MagicMock()

        with (
            patch("polaris.cells.orchestration.pm_dispatch.internal.iteration_state.emit_event", mock_emit),
            patch("polaris.cells.orchestration.pm_dispatch.internal.iteration_state.emit_dialogue", mock_dialogue),
        ):
            _handle_spin_guard(
                pm_state=pm_state,
                reason="spin detected",
                pm_report_full=str(report_path),
                run_events=str(tmp_path / "events.jsonl"),
                dialogue_full=str(tmp_path / "dialogue.jsonl"),
                run_id="run-2",
                iteration=1,
                args=args,
            )

        mock_emit.assert_called_once()
        call_kwargs = mock_emit.call_args.kwargs
        assert call_kwargs["name"] == "spin_guard"
        assert call_kwargs["ok"] is False
        mock_dialogue.assert_called_once()


# ---------------------------------------------------------------------------
# handle_spin_guard (public wrapper)
# ---------------------------------------------------------------------------


class TestHandleSpinGuardPublicWrapper:
    def test_delegates_to_internal(self, tmp_path) -> None:
        report_path = tmp_path / "pm_report.md"
        pm_state: dict = {}
        args = argparse.Namespace(max_spin_rounds=1)

        with patch(
            "polaris.cells.orchestration.pm_dispatch.internal.iteration_state._handle_spin_guard"
        ) as mock_internal:
            mock_internal.return_value = True
            result = handle_spin_guard(
                pm_state=pm_state,
                reason="test",
                pm_report_full=str(report_path),
                run_events=str(tmp_path / "events.jsonl"),
                dialogue_full=str(tmp_path / "dialogue.jsonl"),
                run_id="run-3",
                iteration=2,
                args=args,
            )

        assert result is True
        mock_internal.assert_called_once()


# ---------------------------------------------------------------------------
# _record_stop
# ---------------------------------------------------------------------------


class TestRecordStop:
    def test_appends_to_report(self, tmp_path) -> None:
        report_path = tmp_path / "pm_report.md"
        pm_state: dict = {"pm_iteration": 0}
        state_path = tmp_path / "state.json"

        with patch("polaris.cells.orchestration.pm_dispatch.internal.iteration_state.write_json_atomic") as mock_write:
            _record_stop(
                pm_report_full=str(report_path),
                timestamp="2026-03-23T10:00:00Z",
                iteration=5,
                pm_state=pm_state,
                pm_state_full=str(state_path),
                exit_code=0,
            )

        assert report_path.exists()
        content = report_path.read_text(encoding="utf-8")
        assert "iteration 5" in content
        assert "exit code 0" in content
        mock_write.assert_called_once()

    def test_sets_pm_iteration_on_state(self, tmp_path) -> None:
        report_path = tmp_path / "pm_report.md"
        state_path = tmp_path / "state.json"
        pm_state: dict = {}

        with patch("polaris.cells.orchestration.pm_dispatch.internal.iteration_state.write_json_atomic"):
            _record_stop(
                pm_report_full=str(report_path),
                timestamp="2026-03-23T10:00:00Z",
                iteration=7,
                pm_state=pm_state,
                pm_state_full=str(state_path),
                exit_code=1,
            )

        assert pm_state["pm_iteration"] == 7
        assert pm_state["last_updated_ts"] == "2026-03-23T10:00:00Z"


# ---------------------------------------------------------------------------
# record_stop (public wrapper)
# ---------------------------------------------------------------------------


class TestRecordStopPublicWrapper:
    def test_delegates_to_internal(self, tmp_path) -> None:
        report_path = tmp_path / "pm_report.md"
        state_path = tmp_path / "state.json"
        pm_state: dict = {}

        with patch("polaris.cells.orchestration.pm_dispatch.internal.iteration_state._record_stop") as mock_internal:
            record_stop(
                pm_report_full=str(report_path),
                timestamp="2026-03-23T10:00:00Z",
                iteration=1,
                pm_state=pm_state,
                pm_state_full=str(state_path),
                exit_code=0,
            )

        mock_internal.assert_called_once()


# ---------------------------------------------------------------------------
# _clear_manual_intervention
# ---------------------------------------------------------------------------


class TestClearManualIntervention:
    def test_removes_manual_intervention_flags(self, tmp_path) -> None:
        state_path = tmp_path / "pm_state.json"
        pm_state = {
            "awaiting_manual_intervention": True,
            "awaiting_manual_intervention_since": "2026-03-22T00:00:00Z",
            "manual_intervention_reason_code": "spin_guard",
            "manual_intervention_detail": "too many retries",
        }

        with (
            patch("polaris.cells.orchestration.pm_dispatch.internal.iteration_state.write_json_atomic"),
            patch("polaris.cells.orchestration.pm_dispatch.internal.iteration_state.emit_dialogue"),
            patch(
                "polaris.cells.orchestration.pm_dispatch.internal.iteration_state.pause_flag_path",
                return_value=str(tmp_path / "pause.flag"),
            ),
            patch(
                "polaris.cells.orchestration.pm_dispatch.internal.iteration_state.os.path.exists",
                return_value=False,
            ),
        ):
            _clear_manual_intervention(
                pm_state=pm_state,
                pm_state_full=str(state_path),
                workspace_full=str(tmp_path),
                dialogue_full=str(tmp_path / "dialogue.jsonl"),
                run_id="run-1",
                iteration=1,
            )

        assert pm_state["awaiting_manual_intervention"] is False
        assert "awaiting_manual_intervention_since" not in pm_state
        assert "manual_intervention_reason_code" not in pm_state

    def test_removes_pause_flag_file(self, tmp_path) -> None:
        pause_file = tmp_path / "pause.flag"
        pause_file.write_text("paused", encoding="utf-8")
        state_path = tmp_path / "pm_state.json"
        pm_state: dict = {"awaiting_manual_intervention": True}

        with (
            patch("polaris.cells.orchestration.pm_dispatch.internal.iteration_state.write_json_atomic"),
            patch("polaris.cells.orchestration.pm_dispatch.internal.iteration_state.emit_dialogue"),
            patch(
                "polaris.cells.orchestration.pm_dispatch.internal.iteration_state.pause_flag_path",
                return_value=str(pause_file),
            ),
        ):
            _clear_manual_intervention(
                pm_state=pm_state,
                pm_state_full=str(state_path),
                workspace_full=str(tmp_path),
                dialogue_full=str(tmp_path / "dialogue.jsonl"),
                run_id="run-2",
                iteration=2,
            )

        assert not pause_file.exists()

    def test_emit_dialogue_called(self, tmp_path) -> None:
        state_path = tmp_path / "pm_state.json"
        pm_state: dict = {"awaiting_manual_intervention": True}
        mock_dialogue = MagicMock()

        with (
            patch("polaris.cells.orchestration.pm_dispatch.internal.iteration_state.write_json_atomic"),
            patch(
                "polaris.cells.orchestration.pm_dispatch.internal.iteration_state.emit_dialogue",
                mock_dialogue,
            ),
            patch(
                "polaris.cells.orchestration.pm_dispatch.internal.iteration_state.pause_flag_path",
                return_value=str(tmp_path / "pause.flag"),
            ),
            patch(
                "polaris.cells.orchestration.pm_dispatch.internal.iteration_state.os.path.exists",
                return_value=False,
            ),
        ):
            _clear_manual_intervention(
                pm_state=pm_state,
                pm_state_full=str(state_path),
                workspace_full=str(tmp_path),
                dialogue_full=str(tmp_path / "dialogue.jsonl"),
                run_id="run-3",
                iteration=3,
            )

        mock_dialogue.assert_called_once()


# ---------------------------------------------------------------------------
# clear_manual_intervention (public wrapper)
# ---------------------------------------------------------------------------


class TestClearManualInterventionPublicWrapper:
    def test_delegates_to_internal(self, tmp_path) -> None:
        state_path = tmp_path / "pm_state.json"
        pm_state: dict = {}

        with patch(
            "polaris.cells.orchestration.pm_dispatch.internal.iteration_state._clear_manual_intervention"
        ) as mock_internal:
            clear_manual_intervention(
                pm_state=pm_state,
                pm_state_full=str(state_path),
                workspace_full=str(tmp_path),
                dialogue_full=str(tmp_path / "dialogue.jsonl"),
                run_id="run-4",
                iteration=1,
            )

        mock_internal.assert_called_once()


# ---------------------------------------------------------------------------
# finalize_iteration (with full infrastructure mocks)
# ---------------------------------------------------------------------------


class TestFinalizeIteration:
    def test_sets_pm_iteration_and_exit_code(self, tmp_path) -> None:
        pm_state: dict = {}
        args = argparse.Namespace()
        context = {
            "pm_state_full": str(tmp_path / "pm_state.json"),
            "pm_history_full": "",
            "normalized": {"tasks": []},
            "start_timestamp": "2026-03-23T00:00:00Z",
            "cache_root_full": str(tmp_path / "cache"),
            "run_id": "run-1",
            "exit_code": 0,
            "backend": "openai",
            "events_seq_start": 0,
            "run_events": str(tmp_path / "events.jsonl"),
            "pm_llm_events_full": str(tmp_path / "llm_events.jsonl"),
        }

        with (
            patch("polaris.cells.orchestration.pm_dispatch.internal.iteration_state.write_json_atomic") as mock_write,
            patch("polaris.kernelone.fs.jsonl.ops.append_jsonl"),
            patch("polaris.cells.orchestration.pm_dispatch.internal.iteration_state.emit_llm_event"),
            patch(
                "polaris.cells.orchestration.pm_dispatch.internal.iteration_state.get_task_signature",
                return_value="sig-abc",
            ),
            patch(
                "polaris.cells.orchestration.pm_dispatch.internal.iteration_state.merge_director_result_into_pm_state"
            ),
            patch(
                "polaris.cells.orchestration.pm_dispatch.internal.iteration_state._get_shangshuling_port",
            ) as mock_get_port,
        ):
            mock_port = MagicMock()
            mock_get_port.return_value = mock_port

            finalize_iteration(
                args=args,
                workspace_full=str(tmp_path),
                iteration=1,
                status="completed",
                state=pm_state,
                context=context,
            )

        assert pm_state["pm_iteration"] == 1
        assert pm_state["last_task_signature"] == "sig-abc"
        assert pm_state["last_task_fingerprint"] == "sig-abc"
        mock_port.archive_task_history.assert_called_once()
        mock_write.assert_called()

    def test_non_dict_state_defaults_to_empty(self, tmp_path) -> None:
        args = argparse.Namespace()
        context = {
            "pm_state_full": str(tmp_path / "pm_state.json"),
            "pm_history_full": "",
            "normalized": {},
            "start_timestamp": "2026-03-23T00:00:00Z",
            "cache_root_full": str(tmp_path / "cache"),
            "run_id": "run-1",
            "exit_code": 0,
            "backend": "",
            "events_seq_start": 0,
            "run_events": "",
            "pm_llm_events_full": "",
        }

        with (
            patch("polaris.cells.orchestration.pm_dispatch.internal.iteration_state.write_json_atomic"),
            patch("polaris.kernelone.fs.jsonl.ops.append_jsonl"),
            patch("polaris.cells.orchestration.pm_dispatch.internal.iteration_state.emit_llm_event"),
            patch(
                "polaris.cells.orchestration.pm_dispatch.internal.iteration_state.get_task_signature", return_value=""
            ),
            patch(
                "polaris.cells.orchestration.pm_dispatch.internal.iteration_state.merge_director_result_into_pm_state"
            ),
            patch(
                "polaris.cells.orchestration.pm_dispatch.internal.iteration_state._get_shangshuling_port",
            ) as mock_get_port,
        ):
            mock_port = MagicMock()
            mock_get_port.return_value = mock_port

            result = finalize_iteration(
                args=args,
                workspace_full=str(tmp_path),
                iteration=0,
                status="completed",
                state="not a dict",  # invalid, should become {}
                context=context,
            )

        assert isinstance(result, dict)

    def test_calls_archive_history(self, tmp_path) -> None:
        pm_state: dict = {}
        args = argparse.Namespace()
        context = {
            "pm_state_full": str(tmp_path / "pm_state.json"),
            "pm_history_full": str(tmp_path / "pm_history.jsonl"),
            "normalized": {"tasks": [{"id": "T01"}]},
            "start_timestamp": "2026-03-23T00:00:00Z",
            "cache_root_full": str(tmp_path / "cache"),
            "run_id": "run-2",
            "exit_code": 1,
            "backend": "claude",
            "events_seq_start": 0,
            "run_events": str(tmp_path / "events.jsonl"),
            "pm_llm_events_full": str(tmp_path / "llm_events.jsonl"),
        }

        with (
            patch("polaris.cells.orchestration.pm_dispatch.internal.iteration_state.write_json_atomic"),
            patch("polaris.kernelone.fs.jsonl.ops.append_jsonl"),
            patch("polaris.cells.orchestration.pm_dispatch.internal.iteration_state.emit_llm_event"),
            patch(
                "polaris.cells.orchestration.pm_dispatch.internal.iteration_state.get_task_signature",
                return_value="sig",
            ),
            patch(
                "polaris.cells.orchestration.pm_dispatch.internal.iteration_state.merge_director_result_into_pm_state"
            ),
            patch(
                "polaris.cells.orchestration.pm_dispatch.internal.iteration_state._get_shangshuling_port",
            ) as mock_get_port,
        ):
            mock_port = MagicMock()
            mock_get_port.return_value = mock_port

            finalize_iteration(
                args=args,
                workspace_full=str(tmp_path),
                iteration=2,
                status="failed",
                state=pm_state,
                context=context,
            )

        mock_port.archive_task_history.assert_called_once()
        call_args = mock_port.archive_task_history.call_args
        assert call_args[0][2] == "run-2"  # run_id
        assert call_args[0][3] == 2  # iteration

    def test_injects_pre_provided_port(self, tmp_path) -> None:
        pm_state: dict = {}
        args = argparse.Namespace()
        context = {
            "pm_state_full": str(tmp_path / "pm_state.json"),
            "pm_history_full": "",
            "normalized": {},
            "start_timestamp": "2026-03-23T00:00:00Z",
            "cache_root_full": str(tmp_path / "cache"),
            "run_id": "run-3",
            "exit_code": 0,
            "backend": "",
            "events_seq_start": 0,
            "run_events": "",
            "pm_llm_events_full": "",
        }
        injected_port = MagicMock()

        with (
            patch("polaris.cells.orchestration.pm_dispatch.internal.iteration_state.write_json_atomic"),
            patch("polaris.kernelone.fs.jsonl.ops.append_jsonl"),
            patch("polaris.cells.orchestration.pm_dispatch.internal.iteration_state.emit_llm_event"),
            patch(
                "polaris.cells.orchestration.pm_dispatch.internal.iteration_state.get_task_signature", return_value=""
            ),
            patch(
                "polaris.cells.orchestration.pm_dispatch.internal.iteration_state.merge_director_result_into_pm_state"
            ),
        ):
            finalize_iteration(
                args=args,
                workspace_full=str(tmp_path),
                iteration=0,
                status="completed",
                state=pm_state,
                context=context,
                shangshuling_port=injected_port,
            )

        injected_port.archive_task_history.assert_called_once()
