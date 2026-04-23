"""Integration tests for SessionSource with Terminal Console and pipeline tracing.

These tests verify that SourceChain correctly integrates with JSON serialization
for traceability across the SUPER-mode pipeline.
"""

from __future__ import annotations

import json

import pytest
from polaris.kernelone.traceability.session_source import (
    SessionSource,
    SourceChain,
    SourceChainEncoder,
)

# =============================================================================
# Integration: SourceChain Serialization via SourceChainEncoder
# =============================================================================


class TestSourceChainSerialization:
    """Verify SourceChain serializes correctly via SourceChainEncoder."""

    def test_serialize_empty_chain(self) -> None:
        """Empty SourceChain serializes to empty list."""
        chain = SourceChain(())
        data = {"chain": chain}
        raw = json.dumps(data, cls=SourceChainEncoder)
        decoded = json.loads(raw)
        assert decoded["chain"] == []

    def test_serialize_single_element_chain(self) -> None:
        """Single-element SourceChain serializes to single-item list."""
        chain = SourceChain.root(SessionSource.USER_DIRECT)
        data = {"source": chain}
        raw = json.dumps(data, cls=SourceChainEncoder)
        decoded = json.loads(raw)
        assert decoded["source"] == ["user_direct"]

    def test_serialize_multi_element_chain(self) -> None:
        """Multi-element SourceChain serializes to list of values."""
        chain = (
            SourceChain.root(SessionSource.USER_DIRECT)
            .append(SessionSource.PM_DELEGATED)
            .append(SessionSource.DIRECTOR_EXECUTED)
        )
        data = {"pipeline": chain}
        raw = json.dumps(data, cls=SourceChainEncoder)
        decoded = json.loads(raw)
        assert decoded["pipeline"] == [
            "user_direct",
            "pm_delegated",
            "director_executed",
        ]

    def test_serialize_session_source_directly(self) -> None:
        """SessionSource enum serializes to its value."""
        data = {
            "origin": SessionSource.QA_VALIDATED,
            "source": SessionSource.SYSTEM_GENERATED,
        }
        raw = json.dumps(data, cls=SourceChainEncoder)
        decoded = json.loads(raw)
        assert decoded["origin"] == "qa_validated"
        assert decoded["source"] == "system_generated"

    def test_mixed_serialization(self) -> None:
        """Mixed SourceChain and SessionSource serialize correctly."""
        chain = SourceChain.root(SessionSource.USER_DIRECT).append(SessionSource.PM_DELEGATED)
        data = {
            "trace": chain,
            "last_source": SessionSource.DIRECTOR_EXECUTED,
            "metadata": {"run_id": "123"},
        }
        raw = json.dumps(data, cls=SourceChainEncoder)
        decoded = json.loads(raw)
        assert decoded["trace"] == ["user_direct", "pm_delegated"]
        assert decoded["last_source"] == "director_executed"
        assert decoded["metadata"] == {"run_id": "123"}

    def test_roundtrip_serialization(self) -> None:
        """Serialization and deserialization preserves chain content."""
        original = (
            SourceChain.root(SessionSource.USER_DIRECT)
            .append(SessionSource.PM_DELEGATED)
            .append(SessionSource.DIRECTOR_EXECUTED)
            .append(SessionSource.QA_VALIDATED)
        )
        raw = json.dumps({"chain": original}, cls=SourceChainEncoder)
        decoded = json.loads(raw)

        assert decoded["chain"] == [
            "user_direct",
            "pm_delegated",
            "director_executed",
            "qa_validated",
        ]

    def test_fallback_for_unknown_types(self) -> None:
        """Unknown types raise TypeError for fallback."""

        # Create a custom class that's not handled
        class UnknownType:
            pass

        encoder = SourceChainEncoder()
        with pytest.raises(TypeError):
            encoder.default(UnknownType())


# =============================================================================
# Integration: Chain Propagation
# =============================================================================


class TestChainPropagation:
    """Verify chain propagation from USER_DIRECT through PM_DELEGATED to DIRECTOR_EXECUTED."""

    def test_user_direct_to_pm_delegated(self) -> None:
        """Chain propagates from user to PM."""
        chain = SourceChain.root(SessionSource.USER_DIRECT)
        extended = chain.append(SessionSource.PM_DELEGATED)

        assert chain.to_list() == ["user_direct"]
        assert extended.to_list() == ["user_direct", "pm_delegated"]

    def test_full_super_mode_pipeline(self) -> None:
        """Full SUPER-mode pipeline chain."""
        chain = (
            SourceChain.root(SessionSource.USER_DIRECT)
            .append(SessionSource.PM_DELEGATED)
            .append(SessionSource.ARCHITECT_DESIGNED)
            .append(SessionSource.CHIEF_ENGINEER_ANALYZED)
            .append(SessionSource.DIRECTOR_EXECUTED)
            .append(SessionSource.QA_VALIDATED)
        )

        assert chain.to_list() == [
            "user_direct",
            "pm_delegated",
            "architect_designed",
            "chief_engineer_analyzed",
            "director_executed",
            "qa_validated",
        ]
        assert len(chain) == 6

    def test_chain_propagation_preserves_history(self) -> None:
        """Each append preserves previous sources."""
        chain = SourceChain.root(SessionSource.USER_DIRECT)
        chain = chain.append(SessionSource.PM_DELEGATED)
        chain = chain.append(SessionSource.DIRECTOR_EXECUTED)

        # All previous sources are preserved
        assert SessionSource.USER_DIRECT in chain
        assert SessionSource.PM_DELEGATED in chain
        assert SessionSource.DIRECTOR_EXECUTED in chain
        assert chain.last() == SessionSource.DIRECTOR_EXECUTED

    def test_chain_iteration_order(self) -> None:
        """Iteration yields sources in order."""
        chain = (
            SourceChain.root(SessionSource.USER_DIRECT)
            .append(SessionSource.PM_DELEGATED)
            .append(SessionSource.DIRECTOR_EXECUTED)
        )

        sources = list(chain)
        assert sources == [
            SessionSource.USER_DIRECT,
            SessionSource.PM_DELEGATED,
            SessionSource.DIRECTOR_EXECUTED,
        ]


# =============================================================================
# Integration: SourceChain.root() Singleton Behavior
# =============================================================================


class TestSourceChainRoot:
    """Verify SourceChain.root() creates singleton chains correctly."""

    def test_root_creates_single_element(self) -> None:
        """root() creates chain with exactly one element."""
        chain = SourceChain.root(SessionSource.USER_DIRECT)
        assert len(chain) == 1
        assert chain.last() == SessionSource.USER_DIRECT

    def test_root_every_source_type(self) -> None:
        """root() works for all SessionSource variants."""
        for source in SessionSource:
            chain = SourceChain.root(source)
            assert len(chain) == 1
            assert chain.last() == source

    def test_root_chains_are_equal(self) -> None:
        """Two root() calls with same source create equal chains."""
        chain1 = SourceChain.root(SessionSource.USER_DIRECT)
        chain2 = SourceChain.root(SessionSource.USER_DIRECT)
        assert chain1 == chain2
        assert hash(chain1) == hash(chain2)

    def test_root_chains_are_hashable(self) -> None:
        """Root chains can be used as dict keys."""
        chain = SourceChain.root(SessionSource.USER_DIRECT)
        mapping: dict[SourceChain, str] = {chain: "traced"}
        assert mapping[SourceChain.root(SessionSource.USER_DIRECT)] == "traced"


# =============================================================================
# Integration: Immutability - append() Returns New Chain
# =============================================================================


class TestSourceChainImmutability:
    """Verify SourceChain immutability: append() returns new chain, original unchanged."""

    def test_append_returns_new_chain(self) -> None:
        """append() must not mutate the original chain."""
        original = SourceChain.root(SessionSource.USER_DIRECT)
        extended = original.append(SessionSource.PM_DELEGATED)

        # Original unchanged
        assert len(original) == 1
        assert original.last() == SessionSource.USER_DIRECT

        # New chain has extra element
        assert len(extended) == 2
        assert extended.last() == SessionSource.PM_DELEGATED

    def test_original_unchanged_after_multiple_appends(self) -> None:
        """Original chain stays unchanged after multiple appends."""
        original = SourceChain.root(SessionSource.USER_DIRECT)
        chain = original.append(SessionSource.PM_DELEGATED)
        chain = chain.append(SessionSource.DIRECTOR_EXECUTED)
        chain = chain.append(SessionSource.QA_VALIDATED)

        # Original still single element
        assert len(original) == 1
        assert original.to_list() == ["user_direct"]

    def test_multiple_chains_from_same_root(self) -> None:
        """Multiple chains can be derived from same root without interference."""
        root = SourceChain.root(SessionSource.USER_DIRECT)
        chain1 = root.append(SessionSource.PM_DELEGATED)
        chain2 = root.append(SessionSource.DIRECTOR_EXECUTED)

        assert len(root) == 1
        assert len(chain1) == 2
        assert len(chain2) == 2
        assert chain1 != chain2
        # root is equal to chain1 without last element
        assert root.to_list() == chain1.to_list()[:-1]

    def test_append_returns_functional_value(self) -> None:
        """append() return value can be used immediately."""
        chain = (
            SourceChain.root(SessionSource.USER_DIRECT)
            .append(SessionSource.PM_DELEGATED)
            .append(SessionSource.DIRECTOR_EXECUTED)
        )

        assert chain.to_list() == [
            "user_direct",
            "pm_delegated",
            "director_executed",
        ]

    def test_immutability_with_frozen_dataclass(self) -> None:
        """SourceChain should behave as immutable (though not frozen)."""
        chain = SourceChain.root(SessionSource.USER_DIRECT)
        extended = chain.append(SessionSource.PM_DELEGATED)

        # Verify equality semantics
        assert chain == SourceChain.root(SessionSource.USER_DIRECT)
        assert chain != extended


# =============================================================================
# Integration: to_list() String Representation
# =============================================================================


class TestToListRepresentation:
    """Verify to_list() returns correct string representation."""

    def test_empty_chain_to_list(self) -> None:
        """Empty chain to_list() returns empty list."""
        chain = SourceChain(())
        assert chain.to_list() == []

    def test_single_element_to_list(self) -> None:
        """Single element chain to_list() returns single-item list."""
        chain = SourceChain.root(SessionSource.USER_DIRECT)
        assert chain.to_list() == ["user_direct"]

    def test_multiple_elements_to_list(self) -> None:
        """Multiple element chain to_list() returns all values as strings."""
        chain = (
            SourceChain.root(SessionSource.USER_DIRECT)
            .append(SessionSource.PM_DELEGATED)
            .append(SessionSource.DIRECTOR_EXECUTED)
        )
        assert chain.to_list() == [
            "user_direct",
            "pm_delegated",
            "director_executed",
        ]

    def test_to_list_matches_iteration(self) -> None:
        """to_list() returns same order as iteration."""
        chain = (
            SourceChain.root(SessionSource.ARCHITECT_DESIGNED)
            .append(SessionSource.CHIEF_ENGINEER_ANALYZED)
            .append(SessionSource.DIRECTOR_EXECUTED)
        )
        assert chain.to_list() == [s.value for s in chain]

    def test_to_list_usable_in_json(self) -> None:
        """to_list() output is directly JSON-serializable."""
        chain = SourceChain.root(SessionSource.SYSTEM_GENERATED)
        result = json.dumps(chain.to_list())
        assert result == '["system_generated"]'


# =============================================================================
# Integration: Hashability and Equality
# =============================================================================


class TestHashabilityAndEquality:
    """Verify SourceChain is hashable and comparable."""

    def test_equal_chains_are_equal(self) -> None:
        """Two chains with same content are equal."""
        chain1 = SourceChain.root(SessionSource.USER_DIRECT).append(SessionSource.PM_DELEGATED)
        chain2 = SourceChain.root(SessionSource.USER_DIRECT).append(SessionSource.PM_DELEGATED)
        assert chain1 == chain2

    def test_unequal_chains_are_not_equal(self) -> None:
        """Chains with different content are not equal."""
        chain1 = SourceChain.root(SessionSource.USER_DIRECT).append(SessionSource.PM_DELEGATED)
        chain2 = SourceChain.root(SessionSource.USER_DIRECT).append(SessionSource.DIRECTOR_EXECUTED)
        assert chain1 != chain2

    def test_equal_chains_have_equal_hash(self) -> None:
        """Equal chains have equal hash values."""
        chain1 = SourceChain.root(SessionSource.USER_DIRECT).append(SessionSource.PM_DELEGATED)
        chain2 = SourceChain.root(SessionSource.USER_DIRECT).append(SessionSource.PM_DELEGATED)
        assert hash(chain1) == hash(chain2)

    def test_chain_as_dict_key(self) -> None:
        """Chains can be used as dictionary keys."""
        chain = SourceChain.root(SessionSource.USER_DIRECT).append(SessionSource.DIRECTOR_EXECUTED)
        mapping: dict[SourceChain, str] = {chain: "traced_value"}
        assert mapping[chain] == "traced_value"

    def test_chain_not_equal_to_non_chain(self) -> None:
        """Chain is not equal to non-chain types."""
        chain = SourceChain.root(SessionSource.USER_DIRECT)
        assert chain != "user_direct"
        assert chain != ["user_direct"]
        assert chain != 1

    def test_chain_comparison_with_not_implemented(self) -> None:
        """Chain comparison with incompatible type returns NotImplemented."""
        chain = SourceChain.root(SessionSource.USER_DIRECT)
        result = chain.__eq__("not a chain")
        assert result == NotImplemented


# =============================================================================
# Integration: last() Behavior
# =============================================================================


class TestLastMethod:
    """Verify last() method behavior."""

    def test_last_returns_last_element(self) -> None:
        """last() returns the most recent source in the chain."""
        chain = (
            SourceChain.root(SessionSource.USER_DIRECT)
            .append(SessionSource.PM_DELEGATED)
            .append(SessionSource.DIRECTOR_EXECUTED)
        )
        assert chain.last() == SessionSource.DIRECTOR_EXECUTED

    def test_last_on_single_element(self) -> None:
        """last() on single element chain returns that element."""
        chain = SourceChain.root(SessionSource.QA_VALIDATED)
        assert chain.last() == SessionSource.QA_VALIDATED

    def test_last_on_empty_raises(self) -> None:
        """last() on empty chain raises IndexError."""
        empty = SourceChain(())
        with pytest.raises(IndexError, match="SourceChain is empty"):
            empty.last()


# =============================================================================
# Integration: JSON Encoder Edge Cases
# =============================================================================


class TestEncoderEdgeCases:
    """Test SourceChainEncoder edge cases."""

    def test_encoder_handles_nested_dict(self) -> None:
        """Encoder can handle nested structures."""
        chain = SourceChain.root(SessionSource.USER_DIRECT)
        data = {
            "level1": {
                "level2": {"chain": chain},
            }
        }
        raw = json.dumps(data, cls=SourceChainEncoder)
        decoded = json.loads(raw)
        assert decoded["level1"]["level2"]["chain"] == ["user_direct"]

    def test_encoder_preserves_non_source_types(self) -> None:
        """Encoder preserves non-SourceChain/Source types unchanged."""
        data = {
            "string": "hello",
            "number": 42,
            "float": 3.14,
            "bool": True,
            "null": None,
            "list": [1, 2, 3],
        }
        raw = json.dumps(data, cls=SourceChainEncoder)
        decoded = json.loads(raw)
        assert decoded == data
