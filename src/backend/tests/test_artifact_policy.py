"""Tests for Polaris artifact lifecycle policy metadata.

These tests verify that Polaris-specific business artifact lifecycle metadata
(resides in polaris.cells.audit.verdict.internal.artifact_service and not in the
generic KernelOne storage layer.

Coverage:
- Canonical key lookup
- Legacy key alias resolution
- Compression policy
- Archive-on-terminal policy
- Unknown key default behavior
- Alignment with ARTIFACT_REGISTRY paths
"""

from __future__ import annotations


class TestPolarisArtifactPolicyMetadata:
    """Test artifact lifecycle policy metadata from artifact_service."""

    def test_plan_artifact_policy(self) -> None:
        """contract.plan: active lifecycle, no compression, no archive."""
        from polaris.cells.audit.verdict.internal.artifact_service import (
            get_artifact_policy_metadata,
        )

        metadata = get_artifact_policy_metadata("contract.plan")
        assert metadata is not None
        assert metadata["lifecycle"] == "active"
        assert metadata["compress"] is False
        assert metadata["archive_on_terminal"] is False
        assert metadata["category"] == "runtime_current"

    def test_gap_report_artifact_policy(self) -> None:
        """contract.gap_report: active lifecycle, no compression, no archive."""
        from polaris.cells.audit.verdict.internal.artifact_service import (
            get_artifact_policy_metadata,
        )

        metadata = get_artifact_policy_metadata("contract.gap_report")
        assert metadata is not None
        assert metadata["lifecycle"] == "active"
        assert metadata["compress"] is False
        assert metadata["archive_on_terminal"] is False

    def test_pm_tasks_artifact_policy(self) -> None:
        """contract.pm_tasks: active lifecycle, archive on terminal."""
        from polaris.cells.audit.verdict.internal.artifact_service import (
            get_artifact_policy_metadata,
        )

        metadata = get_artifact_policy_metadata("contract.pm_tasks")
        assert metadata is not None
        assert metadata["lifecycle"] == "active"
        assert metadata["compress"] is False
        assert metadata["archive_on_terminal"] is True

    def test_pm_state_artifact_policy(self) -> None:
        """runtime.state.pm: ephemeral lifecycle, no archive."""
        from polaris.cells.audit.verdict.internal.artifact_service import (
            get_artifact_policy_metadata,
        )

        metadata = get_artifact_policy_metadata("runtime.state.pm")
        assert metadata is not None
        assert metadata["lifecycle"] == "ephemeral"
        assert metadata["archive_on_terminal"] is False

    def test_director_result_artifact_policy(self) -> None:
        """runtime.result.director: active lifecycle, archive on terminal."""
        from polaris.cells.audit.verdict.internal.artifact_service import (
            get_artifact_policy_metadata,
        )

        metadata = get_artifact_policy_metadata("runtime.result.director")
        assert metadata is not None
        assert metadata["lifecycle"] == "active"
        assert metadata["archive_on_terminal"] is True

    def test_director_control_flags_no_archive(self) -> None:
        """runtime.control.*: ephemeral lifecycle, never archived."""
        from polaris.cells.audit.verdict.internal.artifact_service import (
            get_artifact_policy_metadata,
        )

        for key in ("runtime.control.pm_stop", "runtime.control.director_stop", "runtime.control.pause"):
            metadata = get_artifact_policy_metadata(key)
            assert metadata is not None, f"Missing metadata for {key}"
            assert metadata["lifecycle"] == "ephemeral", f"{key} should be ephemeral"
            assert metadata["archive_on_terminal"] is False, f"{key} should not archive"

    def test_event_artifacts_compression(self) -> None:
        """All audit.events.* artifacts should be compressed."""
        from polaris.cells.audit.verdict.internal.artifact_service import (
            get_artifact_policy_metadata,
        )

        event_keys = (
            "audit.events.runtime",
            "audit.events.pm",
            "audit.events.pm_llm",
            "audit.events.pm_task_history",
            "audit.events.director_llm",
            "audit.transcript",
        )
        for key in event_keys:
            metadata = get_artifact_policy_metadata(key)
            assert metadata is not None, f"Missing metadata for {key}"
            assert metadata["compress"] is True, f"{key} should be compress=True"
            assert metadata["archive_on_terminal"] is True, f"{key} should archive"

    def test_agents_artifacts_no_archive(self) -> None:
        """contract.agents_draft and contract.agents_feedback: active, no archive."""
        from polaris.cells.audit.verdict.internal.artifact_service import (
            get_artifact_policy_metadata,
        )

        for key in ("contract.agents_draft", "contract.agents_feedback"):
            metadata = get_artifact_policy_metadata(key)
            assert metadata is not None
            assert metadata["lifecycle"] == "active"
            assert metadata["compress"] is False
            assert metadata["archive_on_terminal"] is False


class TestArtifactPolicyCompression:
    """Test should_compress_artifact helper."""

    def test_events_are_compressed(self) -> None:
        """Event artifacts should be compressed."""
        from polaris.cells.audit.verdict.internal.artifact_service import (
            should_compress_artifact,
        )

        assert should_compress_artifact("audit.events.runtime") is True
        assert should_compress_artifact("audit.events.pm") is True
        assert should_compress_artifact("audit.events.pm_llm") is True
        assert should_compress_artifact("audit.transcript") is True

    def test_non_event_artifacts_not_compressed(self) -> None:
        """Non-event artifacts should not be compressed."""
        from polaris.cells.audit.verdict.internal.artifact_service import (
            should_compress_artifact,
        )

        assert should_compress_artifact("contract.plan") is False
        assert should_compress_artifact("contract.pm_tasks") is False
        assert should_compress_artifact("runtime.result.director") is False
        assert should_compress_artifact("runtime.control.pm_stop") is False

    def test_unknown_artifact_not_compressed(self) -> None:
        """Unknown artifacts return False (no crash)."""
        from polaris.cells.audit.verdict.internal.artifact_service import (
            should_compress_artifact,
        )

        assert should_compress_artifact("unknown.artifact") is False


class TestArtifactPolicyArchive:
    """Test should_archive_artifact helper."""

    def test_archive_on_terminal_artifacts(self) -> None:
        """Artifacts that should be archived on terminal state."""
        from polaris.cells.audit.verdict.internal.artifact_service import (
            should_archive_artifact,
        )

        assert should_archive_artifact("contract.pm_tasks") is True
        assert should_archive_artifact("runtime.report.pm") is True
        assert should_archive_artifact("runtime.result.director") is True
        assert should_archive_artifact("runtime.result.qa") is True
        assert should_archive_artifact("audit.events.runtime") is True
        assert should_archive_artifact("audit.events.pm") is True

    def test_no_archive_artifacts(self) -> None:
        """Artifacts that should never be archived."""
        from polaris.cells.audit.verdict.internal.artifact_service import (
            should_archive_artifact,
        )

        assert should_archive_artifact("contract.plan") is False
        assert should_archive_artifact("contract.gap_report") is False
        assert should_archive_artifact("contract.resident_goal") is False
        assert should_archive_artifact("runtime.state.pm") is False
        assert should_archive_artifact("runtime.status.director") is False
        assert should_archive_artifact("runtime.log.director") is False
        assert should_archive_artifact("runtime.control.pm_stop") is False
        assert should_archive_artifact("runtime.control.director_stop") is False
        assert should_archive_artifact("contract.agents_draft") is False
        assert should_archive_artifact("contract.agents_feedback") is False

    def test_unknown_artifact_returns_false(self) -> None:
        """Unknown artifacts return False without crashing."""
        from polaris.cells.audit.verdict.internal.artifact_service import (
            should_archive_artifact,
        )

        assert should_archive_artifact("nonexistent.artifact") is False


class TestLegacyKeyAliases:
    """Test that legacy key names are resolved to canonical keys."""

    def test_legacy_plan_key(self) -> None:
        """Legacy PLAN resolves to contract.plan."""
        from polaris.cells.audit.verdict.internal.artifact_service import (
            get_artifact_policy_metadata,
        )

        metadata = get_artifact_policy_metadata("PLAN")
        assert metadata is not None
        assert metadata["lifecycle"] == "active"

    def test_legacy_pm_tasks_contract(self) -> None:
        """Legacy PM_TASKS_CONTRACT resolves to contract.pm_tasks."""
        from polaris.cells.audit.verdict.internal.artifact_service import (
            get_artifact_policy_metadata,
        )

        metadata = get_artifact_policy_metadata("PM_TASKS_CONTRACT")
        assert metadata is not None
        assert metadata["archive_on_terminal"] is True

    def test_legacy_director_result(self) -> None:
        """Legacy DIRECTOR_RESULT resolves to runtime.result.director."""
        from polaris.cells.audit.verdict.internal.artifact_service import (
            get_artifact_policy_metadata,
        )

        metadata = get_artifact_policy_metadata("DIRECTOR_RESULT")
        assert metadata is not None
        assert metadata["archive_on_terminal"] is True

    def test_legacy_runtime_events(self) -> None:
        """Legacy RUNTIME_EVENTS resolves to audit.events.runtime."""
        from polaris.cells.audit.verdict.internal.artifact_service import (
            should_compress_artifact,
        )

        assert should_compress_artifact("RUNTIME_EVENTS") is True

    def test_legacy_pm_stop_flag(self) -> None:
        """Legacy PM_STOP_FLAG resolves to runtime.control.pm_stop."""
        from polaris.cells.audit.verdict.internal.artifact_service import (
            should_archive_artifact,
        )

        assert should_archive_artifact("PM_STOP_FLAG") is False

    def test_legacy_director_stop_flag(self) -> None:
        """Legacy DIRECTOR_STOP_FLAG resolves to runtime.control.director_stop."""
        from polaris.cells.audit.verdict.internal.artifact_service import (
            should_archive_artifact,
        )

        assert should_archive_artifact("DIRECTOR_STOP_FLAG") is False


class TestArtifactPolicyRegistryAlignment:
    """Test that POLARIS_ARTIFACT_POLICY_METADATA is aligned with ARTIFACT_REGISTRY."""

    def test_all_policy_keys_exist_in_registry(self) -> None:
        """Every key in policy metadata must exist in ARTIFACT_REGISTRY."""
        from polaris.cells.audit.verdict.internal.artifact_service import (
            ARTIFACT_REGISTRY,
            POLARIS_ARTIFACT_POLICY_METADATA,
        )

        policy_keys = set(POLARIS_ARTIFACT_POLICY_METADATA.keys())
        registry_keys = set(ARTIFACT_REGISTRY.keys())

        missing = policy_keys - registry_keys
        assert not missing, f"Policy keys not in registry: {missing}"

    def test_all_known_artifacts_have_policy(self) -> None:
        """All canonical artifact keys have policy metadata."""
        from polaris.cells.audit.verdict.internal.artifact_service import (
            ARTIFACT_REGISTRY,
            POLARIS_ARTIFACT_POLICY_METADATA,
        )

        policy_keys = set(POLARIS_ARTIFACT_POLICY_METADATA.keys())
        registry_keys = set(ARTIFACT_REGISTRY.keys())

        missing = registry_keys - policy_keys
        assert not missing, f"Registry keys missing policy: {missing}"

    def test_all_entries_have_required_fields(self) -> None:
        """All policy entries must have category, lifecycle, compress, archive_on_terminal."""
        from polaris.cells.audit.verdict.internal.artifact_service import (
            POLARIS_ARTIFACT_POLICY_METADATA,
        )

        required_fields = {"category", "lifecycle", "compress", "archive_on_terminal"}
        for key, entry in POLARIS_ARTIFACT_POLICY_METADATA.items():
            missing = required_fields - set(entry.keys())
            assert not missing, f"{key} missing fields: {missing}"
