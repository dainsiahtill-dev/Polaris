"""Tests for polaris.delivery.cli.polaris_cli module."""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest
from polaris.delivery.cli.polaris_cli import (
    _bind_workspace_environment,
    _default_workflow_run_id,
    _read_workspace_json,
    _resolve_workspace,
    _serialize_workflow_submission,
    create_parser,
    main,
)


class TestCreateParser:
    def test_parser_creation(self):
        parser = create_parser()
        assert parser.prog == "polaris-cli"

    def test_parse_chat_command(self):
        parser = create_parser()
        args = parser.parse_args(["chat", "--role", "pm", "--mode", "interactive"])
        assert args.command == "chat"
        assert args.role == "pm"
        assert args.mode == "interactive"

    def test_parse_chat_oneshot_with_goal(self):
        parser = create_parser()
        args = parser.parse_args(["chat", "--mode", "oneshot", "--goal", "test goal"])
        assert args.goal == "test goal"

    def test_parse_workflow_run_command(self):
        parser = create_parser()
        args = parser.parse_args(["workflow", "run", "--contracts-file", "test.json"])
        assert args.command == "workflow"
        assert args.workflow_action == "run"
        assert args.contracts_file == "test.json"

    def test_parse_workflow_status_with_id(self):
        parser = create_parser()
        args = parser.parse_args(["workflow", "status", "--workflow-id", "wf-1"])
        assert args.workflow_id == "wf-1"

    def test_parse_status_command(self):
        parser = create_parser()
        args = parser.parse_args(["status", "--role", "director"])
        assert args.command == "status"
        assert args.role == "director"

    def test_parse_test_window_command(self):
        parser = create_parser()
        args = parser.parse_args(["test-window", "--role", "pm"])
        assert args.command == "test-window"
        assert args.role == "pm"

    def test_default_workspace(self):
        parser = create_parser()
        args = parser.parse_args(["chat"])
        assert args.workspace == "."

    def test_custom_workspace(self):
        parser = create_parser()
        args = parser.parse_args(["chat", "--workspace", "/tmp/ws"])
        assert args.workspace == "/tmp/ws"

    def test_chat_backend_choices(self):
        parser = create_parser()
        args = parser.parse_args(["chat", "--backend", "plain"])
        assert args.backend == "plain"

    def test_chat_json_render_choices(self):
        parser = create_parser()
        args = parser.parse_args(["chat", "--json-render", "pretty-color"])
        assert args.json_render == "pretty-color"

    def test_workflow_execution_mode_choices(self):
        parser = create_parser()
        args = parser.parse_args(["workflow", "run", "--execution-mode", "serial"])
        assert args.execution_mode == "serial"

    def test_workflow_timeout_defaults(self):
        parser = create_parser()
        args = parser.parse_args(["workflow", "run"])
        assert args.timeout_seconds == 300.0
        assert args.max_parallel_tasks == 3
        assert args.ready_timeout_seconds == 30
        assert args.task_timeout_seconds == 3600


class TestResolveWorkspace:
    def test_resolve_workspace_relative(self):
        result = _resolve_workspace(".")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_resolve_workspace_absolute(self):
        result = _resolve_workspace("C:/test/workspace")
        assert isinstance(result, str)
        assert "workspace" in result


class TestDefaultWorkflowRunId:
    def test_default_workflow_run_id_format(self):
        run_id = _default_workflow_run_id()
        assert run_id.startswith("cli-")
        assert len(run_id) > 10

    def test_default_workflow_run_id_unique(self):
        import time

        id1 = _default_workflow_run_id()
        time.sleep(1.1)
        id2 = _default_workflow_run_id()
        assert id1 != id2


class TestSerializeWorkflowSubmission:
    def test_serialize_with_all_fields(self):
        mock = MagicMock()
        mock.submitted = True
        mock.status = "completed"
        mock.workflow_id = "wf-1"
        mock.workflow_run_id = "run-1"
        mock.error = ""
        mock.details = {"key": "value"}
        result = _serialize_workflow_submission(mock)
        assert result["submitted"] is True
        assert result["status"] == "completed"
        assert result["workflow_id"] == "wf-1"
        assert result["error"] == ""
        assert result["details"] == {"key": "value"}

    def test_serialize_with_defaults(self):
        mock = MagicMock()
        mock.submitted = False
        mock.status = None
        mock.workflow_id = None
        mock.workflow_run_id = None
        mock.error = None
        mock.details = None
        result = _serialize_workflow_submission(mock)
        assert result["submitted"] is False
        assert result["status"] == ""
        assert result["details"] == {}

    def test_serialize_details_dict_conversion(self):
        mock = MagicMock()
        mock.submitted = True
        mock.status = ""
        mock.workflow_id = ""
        mock.workflow_run_id = ""
        mock.error = ""
        mock.details = "not a dict"
        result = _serialize_workflow_submission(mock)
        assert result["details"] == {}


class TestBindWorkspaceEnvironment:
    def test_bind_workspace_sets_env(self):
        with patch.dict(os.environ, {}, clear=False):
            _bind_workspace_environment("C:/test/workspace")
            assert "C:" in os.environ["KERNELONE_CONTEXT_ROOT"]
            assert "test" in os.environ["KERNELONE_CONTEXT_ROOT"]
            assert "workspace" in os.environ["KERNELONE_CONTEXT_ROOT"]

    def test_bind_workspace_runtime_db_set(self):
        with patch.dict(os.environ, {"KERNELONE_RUNTIME_DB": "/existing/db"}, clear=False):
            _bind_workspace_environment("C:/test/workspace")
            assert os.environ["KERNELONE_RUNTIME_DB"] == "/existing/db"

    def test_bind_workspace_runtime_db_unset(self):
        env_copy = dict(os.environ)
        env_copy.pop("KERNELONE_RUNTIME_DB", None)
        with patch.dict(os.environ, env_copy, clear=True):
            _bind_workspace_environment("C:/test/workspace")
            assert "KERNELONE_RUNTIME_ROOT" in os.environ
            assert "runtime" in os.environ["KERNELONE_RUNTIME_ROOT"]


class TestReadWorkspaceJson:
    def test_read_valid_json(self, tmp_path):
        ws = str(tmp_path)
        contracts_dir = tmp_path / "runtime" / "contracts"
        contracts_dir.mkdir(parents=True)
        contract_file = contracts_dir / "pm_tasks.contract.json"
        contract_file.write_text(json.dumps({"tasks": [{"id": 1}]}), encoding="utf-8")
        result = _read_workspace_json(ws, "runtime/contracts/pm_tasks.contract.json")
        assert isinstance(result, dict)
        assert "tasks" in result

    def test_read_file_not_found(self, tmp_path):
        with pytest.raises(SystemExit) as exc_info:
            _read_workspace_json(str(tmp_path), "nonexistent.json")
        assert "not found" in str(exc_info.value).lower() or "not found" in str(exc_info.value)

    def test_read_invalid_json(self, tmp_path):
        ws = str(tmp_path)
        contracts_dir = tmp_path / "runtime" / "contracts"
        contracts_dir.mkdir(parents=True)
        contract_file = contracts_dir / "bad.json"
        contract_file.write_text("not json{", encoding="utf-8")
        with pytest.raises(SystemExit) as exc_info:
            _read_workspace_json(ws, "runtime/contracts/bad.json")
        assert "valid json" in str(exc_info.value).lower()

    def test_read_non_dict_json(self, tmp_path):
        ws = str(tmp_path)
        contracts_dir = tmp_path / "runtime" / "contracts"
        contracts_dir.mkdir(parents=True)
        contract_file = contracts_dir / "list.json"
        contract_file.write_text("[1, 2, 3]", encoding="utf-8")
        with pytest.raises(SystemExit) as exc_info:
            _read_workspace_json(ws, "runtime/contracts/list.json")
        assert "json object" in str(exc_info.value).lower()

    def test_read_empty_path(self, tmp_path):
        with pytest.raises(SystemExit) as exc_info:
            _read_workspace_json(str(tmp_path), "")
        assert "required" in str(exc_info.value).lower()


class TestMainDispatch:
    def test_main_workflow_status_missing_id(self):
        with pytest.raises(SystemExit):
            main(["workflow", "status"])

    def test_main_unsupported_command(self):
        with pytest.raises(SystemExit):
            main(["invalid-command"])

    def test_main_no_args_shows_help(self):
        with pytest.raises(SystemExit):
            main([])

    def test_main_chat_console_mode(self):
        with patch("polaris.delivery.cli.polaris_cli._run_console_chat") as mock_run:
            mock_run.return_value = 0
            result = main(["chat", "--mode", "console"])
            assert result == 0
            mock_run.assert_called_once()

    def test_main_test_window(self):
        with patch("polaris.delivery.cli.polaris_cli._run_test_window") as mock_run:
            mock_run.return_value = 0
            result = main(["test-window"])
            assert result == 0
            mock_run.assert_called_once()

    def test_main_workflow_run(self):
        with patch("polaris.delivery.cli.polaris_cli._read_workspace_json") as mock_read:
            mock_read.return_value = {"tasks": [{"id": 1}]}
            with patch("polaris.delivery.cli.polaris_cli.submit_pm_workflow_sync") as mock_submit:
                mock_result = MagicMock()
                mock_result.submitted = True
                mock_result.status = "ok"
                mock_result.workflow_id = "wf-1"
                mock_result.workflow_run_id = "run-1"
                mock_result.error = ""
                mock_result.details = {}
                mock_submit.return_value = mock_result
                result = main(["workflow", "run"])
                assert result in (0, 1)

    def test_main_log_level_invalid(self):
        with patch("polaris.delivery.cli.polaris_cli.configure_cli_logging") as mock_cfg:
            mock_cfg.side_effect = ValueError("bad level")
            with pytest.raises(SystemExit):
                main(["chat", "--log-level", "invalid"])


class TestParserSubcommands:
    def test_workflow_cancel_reason_default(self):
        parser = create_parser()
        args = parser.parse_args(["workflow", "cancel", "--workflow-id", "wf-1"])
        assert args.reason == "operator_cancelled"

    def test_workflow_event_limit_default(self):
        parser = create_parser()
        args = parser.parse_args(["workflow", "events", "--workflow-id", "wf-1"])
        assert args.event_limit == 100

    def test_chat_defaults(self):
        parser = create_parser()
        args = parser.parse_args(["chat"])
        assert args.role == "director"
        assert args.mode == "interactive"
        assert args.host == "127.0.0.1"
        assert args.port == 50000
        assert args.goal == ""
        assert args.debug is False

    def test_chat_prompt_style_choices(self):
        parser = create_parser()
        args = parser.parse_args(["chat", "--prompt-style", "omp"])
        assert args.prompt_style == "omp"


class TestEdgeCases:
    def test_serialize_workflow_submission_missing_attrs(self):
        mock = MagicMock()
        del mock.submitted
        del mock.status
        del mock.workflow_id
        del mock.workflow_run_id
        del mock.error
        del mock.details
        result = _serialize_workflow_submission(mock)
        assert result["submitted"] is False
        assert result["status"] == ""
        assert result["details"] == {}

    def test_resolve_workspace_with_tilde(self):
        result = _resolve_workspace("~")
        assert isinstance(result, str)
        assert len(result) > 0
