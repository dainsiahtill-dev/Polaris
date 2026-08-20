from __future__ import annotations

import argparse
from pathlib import Path

from polaris.delivery.cli.backend import _runtime_root_for_args


def _args(workspace: Path, runtime_root: Path | None) -> argparse.Namespace:
    return argparse.Namespace(
        workspace=str(workspace),
        runtime_root=str(runtime_root) if runtime_root is not None else "",
    )


def test_backend_normalizes_legacy_workspace_root_to_project_local_runtime(tmp_path: Path) -> None:
    workspace = tmp_path / "project"

    resolved = _runtime_root_for_args(_args(workspace, workspace))

    assert Path(resolved).resolve() == (workspace / ".polaris" / "runtime").resolve()


def test_backend_normalizes_legacy_workspace_runtime_to_project_local_runtime(tmp_path: Path) -> None:
    workspace = tmp_path / "project"

    resolved = _runtime_root_for_args(_args(workspace, workspace / "runtime"))

    assert Path(resolved).resolve() == (workspace / ".polaris" / "runtime").resolve()


def test_backend_preserves_explicit_external_runtime_opt_in(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    external = tmp_path / "external" / "projects" / "project" / "runtime"

    resolved = _runtime_root_for_args(_args(workspace, external))

    assert Path(resolved).resolve() == external.resolve()
