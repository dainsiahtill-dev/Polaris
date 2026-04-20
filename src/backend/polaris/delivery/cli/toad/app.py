"""Minimal toad-compatible wrapper over the canonical Polaris role console."""

from __future__ import annotations

import sys
from pathlib import Path


def _bootstrap_backend_import_path():
    """Lazy import of polaris modules after path bootstrap."""
    if __package__:
        # Already in a package, imports should work
        pass
    else:
        # Running as script - ensure backend is in path
        backend_root = Path(__file__).resolve().parents[4]
        backend_root_str = str(backend_root)
        if backend_root_str not in sys.path:
            sys.path.insert(0, backend_root_str)

    from polaris.delivery.cli.terminal_console import PolarisRoleConsole, run_role_console

    return PolarisRoleConsole, run_role_console


class ToadApp:
    """Minimal runnable toad surface backed by the canonical role console."""

    def __init__(self) -> None:
        PolarisRoleConsole, _ = _bootstrap_backend_import_path()
        self._console = PolarisRoleConsole


def run_toad(
    *,
    workspace: str | Path = ".",
    role: str = "director",
    backend: str = "auto",
    session_id: str | None = None,
    session_title: str | None = None,
    prompt_style: str | None = None,
    omp_config: str | None = None,
    json_render: str | None = None,
) -> int:
    _, run_role_console = _bootstrap_backend_import_path()
    return run_role_console(
        workspace=workspace,
        role=role,
        backend=backend,
        session_id=session_id,
        session_title=session_title,
        prompt_style=prompt_style,
        omp_config=omp_config,
        json_render=json_render,
    )
