"""Saved verification declarations and attempt-bound output observations.

Only model replies/config are synthetic; service/client/HTTP/capture, SQLite,
command sandbox and filesystem observations remain real. Never delivery proof.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shlex
import time
from collections import Counter
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from polaris.cells.roles.runtime.internal.engineering_session import EngineeringSessionStore
from polaris.cells.roles.runtime.internal.engineering_verification import (
    EngineeringVerification,
    verification_observation,
)
from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RoleRunCommand, RunLimits
from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService, _native_client
from polaris.cells.roles.runtime.tests.engineering_support import (
    ScriptedProvider,
    scripted_provider as scripted_provider,
)
from polaris.cells.roles.runtime.tests.test_engineering_autonomous_verification import _response
from polaris.kernelone.fs import get_default_adapter, guarded_create_directory

_ROOT = Path(__file__).resolve().parents[7]
_AUDIT = _ROOT / ".polaris/audits/verification-output-policy-visibility-20261024/implementation"
_HINT = "Host verification output declarations (original saved state, not current authorization): "


@pytest.fixture
def isolated_workspace() -> Path:
    relative = "fixtures/" + uuid4().hex
    guarded_create_directory(str(_AUDIT), relative, parents=True, exist_ok=True)
    return _AUDIT / relative


def _scope(workspace: Path) -> PermissionScope:
    return PermissionScope(
        (workspace,), (workspace,), frozenset({"execute_command", "verify_project", "read_file"}), False
    )


def _limits() -> RunLimits:
    return RunLimits(time.time() + 30, 1000000, 64000)


def _python(source: str) -> str:
    return "python3 -B -c " + shlex.quote(source)


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _seed(
    workspace: Path, outputs: tuple[Path, ...] | None, check: str
) -> tuple[EngineeringSessionStore, PermissionScope, RunLimits, RoleRunCommand]:
    store, scope, limits = EngineeringSessionStore(workspace), _scope(workspace), _limits()
    command = RoleRunCommand(
        "output-policy",
        "director",
        "Observe current source and verify it.",
        workspace,
        required_checks=(check,) if check else (),
    )
    store.create_run(
        command,
        scope=scope,
        limits=limits,
        engine_version="engineering_v2",
        request_key=None,
        verification_output_roots=outputs,
    )
    return store, scope, limits, command


def _verifier(
    workspace: Path,
    store: EngineeringSessionStore,
    scope: PermissionScope,
    limits: RunLimits,
    outputs: tuple[Path, ...],
) -> EngineeringVerification:
    return EngineeringVerification(
        workspace, store=store, scope=scope, limits=limits, cancel=asyncio.Event(), writable_output_roots=outputs
    )


def _policy(declared: list[str] | None, effective: list[str], absent: list[str] | None) -> dict[str, Any]:
    return {
        "declared_output_roots": declared,
        "effective_output_roots": effective,
        "absent_output_roots": absent,
        "absent_reason": "not_present_at_verifier_scope_construction" if absent else None,
    }


def _hint(body: dict[str, Any]) -> dict[str, Any]:
    message = next(
        (item for item in body["messages"] if isinstance(item.get("content"), str) and _HINT in item["content"]), None
    )
    assert message is not None, "final physical provider body lost original saved verification output declarations"
    return json.JSONDecoder().raw_decode(message["content"].split(_HINT, 1)[1])[0]


def _assert_wire(service: EngineeringRunService, script: ScriptedProvider, session_id: str) -> list[dict[str, Any]]:
    fs = get_default_adapter()
    observed = [event["payload"] for event in service.store.load_events(session_id, kinds=("llm_request_observed",))]
    received = Counter(_digest(request["body"]) for request in script.requests)
    bodies: list[dict[str, Any]] = []
    for phase in ("prepared", "terminal"):
        snapshots = []
        for event in observed:
            if event["phase"] != phase:
                continue
            ref = event["context_snapshot_ref"]
            snapshot = json.loads(
                fs.read_text(str(service.workspace / ".polaris/runtime/contexts" / ref[:2] / ref), encoding="utf-8")
            )
            snapshots.append(snapshot["provider_request"]["final_physical_request"]["body"])
        assert Counter(_digest(body) for body in snapshots) == received
        if phase == "prepared":
            bodies = snapshots

    def keys(phase: str) -> Counter[tuple[str, str, int]]:
        return Counter(
            (event["capture_id"], event["provider_request_id"], event["dispatch_attempt_number"])
            for event in observed
            if event["phase"] == phase
        )

    assert keys("prepared") == keys("terminal")
    fs.write_text(
        str(service.workspace / "wire-evidence.json"),
        json.dumps(
            {
                "synthetic_component_only": True,
                "POST_count": len(script.requests),
                "observations": observed,
                "final_bodies": bodies,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return bodies


async def test_initial_final_provider_body_shows_saved_declaration_without_fs_probe(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, dist = isolated_workspace, isolated_workspace / "dist"
    scripted_provider.client(
        "openai", workspace, monkeypatch
    )  # Configure model only; service constructs native client.
    scripted_provider.enqueue_json(_response(text="Observed the original task."))
    _, scope, limits, command = _seed(workspace, (dist,), "")
    service = EngineeringRunService(workspace, writable_output_roots=(dist,))
    result = await service._run_current_role(
        command,
        scope=scope,
        limits=limits,
        cancel=asyncio.Event(),
        client=_native_client(workspace, limits, native_request_observer=service._observe_native_request),
    )
    assert result.status == "completed", result.text
    bodies = _assert_wire(service, scripted_provider, command.session_id)
    assert len(bodies) == 1
    hint = _hint(bodies[0])
    assert hint == {
        "declared_output_roots": [str(dist)],
        "existence_observed": False,
        "grants_current_write_permission": False,
    }
    assert bodies[0]["messages"][0]["role"] == "system" and "Director" in bodies[0]["messages"][0]["content"]
    assert any(str(message.get("content", "")).startswith(command.instruction) for message in bodies[0]["messages"])
    assert not dist.exists()
    history = service.store.load_message_history(command.session_id, role="director")
    assert all(not str(message.get("content", "")).startswith(_HINT) for message in history)


async def test_missing_output_attempt_policy_reaches_next_real_provider_body(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, dist = isolated_workspace, isolated_workspace / "dist"
    check = _python("print('bounded-readonly-check')")
    scripted_provider.client("openai", workspace, monkeypatch)
    scripted_provider.enqueue_json(_response(("verify-once", "verify_project", {"commands": [check]})))
    scripted_provider.enqueue_json(_response(text="The actual check completed."))
    service = EngineeringRunService(workspace, writable_output_roots=(dist,))
    command = RoleRunCommand("missing-output-policy", "director", "Select one finite verification command.", workspace)
    result = await service.run_role(command, scope=_scope(workspace), limits=_limits(), cancel=asyncio.Event())
    assert result.status == "completed", result.text
    completed = service.store.load_events(command.session_id, kinds=("verification_completed",))[-1]["payload"]
    assert completed["verification"] == "passed"
    assert completed["input_binding"]["scope"]["output_roots"] == []
    bodies = _assert_wire(service, scripted_provider, command.session_id)
    assert len(bodies) == 2
    feedback = next(message for message in bodies[1]["messages"] if message.get("tool_call_id") == "verify-once")
    summary = json.loads(json.loads(feedback["content"])["content"])
    assert summary["verification_output_policy"] == _policy([str(dist)], [], [str(dist)])
    assert completed["input_binding"]["verification_output_policy"] == summary["verification_output_policy"]
    assert not dist.exists()


async def test_existing_declared_output_policy_keeps_actual_command_scope(isolated_workspace: Path) -> None:
    workspace, dist = isolated_workspace, isolated_workspace / "dist"
    for name in ("dist", "out", "build"):
        guarded_create_directory(str(workspace), name, parents=True, exist_ok=True)
    check = _python(
        "from pathlib import Path; Path('dist/result.txt').write_text('ok',encoding='utf-8'); print('bounded-dist-write')"
    )
    store, scope, limits, command = _seed(workspace, (dist,), check)
    verifier = _verifier(workspace, store, scope, limits, (dist,))
    with store.acquire_workspace(workspace):
        observations = await verifier.run_verification((check,), session_id=command.session_id, workspace_revision=0)
    assert len(observations) == 1 and observations[0].ok
    completed = store.load_events(command.session_id, kinds=("verification_completed",))[-1]["payload"]
    assert completed["input_binding"]["scope"] == {
        "read_roots": [str(workspace)],
        "output_roots": [str(dist)],
        "execute_command": True,
        "network_allowed": False,
    }
    assert completed["input_binding"]["verification_output_policy"] == _policy([str(dist)], [str(dist)], [])
    assert verifier.tools.scope.write_roots == (dist,)
    assert get_default_adapter().read_text(str(dist / "result.txt"), encoding="utf-8") == "ok"
    assert not (workspace / "out/result.txt").exists() and not (workspace / "build/result.txt").exists()


@pytest.mark.parametrize("outputs", [(), None], ids=["explicit-empty", "legacy-unknown"])
async def test_cold_service_hint_preserves_saved_empty_or_unknown_without_new_host_fallback(
    outputs: tuple[Path, ...] | None,
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = isolated_workspace
    _, scope, limits, command = _seed(workspace, outputs, "")
    scripted_provider.client("openai", workspace, monkeypatch)
    scripted_provider.enqueue_json(_response(text="Cold read uses the original saved declaration."))
    cold = EngineeringRunService(workspace, writable_output_roots=(workspace / "new-host-output",))
    result = await cold._run_current_role(
        command,
        scope=scope,
        limits=limits,
        cancel=asyncio.Event(),
        client=_native_client(workspace, limits, native_request_observer=cold._observe_native_request),
    )
    assert result.status == "completed", result.text
    body = _assert_wire(cold, scripted_provider, command.session_id)[0]
    assert _hint(body)["declared_output_roots"] == ([] if outputs == () else None)
    assert cold.store.load_run(command.session_id, workspace=workspace)["verification_output_roots"] == (
        [] if outputs == () else None
    )
    assert not (workspace / "new-host-output").exists()


async def test_attempt_policy_is_frozen_at_scope_construction_and_cold_projection(isolated_workspace: Path) -> None:
    workspace, dist = isolated_workspace, isolated_workspace / "dist"
    check = _python("print('constructor-snapshot-check')")
    store, scope, limits, command = _seed(workspace, (dist,), check)
    verifier = _verifier(workspace, store, scope, limits, (dist,))
    # Explicit fixture preparation occurs after the Verifier's real scope was built.
    guarded_create_directory(str(workspace), "dist", parents=True, exist_ok=True)
    with store.acquire_workspace(workspace):
        await verifier.run_verification((check,), session_id=command.session_id, workspace_revision=0)
    cold_store = EngineeringSessionStore(workspace)
    payload = cold_store.load_events(command.session_id, kinds=("verification_completed",))[-1]["payload"]
    summary = json.loads(verification_observation("cold-output-policy", payload, 64000).content)
    assert summary["verification_output_policy"] == _policy([str(dist)], [], [str(dist)])
    assert summary["execution_scope"]["output_roots"] == []
    before = cold_store.load_events(command.session_id)
    dist.rmdir()
    assert (
        json.loads(verification_observation("cold-output-policy", payload, 64000).content)["verification_output_policy"]
        == summary["verification_output_policy"]
    )
    assert cold_store.load_events(command.session_id) == before


def test_legacy_completion_missing_policy_stays_unknown_and_unchanged() -> None:
    payload = {
        "verification": "failed",
        "input_binding": {"commands": ["old-check"], "scope": {"output_roots": ["historical-effective"]}},
        "checks": [{"ok": False, "exit_code": 1}],
    }
    before = json.dumps(payload, sort_keys=True)
    summary = json.loads(verification_observation("legacy-no-policy", payload, 64000).content)
    assert summary["execution_scope"]["output_roots"] == ["historical-effective"]
    assert summary.get("verification_output_policy") is None
    assert json.dumps(payload, sort_keys=True) == before


@pytest.mark.parametrize("outputs", [(), None], ids=["empty-attempt", "unknown-attempt"])
async def test_cold_attempt_empty_or_unknown_never_inherits_new_host_outputs(
    outputs: tuple[Path, ...] | None, isolated_workspace: Path
) -> None:
    workspace = isolated_workspace
    check = _python("print('cold-bound-check')")
    _, scope, limits, command = _seed(workspace, outputs, check)
    cold = EngineeringRunService(workspace, writable_output_roots=(workspace / "new-host-output",))
    row = cold.store.load_run(command.session_id, workspace=workspace)
    verifier = cold._verification(scope, limits, asyncio.Event(), output_roots=cold._saved_outputs(row))
    with cold.store.acquire_workspace(workspace):
        observations = await verifier.run_verification((check,), session_id=command.session_id, workspace_revision=0)
    assert observations[0].ok
    payload = cold.store.load_events(command.session_id, kinds=("verification_completed",))[-1]["payload"]
    assert payload["input_binding"]["verification_output_policy"] == _policy(
        [] if outputs == () else None, [], [] if outputs == () else None
    )
    assert payload["input_binding"]["scope"]["output_roots"] == []
    assert not (workspace / "new-host-output").exists()


async def test_revoked_verifier_output_does_not_add_initial_read_gate(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, dist = isolated_workspace, isolated_workspace / "dist"
    get_default_adapter().write_text(
        str(workspace / "source.txt"), "ordinary read remains authorized", encoding="utf-8"
    )
    _, scope, limits, command = _seed(workspace, (dist,), "")
    scripted_provider.client("openai", workspace, monkeypatch)
    for response in (
        _response(("ordinary-read", "read_file", {"file": "source.txt"})),
        _response(("revoked-verify", "verify_project", {"commands": ["exit 0"]})),
        _response(text="Read the source and retained the verifier refusal."),
    ):
        scripted_provider.enqueue_json(response)
    cold = EngineeringRunService(workspace, writable_output_roots=())
    cancel = asyncio.Event()
    client = _native_client(workspace, limits, native_request_observer=cold._observe_native_request)
    token = cold._owner.set((command, scope, limits, cancel, client))
    try:
        result = await cold.run_role(command, scope=scope, limits=limits, cancel=cancel)
    finally:
        cold._owner.reset(token)
    assert result.status == "completed", result.text
    events = cold.store.load_events(command.session_id)
    observations = {
        event["payload"]["call_id"]: event["payload"] for event in events if event["kind"] == "tool_observed"
    }
    assert observations["ordinary-read"]["ok"] is True
    assert observations["revoked-verify"]["ok"] is False
    assert (
        "saved verification output declarations exceed current host policy" in observations["revoked-verify"]["content"]
    )
    assert not any(event["kind"] == "verification_started" for event in events)
    assert not any(
        event["kind"] == "tool_started" and event["payload"]["tool"] == "execute_command" for event in events
    )
    assert _hint(_assert_wire(cold, scripted_provider, command.session_id)[0])["declared_output_roots"] == [str(dist)]
    assert not dist.exists()


@pytest.mark.parametrize("kind", ["symlink", "outside"])
def test_invalid_output_path_still_refuses_before_command_scope(kind: str, isolated_workspace: Path) -> None:
    workspace = isolated_workspace
    dist = workspace / "dist"
    store, scope, limits, _ = _seed(workspace, (dist,), "exit 0")
    if kind == "symlink":
        guarded_create_directory(str(workspace), "real", parents=True, exist_ok=True)
        dist.symlink_to(workspace / "real", target_is_directory=True)
        invalid = dist
    else:
        invalid = workspace.parent
    with pytest.raises(ValueError):
        _verifier(workspace, store, scope, limits, (invalid,))
    assert not store.load_events("output-policy", kinds=("verification_started", "tool_started"))
