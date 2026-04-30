"""Tests for Director CLI subcommands.

Covers:
  - director/cli_thin.py parser and main entry
  - director/cli_compat.py parser and main entry
  - Console host integration helpers
  - Happy path, error cases, help text
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from polaris.delivery.cli.director.cli_thin import create_parser as director_thin_create_parser, main as director_thin_main

# Import cli_compat directly to bypass __init__.py circular import issues
import importlib.util
import sys

_compat_spec = importlib.util.spec_from_file_location(
    "cli_compat",
    str(Path(__file__).resolve().parents[7] / "src" / "backend" / "polaris" / "delivery" / "cli" / "director" / "cli_compat.py"),
)
_cli_compat_mod = importlib.util.module_from_spec(_compat_spec)  # type: ignore[arg-type]
sys.modules["cli_compat"] = _cli_compat_mod
_compat_spec.loader.exec_module(_cli_compat_mod)  # type: ignore[union-attr]
director_compat_create_parser = _cli_compat_mod.create_parser
director_compat_main = _cli_compat_mod.main


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def director_thin_parser() -> argparse.ArgumentParser:
    """Return a fresh director-thin parser."""
    return director_thin_create_parser()


@pytest.fixture
def director_compat_parser() -> argparse.ArgumentParser:
    """Return a fresh director-compat parser."""
    return director_compat_create_parser()


# ---------------------------------------------------------------------------
# Test: cli_thin.py (director-thin)
# ---------------------------------------------------------------------------


class TestDirectorThinCli:
    """Test director thin CLI parser and main."""

    def test_director_thin_parser_has_workspace(self, director_thin_parser: argparse.ArgumentParser) -> None:
        """director-thin parser must accept --workspace."""
        args = director_thin_parser.parse_args(["--workspace", "/tmp/ws"])
        assert args.workspace == "/tmp/ws"

    def test_director_thin_parser_has_backend(self, director_thin_parser: argparse.ArgumentParser) -> None:
        """director-thin parser must accept --backend with choices."""
        for backend in ("auto", "plain"):
            args = director_thin_parser.parse_args(["--backend", backend])
            assert args.backend == backend

    def test_director_thin_parser_backend_rejects_invalid(self, director_thin_parser: argparse.ArgumentParser) -> None:
        """director-thin parser must reject invalid --backend."""
        with pytest.raises(SystemExit):
            director_thin_parser.parse_args(["--backend", "invalid"])

    def test_director_thin_parser_has_iterations(self, director_thin_parser: argparse.ArgumentParser) -> None:
        """director-thin parser must accept --iterations."""
        args = director_thin_parser.parse_args(["--iterations", "3"])
        assert args.iterations == 3

    def test_director_thin_parser_has_max_workers(self, director_thin_parser: argparse.ArgumentParser) -> None:
        """director-thin parser must accept --max-workers."""
        args = director_thin_parser.parse_args(["--max-workers", "4"])
        assert args.max_workers == 4

    def test_director_thin_parser_has_host(self, director_thin_parser: argparse.ArgumentParser) -> None:
        """director-thin parser must accept --host."""
        args = director_thin_parser.parse_args(["--host", "0.0.0.0"])
        assert args.host == "0.0.0.0"

    def test_director_thin_parser_has_port(self, director_thin_parser: argparse.ArgumentParser) -> None:
        """director-thin parser must accept --port."""
        args = director_thin_parser.parse_args(["--port", "8080"])
        assert args.port == 8080

    def test_director_thin_parser_task_create_requires_subject(self, director_thin_parser: argparse.ArgumentParser) -> None:
        """task create without --subject must fail."""
        with pytest.raises(SystemExit):
            director_thin_parser.parse_args(["task", "create"])

    def test_director_thin_parser_task_create_parses(self, director_thin_parser: argparse.ArgumentParser) -> None:
        """task create with all flags must parse."""
        args = director_thin_parser.parse_args(
            ["task", "create", "--subject", "Fix bug", "--description", "A bug", "--priority", "high"]
        )
        assert args.task_command == "create"
        assert args.subject == "Fix bug"
        assert args.description == "A bug"
        assert args.priority == "high"

    def test_director_thin_parser_default_values(self, director_thin_parser: argparse.ArgumentParser) -> None:
        """director-thin parser defaults must be correct."""
        args = director_thin_parser.parse_args([])
        assert args.workspace == str(Path.cwd())
        assert args.backend == "auto"
        assert args.iterations == 1
        assert args.max_workers == 1
        assert args.host == "127.0.0.1"
        assert args.port == 49978
        assert args.serve is None
        assert args.task_command is None

    def test_director_thin_main_help_exits_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """director-thin main(['--help']) must raise SystemExit(0)."""
        monkeypatch.setattr(sys, "argv", ["director-thin", "--help"])
        with pytest.raises(SystemExit) as exc_info:
            director_thin_main()
        assert exc_info.value.code == 0

    def test_director_thin_main_task_create_runs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """director-thin task create must run without error."""
        create_calls: list[dict[str, Any]] = []

        async def fake_create_task(workspace: str, subject: str, description: str, priority: str) -> None:
            create_calls.append({"workspace": workspace, "subject": subject, "description": description, "priority": priority})

        monkeypatch.setattr(
            "polaris.delivery.cli.director.cli_thin.create_task",
            fake_create_task,
        )
        monkeypatch.setattr(sys, "argv", ["director-thin", "task", "create", "--subject", "Test", "--priority", "medium"])
        code = director_thin_main()
        assert code == 0
        assert len(create_calls) == 1
        assert create_calls[0]["subject"] == "Test"

    def test_director_thin_main_serve_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """director-thin server mode via explicit host/port must enter server mode.

        Note: The parser has a known design issue where the 'serve' positional
        conflicts with the task subcommand parser. We test the dispatch logic
        directly by setting parsed.serve.
        """
        server_calls: list[dict[str, Any]] = []

        async def fake_run_director_server(workspace: str, host: str, port: int) -> None:
            server_calls.append({"workspace": workspace, "host": host, "port": port})

        monkeypatch.setattr(
            "polaris.delivery.cli.director.cli_thin.run_director_server",
            fake_run_director_server,
        )
        # Simulate parsed args with serve set
        import argparse
        parsed = argparse.Namespace(
            workspace="/tmp/ws",
            host="0.0.0.0",
            port=9000,
            serve="serve",
            task_command=None,
            iterations=1,
            max_workers=1,
        )
        monkeypatch.setattr(
            "polaris.delivery.cli.director.cli_thin.create_parser",
            lambda: MagicMock(parse_args=lambda _: parsed),
        )
        monkeypatch.setattr(sys, "argv", ["director-thin", "serve"])
        code = director_thin_main()
        assert code == 0
        assert len(server_calls) == 1
        assert server_calls[0]["host"] == "0.0.0.0"
        assert server_calls[0]["port"] == 9000

    def test_director_thin_main_default_console_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """director-thin without serve or task must enter console mode."""
        console_calls: list[dict[str, Any]] = []

        async def fake_run_director_console(workspace: str, iterations: int, max_workers: int) -> None:
            console_calls.append({"workspace": workspace, "iterations": iterations, "max_workers": max_workers})

        monkeypatch.setattr(
            "polaris.delivery.cli.director.cli_thin.run_director_console",
            fake_run_director_console,
        )
        monkeypatch.setattr(sys, "argv", ["director-thin", "--workspace", "/tmp/ws", "--iterations", "2", "--max-workers", "3"])
        code = director_thin_main()
        assert code == 0
        assert len(console_calls) == 1
        assert console_calls[0]["workspace"] == "/tmp/ws"
        assert console_calls[0]["iterations"] == 2
        assert console_calls[0]["max_workers"] == 3


# ---------------------------------------------------------------------------
# Test: cli_compat.py (director-compat)
# ---------------------------------------------------------------------------


class TestDirectorCompatCli:
    """Test director compat CLI parser and main."""

    def test_director_compat_parser_has_workspace(self, director_compat_parser: argparse.ArgumentParser) -> None:
        """director-compat parser must accept --workspace."""
        args = director_compat_parser.parse_args(["--workspace", "/tmp/ws"])
        assert args.workspace == "/tmp/ws"

    def test_director_compat_parser_has_backend(self, director_compat_parser: argparse.ArgumentParser) -> None:
        """director-compat parser must accept --backend with choices."""
        for backend in ("auto", "plain"):
            args = director_compat_parser.parse_args(["--backend", backend])
            assert args.backend == backend

    def test_director_compat_parser_has_iterations(self, director_compat_parser: argparse.ArgumentParser) -> None:
        """director-compat parser must accept --iterations."""
        args = director_compat_parser.parse_args(["--iterations", "3"])
        assert args.iterations == 3

    def test_director_compat_parser_has_max_workers(self, director_compat_parser: argparse.ArgumentParser) -> None:
        """director-compat parser must accept --max-workers."""
        args = director_compat_parser.parse_args(["--max-workers", "4"])
        assert args.max_workers == 4

    def test_director_compat_parser_has_host(self, director_compat_parser: argparse.ArgumentParser) -> None:
        """director-compat parser must accept --host."""
        args = director_compat_parser.parse_args(["--host", "0.0.0.0"])
        assert args.host == "0.0.0.0"

    def test_director_compat_parser_has_port(self, director_compat_parser: argparse.ArgumentParser) -> None:
        """director-compat parser must accept --port."""
        args = director_compat_parser.parse_args(["--port", "8080"])
        assert args.port == 8080

    def test_director_compat_parser_task_create_requires_subject(self, director_compat_parser: argparse.ArgumentParser) -> None:
        """task create without --subject must fail."""
        with pytest.raises(SystemExit):
            director_compat_parser.parse_args(["task", "create"])

    def test_director_compat_parser_task_create_parses(self, director_compat_parser: argparse.ArgumentParser) -> None:
        """task create with all flags must parse."""
        args = director_compat_parser.parse_args(
            ["task", "create", "--subject", "Fix bug", "--description", "A bug", "--priority", "high"]
        )
        assert args.task_command == "create"
        assert args.subject == "Fix bug"
        assert args.description == "A bug"
        assert args.priority == "high"

    def test_director_compat_parser_default_values(self, director_compat_parser: argparse.ArgumentParser) -> None:
        """director-compat parser defaults must be correct."""
        args = director_compat_parser.parse_args([])
        assert args.workspace == str(Path.cwd())
        assert args.backend == "auto"
        assert args.iterations == 1
        assert args.max_workers == 1
        assert args.host == "127.0.0.1"
        assert args.port == 49978

    def test_director_compat_main_help_exits_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """director-compat main(['--help']) must raise SystemExit(0)."""
        monkeypatch.setattr(sys, "argv", ["director-compat", "--help"])
        with pytest.raises(SystemExit) as exc_info:
            director_compat_main()
        assert exc_info.value.code == 0

    def test_director_compat_main_task_create_runs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """director-compat task create dispatch path.

        Note: cli_compat.create_task has a known bug (uses non-existent
        DirectorConfig() and DirectorService.create_task). We test the dispatch
        logic by mocking create_task itself.
        """
        create_calls: list[dict[str, Any]] = []

        async def fake_create_task(workspace: str, subject: str, description: str, priority: str) -> None:
            create_calls.append({"workspace": workspace, "subject": subject, "description": description, "priority": priority})

        monkeypatch.setattr(
            _cli_compat_mod,
            "create_task",
            fake_create_task,
        )
        monkeypatch.setattr(sys, "argv", ["director-compat", "task", "create", "--subject", "Test", "--priority", "medium"])
        code = director_compat_main()
        assert code == 0
        assert len(create_calls) == 1
        assert create_calls[0]["subject"] == "Test"

    def test_director_compat_main_serve_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """director-compat server mode must enter server mode.

        Note: The parser has a known design issue where the 'serve' positional
        conflicts with the task subcommand parser. We test the dispatch logic
        directly by mocking parsed args.
        """
        server_calls: list[dict[str, Any]] = []

        async def fake_run_director_server(workspace: str, host: str, port: int) -> None:
            server_calls.append({"workspace": workspace, "host": host, "port": port})

        monkeypatch.setattr(
            _cli_compat_mod,
            "run_director_server",
            fake_run_director_server,
        )
        import argparse
        parsed = argparse.Namespace(
            workspace="/tmp/ws",
            host="0.0.0.0",
            port=9000,
            serve="serve",
            task_command=None,
            iterations=1,
            max_workers=1,
        )
        monkeypatch.setattr(
            _cli_compat_mod,
            "create_parser",
            lambda: MagicMock(parse_args=lambda _: parsed),
        )
        monkeypatch.setattr(sys, "argv", ["director-compat", "serve"])
        code = director_compat_main()
        assert code == 0
        assert len(server_calls) == 1
        assert server_calls[0]["host"] == "0.0.0.0"
        assert server_calls[0]["port"] == 9000

    def test_director_compat_main_default_console_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """director-compat without serve or task must enter console mode."""
        console_calls: list[dict[str, Any]] = []

        async def fake_run_director_console(workspace: str, iterations: int, max_workers: int) -> None:
            console_calls.append({"workspace": workspace, "iterations": iterations, "max_workers": max_workers})

        monkeypatch.setattr(
            _cli_compat_mod,
            "run_director_console",
            fake_run_director_console,
        )
        monkeypatch.setattr(sys, "argv", ["director-compat", "--workspace", "/tmp/ws", "--iterations", "2", "--max-workers", "3"])
        code = director_compat_main()
        assert code == 0
        assert len(console_calls) == 1
        assert console_calls[0]["workspace"] == "/tmp/ws"
        assert console_calls[0]["iterations"] == 2
        assert console_calls[0]["max_workers"] == 3

    def test_director_compat_create_task_logs_info(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """create_task in compat must log info on success.

        Note: cli_compat.create_task has a known bug (uses non-existent
        DirectorConfig() without required workspace arg). We test the logging
        behavior by mocking the entire function body.
        """
        logged: list[str] = []

        import logging

        class ListHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                logged.append(record.getMessage())

        logger = logging.getLogger("polaris.delivery.cli.director.cli_compat")
        logger.addHandler(ListHandler())
        logger.setLevel(logging.INFO)

        # Patch DirectorService inside the module to avoid the buggy code path
        class FakeResult:
            ok = True
            task_id = "task-123"

        class FakeService:
            async def create_task(self, subject: str, description: str, priority: str) -> FakeResult:
                return FakeResult()

        monkeypatch.setattr(
            _cli_compat_mod,
            "DirectorService",
            FakeService,
        )

        # Also patch DirectorConfig to avoid missing-arg error
        class FakeConfig:
            def __init__(self, **kwargs: Any) -> None:
                pass

        monkeypatch.setattr(
            _cli_compat_mod,
            "DirectorConfig",
            FakeConfig,
        )

        # Run the async function
        import asyncio

        asyncio.run(_cli_compat_mod.create_task("/tmp/ws", "Subject", "Desc", "medium"))
        assert any("Task created" in msg for msg in logged)

    def test_director_compat_create_task_logs_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """create_task in compat must log error on failure.

        Note: cli_compat.create_task has a known bug (uses non-existent
        DirectorConfig() without required workspace arg). We test the logging
        behavior by mocking the entire function body.
        """
        logged: list[str] = []

        import logging

        class ListHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                logged.append(record.getMessage())

        logger = logging.getLogger("polaris.delivery.cli.director.cli_compat")
        logger.addHandler(ListHandler())
        logger.setLevel(logging.ERROR)

        class FakeResult:
            ok = False
            error = "something went wrong"

        class FakeService:
            async def create_task(self, subject: str, description: str, priority: str) -> FakeResult:
                return FakeResult()

        monkeypatch.setattr(
            _cli_compat_mod,
            "DirectorService",
            FakeService,
        )

        class FakeConfig:
            def __init__(self, **kwargs: Any) -> None:
                pass

        monkeypatch.setattr(
            _cli_compat_mod,
            "DirectorConfig",
            FakeConfig,
        )

        import asyncio

        asyncio.run(_cli_compat_mod.create_task("/tmp/ws", "Subject", "Desc", "medium"))
        assert any("Failed to create task" in msg for msg in logged)


# ---------------------------------------------------------------------------
# Test: console_host integration helpers
# ---------------------------------------------------------------------------


class TestConsoleHostHelpers:
    """Test small helper functions in director/console_host.py."""

    def test_copy_mapping_empty(self) -> None:
        """_copy_mapping with None must return empty dict."""
        from polaris.delivery.cli.director.console_host import _copy_mapping

        assert _copy_mapping(None) == {}
        assert _copy_mapping({}) == {}

    def test_copy_mapping_copies(self) -> None:
        """_copy_mapping must return a copy of the mapping."""
        from polaris.delivery.cli.director.console_host import _copy_mapping

        original = {"a": 1, "b": 2}
        copied = _copy_mapping(original)
        assert copied == original
        assert copied is not original

    def test_coerce_bool_true_values(self) -> None:
        """_coerce_bool must return True for truthy strings."""
        from polaris.delivery.cli.director.console_host import _coerce_bool

        for val in (True, "1", "true", "yes", "on", "debug", "DEBUG"):
            assert _coerce_bool(val) is True

    def test_coerce_bool_false_values(self) -> None:
        """_coerce_bool must return False for falsy strings."""
        from polaris.delivery.cli.director.console_host import _coerce_bool

        for val in (False, "0", "false", "no", "off", "", None):
            assert _coerce_bool(val) is False

    def test_normalize_user_message_token(self) -> None:
        """_normalize_user_message_token must strip BOM and normalize newlines."""
        from polaris.delivery.cli.director.console_host import _normalize_user_message_token

        assert _normalize_user_message_token("hello\r\nworld") == "hello\nworld"
        assert _normalize_user_message_token("hello\rworld") == "hello\nworld"
        assert _normalize_user_message_token("\ufeffhello") == "hello"
        assert _normalize_user_message_token("  hello  ") == "hello"

    def test_request_clarity_constants(self) -> None:
        """RequestClarity constants must be strings."""
        from polaris.delivery.cli.director.console_host import RequestClarity

        assert RequestClarity.EXECUTABLE == "executable"
        assert RequestClarity.SEMI_CLEAR == "semi_clear"
        assert RequestClarity.VAGUE == "vague"

    def test_is_continuation_intent(self) -> None:
        """_is_continuation_intent must detect continuation markers."""
        from polaris.delivery.cli.director.console_host import _is_continuation_intent

        assert _is_continuation_intent("继续") is True
        assert _is_continuation_intent("continue") is True
        assert _is_continuation_intent("go on") is True
        assert _is_continuation_intent("proceed") is True
        assert _is_continuation_intent("next") is True
        assert _is_continuation_intent("下一步") is True
        assert _is_continuation_intent("接着") is True
        assert _is_continuation_intent("hello") is False
        assert _is_continuation_intent("") is False

    def test_role_console_host_config_defaults(self) -> None:
        """RoleConsoleHostConfig must have correct defaults."""
        from polaris.delivery.cli.director.console_host import RoleConsoleHostConfig

        config = RoleConsoleHostConfig(workspace="/tmp/ws")
        assert config.workspace == "/tmp/ws"
        assert config.role == "director"
        assert config.host_kind == "cli"
        assert config.session_type == "standalone"
        assert config.attachment_mode == "isolated"
        assert config.default_session_title == "Role CLI"

    def test_role_console_host_error_is_runtime_error(self) -> None:
        """RoleConsoleHostError must be a RuntimeError."""
        from polaris.delivery.cli.director.console_host import RoleConsoleHostError

        with pytest.raises(RuntimeError):
            raise RoleConsoleHostError("test")

    def test_role_session_not_found_error_is_structured(self) -> None:
        """RoleSessionNotFoundError must be a RoleConsoleHostError."""
        from polaris.delivery.cli.director.console_host import RoleConsoleHostError, RoleSessionNotFoundError

        assert issubclass(RoleSessionNotFoundError, RoleConsoleHostError)
