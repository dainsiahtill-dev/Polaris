"""Tests for polaris.domain.director.constants."""

from __future__ import annotations

from polaris.domain.director.constants import (
    CHANNEL_FILES,
    DEFAULT_DIALOGUE,
    DEFAULT_DIRECTOR_LIFECYCLE,
    DEFAULT_DIRECTOR_STATUS,
    DEFAULT_ENGINE_STATUS,
    DEFAULT_GAP,
    DEFAULT_OLLAMA,
    DEFAULT_PLAN,
    DEFAULT_PLANNER,
    DEFAULT_PM_LOG,
    DEFAULT_PM_OUT,
    DEFAULT_PM_REPORT,
    DEFAULT_PM_SUBPROCESS_LOG,
    DEFAULT_QA,
    DEFAULT_REQUIREMENTS,
    DEFAULT_RUNLOG,
    DIRECTOR_CONTRACTS_DIR,
    DIRECTOR_EVENTS_DIR,
    DIRECTOR_LOGS_DIR,
    DIRECTOR_OUTPUT_DIR,
    DIRECTOR_RESULTS_DIR,
    DIRECTOR_RUNTIME_DIR,
    DIRECTOR_STATUS_DIR,
    NEW_CHANNEL_METADATA,
    DirectorPhase,
)


class TestDirectorConstants:
    def test_directory_constants(self) -> None:
        assert DIRECTOR_RUNTIME_DIR == "runtime"
        assert DIRECTOR_OUTPUT_DIR == "runtime/output"
        assert DIRECTOR_CONTRACTS_DIR == "runtime/contracts"
        assert DIRECTOR_RESULTS_DIR == "runtime/results"
        assert DIRECTOR_LOGS_DIR == "runtime/logs"
        assert DIRECTOR_STATUS_DIR == "runtime/status"
        assert DIRECTOR_EVENTS_DIR == "runtime/events"

    def test_file_constants(self) -> None:
        assert "runtime/contracts" in DEFAULT_PM_OUT
        assert "runtime/results" in DEFAULT_PM_REPORT
        assert "runtime/events" in DEFAULT_PM_LOG
        assert "runtime/logs" in DEFAULT_PM_SUBPROCESS_LOG
        assert "runtime/status" in DEFAULT_DIRECTOR_STATUS
        assert "runtime/status" in DEFAULT_ENGINE_STATUS
        assert "runtime/results" in DEFAULT_PLANNER
        assert "runtime/results" in DEFAULT_OLLAMA
        assert "runtime/logs" in DEFAULT_RUNLOG
        assert "runtime/events" in DEFAULT_DIALOGUE
        assert "runtime" in DEFAULT_DIRECTOR_LIFECYCLE
        assert "runtime/contracts" in DEFAULT_PLAN
        assert "runtime/contracts" in DEFAULT_GAP
        assert "runtime/results" in DEFAULT_QA
        assert "workspace/docs/product" in DEFAULT_REQUIREMENTS

    def test_director_phase_values(self) -> None:
        assert DirectorPhase.INIT == "init"
        assert DirectorPhase.PLANNING == "planning"
        assert DirectorPhase.EXECUTING == "executing"
        assert DirectorPhase.REVIEWING == "reviewing"
        assert DirectorPhase.COMPLETING == "completing"
        assert DirectorPhase.FAILED == "failed"
        assert "init" in DirectorPhase.ALL
        assert "failed" in DirectorPhase.ALL

    def test_channel_files_keys(self) -> None:
        assert "pm_report" in CHANNEL_FILES
        assert "pm_log" in CHANNEL_FILES
        assert "system" in CHANNEL_FILES
        assert "process" in CHANNEL_FILES
        assert "llm" in CHANNEL_FILES

    def test_new_channel_metadata(self) -> None:
        assert "system" in NEW_CHANNEL_METADATA
        assert "description" in NEW_CHANNEL_METADATA["system"]
        assert "severity_levels" in NEW_CHANNEL_METADATA["system"]
