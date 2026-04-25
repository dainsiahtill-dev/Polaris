"""Tests for polaris.cells.chief_engineer.blueprint.internal.ce_consumer_cli.

Covers CLI argument parsing, workspace resolution, and run modes.
"""

from __future__ import annotations

import argparse
import asyncio
from unittest.mock import MagicMock, patch

import pytest
from polaris.cells.chief_engineer.blueprint.internal.ce_consumer_cli import (
    _resolve_workspace,
    main,
    run_continuous,
    run_once,
)


class TestResolveWorkspace:
    """Tests for _resolve_workspace."""

    def test_from_args(self) -> None:
        args = argparse.Namespace(workspace="/test/workspace")
        assert _resolve_workspace(args) == "/test/workspace"

    def test_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KERNELONE_WORKSPACE", "/env/workspace")
        args = argparse.Namespace(workspace="")
        assert _resolve_workspace(args) == "/env/workspace"

    def test_missing_raises(self) -> None:
        args = argparse.Namespace(workspace="")
        with pytest.raises(ValueError, match="workspace"):
            _resolve_workspace(args)


class TestRunOnce:
    """Tests for run_once."""

    def test_successful_run(self) -> None:
        consumer = MagicMock()
        consumer.poll_once.return_value = [
            {"task_id": "t1", "ok": True},
        ]
        args = argparse.Namespace()
        result = asyncio.run(run_once(consumer, args))
        assert result == 0

    def test_failed_tasks_return_nonzero(self) -> None:
        consumer = MagicMock()
        consumer.poll_once.return_value = [
            {"task_id": "t1", "ok": False},
        ]
        args = argparse.Namespace()
        result = asyncio.run(run_once(consumer, args))
        assert result == 1

    def test_empty_results_return_zero(self) -> None:
        consumer = MagicMock()
        consumer.poll_once.return_value = []
        args = argparse.Namespace()
        result = asyncio.run(run_once(consumer, args))
        assert result == 0


class TestRunContinuous:
    """Tests for run_continuous."""

    def test_interrupt_stops_gracefully(self) -> None:
        consumer = MagicMock()
        consumer.run.side_effect = KeyboardInterrupt
        args = argparse.Namespace()
        result = asyncio.run(run_continuous(consumer, args))
        assert result == 0
        consumer.stop.assert_called_once()


class TestMain:
    """Tests for main entry point."""

    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer_cli.CEConsumer")
    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer_cli._resolve_workspace")
    def test_oneshot_mode(self, mock_resolve: MagicMock, mock_consumer_cls: MagicMock) -> None:
        mock_resolve.return_value = "/ws"
        mock_consumer = MagicMock()
        mock_consumer.poll_once.return_value = []
        mock_consumer_cls.return_value = mock_consumer

        with patch("sys.argv", ["ce_consumer_cli", "--workspace", "/ws", "--mode", "once"]):
            result = asyncio.run(main())
        assert result == 0

    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer_cli.CEConsumer")
    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer_cli._resolve_workspace")
    def test_continuous_mode(self, mock_resolve: MagicMock, mock_consumer_cls: MagicMock) -> None:
        mock_resolve.return_value = "/ws"
        mock_consumer = MagicMock()
        mock_consumer.run.side_effect = KeyboardInterrupt
        mock_consumer_cls.return_value = mock_consumer

        with patch("sys.argv", ["ce_consumer_cli", "--workspace", "/ws", "--mode", "continuous"]):
            result = asyncio.run(main())
        assert result == 0

    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer_cli._resolve_workspace")
    def test_missing_workspace_errors(self, mock_resolve: MagicMock) -> None:
        mock_resolve.side_effect = ValueError("workspace required")

        with patch("sys.argv", ["ce_consumer_cli"]), patch("argparse.ArgumentParser.error") as mock_error:
            mock_error.side_effect = SystemExit(2)
            with pytest.raises(SystemExit):
                asyncio.run(main())
