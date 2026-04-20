"""Tests for KernelOne storage policy module.

Tests KernelOne's generic storage taxonomy: StorageCategory, Lifecycle,
STORAGE_POLICY_REGISTRY, and path-based policy resolution.

Note: Polaris-specific artifact lifecycle metadata has been migrated to
polaris.cells.audit.verdict.internal.artifact_service. See tests/test_artifact_policy.py.
"""

from __future__ import annotations

import warnings

import pytest
from polaris.kernelone.storage.policy import (
    STORAGE_POLICY_REGISTRY,
    Lifecycle,
    StorageCategory,
    get_category_for_path,
    get_lifecycle_for_path,
    get_policy_for_path,
    is_archive_eligible,
)


class TestStoragePolicy:
    """Test storage policy definitions."""

    def test_global_config_policy(self) -> None:
        """Test global config policy."""
        policy = get_policy_for_path("config/settings.json")
        assert policy.category == StorageCategory.GLOBAL_CONFIG
        assert policy.lifecycle == Lifecycle.PERMANENT
        assert policy.retention_days == -1

    def test_workspace_persistent_policy(self) -> None:
        """Test workspace persistent policies."""
        policy = get_policy_for_path("workspace/docs/readme.md")
        assert policy.category == StorageCategory.WORKSPACE_PERSISTENT
        assert policy.lifecycle == Lifecycle.PERMANENT

    def test_runtime_contracts_policy(self) -> None:
        """Test runtime contracts policy."""
        policy = get_policy_for_path("runtime/contracts/plan.md")
        assert policy.category == StorageCategory.RUNTIME_CURRENT
        assert policy.archive_on_terminal is True

    def test_runtime_tasks_policy(self) -> None:
        """Test runtime tasks policy."""
        policy = get_policy_for_path("runtime/tasks/task_1.json")
        assert policy.category == StorageCategory.RUNTIME_CURRENT
        assert policy.archive_on_terminal is True

    def test_runtime_events_policy(self) -> None:
        """Test runtime events policy."""
        policy = get_policy_for_path("runtime/events/runtime.events.jsonl")
        assert policy.category == StorageCategory.RUNTIME_CURRENT
        assert policy.compress is True
        assert policy.archive_on_terminal is True

    def test_runtime_state_policy(self) -> None:
        """Test runtime state policy (ephemeral)."""
        policy = get_policy_for_path("runtime/state/pm.state.json")
        assert policy.category == StorageCategory.RUNTIME_CURRENT
        assert policy.lifecycle == Lifecycle.EPHEMERAL
        assert policy.retention_days == 7

    def test_runtime_control_policy(self) -> None:
        """Test runtime control policy (very ephemeral)."""
        policy = get_policy_for_path("runtime/control/stop.flag")
        assert policy.lifecycle == Lifecycle.EPHEMERAL
        assert policy.retention_days == 0

    def test_workspace_history_policy(self) -> None:
        """Test workspace history policy."""
        policy = get_policy_for_path("workspace/history/runs/run_123")
        assert policy.category == StorageCategory.WORKSPACE_HISTORY
        assert policy.lifecycle == Lifecycle.HISTORY
        assert policy.compress is True

    def test_runtime_runs_policy(self) -> None:
        """Test runtime runs policy."""
        policy = get_policy_for_path("runtime/runs/run_123")
        assert policy.category == StorageCategory.RUNTIME_RUN
        assert policy.archive_on_terminal is True


class TestArchiveEligibility:
    """Test archive eligibility checks."""

    def test_completed_run_archive_eligible(self) -> None:
        """Test completed run is eligible for archiving."""
        assert is_archive_eligible("runtime/contracts/plan.md", "completed") is True

    def test_failed_run_archive_eligible(self) -> None:
        """Test failed run is eligible for archiving."""
        assert is_archive_eligible("runtime/contracts/plan.md", "failed") is True

    def test_cancelled_run_archive_eligible(self) -> None:
        """Test cancelled run is eligible for archiving."""
        assert is_archive_eligible("runtime/contracts/plan.md", "cancelled") is True

    def test_active_run_not_archive_eligible(self) -> None:
        """Test active run is not eligible for archiving."""
        assert is_archive_eligible("runtime/contracts/plan.md", "running") is False

    def test_ephemeral_not_archive_eligible(self) -> None:
        """Test ephemeral paths are not archived."""
        assert is_archive_eligible("runtime/control/stop.flag", "completed") is False


class TestCategoryAndLifecycle:
    """Test category and lifecycle queries."""

    def test_category_for_path(self) -> None:
        """Test category retrieval."""
        assert get_category_for_path("config/settings.json") == StorageCategory.GLOBAL_CONFIG
        assert get_category_for_path("workspace/docs/x.md") == StorageCategory.WORKSPACE_PERSISTENT
        assert get_category_for_path("runtime/tasks/x.json") == StorageCategory.RUNTIME_CURRENT
        assert get_category_for_path("workspace/history/x") == StorageCategory.WORKSPACE_HISTORY

    def test_lifecycle_for_path(self) -> None:
        """Test lifecycle retrieval."""
        assert get_lifecycle_for_path("config/settings.json") == Lifecycle.PERMANENT
        assert get_lifecycle_for_path("workspace/docs/x.md") == Lifecycle.PERMANENT
        assert get_lifecycle_for_path("runtime/tasks/x.json") == Lifecycle.ACTIVE
        assert get_lifecycle_for_path("runtime/control/x.flag") == Lifecycle.EPHEMERAL
        assert get_lifecycle_for_path("workspace/history/x") == Lifecycle.HISTORY


class TestDeprecatedArtifactPolicy:
    """Test that artifact policy stubs emit deprecation warnings."""

    def test_get_artifact_policy_metadata_emits_deprecation(self) -> None:
        """get_artifact_policy_metadata should emit DeprecationWarning."""
        from polaris.kernelone.storage.policy import get_artifact_policy_metadata

        with warnings.catch_warnings(record=True) as ctx:
            warnings.simplefilter("always")
            result = get_artifact_policy_metadata("contract.plan")

        assert result is None
        assert len(ctx) == 1
        assert issubclass(ctx[0].category, DeprecationWarning)
        assert "deprecated" in str(ctx[0].message).lower()
        assert "artifact_service" in str(ctx[0].message)

    def test_should_compress_artifact_emits_deprecation(self) -> None:
        """should_compress_artifact should emit DeprecationWarning."""
        from polaris.kernelone.storage.policy import should_compress_artifact

        with warnings.catch_warnings(record=True) as ctx:
            warnings.simplefilter("always")
            result = should_compress_artifact("audit.events.runtime")

        assert result is False
        assert len(ctx) == 1
        assert issubclass(ctx[0].category, DeprecationWarning)

    def test_should_archive_artifact_emits_deprecation(self) -> None:
        """should_archive_artifact should emit DeprecationWarning."""
        from polaris.kernelone.storage.policy import should_archive_artifact

        with warnings.catch_warnings(record=True) as ctx:
            warnings.simplefilter("always")
            result = should_archive_artifact("contract.pm_tasks")

        assert result is False
        assert len(ctx) == 1
        assert issubclass(ctx[0].category, DeprecationWarning)

    def test_artifact_policy_metadata_stub_is_empty(self) -> None:
        """ARTIFACT_POLICY_METADATA should be an empty stub."""
        from polaris.kernelone.storage.policy import ARTIFACT_POLICY_METADATA

        # The stub is empty - Polaris metadata has moved to artifact_service
        assert ARTIFACT_POLICY_METADATA == {}


class TestStoragePolicyRegistry:
    """Test STORAGE_POLICY_REGISTRY completeness and invariants."""

    def test_registry_has_fallback_entry(self) -> None:
        """Registry must have a fallback entry for unknown prefixes."""
        fallback = STORAGE_POLICY_REGISTRY[-1]
        assert fallback.logical_prefix == ""

    def test_all_storage_policies_are_frozen(self) -> None:
        """All StoragePolicy entries must be hashable (frozen=True)."""
        for policy in STORAGE_POLICY_REGISTRY:
            hash(policy)  # Must not raise

    def test_no_duplicates_by_prefix(self) -> None:
        """No two non-fallback entries should share the same prefix."""
        prefixes = [p.logical_prefix for p in STORAGE_POLICY_REGISTRY if p.logical_prefix]
        assert len(prefixes) == len(set(prefixes))

    def test_storage_policy_should_archive(self) -> None:
        """StoragePolicy.should_archive() reflects archive_on_terminal."""
        for policy in STORAGE_POLICY_REGISTRY:
            assert policy.should_archive() == policy.archive_on_terminal

    def test_storage_policy_should_compress(self) -> None:
        """StoragePolicy.should_compress() reflects compress flag."""
        for policy in STORAGE_POLICY_REGISTRY:
            assert policy.should_compress() == policy.compress

    def test_storage_policy_get_retention_days(self) -> None:
        """StoragePolicy.get_retention_days() returns retention_days."""
        for policy in STORAGE_POLICY_REGISTRY:
            assert policy.get_retention_days() == policy.retention_days


class TestStoragePolicyService:
    """Test StoragePolicyService."""

    def test_get_policy_returns_matching_entry(self, tmp_path: pytest.TempPathFactory) -> None:
        """get_policy returns the correct StoragePolicy for a path."""
        from polaris.kernelone.storage.policy import StoragePolicyService

        svc = StoragePolicyService(str(tmp_path))
        policy = svc.get_policy("runtime/contracts/plan.md")
        assert policy.category == StorageCategory.RUNTIME_CURRENT
        assert policy.lifecycle == Lifecycle.ACTIVE

    def test_should_archive_uses_is_archive_eligible(self, tmp_path: pytest.TempPathFactory) -> None:
        """should_archive delegates to is_archive_eligible."""
        from polaris.kernelone.storage.policy import StoragePolicyService

        svc = StoragePolicyService(str(tmp_path))
        # contracts path is archive_on_terminal=True
        assert svc.should_archive("runtime/contracts/plan.md", "completed") is True
        # control path is archive_on_terminal=False
        assert svc.should_archive("runtime/control/stop.flag", "completed") is False
