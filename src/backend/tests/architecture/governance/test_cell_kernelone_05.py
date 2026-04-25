"""Tests for CELL_KERNELONE_05 governance rule.

Verifies that event publishing uses kernelone.events as the canonical source.

Rule ID: CELL_KERNELONE_05
Severity: high
Description:
    Event publishing must use kernelone.events as canonical source.
    Multiple parallel event emitters must be consolidated.

Evidence:
    - docs/blueprints/CELLS_KERNELONE_INTEGRATION_BLUEPRINT_20260403.md
    - polaris/kernelone/events/fact_events.py
    - polaris/kernelone/events/session_events.py
    - polaris/kernelone/events/__init__.py

Compliance:
    1. emit_fact_event and emit_session_event must be primary interfaces
    2. Local _emit_event patterns in cells should be adapter-specific wrappers
    3. No duplicate canonical event implementation in cells/

Violations:
    - Cells defining their own emit_fact_event implementation
    - Cells defining their own emit_session_event implementation
    - Duplicate _emit_event with full logic instead of delegation
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

BACKEND_ROOT = Path(__file__).resolve().parents[3]
FITNESS_RULES_FILE = BACKEND_ROOT / "docs" / "governance" / "ci" / "fitness-rules.yaml"
CANONICAL_FACT_EVENTS = BACKEND_ROOT / "polaris" / "kernelone" / "events" / "fact_events.py"
CANONICAL_SESSION_EVENTS = BACKEND_ROOT / "polaris" / "kernelone" / "events" / "session_events.py"


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
    """Test that CELL_KERNELONE_05 rule is declared in fitness-rules.yaml."""
    assert FITNESS_RULES_FILE.is_file(), f"missing fitness rules file: {FITNESS_RULES_FILE}"

    payload = yaml.safe_load(FITNESS_RULES_FILE.read_text(encoding="utf-8")) or {}
    rules = payload.get("rules", [])
    rule_ids = {str(item.get("id") or "").strip() for item in rules if isinstance(item, dict)}
    assert "CELL_KERNELONE_05" in rule_ids


def test_rule_has_correct_severity() -> None:
    """Test that CELL_KERNELONE_05 has severity 'high'."""
    payload = yaml.safe_load(FITNESS_RULES_FILE.read_text(encoding="utf-8")) or {}
    rules = payload.get("rules", [])

    for rule in rules:
        if isinstance(rule, dict) and rule.get("id") == "CELL_KERNELONE_05":
            assert rule.get("severity") == "high", "CELL_KERNELONE_05 severity must be 'high'"
            return

    pytest.fail("CELL_KERNELONE_05 rule not found in fitness-rules.yaml")


# =============================================================================
# Test: Canonical Module Existence
# =============================================================================


def test_canonical_fact_events_module_exists() -> None:
    """Test that the canonical fact_events module exists."""
    assert CANONICAL_FACT_EVENTS.is_file(), (
        f"Canonical module not found: {CANONICAL_FACT_EVENTS}. "
        "Fact event emission must be defined in kernelone.events.fact_events."
    )


def test_canonical_session_events_module_exists() -> None:
    """Test that the canonical session_events module exists."""
    assert CANONICAL_SESSION_EVENTS.is_file(), (
        f"Canonical module not found: {CANONICAL_SESSION_EVENTS}. "
        "Session event emission must be defined in kernelone.events.session_events."
    )


def test_canonical_modules_expose_public_api() -> None:
    """Test that canonical modules export required public functions."""
    from polaris.kernelone.events.fact_events import emit_fact_event
    from polaris.kernelone.events.session_events import emit_session_event

    assert callable(emit_fact_event), "emit_fact_event must be callable"
    assert callable(emit_session_event), "emit_session_event must be callable"


# =============================================================================
# Test: emit_fact_event Functionality
# =============================================================================


class TestEmitFactEvent:
    """Test the emit_fact_event function."""

    def test_emit_fact_event_is_callable(self) -> None:
        """Test that emit_fact_event is callable."""
        from polaris.kernelone.events.fact_events import emit_fact_event

        assert callable(emit_fact_event)

    def test_emit_fact_event_signature(self) -> None:
        """Test that emit_fact_event has the expected signature."""
        import inspect
        from polaris.kernelone.events.fact_events import emit_fact_event

        sig = inspect.signature(emit_fact_event)
        params = list(sig.parameters.keys())

        assert "workspace" in params, "emit_fact_event must accept 'workspace' parameter"
        assert "event_name" in params, "emit_fact_event must accept 'event_name' parameter"
        assert "payload" in params, "emit_fact_event must accept 'payload' parameter"

    def test_emit_fact_event_has_optional_actor(self) -> None:
        """Test that emit_fact_event accepts optional actor parameter."""
        import inspect
        from polaris.kernelone.events.fact_events import emit_fact_event

        sig = inspect.signature(emit_fact_event)
        params = sig.parameters

        assert "actor" in params, "emit_fact_event should have 'actor' parameter"
        assert params["actor"].default != inspect.Parameter.empty, "actor should have a default value"


# =============================================================================
# Test: emit_session_event Functionality
# =============================================================================


class TestEmitSessionEvent:
    """Test the emit_session_event function."""

    def test_emit_session_event_is_callable(self) -> None:
        """Test that emit_session_event is callable."""
        from polaris.kernelone.events.session_events import emit_session_event

        assert callable(emit_session_event)

    def test_emit_session_event_signature(self) -> None:
        """Test that emit_session_event has the expected signature."""
        import inspect
        from polaris.kernelone.events.session_events import emit_session_event

        sig = inspect.signature(emit_session_event)
        params = list(sig.parameters.keys())

        assert "workspace" in params, "emit_session_event must accept 'workspace' parameter"
        assert "event_name" in params, "emit_session_event must accept 'event_name' parameter"
        assert "session_id" in params, "emit_session_event must accept 'session_id' parameter"


# =============================================================================
# Test: No Duplicate Event Emitters in Cells
# =============================================================================


def test_no_local_emit_fact_event_in_cells() -> None:
    """Test that no cells define local emit_fact_event implementation."""
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

        # Check for local emit_fact_event definitions
        for i, line in enumerate(content.splitlines(), 1):
            if "def emit_fact_event" in line:
                stripped = line.strip()
                if not stripped.startswith("#"):
                    # Allow if it's importing from kernelone
                    if "from polaris.kernelone.events.fact_events import emit_fact_event" not in content:
                        violations.append(f"{py_file.relative_to(BACKEND_ROOT)}:{i}: {stripped}")

    assert len(violations) == 0, f"Found {len(violations)} local emit_fact_event definitions in cells:\n" + "\n".join(
        violations[:10]
    )


def test_no_local_emit_session_event_in_cells() -> None:
    """Test that no cells define local emit_session_event implementation."""
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

        # Check for local emit_session_event definitions
        for i, line in enumerate(content.splitlines(), 1):
            if "def emit_session_event" in line:
                stripped = line.strip()
                if not stripped.startswith("#"):
                    # Allow if it's importing from kernelone
                    if "from polaris.kernelone.events.session_events import emit_session_event" not in content:
                        violations.append(f"{py_file.relative_to(BACKEND_ROOT)}:{i}: {stripped}")

    assert len(violations) == 0, (
        f"Found {len(violations)} local emit_session_event definitions in cells:\n" + "\n".join(violations[:10])
    )


def test_no_local_emit_event_with_full_logic_in_cells() -> None:
    """Test that no cells have _emit_event with full duplicate logic.

    Cells may have adapter-specific _emit_event wrappers that delegate to
    kernelone.events, but not duplicate canonical implementations.
    """
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

        # Check for _emit_event definitions
        if "def _emit_event" in content or "async def _emit_event" in content:
            # Check if it has file I/O or JSON writing (duplicate logic)
            lines = content.splitlines()
            for i, line in enumerate(lines):
                if "def _emit_event" in line or "async def _emit_event" in line:
                    # Look ahead to see if this function has duplicate logic
                    # Get next 20 lines to check for file operations
                    function_body = "\n".join(lines[i : i + 20])

                    # Duplicate logic indicators
                    has_file_write = any(
                        pattern in function_body for pattern in ["open(", ".write(", "json.dump", "json.dumps"]
                    )
                    has_path_construction = any(
                        pattern in function_body for pattern in ["Path(", "/runtime/", "events/"]
                    )

                    if has_file_write and has_path_construction:
                        violations.append(
                            f"{py_file.relative_to(BACKEND_ROOT)}:{i + 1}: _emit_event has duplicate file I/O logic"
                        )

    assert len(violations) == 0, (
        f"Found {len(violations)} _emit_event definitions with duplicate canonical logic in cells:\n"
        + "\n".join(violations[:10])
    )


# =============================================================================
# Test: Cells Import From Canonical Source
# =============================================================================


def test_cells_import_from_kernelone_events() -> None:
    """Test that cells importing events use kernelone.events."""
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

        if "emit_fact_event" in content or "emit_session_event" in content:
            # Should import from kernelone
            if "polaris.kernelone.events" in content or "kernelone.events" in content:
                importing_cells.append(str(py_file.relative_to(BACKEND_ROOT)))

    # At least some cells should be importing from the canonical source
    assert len(importing_cells) > 0, (
        "No cells appear to import from kernelone.events. The integration may not be complete."
    )


# =============================================================================
# Test: Known Locations
# =============================================================================


def test_known_locations_import_correctly() -> None:
    """Test that known locations with historical event definitions now import correctly."""
    # These files previously had local event definitions
    known_files = [
        BACKEND_ROOT / "polaris" / "cells" / "roles" / "kernel" / "internal" / "events.py",
        BACKEND_ROOT / "polaris" / "cells" / "roles" / "session" / "internal" / "session_persistence.py",
    ]

    violations: list[str] = []

    for file_path in known_files:
        if not file_path.exists():
            continue

        content = file_path.read_text(encoding="utf-8")

        # Should import from kernelone.events if using event functions
        has_kernelone_import = "from polaris.kernelone.events" in content or "from kernelone.events" in content

        # Should NOT have local emit_fact_event or emit_session_event definitions
        # that don't delegate to kernelone
        lines = content.splitlines()
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if ("def emit_fact_event" in line or "def emit_session_event" in line) and not stripped.startswith("#"):
                if not has_kernelone_import:
                    violations.append(f"{file_path.relative_to(BACKEND_ROOT)}:{i}: {stripped}")

    assert len(violations) == 0, (
        f"Found {len(violations)} local event definitions without kernelone imports:\n"
        + "\n".join(violations[:10])
    )


# =============================================================================
# Test: Integration
# =============================================================================


def test_kernelone_events_exports_fact_event() -> None:
    """Test that kernelone.events exports emit_fact_event."""
    from polaris.kernelone.events import emit_fact_event

    assert callable(emit_fact_event)


def test_kernelone_events_exports_session_event() -> None:
    """Test that kernelone.events exports emit_session_event."""
    from polaris.kernelone.events import emit_session_event

    assert callable(emit_session_event)


def test_fact_events_module_exports_emit_fact_event() -> None:
    """Test that the fact_events module exports emit_fact_event."""
    from polaris.kernelone.events import fact_events as module

    assert hasattr(module, "emit_fact_event"), "Missing export: emit_fact_event"
    assert callable(module.emit_fact_event)


def test_session_events_module_exports_emit_session_event() -> None:
    """Test that the session_events module exports emit_session_event."""
    from polaris.kernelone.events import session_events as module

    assert hasattr(module, "emit_session_event"), "Missing export: emit_session_event"
    assert callable(module.emit_session_event)


# =============================================================================
# Test: Event Path Resolution
# =============================================================================


def test_emit_fact_event_resolves_path_correctly() -> None:
    """Test that emit_fact_event resolves the fact event path correctly."""
    from polaris.kernelone.events.fact_events import _resolve_fact_event_path

    path = _resolve_fact_event_path("/workspace")
    assert "runtime/events/" in path or "facts" in path


# =============================================================================
# Test: Architecture Compliance
# =============================================================================


def test_kernelone_events_is_canonical_source() -> None:
    """Test that kernelone.events is the canonical source for events."""
    # Verify the module structure
    events_init = BACKEND_ROOT / "polaris" / "kernelone" / "events" / "__init__.py"

    if events_init.exists():
        content = events_init.read_text(encoding="utf-8")

        # Should export emit_fact_event and emit_session_event
        assert "emit_fact_event" in content, "kernelone.events should export emit_fact_event"
        assert "emit_session_event" in content, "kernelone.events should export emit_session_event"


def test_fact_events_delegates_to_io_events() -> None:
    """Test that fact_events delegates to io_events."""
    fact_events_content = CANONICAL_FACT_EVENTS.read_text(encoding="utf-8")

    # Should import emit_event from io_events
    assert (
        "from polaris.kernelone.events.io_events import emit_event" in fact_events_content
        or "from .io_events import emit_event" in fact_events_content
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
