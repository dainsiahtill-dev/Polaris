"""Unit tests for polaris.kernelone.storage policy, paths, and persona_store.

Covers:
- StoragePolicy dataclass behavior (should_archive, should_compress, get_retention_days)
- get_policy_for_path: exact match, prefix match, fallback
- is_archive_eligible: terminal vs non-terminal statuses
- get_category_for_path / get_lifecycle_for_path
- StoragePolicyService initialization and methods
- resolve_signal_path / resolve_artifact_path / resolve_session_path / resolve_taskboard_path / resolve_runtime_path
- load_workspace_persona: first load, subsequent load, missing persona_ids
- clear_workspace_persona
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from polaris.kernelone.storage.contracts import Lifecycle as ContractsLifecycle, StorageCategory, StoragePolicy
from polaris.kernelone.storage.paths import (
    resolve_artifact_path,
    resolve_runtime_path,
    resolve_session_path,
    resolve_signal_path,
    resolve_taskboard_path,
)
from polaris.kernelone.storage.persona_store import (
    clear_workspace_persona,
    get_workspace_persona_store_path,
    load_workspace_persona,
)
from polaris.kernelone.storage.policy import (
    Lifecycle,
    StoragePolicyService,
    get_category_for_path,
    get_lifecycle_for_path,
    get_policy_for_path,
    is_archive_eligible,
)

# -----------------------------------------------------------------------------
# StoragePolicy dataclass
# -----------------------------------------------------------------------------


def test_storage_policy_methods() -> None:
    """StoragePolicy helper methods return expected values."""
    policy = StoragePolicy(
        logical_prefix="runtime/contracts",
        category=StorageCategory.RUNTIME_CURRENT,
        lifecycle=ContractsLifecycle.ACTIVE,
        retention_days=-1,
        compress=True,
        archive_on_terminal=True,
    )
    assert policy.should_archive() is True
    assert policy.should_compress() is True
    assert policy.get_retention_days() == -1


def test_storage_policy_defaults() -> None:
    """StoragePolicy defaults are sensible."""
    policy = StoragePolicy(
        logical_prefix="",
        category=StorageCategory.RUNTIME_CURRENT,
        lifecycle=ContractsLifecycle.EPHEMERAL,
    )
    assert policy.should_archive() is False
    assert policy.should_compress() is False
    assert policy.get_retention_days() == -1


# -----------------------------------------------------------------------------
# get_policy_for_path
# -----------------------------------------------------------------------------


def test_get_policy_exact_match() -> None:
    """Exact prefix match returns the correct policy."""
    policy = get_policy_for_path("config")
    assert policy.logical_prefix == "config"
    assert policy.category == StorageCategory.GLOBAL_CONFIG
    assert policy.lifecycle == Lifecycle.PERMANENT


def test_get_policy_prefix_match() -> None:
    """Prefix matching selects the most specific policy."""
    policy = get_policy_for_path("runtime/contracts/plan.md")
    assert policy.logical_prefix == "runtime/contracts"
    assert policy.category == StorageCategory.RUNTIME_CURRENT


def test_get_policy_workspace_docs() -> None:
    """workspace/docs maps to WORKSPACE_PERSISTENT."""
    policy = get_policy_for_path("workspace/docs/readme.md")
    assert policy.logical_prefix == "workspace/docs"
    assert policy.category == StorageCategory.WORKSPACE_PERSISTENT


def test_get_policy_workspace_factory() -> None:
    """workspace/factory maps to FACTORY_CURRENT with archive."""
    policy = get_policy_for_path("workspace/factory/run_42")
    assert policy.logical_prefix == "workspace/factory"
    assert policy.category == StorageCategory.FACTORY_CURRENT
    assert policy.archive_on_terminal is True


def test_get_policy_history() -> None:
    """workspace/history maps to HISTORY with compression."""
    policy = get_policy_for_path("workspace/history/2024-01-01")
    assert policy.logical_prefix == "workspace/history"
    assert policy.lifecycle == Lifecycle.HISTORY
    assert policy.compress is True


def test_get_policy_runtime_logs() -> None:
    """runtime/logs is EPHEMERAL with 7-day retention."""
    policy = get_policy_for_path("runtime/logs/app.log")
    assert policy.logical_prefix == "runtime/logs"
    assert policy.lifecycle == Lifecycle.EPHEMERAL
    assert policy.retention_days == 7


def test_get_policy_fallback() -> None:
    """Unknown paths fall back to the empty-prefix policy."""
    policy = get_policy_for_path("totally/unknown/path")
    assert policy.logical_prefix == ""
    assert policy.lifecycle == Lifecycle.EPHEMERAL
    assert policy.retention_days == 1


def test_get_policy_backslash_normalized() -> None:
    """Backslashes are normalized to forward slashes."""
    policy = get_policy_for_path("runtime\\contracts\\plan.md")
    assert policy.logical_prefix == "runtime/contracts"


# -----------------------------------------------------------------------------
# is_archive_eligible
# -----------------------------------------------------------------------------


def test_is_archive_eligible_terminal_statuses() -> None:
    """Terminal statuses with archive_on_terminal=True are eligible."""
    assert is_archive_eligible("runtime/contracts/plan.md", "completed") is True
    assert is_archive_eligible("runtime/contracts/plan.md", "failed") is True
    assert is_archive_eligible("runtime/contracts/plan.md", "cancelled") is True
    assert is_archive_eligible("runtime/contracts/plan.md", "blocked") is True
    assert is_archive_eligible("runtime/contracts/plan.md", "timeout") is True


def test_is_archive_eligible_non_terminal_status() -> None:
    """Non-terminal statuses are not eligible."""
    assert is_archive_eligible("runtime/contracts/plan.md", "running") is False
    assert is_archive_eligible("runtime/contracts/plan.md", "pending") is False


def test_is_archive_eligible_no_archive_policy() -> None:
    """Paths without archive_on_terminal are never eligible."""
    assert is_archive_eligible("config/settings.yaml", "completed") is False


def test_is_archive_eligible_case_insensitive_status() -> None:
    """Status comparison is case-insensitive."""
    assert is_archive_eligible("runtime/contracts/plan.md", "COMPLETED") is True
    assert is_archive_eligible("runtime/contracts/plan.md", "Failed") is True


# -----------------------------------------------------------------------------
# get_category_for_path / get_lifecycle_for_path
# -----------------------------------------------------------------------------


def test_get_category_for_path() -> None:
    """get_category_for_path returns the correct StorageCategory."""
    assert get_category_for_path("config") == StorageCategory.GLOBAL_CONFIG
    assert get_category_for_path("runtime/state") == StorageCategory.RUNTIME_CURRENT
    assert get_category_for_path("workspace/history") == StorageCategory.WORKSPACE_HISTORY


def test_get_lifecycle_for_path() -> None:
    """get_lifecycle_for_path returns the correct Lifecycle."""
    assert get_lifecycle_for_path("config") == Lifecycle.PERMANENT
    assert get_lifecycle_for_path("runtime/control") == Lifecycle.EPHEMERAL
    assert get_lifecycle_for_path("workspace/history") == Lifecycle.HISTORY


# -----------------------------------------------------------------------------
# StoragePolicyService
# -----------------------------------------------------------------------------


def test_service_init_resolves_absolute_path(tmp_path: Path) -> None:
    """StoragePolicyService stores an absolute workspace path."""
    service = StoragePolicyService(str(tmp_path))
    assert service.workspace == os.path.abspath(str(tmp_path))


def test_service_get_policy_delegates() -> None:
    """get_policy delegates to get_policy_for_path."""
    service = StoragePolicyService("/tmp/ws")
    policy = service.get_policy("runtime/contracts")
    assert policy.logical_prefix == "runtime/contracts"


def test_service_should_archive_delegates() -> None:
    """should_archive delegates to is_archive_eligible."""
    service = StoragePolicyService("/tmp/ws")
    assert service.should_archive("runtime/contracts", "completed") is True
    assert service.should_archive("config", "completed") is False


def test_service_resolve_target_path_runtime(tmp_path: Path) -> None:
    """resolve_target_path resolves runtime/ prefix correctly."""
    service = StoragePolicyService(str(tmp_path))
    runtime_root = str(tmp_path / "runtime")
    result = service.resolve_target_path("runtime/contracts", str(tmp_path), runtime_root)
    assert result == os.path.join(runtime_root, "contracts")


def test_service_resolve_target_path_workspace(tmp_path: Path) -> None:
    """resolve_target_path resolves workspace/ prefix correctly."""
    service = StoragePolicyService(str(tmp_path))
    runtime_root = str(tmp_path / "runtime")
    result = service.resolve_target_path("workspace/docs", str(tmp_path), runtime_root)
    # workspace_persistent_root is typically <workspace>/.polaris/workspace
    assert "docs" in result


def test_service_resolve_target_path_config(tmp_path: Path) -> None:
    """resolve_target_path resolves config prefix correctly."""
    service = StoragePolicyService(str(tmp_path))
    runtime_root = str(tmp_path / "runtime")
    result = service.resolve_target_path("config", str(tmp_path), runtime_root)
    assert result != runtime_root  # should be config root


def test_service_resolve_target_path_unknown_defaults_to_runtime(tmp_path: Path) -> None:
    """Unknown prefixes default to runtime_root."""
    service = StoragePolicyService(str(tmp_path))
    runtime_root = str(tmp_path / "runtime")
    result = service.resolve_target_path("unknown/path", str(tmp_path), runtime_root)
    assert result == runtime_root


# -----------------------------------------------------------------------------
# paths.py
# -----------------------------------------------------------------------------


def test_resolve_signal_path(tmp_path: Path) -> None:
    """resolve_signal_path builds the correct path."""
    result = resolve_signal_path(str(tmp_path), "director", "planning")
    assert result.name == "planning.director.signals.json"
    assert "runtime/signals" in str(result).replace("\\", "/")


def test_resolve_artifact_path(tmp_path: Path) -> None:
    """resolve_artifact_path builds the correct path."""
    result = resolve_artifact_path(str(tmp_path), "artifact_123")
    assert result.name == "artifact_123"
    assert "runtime/artifacts" in str(result).replace("\\", "/")


def test_resolve_session_path(tmp_path: Path) -> None:
    """resolve_session_path builds the correct directory path."""
    result = resolve_session_path(str(tmp_path), "sess_42")
    assert result.name == "sess_42"
    assert "runtime/sessions" in str(result).replace("\\", "/")


def test_resolve_taskboard_path(tmp_path: Path) -> None:
    """resolve_taskboard_path builds the correct file path."""
    result = resolve_taskboard_path(str(tmp_path))
    assert result.name == "taskboard.json"
    assert "runtime/tasks" in str(result).replace("\\", "/")


def test_resolve_runtime_path(tmp_path: Path) -> None:
    """resolve_runtime_path builds the correct runtime subpath."""
    result = resolve_runtime_path(str(tmp_path), "logs/app.log")
    assert result.name == "app.log"
    assert "runtime/logs" in str(result).replace("\\", "/")


# -----------------------------------------------------------------------------
# persona_store.py
# -----------------------------------------------------------------------------


def test_get_workspace_persona_store_path(tmp_path: Path) -> None:
    """Persona store path ends with role_persona.json."""
    path = get_workspace_persona_store_path(str(tmp_path))
    assert path.name == "role_persona.json"


def test_load_workspace_persona_first_load(tmp_path: Path) -> None:
    """First load selects randomly and persists."""
    persona_ids = ["alpha", "beta", "gamma"]
    selected = load_workspace_persona(str(tmp_path), persona_ids)
    assert selected in persona_ids

    # File should be written
    store_path = get_workspace_persona_store_path(str(tmp_path))
    assert store_path.exists()
    data = json.loads(store_path.read_text(encoding="utf-8"))
    assert data["persona_id"] == selected


def test_load_workspace_persona_subsequent_load(tmp_path: Path) -> None:
    """Subsequent loads return the persisted persona."""
    persona_ids = ["alpha", "beta", "gamma"]
    first = load_workspace_persona(str(tmp_path), persona_ids)

    # Call again with same list
    second = load_workspace_persona(str(tmp_path), persona_ids)
    assert second == first


def test_load_workspace_persona_missing_from_list_creates_new(tmp_path: Path) -> None:
    """If persisted persona is not in the provided list, a new one is chosen."""
    store_path = get_workspace_persona_store_path(str(tmp_path))
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(json.dumps({"persona_id": "old_persona"}), encoding="utf-8")

    persona_ids = ["alpha", "beta"]
    selected = load_workspace_persona(str(tmp_path), persona_ids)
    assert selected in persona_ids
    assert selected != "old_persona"


def test_load_workspace_persona_empty_list_returns_default(tmp_path: Path) -> None:
    """Empty persona_ids returns 'default'."""
    selected = load_workspace_persona(str(tmp_path), [])
    assert selected == "default"


def test_load_workspace_persona_corrupt_file_chooses_new(tmp_path: Path) -> None:
    """Corrupt JSON file triggers new selection."""
    store_path = get_workspace_persona_store_path(str(tmp_path))
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text("not json", encoding="utf-8")

    persona_ids = ["alpha", "beta"]
    selected = load_workspace_persona(str(tmp_path), persona_ids)
    assert selected in persona_ids


@pytest.mark.skipif(sys.platform == "win32", reason="Windows does not enforce read-only via chmod for current user")
def test_load_workspace_persona_readonly_fs_falls_back(tmp_path: Path) -> None:
    """Write failure on first load returns selection without persisting."""
    # Create a read-only directory
    readonly_dir = tmp_path / "readonly"
    readonly_dir.mkdir()
    os.chmod(str(readonly_dir), 0o555)

    try:
        # The persona file path will be inside readonly_dir/.polaris/...
        # Since parent creation fails, the write is skipped
        selected = load_workspace_persona(str(readonly_dir), ["alpha"])
        assert selected == "alpha"
        # File should not exist because write failed
        store_path = get_workspace_persona_store_path(str(readonly_dir))
        assert not store_path.exists()
    finally:
        os.chmod(str(readonly_dir), 0o755)


def test_clear_workspace_persona_removes_file(tmp_path: Path) -> None:
    """clear_workspace_persona deletes the store file."""
    load_workspace_persona(str(tmp_path), ["alpha", "beta"])
    store_path = get_workspace_persona_store_path(str(tmp_path))
    assert store_path.exists()

    clear_workspace_persona(str(tmp_path))
    assert not store_path.exists()


def test_clear_workspace_persona_missing_file_is_safe(tmp_path: Path) -> None:
    """clear_workspace_persona is safe when file does not exist."""
    clear_workspace_persona(str(tmp_path))
    store_path = get_workspace_persona_store_path(str(tmp_path))
    assert not store_path.exists()
