"""Tests for Agent-32: KernelOne role enumeration migration.

Validates that:
1. KernelAuditRole enum no longer contains Polaris business roles (PM, ARCHITECT, etc.)
2. KernelAuditRole.SYSTEM is retained as a safe default for Cell-layer callers.
3. Audit runtime accepts plain string role values.
4. normalize_role returns str, not KernelAuditRole.
5. DEFAULT_ROLE_REQUIREMENTS has been migrated out of KernelOne LLM config.
6. meta_prompting delegates role normalization to the roles Cell.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from polaris.kernelone.audit import (
    SYSTEM_ROLE,
    KernelAuditEvent,
    KernelAuditEventType,
    KernelAuditRole,
    KernelAuditRuntime,
    KernelAuditWriteError,
)
from polaris.kernelone.audit.contracts import (
    GENESIS_HASH,
    KernelChainVerificationResult,
)
from polaris.kernelone.audit.validators import normalize_role


class _InMemoryAuditStore:
    """Minimal in-memory store for testing."""

    def __init__(self, runtime_root: Path) -> None:
        self.runtime_root = runtime_root
        self.events: list[KernelAuditEvent] = []

    def append(self, event: KernelAuditEvent) -> KernelAuditEvent:
        stored = replace(
            event,
            source=dict(event.source),
            task=dict(event.task),
            resource=dict(event.resource),
            action=dict(event.action),
            data=dict(event.data),
            context=dict(event.context),
        )
        self.events.append(stored)
        return stored

    def query(
        self,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        event_type: KernelAuditEventType | None = None,
        role: str | None = None,
        task_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[KernelAuditEvent]:
        del start_time, end_time, event_type, role, task_id
        rows = list(reversed(self.events))
        return rows[offset : offset + limit]

    def export_json(self, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        return {}

    def export_csv(self, **kwargs: Any) -> str:
        del kwargs
        return ""

    def verify_chain(self) -> KernelChainVerificationResult:
        return KernelChainVerificationResult(
            is_valid=True,
            first_hash=GENESIS_HASH,
            last_hash=GENESIS_HASH,
            total_events=len(self.events),
            gap_count=0,
            verified_at=datetime.now(timezone.utc),
        )

    def get_stats(self, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        return {"total_events": len(self.events)}

    def cleanup_old_logs(self, *, dry_run: bool = False) -> dict[str, Any]:
        return {"dry_run": dry_run, "deleted": 0}


@pytest.fixture(autouse=True)
def _reset_audit_singletons() -> None:
    KernelAuditRuntime.shutdown_all()
    yield
    KernelAuditRuntime.shutdown_all()


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: KernelAuditRole no longer contains Polaris business roles
# ─────────────────────────────────────────────────────────────────────────────


class TestKernelAuditRoleMigration:
    """Verify KernelAuditRole enum has been stripped of business roles."""

    def test_kernel_audit_role_has_system_only(self) -> None:
        """SYSTEM sentinel is the only remaining value."""
        members = [m.value for m in KernelAuditRole]
        assert members == ["system"], (
            f"Expected only 'system' in KernelAuditRole, got: {members}"
        )

    def test_kernel_audit_role_system_value(self) -> None:
        """KernelAuditRole.SYSTEM resolves to 'system' string."""
        assert KernelAuditRole.SYSTEM.value == "system"

    def test_system_role_constant_equals_kernel_audit_role_system(self) -> None:
        """SYSTEM_ROLE constant exported from validators matches KernelAuditRole.SYSTEM."""
        assert SYSTEM_ROLE == "system"
        assert KernelAuditRole.SYSTEM.value == SYSTEM_ROLE


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: Audit runtime accepts plain string roles
# ─────────────────────────────────────────────────────────────────────────────


class TestAuditRuntimePlainStringRoles:
    """Verify emit_event works with plain str role values."""

    def test_emit_event_with_string_role(self, tmp_path: Path) -> None:
        """Runtime emits event with a plain string role identifier."""
        runtime_root = tmp_path / "runtime"
        store = _InMemoryAuditStore(runtime_root)
        runtime = KernelAuditRuntime(runtime_root, store)

        result = runtime.emit_event(
            event_type=KernelAuditEventType.TASK_START,
            role="pm",  # Plain string, not KernelAuditRole
            workspace=str(tmp_path),
            run_id="run-test-1",
        )

        assert result.success
        assert result.event_id is not None
        assert len(store.events) == 1
        assert store.events[0].source["role"] == "pm"

    def test_emit_event_with_system_string_role(self, tmp_path: Path) -> None:
        """Runtime emits event with 'system' string role."""
        runtime_root = tmp_path / "runtime"
        store = _InMemoryAuditStore(runtime_root)
        runtime = KernelAuditRuntime(runtime_root, store)

        result = runtime.emit_event(
            event_type=KernelAuditEventType.POLICY_CHECK,
            role="system",
            workspace=str(tmp_path),
            run_id="run-test-2",
        )

        assert result.success
        assert store.events[0].source["role"] == "system"

    def test_emit_event_with_arbitrary_role_string(self, tmp_path: Path) -> None:
        """Runtime accepts any arbitrary role string without enum constraints."""
        runtime_root = tmp_path / "runtime"
        store = _InMemoryAuditStore(runtime_root)
        runtime = KernelAuditRuntime(runtime_root, store)

        result = runtime.emit_event(
            event_type=KernelAuditEventType.LLM_CALL,
            role="my-custom-role",
            workspace=str(tmp_path),
            run_id="run-test-3",
        )

        assert result.success
        assert store.events[0].source["role"] == "my-custom-role"

    def test_emit_llm_event_with_string_role(self, tmp_path: Path) -> None:
        """emit_llm_event accepts plain string role."""
        runtime_root = tmp_path / "runtime"
        store = _InMemoryAuditStore(runtime_root)
        runtime = KernelAuditRuntime(runtime_root, store)

        result = runtime.emit_llm_event(
            role="director",
            workspace=str(tmp_path),
            model="gpt-4",
            prompt_tokens=100,
            completion_tokens=50,
        )

        assert result.success
        assert store.events[0].source["role"] == "director"

    def test_emit_dialogue_with_string_role(self, tmp_path: Path) -> None:
        """emit_dialogue accepts plain string role."""
        runtime_root = tmp_path / "runtime"
        store = _InMemoryAuditStore(runtime_root)
        runtime = KernelAuditRuntime(runtime_root, store)

        result = runtime.emit_dialogue(
            role="qa",
            workspace=str(tmp_path),
            dialogue_type="user_message",
            message_summary="Test message",
        )

        assert result.success
        assert store.events[0].source["role"] == "qa"

    def test_prev_hash_chain_with_string_roles(self, tmp_path: Path) -> None:
        """Hash chain integrity is maintained when using plain string roles."""
        runtime_root = tmp_path / "runtime"
        store = _InMemoryAuditStore(runtime_root)
        runtime = KernelAuditRuntime(runtime_root, store)

        runtime.emit_event(
            event_type=KernelAuditEventType.TASK_START,
            role="architect",
            workspace=str(tmp_path),
            run_id="run-chain-1",
        )
        runtime.emit_event(
            event_type=KernelAuditEventType.TASK_COMPLETE,
            role="architect",
            workspace=str(tmp_path),
            run_id="run-chain-1",
        )

        assert len(store.events) == 2
        # First event starts the chain from genesis hash
        assert store.events[0].prev_hash == GENESIS_HASH
        # Second event chains to first
        second_hash = KernelAuditRuntime._hash_event(store.events[0])
        assert store.events[1].prev_hash == second_hash


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: normalize_role returns str
# ─────────────────────────────────────────────────────────────────────────────


class TestNormalizeRole:
    """Verify normalize_role returns str, not KernelAuditRole."""

    def test_normalize_role_with_string(self) -> None:
        """normalize_role returns str when given str input."""
        result = normalize_role("pm")
        assert isinstance(result, str)
        assert result == "pm"

    def test_normalize_role_with_kernel_audit_role_system(self) -> None:
        """normalize_role returns 'system' when given KernelAuditRole.SYSTEM."""
        result = normalize_role(KernelAuditRole.SYSTEM)
        assert isinstance(result, str)
        assert result == "system"

    def test_normalize_role_strips_whitespace(self) -> None:
        """normalize_role strips whitespace from string input."""
        result = normalize_role("  qa  ")
        assert isinstance(result, str)
        assert result == "qa"

    def test_normalize_role_empty_string(self) -> None:
        """normalize_role handles empty string gracefully."""
        result = normalize_role("")
        assert isinstance(result, str)
        assert result == ""


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: DEFAULT_ROLE_REQUIREMENTS migrated out of KernelOne
# ─────────────────────────────────────────────────────────────────────────────


class TestLLMConfigNoRoleRequirements:
    """Verify DEFAULT_ROLE_REQUIREMENTS is no longer in KernelOne."""

    def test_config_store_has_no_default_role_requirements(self) -> None:
        """DEFAULT_ROLE_REQUIREMENTS is not defined in KernelOne config_store."""
        import polaris.kernelone.llm.config_store as config_store

        assert not hasattr(config_store, "DEFAULT_ROLE_REQUIREMENTS"), (
            "DEFAULT_ROLE_REQUIREMENTS must not exist in KernelOne config_store"
        )

    def test_default_policies_is_generic(self) -> None:
        """DEFAULT_POLICIES has no role-specific entries in KernelOne."""
        from polaris.kernelone.llm.config_store import DEFAULT_POLICIES

        # Generic policies only
        assert "test_required_suites" in DEFAULT_POLICIES
        # No role-specific policy keys
        assert "required_ready_roles" not in DEFAULT_POLICIES
        assert "role_requirements" not in DEFAULT_POLICIES
        assert "pm" not in DEFAULT_POLICIES
        assert "director" not in DEFAULT_POLICIES
        assert "qa" not in DEFAULT_POLICIES
        assert "architect" not in DEFAULT_POLICIES

    def test_build_default_config_has_no_role_requirements_in_policies(self) -> None:
        """build_default_config policies have no role_requirements key."""
        from polaris.kernelone.llm.config_store import build_default_config

        config = build_default_config()
        assert "policies" in config
        assert "role_requirements" not in config["policies"]
        assert "required_ready_roles" not in config["policies"]


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: meta_prompting delegates to roles Cell
# ─────────────────────────────────────────────────────────────────────────────


class TestMetaPromptingRoleNormalization:
    """Verify meta_prompting uses roles Cell for role normalization."""

    def test_normalize_role_alias_from_roles_cell(self) -> None:
        """normalize_role_alias is accessible from the roles Cell contract."""
        from polaris.cells.roles.kernel.public.role_alias import (
            ROLE_ALIASES,
        )

        assert isinstance(ROLE_ALIASES, dict)
        assert ROLE_ALIASES.get("docs") == "architect"
        assert ROLE_ALIASES.get("auditor") == "qa"

    def test_meta_prompting_imports_from_roles_cell(self) -> None:
        """meta_prompting re-exports normalize_role_alias from roles Cell."""
        from polaris.kernelone.prompts import meta_prompting

        assert hasattr(meta_prompting, "normalize_role_alias")
        # Verify it is the same function from roles Cell
        from polaris.cells.roles.kernel.public.role_alias import (
            normalize_role_alias as cell_alias,
        )

        assert meta_prompting.normalize_role_alias is cell_alias

    def test_meta_prompting_resolves_docs_alias(self) -> None:
        """meta_prompting resolves 'docs' to 'architect' via roles Cell."""
        from polaris.kernelone.prompts.meta_prompting import normalize_role_alias

        assert normalize_role_alias("docs") == "architect"

    def test_meta_prompting_resolves_auditor_alias(self) -> None:
        """meta_prompting resolves 'auditor' to 'qa' via roles Cell."""
        from polaris.kernelone.prompts.meta_prompting import normalize_role_alias

        assert normalize_role_alias("auditor") == "qa"

    def test_meta_prompting_unknown_role_passthrough(self) -> None:
        """Unknown role tokens pass through unchanged (lowercased)."""
        from polaris.kernelone.prompts.meta_prompting import normalize_role_alias

        assert normalize_role_alias("SCOUT") == "scout"
        assert normalize_role_alias("custom_agent") == "custom_agent"


# ─────────────────────────────────────────────────────────────────────────────
# Test 6: Audit runtime raises on store failure with string roles
# ─────────────────────────────────────────────────────────────────────────────


class TestAuditRuntimeErrorHandling:
    """Verify error paths work with string roles."""

    def test_raises_on_store_failure_with_string_role(self, tmp_path: Path) -> None:
        """KernelAuditWriteError is raised even when role is a plain string."""

        class _FailingStore(_InMemoryAuditStore):
            def append(self, event: KernelAuditEvent) -> KernelAuditEvent:
                del event
                raise OSError("disk full")

        runtime_root = tmp_path / "runtime"
        runtime = KernelAuditRuntime(runtime_root, _FailingStore(runtime_root))

        with pytest.raises(KernelAuditWriteError, match="Mandatory audit write failed"):
            runtime.emit_event(
                event_type=KernelAuditEventType.LLM_CALL,
                role="director",  # Plain string role
                workspace=str(tmp_path),
                run_id="run-fail-1",
            )
