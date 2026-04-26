"""Architecture guard tests - enforce file size limits and structure.

These tests ensure that large files are properly split and maintainable.
File size limits are based on the Phase 9 architecture consolidation plan.
"""

from pathlib import Path

import pytest

# Maximum lines per file (Phase 9 targets)
MAX_LINES = {
    "orchestration_engine.py": 900,
    "worker_executor.py": 1200,
    "polaris_engine.py": 1000,
}

# Backend paths to check
BACKEND_DIR = Path(__file__).resolve().parents[1]
PM_CLI_DIR = BACKEND_DIR / "polaris" / "delivery" / "cli" / "pm"
DIRECTOR_EXECUTION_INTERNAL_DIR = BACKEND_DIR / "polaris" / "cells" / "director" / "execution" / "internal"
DIRECTOR_TASKING_INTERNAL_DIR = BACKEND_DIR / "polaris" / "cells" / "director" / "tasking" / "internal"
COURT_WORKFLOW_INTERNAL_DIR = BACKEND_DIR / "polaris" / "cells" / "docs" / "court_workflow" / "internal"
PM_DISPATCH_INTERNAL_DIR = BACKEND_DIR / "polaris" / "cells" / "orchestration" / "pm_dispatch" / "internal"
PM_PLANNING_INTERNAL_DIR = BACKEND_DIR / "polaris" / "cells" / "orchestration" / "pm_planning" / "internal"
RUNTIME_STATE_OWNER_INTERNAL_DIR = BACKEND_DIR / "polaris" / "cells" / "runtime" / "state_owner" / "internal"
CHIEF_ENGINEER_BLUEPRINT_INTERNAL_DIR = BACKEND_DIR / "polaris" / "cells" / "chief_engineer" / "blueprint" / "internal"


def get_line_count(file_path: Path) -> int:
    """Get line count of a file."""
    if not file_path.exists():
        return 0
    return len(file_path.read_text(encoding="utf-8").splitlines())


def test_orchestration_engine_size():
    """Verify orchestration_engine.py is under limit.

    Target: <900 lines
    Current: ~3308 lines (needs splitting)
    """
    file_path = PM_CLI_DIR / "orchestration_engine.py"
    if not file_path.exists():
        pytest.skip("File does not exist")

    lines = get_line_count(file_path)
    limit = MAX_LINES["orchestration_engine.py"]

    # Mark as xfail until refactoring is complete
    if lines > limit:
        pytest.xfail(
            f"orchestration_engine.py has {lines} lines (limit: {limit}). "
            f"Refactoring in progress - see Phase 9 architecture consolidation plan."
        )

    assert lines <= limit, f"orchestration_engine.py has {lines} lines (limit: {limit})"


def test_worker_executor_size():
    """Verify worker_executor.py is under limit.

    Target: <1200 lines (canonical location: director.tasking/internal/)
    Phase 3 complete: implementation migrated from director.execution/internal/.
    """
    file_path = DIRECTOR_TASKING_INTERNAL_DIR / "worker_executor.py"
    if not file_path.exists():
        pytest.skip("File does not exist")

    lines = get_line_count(file_path)
    limit = MAX_LINES["worker_executor.py"]

    assert lines <= limit, f"worker_executor.py has {lines} lines (limit: {limit})"


def test_polaris_engine_size():
    """Verify polaris_engine.py is under limit (if exists).

    Target: <1000 lines
    """
    file_path = PM_CLI_DIR / "polaris_engine.py"
    if not file_path.exists():
        pytest.skip("File does not exist")

    lines = get_line_count(file_path)
    limit = MAX_LINES["polaris_engine.py"]

    if lines > limit:
        pytest.xfail(f"polaris_engine.py has {lines} lines (limit: {limit}). Refactoring may be needed.")

    assert lines <= limit, f"polaris_engine.py has {lines} lines (limit: {limit})"


def test_no_duplicate_merge_logic():
    """Verify there's only one merge_director_status implementation."""
    backend_dir = BACKEND_DIR
    if not backend_dir.exists():
        pytest.skip("Backend directory does not exist")

    implementations = []
    for py_file in backend_dir.rglob("*.py"):
        # Skip test files
        if "test_" in py_file.name:
            continue
        content = py_file.read_text(encoding="utf-8")
        if "def merge_director_status" in content:
            implementations.append(str(py_file))

    assert len(implementations) <= 1, f"Multiple merge_director_status implementations: {implementations}"


def test_canonical_modules_exist():
    """Verify that canonical extracted modules exist in their owning Cells."""
    extracted_modules = [
        PM_PLANNING_INTERNAL_DIR / "task_quality_gate.py",
        RUNTIME_STATE_OWNER_INTERNAL_DIR / "pm_contract_store.py",
        PM_DISPATCH_INTERNAL_DIR / "dispatch_pipeline.py",
        CHIEF_ENGINEER_BLUEPRINT_INTERNAL_DIR / "chief_engineer_preflight.py",
        COURT_WORKFLOW_INTERNAL_DIR / "docs_stage_service.py",
        PM_PLANNING_INTERNAL_DIR / "shared_quality.py",
    ]

    missing = []
    for module in extracted_modules:
        if not module.exists():
            missing.append(str(module))

    assert not missing, f"Missing extracted modules: {missing}"


def test_worker_executor_imports_canonical_modules():
    """Verify worker_executor.py imports canonical modules only.

    Phase 3: implementation migrated to director.tasking/internal/.
    Must import bootstrap_template_catalog from tasking/internal (Phase 3 canonical).
    Phase 4 deps (CodeGenerationEngine, FileApplyService) remain in execution/internal.
    """
    file_path = DIRECTOR_TASKING_INTERNAL_DIR / "worker_executor.py"
    if not file_path.exists():
        pytest.skip("File does not exist (Phase 3 migration complete, stub in execution/internal)")

    content = file_path.read_text(encoding="utf-8")

    # Check that it imports from canonical (tasking/internal for Phase 3, execution/internal for Phase 4)
    required_imports = [
        "from polaris.cells.director.tasking.internal.bootstrap_template_catalog import",
        "from polaris.cells.audit.evidence.public.service import",
    ]

    missing = []
    for imp in required_imports:
        if imp not in content:
            missing.append(imp)

    assert not missing, f"worker_executor.py missing imports: {missing}"


def test_orchestration_engine_imports_extracted_modules():
    """Verify orchestration_engine.py imports from extracted modules via public contracts."""
    file_path = PM_CLI_DIR / "orchestration_engine.py"
    if not file_path.exists():
        pytest.skip("File does not exist")

    content = file_path.read_text(encoding="utf-8")

    # Check that it imports from extracted modules via public contract paths.
    # Per ACGA 2.0 rules: cross-Cell imports must use public contracts, not internal.
    required_imports = [
        "from polaris.cells.orchestration.pm_dispatch.public import",
        "from polaris.cells.runtime.state_owner.public import",
        "from polaris.cells.orchestration.pm_planning.public.pipeline import",
    ]

    missing = []
    for imp in required_imports:
        if imp not in content:
            missing.append(imp)

    assert not missing, f"orchestration_engine.py missing imports: {missing}"
