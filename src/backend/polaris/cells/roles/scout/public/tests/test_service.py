"""Tests for ScoutProbeService (UTF-8)."""
import pytest
from polaris.cells.roles.scout.public.contracts import ScoutProbeTargetV1
from polaris.cells.roles.scout.public.service import ScoutProbeService
from polaris.cells.roles.scout.internal.ports import FakeReadTool, FakeDistiller


@pytest.mark.asyncio
async def test_probe_returns_report_with_findings_and_hash() -> None:
    fake_reads = FakeReadTool({})
    fake_reads._scripted[("repo_rg", ("(def|class|func|function|interface|type)\\s+\\w*payment", "--max", "40"))] = {
        "ok": True, "hits": [{"file": "pay.py", "line": 10, "text": "def payment():"}],
    }
    svc = ScoutProbeService(read_tool=fake_reads, distiller=FakeDistiller("PACK"))
    report = await svc.probe(ScoutProbeTargetV1(query="payment", mode="locate"))
    assert report.summary == "PACK"
    assert report.findings and report.findings[0].path == "pay.py"
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
