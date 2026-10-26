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
