"""Delivery -> Cell Internal Import Fence (Phase 0: Freeze the Bleed)

Hard gate that blocks ALL delivery/ imports from cells.*.internal paths.
Existing violations are tracked in BASELINE_VIOLATIONS; no new ones may be added.

Rules enforced:
- AGENTS.md section 4.3: Public/Internal Fence
- Blueprint Phase 0: Block all new delivery -> cells.*.internal imports

This test operates in fail-on-new mode: existing baseline violations are
tolerated during migration, but any NEW violation causes immediate failure.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
DELIVERY_ROOT = BACKEND_ROOT / "polaris" / "delivery"

# ---------------------------------------------------------------------------
# Baseline: existing violations tracked for migration retirement.
# Each entry is (relative_path_from_backend, imported_module_fragment).
# These MUST shrink over time; adding new entries is prohibited.
# ---------------------------------------------------------------------------
BASELINE_VIOLATIONS: frozenset[str] = frozenset(
    {
        "polaris/delivery/cli/pm/chief_engineer_llm_tools.py",
        "polaris/delivery/cli/director/director_llm_tools.py",
        "polaris/delivery/cli/terminal_console.py",
        "polaris/delivery/cli/director/console_host.py",
        "polaris/delivery/http/routers/test_role_session_context_memory_router.py",
        "polaris/delivery/http/routers/test_agent_router_canonical.py",
        "polaris/delivery/cli/director/audit_decorator.py",
        "polaris/delivery/http/routers/runtime.py",
        "polaris/delivery/cli/audit/audit/handlers.py",
        "polaris/delivery/http/middleware/metrics.py",
        "polaris/delivery/cli/director/tests/test_orchestrator_e2e_integration.py",
        "polaris/delivery/cli/director/tests/test_console_host_e2e_smoke.py",
        "polaris/delivery/cli/tests/test_terminal_console.py",
        "polaris/delivery/cli/pm/orchestration_engine.py",
    }
)

# Pattern: any import from polaris.cells.X.Y.internal
_CELLS_INTERNAL_SEGMENTS = ("polaris", "cells")


def _is_cells_internal_import(module: str) -> bool:
    """Return True if *module* references polaris.cells.*.*.internal."""
    parts = module.split(".")
    if len(parts) < 5:
        return False
    if parts[0] == "polaris" and parts[1] == "cells" and "internal" in parts:
        return True
    return False


def _collect_imports(source: str) -> list[str]:
    """Parse *source* and return all imported module names."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.append(node.module)
    return modules


def _scan_delivery_violations() -> dict[str, list[str]]:
    """Return {relative_path: [violating_module, ...]} for delivery/ files."""
    violations: dict[str, list[str]] = {}
    if not DELIVERY_ROOT.is_dir():
        return violations

    for py_file in sorted(DELIVERY_ROOT.rglob("*.py")):
        try:
            source = py_file.read_text(encoding="utf-8")
        except OSError:
            continue

        imported = _collect_imports(source)
        bad = [m for m in imported if _is_cells_internal_import(m)]
        if bad:
            rel = str(py_file.relative_to(BACKEND_ROOT)).replace("\\", "/")
            violations[rel] = bad
    return violations


class TestDeliveryInternalImportFence:
    """Hard gate: no new delivery -> cells.*.internal imports."""

    def test_no_new_delivery_cells_internal_imports(self) -> None:
        """Fail if ANY file outside the baseline imports cells.*.internal."""
        all_violations = _scan_delivery_violations()
        new_violations = {
            path: modules
            for path, modules in all_violations.items()
            if path not in BASELINE_VIOLATIONS
        }

        if new_violations:
            lines = ["NEW delivery -> cells.*.internal imports detected (BLOCKER):"]
            for path, modules in sorted(new_violations.items()):
                for m in modules:
                    lines.append(f"  {path} -> {m}")
            lines.append("")
            lines.append("Fix: use public contracts (cells.*.public) instead.")
            lines.append("See AGENTS.md section 4.3: Public/Internal Fence.")
            pytest.fail("\n".join(lines))

    def test_baseline_is_not_growing(self) -> None:
        """Verify that no one silently added entries to the baseline."""
        max_allowed = 14  # current count as of 2026-04-25
        assert len(BASELINE_VIOLATIONS) <= max_allowed, (
            f"Baseline has {len(BASELINE_VIOLATIONS)} entries but max is "
            f"{max_allowed}. Baseline must shrink, not grow."
        )

    def test_baseline_violations_still_exist(self) -> None:
        """If a baseline file was already fixed, remove it from the baseline."""
        all_violations = _scan_delivery_violations()
        fixed = BASELINE_VIOLATIONS - set(all_violations.keys())
        if fixed:
            lines = [
                "Baseline entries no longer have violations (please remove from BASELINE_VIOLATIONS):"
            ]
            for path in sorted(fixed):
                lines.append(f"  {path}")
            # This is a soft warning, not a blocker
            pytest.skip("\n".join(lines))

    def test_no_delivery_direct_cell_orchestration(self) -> None:
        """Delivery must not directly instantiate or call Cell runtime orchestrators.

        This catches patterns like:
        - from polaris.cells.roles.runtime.internal.session_orchestrator import ...
        - RoleSessionOrchestrator(...)

        These should go through application/ facades.
        """
        orchestrator_patterns = [
            "RoleSessionOrchestrator(",
            "DevelopmentWorkflowRuntime(",
            "TurnTransactionController(",
        ]
        violations: list[str] = []

        if not DELIVERY_ROOT.is_dir():
            return

        for py_file in sorted(DELIVERY_ROOT.rglob("*.py")):
            rel = str(py_file.relative_to(BACKEND_ROOT)).replace("\\", "/")
            if rel in BASELINE_VIOLATIONS:
                continue
            # Skip test files for this particular check
            if "/tests/" in rel or rel.endswith("_test.py"):
                continue

            try:
                source = py_file.read_text(encoding="utf-8")
            except OSError:
                continue

            for pattern in orchestrator_patterns:
                if pattern in source:
                    violations.append(f"  {rel}: direct usage of {pattern.rstrip('(')}")

        if violations:
            lines = [
                "Delivery layer directly orchestrates Cell runtime (BLOCKER):",
                *violations,
                "",
                "Fix: route through application/ facades.",
                "See Blueprint section 8: delivery must stop bypassing application.",
            ]
            pytest.fail("\n".join(lines))
