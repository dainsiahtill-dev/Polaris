"""Scout read-scope authority: the parent executor's scope must reach the reads.

Synthetic disposable workspace only (a temp dir per test). No real project,
no Bench target, no paid model call. The LLM is never involved: every test
drives the REAL ``AgentAccelToolExecutor`` -> ``scout_probe`` handler ->
``roles.scout`` cell -> REAL read tools.

The defect these tests pin (Root's counterexample,
``../Root-scout-executor-effects/scout-scope-result.json``): the parent
executor held ``capability_token={'allowed_read_paths': ['allowed']}``, its
direct ``repo_rg`` on ``private`` was refused by the canonical gate, yet the
SAME parent's ``scout_probe`` returned ``private/hidden.py`` and persisted it
into the scout anchor store. Root cause: the Scout child executor was built
without the caller's scope, and a missing scope key means the legacy
"undefined scope" (= whole workspace).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from polaris.kernelone.llm.toolkit import AgentAccelToolExecutor

ALLOWED_CANARY = "SCOUT_ALLOWED_CANARY_9f31"
SECOND_CANARY = "SCOUT_SECOND_CANARY_4b77"
PRIVATE_CANARY = "SCOUT_PRIVATE_CANARY_6da9"


def _build_workspace(root: Path) -> Path:
    """Create a synthetic workspace with two authorized roots and one private one."""
    workspace = root / "scope-workspace"
    for name in ("alpha", "beta", "private"):
        (workspace / name).mkdir(parents=True, exist_ok=True)
    (workspace / "alpha" / "a.py").write_text(f"def alpha_symbol():\n    return '{ALLOWED_CANARY}'\n", encoding="utf-8")
    (workspace / "beta" / "b.py").write_text(f"def beta_symbol():\n    return '{SECOND_CANARY}'\n", encoding="utf-8")
    (workspace / "private" / "hidden.py").write_text(f'SECRET = "{PRIVATE_CANARY}"\n', encoding="utf-8")
    return workspace


def _probe(parent: AgentAccelToolExecutor, query: str) -> dict[str, Any]:
    """Run scout_probe on the real parent executor and flatten the result.

    A refused probe returns no ``result`` payload at all — the canonical gate
    refuses ``scout_probe`` itself when the committed scope is invalid, and
    the handler refuses it when every read is denied. Both are real denials
    and must be observable, so the helper reports them instead of asserting
    a payload shape.
    """
    outcome = parent.execute("scout_probe", {"query": query})
    payload = outcome.get("result")
    if not isinstance(payload, dict):
        return {
            "ok": bool(outcome.get("ok")),
            "error": outcome.get("error"),
            "stdout": "",
            "paths": [],
            "snippets": [],
            "coverage": {},
        }
    findings = payload.get("findings")
    rows = [f for f in findings or [] if isinstance(f, dict)]
    return {
        "ok": bool(outcome.get("ok")),
        "error": outcome.get("error"),
        "stdout": str(payload.get("stdout") or ""),
        "paths": [str(f.get("path") or "") for f in rows],
        "snippets": [str(f.get("snippet") or "") for f in rows],
        "coverage": payload.get("coverage") or {},
    }


def _anchors(workspace: Path) -> list[dict[str, Any]]:
    """Read the persisted scout anchor store for this workspace."""
    path = workspace / ".polaris" / "runtime" / "scout_anchors.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    anchors = data.get("anchors") if isinstance(data, dict) else None
    return [a for a in anchors or [] if isinstance(a, dict)]


# ── the defect: the parent's scope must not be dropped on the way to the reads ──


def test_scout_probe_does_not_read_outside_the_parent_read_scope(tmp_path: Path) -> None:
    """A private path must not appear in the scout findings nor in the anchors.

    This is the exact shape of Root's counterexample: the parent executor's
    scope allows only ``alpha``; the private file must stay invisible.

    The probe queries the shared term ``CANARY`` (present in both the
    authorized and the private file) so the assertion is about CONTENT that
    was read from an unauthorized file — the summary header merely echoes the
    caller's own query and is not a leak.
    """
    workspace = _build_workspace(tmp_path)
    parent = AgentAccelToolExecutor(str(workspace), capability_token={"allowed_read_paths": ["alpha"]})
    try:
        result = _probe(parent, "CANARY")
    finally:
        parent.close_sync()

    assert result["ok"] is True
    assert not any("private" in path for path in result["paths"]), (
        f"scout_probe leaked a private path: {result['paths']!r}"
    )
    assert not any(PRIVATE_CANARY in snippet for snippet in result["snippets"]), (
        f"scout_probe leaked content read from a private file: {result['snippets']!r}"
    )
    assert not any("private" in str(a.get("path") or "") for a in _anchors(workspace)), (
        "the scout anchor store persisted an unauthorized private path"
    )


def test_direct_read_and_scout_probe_agree_on_the_same_scope(tmp_path: Path) -> None:
    """The gate must reject the private path directly AND through the scout.

    Before the fix the direct call was refused while the scout call returned
    the very same file, so the two disagreed about one scope.
    """
    workspace = _build_workspace(tmp_path)
    parent = AgentAccelToolExecutor(str(workspace), capability_token={"allowed_read_paths": ["alpha"]})
    try:
        direct = parent.execute("repo_rg", {"pattern": PRIVATE_CANARY, "path": "private"})
        probe = _probe(parent, PRIVATE_CANARY)
    finally:
        parent.close_sync()

    assert direct.get("ok") is False, "the canonical gate must refuse the private read"
    assert not any("private" in path for path in probe["paths"]), (
        "the scout path must be refused by the same gate as the direct path"
    )


# ── legal progress must survive the fix ──


def test_scout_probe_still_finds_content_inside_the_allowed_root(tmp_path: Path) -> None:
    """A canary inside the authorized root is still found."""
    workspace = _build_workspace(tmp_path)
    parent = AgentAccelToolExecutor(str(workspace), capability_token={"allowed_read_paths": ["alpha"]})
    try:
        result = _probe(parent, ALLOWED_CANARY)
    finally:
        parent.close_sync()

    assert result["ok"] is True
    assert any(path.endswith("alpha/a.py") for path in result["paths"]), (
        f"the authorized root was not searched: {result!r}"
    )
    assert ALLOWED_CANARY in result["stdout"], f"the authorized canary was not reported: {result['stdout']!r}"


def test_scout_probe_searches_every_authorized_root(tmp_path: Path) -> None:
    """Two authorized roots must both be searched, not only the first one."""
    workspace = _build_workspace(tmp_path)
    parent = AgentAccelToolExecutor(str(workspace), capability_token={"allowed_read_paths": ["alpha", "beta"]})
    try:
        result = _probe(parent, "CANARY")
    finally:
        parent.close_sync()

    assert result["ok"] is True
    paths = result["paths"]
    assert any(path.endswith("alpha/a.py") for path in paths), f"alpha missing: {paths!r}"
    assert any(path.endswith("beta/b.py") for path in paths), f"beta missing: {paths!r}"
    assert not any("private" in path for path in paths), f"private leaked: {paths!r}"


def test_scout_probe_reads_a_single_authorized_file(tmp_path: Path) -> None:
    """A scope of one FILE keeps that file readable and stays inside it."""
    workspace = _build_workspace(tmp_path)
    parent = AgentAccelToolExecutor(str(workspace), capability_token={"allowed_read_paths": ["alpha/a.py"]})
    try:
        result = _probe(parent, ALLOWED_CANARY)
    finally:
        parent.close_sync()

    assert result["ok"] is True
    assert any(path.endswith("alpha/a.py") for path in result["paths"]), (
        f"the single authorized file was not read: {result!r}"
    )
    assert not any("private" in path for path in result["paths"])


# ── explicit denials ──


def test_explicitly_empty_scope_is_refused(tmp_path: Path) -> None:
    """An explicitly empty scope must stay empty and refuse the probe."""
    workspace = _build_workspace(tmp_path)
    parent = AgentAccelToolExecutor(str(workspace), capability_token={"allowed_read_paths": []})
    try:
        result = _probe(parent, ALLOWED_CANARY)
    finally:
        parent.close_sync()

    assert result["ok"] is False
    assert result["paths"] == []
    assert not any("private" in str(a.get("path") or "") for a in _anchors(workspace))


def test_scout_probe_refuses_when_the_host_forbids_every_read_tool(tmp_path: Path) -> None:
    """A host that allows only ``scout_probe`` must not gain read tools.

    The Scout composite may not widen the host's tool whitelist: the reads
    must be refused with the concrete reason, not silently succeed.
    """
    workspace = _build_workspace(tmp_path)
    parent = AgentAccelToolExecutor(
        str(workspace),
        allowed_tools=frozenset({"scout_probe"}),
        capability_token={"allowed_read_paths": ["alpha"]},
    )
    try:
        result = _probe(parent, ALLOWED_CANARY)
    finally:
        parent.close_sync()

    assert result["ok"] is False
    assert result["paths"] == []
    assert "not allowed" in str(result["error"])


def test_scout_probe_works_when_the_host_allows_the_read_tools(tmp_path: Path) -> None:
    """A host that grants the read tools makes real progress."""
    workspace = _build_workspace(tmp_path)
    parent = AgentAccelToolExecutor(
        str(workspace),
        allowed_tools=frozenset({"scout_probe", "repo_rg", "repo_tree", "repo_symbols_index"}),
        capability_token={"allowed_read_paths": ["alpha"]},
    )
    try:
        result = _probe(parent, ALLOWED_CANARY)
    finally:
        parent.close_sync()

    assert result["ok"] is True
    assert any(path.endswith("alpha/a.py") for path in result["paths"])


def test_a_model_supplied_path_cannot_widen_the_scope(tmp_path: Path) -> None:
    """Extra model arguments must not reach or widen the host scope.

    ``scout_probe`` declares only ``query`` and ``mode``; the executor drops
    undeclared arguments before dispatch, so a model cannot smuggle a path.
    """
    workspace = _build_workspace(tmp_path)
    parent = AgentAccelToolExecutor(str(workspace), capability_token={"allowed_read_paths": ["alpha"]})
    try:
        outcome = parent.execute(
            "scout_probe",
            {
                "query": PRIVATE_CANARY,
                "path": "private",
                "paths": ["private"],
                "allowed_read_paths": ["private"],
                "capability_token": {"allowed_read_paths": ["private"]},
            },
        )
    finally:
        parent.close_sync()

    payload = outcome.get("result")
    findings = payload.get("findings") if isinstance(payload, dict) else []
    paths = [str(f.get("path") or "") for f in findings or [] if isinstance(f, dict)]
    assert not any("private" in path for path in paths), f"a model-supplied argument widened the scope: {paths!r}"


# ── lifecycle ──


def test_the_host_executor_is_not_closed_by_the_scout_cell(tmp_path: Path) -> None:
    """A borrowed executor stays usable after the scout probe returned.

    The Scout cell borrows the host's executor; closing it would break the
    host's own turn.
    """
    workspace = _build_workspace(tmp_path)
    parent = AgentAccelToolExecutor(str(workspace), capability_token={"allowed_read_paths": ["alpha"]})
    try:
        _probe(parent, ALLOWED_CANARY)
        after = parent.execute("repo_rg", {"pattern": ALLOWED_CANARY, "path": "alpha"})
    finally:
        parent.close_sync()

    assert after.get("ok") is True, f"the host executor was unusable after the scout probe: {after!r}"


@pytest.mark.asyncio
async def test_scout_probe_works_inside_a_running_event_loop(tmp_path: Path) -> None:
    """The handler must work when an event loop is already running.

    The handler runs the async probe off-loop; this asserts the async call
    path works and keeps the scope enforced.
    """
    workspace = _build_workspace(tmp_path)
    parent = AgentAccelToolExecutor(str(workspace), capability_token={"allowed_read_paths": ["alpha"]})
    try:
        outcome = await asyncio.to_thread(parent.execute, "scout_probe", {"query": PRIVATE_CANARY})
    finally:
        parent.close_sync()

    payload = outcome.get("result")
    findings = payload.get("findings") if isinstance(payload, dict) else []
    paths = [str(f.get("path") or "") for f in findings or [] if isinstance(f, dict)]
    assert not any("private" in path for path in paths), f"the async path leaked a private path: {paths!r}"


def test_repeated_probes_do_not_leak_or_drift(tmp_path: Path) -> None:
    """Repeating the same probe keeps the same scope and stays authorized."""
    workspace = _build_workspace(tmp_path)
    parent = AgentAccelToolExecutor(str(workspace), capability_token={"allowed_read_paths": ["alpha"]})
    try:
        first = _probe(parent, ALLOWED_CANARY)
        second = _probe(parent, ALLOWED_CANARY)
    finally:
        parent.close_sync()

    assert first["ok"] is True
    assert second["ok"] is True
    assert not any("private" in path for path in first["paths"] + second["paths"])


# ── host tool policy: the nested reads follow the host's own decision ──


def test_a_nested_read_denied_by_the_host_policy_is_refused(tmp_path: Path) -> None:
    """The host's per-tool decision must gate every nested Scout read.

    The Scout composite expands ONE model-visible dispatch into SEVERAL
    executor calls that never re-enter the host's gateway. Those nested calls
    must therefore be authorized by the host itself, not by the executor's
    allow-list alone (which does not carry the host's blacklist).
    """
    workspace = _build_workspace(tmp_path)
    calls: list[str] = []

    def authorizer(tool: str, args: Mapping[str, Any]) -> tuple[bool, str]:
        calls.append(tool)
        if tool == "repo_rg":
            return False, "tool 'repo_rg' is in the role blacklist"
        return True, ""

    parent = AgentAccelToolExecutor(
        str(workspace),
        allowed_tools=frozenset({"scout_probe", "repo_rg", "repo_tree", "repo_symbols_index"}),
        capability_token={"allowed_read_paths": ["alpha"]},
    )
    try:
        from polaris.cells.roles.scout.public.service import ScoutReadAuthorityV1

        authority = ScoutReadAuthorityV1(
            capability_token={"allowed_read_paths": ["alpha"]},
            allowed_tools=frozenset({"scout_probe", "repo_rg", "repo_tree", "repo_symbols_index"}),
            read_roots=("alpha",),
            borrowed_executor=parent,
            tool_authorizer=authorizer,
        )
        from polaris.cells.roles.scout.internal.read_tool_adapter import RegistryReadTool
        from polaris.cells.roles.scout.public.contracts import ScoutProbeTargetV1
        from polaris.cells.roles.scout.public.service import ScoutProbeService

        svc = ScoutProbeService(
            read_tool=RegistryReadTool(workspace=str(workspace), authority=authority),
            workspace=str(workspace),
            authority=authority,
        )
        report = asyncio.run(svc.probe(ScoutProbeTargetV1(query="CANARY")))
    finally:
        parent.close_sync()

    assert "repo_rg" in calls, "the nested read never consulted the host policy"
    assert any("repo_rg" in str(err) for err in report.coverage["errors"]), (
        f"the host denial was not preserved: {report.coverage['errors']!r}"
    )
    assert "blacklist" in " ".join(str(err) for err in report.coverage["errors"]), (
        "the host's own denial reason was lost"
    )
    # The other read tools still ran: a partial denial keeps real results.
    assert report.coverage["successful_calls"] >= 1


def test_a_legal_nested_read_is_not_blocked_by_the_host_policy(tmp_path: Path) -> None:
    """The host policy seam must not block a legal read."""
    workspace = _build_workspace(tmp_path)
    calls: list[str] = []

    def authorizer(tool: str, args: Mapping[str, Any]) -> tuple[bool, str]:
        calls.append(tool)
        return True, ""

    parent = AgentAccelToolExecutor(
        str(workspace),
        allowed_tools=frozenset({"scout_probe", "repo_rg", "repo_tree", "repo_symbols_index"}),
        capability_token={"allowed_read_paths": ["alpha"]},
    )
    try:
        from polaris.cells.roles.scout.internal.read_tool_adapter import RegistryReadTool
        from polaris.cells.roles.scout.public.contracts import ScoutProbeTargetV1
        from polaris.cells.roles.scout.public.service import ScoutProbeService, ScoutReadAuthorityV1

        authority = ScoutReadAuthorityV1(
            capability_token={"allowed_read_paths": ["alpha"]},
            allowed_tools=frozenset({"scout_probe", "repo_rg", "repo_tree", "repo_symbols_index"}),
            read_roots=("alpha",),
            borrowed_executor=parent,
            tool_authorizer=authorizer,
        )
        svc = ScoutProbeService(
            read_tool=RegistryReadTool(workspace=str(workspace), authority=authority),
            workspace=str(workspace),
            authority=authority,
        )
        report = asyncio.run(svc.probe(ScoutProbeTargetV1(query="CANARY")))
    finally:
        parent.close_sync()

    assert "repo_rg" in calls
    assert report.coverage["errors"] == []
    assert any(path.endswith("alpha/a.py") for path in (f.path for f in report.findings))


# ── cache: a stale snapshot must not be promoted to the current permission ──


def test_a_cached_report_is_not_served_after_the_parent_scope_shrinks(tmp_path: Path) -> None:
    """A borrowed executor without a live authority reader must not serve a cache hit.

    The executor's own gate is what actually authorizes the reads, and it can
    change underneath us. Without an ``authority_reader`` the Scout cell
    cannot re-observe that state, so an old snapshot must never be promoted
    to the current permission: the probe re-reads through the borrowed
    executor, which applies its CURRENT gate and refuses.
    """
    workspace = _build_workspace(tmp_path)
    parent = AgentAccelToolExecutor(str(workspace), capability_token={"allowed_read_paths": ["alpha"]})
    try:
        first = _probe(parent, ALLOWED_CANARY)
        assert first["ok"] is True

        # The parent's scope shrinks to nothing (a state change of the host).
        parent._capability_token["allowed_read_paths"] = []  # deliberate simulation of a host scope change

        direct = parent.execute("repo_rg", {"pattern": ALLOWED_CANARY, "path": "alpha"})
        assert direct.get("ok") is False, "the parent's own gate must refuse the read"

        second = _probe(parent, ALLOWED_CANARY)
        assert second["ok"] is False, "the stale cached report was served after the parent's scope shrank"
        assert second["paths"] == []
    finally:
        parent.close_sync()


def test_a_legal_read_still_proceeds_through_a_borrowed_executor(tmp_path: Path) -> None:
    """The cache refusal must not cost a legal read its result."""
    workspace = _build_workspace(tmp_path)
    parent = AgentAccelToolExecutor(str(workspace), capability_token={"allowed_read_paths": ["alpha"]})
    try:
        result = _probe(parent, ALLOWED_CANARY)
    finally:
        parent.close_sync()

    assert result["ok"] is True
    assert any(path.endswith("alpha/a.py") for path in result["paths"]), (
        f"the authorized root was not searched: {result!r}"
    )
    assert ALLOWED_CANARY in result["stdout"]


# ── the committed scope is an immutable snapshot, not a shared list ──


def test_the_authority_does_not_share_the_callers_scope_list(tmp_path: Path) -> None:
    """A caller that empties its scope list must not narrow the authority.

    Root's counterexample: the authority shallow-copied the token, so the
    nested ``allowed_read_paths`` list stayed shared with the caller. The
    caller emptied it afterwards and the authority's committed scope silently
    became empty — while the report cached under the wider scope was still
    served.
    """
    from polaris.cells.roles.scout.public.service import ScoutReadAuthorityV1

    scope = ["alpha"]
    authority = ScoutReadAuthorityV1(
        capability_token={"allowed_read_paths": scope},
        read_roots=("alpha",),
    )

    scope.clear()

    assert list(authority.capability_token["allowed_read_paths"]) == ["alpha"], (
        "the caller emptied the authority's committed scope"
    )


def test_the_authority_token_rejects_every_mutation_path(tmp_path: Path) -> None:
    """The committed token must reject every mutation, ``|=`` included.

    Root found that ``token |= {"allowed_read_paths": []}`` still emptied the
    scope, because CPython implements ``dict.__ior__`` at C level and a
    ``dict`` subclass cannot block it. The token is a read-only mapping
    instead, which rejects the operation at every level.
    """
    from polaris.cells.roles.scout.public.service import ScoutReadAuthorityV1

    authority = ScoutReadAuthorityV1(
        capability_token={"allowed_read_paths": ["alpha"]},
        read_roots=("alpha",),
    )
    token: Any = authority.capability_token

    with pytest.raises(TypeError):
        token |= {"allowed_read_paths": []}
    with pytest.raises(TypeError):
        token["allowed_read_paths"] = []
    with pytest.raises(AttributeError):
        token.update({"allowed_read_paths": []})
    with pytest.raises(AttributeError):
        token.clear()
    with pytest.raises(AttributeError):
        token["allowed_read_paths"].append("beta")

    assert list(authority.capability_token["allowed_read_paths"]) == ["alpha"], (
        "the committed scope was emptied through a mutation path"
    )


def test_a_stable_legal_probe_hits_the_cache_and_does_not_read_again(tmp_path: Path) -> None:
    """The legal case must keep its cache hit and must not re-read.

    The cache must not be disabled wholesale: with an unchanged authority the
    second probe is served from the cache and performs no further read.
    """
    from polaris.cells.roles.scout.internal.cache import TTLCache
    from polaris.cells.roles.scout.internal.read_tool_adapter import RegistryReadTool
    from polaris.cells.roles.scout.public.contracts import ScoutProbeTargetV1, ScoutReportV1
    from polaris.cells.roles.scout.public.service import ScoutProbeService, ScoutReadAuthorityV1

    workspace = _build_workspace(tmp_path)
    parent = AgentAccelToolExecutor(str(workspace), capability_token={"allowed_read_paths": ["alpha"]})
    shared_cache: TTLCache[ScoutReportV1] = TTLCache(ttl_seconds=60.0)

    def authorizer(_tool_name: str, _args: Mapping[str, Any]) -> tuple[bool, str]:
        return True, ""

    def reader() -> ScoutReadAuthorityV1:
        return ScoutReadAuthorityV1(
            capability_token={"allowed_read_paths": ["alpha"]},
            allowed_tools=frozenset({"scout_probe", "repo_rg", "repo_tree", "repo_symbols_index"}),
            read_roots=("alpha",),
            borrowed_executor=parent,
            tool_authorizer=authorizer,
        )

    authority = ScoutReadAuthorityV1(
        capability_token={"allowed_read_paths": ["alpha"]},
        allowed_tools=frozenset({"scout_probe", "repo_rg", "repo_tree", "repo_symbols_index"}),
        read_roots=("alpha",),
        borrowed_executor=parent,
        authority_reader=reader,
        tool_authorizer=authorizer,
    )
    svc = ScoutProbeService(
        read_tool=RegistryReadTool(workspace=str(workspace), authority=authority),
        cache=shared_cache,
        workspace=str(workspace),
        authority=authority,
    )
    try:
        first = asyncio.run(svc.probe(ScoutProbeTargetV1(query="CANARY")))
        second = asyncio.run(svc.probe(ScoutProbeTargetV1(query="CANARY")))
    finally:
        svc.close()
        parent.close_sync()

    assert first.cache_hit is False
    assert second.cache_hit is True, "the legal, stable authority lost its cache hit"
    assert any(path.endswith("alpha/a.py") for path in (f.path for f in second.findings))


# ── the committed token must not leak a reference to the caller's state ──


def test_a_caller_mutation_after_the_probe_cannot_widen_the_real_read(tmp_path: Path) -> None:
    """A caller that widens its own scope list after the probe must not widen the reads.

    This is the REAL read path — the canonical executor, the scout cell and
    the real read tools — with the host committing its scope through a
    ``MappingProxyType``-wrapped token, the shape Root's live counterexample
    used (``Root-scout-proxy-live-effect-1793049323908352228/result.json``).

    Both the BEFORE and the AFTER probe are REAL reads: the test performs an
    actual Scout probe before the mutation and another one after it. A
    previous version of this test probed only BEFORE the mutation and merely
    asserted the object afterwards, which proved nothing about the reads.

    The committed scope is deliberately NARROWER than the declared
    ``read_roots`` (the host searched a wider area but committed only
    ``alpha``), so a scope expansion is observable in the findings: the
    private root must stay unreadable even though the plan would have
    searched it.

    Scope of the invariant: the authority is a SNAPSHOT of the token at the
    moment it was built. A token built LATER from a backing dict the caller
    has since mutated legitimately reports the new value — the snapshot
    protects what was committed, it does not freeze the host's own state.
    """
    from types import MappingProxyType

    from polaris.cells.roles.scout.internal.read_tool_adapter import RegistryReadTool
    from polaris.cells.roles.scout.public.contracts import ScoutProbeTargetV1
    from polaris.cells.roles.scout.public.service import ScoutProbeService, ScoutReadAuthorityV1

    workspace = _build_workspace(tmp_path)
    backing: dict[str, Any] = {"allowed_read_paths": ["alpha"]}
    proxy = MappingProxyType(backing)

    # The authority is a SNAPSHOT of what the host committed at this moment.
    authority = ScoutReadAuthorityV1(
        capability_token=proxy,
        read_roots=("alpha", "private"),
    )

    def owned_probe() -> tuple[dict[str, Any], bool]:
        """Run one real Scout probe under the committed scope.

        The Scout cell builds its OWN executor from the committed token, so
        the probe is a real read governed by the COMMITTED scope and not by
        the host's live one.

        Returns the flattened result and whether the owned executor was
        released afterwards (resource release is part of the contract).
        """
        read_tool = RegistryReadTool(workspace=str(workspace), authority=authority)
        service = ScoutProbeService(
            read_tool=read_tool,
            workspace=str(workspace),
            authority=authority,
        )
        try:
            report = asyncio.run(service.probe(ScoutProbeTargetV1(query="CANARY")))
            # Capture the owned executor BEFORE the close: closing the
            # service releases it and drops the adapter's reference.
            owned_executor = read_tool._executor
        finally:
            service.close()
        released = bool(getattr(owned_executor, "_closed", False))
        return (
            {
                "cache_hit": report.cache_hit,
                "paths": [f.path for f in report.findings],
                "snippets": [f.snippet for f in report.findings],
                "coverage": report.coverage,
            },
            released,
        )

    # BEFORE the mutation: a real, uncached probe under the committed scope.
    before, before_released = owned_probe()
    assert before["cache_hit"] is False, "the first probe must be a real read, not a cache hit"
    assert before["coverage"].get("successful_calls", 0) >= 1, f"no read tool actually ran: {before['coverage']!r}"
    assert any(path.endswith("alpha/a.py") for path in before["paths"]), (
        f"the authorized root was not searched: {before!r}"
    )
    assert not any("private" in path for path in before["paths"]), (
        f"the probe leaked a private path: {before['paths']!r}"
    )
    assert not any(PRIVATE_CANARY in snippet for snippet in before["snippets"]), (
        f"the probe leaked content read from a private file: {before['snippets']!r}"
    )
    assert before_released is True, "the owned executor was not released after the probe"

    # The host's own backing dict is mutated AFTER the authority was taken.
    backing["allowed_read_paths"].append("private")

    # The authority already taken must be unaffected: it committed "alpha".
    assert list(authority.capability_token["allowed_read_paths"]) == ["alpha"], (
        "the committed scope was widened through the caller's backing dict"
    )

    # AFTER the mutation: another REAL probe under the SAME committed
    # authority. It must not be served from a cache and it must not read the
    # private root the caller just added to its own list.
    after, after_released = owned_probe()
    assert after["cache_hit"] is False, "the post-mutation probe was served from a cache"
    assert any(path.endswith("alpha/a.py") for path in after["paths"]), (
        f"the authorized root was not searched after the mutation: {after!r}"
    )
    assert not any("private" in path for path in after["paths"]), (
        f"the caller's mutation widened the committed read scope: {after['paths']!r}"
    )
    assert not any(PRIVATE_CANARY in snippet for snippet in after["snippets"]), (
        f"the caller's mutation leaked private content: {after['snippets']!r}"
    )
    assert after_released is True, "the owned executor was not released after the probe"

    # The private root was genuinely refused, not merely absent from the
    # findings: the canonical gate must have rejected the reads that pointed
    # outside the committed scope.
    errors = [str(item) for item in after["coverage"].get("errors", [])]
    assert any("committed read scope" in error for error in errors), (
        f"the private root was not refused by the committed read scope: {errors!r}"
    )


def test_an_explicitly_empty_committed_scope_stays_empty_in_the_real_path(tmp_path: Path) -> None:
    """An explicitly empty committed scope must stay empty and refuse the reads."""
    from types import MappingProxyType

    from polaris.cells.roles.scout.public.service import ScoutReadAuthorityV1

    workspace = _build_workspace(tmp_path)
    backing: dict[str, Any] = {"allowed_read_paths": []}
    proxy = MappingProxyType(backing)

    authority = ScoutReadAuthorityV1(capability_token=proxy, read_roots=())
    assert authority.scope_committed is True
    assert list(authority.capability_token["allowed_read_paths"]) == []

    parent = AgentAccelToolExecutor(str(workspace), capability_token=dict(proxy))
    try:
        result = _probe(parent, ALLOWED_CANARY)
    finally:
        parent.close_sync()

    assert result["ok"] is False
    assert result["paths"] == []
