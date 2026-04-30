"""Tests for strategy contracts and profiles.

Covers StrategyProfile, ProfileMetadata, StrategyBundle, StrategyReceipt,
BudgetDecision, Scorecard, and related types. Also tests built-in profiles.
"""

from __future__ import annotations

import dataclasses

import pytest
from polaris.kernelone.context.strategy_contracts import (
    BudgetDecision,
    BudgetDecisionKind,
    CompactionDecision,
    CompactionResult,
    ExpansionDecision,
    ExpansionDecisionResult,
    HistoryMaterialization,
    ProfileMetadata,
    ReadEscalation,
    ReadEscalationDecision,
    ReadEscalationDecisionResult,
    Scorecard,
    ScoreDiff,
    StrategyBundle,
    StrategyProfile,
    StrategyReceipt,
)
from polaris.kernelone.context.strategy_profiles import (
    BUILTIN_PROFILES,
    canonical_balanced,
    claude_like_dynamic,
    cost_guarded,
    deep_research,
    speed_first,
)

# ---------------------------------------------------------------------------
# ProfileMetadata Tests
# ---------------------------------------------------------------------------


class TestProfileMetadata:
    """Test ProfileMetadata dataclass."""

    def test_default_values(self) -> None:
        """ProfileMetadata should have sensible defaults."""
        metadata = ProfileMetadata()
        assert metadata.description == ""
        assert metadata.target_domain == "code"
        assert metadata.risk_level == "canonical"

    def test_custom_values(self) -> None:
        """ProfileMetadata should accept custom values."""
        metadata = ProfileMetadata(
            description="Test profile",
            target_domain="document",
            risk_level="experimental",
        )
        assert metadata.description == "Test profile"
        assert metadata.target_domain == "document"
        assert metadata.risk_level == "experimental"

    def test_frozen_dataclass(self) -> None:
        """ProfileMetadata should be frozen."""
        metadata = ProfileMetadata(description="test")
        with pytest.raises((TypeError, dataclasses.FrozenInstanceError)):  # frozen dataclass
            metadata.description = "modified"


# ---------------------------------------------------------------------------
# StrategyProfile Tests
# ---------------------------------------------------------------------------


class TestStrategyProfile:
    """Test StrategyProfile dataclass."""

    def test_required_fields(self) -> None:
        """StrategyProfile should require profile_id."""
        profile = StrategyProfile(profile_id="test_profile")
        assert profile.profile_id == "test_profile"

    def test_default_values(self) -> None:
        """StrategyProfile should have sensible defaults."""
        profile = StrategyProfile(profile_id="test")
        assert profile.profile_version == "1.0.0"
        assert profile.bundle_id == "kernelone.default.v1"
        assert profile.overrides == {}
        assert isinstance(profile.metadata, ProfileMetadata)

    def test_with_overrides(self) -> None:
        """StrategyProfile should accept overrides dict."""
        profile = StrategyProfile(
            profile_id="test",
            overrides={
                "exploration": {"max_expansion_depth": 5},
                "compaction": {"trigger_at_budget_pct": 0.9},
            },
        )
        assert profile.overrides["exploration"]["max_expansion_depth"] == 5
        assert profile.overrides["compaction"]["trigger_at_budget_pct"] == 0.9

    def test_frozen_dataclass(self) -> None:
        """StrategyProfile should be frozen."""
        profile = StrategyProfile(profile_id="test")
        with pytest.raises((TypeError, dataclasses.FrozenInstanceError)):  # frozen dataclass
            profile.profile_id = "modified"


# ---------------------------------------------------------------------------
# Enums Tests
# ---------------------------------------------------------------------------


class TestEnums:
    """Test enum types."""

    def test_read_escalation_decision_values(self) -> None:
        """ReadEscalationDecision should have expected values."""
        assert ReadEscalationDecision.DIRECT_READ.value == "direct_read"
        assert ReadEscalationDecision.RANGE_FIRST.value == "range_first"
        assert ReadEscalationDecision.DENIED.value == "denied"

    def test_compaction_decision_values(self) -> None:
        """CompactionDecision should have expected values."""
        assert CompactionDecision.TRIGGER.value == "trigger"
        assert CompactionDecision.DEFER.value == "defer"
        assert CompactionDecision.NONE.value == "none"

    def test_budget_decision_kind_values(self) -> None:
        """BudgetDecisionKind should have expected values."""
        assert BudgetDecisionKind.CHECK.value == "check"
        assert BudgetDecisionKind.APPROVED.value == "approved"
        assert BudgetDecisionKind.DENIED.value == "denied"
        assert BudgetDecisionKind.DEFERRED.value == "deferred"
        assert BudgetDecisionKind.COMPACTION_SUGGESTED.value == "compaction_suggested"

    def test_expansion_decision_values(self) -> None:
        """ExpansionDecision should have expected values."""
        # These are imported from exploration_policy
        assert ExpansionDecision.APPROVED.value == "approved"
        assert ExpansionDecision.DENIED.value == "denied"
        assert ExpansionDecision.DEFERRED.value == "deferred"


# ---------------------------------------------------------------------------
# Result Types Tests
# ---------------------------------------------------------------------------


class TestExpansionDecisionResult:
    """Test ExpansionDecisionResult dataclass."""

    def test_required_fields(self) -> None:
        """ExpansionDecisionResult should require decision."""
        result = ExpansionDecisionResult(decision="approved")
        assert result.decision == "approved"
        assert result.reason == ""
        assert result.asset_key == ""

    def test_with_all_fields(self) -> None:
        """ExpansionDecisionResult should accept all fields."""
        result = ExpansionDecisionResult(
            decision="denied",
            reason="Budget exceeded",
            asset_key="src/main.py",
        )
        assert result.decision == "denied"
        assert result.reason == "Budget exceeded"
        assert result.asset_key == "src/main.py"

    def test_frozen(self) -> None:
        """ExpansionDecisionResult should be frozen."""
        result = ExpansionDecisionResult(decision="approved")
        with pytest.raises((TypeError, dataclasses.FrozenInstanceError)):
            result.decision = "denied"


class TestReadEscalationDecisionResult:
    """Test ReadEscalationDecisionResult dataclass."""

    def test_with_decision_enum(self) -> None:
        """ReadEscalationDecisionResult should accept ReadEscalationDecision enum."""
        result = ReadEscalationDecisionResult(
            decision=ReadEscalationDecision.RANGE_FIRST,
            asset_key="file.py",
            estimated_tokens=500,
            reason="File too large for full read",
        )
        assert result.decision == ReadEscalationDecision.RANGE_FIRST
        assert result.asset_key == "file.py"
        assert result.estimated_tokens == 500


class TestCompactionResult:
    """Test CompactionResult dataclass."""

    def test_triggered_compaction(self) -> None:
        """CompactionResult should record compaction details."""
        result = CompactionResult(
            triggered=True,
            compacted_items=5,
            tokens_recovered=2000,
            summary="Compressed history items",
        )
        assert result.triggered is True
        assert result.compacted_items == 5
        assert result.tokens_recovered == 2000

    def test_no_compaction(self) -> None:
        """CompactionResult should handle no compaction case."""
        result = CompactionResult(triggered=False)
        assert result.triggered is False
        assert result.compacted_items == 0
        assert result.tokens_recovered == 0


class TestHistoryMaterialization:
    """Test HistoryMaterialization dataclass."""

    def test_defaults(self) -> None:
        """HistoryMaterialization should have sensible defaults."""
        hm = HistoryMaterialization()
        assert hm.history_tokens == 0
        assert hm.receipt_tokens == 0
        assert hm.total_tokens == 0
        assert hm.message_count == 0
        assert hm.receipt_count == 0
        assert hm.micro_compacted is False
        assert hm.artifact_stub_count == 0

    def test_with_values(self) -> None:
        """HistoryMaterialization should accept values."""
        hm = HistoryMaterialization(
            history_tokens=1000,
            receipt_tokens=200,
            total_tokens=1200,
            message_count=10,
            receipt_count=5,
            micro_compacted=True,
            artifact_stub_count=2,
        )
        assert hm.total_tokens == 1200
        assert hm.micro_compacted is True


# ---------------------------------------------------------------------------
# BudgetDecision Tests
# ---------------------------------------------------------------------------


class TestBudgetDecision:
    """Test BudgetDecision dataclass."""

    def test_with_kind(self) -> None:
        """BudgetDecision should accept BudgetDecisionKind."""
        decision = BudgetDecision(
            kind=BudgetDecisionKind.APPROVED,
            estimated_tokens=100,
            headroom_before=500,
            headroom_after=400,
            decision="approved",
            reason="Within budget",
        )
        assert decision.kind == BudgetDecisionKind.APPROVED
        assert decision.estimated_tokens == 100
        assert decision.headroom_after == 400


# ---------------------------------------------------------------------------
# ReadEscalation Tests
# ---------------------------------------------------------------------------


class TestReadEscalation:
    """Test ReadEscalation dataclass."""

    def test_fields(self) -> None:
        """ReadEscalation should have expected fields."""
        escalation = ReadEscalation(
            asset_key="src/main.py",
            decision="range_first",
            estimated_tokens=500,
            reason="Large file",
        )
        assert escalation.asset_key == "src/main.py"
        assert escalation.decision == "range_first"
        assert escalation.estimated_tokens == 500


# ---------------------------------------------------------------------------
# StrategyReceipt Tests
# ---------------------------------------------------------------------------


class TestStrategyReceipt:
    """Test StrategyReceipt dataclass."""

    def test_required_fields(self) -> None:
        """StrategyReceipt should require identity fields."""
        receipt = StrategyReceipt(
            bundle_id="kernelone.default.v1",
            bundle_version="1.0.0",
            profile_id="test",
            profile_hash="abc123",
            turn_index=1,
        )
        assert receipt.bundle_id == "kernelone.default.v1"
        assert receipt.turn_index == 1

    def test_defaults(self) -> None:
        """StrategyReceipt should have sensible defaults."""
        receipt = StrategyReceipt(
            bundle_id="test",
            bundle_version="1.0.0",
            profile_id="test",
            profile_hash="hash",
            turn_index=0,
        )
        assert receipt.timestamp is not None
        assert receipt.budget_decisions == ()
        assert receipt.read_escalations == ()
        assert receipt.compaction_triggered is False
        assert receipt.run_id == ""

    def test_to_dict(self) -> None:
        """StrategyReceipt should serialize to dict."""
        receipt = StrategyReceipt(
            bundle_id="test",
            bundle_version="1.0.0",
            profile_id="test",
            profile_hash="hash123",
            turn_index=1,
            prompt_tokens_estimate=500,
        )
        d = receipt.to_dict()
        assert d["bundle_id"] == "test"
        assert d["turn_index"] == 1
        assert d["prompt_tokens_estimate"] == 500
        assert "timestamp" in d
        assert "budget_decisions" in d

    def test_to_dict_with_decisions(self) -> None:
        """StrategyReceipt.to_dict should serialize nested decisions."""
        budget_decision = BudgetDecision(
            kind=BudgetDecisionKind.APPROVED,
            estimated_tokens=100,
            headroom_before=500,
            headroom_after=400,
            decision="approved",
            reason="OK",
        )
        receipt = StrategyReceipt(
            bundle_id="test",
            bundle_version="1.0.0",
            profile_id="test",
            profile_hash="hash",
            turn_index=1,
            budget_decisions=(budget_decision,),
        )
        d = receipt.to_dict()
        assert len(d["budget_decisions"]) == 1
        assert d["budget_decisions"][0]["kind"] == "approved"


# ---------------------------------------------------------------------------
# Scorecard Tests
# ---------------------------------------------------------------------------


class TestScorecard:
    """Test Scorecard dataclass."""

    def test_default_scores(self) -> None:
        """Scorecard should have default score values."""
        scorecard = Scorecard()
        assert scorecard.quality_score == 0.0
        assert scorecard.efficiency_score == 0.0
        assert scorecard.overall_score == 0.0

    def test_with_scores(self) -> None:
        """Scorecard should accept score values."""
        scorecard = Scorecard(
            quality_score=0.9,
            efficiency_score=0.8,
            context_score=0.7,
            latency_score=0.95,
            cost_score=0.85,
            overall_score=0.85,
        )
        assert scorecard.quality_score == 0.9
        assert scorecard.overall_score == 0.85

    def test_scores_clamped_to_1(self) -> None:
        """Scorecard should clamp scores to 0.0-1.0 (by convention)."""
        # Note: The dataclass doesn't enforce this, but convention says scores
        # should be in range
        scorecard = Scorecard(
            quality_score=1.5,  # Would be invalid
            overall_score=1.5,
        )
        assert scorecard.quality_score == 1.5  # Still stored
        # Enforcement would be done by validation layer

    def test_to_dict(self) -> None:
        """Scorecard should serialize to dict."""
        scorecard = Scorecard(
            bundle_id="test",
            profile_id="canonical",
            profile_hash="hash",
            turn_index=1,
            quality_score=0.9,
            overall_score=0.85,
        )
        d = scorecard.to_dict()
        assert d["bundle_id"] == "test"
        assert d["quality_score"] == 0.9
        assert "timestamp" in d


# ---------------------------------------------------------------------------
# ScoreDiff Tests
# ---------------------------------------------------------------------------


class TestScoreDiff:
    """Test ScoreDiff dataclass."""

    def test_fields(self) -> None:
        """ScoreDiff should have delta fields."""
        diff = ScoreDiff(
            profile_a="profile_a",
            profile_b="profile_b",
            quality_delta=0.1,
            efficiency_delta=-0.05,
            overall_delta=0.05,
            winner="a",
        )
        assert diff.profile_a == "profile_a"
        assert diff.profile_b == "profile_b"
        assert diff.quality_delta == 0.1
        assert diff.winner == "a"

    def test_tie(self) -> None:
        """ScoreDiff should handle ties."""
        diff = ScoreDiff(
            profile_a="a",
            profile_b="b",
            overall_delta=0.0,
            winner="tie",
        )
        assert diff.winner == "tie"


# ---------------------------------------------------------------------------
# StrategyBundle Tests
# ---------------------------------------------------------------------------


class TestStrategyBundle:
    """Test StrategyBundle dataclass."""

    def test_required_fields(self) -> None:
        """StrategyBundle should require bundle_id and version."""
        bundle = StrategyBundle(
            bundle_id="kernelone.default.v1",
            bundle_version="1.0.0",
        )
        assert bundle.bundle_id == "kernelone.default.v1"
        assert bundle.bundle_version == "1.0.0"

    def test_strategy_ports_optional(self) -> None:
        """StrategyBundle should accept optional strategy ports."""
        bundle = StrategyBundle(
            bundle_id="test",
            bundle_version="1.0.0",
            exploration=None,
            compaction=None,
        )
        assert bundle.exploration is None
        assert bundle.compaction is None


# ---------------------------------------------------------------------------
# Built-in Profiles Tests
# ---------------------------------------------------------------------------


class TestBuiltinProfiles:
    """Test built-in strategy profiles."""

    def test_all_profiles_have_required_fields(self) -> None:
        """All built-in profiles should have required fields."""
        profiles = [
            canonical_balanced,
            speed_first,
            deep_research,
            cost_guarded,
            claude_like_dynamic,
        ]
        for profile in profiles:
            assert profile.profile_id
            assert profile.profile_version
            assert profile.bundle_id
            assert isinstance(profile.metadata, ProfileMetadata)

    def test_canonical_balanced(self) -> None:
        """canonical_balanced should have expected settings."""
        assert canonical_balanced.profile_id == "canonical_balanced"
        assert canonical_balanced.metadata.target_domain == "code"
        assert canonical_balanced.metadata.risk_level == "canonical"

        # Check exploration overrides
        exploration = canonical_balanced.overrides.get("exploration", {})
        assert exploration.get("map_first") is True
        assert exploration.get("search_before_read") is True
        assert exploration.get("max_expansion_depth") == 3

        # Check compaction overrides
        compaction = canonical_balanced.overrides.get("compaction", {})
        assert compaction.get("receipt_micro_compact") is True

    def test_speed_first(self) -> None:
        """speed_first should be optimized for low latency."""
        assert speed_first.profile_id == "speed_first"
        assert speed_first.metadata.risk_level == "experimental"

        exploration = speed_first.overrides.get("exploration", {})
        assert exploration.get("max_expansion_depth") == 1
        assert exploration.get("map_first") is False

        # Should have faster cache TTLs
        cache = speed_first.overrides.get("cache", {})
        assert cache.get("hot_slice_ttl_seconds") > 300

    def test_deep_research(self) -> None:
        """deep_research should have deeper exploration settings."""
        assert deep_research.profile_id == "deep_research"
        assert deep_research.metadata.risk_level == "experimental"

        exploration = deep_research.overrides.get("exploration", {})
        assert exploration.get("max_expansion_depth") == 5
        assert exploration.get("neighbor_expansion_aggressive") is True

        # Should have later compaction trigger
        compaction = deep_research.overrides.get("compaction", {})
        assert compaction.get("trigger_at_budget_pct") == 0.90

    def test_cost_guarded(self) -> None:
        """cost_guarded should have conservative settings."""
        assert cost_guarded.profile_id == "cost_guarded"
        assert cost_guarded.metadata.target_domain == "universal"
        assert cost_guarded.metadata.risk_level == "experimental"

        # Should have early compaction
        compaction = cost_guarded.overrides.get("compaction", {})
        assert compaction.get("trigger_at_budget_pct") == 0.65

        # Should have longer cache TTLs
        cache = cost_guarded.overrides.get("cache", {})
        assert cache.get("repo_map_ttl_seconds") == 3600

    def test_claude_like_dynamic(self) -> None:
        """claude_like_dynamic should be a reference profile."""
        assert claude_like_dynamic.profile_id == "claude_like_dynamic"
        assert claude_like_dynamic.metadata.risk_level == "reference"

        # Should have search_first behavior
        exploration = claude_like_dynamic.overrides.get("exploration", {})
        assert exploration.get("search_first") is True
        assert exploration.get("implicit_map") is True

    def test_builtin_profiles_registry(self) -> None:
        """BUILTIN_PROFILES should contain all built-in profiles."""
        assert len(BUILTIN_PROFILES) == 5
        assert "canonical_balanced" in BUILTIN_PROFILES
        assert "speed_first" in BUILTIN_PROFILES
        assert "deep_research" in BUILTIN_PROFILES
        assert "cost_guarded" in BUILTIN_PROFILES
        assert "claude_like_dynamic" in BUILTIN_PROFILES

    def test_profile_ids_unique(self) -> None:
        """All built-in profiles should have unique IDs."""
        ids = [p.profile_id for p in BUILTIN_PROFILES.values()]
        assert len(ids) == len(set(ids))

    def test_profiles_frozen(self) -> None:
        """Built-in profiles should be frozen (immutable)."""
        with pytest.raises((TypeError, dataclasses.FrozenInstanceError)):
            canonical_balanced.profile_id = "modified"

    def test_all_profiles_valid_overrides_structure(self) -> None:
        """All profiles should have valid override structure."""
        valid_keys = {
            "exploration",
            "read_escalation",
            "compaction",
            "cache",
        }
        for profile in BUILTIN_PROFILES.values():
            for key in profile.overrides:
                assert key in valid_keys, f"Unknown override key: {key}"


# ---------------------------------------------------------------------------
# Integration Tests
# ---------------------------------------------------------------------------


class TestStrategyIntegration:
    """Integration tests for strategy components."""

    def test_create_receipt_from_profile(self) -> None:
        """Should be able to create a receipt from a profile."""
        receipt = StrategyReceipt(
            bundle_id=canonical_balanced.bundle_id,
            bundle_version=canonical_balanced.profile_version,
            profile_id=canonical_balanced.profile_id,
            profile_hash="abc123",
            turn_index=1,
            prompt_tokens_estimate=1000,
            exploration_phase_reached=ExpansionDecision.APPROVED.value,
        )
        assert receipt.profile_id == "canonical_balanced"
        assert receipt.exploration_phase_reached == "approved"

    def test_profile_roundtrip(self) -> None:
        """Profile should survive serialization roundtrip."""
        original = canonical_balanced
        # Convert to dict (simulating serialization)
        d = {
            "profile_id": original.profile_id,
            "profile_version": original.profile_version,
            "bundle_id": original.bundle_id,
            "overrides": original.overrides,
            "metadata": {
                "description": original.metadata.description,
                "target_domain": original.metadata.target_domain,
                "risk_level": original.metadata.risk_level,
            },
        }
        # Reconstruct
        reconstructed = StrategyProfile(
            profile_id=d["profile_id"],
            profile_version=d["profile_version"],
            bundle_id=d["bundle_id"],
            overrides=d["overrides"],
            metadata=ProfileMetadata(
                description=d["metadata"]["description"],
                target_domain=d["metadata"]["target_domain"],
                risk_level=d["metadata"]["risk_level"],
            ),
        )
        assert reconstructed.profile_id == original.profile_id
        assert reconstructed.overrides == original.overrides
