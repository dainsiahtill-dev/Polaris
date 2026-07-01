"""Architecture fence for the retired KernelOne policy package root."""

from __future__ import annotations

from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
POLICY_ROOT = BACKEND_ROOT / "polaris" / "kernelone" / "policy"


def test_kernelone_policy_package_root_is_retired() -> None:
    """KernelOne must not keep policy package source beside policy.permission cells."""
    policy_sources = [
        path.relative_to(BACKEND_ROOT).as_posix()
        for path in POLICY_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts
    ]

    assert policy_sources == []


def test_kernelone_reverse_dep_baseline_does_not_allow_retired_policy_root() -> None:
    """The reverse-dependency fence must not keep budget for the retired package."""
    fence_source = (
        BACKEND_ROOT / "polaris" / "tests" / "architecture" / "test_kernelone_reverse_dep_fence.py"
    ).read_text(encoding="utf-8")

    assert "polaris/kernelone/policy/__init__.py" not in fence_source
