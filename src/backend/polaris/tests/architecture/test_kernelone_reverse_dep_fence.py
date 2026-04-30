"""KernelOne Reverse Dependency Fence (Phase 0: Freeze the Bleed)

Hard gate that blocks ALL kernelone/ imports from upper layers:
- polaris.cells
- polaris.domain
- polaris.delivery
- polaris.application
- polaris.infrastructure

Existing violations are tracked in BASELINE_VIOLATIONS.
No new violations may be introduced.

Rules enforced:
- AGENTS.md section 4.2.1: KernelOne Foundation
- Blueprint Phase 0: Block all new kernelone -> upper layer imports
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
KERNELONE_ROOT = BACKEND_ROOT / "polaris" / "kernelone"

FORBIDDEN_LAYERS = frozenset(
    {
        "polaris.cells",
        "polaris.domain",
        "polaris.delivery",
        "polaris.application",
        "polaris.infrastructure",
    }
)

# ---------------------------------------------------------------------------
# Baseline: existing violations tracked for migration retirement.
# Production files (non-test) that import from forbidden layers.
# Test files are tracked separately with a softer budget.
# ---------------------------------------------------------------------------
BASELINE_PRODUCTION_FILES: frozenset[str] = frozenset(
    {
        # Adapter shims (scheduled for port extraction)
        "polaris/kernelone/cognitive/orchestrator.py",
        "polaris/kernelone/context/chunks/assembler.py",
        "polaris/kernelone/context/context_os/models.py",
        "polaris/kernelone/context/context_os/summarizers/slm.py",
        "polaris/kernelone/events/task_trace_events.py",
        "polaris/kernelone/events/uep_publisher.py",
        "polaris/kernelone/fs/registry.py",
        "polaris/kernelone/llm/providers/registry.py",
        "polaris/kernelone/llm/toolkit/__init__.py",
        "polaris/kernelone/multi_agent/bus_port.py",
        "polaris/kernelone/multi_agent/neural_syndicate/nats_broker.py",
        "polaris/kernelone/policy/__init__.py",
        "polaris/kernelone/ports/__init__.py",
        "polaris/kernelone/ports/alignment.py",
        "polaris/kernelone/prompts/meta_prompting.py",
        "polaris/kernelone/runtime/defaults.py",
        "polaris/kernelone/storage/policy.py",
        # Benchmark adapters (scheduled for extraction)
        "polaris/kernelone/benchmark/holographic_runner.py",
        "polaris/kernelone/benchmark/unified_judge.py",
    }
)

# Test files are budgeted separately: these need public test fixtures
BASELINE_TEST_FILE_COUNT = 10  # current count of test files with violations


def _imports_forbidden_layer(module: str) -> str | None:
    """Return the forbidden layer prefix if *module* imports from it, else None."""
    for prefix in FORBIDDEN_LAYERS:
        if module == prefix or module.startswith(prefix + "."):
            return prefix
    return None


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
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


def _is_test_file(rel_path: str) -> bool:
    """Return True if this is a test file."""
    return "/tests/" in rel_path or rel_path.endswith("_test.py") or "/test_" in rel_path.split("/")[-1]


def _scan_violations() -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Return (production_violations, test_violations) as {rel_path: [modules]}."""
    prod: dict[str, list[str]] = {}
    test: dict[str, list[str]] = {}

    if not KERNELONE_ROOT.is_dir():
        return prod, test

    for py_file in sorted(KERNELONE_ROOT.rglob("*.py")):
        try:
            source = py_file.read_text(encoding="utf-8")
        except OSError:
            continue

        imports = _collect_imports(source)
        forbidden = []
        for m in imports:
            layer = _imports_forbidden_layer(m)
            if layer:
                forbidden.append(m)

        if forbidden:
            rel = str(py_file.relative_to(BACKEND_ROOT)).replace("\\", "/")
            if _is_test_file(rel):
                test[rel] = forbidden
            else:
                prod[rel] = forbidden

    return prod, test


class TestKernelOneReverseDependencyFence:
    """Hard gate: no new kernelone -> upper layer imports."""

    def test_no_new_production_reverse_deps(self) -> None:
        """Fail if any production file outside the baseline imports forbidden layers."""
        prod_violations, _ = _scan_violations()
        new_violations = {
            path: modules
            for path, modules in prod_violations.items()
            if path not in BASELINE_PRODUCTION_FILES
        }

        if new_violations:
            lines = [
                "NEW kernelone -> upper layer imports detected in production code (BLOCKER):",
            ]
            for path, modules in sorted(new_violations.items()):
                for m in modules:
                    lines.append(f"  {path} -> {m}")
            lines.append("")
            lines.append("Fix: use kernelone ports/contracts or extract to adapter shim.")
            lines.append("See AGENTS.md section 4.2.1: KernelOne Foundation.")
            pytest.fail("\n".join(lines))

    def test_production_baseline_is_not_growing(self) -> None:
        """Verify no one silently expanded the baseline."""
        max_allowed = 19  # current count as of 2026-04-29
        assert len(BASELINE_PRODUCTION_FILES) <= max_allowed, (
            f"Production baseline has {len(BASELINE_PRODUCTION_FILES)} entries "
            f"but max is {max_allowed}. Baseline must shrink, not grow."
        )

    def test_production_baseline_entries_still_needed(self) -> None:
        """If a baseline file was fixed, remove it from the baseline."""
        prod_violations, _ = _scan_violations()
        fixed = BASELINE_PRODUCTION_FILES - set(prod_violations.keys())
        if fixed:
            lines = [
                "Production baseline entries no longer needed (remove from BASELINE_PRODUCTION_FILES):"
            ]
            for path in sorted(fixed):
                lines.append(f"  {path}")
            pytest.skip("\n".join(lines))

    def test_test_file_violation_budget(self) -> None:
        """Test files have a budget: violations must not exceed the baseline count."""
        _, test_violations = _scan_violations()
        count = len(test_violations)
        if count > BASELINE_TEST_FILE_COUNT:
            lines = [
                f"Test file violation budget exceeded: {count} > {BASELINE_TEST_FILE_COUNT}",
                "New test files must use public contracts, not internal imports.",
                "",
                "New violating test files:",
            ]
            for path in sorted(test_violations.keys()):
                lines.append(f"  {path}")
            pytest.fail("\n".join(lines))

    def test_no_kernelone_imports_from_delivery(self) -> None:
        """KernelOne must NEVER import from delivery (zero tolerance)."""
        prod_violations, test_violations = _scan_violations()
        all_violations = {**prod_violations, **test_violations}

        delivery_imports: list[str] = []
        for path, modules in all_violations.items():
            for m in modules:
                if m.startswith("polaris.delivery"):
                    delivery_imports.append(f"  {path} -> {m}")

        if delivery_imports:
            lines = [
                "KernelOne -> delivery imports detected (ABSOLUTE BLOCKER):",
                *delivery_imports,
                "",
                "This is a zero-tolerance violation. KernelOne must never depend on delivery.",
            ]
            pytest.fail("\n".join(lines))

    def test_no_kernelone_imports_from_application(self) -> None:
        """KernelOne must NEVER import from application (zero tolerance)."""
        prod_violations, test_violations = _scan_violations()
        all_violations = {**prod_violations, **test_violations}

        app_imports: list[str] = []
        for path, modules in all_violations.items():
            for m in modules:
                if m.startswith("polaris.application"):
                    app_imports.append(f"  {path} -> {m}")

        if app_imports:
            lines = [
                "KernelOne -> application imports detected (ABSOLUTE BLOCKER):",
                *app_imports,
                "",
                "This is a zero-tolerance violation. KernelOne must never depend on application.",
            ]
            pytest.fail("\n".join(lines))
