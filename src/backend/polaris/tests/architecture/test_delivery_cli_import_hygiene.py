from __future__ import annotations

from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[3]

CLI_ENTRYPOINTS = [
    "polaris/delivery/cli/pm/cli.py",
    "polaris/delivery/cli/pm/cli_thin.py",
    "polaris/delivery/cli/director/cli_thin.py",
    "polaris/delivery/cli/audit/audit_cli.py",
    "polaris/delivery/cli/loop-pm.py",
    "polaris/delivery/cli/loop-director.py",
    "polaris/delivery/cli/director_v2.py",
]

CLI_SUPPORT_MODULES = [
    "polaris/delivery/cli/director/director_service.py",
    "polaris/delivery/cli/director/director_role.py",
    "polaris/delivery/cli/pm/director_interface_integration.py",
    "polaris/delivery/cli/pm/pm_role.py",
    "polaris/delivery/cli/pm/pm_service.py",
    "polaris/delivery/cli/audit/audit_quick.py",
    "polaris/delivery/cli/audit/audit_agent.py",
    "polaris/delivery/cli/audit/audit_agent_example.py",
]

DELIVERY_ADAPTERS = [
    "polaris/delivery/http/adapters/scripts_pm.py",
]

CANONICAL_RUNTIME_ROOTS = [
    "polaris/delivery",
    "polaris/cells",
]

RETIRED_SCRIPT_SHIMS = [
    "scripts/benchmark_iterative_loop.py",
    "scripts/check_cell_imports.py",
    "scripts/check_legacy_imports.py",
    "scripts/contextos_gate_checker.py",
    "scripts/dev-tools.py",
]

RETIRED_APPLICATION_ORCHESTRATION_PATHS = [
    "polaris/application/orchestration/__init__.py",
    "polaris/application/orchestration/architect_orchestrator.py",
    "polaris/application/orchestration/architect_schemas.py",
    "polaris/application/orchestration/director_orchestrator.py",
    "polaris/application/orchestration/director_schemas.py",
    "polaris/application/orchestration/pm_orchestrator.py",
    "polaris/application/orchestration/pm_schemas.py",
    "polaris/application/orchestration/protocols.py",
    "polaris/application/orchestration/qa_orchestrator.py",
    "polaris/application/orchestration/qa_schemas.py",
    "polaris/application/orchestration/tests/__init__.py",
    "polaris/application/orchestration/tests/test_director_orchestrator_parallel.py",
    "polaris/application/orchestration/tests/test_director_schemas.py",
    "polaris/application/orchestration/tests/test_orchestration_schemas.py",
    "polaris/application/orchestration/tests/test_orchestrator_regression.py",
    "polaris/tests/application/test_architect_orchestrator.py",
    "polaris/tests/application/test_qa_orchestrator.py",
    "polaris/tests/orchestration/test_boundary_conditions.py",
    "polaris/tests/orchestration/test_qa_orchestrator.py",
    "polaris/tests/test_director_orchestrator_adapter_routing.py",
    "polaris/tests/test_director_orchestrator_resident_decision.py",
]


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("relative_path", CLI_ENTRYPOINTS)
def test_cli_entrypoints_use_conditional_bootstrap(relative_path: str) -> None:
    full_path = BACKEND_ROOT / relative_path
    assert full_path.is_file(), f"missing cli entrypoint: {relative_path}"
    source = _read_text(full_path)

    assert "_bootstrap_backend_import_path" in source, f"{relative_path} must define conditional path bootstrap helper"
    assert "if __package__:" in source, f"{relative_path} bootstrap must be conditional for package imports"
    assert 'scripts") not in sys.path' not in source, f"{relative_path} must not inject legacy scripts path"
    assert 'core" / "polaris_loop' not in source, f"{relative_path} must not inject legacy core loop path"


@pytest.mark.parametrize("relative_path", CLI_SUPPORT_MODULES)
def test_cli_support_modules_avoid_import_time_path_hacks(relative_path: str) -> None:
    full_path = BACKEND_ROOT / relative_path
    assert full_path.is_file(), f"missing support module: {relative_path}"
    source = _read_text(full_path)

    assert "_bootstrap_backend_import_path" in source, f"{relative_path} must define conditional path bootstrap helper"
    assert "if __package__:" in source, f"{relative_path} bootstrap must be conditional for package imports"
    assert "Path(__file__).parent.parent" not in source, (
        f"{relative_path} must not use legacy relative sys.path bootstrap"
    )
    assert 'scripts") not in sys.path' not in source, f"{relative_path} must not inject legacy scripts path"
    assert 'core" / "polaris_loop' not in source, f"{relative_path} must not inject legacy core loop path"


def test_pm_config_stays_import_side_effect_lightweight() -> None:
    config_path = BACKEND_ROOT / "polaris/delivery/cli/pm/config.py"
    assert config_path.is_file(), "missing pm config module"
    source = _read_text(config_path)

    assert "enforce_utf8()" not in source, "pm.config must not mutate process stdio encoding at import time"
    assert "_PM_PROVIDER_ID, _PM_MODEL = load_pm_model_config()" not in source, (
        "pm.config must not trigger runtime config loading at import time"
    )
    assert "sys.path.insert(0, PROJECT_ROOT)" not in source, (
        "pm.config must not mutate sys.path for project root imports"
    )
    assert "from polaris.bootstrap.config import get_settings" not in source, (
        "pm.config must not import runtime settings for module-level constants"
    )
    assert "get_settings()" not in source, "pm.config must not resolve settings at import time"
    assert "os.listdir(base_dir)" not in source, "pm.config must avoid broad directory scans during import"


def test_delivery_cli_deprecation_warnings_do_not_use_compat_module_name() -> None:
    retired_path = BACKEND_ROOT / "polaris/delivery/cli/cli_compat.py"
    canonical_path = BACKEND_ROOT / "polaris/delivery/cli/entrypoint_warnings.py"

    assert not retired_path.exists(), "Retired cli_compat.py module was recreated."
    assert canonical_path.is_file(), "CLI entrypoint warnings must live in entrypoint_warnings.py."

    router_source = _read_text(BACKEND_ROOT / "polaris/delivery/cli/cli_router.py")
    assert "polaris.delivery.cli.cli_compat" not in router_source
    assert "polaris.delivery.cli.entrypoint_warnings" in router_source


def test_director_cli_entrypoint_does_not_use_compat_module_name() -> None:
    retired_path = BACKEND_ROOT / "polaris/delivery/cli/director/cli_compat.py"
    canonical_path = BACKEND_ROOT / "polaris/delivery/cli/director/cli_entrypoint.py"

    assert not retired_path.exists(), "Retired director/cli_compat.py module was recreated."
    assert canonical_path.is_file(), "Director CLI entrypoint must live in cli_entrypoint.py."

    source = _read_text(canonical_path)
    assert "polaris.delivery.cli.director.cli_compat" not in source
    assert "polaris.delivery.cli.director.cli_entrypoint" in source


def test_retired_import_checker_does_not_use_legacy_module_name() -> None:
    retired_path = BACKEND_ROOT / "polaris/delivery/cli/tools/check_legacy_imports.py"
    canonical_path = BACKEND_ROOT / "polaris/delivery/cli/tools/check_retired_imports.py"

    assert not retired_path.exists(), "Retired check_legacy_imports.py module was recreated."
    assert canonical_path.is_file(), "Retired-root import checker must live in check_retired_imports.py."

    source = _read_text(canonical_path)
    assert "find_legacy_imports" not in source
    assert "check_legacy_imports" not in source
    assert "find_retired_imports" in source


@pytest.mark.parametrize("relative_path", RETIRED_SCRIPT_SHIMS)
def test_retired_backend_script_shims_are_removed(relative_path: str) -> None:
    full_path = BACKEND_ROOT / relative_path
    assert not full_path.exists(), f"Retired backend script shim was recreated: {relative_path}"


@pytest.mark.parametrize("relative_path", RETIRED_APPLICATION_ORCHESTRATION_PATHS)
def test_application_orchestration_shims_are_removed(relative_path: str) -> None:
    full_path = BACKEND_ROOT / relative_path
    assert not full_path.exists(), f"Retired application orchestration path was recreated: {relative_path}"


def test_application_orchestration_has_no_source_files() -> None:
    root = BACKEND_ROOT / "polaris/application/orchestration"
    if not root.exists():
        return

    offenders = [
        path.relative_to(BACKEND_ROOT).as_posix()
        for path in root.rglob("*.py")
        if "__pycache__" not in path.parts
    ]
    assert not offenders, f"Retired application orchestration source files were recreated: {offenders}"


@pytest.mark.parametrize("relative_path", DELIVERY_ADAPTERS)
def test_delivery_adapters_do_not_mutate_sys_path(relative_path: str) -> None:
    full_path = BACKEND_ROOT / relative_path
    assert full_path.is_file(), f"missing delivery adapter module: {relative_path}"
    source = _read_text(full_path)

    assert "sys.path.insert(" not in source, f"{relative_path} must not mutate sys.path at runtime"


@pytest.mark.parametrize("relative_root", CANONICAL_RUNTIME_ROOTS)
def test_runtime_production_code_does_not_import_application_orchestration(relative_root: str) -> None:
    root = BACKEND_ROOT / relative_root
    assert root.is_dir(), f"missing runtime root: {relative_root}"

    offenders: list[str] = []
    for path in root.rglob("*.py"):
        relative = path.relative_to(BACKEND_ROOT).as_posix()
        if "/tests/" in relative or relative.endswith("/tests.py"):
            continue
        source = _read_text(path)
        if "polaris.application.orchestration" in source:
            offenders.append(relative)

    assert not offenders, (
        "Runtime production code must use public Cell contracts instead of "
        f"polaris.application.orchestration: {offenders}"
    )
