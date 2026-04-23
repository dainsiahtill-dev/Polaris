"""Unit tests for session_source traceability primitives."""

from __future__ import annotations

import json

import pytest
from polaris.kernelone.traceability.session_source import (
    SessionSource,
    SourceChain,
    SourceChainEncoder,
)


class TestSessionSource:
    """Tests for the SessionSource enum."""

    def test_members_exist(self) -> None:
        assert SessionSource.USER_DIRECT.value == "user_direct"
        assert SessionSource.PM_DELEGATED.value == "pm_delegated"
        assert SessionSource.ARCHITECT_DESIGNED.value == "architect_designed"
        assert SessionSource.CHIEF_ENGINEER_ANALYZED.value == "chief_engineer_analyzed"
        assert SessionSource.DIRECTOR_EXECUTED.value == "director_executed"
        assert SessionSource.QA_VALIDATED.value == "qa_validated"
        assert SessionSource.SYSTEM_GENERATED.value == "system_generated"


class TestSourceChain:
    """Tests for the immutable SourceChain."""

    def test_root_creates_single_element_chain(self) -> None:
        chain = SourceChain.root(SessionSource.USER_DIRECT)
        assert len(chain) == 1
        assert chain.last() == SessionSource.USER_DIRECT

    def test_append_returns_new_chain(self) -> None:
        root = SourceChain.root(SessionSource.USER_DIRECT)
        extended = root.append(SessionSource.PM_DELEGATED)
        # Original is unchanged
        assert len(root) == 1
        assert root.last() == SessionSource.USER_DIRECT
        # New chain has the extra element
        assert len(extended) == 2
        assert extended.last() == SessionSource.PM_DELEGATED

    def test_append_is_chainable(self) -> None:
        chain = (
            SourceChain.root(SessionSource.USER_DIRECT)
            .append(SessionSource.PM_DELEGATED)
            .append(SessionSource.DIRECTOR_EXECUTED)
        )
        assert len(chain) == 3
        assert list(chain) == [
            SessionSource.USER_DIRECT,
            SessionSource.PM_DELEGATED,
            SessionSource.DIRECTOR_EXECUTED,
        ]

    def test_to_list_human_readable(self) -> None:
        chain = SourceChain.root(SessionSource.ARCHITECT_DESIGNED)
        assert chain.to_list() == ["architect_designed"]

    def test_iter_yields_sources(self) -> None:
        chain = SourceChain.root(SessionSource.USER_DIRECT).append(SessionSource.QA_VALIDATED)
        sources = list(chain)
        assert sources == [SessionSource.USER_DIRECT, SessionSource.QA_VALIDATED]

    def test_repr(self) -> None:
        chain = SourceChain.root(SessionSource.SYSTEM_GENERATED)
        assert repr(chain) == "SourceChain([<SessionSource.SYSTEM_GENERATED: 'system_generated'>])"

    def test_empty_chain_last_raises(self) -> None:
        empty = SourceChain(())
        with pytest.raises(IndexError):
            empty.last()

    def test_equality(self) -> None:
        a = SourceChain.root(SessionSource.USER_DIRECT).append(SessionSource.PM_DELEGATED)
        b = SourceChain.root(SessionSource.USER_DIRECT).append(SessionSource.PM_DELEGATED)
        c = SourceChain.root(SessionSource.USER_DIRECT)
        assert a == b
        assert a != c
        assert a != "not a chain"

    def test_hashable(self) -> None:
        a = SourceChain.root(SessionSource.USER_DIRECT)
        b = SourceChain.root(SessionSource.USER_DIRECT)
        assert hash(a) == hash(b)
        # Should be usable as a dict key
        d: dict[SourceChain, int] = {a: 1}
        assert d[b] == 1


class TestSourceChainEncoder:
    """Tests for JSON serialization of SourceChain objects."""

    def test_encodes_source_chain(self) -> None:
        chain = (
            SourceChain.root(SessionSource.USER_DIRECT)
            .append(SessionSource.PM_DELEGATED)
            .append(SessionSource.DIRECTOR_EXECUTED)
        )
        payload = {"chain": chain}
        raw = json.dumps(payload, cls=SourceChainEncoder)
        decoded = json.loads(raw)
        assert decoded["chain"] == ["user_direct", "pm_delegated", "director_executed"]

    def test_encodes_session_source_directly(self) -> None:
        raw = json.dumps({"src": SessionSource.QA_VALIDATED}, cls=SourceChainEncoder)
        assert json.loads(raw)["src"] == "qa_validated"

    def test_fallback_for_unknown_types(self) -> None:
        # A plain object without a handler should raise TypeError
        class Unknown:
            pass

        with pytest.raises(TypeError):
            json.dumps({"obj": Unknown()}, cls=SourceChainEncoder)
