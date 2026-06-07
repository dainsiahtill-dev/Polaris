"""Tests for ScoutProbeService (UTF-8)."""

import pytest
from polaris.cells.roles.scout.internal.ports import FakeDistiller, FakeReadTool, canonical_args_key
from polaris.cells.roles.scout.public.contracts import ScoutProbeTargetV1
from polaris.cells.roles.scout.public.service import ScoutProbeService, build_default_scout_service


@pytest.mark.asyncio
async def test_probe_returns_report_with_findings_and_hash() -> None:
    # Symbol-biased rg pattern the planner emits first for the term "payment".
    rg_args = {"pattern": r"(def|class|func|function|interface|type)\s+\w*payment", "max_results": 40}
    fake_reads = FakeReadTool(
        {
            ("repo_rg", canonical_args_key(rg_args)): {
                "ok": True,
                "results": [{"file": "pay.py", "line": 10, "snippet": "def payment():"}],
            },
        }
    )
    svc = ScoutProbeService(read_tool=fake_reads, distiller=FakeDistiller("PACK"))
    report = await svc.probe(ScoutProbeTargetV1(query="payment", mode="locate"))
    assert report.summary == "PACK"
    assert any(f.path == "pay.py" for f in report.findings)
    assert report.content_hash
    assert report.cache_hit is False


@pytest.mark.asyncio
async def test_probe_second_call_is_cache_hit() -> None:
    svc = ScoutProbeService(read_tool=FakeReadTool({}), distiller=FakeDistiller("PACK"))
    target = ScoutProbeTargetV1(query="payment")
    first = await svc.probe(target)
    second = await svc.probe(target)
    assert first.cache_hit is False
    assert second.cache_hit is True


@pytest.mark.asyncio
async def test_probe_real_executor_surfaces_finding(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """End-to-end: the production factory runs REAL read tools and returns a real finding.

    Execution flows through ``build_default_scout_service`` ->
    ``RegistryReadTool`` -> ``AgentAccelToolExecutor`` against a temp workspace.
    The ``repo_tree`` read tool reliably surfaces ``pay.py`` here; ``repo_rg`` /
    ``repo_symbols_index`` fail soft (rg binary / tree-sitter unavailable) and are
    recorded under coverage errors without aborting the probe.
    """
    (tmp_path / "pay.py").write_text("def payment_gateway():\n    return 1\n", encoding="utf-8")
    svc = build_default_scout_service(str(tmp_path))
    report = await svc.probe(ScoutProbeTargetV1(query="payment_gateway"))
    assert any(f.path.endswith("pay.py") for f in report.findings)
    assert "repo_tree" in report.coverage["tools_used"]


def test_scout_probe_dispatches_through_real_executor(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """End-to-end: ``scout_probe`` is dispatched AS a tool by the real executor.

    This exercises the full role-kernel path:
    ``AgentAccelToolExecutor.execute('scout_probe', ...)`` -> ToolHandlerRegistry
    handler -> ``build_default_scout_service`` -> real read tools. The executor
    wraps a successful handler payload under ``result``.
    """
    from polaris.kernelone.llm.toolkit import AgentAccelToolExecutor

    (tmp_path / "pay.py").write_text("def payment_gateway():\n    return 1\n", encoding="utf-8")

    executor = AgentAccelToolExecutor(str(tmp_path))
    try:
        outcome = executor.execute("scout_probe", {"query": "payment_gateway"})
    finally:
        executor.close_sync()

    assert outcome["ok"] is True
    payload = outcome["result"]
    # content_hash is always present on a real report.
    assert payload["content_hash"]
    # repo_tree reliably surfaces pay.py in this env; assert the real finding.
    findings = payload["findings"]
    assert any(str(f.get("path", "")).endswith("pay.py") for f in findings)


def test_scout_probe_rejects_empty_query(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """An empty query is rejected fail-closed at validation (no probe runs)."""
    from polaris.kernelone.llm.toolkit import AgentAccelToolExecutor

    executor = AgentAccelToolExecutor(str(tmp_path))
    try:
        outcome = executor.execute("scout_probe", {"query": "   "})
    finally:
        executor.close_sync()

    assert outcome["ok"] is False
    assert "error" in outcome
