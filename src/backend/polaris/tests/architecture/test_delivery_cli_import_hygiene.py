from __future__ import annotations

from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]

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


@pytest.mark.parametrize("relative_path", DELIVERY_ADAPTERS)
def test_delivery_adapters_do_not_mutate_sys_path(relative_path: str) -> None:
    full_path = BACKEND_ROOT / relative_path
    assert full_path.is_file(), f"missing delivery adapter module: {relative_path}"
    source = _read_text(full_path)

    assert "sys.path.insert(" not in source, f"{relative_path} must not mutate sys.path at runtime"
