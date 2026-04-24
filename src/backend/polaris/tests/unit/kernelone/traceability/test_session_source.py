"""Tests for polaris.kernelone.traceability.session_source."""

from __future__ import annotations

import json

import pytest

from polaris.kernelone.traceability.session_source import (
    SessionSource,
    SourceChain,
    SourceChainEncoder,
)


class TestSessionSource:
    def test_values(self) -> None:
        assert SessionSource.USER_DIRECT.value == "user_direct"
        assert SessionSource.PM_DELEGATED.value == "pm_delegated"
        assert SessionSource.ARCHITECT_DESIGNED.value == "architect_designed"
        assert SessionSource.CHIEF_ENGINEER_ANALYZED.value == "chief_engineer_analyzed"
        assert SessionSource.DIRECTOR_EXECUTED.value == "director_executed"
        assert SessionSource.QA_VALIDATED.value == "qa_validated"
        assert SessionSource.SYSTEM_GENERATED.value == "system_generated"


class TestSourceChain:
    def test_root(self) -> None:
        chain = SourceChain.root(SessionSource.USER_DIRECT)
        assert len(chain) == 1
        assert chain.last() == SessionSource.USER_DIRECT

    def test_append_returns_new_chain(self) -> None:
        chain1 = SourceChain.root(SessionSource.USER_DIRECT)
        chain2 = chain1.append(SessionSource.PM_DELEGATED)
        assert len(chain1) == 1
        assert len(chain2) == 2
        assert chain2.last() == SessionSource.PM_DELEGATED

    def test_to_list(self) -> None:
        chain = SourceChain.root(SessionSource.USER_DIRECT).append(SessionSource.PM_DELEGATED)
        assert chain.to_list() == ["user_direct", "pm_delegated"]

    def test_last_on_empty_raises(self) -> None:
        chain = SourceChain(())
        with pytest.raises(IndexError, match="empty"):
            chain.last()

    def test_eq_and_hash(self) -> None:
        chain1 = SourceChain.root(SessionSource.USER_DIRECT)
        chain2 = SourceChain.root(SessionSource.USER_DIRECT)
        assert chain1 == chain2
        assert hash(chain1) == hash(chain2)

    def test_eq_different(self) -> None:
        chain1 = SourceChain.root(SessionSource.USER_DIRECT)
        chain2 = SourceChain.root(SessionSource.PM_DELEGATED)
        assert chain1 != chain2

    def test_eq_non_chain(self) -> None:
        chain = SourceChain.root(SessionSource.USER_DIRECT)
        assert chain != "user_direct"

    def test_repr(self) -> None:
        chain = SourceChain.root(SessionSource.USER_DIRECT)
        assert repr(chain) == "SourceChain([<SessionSource.USER_DIRECT: 'user_direct'>])"

    def test_iter(self) -> None:
        chain = SourceChain.root(SessionSource.USER_DIRECT).append(SessionSource.PM_DELEGATED)
        values = list(chain)
        assert values == [SessionSource.USER_DIRECT, SessionSource.PM_DELEGATED]


class TestSourceChainEncoder:
    def test_encode_source_chain(self) -> None:
        chain = SourceChain.root(SessionSource.USER_DIRECT).append(SessionSource.PM_DELEGATED)
        result = json.dumps(chain, cls=SourceChainEncoder)
        assert result == '["user_direct", "pm_delegated"]'

    def test_encode_session_source(self) -> None:
        result = json.dumps(SessionSource.USER_DIRECT, cls=SourceChainEncoder)
        assert result == '"user_direct"'

    def test_encode_fallback(self) -> None:
        with pytest.raises(TypeError):
            json.dumps(object(), cls=SourceChainEncoder)
