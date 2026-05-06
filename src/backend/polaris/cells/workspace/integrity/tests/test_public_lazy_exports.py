"""Regression tests for workspace.integrity public lazy exports."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_public_package_imports_lazy_helpers_as_callables_in_fresh_process() -> None:
    """Package-level imports must not bind lazy public helpers to ``None``."""
    code = (
        "from tempfile import TemporaryDirectory\n"
        "from polaris.cells.workspace.integrity.public import (\n"
        "    build_docs_templates,\n"
        "    detect_project_profile,\n"
        "    select_docs_target_root,\n"
        "    workspace_has_docs,\n"
        ")\n"
        "for name, value in {\n"
        "    'build_docs_templates': build_docs_templates,\n"
        "    'detect_project_profile': detect_project_profile,\n"
        "    'select_docs_target_root': select_docs_target_root,\n"
        "    'workspace_has_docs': workspace_has_docs,\n"
        "}.items():\n"
        "    assert callable(value), f'{name} imported as {value!r}'\n"
        "with TemporaryDirectory() as workspace:\n"
        "    docs = build_docs_templates(workspace, 'manual', {'goal': 'Ship Polaris'}, [])\n"
        "    assert 'docs/product/requirements.md' in docs\n"
        "    assert 'Ship Polaris' in docs['docs/product/requirements.md']\n"
    )
    env = os.environ.copy()
    backend_path = str(Path.cwd() / "src" / "backend")
    env["PYTHONPATH"] = backend_path + os.pathsep + env.get("PYTHONPATH", "")

    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )

    assert result.returncode == 0, result.stderr or result.stdout
