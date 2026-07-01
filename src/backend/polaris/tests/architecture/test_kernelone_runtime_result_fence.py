"""Architecture fence for KernelOne runtime Result convergence."""

from __future__ import annotations

from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
RUNTIME_ROOT = BACKEND_ROOT / "polaris" / "kernelone" / "runtime"


def test_legacy_runtime_result_module_is_removed() -> None:
    retired_path = RUNTIME_ROOT / "result.py"

    assert not retired_path.exists()


def test_runtime_package_root_does_not_export_errorcodes() -> None:
    runtime_init = (RUNTIME_ROOT / "__init__.py").read_text(encoding="utf-8")

    assert "ErrorCodes" not in runtime_init
    assert "polaris.kernelone.runtime.result" not in runtime_init


def test_production_code_does_not_import_legacy_runtime_result() -> None:
    forbidden = "polaris.kernelone.runtime.result"
    offenders: list[str] = []

    for path in (BACKEND_ROOT / "polaris").rglob("*.py"):
        if "__pycache__" in path.parts or "tests" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        if forbidden in source:
            offenders.append(str(path.relative_to(BACKEND_ROOT)))

    assert offenders == []
