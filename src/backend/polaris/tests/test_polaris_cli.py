from __future__ import annotations

from pathlib import Path
from typing import Any

from polaris.delivery.cli import __main__ as canonical_cli, polaris_cli


def _disable_runtime_bootstrap(monkeypatch) -> None:
    monkeypatch.setattr(canonical_cli, "_bootstrap_runtime", lambda: None)


def test_polaris_cli_module_console_delegates_to_canonical_host(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}
    _disable_runtime_bootstrap(monkeypatch)

    def _fake_run_role_console(
        *,
        workspace: str | Path,
        role: str = "director",
        backend: str = "auto",
        session_id: str | None = None,
        session_title: str | None = None,
        **_: Any,
    ) -> int:
        captured["workspace"] = workspace
        captured["role"] = role
        captured["backend"] = backend
        captured["session_id"] = session_id
        captured["session_title"] = session_title
        return 23

    monkeypatch.setattr("polaris.delivery.cli.terminal.run_role_console", _fake_run_role_console)

    exit_code = polaris_cli.main(
        [
            "console",
            "--role",
            "director",
            "--workspace",
            str(tmp_path),
            "--backend",
            "plain",
            "--session-id",
            "session-9",
            "--session-title",
            "Polaris Director",
        ]
    )

    assert exit_code == 23
    assert captured["workspace"] == str(tmp_path.resolve())
    assert captured["role"] == "director"
    assert captured["backend"] == "plain"
    assert captured["session_id"] == "session-9"
    assert captured["session_title"] == "Polaris Director"


def test_polaris_cli_module_console_accepts_workspace_after_subcommand(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}
    _disable_runtime_bootstrap(monkeypatch)

    def _fake_run_role_console(
        *,
        workspace: str | Path,
        role: str = "director",
        backend: str = "auto",
        **_: Any,
    ) -> int:
        captured["workspace"] = workspace
        captured["role"] = role
        captured["backend"] = backend
        return 0

    monkeypatch.setattr("polaris.delivery.cli.terminal.run_role_console", _fake_run_role_console)

    exit_code = polaris_cli.main(
        [
            "console",
            "--role",
            "director",
            "--workspace",
            str(tmp_path),
            "--backend",
            "plain",
        ]
    )

    assert exit_code == 0
    assert captured["workspace"] == str(tmp_path.resolve())
    assert captured["role"] == "director"
    assert captured["backend"] == "plain"


def test_polaris_cli_module_retired_aliases_fail_closed(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    _disable_runtime_bootstrap(monkeypatch)

    aliases = (
        ["--workspace", str(tmp_path), "chat", "--role", "director"],
        ["--workspace", str(tmp_path), "status", "--role", "director"],
        ["--workspace", str(tmp_path), "workflow", "status", "--workflow-id", "wf-1"],
    )
    for argv in aliases:
        assert polaris_cli.main(argv) == 1

    err = capsys.readouterr().err
    assert "retired" in err
    assert "retired command host" in err
