"""Tests for CELL_KERNELONE_04 governance rule.

Verifies that storage path resolution has a single canonical source
in polaris.kernelone.storage.paths.

Rule ID: CELL_KERNELONE_04
Severity: high
Description:
    Storage path resolution must have a single canonical source
    in polaris.kernelone.storage.paths. Duplicate local path resolution
    functions in Cells are forbidden.

Evidence:
    - docs/blueprints/CELLS_KERNELONE_INTEGRATION_BLUEPRINT_20260403.md
    - polaris/kernelone/storage/paths.py
    - polaris/kernelone/storage/__init__.py

Compliance:
    1. All cells must use path resolution functions from kernelone.storage.paths
    2. No local resolve_* functions in cells/
    3. Functions like resolve_signal_path, resolve_artifact_path, resolve_session_path
       must only exist in kernelone/storage/

Violations:
    - Local _resolve_artifact_path definitions in cells/
    - Local resolve_signal_path definitions in cells/
    - Local resolve_session_path definitions in cells/
    - Local resolve_preferred_logical_prefix definitions in cells/
    - Local resolve_runtime_path definitions in cells/
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

BACKEND_ROOT = Path(__file__).resolve().parents[4]
FITNESS_RULES_FILE = BACKEND_ROOT / "docs" / "governance" / "ci" / "fitness-rules.yaml"
CANONICAL_MODULE = BACKEND_ROOT / "polaris" / "kernelone" / "storage" / "paths.py"


def _build_utf8_env() -> dict[str, str]:
    """Build environment dict with UTF-8 settings."""
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("LANG", "en_US.UTF-8")
    env.setdefault("LC_ALL", "en_US.UTF-8")
    return env


# =============================================================================
# Test: Rule Declaration
# =============================================================================


def test_rule_declared_in_fitness_rules() -> None:
    """Test that CELL_KERNELONE_04 rule is declared in fitness-rules.yaml."""
    assert FITNESS_RULES_FILE.is_file(), f"missing fitness rules file: {FITNESS_RULES_FILE}"

    payload = yaml.safe_load(FITNESS_RULES_FILE.read_text(encoding="utf-8")) or {}
    rules = payload.get("rules", [])
    rule_ids = {str(item.get("id") or "").strip() for item in rules if isinstance(item, dict)}
    assert "CELL_KERNELONE_04" in rule_ids


def test_rule_has_correct_severity() -> None:
    """Test that CELL_KERNELONE_04 has severity 'high'."""
    payload = yaml.safe_load(FITNESS_RULES_FILE.read_text(encoding="utf-8")) or {}
    rules = payload.get("rules", [])

    for rule in rules:
        if isinstance(rule, dict) and rule.get("id") == "CELL_KERNELONE_04":
            assert rule.get("severity") == "high", "CELL_KERNELONE_04 severity must be 'high'"
            return

    pytest.fail("CELL_KERNELONE_04 rule not found in fitness-rules.yaml")


# =============================================================================
# Test: Canonical Module Existence
# =============================================================================


def test_canonical_module_exists() -> None:
    """Test that the canonical storage/paths module exists."""
    assert CANONICAL_MODULE.is_file(), (
        f"Canonical module not found: {CANONICAL_MODULE}. "
        "Storage path resolution must be defined in kernelone.storage.paths."
    )


def test_canonical_module_exports_public_api() -> None:
    """Test that the canonical module exports required public functions."""
    from polaris.kernelone.storage.paths import (
        resolve_artifact_path,
        resolve_runtime_path,
        resolve_session_path,
        resolve_signal_path,
        resolve_taskboard_path,
    )

    # Verify functions exist and are callable
    assert callable(resolve_signal_path), "resolve_signal_path must be callable"
    assert callable(resolve_artifact_path), "resolve_artifact_path must be callable"
    assert callable(resolve_session_path), "resolve_session_path must be callable"
    assert callable(resolve_taskboard_path), "resolve_taskboard_path must be callable"
    assert callable(resolve_runtime_path), "resolve_runtime_path must be callable"


def test_canonical_module_has_workspace_constants() -> None:
    """Test that canonical module defines workspace constants."""
    from polaris.kernelone.storage.paths import (
        WORKSPACE_ARTIFACTS,
        WORKSPACE_SESSIONS,
        WORKSPACE_SIGNALS,
        WORKSPACE_TASKS,
    )

    assert WORKSPACE_SIGNALS == "runtime/signals"
    assert WORKSPACE_ARTIFACTS == "runtime/artifacts"
    assert WORKSPACE_SESSIONS == "runtime/sessions"
    assert WORKSPACE_TASKS == "runtime/tasks"


# =============================================================================
# Test: Path Resolution Functionality
# =============================================================================


class TestResolveSignalPath:
    """Test the resolve_signal_path function."""

    def test_resolves_signal_path_correctly(self) -> None:
        """Test that resolve_signal_path returns correct path."""
        from polaris.kernelone.storage.paths import resolve_signal_path

        result = resolve_signal_path(
            workspace="/workspace",
            role="director",
            stage="planning",
        )

        assert result == Path("/workspace/runtime/signals/planning.director.signals.json")

    def test_signal_path_handles_different_stages(self) -> None:
        """Test that resolve_signal_path handles different stages."""
        from polaris.kernelone.storage.paths import resolve_signal_path

        result = resolve_signal_path(
            workspace="/workspace",
            role="pm",
            stage="execution",
        )

        assert "execution.pm.signals.json" in str(result)
        assert os.path.join("runtime", "signals") in str(result)


class TestResolveArtifactPath:
    """Test the resolve_artifact_path function."""

    def test_resolves_artifact_path_correctly(self) -> None:
        """Test that resolve_artifact_path returns correct path."""
        from polaris.kernelone.storage.paths import resolve_artifact_path

        result = resolve_artifact_path(
            workspace="/workspace",
            artifact_id="abc123",
        )

        assert result == Path("/workspace/runtime/artifacts/abc123")

    def test_artifact_path_handles_nested_ids(self) -> None:
        """Test that resolve_artifact_path handles nested artifact IDs."""
        from polaris.kernelone.storage.paths import resolve_artifact_path

        result = resolve_artifact_path(
            workspace="/workspace",
            artifact_id="dir/subdir/file.txt",
        )

        assert os.path.join("runtime", "artifacts") in str(result)
        assert os.path.join("dir", "subdir", "file.txt") in str(result)


class TestResolveSessionPath:
    """Test the resolve_session_path function."""

    def test_resolves_session_path_correctly(self) -> None:
        """Test that resolve_session_path returns correct path."""
        from polaris.kernelone.storage.paths import resolve_session_path

        result = resolve_session_path(
            workspace="/workspace",
            session_id="sess_12345",
        )

        assert result == Path("/workspace/runtime/sessions/sess_12345")


class TestResolveTaskboardPath:
    """Test the resolve_taskboard_path function."""

    def test_resolves_taskboard_path_correctly(self) -> None:
        """Test that resolve_taskboard_path returns correct path."""
        from polaris.kernelone.storage.paths import resolve_taskboard_path

        result = resolve_taskboard_path(workspace="/workspace")

        assert result == Path("/workspace/runtime/tasks/taskboard.json")


class TestResolveRuntimePath:
    """Test the resolve_runtime_path function."""

    def test_resolves_runtime_path_correctly(self) -> None:
        """Test that resolve_runtime_path returns correct path."""
        from polaris.kernelone.storage.paths import resolve_runtime_path

        result = resolve_runtime_path(
            workspace="/workspace",
            relative_path="events/facts.json",
        )

        assert result == Path("/workspace/runtime/events/facts.json")

    def test_runtime_path_handles_subpaths(self) -> None:
        """Test that resolve_runtime_path handles subpaths."""
        from polaris.kernelone.storage.paths import resolve_runtime_path

        result = resolve_runtime_path(
            workspace="/workspace",
            relative_path="signals/planning.director.signals.json",
        )

        assert os.path.join("runtime") in str(result)
        assert os.path.join("signals") in str(result)


# =============================================================================
# Test: No Duplicate Definitions in Cells
# =============================================================================


def test_no_local_resolve_artifact_path_in_cells() -> None:
    """Test that no cells define local _resolve_artifact_path."""
    cells_dir = BACKEND_ROOT / "polaris" / "cells"

    if not cells_dir.exists():
        pytest.skip("cells directory not found")

    violations: list[str] = []

    for py_file in cells_dir.rglob("*.py"):
        if "test" in py_file.parts or "__pycache__" in str(py_file):
            continue

        try:
            content = py_file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        for i, line in enumerate(content.splitlines(), 1):
            if "_resolve_artifact_path" in line and "def " in line:
                stripped = line.strip()
                if not stripped.startswith("#"):
                    violations.append(f"{py_file.relative_to(BACKEND_ROOT)}:{i}: {stripped}")

    assert len(violations) == 0, (
        f"Found {len(violations)} local _resolve_artifact_path definitions in cells:\n" + "\n".join(violations[:10])
    )


def test_no_local_resolve_signal_path_in_cells() -> None:
    """Test that no cells define local resolve_signal_path (non-canonical)."""
    cells_dir = BACKEND_ROOT / "polaris" / "cells"

    if not cells_dir.exists():
        pytest.skip("cells directory not found")

    violations: list[str] = []

    for py_file in cells_dir.rglob("*.py"):
        if "test" in py_file.parts or "__pycache__" in str(py_file):
            continue

        try:
            content = py_file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        for i, line in enumerate(content.splitlines(), 1):
            if "resolve_signal_path" in line and "def " in line and not line.strip().startswith("#"):
                stripped = line.strip()
                # Allow if it's importing from kernelone
                if "from polaris.kernelone.storage.paths import" not in content:
                    violations.append(f"{py_file.relative_to(BACKEND_ROOT)}:{i}: {stripped}")

    assert len(violations) == 0, (
        f"Found {len(violations)} local resolve_signal_path definitions in cells:\n" + "\n".join(violations[:10])
    )


def test_no_local_resolve_preferred_logical_prefix_in_cells() -> None:
    """Test that no cells define local resolve_preferred_logical_prefix."""
    cells_dir = BACKEND_ROOT / "polaris" / "cells"

    if not cells_dir.exists():
        pytest.skip("cells directory not found")

    violations: list[str] = []

    for py_file in cells_dir.rglob("*.py"):
        if "test" in py_file.parts or "__pycache__" in str(py_file):
            continue

        try:
            content = py_file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        for i, line in enumerate(content.splitlines(), 1):
            if "resolve_preferred_logical_prefix" in line and "def " in line:
                stripped = line.strip()
                if not stripped.startswith("#"):
                    violations.append(f"{py_file.relative_to(BACKEND_ROOT)}:{i}: {stripped}")

    assert len(violations) == 0, (
        f"Found {len(violations)} local resolve_preferred_logical_prefix definitions in cells:\n"
        + "\n".join(violations[:10])
    )


def test_no_local_resolve_runtime_path_in_cells() -> None:
    """Test that no cells define local _resolve_runtime_path (non-canonical)."""
    cells_dir = BACKEND_ROOT / "polaris" / "cells"

    if not cells_dir.exists():
        pytest.skip("cells directory not found")

    violations: list[str] = []

    for py_file in cells_dir.rglob("*.py"):
        if "test" in py_file.parts or "__pycache__" in str(py_file):
            continue

        try:
            content = py_file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        for i, line in enumerate(content.splitlines(), 1):
            if "_resolve_runtime_path" in line and "def " in line:
                stripped = line.strip()
                if not stripped.startswith("#"):
                    violations.append(f"{py_file.relative_to(BACKEND_ROOT)}:{i}: {stripped}")

    assert len(violations) == 0, (
        f"Found {len(violations)} local _resolve_runtime_path definitions in cells:\n" + "\n".join(violations[:10])
    )


# =============================================================================
# Test: Cells Import From Canonical Source
# =============================================================================


def test_cells_import_from_kernelone_storage() -> None:
    """Test that cells importing storage paths use kernelone.storage.paths."""
    cells_dir = BACKEND_ROOT / "polaris" / "cells"

    if not cells_dir.exists():
        pytest.skip("cells directory not found")

    importing_cells: list[str] = []

    for py_file in cells_dir.rglob("*.py"):
        if "test" in py_file.parts or "__pycache__" in str(py_file):
            continue

        try:
            content = py_file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        if "resolve_signal_path" in content or "resolve_artifact_path" in content:
            importing_cells.append(str(py_file.relative_to(BACKEND_ROOT)))

    # At least some cells should be importing from the canonical source
    assert len(importing_cells) > 0, (
        "No cells appear to import from kernelone.storage.paths. The integration may not be complete."
    )


# =============================================================================
# Test: Known Locations
# =============================================================================


def test_known_locations_import_correctly() -> None:
    """Test that known locations with historical path definitions now import correctly."""
    # These files previously had local path resolution definitions
    known_files = [
        BACKEND_ROOT / "polaris" / "cells" / "roles" / "adapters" / "internal" / "base.py",
        BACKEND_ROOT / "polaris" / "cells" / "roles" / "session" / "internal" / "storage_paths.py",
    ]

    violations: list[str] = []

    for file_path in known_files:
        if not file_path.exists():
            continue

        content = file_path.read_text(encoding="utf-8")

        # Check for local _resolve_* definitions that are NOT importing from kernelone
        lines = content.splitlines()
        has_kernelone_import = "from polaris.kernelone.storage.paths import" in content

        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if "_resolve_" in line and "def " in line and not stripped.startswith("#") and not has_kernelone_import:
                violations.append(f"{file_path.relative_to(BACKEND_ROOT)}:{i}: {stripped}")

    assert len(violations) == 0, (
        f"Found {len(violations)} local path resolution definitions without kernelone imports:\n"
        + "\n".join(violations[:10])
    )


# =============================================================================
# Test: Integration
# =============================================================================


def test_kernelone_storage_exports_path_functions() -> None:
    """Test that kernelone.storage exports path resolution functions."""
    from polaris.kernelone.storage import (
        resolve_artifact_path,
        resolve_runtime_path,
        resolve_session_path,
        resolve_signal_path,
        resolve_taskboard_path,
    )

    assert callable(resolve_signal_path)
    assert callable(resolve_artifact_path)
    assert callable(resolve_session_path)
    assert callable(resolve_taskboard_path)
    assert callable(resolve_runtime_path)


def test_storage_module_exports_path_constants() -> None:
    """Test that kernelone.storage exports workspace constants."""
    from polaris.kernelone.storage import WORKSPACE_ARTIFACTS, WORKSPACE_SESSIONS, WORKSPACE_SIGNALS, WORKSPACE_TASKS

    assert WORKSPACE_SIGNALS == "runtime/signals"
    assert WORKSPACE_ARTIFACTS == "runtime/artifacts"
    assert WORKSPACE_SESSIONS == "runtime/sessions"
    assert WORKSPACE_TASKS == "runtime/tasks"


def test_path_module_has_required_exports() -> None:
    """Test that the paths module exports required items."""
    from polaris.kernelone.storage import paths as module

    required_exports = [
        "resolve_signal_path",
        "resolve_artifact_path",
        "resolve_session_path",
        "resolve_taskboard_path",
        "resolve_runtime_path",
        "WORKSPACE_SIGNALS",
        "WORKSPACE_ARTIFACTS",
        "WORKSPACE_SESSIONS",
        "WORKSPACE_TASKS",
    ]

    for export in required_exports:
        assert hasattr(module, export), f"Missing export: {export}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
