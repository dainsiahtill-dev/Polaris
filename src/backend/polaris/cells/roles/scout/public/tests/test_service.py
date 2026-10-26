"""Tests for ScoutProbeService (UTF-8)."""

import asyncio

import pytest
from polaris.cells.roles.scout.internal.ports import FakeDistiller, FakeReadTool, canonical_args_key
from polaris.cells.roles.scout.public.contracts import ScoutProbeTargetV1, ScoutReportV1
from polaris.cells.roles.scout.public.service import ScoutProbeService, build_default_scout_service


def _patch_escalation(monkeypatch: pytest.MonkeyPatch, output: str) -> list[ScoutProbeTargetV1]:
    """Replace the escalation bridge with a recording fake (no real LLM call).

    Returns the list that captures each escalated target so callers can assert
    whether (and how many times) escalation was triggered.
    """
    from polaris.cells.roles.scout.internal import escalation as escalation_module

    captured: list[ScoutProbeTargetV1] = []

    async def fake_escalate(target: ScoutProbeTargetV1, workspace: str) -> str:
        captured.append(target)
        return output

    monkeypatch.setattr(escalation_module, "escalate_probe", fake_escalate)
    return captured


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

    Critically, this asserts the *Cell* answered — not a duplicate scanner. The
    handler delegates to ``build_default_scout_service`` (single source of truth),
    so the dispatched ``content_hash`` must equal the one the Cell produces
    directly, and ``coverage`` must carry the Cell's keys (``raw_findings`` /
    ``tools_used``) and NOT the deleted scanner's ``files_scanned`` shape.
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

    # PROOF the Cell (not a scanner) answered: the dispatched result is byte-for-byte
    # the Cell's report. content_hash is the Cell's deterministic 16-char digest, and
    # coverage carries Cell keys — never the deleted scanner's `files_scanned` shape.
    cell_report = asyncio.run(
        build_default_scout_service(str(tmp_path)).probe(ScoutProbeTargetV1(query="payment_gateway"))
    )
    assert payload["content_hash"] == cell_report.content_hash
    assert len(payload["content_hash"]) == 16  # Cell digest; the old scanner used 64-char sha256.
    assert "raw_findings" in payload["coverage"]
    assert "tools_used" in payload["coverage"]
    assert "files_scanned" not in payload["coverage"]


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


# ── authority-bound cache isolation ──


class _CountingReadTool:
    """A read tool that records its calls and returns a fixed payload."""

    def __init__(self, file_name: str) -> None:
        self.file_name = file_name
        self.calls: list[tuple[str, dict[str, object]]] = []

    def run(self, tool: str, args: dict[str, object]) -> dict[str, object]:
        self.calls.append((tool, dict(args)))
        return {
            "ok": True,
            "results": [{"file": self.file_name, "line": 1, "snippet": "x"}],
        }


@pytest.mark.asyncio
async def test_two_workspaces_never_share_a_cached_report() -> None:
    """Two workspaces with the SAME roots and query must not share a report.

    A shared cache would let workspace B's probe return workspace A's content
    without ever calling B's read port.
    """
    from polaris.cells.roles.scout.internal.cache import TTLCache
    from polaris.cells.roles.scout.public.service import ScoutReadAuthorityV1

    shared_cache: TTLCache[ScoutReportV1] = TTLCache(ttl_seconds=60.0)
    tool_a = _CountingReadTool("alpha/a.py")
    tool_b = _CountingReadTool("beta/b.py")

    svc_a = ScoutProbeService(
        read_tool=tool_a,
        cache=shared_cache,
        workspace="/workspace-a",
        authority=ScoutReadAuthorityV1(
            capability_token={"allowed_read_paths": ["alpha"]},
            read_roots=("alpha",),
        ),
    )
    svc_b = ScoutProbeService(
        read_tool=tool_b,
        cache=shared_cache,
        workspace="/workspace-b",
        authority=ScoutReadAuthorityV1(
            capability_token={"allowed_read_paths": ["alpha"]},
            read_roots=("alpha",),
        ),
    )

    report_a = await svc_a.probe(ScoutProbeTargetV1(query="payment"))
    report_b = await svc_b.probe(ScoutProbeTargetV1(query="payment"))

    assert report_a.findings[0].path == "alpha/a.py"
    assert report_b.findings[0].path == "beta/b.py", "workspace B received workspace A's cached content"
    assert tool_b.calls, "workspace B's read port was never called"


@pytest.mark.asyncio
async def test_a_shrinking_scope_cannot_reuse_a_wider_report() -> None:
    """A narrower scope must not reuse a report produced under a wider one."""
    from polaris.cells.roles.scout.internal.cache import TTLCache
    from polaris.cells.roles.scout.public.service import ScoutReadAuthorityV1

    shared_cache: TTLCache[ScoutReportV1] = TTLCache(ttl_seconds=60.0)
    wide_tool = _CountingReadTool("alpha/a.py")
    narrow_tool = _CountingReadTool("alpha/narrow.py")

    wide = ScoutProbeService(
        read_tool=wide_tool,
        cache=shared_cache,
        workspace="/workspace-a",
        authority=ScoutReadAuthorityV1(
            capability_token={"allowed_read_paths": ["alpha", "beta"]},
            read_roots=("alpha", "beta"),
        ),
    )
    narrow = ScoutProbeService(
        read_tool=narrow_tool,
        cache=shared_cache,
        workspace="/workspace-a",
        authority=ScoutReadAuthorityV1(
            capability_token={"allowed_read_paths": ["alpha"]},
            read_roots=("alpha",),
        ),
    )

    await wide.probe(ScoutProbeTargetV1(query="payment"))
    report = await narrow.probe(ScoutProbeTargetV1(query="payment"))

    assert report.findings[0].path == "alpha/narrow.py", "the narrower scope reused the wider scope's report"
    assert narrow_tool.calls, "the narrower scope's read port was never called"


@pytest.mark.asyncio
async def test_a_different_authorizing_identity_cannot_reuse_a_report() -> None:
    """A different authorizing identity must not reuse another one's report."""
    from polaris.cells.roles.scout.internal.cache import TTLCache
    from polaris.cells.roles.scout.public.service import ScoutReadAuthorityV1

    shared_cache: TTLCache[ScoutReportV1] = TTLCache(ttl_seconds=60.0)
    tool_a = _CountingReadTool("alpha/a.py")
    tool_b = _CountingReadTool("alpha/b.py")

    svc_a = ScoutProbeService(
        read_tool=tool_a,
        cache=shared_cache,
        workspace="/workspace-a",
        authority=ScoutReadAuthorityV1(
            capability_token={"allowed_read_paths": ["alpha"], "token_id": "token-a"},
            read_roots=("alpha",),
        ),
    )
    svc_b = ScoutProbeService(
        read_tool=tool_b,
        cache=shared_cache,
        workspace="/workspace-a",
        authority=ScoutReadAuthorityV1(
            capability_token={"allowed_read_paths": ["alpha"], "token_id": "token-b"},
            read_roots=("alpha",),
        ),
    )

    await svc_a.probe(ScoutProbeTargetV1(query="payment"))
    report = await svc_b.probe(ScoutProbeTargetV1(query="payment"))

    assert report.findings[0].path == "alpha/b.py", "a different authorizing identity reused another identity's report"
    assert tool_b.calls, "the second identity's read port was never called"


@pytest.mark.asyncio
async def test_probe_escalates_when_findings_empty_and_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A weak (no-finding) deterministic result escalates when allow_escalation=True."""
    captured = _patch_escalation(monkeypatch, "ESCALATED SUMMARY")
    svc = ScoutProbeService(read_tool=FakeReadTool({}), distiller=FakeDistiller("DETERMINISTIC"))
    report = await svc.probe(ScoutProbeTargetV1(query="nonexistent symbol", allow_escalation=True))
    assert report.findings == ()
    assert report.escalated is True
    assert report.summary == "ESCALATED SUMMARY"
    assert len(captured) == 1


@pytest.mark.asyncio
async def test_probe_does_not_escalate_when_not_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """No escalation when allow_escalation is False, even with an empty result."""
    captured = _patch_escalation(monkeypatch, "ESCALATED SUMMARY")
    svc = ScoutProbeService(read_tool=FakeReadTool({}), distiller=FakeDistiller("DETERMINISTIC"))
    report = await svc.probe(ScoutProbeTargetV1(query="nonexistent symbol", allow_escalation=False))
    assert report.findings == ()
    assert report.escalated is False
    assert report.summary == "DETERMINISTIC"
    assert captured == []


@pytest.mark.asyncio
async def test_probe_does_not_escalate_when_findings_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    """A strong deterministic result (real finding) does not escalate even if allowed."""
    captured = _patch_escalation(monkeypatch, "ESCALATED SUMMARY")
    rg_args = {"pattern": r"(def|class|func|function|interface|type)\s+\w*payment", "max_results": 40}
    fake_reads = FakeReadTool(
        {
            ("repo_rg", canonical_args_key(rg_args)): {
                "ok": True,
                "results": [{"file": "pay.py", "line": 10, "snippet": "def payment():"}],
            },
        }
    )
    svc = ScoutProbeService(read_tool=fake_reads, distiller=FakeDistiller("DETERMINISTIC"))
    report = await svc.probe(ScoutProbeTargetV1(query="payment", allow_escalation=True))
    assert report.findings
    assert report.confidence >= 0.3
    assert report.escalated is False
    assert report.summary == "DETERMINISTIC"
    assert captured == []


@pytest.mark.asyncio
async def test_a_live_scope_shrink_invalidates_a_cached_report() -> None:
    """A host that shrinks its scope must not keep serving the old report.

    The authority is re-read LIVE at the moment of use, so a report cached
    under a wider scope is refused once the host narrowed its scope. A frozen
    snapshot in the cache key is NOT sufficient on its own.
    """
    from polaris.cells.roles.scout.internal.cache import TTLCache
    from polaris.cells.roles.scout.public.service import ScoutProbeDeniedError, ScoutReadAuthorityV1

    shared_cache: TTLCache[ScoutReportV1] = TTLCache(ttl_seconds=60.0)
    tool = _CountingReadTool("alpha/a.py")

    live: dict[str, ScoutReadAuthorityV1] = {
        "current": ScoutReadAuthorityV1(
            capability_token={"allowed_read_paths": ["alpha", "beta"]},
            read_roots=("alpha", "beta"),
        ),
    }

    def _reader() -> ScoutReadAuthorityV1:
        return live["current"]

    svc = ScoutProbeService(
        read_tool=tool,
        cache=shared_cache,
        workspace="/workspace-a",
        authority=ScoutReadAuthorityV1(
            capability_token={"allowed_read_paths": ["alpha", "beta"]},
            read_roots=("alpha", "beta"),
            authority_reader=_reader,
        ),
    )

    await svc.probe(ScoutProbeTargetV1(query="payment"))

    # The host shrinks its scope to nothing.
    live["current"] = ScoutReadAuthorityV1(
        capability_token={"allowed_read_paths": []},
        read_roots=(),
    )

    with pytest.raises(ScoutProbeDeniedError):
        await svc.probe(ScoutProbeTargetV1(query="payment"))


@pytest.mark.asyncio
async def test_a_cache_hit_whose_paths_leave_the_current_scope_is_refused() -> None:
    """A cached report naming a path outside the live roots must not be served.

    This is the defence-in-depth layer: the cache key already binds the
    committed roots, but a report whose FINDINGS leave the current scope must
    be refused even on a key hit, so a read port that returned an
    out-of-scope path cannot be laundered through the cache.
    """
    from polaris.cells.roles.scout.internal.cache import TTLCache
    from polaris.cells.roles.scout.public.service import ScoutProbeDeniedError, ScoutReadAuthorityV1

    shared_cache: TTLCache[ScoutReportV1] = TTLCache(ttl_seconds=60.0)
    # A read port that returns a path OUTSIDE the committed scope.
    tool = _CountingReadTool("private/hidden.py")

    svc = ScoutProbeService(
        read_tool=tool,
        cache=shared_cache,
        workspace="/workspace-a",
        authority=ScoutReadAuthorityV1(
            capability_token={"allowed_read_paths": ["alpha"]},
            read_roots=("alpha",),
        ),
    )

    await svc.probe(ScoutProbeTargetV1(query="payment"))

    # The second probe hits the SAME cache key (same workspace/roots/identity),
    # so the cached report must be re-validated against the current scope.
    with pytest.raises(ScoutProbeDeniedError):
        await svc.probe(ScoutProbeTargetV1(query="payment"))
