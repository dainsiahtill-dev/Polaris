from __future__ import annotations

from polaris.delivery.cli import __main__ as cli_main, polaris_cli


def test_main_parser_accepts_log_level_flag() -> None:
    parser = cli_main.create_parser()
    args = parser.parse_args(["--log-level", "info", "status"])
    assert args.log_level == "info"


def test_main_parser_accepts_log_level_after_subcommand() -> None:
    parser = cli_main.create_parser()
    args = parser.parse_args(["console", "--log-level", "error"])
    assert args.log_level == "error"


def test_main_parser_accepts_console_super_flag() -> None:
    parser = cli_main.create_parser()
    args = parser.parse_args(["console", "--super"])
    assert args.super is True


def test_polaris_cli_parser_accepts_log_level_flag() -> None:
    parser = polaris_cli.create_parser()
    args = parser.parse_args(["--log-level", "warn", "status"])
    assert args.log_level == "warn"


def test_polaris_cli_parser_accepts_log_level_after_subcommand() -> None:
    parser = polaris_cli.create_parser()
    args = parser.parse_args(["chat", "--log-level", "info"])
    assert args.log_level == "info"
