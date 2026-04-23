"""Session source tracking for Polaris traceability.

Provides immutable source-chain tracking so that every artifact,
message, and decision in a SUPER-mode pipeline can be traced back
to its origin (user, PM, Director, etc.).
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any


class SessionSource(Enum):
    """Origin of a message or artifact within a Polaris session."""

    USER_DIRECT = "user_direct"
    PM_DELEGATED = "pm_delegated"
    ARCHITECT_DESIGNED = "architect_designed"
    CHIEF_ENGINEER_ANALYZED = "chief_engineer_analyzed"
    DIRECTOR_EXECUTED = "director_executed"
    QA_VALIDATED = "qa_validated"
    SYSTEM_GENERATED = "system_generated"


class SourceChain:
    """Immutable chain of session sources.

    Each ``append()`` returns a *new* ``SourceChain``; the original is
    never modified.  Internally the chain is stored as a tuple for
    hashability and immutability.
    """

    __slots__ = ("_chain",)

    def __init__(self, chain: tuple[SessionSource, ...]) -> None:
        self._chain: tuple[SessionSource, ...] = chain

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def root(cls, source: SessionSource) -> SourceChain:
        """Create a new chain containing exactly one source."""
        return cls((source,))

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def last(self) -> SessionSource:
        """Return the most recent source in the chain.

        Raises:
            IndexError: if the chain is empty (should never happen for
                correctly-constructed instances).
        """
        if not self._chain:
            raise IndexError("SourceChain is empty")
        return self._chain[-1]

    def to_list(self) -> list[str]:
        """Return a human-readable list of source names."""
        return [src.value for src in self._chain]

    def __len__(self) -> int:
        return len(self._chain)

    def __iter__(self) -> Any:
        return iter(self._chain)

    def __repr__(self) -> str:
        return f"SourceChain({list(self._chain)})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SourceChain):
            return NotImplemented
        return self._chain == other._chain

    def __hash__(self) -> int:
        return hash(self._chain)

    # ------------------------------------------------------------------
    # Mutation (functional)
    # ------------------------------------------------------------------

    def append(self, source: SessionSource) -> SourceChain:
        """Return a new chain with *source* appended to the end."""
        return SourceChain((*self._chain, source))


class SourceChainEncoder(json.JSONEncoder):
    """JSON encoder that serialises ``SourceChain`` and ``SessionSource``."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, SourceChain):
            return obj.to_list()
        if isinstance(obj, SessionSource):
            return obj.value
        return super().default(obj)
