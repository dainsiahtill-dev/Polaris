"""ScoutProbeService — contract-first facade for roles.scout (UTF-8)."""

from __future__ import annotations

import time

from polaris.cells.roles.scout.internal.cache import TTLCache
from polaris.cells.roles.scout.internal.distiller import DeterministicDistiller
from polaris.cells.roles.scout.internal.evidence import build_content_hash
from polaris.cells.roles.scout.internal.planner import build_read_plan
from polaris.cells.roles.scout.internal.ports import DistillerPort, ReadToolPort
from polaris.cells.roles.scout.internal.ranker import rank
from polaris.cells.roles.scout.internal.retrieval import retrieve
from polaris.cells.roles.scout.public.contracts import ScoutProbeTargetV1, ScoutReportV1


class ScoutProbeService:
    """Synchronous-inline, read-only code/symbol reconnaissance facade."""

    def __init__(
        self,
        read_tool: ReadToolPort,
        distiller: DistillerPort | None = None,
        cache: TTLCache[ScoutReportV1] | None = None,
    ) -> None:
        self._read_tool = read_tool
        self._distiller: DistillerPort = distiller or DeterministicDistiller()
        self._cache: TTLCache[ScoutReportV1] = cache or TTLCache(ttl_seconds=60.0)

    async def probe(self, target: ScoutProbeTargetV1) -> ScoutReportV1:
        key = target.cache_key()
        cached = self._cache.get(key)
        if cached is not None:
            return _with_cache_hit(cached)

        start = time.monotonic()
        plan = build_read_plan(target)
        raw_findings, coverage = retrieve(self._read_tool, plan)
        findings = rank(raw_findings, target)
        summary = await self._distiller.distill(
            query=target.query,
            findings=findings,
            token_budget=target.token_budget,
        )
        content_hash = build_content_hash(
            task_id=target.task_id,
            findings=findings,
            summary=summary,
            tools_used=coverage.get("tools_used", []),
        )
        confidence = max((f.confidence for f in findings), default=0.0)
        duration_ms = int((time.monotonic() - start) * 1000)
        report = ScoutReportV1(
            findings=tuple(findings),
            summary=summary,
            coverage=coverage,
            confidence=confidence,
            content_hash=content_hash,
            usage={
                "model": "deterministic",
                "tokens": 0,
                "duration_ms": duration_ms,
                "context_saved": _estimate_context_saved(coverage),
            },
            cache_hit=False,
        )
        self._cache.set(key, report)
        return report


def _with_cache_hit(report: ScoutReportV1) -> ScoutReportV1:
    from dataclasses import replace

    return replace(report, cache_hit=True)


def _estimate_context_saved(coverage: dict) -> int:  # type: ignore[type-arg]
    """Rough noise the caller avoided: ~200 tokens per raw finding swept."""
    return int(coverage.get("raw_findings", 0)) * 200


def build_default_scout_service(workspace: str) -> ScoutProbeService:
    """Production factory: registry-backed read tools + deterministic distiller."""
    from polaris.cells.roles.scout.internal.read_tool_adapter import RegistryReadTool

    return ScoutProbeService(read_tool=RegistryReadTool(workspace=workspace))
