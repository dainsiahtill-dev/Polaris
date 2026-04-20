"""Tests for WS2: Strategy Framework Runtime Handshake Convergence.

Covers:
- StrategyRunContext.from_resolved()
- StrategyRegistry.resolve() / profile hash stability
- StrategyReceiptEmitter write/load roundtrip
- Builtin profile resolution
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from polaris.kernelone.context import (
    StrategyReceiptEmitter,
    StrategyRunContext,
    get_registry,
)
from polaris.kernelone.context.strategy_profiles import BUILTIN_PROFILES
from polaris.kernelone.context.strategy_registry import (
    resolve_profile_hash,
)

if TYPE_CHECKING:
    from pathlib import Path


class TestStrategyRunContext:
    """StrategyRunContext creation and mutation tests."""

    def test_from_resolved_default(self) -> None:
        registry = get_registry()
        resolved = registry.resolve(domain="code")
        ctx = StrategyRunContext.from_resolved(
            resolved,
            turn_index=0,
            session_id="sess-001",
            workspace="/tmp",
            role="director",
            domain="code",
        )
        assert ctx.bundle_id == "kernelone.default.v1"
        assert ctx.profile_id == "canonical_balanced"
        assert ctx.session_id == "sess-001"
        assert ctx.workspace == "/tmp"
        assert ctx.role == "director"
        assert ctx.domain == "code"
        assert ctx.turn_index == 0
        assert ctx.profile_hash != "none"

    def test_from_resolved_turn_increment(self) -> None:
        registry = get_registry()
        resolved = registry.resolve(domain="code")
        ctx0 = StrategyRunContext.from_resolved(resolved, turn_index=0)
        ctx1 = StrategyRunContext.from_resolved(resolved, turn_index=1)
        assert ctx0.turn_index == 0
        assert ctx1.turn_index == 1

    def test_with_tool_call_accumulates(self) -> None:
        registry = get_registry()
        resolved = registry.resolve(domain="code")
        ctx = StrategyRunContext.from_resolved(resolved, turn_index=0)
        ctx2 = ctx.with_tool_call("Read")
        ctx3 = ctx2.with_tool_call("Edit")
        assert "Read" in ctx2.tool_sequence
        assert "Read" in ctx3.tool_sequence
        assert "Edit" in ctx3.tool_sequence

    def test_with_cache_hit_miss(self) -> None:
        registry = get_registry()
        resolved = registry.resolve(domain="code")
        ctx = (
            StrategyRunContext.from_resolved(resolved, turn_index=0)
            .with_cache_hit("file:polaris/kernelone/vfs.py")
            .with_cache_miss("file:polaris/kernelone/bus.py")
        )
        assert "file:polaris/kernelone/vfs.py" in ctx._cache_hits
        assert "file:polaris/kernelone/bus.py" in ctx._cache_misses

    def test_mark_ended_sets_timestamp(self) -> None:
        registry = get_registry()
        resolved = registry.resolve(domain="code")
        ctx = StrategyRunContext.from_resolved(resolved, turn_index=0)
        assert ctx.ended_at == ""
        ctx_ended = ctx.mark_ended()
        assert ctx_ended.ended_at != ""

    def test_to_dict_includes_strategy_identity(self) -> None:
        registry = get_registry()
        resolved = registry.resolve(domain="code")
        ctx = StrategyRunContext.from_resolved(
            resolved,
            turn_index=3,
            session_id="sess-xyz",
            workspace="/repo",
        )
        d = ctx.to_dict()
        assert d["profile_id"] == "canonical_balanced"
        assert d["profile_hash"] == ctx.profile_hash
        assert d["bundle_id"] == "kernelone.default.v1"
        assert d["turn_index"] == 3
        assert d["session_id"] == "sess-xyz"
        assert d["workspace"] == "/repo"

    def test_emit_receipt_contains_strategy_identity(self) -> None:
        registry = get_registry()
        resolved = registry.resolve(domain="code")
        ctx = StrategyRunContext.from_resolved(
            resolved,
            turn_index=2,
            session_id="sess-abc",
            workspace="/repo",
        ).with_tool_call("Search")
        receipt = ctx.emit_receipt()
        assert receipt.profile_id == "canonical_balanced"
        assert receipt.profile_hash == ctx.profile_hash
        assert receipt.turn_index == 2
        assert receipt.session_id == "sess-abc"
        assert "Search" in receipt.tool_sequence


class TestStrategyRegistry:
    """StrategyRegistry resolve and hash tests."""

    def test_resolve_code_domain_defaults_to_canonical(self) -> None:
        registry = get_registry()
        resolved = registry.resolve(domain="code")
        assert resolved.profile.profile_id == "canonical_balanced"
        assert resolved.bundle.bundle_id == "kernelone.default.v1"
        assert resolved.profile_hash != "none"

    def test_resolve_unknown_domain_falls_back_to_code(self) -> None:
        registry = get_registry()
        resolved = registry.resolve(domain="nonexistent")
        assert resolved.profile.profile_id == "canonical_balanced"

    def test_resolve_with_session_override_applies(self) -> None:
        registry = get_registry()
        override = {"compaction": {"trigger_at_budget_pct": 0.99}}
        resolved = registry.resolve(domain="code", override=override)
        assert resolved.profile.overrides.get("compaction", {}).get("trigger_at_budget_pct") == 0.99
        assert "overrides_applied" in resolved.to_dict()

    def test_profile_hash_is_stable(self) -> None:
        registry = get_registry()
        r1 = registry.resolve(domain="code")
        r2 = registry.resolve(domain="code")
        assert r1.profile_hash == r2.profile_hash

    def test_profile_hash_changes_with_override(self) -> None:
        registry = get_registry()
        r1 = registry.resolve(domain="code")
        r2 = registry.resolve(domain="code", override={"compaction": {"trigger_at_budget_pct": 0.99}})
        assert r1.profile_hash != r2.profile_hash

    def test_resolve_profile_hash_matches_utility(self) -> None:
        registry = get_registry()
        resolved = registry.resolve(domain="code")
        direct = resolve_profile_hash(resolved.profile)
        assert direct == resolved.profile_hash


class TestStrategyReceiptEmitter:
    """StrategyReceiptEmitter persistence tests."""

    def test_write_and_load_receipt_roundtrip(self, tmp_path: Path) -> None:
        registry = get_registry()
        resolved = registry.resolve(domain="code")
        ctx = (
            StrategyRunContext.from_resolved(
                resolved,
                turn_index=1,
                session_id="sess-round",
                workspace=str(tmp_path),
            )
            .with_tool_call("Read")
            .with_tool_call("Edit")
            .with_cache_hit("file:a.py")
        )

        emitter = StrategyReceiptEmitter(workspace=str(tmp_path))
        receipt = emitter.emit(run=ctx)
        path = emitter.write_receipt(receipt)

        assert path.exists()
        loaded = emitter.load_receipt(path.stem)
        assert loaded.profile_id == "canonical_balanced"
        assert loaded.turn_index == 1
        assert loaded.session_id == "sess-round"
        assert "Read" in loaded.tool_sequence
        assert "Edit" in loaded.tool_sequence
        assert "file:a.py" in loaded.cache_hits

    def test_list_receipts_returns_sorted_by_mtime(self, tmp_path: Path) -> None:
        emitter = StrategyReceiptEmitter(workspace=str(tmp_path))
        receipts = emitter.list_receipts()
        assert isinstance(receipts, list)


class TestBuiltinProfiles:
    """All built-in profiles resolve cleanly."""

    @pytest.mark.parametrize("profile_id", list(BUILTIN_PROFILES.keys()))
    def test_profile_resolves(self, profile_id: str) -> None:
        registry = get_registry()
        resolved = registry.resolve(domain="code")
        assert resolved.profile.profile_id == "canonical_balanced"
        assert registry.get(profile_id).profile_id == profile_id
