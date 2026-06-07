"""Ports (interfaces) for roles.scout + in-memory fakes for tests (UTF-8)."""

from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable

from polaris.cells.roles.scout.public.contracts import ScoutFinding


@runtime_checkable
class ReadToolPort(Protocol):
    """Synchronous read-only tool runner.

    Implementations MUST refuse non-read tools and accept arguments as a dict
    keyed by the tool's real parameter names (e.g. ``{"pattern": ..., "max_results": ...}``).
    """

    def run(self, tool: str, args: dict[str, Any]) -> dict[str, Any]: ...


@runtime_checkable
class DistillerPort(Protocol):
    """Summarize findings into a token-bounded pack."""

    async def distill(self, *, query: str, findings: list[ScoutFinding], token_budget: int) -> str: ...


def canonical_args_key(args: dict[str, Any]) -> str:
    """Deterministic, order-independent key for a dict of tool arguments.

    Used to script and assert :class:`FakeReadTool` calls without depending on
    dict insertion order.
    """
    return json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)


class FakeReadTool:
    """Scripted ReadToolPort for tests.

    Scripted results are keyed by ``(tool, canonical_args_key(args))`` so callers
    can supply expected arguments as a plain dict regardless of key ordering.
    """

    def __init__(self, scripted: dict[tuple[str, str], dict[str, Any]]) -> None:
        self._scripted = scripted
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def run(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool, dict(args)))
        return self._scripted.get((tool, canonical_args_key(args)), {"ok": True, "hits": [], "stdout": ""})


class FakeDistiller:
    """Constant-output DistillerPort for tests."""

    def __init__(self, output: str) -> None:
        self._output = output

    async def distill(self, *, query: str, findings: list[ScoutFinding], token_budget: int) -> str:
        return self._output
