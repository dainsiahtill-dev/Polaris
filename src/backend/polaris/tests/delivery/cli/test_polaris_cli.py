"""Tests for the retired polaris_cli compatibility module."""

from __future__ import annotations

import argparse
import json
import os
from unittest.mock import MagicMock

import pytest
from polaris.delivery.cli import polaris_cli
from polaris.delivery.cli.polaris_cli import (
    _bind_workspace_environment,
    _default_workflow_run_id,
    _ensure_cli_runtime_bindings,
    _kernel_fs_for_workspace,
    _read_workspace_json,
    _resolve_workspace,
    _serialize_workflow_submission,
    create_parser,
    main,
)


class TestCompatibilityParser:
    def test_create_parser_delegates_to_canonical_host(self) -> None:
        parser = create_parser()

        assert parser.prog == "polaris"
        subparsers_actions = [
            action
            for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)  # type: ignore[attr-defined]
        ]
        choices = subparsers_actions[0].choices
        assert {"console", "task", "session", "serve", "cell"}.issubset(set(choices.keys()))

    def test_main_delegates_retired_aliases_to_canonical_router(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import polaris.delivery.cli.__main__ as canonical_cli

        monkeypatch.setattr(canonical_cli, "_bootstrap_runtime", lambda: None)

        for argv in (
            ["--workspace", str(tmp_path), "chat", "--role", "director"],
            ["--workspace", str(tmp_path), "status", "--role", "director"],
            ["--workspace", str(tmp_path), "workflow", "status", "--workflow-id", "wf-1"],
        ):
            assert main(argv) == 1

        err = capsys.readouterr().err
        assert "retired" in err
        assert "retired command host" in err

    def test_retired_module_no_longer_owns_execution_dispatch(self) -> None:
        assert not hasattr(polaris_cli, "_dispatch")
        assert not hasattr(polaris_cli, "_run_chat")
        assert not hasattr(polaris_cli, "_run_status")
        assert not hasattr(polaris_cli, "_run_workflow")
        assert not hasattr(polaris_cli, "_run_console_chat")


class TestCompatibilityHelpers:
    def test_resolve_workspace_relative(self) -> None:
        result = _resolve_workspace(".")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_default_workflow_run_id_format(self) -> None:
        run_id = _default_workflow_run_id()
        assert run_id.startswith("cli-")
        assert len(run_id) > 10

    def test_serialize_workflow_submission(self) -> None:
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

    def test_serialize_workflow_submission_defaults(self) -> None:
        mock = MagicMock()
        mock.submitted = False
        mock.status = None
        mock.workflow_id = None
        mock.workflow_run_id = None
        mock.error = None
        mock.details = "not a dict"

        result = _serialize_workflow_submission(mock)

        assert result["submitted"] is False
        assert result["status"] == ""
        assert result["details"] == {}

    def test_bind_workspace_environment_sets_env(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        monkeypatch.delenv("KERNELONE_CONTEXT_ROOT", raising=False)
        monkeypatch.delenv("KERNELONE_RUNTIME_ROOT", raising=False)

        _bind_workspace_environment(str(tmp_path))

        assert os.environ["KERNELONE_CONTEXT_ROOT"] == str(tmp_path.resolve())
        assert os.environ["KERNELONE_RUNTIME_ROOT"] == str(tmp_path.resolve() / "runtime")

    def test_kernel_fs_for_workspace(self) -> None:
        fs = _kernel_fs_for_workspace(".")
        assert fs is not None

    def test_ensure_cli_runtime_bindings_is_best_effort(self) -> None:
        _ensure_cli_runtime_bindings()


class TestReadWorkspaceJson:
    def test_read_valid_json(self, tmp_path) -> None:
        contracts_dir = tmp_path / "runtime" / "contracts"
        contracts_dir.mkdir(parents=True)
        contract_file = contracts_dir / "pm_tasks.contract.json"
        contract_file.write_text(json.dumps({"tasks": [{"id": 1}]}), encoding="utf-8")

        result = _read_workspace_json(str(tmp_path), "runtime/contracts/pm_tasks.contract.json")

        assert isinstance(result, dict)
        assert "tasks" in result

    def test_read_file_not_found(self, tmp_path) -> None:
        with pytest.raises(SystemExit) as exc_info:
            _read_workspace_json(str(tmp_path), "nonexistent.json")
        assert "not found" in str(exc_info.value).lower()

    def test_read_invalid_json(self, tmp_path) -> None:
        contracts_dir = tmp_path / "runtime" / "contracts"
        contracts_dir.mkdir(parents=True)
        contract_file = contracts_dir / "bad.json"
        contract_file.write_text("not json{", encoding="utf-8")

        with pytest.raises(SystemExit) as exc_info:
            _read_workspace_json(str(tmp_path), "runtime/contracts/bad.json")

        assert "valid json" in str(exc_info.value).lower()

    def test_read_non_dict_json(self, tmp_path) -> None:
        contracts_dir = tmp_path / "runtime" / "contracts"
        contracts_dir.mkdir(parents=True)
        contract_file = contracts_dir / "list.json"
        contract_file.write_text("[1, 2, 3]", encoding="utf-8")

        with pytest.raises(SystemExit) as exc_info:
            _read_workspace_json(str(tmp_path), "runtime/contracts/list.json")

        assert "json object" in str(exc_info.value).lower()

    def test_read_empty_path(self, tmp_path) -> None:
        with pytest.raises(SystemExit) as exc_info:
            _read_workspace_json(str(tmp_path), "")
        assert "required" in str(exc_info.value).lower()
