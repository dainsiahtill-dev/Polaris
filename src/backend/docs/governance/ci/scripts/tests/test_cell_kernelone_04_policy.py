"""Tests for CELL_KERNELONE_04 path resolution governance."""

from __future__ import annotations

from pathlib import Path

from docs.governance.ci.scripts.cell_kernelone_04_policy import (
    RULE_ID,
    evaluate_cell_kernelone_04,
)
from docs.governance.ci.scripts.check_cell_kernelone_04 import CellKernelone04Checker


def _write_source(workspace: Path, relative_path: str, content: str) -> None:
    """Write a UTF-8 Python source fixture."""
    path = workspace / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_canonical_kernelone_paths(workspace: Path) -> None:
    """Write the minimal canonical KernelOne path source required by the policy."""
    _write_source(
        workspace,
        "polaris/kernelone/storage/paths.py",
        """
from pathlib import Path


def resolve_artifact_path(workspace: str, artifact_id: str) -> Path:
    return Path(workspace) / "runtime" / "artifacts" / artifact_id
""",
    )


def test_cell_kernelone_04_entrypoints_use_canonical_policy(tmp_path: Path) -> None:
    """The standalone and aggregate-style entrypoints must match the policy."""
    _write_canonical_kernelone_paths(tmp_path)
    _write_source(
        tmp_path,
        "polaris/cells/example/internal/paths.py",
        """
def resolve_artifact_path(workspace_full: str, cache_root_full: str, rel_path: str) -> str:
    return f"{workspace_full}/{rel_path}"
""",
    )

    policy = evaluate_cell_kernelone_04(tmp_path)
    standalone = CellKernelone04Checker(tmp_path).check()

    assert policy.passed is False
    assert standalone.passed is False
    assert standalone.rule_id == policy.rule_id == RULE_ID
    assert standalone.violations == list(policy.violations)
    assert any("polaris/cells/example/internal/paths.py" in violation for violation in policy.violations)


def test_cell_kernelone_04_policy_allows_kernelone_delegating_wrapper(tmp_path: Path) -> None:
    """Cell compatibility wrappers may delegate to KernelOne storage helpers."""
    _write_canonical_kernelone_paths(tmp_path)
    _write_source(
        tmp_path,
        "polaris/cells/runtime/artifact_store/internal/artifact_paths.py",
        """
from polaris.kernelone.storage.io_paths import resolve_artifact_path as _kernelone_resolve_artifact_path


def resolve_artifact_path(workspace_full: str, cache_root_full: str, rel_path: str) -> str:
    return _kernelone_resolve_artifact_path(workspace_full, cache_root_full, rel_path)
""",
    )

    result = evaluate_cell_kernelone_04(tmp_path)

    assert result.passed is True
    assert result.violations == ()
    assert result.details["delegating_wrappers"][0]["target"].endswith("resolve_artifact_path")


def test_cell_kernelone_04_policy_allows_public_service_lazy_proxy(tmp_path: Path) -> None:
    """Projection helpers may lazily call the runtime artifact store public API."""
    _write_canonical_kernelone_paths(tmp_path)
    _write_source(
        tmp_path,
        "polaris/cells/runtime/projection/internal/io_helpers.py",
        """
def resolve_artifact_path(workspace_full: str, cache_root_full: str, rel_path: str) -> str:
    from polaris.cells.runtime.artifact_store.public.service import resolve_artifact_path as _resolve

    return _resolve(workspace_full, cache_root_full, rel_path)
""",
    )

    result = evaluate_cell_kernelone_04(tmp_path)

    assert result.passed is True
    assert result.violations == ()
    assert result.details["delegating_wrappers"][0]["target"].endswith("resolve_artifact_path")


def test_cell_kernelone_04_policy_requires_canonical_source(tmp_path: Path) -> None:
    """A Cell-local resolver cannot pass when KernelOne has no canonical source."""
    _write_source(
        tmp_path,
        "polaris/cells/example/internal/paths.py",
        """
def resolve_artifact_path(workspace_full: str, cache_root_full: str, rel_path: str) -> str:
    return rel_path
""",
    )

    result = evaluate_cell_kernelone_04(tmp_path)

    assert result.passed is False
    assert result.violations == ("Canonical path resolution not found in polaris/kernelone/storage/",)
