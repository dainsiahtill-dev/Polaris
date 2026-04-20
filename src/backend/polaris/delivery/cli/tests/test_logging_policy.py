from __future__ import annotations

import logging

import pytest
from polaris.delivery.cli.logging_policy import (
    configure_cli_logging,
    normalize_log_level,
    resolve_log_level,
)


def test_normalize_warn_alias() -> None:
    assert normalize_log_level("warn") == "warning"


def test_normalize_error_level() -> None:
    assert normalize_log_level("error") == "error"


def test_normalize_invalid_level_raises() -> None:
    with pytest.raises(ValueError):
        normalize_log_level("trace")


def test_resolve_prefers_explicit_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLARIS_CLI_LOG_LEVEL", "error")
    assert resolve_log_level("info") == "info"


def test_resolve_reads_env_when_flag_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLARIS_CLI_LOG_LEVEL", "warn")
    assert resolve_log_level(None) == "warning"


def test_configure_cli_logging_returns_numeric_level() -> None:
    level = configure_cli_logging("error")
    assert level == logging.ERROR
    assert logging.getLogger().getEffectiveLevel() == logging.ERROR
