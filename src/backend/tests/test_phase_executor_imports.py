from __future__ import annotations

from pathlib import Path

from polaris.domain.entities.policy import Policy
from polaris.domain.state_machine import PhaseExecutor


def test_check_python_imports_accepts_local_workspace_packages(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    package_dir = workspace / "src"
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / "role_agent_service.py").write_text(
        "def build_health_report() -> dict[str, str]:\n"
        "    return {'status': 'ok'}\n",
        encoding="utf-8",
    )

    executor = PhaseExecutor(str(workspace), Policy(), snapshot_enabled=False)
    source = "from src.role_agent_service import build_health_report\n"

    unresolved = executor._check_python_imports(source, "src/fastapi_entrypoint.py")

    assert unresolved == []


def test_check_python_imports_reports_missing_workspace_packages(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    executor = PhaseExecutor(str(workspace), Policy(), snapshot_enabled=False)
    source = "from missing_package.service import build_health_report\n"

    unresolved = executor._check_python_imports(source, "src/fastapi_entrypoint.py")

    assert unresolved == ["src/fastapi_entrypoint.py: missing_package.service"]
