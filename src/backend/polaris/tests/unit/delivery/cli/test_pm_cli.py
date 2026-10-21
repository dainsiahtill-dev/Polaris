"""Tests for PM CLI subcommands.

Covers:
  - pm_cli.py parser construction and command dispatch
  - cli.py (loop-pm) parser and main entry
  - cli_thin.py (pm-thin) parser
  - Happy path, error cases, help text
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from polaris.delivery.cli.pm.cli import create_parser as pm_loop_create_parser, main as pm_loop_main
from polaris.delivery.cli.pm.cli_thin import create_parser as pm_thin_create_parser, main as pm_thin_main
from polaris.delivery.cli.pm.pm_cli import main as pm_cli_main

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def pm_loop_parser() -> argparse.ArgumentParser:
    """Return a fresh loop-pm parser."""
    return pm_loop_create_parser()


@pytest.fixture
def pm_thin_parser() -> argparse.ArgumentParser:
    """Return a fresh pm-thin parser."""
    return pm_thin_create_parser()


# ---------------------------------------------------------------------------
# Test: pm_cli.py (legacy PM CLI)
# ---------------------------------------------------------------------------


class TestPmCliMain:
    """Test pm_cli.py main() and subcommands."""

    def test_pm_cli_help_exits_zero(self) -> None:
        """pm_cli main(['--help']) must raise SystemExit(0)."""
        with pytest.raises(SystemExit) as exc_info:
            pm_cli_main(["--help"])
        assert exc_info.value.code == 0

    def test_pm_cli_init_parses(self) -> None:
        """init command must parse with optional flags."""
        with pytest.raises(SystemExit) as exc_info:
            pm_cli_main(["init", "--help"])
        assert exc_info.value.code == 0

    def test_pm_cli_status_parses(self) -> None:
        """status command help must exit 0."""
        with pytest.raises(SystemExit) as exc_info:
            pm_cli_main(["status", "--help"])
        assert exc_info.value.code == 0

    def test_pm_cli_health_parses(self) -> None:
        """health command help must exit 0."""
        with pytest.raises(SystemExit) as exc_info:
            pm_cli_main(["health", "--help"])
        assert exc_info.value.code == 0

    def test_pm_cli_report_parses(self) -> None:
        """report command help must exit 0."""
        with pytest.raises(SystemExit) as exc_info:
            pm_cli_main(["report", "--help"])
        assert exc_info.value.code == 0

    def test_pm_cli_coverage_parses(self) -> None:
        """coverage command help must exit 0."""
        with pytest.raises(SystemExit) as exc_info:
            pm_cli_main(["coverage", "--help"])
        assert exc_info.value.code == 0

    def test_pm_cli_requirement_add_parses(self) -> None:
        """requirement add help must exit 0."""
        with pytest.raises(SystemExit) as exc_info:
            pm_cli_main(["requirement", "add", "--help"])
        assert exc_info.value.code == 0

    def test_pm_cli_requirement_list_parses(self) -> None:
        """requirement list help must exit 0."""
        with pytest.raises(SystemExit) as exc_info:
            pm_cli_main(["requirement", "list", "--help"])
        assert exc_info.value.code == 0

    def test_pm_cli_requirement_status_parses(self) -> None:
        """requirement status help must exit 0."""
        with pytest.raises(SystemExit) as exc_info:
            pm_cli_main(["requirement", "status", "--help"])
        assert exc_info.value.code == 0

    def test_pm_cli_task_add_parses(self) -> None:
        """task add help must exit 0."""
        with pytest.raises(SystemExit) as exc_info:
            pm_cli_main(["task", "add", "--help"])
        assert exc_info.value.code == 0

    def test_pm_cli_task_list_parses(self) -> None:
        """task list help must exit 0."""
        with pytest.raises(SystemExit) as exc_info:
            pm_cli_main(["task", "list", "--help"])
        assert exc_info.value.code == 0

    def test_pm_cli_task_assign_parses(self) -> None:
        """task assign help must exit 0."""
        with pytest.raises(SystemExit) as exc_info:
            pm_cli_main(["task", "assign", "--help"])
        assert exc_info.value.code == 0

    def test_pm_cli_task_complete_parses(self) -> None:
        """task complete help must exit 0."""
        with pytest.raises(SystemExit) as exc_info:
            pm_cli_main(["task", "complete", "--help"])
        assert exc_info.value.code == 0

    def test_pm_cli_task_history_parses(self) -> None:
        """task history help must exit 0."""
        with pytest.raises(SystemExit) as exc_info:
            pm_cli_main(["task", "history", "--help"])
        assert exc_info.value.code == 0

    def test_pm_cli_document_list_parses(self) -> None:
        """document list help must exit 0."""
        with pytest.raises(SystemExit) as exc_info:
            pm_cli_main(["document", "list", "--help"])
        assert exc_info.value.code == 0

    def test_pm_cli_document_show_parses(self) -> None:
        """document show help must exit 0."""
        with pytest.raises(SystemExit) as exc_info:
            pm_cli_main(["document", "show", "--help"])
        assert exc_info.value.code == 0

    def test_pm_cli_api_server_parses(self) -> None:
        """api-server help must exit 0."""
        with pytest.raises(SystemExit) as exc_info:
            pm_cli_main(["api-server", "--help"])
        assert exc_info.value.code == 0

    def test_pm_cli_no_command_prints_help(self, capsys: pytest.CaptureFixture[str]) -> None:
        """pm_cli main([]) must print help and return 1."""
        code = pm_cli_main([])
        assert code == 1
        captured = capsys.readouterr()
        assert "PM" in captured.out or "usage" in captured.out.lower()

    def test_pm_cli_init_not_initialized(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """init on a fresh workspace must succeed."""
        ws = str(tmp_path)
        from polaris.delivery.cli.pm import pm_cli

        # Mock get_pm to avoid full backend initialization
        mock_pm = MagicMock()
        mock_pm.is_initialized.return_value = False
        mock_pm.initialize.return_value = {
            "workspace": ws,
            "project_name": "Test",
            "pm_version": "1.0.0",
        }

        with patch.object(pm_cli, "get_pm", return_value=mock_pm):
            code = pm_cli_main(["--workspace", ws, "init", "--project-name", "Test"])
            assert code == 0
            mock_pm.initialize.assert_called_once()

    def test_pm_cli_init_already_initialized_no_force(self, tmp_path: Path) -> None:
        """init on an already-initialized workspace without --force must return 1."""
        ws = str(tmp_path)
        from polaris.delivery.cli.pm import pm_cli

        mock_pm = MagicMock()
        mock_pm.is_initialized.return_value = True

        with patch.object(pm_cli, "get_pm", return_value=mock_pm):
            code = pm_cli_main(["--workspace", ws, "init"])
            assert code == 1

    def test_pm_cli_status_not_initialized(self, tmp_path: Path) -> None:
        """status on an uninitialized workspace must return 1."""
        ws = str(tmp_path)
        from polaris.delivery.cli.pm import pm_cli

        mock_pm = MagicMock()
        mock_pm.is_initialized.return_value = False

        with patch.object(pm_cli, "get_pm", return_value=mock_pm):
            code = pm_cli_main(["--workspace", ws, "status"])
            assert code == 1

    def test_pm_cli_status_initialized(self, tmp_path: Path) -> None:
        """status on an initialized workspace must return 0."""
        ws = str(tmp_path)
        from polaris.delivery.cli.pm import pm_cli

        mock_pm = MagicMock()
        mock_pm.is_initialized.return_value = True
        mock_pm.get_status.return_value = {
            "project": "Test",
            "version": "1.0.0",
            "stats": {
                "tasks": {"total": 0, "completed": 0, "in_progress": 0, "pending": 0, "completion_rate": 0.0},
                "requirements": {"total": 0, "implemented": 0, "verified": 0, "coverage": 0.0},
            },
        }

        with patch.object(pm_cli, "get_pm", return_value=mock_pm):
            code = pm_cli_main(["--workspace", ws, "status"])
            assert code == 0

    def test_pm_cli_health_not_initialized(self, tmp_path: Path) -> None:
        """health on an uninitialized workspace must return 1."""
        ws = str(tmp_path)
        from polaris.delivery.cli.pm import pm_cli

        mock_pm = MagicMock()
        mock_pm.is_initialized.return_value = False

        with patch.object(pm_cli, "get_pm", return_value=mock_pm):
            code = pm_cli_main(["--workspace", ws, "health"])
            assert code == 1

    def test_pm_cli_coverage_not_initialized(self, tmp_path: Path) -> None:
        """coverage on an uninitialized workspace must return 1."""
        ws = str(tmp_path)
        from polaris.delivery.cli.pm import pm_cli

        mock_pm = MagicMock()
        mock_pm.is_initialized.return_value = False

        with patch.object(pm_cli, "get_pm", return_value=mock_pm):
            code = pm_cli_main(["--workspace", ws, "coverage"])
            assert code == 1

    def test_pm_cli_report_not_initialized(self, tmp_path: Path) -> None:
        """report on an uninitialized workspace must return 1."""
        ws = str(tmp_path)
        from polaris.delivery.cli.pm import pm_cli

        mock_pm = MagicMock()
        mock_pm.is_initialized.return_value = False

        with patch.object(pm_cli, "get_pm", return_value=mock_pm):
            code = pm_cli_main(["--workspace", ws, "report"])
            assert code == 1

    def test_pm_cli_requirement_add_not_initialized(self, tmp_path: Path) -> None:
        """requirement add on uninitialized workspace must return 1."""
        ws = str(tmp_path)
        from polaris.delivery.cli.pm import pm_cli

        mock_pm = MagicMock()
        mock_pm.is_initialized.return_value = False

        with patch.object(pm_cli, "get_pm", return_value=mock_pm):
            code = pm_cli_main(["--workspace", ws, "requirement", "add", "Test Req"])
            assert code == 1

    def test_pm_cli_task_add_not_initialized(self, tmp_path: Path) -> None:
        """task add on uninitialized workspace must return 1."""
        ws = str(tmp_path)
        from polaris.delivery.cli.pm import pm_cli

        mock_pm = MagicMock()
        mock_pm.is_initialized.return_value = False

        with patch.object(pm_cli, "get_pm", return_value=mock_pm):
            code = pm_cli_main(["--workspace", ws, "task", "add", "Test Task"])
            assert code == 1

    def test_pm_cli_document_list_not_initialized(self, tmp_path: Path) -> None:
        """document list on uninitialized workspace must return 1."""
        ws = str(tmp_path)
        from polaris.delivery.cli.pm import pm_cli

        mock_pm = MagicMock()
        mock_pm.is_initialized.return_value = False

        with patch.object(pm_cli, "get_pm", return_value=mock_pm):
            code = pm_cli_main(["--workspace", ws, "document", "list"])
            assert code == 1


# ---------------------------------------------------------------------------
# Test: cli.py (loop-pm)
# ---------------------------------------------------------------------------


class TestPmLoopCli:
    """PM entry validates the supported engineering intent, not old worker protocols.

    Native defaults/explicit execution and durable state are covered by
    test_engineering_cli_entry_adapters; PM document/service tests stay below.
    """

    def test_parser_preserves_workspace(self, pm_loop_parser: argparse.ArgumentParser) -> None:
        args = pm_loop_parser.parse_args(["--workspace", "/tmp/ws"])
        assert args.workspace == "/tmp/ws"

    def test_parser_preserves_required_commands_including_duplicates(
        self,
        pm_loop_parser: argparse.ArgumentParser,
    ) -> None:
        args = pm_loop_parser.parse_args(
            ["--required-check", "npm test", "--required-check", "npm test", "--required-check", "npm run check"]
        )
        assert args.required_check == ["npm test", "npm test", "npm run check"]

    @pytest.mark.parametrize(
        "arguments",
        [
            ["--iterations", "0"],
            ["--agent", "director"],
            ["--run-director"],
            ["--pm-backend", "auto"],
            ["--director-path", "some-worker.py"],
        ],
    )
    def test_worker_protocol_is_rejected(self, pm_loop_parser: argparse.ArgumentParser, arguments: list[str]) -> None:
        with pytest.raises(SystemExit) as error:
            pm_loop_parser.parse_args(arguments)
        assert error.value.code == 2

    def test_main_help_exits_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "argv", ["polaris-pm", "--help"])
        with pytest.raises(SystemExit) as error:
            pm_loop_main()
        assert error.value.code == 0


# ---------------------------------------------------------------------------
# Test: cli_thin.py (pm-thin)
# ---------------------------------------------------------------------------


class TestPmThinCli:
    """Test pm-thin CLI parser and main."""

    def test_pm_thin_parser_has_workspace(self, pm_thin_parser: argparse.ArgumentParser) -> None:
        """pm-thin parser must accept --workspace."""
        args = pm_thin_parser.parse_args(["--workspace", "/tmp/ws"])
        assert args.workspace == "/tmp/ws"

    def test_pm_thin_parser_has_loop(self, pm_thin_parser: argparse.ArgumentParser) -> None:
        """pm-thin parser must accept --loop."""
        args = pm_thin_parser.parse_args(["--loop"])
        assert args.loop is True

    def test_pm_thin_parser_has_directive(self, pm_thin_parser: argparse.ArgumentParser) -> None:
        """pm-thin parser must accept --directive."""
        args = pm_thin_parser.parse_args(["--directive", "build"])
        assert args.directive == "build"

    def test_pm_thin_parser_has_start_from(self, pm_thin_parser: argparse.ArgumentParser) -> None:
        """pm-thin parser must accept --start-from with choices."""
        for role in ("pm", "architect"):
            args = pm_thin_parser.parse_args(["--start-from", role])
            assert args.start_from == role

        for role in ("director", "qa"):
            with pytest.raises(SystemExit):
                pm_thin_parser.parse_args(["--start-from", role])

    def test_pm_thin_parser_default_values(self, pm_thin_parser: argparse.ArgumentParser) -> None:
        """pm-thin parser defaults must be correct."""
        args = pm_thin_parser.parse_args([])
        assert args.workspace == str(Path.cwd())
        assert args.loop is False
        assert args.directive is None
        assert args.start_from == "pm"

    def test_pm_thin_main_help_exits_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """pm-thin main(['--help']) must raise SystemExit(0)."""
        monkeypatch.setattr(sys, "argv", ["pm-thin", "--help"])
        with pytest.raises(SystemExit) as exc_info:
            pm_thin_main()
        assert exc_info.value.code == 0

    def test_pm_thin_command_forwards_directive_and_start_from(self) -> None:
        """FINDING 4 regression: --directive and --start-from must be forwarded
        into the launched ServiceDefinition command, not silently dropped."""
        from polaris.delivery.cli.pm.cli_thin import _build_pm_command

        command = _build_pm_command("/tmp/ws", "Ship the feature", "architect")
        assert command[:5] == [
            sys.executable,
            "-m",
            "polaris.delivery.cli.pm.cli_thin",
            "--workspace",
            "/tmp/ws",
        ]
        assert "--directive" in command
        assert command[command.index("--directive") + 1] == "Ship the feature"
        assert "--start-from" in command
        assert command[command.index("--start-from") + 1] == "architect"

    def test_pm_thin_command_omits_absent_optional_flags(self) -> None:
        """FINDING 4: optional flags must NOT appear when not supplied."""
        from polaris.delivery.cli.pm.cli_thin import _build_pm_command

        command = _build_pm_command("/tmp/ws", None, None)
        assert "--directive" not in command
        assert "--start-from" not in command

    def test_pm_thin_main_forwards_directive_to_service_definition(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """FINDING 4: main() must thread parsed --directive/--start-from through
        run_pm_console into the built ServiceDefinition command."""
        from polaris.delivery.cli.pm import cli_thin

        captured: dict[str, Any] = {}

        async def fake_run_pm_console(
            workspace: str,
            loop: bool,
            directive: str | None,
            start_from: str | None = None,
        ) -> None:
            captured["command"] = cli_thin._build_pm_command(workspace, directive, start_from)
            captured["start_from"] = start_from
            captured["directive"] = directive

        monkeypatch.setattr(cli_thin, "run_pm_console", fake_run_pm_console)
        monkeypatch.setattr(
            sys,
            "argv",
            ["pm-thin", "--workspace", "/tmp/ws", "--directive", "Do it", "--start-from", "pm"],
        )

        assert cli_thin.main() == 0
        assert captured["directive"] == "Do it"
        assert captured["start_from"] == "pm"
        assert "--directive" in captured["command"]
        assert "--start-from" in captured["command"]
        assert captured["command"][captured["command"].index("--start-from") + 1] == "pm"


# ---------------------------------------------------------------------------
# Test: PM integration helpers
# ---------------------------------------------------------------------------


class TestPmIntegration:
    """Test PM integration module helpers."""

    def test_get_pm_caches_instance(self, tmp_path: Path) -> None:
        """get_pm must cache and return the same instance for the same workspace."""
        from polaris.delivery.cli.pm.pm_integration import get_pm, reset_pm

        ws = str(tmp_path)
        reset_pm(ws)
        pm1 = get_pm(ws)
        pm2 = get_pm(ws)
        assert pm1 is pm2
        reset_pm(ws)

    def test_reset_pm_removes_instance(self, tmp_path: Path) -> None:
        """reset_pm must remove the cached instance."""
        from polaris.delivery.cli.pm.pm_integration import get_pm, reset_pm

        ws = str(tmp_path)
        reset_pm(ws)
        pm1 = get_pm(ws)
        reset_pm(ws)
        pm2 = get_pm(ws)
        assert pm1 is not pm2

    def test_pm_class_has_properties(self, tmp_path: Path) -> None:
        """PM class must expose state, requirements, documents, tasks, execution."""
        from polaris.delivery.cli.pm.pm_integration import PM

        pm = PM(str(tmp_path))
        assert pm.state is not None
        assert pm.requirements is not None
        assert pm.documents is not None
        assert pm.tasks is not None
        assert pm.execution is not None

    def test_pm_initialize(self, tmp_path: Path) -> None:
        """PM.initialize must return a dict with expected keys."""
        from polaris.delivery.cli.pm.pm_integration import PM

        pm = PM(str(tmp_path))
        # Mock state manager to avoid filesystem side effects
        mock_state = MagicMock()
        mock_state.metadata.name = "TestProject"
        mock_state.version = "1.0.0"
        pm._state_manager = MagicMock()
        pm._state_manager.initialize.return_value = mock_state

        result = pm.initialize(project_name="TestProject", description="Desc")
        assert result["initialized"] is True
        assert result["project_name"] == "TestProject"
        assert result["pm_version"] == "1.0.0"

    def test_pm_is_initialized_delegates(self, tmp_path: Path) -> None:
        """PM.is_initialized must delegate to state manager."""
        from polaris.delivery.cli.pm.pm_integration import PM

        pm = PM(str(tmp_path))
        pm._state_manager = MagicMock()
        pm._state_manager.is_initialized.return_value = True
        assert pm.is_initialized() is True

    def test_pm_get_status(self, tmp_path: Path) -> None:
        """PM.get_status must return a dict with expected structure."""
        from polaris.delivery.cli.pm.pm_integration import PM

        pm = PM(str(tmp_path))
        pm._state_manager = MagicMock()
        pm._state_manager.get_state.return_value = MagicMock(metadata=MagicMock(name="Proj"), version="1.0")
        pm._state_manager.is_initialized.return_value = True
        pm._task_orchestrator = MagicMock()
        pm._task_orchestrator.get_stats_summary.return_value = {}
        pm._requirements_tracker = MagicMock()
        pm._requirements_tracker.get_coverage_report.return_value = {}

        status = pm.get_status()
        assert "initialized" in status
        assert "project" in status
        assert "stats" in status

    def test_pm_sync_from_source_tasks_empty(self, tmp_path: Path) -> None:
        """PM.sync_from_source_tasks with empty list must return 0."""
        from polaris.delivery.cli.pm.pm_integration import PM

        pm = PM(str(tmp_path))
        result = pm.sync_from_source_tasks([])
        assert result == 0

    def test_pm_sync_from_source_tasks_invalid_input(self, tmp_path: Path) -> None:
        """PM.sync_from_source_tasks with non-list must return 0."""
        from polaris.delivery.cli.pm.pm_integration import PM

        pm = PM(str(tmp_path))
        result = pm.sync_from_source_tasks("not-a-list")  # type: ignore[arg-type]
        assert result == 0

    def test_pm_resolve_task_id_empty(self, tmp_path: Path) -> None:
        """PM.resolve_task_id with empty string must return None."""
        from polaris.delivery.cli.pm.pm_integration import PM

        pm = PM(str(tmp_path))
        assert pm.resolve_task_id("") is None
        assert pm.resolve_task_id("   ") is None

    def test_pm_record_task_completion_bad_id(self, tmp_path: Path) -> None:
        """PM.record_task_completion with unresolvable task_id must return False."""
        from polaris.delivery.cli.pm.pm_integration import PM

        pm = PM(str(tmp_path))
        pm._task_orchestrator = MagicMock()
        pm._task_orchestrator.get_task.return_value = None
        result = pm.record_task_completion("bad-id", "executor", True, {})
        assert result is False

    def test_pm_analyze_project_health(self, tmp_path: Path) -> None:
        """PM.analyze_project_health must return expected structure."""
        from polaris.delivery.cli.pm.pm_integration import PM

        pm = PM(str(tmp_path))
        pm._state_manager = MagicMock()
        pm._state_manager.get_state.return_value = MagicMock(metadata=MagicMock(name="Proj"), version="1.0")
        pm._state_manager.is_initialized.return_value = True
        pm._task_orchestrator = MagicMock()
        pm._task_orchestrator.get_stats_summary.return_value = {"completion_rate": 0.5, "failed": 0, "completed": 1}
        pm._requirements_tracker = MagicMock()
        pm._requirements_tracker.get_coverage_report.return_value = {"coverage": 0.5}
        pm._execution_tracker = MagicMock()
        pm._execution_tracker.get_execution_summary.return_value = {"success_rate": 0.8}

        health = pm.analyze_project_health()
        assert "overall" in health
        assert "components" in health
        assert "metrics" in health
        assert "recommendations" in health

    def test_pm_generate_comprehensive_report(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """PM.generate_comprehensive_report must return a file path string."""
        from polaris.delivery.cli.pm.pm_integration import PM

        pm = PM(str(tmp_path))
        pm._state_manager = MagicMock()
        pm._state_manager.get_state.return_value = MagicMock(metadata=MagicMock(name="Proj"), version="1.0")
        pm._state_manager.is_initialized.return_value = True
        pm._task_orchestrator = MagicMock()
        pm._task_orchestrator.get_stats_summary.return_value = {}
        pm._requirements_tracker = MagicMock()
        pm._requirements_tracker.get_coverage_report.return_value = {}
        pm._execution_tracker = MagicMock()
        pm._execution_tracker.get_execution_summary.return_value = {}

        # Mock get_status to return a JSON-serializable dict
        monkeypatch.setattr(
            pm,
            "get_status",
            MagicMock(
                return_value={
                    "initialized": True,
                    "project": "Proj",
                    "version": "1.0",
                    "stats": {"tasks": {}, "requirements": {}},
                }
            ),
        )

        path = pm.generate_comprehensive_report(str(tmp_path))
        assert isinstance(path, str)
        assert Path(path).parent == tmp_path
        assert Path(path).exists()

    def test_pm_list_documents_delegates(self, tmp_path: Path) -> None:
        """PM.list_documents must delegate to DocumentManager."""
        from polaris.delivery.cli.pm.pm_integration import PM

        pm = PM(str(tmp_path))
        pm._document_manager = MagicMock()
        pm._document_manager.list_documents.return_value = {"documents": [], "pagination": {}}
        result = pm.list_documents()
        assert "documents" in result

    def test_pm_get_document_delegates(self, tmp_path: Path) -> None:
        """PM.get_document must delegate to DocumentManager."""
        from polaris.delivery.cli.pm.pm_integration import PM

        pm = PM(str(tmp_path))
        pm._document_manager = MagicMock()
        pm._document_manager.get_document_info.return_value = {"path": "test.md"}
        result = pm.get_document("test.md")
        assert result is not None
        assert result["path"] == "test.md"

    def test_pm_get_document_content_delegates(self, tmp_path: Path) -> None:
        """PM.get_document_content must delegate to DocumentManager."""
        from polaris.delivery.cli.pm.pm_integration import PM

        pm = PM(str(tmp_path))
        pm._document_manager = MagicMock()
        pm._document_manager.get_version_content.return_value = "hello"
        result = pm.get_document_content("test.md")
        assert result == "hello"

    def test_pm_search_documents_delegates(self, tmp_path: Path) -> None:
        """PM.search_documents must delegate to DocumentManager."""
        from polaris.delivery.cli.pm.pm_integration import PM

        pm = PM(str(tmp_path))
        pm._document_manager = MagicMock()
        pm._document_manager.search_documents.return_value = [{"path": "a.md"}]
        result = pm.search_documents("query")
        assert len(result) == 1

    def test_pm_get_task_history_delegates(self, tmp_path: Path) -> None:
        """PM.get_task_history must delegate to TaskOrchestrator."""
        from polaris.delivery.cli.pm.pm_integration import PM

        pm = PM(str(tmp_path))
        pm._task_orchestrator = MagicMock()
        pm._task_orchestrator.get_task_history.return_value = {"tasks": [], "pagination": {}}
        result = pm.get_task_history()
        assert "tasks" in result

    def test_pm_get_director_task_history_delegates(self, tmp_path: Path) -> None:
        """PM.get_director_task_history must delegate to TaskOrchestrator."""
        from polaris.delivery.cli.pm.pm_integration import PM

        pm = PM(str(tmp_path))
        pm._task_orchestrator = MagicMock()
        pm._task_orchestrator.get_director_task_history.return_value = {"tasks": [], "pagination": {}}
        result = pm.get_director_task_history()
        assert "tasks" in result

    def test_pm_get_task_assignments_delegates(self, tmp_path: Path) -> None:
        """PM.get_task_assignments must delegate to TaskOrchestrator."""
        from polaris.delivery.cli.pm.pm_integration import PM

        pm = PM(str(tmp_path))
        pm._task_orchestrator = MagicMock()
        pm._task_orchestrator.get_task_assignments.return_value = []
        result = pm.get_task_assignments()
        assert result == []

    def test_pm_search_tasks_delegates(self, tmp_path: Path) -> None:
        """PM.search_tasks must delegate to TaskOrchestrator."""
        from polaris.delivery.cli.pm.pm_integration import PM

        pm = PM(str(tmp_path))
        pm._task_orchestrator = MagicMock()
        pm._task_orchestrator.search_tasks.return_value = [{"id": "t1"}]
        result = pm.search_tasks("query")
        assert len(result) == 1

    def test_pm_get_task_delegates(self, tmp_path: Path) -> None:
        """PM.get_task must delegate to TaskOrchestrator."""
        from polaris.delivery.cli.pm.pm_integration import PM

        pm = PM(str(tmp_path))
        pm._task_orchestrator = MagicMock()
        pm._task_orchestrator.get_task.return_value = MagicMock(id="t1")
        result = pm.get_task("t1")
        assert result is not None

    def test_pm_list_tasks_delegates(self, tmp_path: Path) -> None:
        """PM.list_tasks must return expected structure."""
        from polaris.delivery.cli.pm.pm_integration import PM

        pm = PM(str(tmp_path))
        pm._task_orchestrator = MagicMock()
        pm._task_orchestrator._load_registry.return_value = {"tasks": {}}
        result = pm.list_tasks()
        assert "tasks" in result
        assert "pagination" in result

    def test_pm_list_requirements_delegates(self, tmp_path: Path) -> None:
        """PM.list_requirements must return expected structure."""
        from polaris.delivery.cli.pm.pm_integration import PM

        pm = PM(str(tmp_path))
        pm._requirements_tracker = MagicMock()
        pm._requirements_tracker.list_requirements.return_value = []
        result = pm.list_requirements()
        assert "requirements" in result
        assert "pagination" in result

    def test_pm_get_requirement_found(self, tmp_path: Path) -> None:
        """PM.get_requirement must return dict when found."""
        from polaris.delivery.cli.pm.pm_integration import PM

        pm = PM(str(tmp_path))
        mock_req = MagicMock()
        mock_req.id = "r1"
        mock_req.title = "Title"
        mock_req.description = "Desc"
        mock_req.status.value = "pending"
        mock_req.priority.value = "high"
        mock_req.req_type.value = "functional"
        mock_req.source = "manual"
        mock_req.source_section = ""
        mock_req.created_at = "2024-01-01"
        mock_req.updated_at = "2024-01-01"
        mock_req.tasks = []
        mock_req.metadata = {}
        pm._requirements_tracker = MagicMock()
        pm._requirements_tracker.get_requirement.return_value = mock_req
        result = pm.get_requirement("r1")
        assert result is not None
        assert result["id"] == "r1"

    def test_pm_get_requirement_not_found(self, tmp_path: Path) -> None:
        """PM.get_requirement must return None when not found."""
        from polaris.delivery.cli.pm.pm_integration import PM

        pm = PM(str(tmp_path))
        pm._requirements_tracker = MagicMock()
        pm._requirements_tracker.get_requirement.return_value = None
        result = pm.get_requirement("r1")
        assert result is None
