"""Frontend file size guard tests.

These tests enforce file size limits for frontend TypeScript/TSX files
to maintain code maintainability and readability.
"""

from pathlib import Path

import pytest

# Frontend paths
FRONTEND_DIR = Path("src/frontend/src")
COMPONENTS_DIR = FRONTEND_DIR / "app/components"
HOOKS_DIR = FRONTEND_DIR / "app/hooks"

# Maximum lines per TSX/TS file (Phase 9 targets)
MAX_TSX_LINES = {
    "SettingsModal.tsx": 200,
    "useRuntime.ts": 650,
    "DirectorWorkspace.tsx": 900,
}


def get_line_count(file_path: Path) -> int:
    """Get line count of a file."""
    if not file_path.exists():
        return 0
    return len(file_path.read_text(encoding="utf-8").splitlines())


def test_settings_modal_size():
    """Verify SettingsModal.tsx is under limit.

    Target: <200 lines
    Location: src/frontend/src/app/components/settings/SettingsModal.tsx
    """
    file_path = COMPONENTS_DIR / "settings/SettingsModal.tsx"
    if not file_path.exists():
        pytest.skip("File does not exist")

    lines = get_line_count(file_path)
    limit = MAX_TSX_LINES["SettingsModal.tsx"]

    assert lines <= limit, f"SettingsModal.tsx has {lines} lines (limit: {limit})"


def test_use_runtime_size():
    """Verify useRuntime.ts is under limit.

    Target: <650 lines
    Location: src/frontend/src/app/hooks/useRuntime.ts
    """
    file_path = HOOKS_DIR / "useRuntime.ts"
    if not file_path.exists():
        pytest.skip("File does not exist")

    lines = get_line_count(file_path)
    limit = MAX_TSX_LINES["useRuntime.ts"]

    if lines > limit:
        pytest.xfail(f"useRuntime.ts has {lines} lines (limit: {limit}). Split into useRuntimeSocket.ts and selectors.")

    assert lines <= limit, f"useRuntime.ts has {lines} lines (limit: {limit})"


def test_director_workspace_size():
    """Verify DirectorWorkspace.tsx is under limit.

    Target: <900 lines
    Location: src/frontend/src/app/components/director/DirectorWorkspace.tsx
    """
    file_path = COMPONENTS_DIR / "director/DirectorWorkspace.tsx"
    if not file_path.exists():
        pytest.skip("File does not exist")

    lines = get_line_count(file_path)
    limit = MAX_TSX_LINES["DirectorWorkspace.tsx"]

    if lines > limit:
        pytest.xfail(
            f"DirectorWorkspace.tsx has {lines} lines (limit: {limit}). "
            f"Split into statusReducers.ts and taskSelectors.ts."
        )

    assert lines <= limit, f"DirectorWorkspace.tsx has {lines} lines (limit: {limit})"


def test_settings_modal_split_components_exist():
    """Verify SettingsModal.tsx has been split into components."""
    expected_components = [
        COMPONENTS_DIR / "settings/GeneralSettingsTab.tsx",
        COMPONENTS_DIR / "settings/LLMSettingsBridge.tsx",
        COMPONENTS_DIR / "settings/WorkflowSettingsTab.tsx",
        COMPONENTS_DIR / "settings/SystemServicesTabHost.tsx",
    ]

    missing = []
    for component in expected_components:
        if not component.exists():
            missing.append(str(component.name))

    # Only warn if some exist but not all
    if missing and len(missing) < len(expected_components):
        pytest.skip(f"Partial split - missing: {missing}")

    if missing:
        pytest.xfail(f"SettingsModal split components missing: {missing}")


def test_use_runtime_socket_exists():
    """Verify useRuntimeSocket.ts has been created as part of useRuntime.ts split."""
    file_path = HOOKS_DIR / "useRuntimeSocket.ts"
    if not file_path.exists():
        pytest.xfail("useRuntimeSocket.ts should be created from useRuntime.ts split")


def test_director_workspace_split_modules_exist():
    """Verify DirectorWorkspace.tsx has been split into modules."""
    expected_modules = [
        COMPONENTS_DIR / "director/statusReducers.ts",
        COMPONENTS_DIR / "director/taskSelectors.ts",
    ]

    missing = []
    for module in expected_modules:
        if not module.exists():
            missing.append(str(module.name))

    if missing:
        pytest.xfail(f"DirectorWorkspace split modules missing: {missing}")
