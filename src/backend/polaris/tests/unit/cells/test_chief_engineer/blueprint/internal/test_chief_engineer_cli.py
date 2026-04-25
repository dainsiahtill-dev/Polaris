"""Tests for polaris.cells.chief_engineer.blueprint.internal.chief_engineer_cli.

Covers CLI argument parsing and mode routing through RoleRuntimeService.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from polaris.cells.chief_engineer.blueprint.internal.chief_engineer_cli import main


class TestChiefEngineerCliMain:
    """Tests for chief_engineer_cli main entry point."""

    @pytest.mark.asyncio
    @patch("polaris.cells.chief_engineer.blueprint.internal.chief_engineer_cli.RoleRuntimeService")
    @patch("polaris.cells.chief_engineer.blueprint.internal.chief_engineer_cli.create_role_cli_parser")
    async def test_interactive_mode(self, mock_parser_cls: MagicMock, mock_service_cls: MagicMock) -> None:
        mock_parser = MagicMock()
        mock_args = MagicMock()
        mock_args.mode = "interactive"
        mock_args.workspace = "/ws"
        mock_parser.parse_args.return_value = mock_args
        mock_parser_cls.return_value = mock_parser

        mock_service = MagicMock()
        mock_service.run_interactive = AsyncMock()
        mock_service_cls.return_value = mock_service

        with patch("sys.argv", ["chief_engineer_cli", "--workspace", "/ws", "--mode", "interactive"]):
            result = await main()
        assert result == 0
        mock_service.run_interactive.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("polaris.cells.chief_engineer.blueprint.internal.chief_engineer_cli.RoleRuntimeService")
    @patch("polaris.cells.chief_engineer.blueprint.internal.chief_engineer_cli.create_role_cli_parser")
    async def test_oneshot_mode_requires_goal(self, mock_parser_cls: MagicMock, mock_service_cls: MagicMock) -> None:
        mock_parser = MagicMock()
        mock_args = MagicMock()
        mock_args.mode = "oneshot"
        mock_args.workspace = "/ws"
        mock_args.goal = ""
        mock_parser.parse_args.return_value = mock_args
        mock_parser_cls.return_value = mock_parser

        with patch("sys.argv", ["chief_engineer_cli", "--workspace", "/ws", "--mode", "oneshot"]):
            result = await main()
        assert result == 1

    @pytest.mark.asyncio
    @patch("polaris.cells.chief_engineer.blueprint.internal.chief_engineer_cli.RoleRuntimeService")
    @patch("polaris.cells.chief_engineer.blueprint.internal.chief_engineer_cli.create_role_cli_parser")
    async def test_oneshot_mode_with_goal(self, mock_parser_cls: MagicMock, mock_service_cls: MagicMock) -> None:
        mock_parser = MagicMock()
        mock_args = MagicMock()
        mock_args.mode = "oneshot"
        mock_args.workspace = "/ws"
        mock_args.goal = "Create blueprint"
        mock_parser.parse_args.return_value = mock_args
        mock_parser_cls.return_value = mock_parser

        mock_service = MagicMock()
        mock_service.run_oneshot = AsyncMock(return_value={"result": {"status": "ok"}})
        mock_service_cls.return_value = mock_service

        with patch(
            "sys.argv",
            [
                "chief_engineer_cli",
                "--workspace",
                "/ws",
                "--mode",
                "oneshot",
                "--goal",
                "Create blueprint",
            ],
        ):
            result = await main()
        assert result == 0
        mock_service.run_oneshot.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("polaris.cells.chief_engineer.blueprint.internal.chief_engineer_cli.RoleRuntimeService")
    @patch("polaris.cells.chief_engineer.blueprint.internal.chief_engineer_cli.create_role_cli_parser")
    async def test_autonomous_mode_requires_goal(self, mock_parser_cls: MagicMock, mock_service_cls: MagicMock) -> None:
        mock_parser = MagicMock()
        mock_args = MagicMock()
        mock_args.mode = "autonomous"
        mock_args.workspace = "/ws"
        mock_args.goal = ""
        mock_args.max_iterations = 5
        mock_parser.parse_args.return_value = mock_args
        mock_parser_cls.return_value = mock_parser

        with patch("sys.argv", ["chief_engineer_cli", "--workspace", "/ws", "--mode", "autonomous"]):
            result = await main()
        assert result == 1

    @pytest.mark.asyncio
    @patch("polaris.cells.chief_engineer.blueprint.internal.chief_engineer_cli.RoleRuntimeService")
    @patch("polaris.cells.chief_engineer.blueprint.internal.chief_engineer_cli.create_role_cli_parser")
    async def test_server_mode(self, mock_parser_cls: MagicMock, mock_service_cls: MagicMock) -> None:
        mock_parser = MagicMock()
        mock_args = MagicMock()
        mock_args.mode = "server"
        mock_args.workspace = "/ws"
        mock_args.host = "127.0.0.1"
        mock_args.port = 50002
        mock_parser.parse_args.return_value = mock_args
        mock_parser_cls.return_value = mock_parser

        mock_service = MagicMock()
        mock_service.run_server = AsyncMock()
        mock_service_cls.return_value = mock_service

        with patch("sys.argv", ["chief_engineer_cli", "--workspace", "/ws", "--mode", "server"]):
            result = await main()
        assert result == 0
        mock_service.run_server.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("polaris.cells.chief_engineer.blueprint.internal.chief_engineer_cli.RoleRuntimeService")
    @patch("polaris.cells.chief_engineer.blueprint.internal.chief_engineer_cli.create_role_cli_parser")
    async def test_tui_mode_import_error(self, mock_parser_cls: MagicMock, mock_service_cls: MagicMock) -> None:
        mock_parser = MagicMock()
        mock_args = MagicMock()
        mock_args.mode = "tui"
        mock_args.workspace = "/ws"
        mock_parser.parse_args.return_value = mock_args
        mock_parser_cls.return_value = mock_parser

        with (
            patch(
                "polaris.cells.chief_engineer.blueprint.internal.chief_engineer_cli.run_tui",
                side_effect=ImportError("no textual"),
            ),
            patch("sys.argv", ["chief_engineer_cli", "--workspace", "/ws", "--mode", "tui"]),
        ):
            result = await main()
        assert result == 1
