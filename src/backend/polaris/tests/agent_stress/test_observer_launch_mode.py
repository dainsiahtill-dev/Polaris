from __future__ import annotations

import importlib
import runpy

observer_main = importlib.import_module("polaris.tests.agent_stress.observer.main")


def test_package_main_delegates_to_the_canonical_entry_without_starting_services(monkeypatch) -> None:
    calls = []

    def entry() -> int:
        calls.append("canonical")
        return 0

    monkeypatch.setattr(observer_main, "main", entry)
    result = runpy.run_module("polaris.tests.agent_stress.observer", run_name="__main__")
    assert result["main"] is entry
    assert calls == ["canonical"]


def test_should_spawn_new_console_for_terminal_hosted_local_sessions(
    monkeypatch,
) -> None:
    monkeypatch.setattr(observer_main, "IS_WINDOWS", True)

    assert (
        observer_main._should_spawn_new_console_window(
            {
                "SESSIONNAME": "Console",
                "VSCODE_PID": "11128",
            }
        )
        is True
    )


def test_should_spawn_new_console_for_plain_windows_console(
    monkeypatch,
) -> None:
    monkeypatch.setattr(observer_main, "IS_WINDOWS", True)

    assert (
        observer_main._should_spawn_new_console_window(
            {
                "SESSIONNAME": "Console",
            }
        )
        is True
    )


def test_should_not_spawn_new_console_for_ci_or_remote_sessions(
    monkeypatch,
) -> None:
    monkeypatch.setattr(observer_main, "IS_WINDOWS", True)

    assert (
        observer_main._should_spawn_new_console_window(
            {
                "SESSIONNAME": "Console",
                "CI": "1",
            }
        )
        is False
    )
