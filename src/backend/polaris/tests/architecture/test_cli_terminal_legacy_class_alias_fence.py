"""Architecture guard for retired terminal console class aliases."""

from __future__ import annotations

from pathlib import Path

from polaris.delivery.cli import director as director_package, terminal

_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_TERMINAL_CONSOLE = _BACKEND_ROOT / "polaris" / "delivery" / "cli" / "terminal" / "console.py"
_TERMINAL_INIT = _BACKEND_ROOT / "polaris" / "delivery" / "cli" / "terminal" / "__init__.py"
_DIRECTOR_INIT = _BACKEND_ROOT / "polaris" / "delivery" / "cli" / "director" / "__init__.py"


def test_terminal_console_does_not_export_retired_lazy_claude_alias() -> None:
    """Role console callers must use the explicit current class name."""
    assert hasattr(terminal, "PolarisRoleConsole")
    assert not hasattr(terminal, "PolarisLazyClaude")
    assert not hasattr(director_package, "PolarisLazyClaude")

    for path in (_TERMINAL_CONSOLE, _TERMINAL_INIT, _DIRECTOR_INIT):
        source = path.read_text(encoding="utf-8")
        assert "PolarisLazyClaude" not in source
