from __future__ import annotations

from pathlib import Path


def test_no_runtime_imports_from_aider_vendor() -> None:
    root = Path(__file__).resolve().parents[1]
    polaris_root = root / "polaris"

    violations: list[str] = []
    for py_file in polaris_root.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        if (
            "polaris.kernelone.editing.vendor" in content
            or "editing.vendor.aider_core" in content
        ):
            rel = py_file.relative_to(root).as_posix()
            violations.append(rel)

    assert not violations, (
        "Runtime modules must not import from vendored editing modules. "
        f"Violations: {violations}"
    )
