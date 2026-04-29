# ruff: noqa: E402
"""Tests for polaris.domain.director.constants module."""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = str(Path(__file__).resolve().parents[4])
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from polaris.domain.director import constants


class TestDirectorPathConstants:
    def test_runtime_dir(self) -> None:
        assert constants.DIRECTOR_RUNTIME_DIR == "runtime"

    def test_output_dir(self) -> None:
        assert constants.DIRECTOR_OUTPUT_DIR == "runtime/output"

    def test_contracts_dir(self) -> None:
        assert constants.DIRECTOR_CONTRACTS_DIR == "runtime/contracts"

    def test_results_dir(self) -> None:
        assert constants.DIRECTOR_RESULTS_DIR == "runtime/results"

    def test_logs_dir(self) -> None:
        assert constants.DIRECTOR_LOGS_DIR == "runtime/logs"

    def test_status_dir(self) -> None:
        assert constants.DIRECTOR_STATUS_DIR == "runtime/status"

    def test_events_dir(self) -> None:
        assert constants.DIRECTOR_EVENTS_DIR == "runtime/events"


class TestDefaultFilePaths:
    def test_pm_out(self) -> None:
        assert constants.DEFAULT_PM_OUT == "runtime/contracts/pm_tasks.contract.json"

    def test_pm_report(self) -> None:
        assert constants.DEFAULT_PM_REPORT == "runtime/results/pm.report.md"

    def test_pm_log(self) -> None:
        assert constants.DEFAULT_PM_LOG == "runtime/events/pm.events.jsonl"

    def test_director_status(self) -> None:
        assert constants.DEFAULT_DIRECTOR_STATUS == "runtime/status/director.status.json"

    def test_engine_status(self) -> None:
        assert constants.DEFAULT_ENGINE_STATUS == "runtime/status/engine.status.json"

    def test_plan(self) -> None:
        assert constants.DEFAULT_PLAN == "runtime/contracts/plan.md"

    def test_qa(self) -> None:
        assert constants.DEFAULT_QA == "runtime/results/qa.review.md"

    def test_requirements(self) -> None:
        assert constants.DEFAULT_REQUIREMENTS == "workspace/docs/product/requirements.md"


class TestDirectorPhase:
    def test_phase_values(self) -> None:
        assert constants.DirectorPhase.INIT == "init"
        assert constants.DirectorPhase.PLANNING == "planning"
        assert constants.DirectorPhase.EXECUTING == "executing"
        assert constants.DirectorPhase.REVIEWING == "reviewing"
        assert constants.DirectorPhase.COMPLETING == "completing"
        assert constants.DirectorPhase.FAILED == "failed"

    def test_all_phases(self) -> None:
        assert constants.DirectorPhase.ALL == (
            "init",
            "planning",
            "executing",
            "reviewing",
            "completing",
            "failed",
        )

    def test_all_contains_all_phases(self) -> None:
        for phase in [
            constants.DirectorPhase.INIT,
            constants.DirectorPhase.PLANNING,
            constants.DirectorPhase.EXECUTING,
            constants.DirectorPhase.REVIEWING,
            constants.DirectorPhase.COMPLETING,
            constants.DirectorPhase.FAILED,
        ]:
            assert phase in constants.DirectorPhase.ALL


class TestChannelFiles:
    def test_channel_files_is_dict(self) -> None:
        assert isinstance(constants.CHANNEL_FILES, dict)

    def test_has_legacy_channels(self) -> None:
        assert "pm_report" in constants.CHANNEL_FILES
        assert "pm_log" in constants.CHANNEL_FILES
        assert "dialogue" in constants.CHANNEL_FILES

    def test_has_new_channels(self) -> None:
        assert "system" in constants.CHANNEL_FILES
        assert "process" in constants.CHANNEL_FILES
        assert "llm" in constants.CHANNEL_FILES

    def test_new_channels_have_run_id_placeholder(self) -> None:
        assert "{run_id}" in constants.CHANNEL_FILES["system"]


class TestNewChannelMetadata:
    def test_is_dict(self) -> None:
        assert isinstance(constants.NEW_CHANNEL_METADATA, dict)

    def test_system_metadata(self) -> None:
        meta = constants.NEW_CHANNEL_METADATA["system"]
        assert "description" in meta
        assert "severity_levels" in meta
        assert "critical" in meta["severity_levels"]

    def test_process_metadata(self) -> None:
        meta = constants.NEW_CHANNEL_METADATA["process"]
        assert "error" in meta["severity_levels"]

    def test_llm_metadata(self) -> None:
        meta = constants.NEW_CHANNEL_METADATA["llm"]
        assert "debug" in meta["severity_levels"]


class TestAllExports:
    def test_all_exports_are_defined(self) -> None:
        for name in constants.__all__:
            assert hasattr(constants, name), f"{name} not defined"

    def test_no_extra_exports(self) -> None:
        import types
        from typing import Final

        public_names = [n for n in dir(constants) if not n.startswith("_")]
        for name in public_names:
            if name in ("annotations",):
                continue
            obj = getattr(constants, name)
            if isinstance(obj, types.ModuleType):
                continue
            if obj is Final:
                continue
            assert name in constants.__all__, f"{name} not in __all__"
