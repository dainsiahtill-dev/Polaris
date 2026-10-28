"""Tests for ScoutProbeService (UTF-8)."""

import asyncio
from collections.abc import Mapping
from typing import Any

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


# ── the authority is an immutable snapshot of the host's committed facts ──


def test_the_authority_does_not_share_the_callers_mutable_scope_list() -> None:
    """The host's scope list must not stay shared with the authority.

    Root's counterexample: the authority's ``__post_init__`` shallow-copied
    the token, so the nested ``allowed_read_paths`` list stayed shared with
    the caller. The caller emptied it afterwards, the authority's committed
    scope silently became empty, and the authority then contradicted the
    report that had been cached under the wider scope.
    """
    from polaris.cells.roles.scout.public.service import ScoutReadAuthorityV1

    scope = ["allowed"]
    authority = ScoutReadAuthorityV1(
        capability_token={"allowed_read_paths": scope},
        read_roots=("allowed",),
    )

    scope.clear()

    assert list(authority.capability_token["allowed_read_paths"]) == ["allowed"], (
        "the authority's committed scope was changed by the caller"
    )


def test_the_authority_token_cannot_be_mutated_through_any_common_operation() -> None:
    """The committed token must reject every mutation path, including ``|=``.

    Root found that ``token |= {"allowed_read_paths": []}`` still emptied the
    scope: CPython implements ``dict.__ior__`` and ``dict.__setitem__`` at C
    level, so a ``dict`` subclass cannot block them and any subclass override
    would be a false immutability promise. The token is therefore a
    read-only mapping, which rejects them at every level.
    """
    from polaris.cells.roles.scout.public.service import ScoutReadAuthorityV1

    authority = ScoutReadAuthorityV1(
        capability_token={"allowed_read_paths": ["allowed"]},
        read_roots=("allowed",),
    )
    token: Any = authority.capability_token

    with pytest.raises(TypeError):
        token |= {"allowed_read_paths": []}
    with pytest.raises(TypeError):
        token["allowed_read_paths"] = []
    with pytest.raises(TypeError):
        del token["allowed_read_paths"]
    with pytest.raises(AttributeError):
        token.update({"allowed_read_paths": []})
    with pytest.raises(AttributeError):
        token.pop("allowed_read_paths", None)
    with pytest.raises(AttributeError):
        token.clear()
    with pytest.raises(AttributeError):
        token["allowed_read_paths"].append("other")

    assert list(authority.capability_token["allowed_read_paths"]) == ["allowed"], (
        "the committed scope was emptied through a mutation path"
    )
    assert list(authority.capability_token["allowed_read_paths"]) == ["allowed"]


def test_the_authority_token_stays_json_serialisable_and_plain_copyable() -> None:
    """Immutability must not break the consumers that carry the token around."""
    import json

    from polaris.cells.roles.scout.public.service import ScoutReadAuthorityV1

    authority = ScoutReadAuthorityV1(
        capability_token={"allowed_read_paths": ["allowed"], "token_id": "t-1"},
        read_roots=("allowed",),
    )

    payload = json.loads(json.dumps(dict(authority.capability_token)))
    assert payload["allowed_read_paths"] == ["allowed"]
    assert payload["token_id"] == "t-1"

    plain = dict(authority.capability_token)
    assert plain["allowed_read_paths"] == ("allowed",)


# ── the frozen token must not leak a reference to the caller's mutable state ──


def test_a_mapping_proxy_input_is_unwrapped_by_value_not_by_reference() -> None:
    """A ``MappingProxyType`` input must not keep the caller's mutable state alive.

    Root's live counterexample
    (``Root-scout-proxy-live-effect-1793049323908352228/result.json``): the
    candidate adopted the proxy as-is, so the caller kept the backing dict
    and could still append to the committed scope list afterwards. The
    authority then widened without the host ever re-committing it.
    """
    from types import MappingProxyType

    from polaris.cells.roles.scout.public.service import ScoutReadAuthorityV1

    backing: dict[str, Any] = {"allowed_read_paths": ["allowed"]}
    authority = ScoutReadAuthorityV1(
        capability_token=MappingProxyType(backing),
        read_roots=("allowed",),
    )

    backing["allowed_read_paths"].append("private")

    assert list(authority.capability_token["allowed_read_paths"]) == ["allowed"], (
        "the caller widened the committed scope through the proxy's backing dict"
    )


def test_a_nested_row_in_the_committed_token_cannot_be_edited_in_place() -> None:
    """A nested mapping row must be frozen by value too, not shared.

    The host commits rows such as ``verification_command_scope``; a row that
    stayed shared with the caller could be re-pointed at another directory
    after the fact.
    """
    from polaris.cells.roles.scout.public.service import ScoutReadAuthorityV1

    row = {"command": "pytest", "cwd": "."}
    authority = ScoutReadAuthorityV1(
        capability_token={"allowed_read_paths": ["allowed"], "verification_command_scope": [row]},
        read_roots=("allowed",),
    )

    row["cwd"] = "private"

    assert authority.capability_token["verification_command_scope"][0]["cwd"] == ".", (
        "the caller re-pointed a committed row through the shared reference"
    )


def test_the_committed_token_survives_every_c_level_dict_bypass() -> None:
    """The frozen token must not be a ``dict`` that C-level code can write into.

    Root rejected a ``dict`` subclass on this ground: CPython implements
    ``dict.__setitem__`` and ``dict.__ior__`` at C level, so
    ``dict.__setitem__(token, key, value)`` writes straight into the concrete
    storage and bypasses any Python-level override. The committed scope must
    survive that bypass.
    """
    from polaris.cells.roles.scout.public.service import ScoutReadAuthorityV1

    authority = ScoutReadAuthorityV1(
        capability_token={"allowed_read_paths": ["allowed"]},
        read_roots=("allowed",),
    )
    token: Any = authority.capability_token

    with pytest.raises(TypeError):
        dict.__setitem__(token, "allowed_read_paths", ["allowed", "private"])
    with pytest.raises(TypeError):
        dict.__ior__(token, {"allowed_read_paths": ["allowed", "private"]})

    assert list(authority.capability_token["allowed_read_paths"]) == ["allowed"], (
        "a C-level dict bypass widened the committed scope"
    )


def test_the_committed_token_cannot_have_its_payload_reassigned() -> None:
    """The frozen token must not expose any attribute a holder can rewrite.

    The current snapshot stores its data in ``_payload``. Replacing that
    attribute must not change the committed scope.
    """
    from types import MappingProxyType

    from polaris.cells.roles.scout.public.service import ScoutReadAuthorityV1

    authority = ScoutReadAuthorityV1(
        capability_token={"allowed_read_paths": ["allowed"]},
        read_roots=("allowed",),
    )
    token: Any = authority.capability_token

    with pytest.raises((AttributeError, TypeError)):
        token._payload = MappingProxyType({"allowed_read_paths": ("private",)})
    with pytest.raises((AttributeError, TypeError)):
        token.__dict__ = {}

    assert list(authority.capability_token["allowed_read_paths"]) == ["allowed"], (
        "the committed scope was replaced through a reassignable attribute"
    )


def test_a_mutated_read_copy_cannot_change_the_committed_authority() -> None:
    """What a consumer reads must not be a handle onto the committed facts.

    A read hands out a detached copy, so a consumer that mutates what it
    received cannot change the authority underneath the service.
    """
    from polaris.cells.roles.scout.public.service import ScoutReadAuthorityV1

    authority = ScoutReadAuthorityV1(
        capability_token={
            "allowed_read_paths": ["allowed"],
            "verification_command_scope": [{"command": "pytest", "cwd": "."}],
        },
        read_roots=("allowed",),
    )

    rows = authority.capability_token["verification_command_scope"]
    rows[0]["cwd"] = "private"

    assert authority.capability_token["verification_command_scope"][0]["cwd"] == ".", (
        "the committed row changed through the copy a consumer read"
    )


def test_the_committed_token_with_nested_rows_stays_json_serialisable() -> None:
    """A JSON-shaped token must still encode, nested rows included.

    The previous candidate froze mappings as ``MappingProxyType``, which is
    not JSON-serialisable, so ``json.dumps(dict(token))`` raised
    ``TypeError``. The token's documented contract is that it encodes.
    """
    import json

    from polaris.cells.roles.scout.public.service import ScoutReadAuthorityV1

    authority = ScoutReadAuthorityV1(
        capability_token={
            "allowed_read_paths": ["allowed"],
            "token_id": "t-1",
            "verification_command_scope": [{"command": "pytest", "cwd": "."}],
        },
        read_roots=("allowed",),
    )

    payload = json.loads(json.dumps(dict(authority.capability_token)))
    assert payload["allowed_read_paths"] == ["allowed"]
    assert payload["token_id"] == "t-1"
    assert payload["verification_command_scope"] == [{"command": "pytest", "cwd": "."}]


@pytest.mark.asyncio
async def test_a_caller_that_mutates_its_scope_list_cannot_desynchronize_the_cache() -> None:
    """A caller-side scope mutation must not let a stale report be served.

    The authority is a snapshot: the caller's later mutation of the list it
    passed in cannot narrow the authority behind the service's back. The
    cached report therefore still matches the authority that produced it, and
    the probe keeps returning the content the authority actually authorizes.
    """
    from polaris.cells.roles.scout.internal.cache import TTLCache
    from polaris.cells.roles.scout.public.service import ScoutReadAuthorityV1

    shared_cache: TTLCache[ScoutReportV1] = TTLCache(ttl_seconds=60.0)
    tool = _CountingReadTool("alpha/a.py")
    scope = ["alpha"]

    authority = ScoutReadAuthorityV1(
        capability_token={"allowed_read_paths": scope},
        read_roots=("alpha",),
    )
    svc = ScoutProbeService(
        read_tool=tool,
        cache=shared_cache,
        workspace="/workspace-a",
        authority=authority,
    )

    first = await svc.probe(ScoutProbeTargetV1(query="payment"))
    scope.clear()
    second = await svc.probe(ScoutProbeTargetV1(query="payment"))

    assert first.cache_hit is False
    assert second.cache_hit is True
    assert second.findings[0].path == "alpha/a.py"
    assert list(authority.capability_token["allowed_read_paths"]) == ["alpha"]


# ── the cache must follow the host's CURRENT tool decisions ──


def test_a_host_that_forbids_a_read_tool_invalidates_the_cached_report() -> None:
    """A host blacklist change must not be served from the cache.

    The host's tool policy is not part of the committed token: the host can
    forbid a read tool at any moment. A report produced while the tool was
    allowed must not be served once the host forbids it, so the host's OWN
    per-tool decision is bound into the cache key.
    """
    from polaris.cells.roles.scout.internal.cache import TTLCache
    from polaris.cells.roles.scout.public.service import ScoutReadAuthorityV1

    shared_cache: TTLCache[ScoutReportV1] = TTLCache(ttl_seconds=60.0)
    tool = _CountingReadTool("alpha/a.py")
    blacklisted: set[str] = set()

    def authorizer(tool_name: str, _args: Mapping[str, Any]) -> tuple[bool, str]:
        if tool_name in blacklisted:
            return False, f"tool '{tool_name}' is in the role blacklist"
        return True, ""

    authority = ScoutReadAuthorityV1(
        capability_token={"allowed_read_paths": ["alpha"]},
        read_roots=("alpha",),
        tool_authorizer=authorizer,
    )
    svc = ScoutProbeService(
        read_tool=tool,
        cache=shared_cache,
        workspace="/workspace-a",
        authority=authority,
    )

    first = asyncio.run(svc.probe(ScoutProbeTargetV1(query="payment")))
    assert first.cache_hit is False

    # The host forbids a read tool AFTER the report was cached.
    blacklisted.add("repo_rg")
    calls_before = len(tool.calls)
    second = asyncio.run(svc.probe(ScoutProbeTargetV1(query="payment")))

    assert second.cache_hit is False, "the stale report was served after the host forbade a read tool"
    assert len(tool.calls) > calls_before, "the forbidden probe did not re-read through the host's gate"


def test_a_stable_legal_authority_still_hits_the_shared_cache() -> None:
    """The legal case must keep working: a stable authority hits the cache.

    The cache must not be disabled wholesale. With an unchanged authority the
    second probe is served from the cache and does NOT read again.
    """
    from polaris.cells.roles.scout.internal.cache import TTLCache
    from polaris.cells.roles.scout.public.service import ScoutReadAuthorityV1

    shared_cache: TTLCache[ScoutReportV1] = TTLCache(ttl_seconds=60.0)
    tool = _CountingReadTool("alpha/a.py")

    def authorizer(_tool_name: str, _args: Mapping[str, Any]) -> tuple[bool, str]:
        return True, ""

    authority = ScoutReadAuthorityV1(
        capability_token={"allowed_read_paths": ["alpha"]},
        read_roots=("alpha",),
        tool_authorizer=authorizer,
    )
    svc = ScoutProbeService(
        read_tool=tool,
        cache=shared_cache,
        workspace="/workspace-a",
        authority=authority,
    )

    first = asyncio.run(svc.probe(ScoutProbeTargetV1(query="payment")))
    calls_after_first = len(tool.calls)
    second = asyncio.run(svc.probe(ScoutProbeTargetV1(query="payment")))

    assert first.cache_hit is False
    assert second.cache_hit is True, "the legal, stable authority lost its cache hit"
    assert len(tool.calls) == calls_after_first, "the cached probe read again"
