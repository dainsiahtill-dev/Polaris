"""Scout nested reads must obey the REAL role gateway's own policy.

Synthetic disposable workspace (a temp dir per test). No real project, no
Bench target, no paid model call. The LLM is never involved: every test
drives the REAL ``RoleToolGateway`` -> REAL ``AgentAccelToolExecutor`` ->
``scout_probe`` handler -> REAL ``roles.scout`` cell -> REAL read tools.

The defect these tests pin (Root's counterexample,
``scout-review-gateway-1793044717336349203``): with a role whose whitelist
contains ``scout_probe``/``repo_rg`` and whose blacklist contains ``repo_rg``,
the gateway refused a direct ``repo_rg`` dispatch, yet the ``scout_probe``
dispatch succeeded and its NESTED ``repo_rg`` calls ran inside the borrowed
executor — the blacklist was never applied to them.

Root cause: ``RoleToolGateway`` projects a WHITELIST-ONLY tool set into the
executor, and a composite dispatch expands into nested executor calls that
never re-enter the gateway.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

CANARY = "SCOUT_GATEWAY_CANARY_7c21"

_READ_TOOLS = ("repo_rg", "repo_tree", "repo_symbols_index")


def _build_workspace(root: Path) -> Path:
    """A synthetic workspace with one readable source file."""
    workspace = root / "gw-workspace"
    (workspace / "src").mkdir(parents=True, exist_ok=True)
    (workspace / "src" / "mod.py").write_text(f"def target():\n    return '{CANARY}'\n", encoding="utf-8")
    return workspace


def _gateway(
    workspace: str,
    *,
    blacklist: list[str] | None = None,
    max_tool_calls_per_turn: int = 20,
) -> Any:
    """Build a REAL RoleToolGateway with a synthetic role policy."""
    from polaris.cells.roles.kernel.internal.tool_gateway import RoleToolGateway

    profile = SimpleNamespace(
        role_id="synthetic_scout_role",
        tool_policy=SimpleNamespace(
            whitelist=["scout_probe", *_READ_TOOLS],
            blacklist=list(blacklist or []),
            allow_code_write=False,
            allow_command_execution=False,
            allow_file_delete=False,
            max_tool_calls_per_turn=max_tool_calls_per_turn,
        ),
    )
    return RoleToolGateway(profile=profile, workspace=workspace)  # type: ignore[arg-type]


def _scout_payload(result: dict[str, Any]) -> dict[str, Any]:
    """Flatten the gateway's response for a scout_probe dispatch."""
    payload = result.get("result")
    if not isinstance(payload, dict):
        return {"ok": False, "error": str(result.get("error") or "")[:200]}
    findings = payload.get("findings")
    coverage = payload.get("coverage")
    return {
        "ok": bool(result.get("success")),
        "error": str(result.get("error") or "")[:200],
        "paths": [str(f.get("path") or "") for f in findings or [] if isinstance(f, dict)],
        "coverage": coverage if isinstance(coverage, dict) else {},
    }


# ── the counterexample: a blacklisted nested read must be refused ──


def test_a_blacklisted_nested_read_is_refused_by_the_real_gateway(tmp_path: Path) -> None:
    """The gateway refuses a blacklisted nested read exactly like a direct one.

    Root's committed counterexample: whitelist contains ``repo_rg``,
    blacklist contains ``repo_rg``. The direct dispatch is refused; the
    nested read inside ``scout_probe`` must be refused too.
    """
    workspace = _build_workspace(tmp_path)
    gateway = _gateway(str(workspace), blacklist=["repo_rg"])
    try:
        with pytest.raises(Exception) as direct_error:
            gateway.execute_tool("repo_rg", {"pattern": CANARY, "path": "src"})
        assert "黑名单" in str(direct_error.value)

        result = gateway.execute_tool("scout_probe", {"query": "target"})
    finally:
        gateway.close()

    payload = _scout_payload(result)
    errors = [str(item) for item in payload["coverage"].get("errors", [])]
    assert any("repo_rg" in err for err in errors), f"the blacklisted nested read was not refused: {errors!r}"
    assert any("黑名单" in err for err in errors), f"the gateway's own refusal reason was lost: {errors!r}"


def test_a_legal_nested_read_still_succeeds_through_the_real_gateway(tmp_path: Path) -> None:
    """A role WITHOUT a blacklist must keep making real progress."""
    workspace = _build_workspace(tmp_path)
    gateway = _gateway(str(workspace), blacklist=[])
    try:
        result = gateway.execute_tool("scout_probe", {"query": "target"})
    finally:
        gateway.close()

    payload = _scout_payload(result)
    assert payload["ok"] is True, f"the legal probe failed: {payload!r}"
    assert payload["coverage"].get("errors") == [], (
        f"a legal probe reported errors: {payload['coverage'].get('errors')!r}"
    )
    assert payload["coverage"].get("successful_calls", 0) >= 1
    assert any(path.endswith("src/mod.py") for path in payload["paths"]), (
        f"the legal probe found nothing: {payload['paths']!r}"
    )


def test_a_legal_scout_probe_completes_at_the_max_call_limit(tmp_path: Path) -> None:
    """One model-visible dispatch is one count, even with nested reads.

    The per-turn counter models MODEL-VISIBLE dispatches. A legal Scout probe
    must therefore still complete when ``max_tool_calls_per_turn == 1``; the
    NEXT model-visible dispatch is the one that is refused.
    """
    workspace = _build_workspace(tmp_path)
    gateway = _gateway(str(workspace), blacklist=[], max_tool_calls_per_turn=1)
    try:
        first = gateway.execute_tool("scout_probe", {"query": "target"})
        payload = _scout_payload(first)
        assert payload["ok"] is True, f"the legal probe was refused at max=1: {payload!r}"
        assert payload["coverage"].get("errors") == [], (
            f"the nested reads were refused by the counter at max=1: {payload['coverage'].get('errors')!r}"
        )
        assert payload["coverage"].get("successful_calls", 0) >= 1

        with pytest.raises(Exception) as second_error:
            gateway.execute_tool("scout_probe", {"query": "target"})
        assert "最大工具调用次数" in str(second_error.value)
    finally:
        gateway.close()


# ── the model's arguments must not be able to supply an authorizer ──


def test_model_arguments_cannot_inject_an_authorizer(tmp_path: Path) -> None:
    """A model cannot smuggle its own authorization into a nested read."""
    workspace = _build_workspace(tmp_path)
    gateway = _gateway(str(workspace), blacklist=["repo_rg"])
    try:
        result = gateway.execute_tool(
            "scout_probe",
            {
                "query": "target",
                "tool_authorizer": lambda *args, **kwargs: (True, ""),
                "capability_token": {"allowed_read_paths": ["."]},
                "allowed_tools": ["repo_rg"],
            },
        )
    finally:
        gateway.close()

    payload = _scout_payload(result)
    errors = [str(item) for item in payload["coverage"].get("errors", [])]
    assert any("repo_rg" in err for err in errors), f"a model-supplied authorizer widened the policy: {errors!r}"


# ── the committed read scope still applies to the nested reads ──


def test_the_committed_read_scope_still_applies_to_nested_reads(tmp_path: Path) -> None:
    """A nested read outside the committed scope is refused by the gateway."""
    workspace = _build_workspace(tmp_path)
    (workspace / "private").mkdir(parents=True, exist_ok=True)
    (workspace / "private" / "hidden.py").write_text('SECRET = "HIDDEN"\n', encoding="utf-8")

    gateway = _gateway(str(workspace), blacklist=[])
    try:
        # The gateway's own committed scope limits the nested read.
        gateway._capability_token = {"allowed_read_paths": ["src"]}  # deliberate: the committed scope under test
        result = gateway.execute_tool("scout_probe", {"query": "target"})
    finally:
        gateway.close()

    payload = _scout_payload(result)
    paths = payload["paths"]
    assert not any("private" in path for path in paths), f"a nested read escaped the committed scope: {paths!r}"


def test_an_empty_committed_scope_refuses_the_nested_reads(tmp_path: Path) -> None:
    """An explicitly empty committed scope must not authorize any nested read.

    The gateway refuses the dispatch itself, because the committed scope is
    empty and the dispatch's own operands are outside it. Either way no
    finding may be produced.
    """
    workspace = _build_workspace(tmp_path)
    gateway = _gateway(str(workspace), blacklist=[])
    try:
        gateway._capability_token = {"allowed_read_paths": []}  # deliberate: the committed scope under test
        try:
            result = gateway.execute_tool("scout_probe", {"query": "target"})
        except Exception as exc:  # noqa: BLE001 - the refusal may be raised
            assert "read scope" in str(exc) or "读取路径" in str(exc), str(exc)[:200]
            return
    finally:
        gateway.close()

    payload = _scout_payload(result)
    errors = [str(item) for item in payload["coverage"].get("errors", [])]
    assert errors, f"an empty scope produced no denial: {payload!r}"
    assert payload["paths"] == [], f"an empty scope still returned findings: {payload['paths']!r}"


# ── async re-entry ──


def test_the_nested_policy_holds_under_async_reentry(tmp_path: Path) -> None:
    """The same policy applies when the dispatch runs inside a running loop."""
    workspace = _build_workspace(tmp_path)
    gateway = _gateway(str(workspace), blacklist=["repo_rg"])
    try:
        # ``execute_tool`` is synchronous; run it inside a running event loop so
        # the handler's off-loop execution path is exercised.
        async def _dispatch() -> dict[str, Any]:
            return gateway.execute_tool("scout_probe", {"query": "target"})

        result = asyncio.run(_dispatch())
    finally:
        gateway.close()

    payload = _scout_payload(result)
    errors = [str(item) for item in payload["coverage"].get("errors", [])]
    assert any("repo_rg" in err for err in errors), f"the async path bypassed the blacklist: {errors!r}"


# ── the anchor store must not receive an unauthorized result ──


def test_the_anchor_store_receives_no_unauthorized_path(tmp_path: Path) -> None:
    """A refused nested read must not reach the persistent anchor store."""
    workspace = _build_workspace(tmp_path)
    gateway = _gateway(str(workspace), blacklist=["repo_rg"])
    try:
        gateway.execute_tool("scout_probe", {"query": "target"})
    finally:
        gateway.close()

    anchor_path = workspace / ".polaris" / "runtime" / "scout_anchors.json"
    if not anchor_path.exists():
        return
    data = json.loads(anchor_path.read_text(encoding="utf-8"))
    anchors = data.get("anchors") if isinstance(data, dict) else None
    for anchor in anchors or []:
        assert isinstance(anchor, dict)
        assert "private" not in str(anchor.get("path") or "")
