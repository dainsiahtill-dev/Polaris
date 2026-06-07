"""Ports (interfaces) for roles.scout + in-memory fakes for tests (UTF-8)."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from polaris.cells.roles.scout.public.contracts import ScoutFinding


@runtime_checkable
class ReadToolPort(Protocol):
    """Synchronous read-only tool runner. Implementations MUST refuse non-read tools."""

    def run(self, tool: str, args: list[str]) -> dict[str, Any]: ...


@runtime_checkable
class DistillerPort(Protocol):
    """Summarize findings into a token-bounded pack."""

    async def distill(self, *, query: str, findings: list[ScoutFinding], token_budget: int) -> str: ...


class FakeReadTool:
    """Scripted ReadToolPort for tests."""

    def __init__(self, scripted: dict[tuple[str, tuple[str, ...]], dict[str, Any]]) -> None:
        self._scripted = scripted
        self.calls: list[tuple[str, list[str]]] = []

    def run(self, tool: str, args: list[str]) -> dict[str, Any]:
        self.calls.append((tool, list(args)))
        return self._scripted.get((tool, tuple(args)), {"ok": True, "hits": [], "stdout": ""})


class FakeDistiller:
    """Constant-output DistillerPort for tests."""

    def __init__(self, output: str) -> None:
        self._output = output

    async def distill(self, *, query: str, findings: list[ScoutFinding], token_budget: int) -> str:
        return self._output
