from __future__ import annotations

import os
import sys

BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for candidate in (BACKEND_ROOT,):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from polaris.cells.orchestration.pm_planning.internal.shared_quality import (
    detect_integration_verify_command,
)  # noqa: E402


def test_detect_integration_verify_command_prefers_compileall_for_python_without_tests(
    tmp_path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='demo'\nversion='0.1.0'\n",
        encoding="utf-8",
    )
    app_dir = tmp_path / "app"
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "fastapi_entrypoint.py").write_text("def ready() -> bool:\n    return True\n", encoding="utf-8")

    command = detect_integration_verify_command(str(tmp_path))

    assert command == "python -m compileall -q app"


def test_detect_integration_verify_command_uses_pytest_when_python_tests_exist(
    tmp_path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='demo'\nversion='0.1.0'\n",
        encoding="utf-8",
    )
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "test_main.py").write_text(
        "def test_ready() -> None:\n    assert True\n",
        encoding="utf-8",
    )

    command = detect_integration_verify_command(str(tmp_path))

    assert command == "python -m pytest --collect-only -q"

