from __future__ import annotations

from pathlib import Path

from polaris.kernelone._runtime_config import set_workspace_metadata_dir_name
from polaris.kernelone.storage import (
    clear_business_roots_resolver,
    clear_storage_roots_cache,
    resolve_storage_roots,
)
from .backend_bootstrap import BACKEND_SERVER_SCRIPT
from .observer.constants import BACKEND_DIR, PROJECT_ROOT
from .paths import BACKEND_ROOT, REPO_ROOT
from .stress_path_policy import (
    default_stress_runtime_root,
    default_stress_workspace_base,
)


def test_agent_stress_paths_point_to_current_backend_root() -> None:
    expected_backend_root = Path(__file__).resolve().parents[2]

    assert expected_backend_root == BACKEND_ROOT
    assert expected_backend_root == BACKEND_DIR
    assert expected_backend_root == PROJECT_ROOT
    assert expected_backend_root.parents[1] == REPO_ROOT

    assert expected_backend_root / "server.py" == BACKEND_SERVER_SCRIPT
    assert BACKEND_SERVER_SCRIPT.is_file()


def test_agent_stress_registers_polaris_storage_layout(tmp_path: Path) -> None:
    original_metadata_dir = ".kernelone"
    clear_business_roots_resolver()
    clear_storage_roots_cache()
    set_workspace_metadata_dir_name(original_metadata_dir)
    try:
        from .paths import ensure_backend_root_on_syspath

        ensure_backend_root_on_syspath()
        roots = resolve_storage_roots(
            str(tmp_path / "workspace"),
            "X:/tests-agent-stress-runtime",
        )

        assert Path(roots.project_root).name == ".polaris"
        assert ".polaris" in Path(roots.runtime_root).parts
        assert str(Path(roots.runtime_base)).startswith(str(Path("X:/")))
    finally:
        clear_storage_roots_cache()
        clear_business_roots_resolver()
        set_workspace_metadata_dir_name(original_metadata_dir)


def test_default_stress_workspace_base_uses_c_temp_on_windows() -> None:
    workspace = default_stress_workspace_base(
        "tests-agent-stress-backend",
        env={},
        platform_name="nt",
    )
    assert workspace == Path("C:/Temp/tests-agent-stress-backend").resolve()


def test_default_stress_runtime_root_uses_x_drive_on_windows() -> None:
    runtime_root = default_stress_runtime_root(
        "tests-agent-stress-runtime",
        env={},
        platform_name="nt",
    )
    assert runtime_root == Path("X:/tests-agent-stress-runtime").resolve()
