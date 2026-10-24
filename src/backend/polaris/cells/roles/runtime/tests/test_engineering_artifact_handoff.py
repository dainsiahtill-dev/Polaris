"""Actual Service/HTTP/tools/K1/browser handoff; synthetic SVG/model is not Bench delivery."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import shlex
import time
from collections.abc import Iterator
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

import pytest
from polaris.cells.roles.runtime.public.contracts.engineering_run import (
    BrowserObservationConfig,
    PermissionScope,
    RoleRunCommand,
    RunLimits,
)
from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService
from polaris.cells.roles.runtime.tests.engineering_support import (
    ScriptedProvider,
    isolated_workspace as isolated_workspace,
)
from polaris.cells.roles.runtime.tests.test_engineering_autonomous_verification import _response
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter

SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="240"><rect x="40" y="40" width="120" height="100" fill="royalblue"/></svg>'
PACKAGES = Path("/home/dains/.pyenv/versions/3.12.3/lib/python3.12/site-packages")
CHROME = Path(
    "/home/dains/.cache/ms-playwright/chromium_headless_shell-1223/chrome-headless-shell-linux64/chrome-headless-shell"
)


def _tool_payloads(body: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for msg in body.get("messages", []):
        if msg.get("role") == "tool" and isinstance(msg.get("content"), str):
            try:
                value = json.loads(msg["content"])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                result.append(value)
    return result


@pytest.fixture
def artifact_provider(monkeypatch: pytest.MonkeyPatch) -> Iterator[ScriptedProvider]:
    """Only remote choices are simulated; each recorded POST is actual native HTTP."""
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")
    monkeypatch.setenv("KERNELONE_LIGHTWEIGHT_STREAM_SESSIONS", "0")
    script = ScriptedProvider()
    code = (
        'from pathlib import Path; import os; Path(os.environ["POLARIS_SANDBOX_OUTPUT"],"report.svg").write_text('
        + repr(SVG)
        + ',encoding="utf-8"); print("REAL_PRIVATE_OUTPUT_CREATED")'
    )
    command = "python3 -B -c " + shlex.quote(code)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *args: object) -> None:
            pass

        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode("utf-8"))
            script.requests.append({"path": self.path, "body": body})
            results = _tool_payloads(body)
            if getattr(script, "qa_mode", False):
                qa = "You are QA" in str(body.get("messages", [{}])[0].get("content", ""))
                tools = {value.get("tool") for value in results}
                if not qa:
                    payload = _response(text="Director handoff for actual independent QA transport.")
                elif "read_file" not in tools:
                    payload = _response(("qa-read", "read_file", {"file": "source.txt"}))
                elif "execute_command" not in tools:
                    payload = _response(
                        ("qa-produce", "execute_command", {"command": command, "capture_artifacts": ["report.svg"]})
                    )
                elif "browser_observe" not in tools:
                    artifacts = next(value["artifacts"] for value in results if value.get("artifacts"))
                    payload = _response(
                        (
                            "qa-view",
                            "browser_observe",
                            {
                                "artifact_ref": artifacts[0]["artifact_ref"],
                                "viewport": {"width": 320, "height": 240},
                                "frame_times_ms": [0],
                            },
                        )
                    )
                else:
                    payload = _response(
                        (
                            "qa-report",
                            "report_review",
                            {"approved": True, "feedback": "Synthetic image transport only.", "major_findings": []},
                        )
                    )
            elif not results:
                payload = _response(
                    (
                        "produce",
                        getattr(script, "command_tool", "execute_command"),
                        {"command": command, "artifacts": ["report.svg"]},
                    )
                )
            else:
                produced = next((v for v in results if v.get("tool") == "execute_command"), {})
                artifacts = produced.get("artifacts", [])
                if artifacts and not any(v.get("tool") == "browser_observe" for v in results):
                    payload = _response(
                        (
                            "view-saved",
                            "browser_observe",
                            {
                                "artifact_ref": artifacts[0]["artifact_ref"],
                                "viewport": {"width": 320, "height": 240},
                                "frame_times_ms": [0],
                            },
                        )
                    )
                else:
                    payload = _response(text="Synthetic transport finished; no semantic delivery claim.")
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    script.base_url = f"http://127.0.0.1:{server.server_port}"
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield script
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.asyncio
async def test_real_readonly_command_output_survives_journal_and_reaches_browser_and_wire(
    isolated_workspace: Path, artifact_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dropping actual command output or substituting same-name workspace decoy fails."""
    workspace = isolated_workspace
    fs = KernelFileSystem(str(workspace), get_default_adapter(), runtime_owner_workspace=str(workspace))
    fs.workspace_write_text("source.txt", "SYNTHETIC_SOURCE_V1", encoding="utf-8")
    fs.workspace_write_text("report.svg", SVG.replace("royalblue", "red"), encoding="utf-8")
    fs.workspace_write_text("expected.svg", SVG, encoding="utf-8")
    scope = PermissionScope(
        (workspace, PACKAGES / "playwright", PACKAGES / "pyee", PACKAGES / "greenlet", CHROME.parent),
        (),
        frozenset({"execute_command", "browser_observe"}),
        False,
        BrowserObservationConfig(str(CHROME), "/usr/bin/python3", (str(PACKAGES),)),
    )
    artifact_provider.client("openai", workspace, monkeypatch)
    service = EngineeringRunService(workspace, require_qa=False, permission_resolver=lambda _row: scope)
    command = RoleRunCommand(
        "actual-artifact-handoff",
        "director",
        "Synthetic output persistence and native visual transport only.",
        workspace,
        required_checks=("test -f source.txt",),
    )
    await service.start(command, scope=scope, limits=RunLimits(time.time() + 60, 1_000_000, 64_000))
    await service.wait(command.session_id, workspace=workspace)
    events = service.store.load_events(command.session_id)
    produced = next(
        e["payload"] for e in events if e["kind"] == "tool_observed" and e["payload"].get("call_id") == "produce"
    )
    assert produced.get("artifacts"), produced
    artifact = produced["artifacts"][0]
    assert base64.b64decode(artifact["data_base64"]) == SVG.encode("utf-8")
    assert artifact["sha256"] == hashlib.sha256(SVG.encode("utf-8")).hexdigest()
    viewed = next(
        e["payload"] for e in events if e["kind"] == "tool_observed" and e["payload"].get("call_id") == "view-saved"
    )
    assert viewed["ok"] and viewed.get("images"), viewed
    assert all(
        artifact["data_base64"] not in json.dumps(q["body"], ensure_ascii=False) for q in artifact_provider.requests
    )
    native_images = []
    for item in artifact_provider.requests[-1]["body"]["messages"]:
        for block in item.get("content", []) if isinstance(item.get("content"), list) else []:
            if block.get("type") == "image_url":
                native_images.append(base64.b64decode(block["image_url"]["url"].split(",", 1)[1]))
    assert native_images == [base64.b64decode(viewed["images"][0]["data_base64"])]
    before = len([e for e in events if e["kind"] == "tool_started" and e["payload"].get("tool") == "execute_command"])
    cold = EngineeringRunService(workspace, require_qa=False, permission_resolver=lambda _row: scope)
    saved = cold.store.artifact(command.session_id, artifact["artifact_ref"])
    assert saved is not None and base64.b64decode(saved.data_base64) == SVG.encode("utf-8")
    assert (
        len(
            [
                e
                for e in cold.store.load_events(command.session_id)
                if e["kind"] == "tool_started" and e["payload"].get("tool") == "execute_command"
            ]
        )
        == before
    )
    from polaris.cells.roles.runtime.internal.engineering_roles import register_browser_observation_tool

    # Cold host invocation supplies the same production ToolSpec bootstrap that
    # a normal newly admitted role loop performs in its own task context.
    register_browser_observation_tool()
    # A cold owner renders journal bytes, then an independently authorized file
    # positive control proves the same actual pixels despite the red decoy.
    cold_limits = RunLimits(time.time() + 60, 1_000_000, 64_000)
    client = artifact_provider.client("openai", workspace, monkeypatch)
    owner = cold._owner.set((command, scope, cold_limits, asyncio.Event(), client))
    role = cold._active_role.set("director")
    try:
        cold_turn = cold.store.append_event(command.session_id, kind="turn_started", role="director", payload={})
        cold_view = await cold._observe_dispatch(
            {
                "artifact_ref": artifact["artifact_ref"],
                "viewport": {"width": 320, "height": 240},
                "frame_times_ms": [0],
            },
            cold_turn,
            "cold-saved",
        )
        expected_view = await cold._observe_dispatch(
            {"entry": "expected.svg", "viewport": {"width": 320, "height": 240}, "frame_times_ms": [0]},
            cold_turn,
            "expected-file",
        )
        assert cold_view.ok and expected_view.ok, (cold_view.content, expected_view.content)
        assert (
            cold_view.images[0].data_base64 == expected_view.images[0].data_base64 == viewed["images"][0]["data_base64"]
        )
        forged = await cold._observe_dispatch({"artifact_ref": "artifact_" + "0" * 32}, cold_turn, "forged")
        assert not forged.ok and "committed producer" in forged.content
        original_scope = scope
        scope = replace(scope, enabled_tools=frozenset({"browser_observe"}))
        with pytest.raises(PermissionError, match="permission policy"):
            await cold._observe_dispatch({"artifact_ref": artifact["artifact_ref"]}, cold_turn, "revoked")
        scope = original_scope
        foreign = RoleRunCommand("foreign-run", "director", "Foreign synthetic run.", workspace)
        cold.store.create_run(
            foreign, scope=scope, limits=cold_limits, engine_version="engineering_v2", request_key=None
        )
        with pytest.raises(ValueError, match="committed producer"):
            cold._saved_artifact(foreign.session_id, artifact["artifact_ref"], {})
        fs.workspace_write_text("source.txt", "SYNTHETIC_SOURCE_CHANGED", encoding="utf-8")
        stale = await cold._observe_dispatch({"artifact_ref": artifact["artifact_ref"]}, cold_turn, "stale")
        assert not stale.ok and "stale" in stale.content
        revisited = await cold._observe_dispatch({"revisit_call_ids": ["view-saved"]}, cold_turn, "old-pixels")
        assert revisited.ok and json.loads(revisited.content)["saved_captures"][0]["image_sha256"] == [
            viewed["images"][0]["sha256"]
        ]
        fs.workspace_write_text("source.txt", "SYNTHETIC_SOURCE_V1", encoding="utf-8")
        import sqlite3

        # Precise persistence corruption: keep original digest while altering bytes.
        tampered_payload = {key: value for key, value in produced.items() if key != "turn_seq"}
        tampered_payload = json.loads(json.dumps(tampered_payload))
        tampered_payload["artifacts"][0]["data_base64"] = base64.b64encode(b"tampered").decode("ascii")
        with sqlite3.connect(cold.store.database_path) as connection:
            connection.execute(
                "UPDATE tool_calls SET observation=? WHERE session_id=? AND call_id=?",
                (json.dumps(tampered_payload), command.session_id, "produce"),
            )
        tampered = await cold._observe_dispatch({"artifact_ref": artifact["artifact_ref"]}, cold_turn, "tampered")
        assert not tampered.ok and any(word in tampered.content for word in ("digest", "sha256", "SHA", "integrity")), (
            tampered
        )

    finally:
        cold._active_role.reset(role)
        cold._owner.reset(owner)
    assert fs.workspace_read_text("source.txt", encoding="utf-8") == "SYNTHETIC_SOURCE_V1"
    assert 'fill="red"' in fs.workspace_read_text("report.svg", encoding="utf-8")


def _scope(workspace: Path, *, visual: bool = False) -> PermissionScope:
    return PermissionScope(
        (workspace, PACKAGES / "playwright", PACKAGES / "pyee", PACKAGES / "greenlet", CHROME.parent),
        (),
        frozenset({"execute_command", "browser_observe", "read_file", "report_review"}),
        False,
        BrowserObservationConfig(str(CHROME), "/usr/bin/python3", (str(PACKAGES),), require_visual_review=visual),
    )


def _filesystem(workspace: Path) -> KernelFileSystem:
    return KernelFileSystem(str(workspace), get_default_adapter(), runtime_owner_workspace=str(workspace))


@pytest.mark.asyncio
async def test_actual_qa_frame_consumes_original_saved_artifact_before_report_and_cold_verification(
    isolated_workspace: Path,
    artifact_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = isolated_workspace
    _filesystem(workspace).workspace_write_text("source.txt", "QA_SOURCE", encoding="utf-8")
    scope = _scope(workspace, visual=True)
    artifact_provider.qa_mode = True
    artifact_provider.client("openai", workspace, monkeypatch)
    service = EngineeringRunService(workspace, require_qa=True, permission_resolver=lambda _row: scope)
    command = RoleRunCommand(
        "actual-qa-artifact",
        "director",
        "Original synthetic goal must reach independent QA.",
        workspace,
        required_checks=("test -f source.txt",),
    )
    await service.start(command, scope=scope, limits=RunLimits(time.time() + 90, 1_000_000, 64_000))
    result = await service.wait(command.session_id, workspace=workspace)
    events = service.store.load_events(command.session_id)
    captures = [e for e in events if e["kind"] == "tool_observed" and e["payload"].get("call_id") == "qa-view"]
    assert captures and captures[0]["payload"]["ok"], result
    reviews = [e for e in events if e["kind"] == "qa_review_completed"]
    assert reviews and reviews[-1]["payload"]["verdict"] == "approved", reviews
    assert any(
        e["kind"] == "llm_request_observed" and e["role"] == "qa" and e["seq"] > captures[0]["seq"] for e in events
    )
    wire_images = [
        block
        for q in artifact_provider.requests
        for msg in q["body"].get("messages", [])
        for block in msg.get("content", [])
        if isinstance(msg.get("content"), list) and isinstance(block, dict) and block.get("type") == "image_url"
    ]
    assert wire_images
    cold = EngineeringRunService(workspace, require_qa=False, permission_resolver=lambda _row: scope)
    row = cold.observe(command.session_id, workspace=workspace)
    assert row["qa_verdict"] == "approved" and row["verification"] == "passed", row
    _filesystem(workspace).workspace_write_text("source.txt", "QA_CHANGED_SOURCE", encoding="utf-8")
    changed = cold.observe(command.session_id, workspace=workspace)
    assert changed["qa_verdict"] == "unknown" and changed["verification"] == "unknown"
    current_verifier = cold._verification(
        scope, RunLimits(time.time() + 30, 1_000_000, 64_000), asyncio.Event(), output_roots=()
    )
    old_binding = next(
        e["payload"]["input_binding"]
        for e in events
        if e["kind"] == "visual_observation_completed" and e["role"] == "qa"
    )
    with pytest.raises(ValueError, match=r"current|fresh|candidate"):
        cold._require_visual_input(
            command.session_id,
            role="qa",
            scope=scope,
            current_binding=lambda: current_verifier.review_binding(command.session_id),
        )
    original = cold._saved_artifact(
        command.session_id,
        next(
            e["payload"]["artifacts"][0]["artifact_ref"]
            for e in events
            if e["kind"] == "tool_observed" and e["payload"].get("artifacts")
        ),
        current_verifier.review_binding(command.session_id),
        require_current=False,
    )
    assert (
        original.source_digest is not None
        and old_binding["inputs"] != current_verifier.review_binding(command.session_id)["inputs"]
    )
    for q in artifact_provider.requests:
        if "You are QA" in str(q["body"]["messages"][0].get("content")):
            assert "Original synthetic goal" in json.dumps(q["body"]["messages"])


@pytest.mark.parametrize(
    "selection", [["../escape.svg"], ["/absolute.svg"], ["dup.svg", "dup.svg"], ["x"] * 17, "file.svg"]
)
@pytest.mark.asyncio
async def test_invalid_requested_names_fail_before_any_real_effect(isolated_workspace: Path, selection: object) -> None:
    from polaris.cells.roles.runtime.internal.engineering_tools import EngineeringTools

    workspace = isolated_workspace
    tools = EngineeringTools(
        workspace,
        PermissionScope((workspace,), (), frozenset({"execute_command"}), False),
        RunLimits(time.time() + 15, 1_000_000, 64_000),
        asyncio.Event(),
        artifact_binding=lambda: {"inputs": {}},
    )
    result = await tools.execute(
        "invalid", "execute_command", {"command": "printf SHOULD_NOT_RUN", "artifacts": selection}
    )
    assert not result.ok and result.exit_code is None and "SHOULD_NOT_RUN" not in result.content
    assert not (workspace / ".polaris" / "runtime" / "command-output").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["missing", "oversized", "failed-with-file"])
async def test_actual_command_result_survives_capture_failure_and_failed_exit(
    isolated_workspace: Path, mode: str
) -> None:
    from polaris.cells.roles.runtime.internal.engineering_tools import EngineeringTools
    from polaris.kernelone.process import open_command_output_capture

    workspace = isolated_workspace
    tools = EngineeringTools(
        workspace,
        PermissionScope((workspace,), (), frozenset({"execute_command"}), False),
        RunLimits(time.time() + 15, 1_000_000, 64_000),
        asyncio.Event(),
        artifact_binding=lambda: {"inputs": {}},
    )
    script = "printf ACTUAL_STDOUT; printf ACTUAL_STDERR >&2; "
    if mode == "oversized":
        script += 'head -c 1000001 /dev/zero > "$POLARIS_SANDBOX_OUTPUT/report.svg"; exit 7'
    elif mode == "failed-with-file":
        script += 'printf diagnostic > "$POLARIS_SANDBOX_OUTPUT/report.svg"; exit 7'
    else:
        script += "exit 7"
    from polaris.kernelone.fs import guarded_create_directory

    guarded_create_directory(str(workspace), ".polaris", parents=True, exist_ok=True)
    with open_command_output_capture(workspace) as capture:
        root = capture.root
        result = await tools.execute(
            "real", "execute_command", {"command": script, "artifacts": ["report.svg"]}, output_capture=capture
        )
        assert (
            not result.ok
            and result.exit_code == 7
            and "ACTUAL_STDOUT" in result.content
            and "ACTUAL_STDERR" in result.content
        )
        if mode == "failed-with-file":
            assert len(result.artifacts) == 1 and base64.b64decode(result.artifacts[0].data_base64) == b"diagnostic"
        else:
            assert not result.artifacts and "Artifact capture failed" in result.content
    assert not root.exists()


def test_capture_schema_aliases_and_unavailable_host_hide_capture_field(isolated_workspace: Path) -> None:
    from polaris.cells.roles.runtime.internal.engineering_tools import EngineeringTools
    from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

    workspace = isolated_workspace
    scope = PermissionScope((workspace,), (), frozenset({"execute_command"}), False)
    tools = EngineeringTools(workspace, scope, RunLimits(None, 1_000_000, 64_000), asyncio.Event())
    assert "artifacts" not in tools.schemas()[0]["function"]["parameters"]["properties"]
    supported = EngineeringTools(
        workspace, scope, RunLimits(None, 1_000_000, 64_000), asyncio.Event(), artifact_binding=lambda: {"inputs": {}}
    )
    assert "artifacts" in supported.schemas()[0]["function"]["parameters"]["properties"]
    for alias in ("capture_artifacts", "output_files"):
        assert supported.normalize("run_command", {"cmd": "true", alias: ["report.svg"]}) == (
            "execute_command",
            {"command": "true", "artifacts": ["report.svg"]},
        )
    assert ToolSpecRegistry.get_canonical("shell") == "execute_command"


@pytest.mark.asyncio
async def test_explicit_empty_authority_allocates_no_capture(isolated_workspace: Path) -> None:
    from polaris.cells.roles.runtime.internal.engineering_tools import EngineeringTools

    workspace = isolated_workspace
    tools = EngineeringTools(
        workspace,
        PermissionScope((), (), frozenset(), False),
        RunLimits(None, 1_000_000, 64_000),
        asyncio.Event(),
        artifact_binding=lambda: {"inputs": {}},
    )
    result = await tools.execute(
        "denied", "execute_command", {"command": "printf SHOULD_NOT_RUN", "artifacts": ["report.svg"]}
    )
    assert not result.ok and result.exit_code is None
    assert not (workspace / ".polaris" / "runtime" / "command-output").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["execute_command", "run_command"])
async def test_capture_without_browser_configuration_persists_actual_bytes(
    isolated_workspace: Path,
    artifact_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
) -> None:
    workspace = isolated_workspace
    artifact_provider.command_tool = tool_name
    _filesystem(workspace).workspace_write_text("source.txt", "NO_BROWSER", encoding="utf-8")
    scope = PermissionScope((workspace,), (), frozenset({"execute_command"}), False)
    artifact_provider.client("openai", workspace, monkeypatch)
    service = EngineeringRunService(workspace, require_qa=False, permission_resolver=lambda _row: scope)
    command = RoleRunCommand(
        "capture-no-browser",
        "director",
        "Persist actual bytes without browser capability.",
        workspace,
        required_checks=("test -f source.txt",),
    )
    await service.start(command, scope=scope, limits=RunLimits(time.time() + 35, 1_000_000, 64_000))
    await service.wait(command.session_id, workspace=workspace)
    produced = next(
        e["payload"]
        for e in service.store.load_events(command.session_id)
        if e["kind"] == "tool_observed" and e["payload"].get("call_id") == "produce"
    )
    assert produced.get("artifacts"), produced
    cold = EngineeringRunService(workspace, require_qa=False, permission_resolver=lambda _row: scope)
    saved = cold.store.artifact(command.session_id, produced["artifacts"][0]["artifact_ref"])
    assert saved is not None and base64.b64decode(saved.data_base64) == SVG.encode("utf-8")
    assert all(
        "browser_observe" not in {t["function"]["name"] for t in q["body"].get("tools", [])}
        for q in artifact_provider.requests
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["observation", "completion"])
async def test_actual_capture_commit_fault_retains_resource_and_cold_resume_never_replays(
    isolated_workspace: Path,
    artifact_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    workspace = isolated_workspace
    _filesystem(workspace).workspace_write_text("source.txt", "FAULT_SOURCE", encoding="utf-8")
    scope = PermissionScope((workspace,), (), frozenset({"execute_command"}), False)
    artifact_provider.client("openai", workspace, monkeypatch)
    service = EngineeringRunService(workspace, require_qa=False, permission_resolver=lambda _row: scope)
    original_record = service.store.record_tool_observation
    original_append = service.store._append_event

    def record(session_id: str, *, turn_seq: int, observation: Any, role: str, **options: Any) -> None:
        if phase == "observation" and observation.call_id == "produce":
            assert observation.artifacts and observation.exit_code == 0
            raise OSError("injected observation commit fault after actual command")
        original_record(session_id, turn_seq=turn_seq, observation=observation, role=role, **options)

    def append(connection: Any, session_id: str, *, kind: str, role: str, payload: dict[str, Any]) -> int:
        if phase == "completion" and kind == "tool_output_capture_completed":
            raise OSError("injected capture metadata commit fault")
        return original_append(connection, session_id, kind=kind, role=role, payload=payload)

    monkeypatch.setattr(service.store, "record_tool_observation", record)
    monkeypatch.setattr(service.store, "_append_event", append)
    command = RoleRunCommand(
        "capture-commit-fault",
        "director",
        "Observe commit uncertainty without replay.",
        workspace,
        required_checks=("test -f source.txt",),
    )
    await service.start(command, scope=scope, limits=RunLimits(time.time() + 40, 1_000_000, 64_000))
    result = await service.wait(command.session_id, workspace=workspace)
    assert result.status == "interrupted" and result.verification == "unknown", result
    events = service.store.load_events(command.session_id)
    prepared = next(e for e in events if e["kind"] == "tool_output_capture_prepared")
    scratch = Path(prepared["payload"]["scratch_path"])
    resource_fs = KernelFileSystem(str(scratch.parent), get_default_adapter(), read_only_paths=True)
    assert resource_fs.workspace_read_bytes(str(scratch / "report.svg")) == SVG.encode("utf-8")
    before = len(artifact_provider.requests)
    cold = EngineeringRunService(workspace, require_qa=False, permission_resolver=lambda _row: scope)
    with pytest.raises(RuntimeError, match="reconcil"):
        await cold.resume(command.session_id, workspace=workspace)
    assert len(artifact_provider.requests) == before
    assert cold.store.pending_tool_calls(command.session_id)
    assert not any(e["kind"] == "tool_observed" and e["payload"].get("call_id") == "produce" for e in events)
    assert not any(e["kind"] == "tool_output_capture_completed" for e in events)
    assert cold.store.load_run(command.session_id, workspace=workspace)["workspace_revision"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["html-large", "png-and-jpeg"])
async def test_real_saved_static_formats_and_large_chunked_artifact_transport(
    isolated_workspace: Path, kind: str
) -> None:
    from polaris.cells.roles.runtime.internal.engineering_browser import capture_browser_observation
    from polaris.cells.roles.runtime.internal.engineering_tools import EngineeringTools
    from polaris.kernelone.fs import guarded_create_directory
    from polaris.kernelone.process import open_command_output_capture

    workspace = isolated_workspace
    guarded_create_directory(str(workspace), ".polaris", parents=True, exist_ok=True)
    scope = _scope(workspace)
    tools = EngineeringTools(
        workspace,
        scope,
        RunLimits(time.time() + 40, 1_000_000, 64_000),
        asyncio.Event(),
        artifact_binding=lambda: {"inputs": {}},
    )
    if kind == "html-large":
        filename = "large.html"
        prefix = '<html><body><div style="width:120px;height:100px;background:royalblue">ORIGINAL_HTML</div><!--'
        script = (
            "from pathlib import Path; import os; content="
            + repr(prefix)
            + '+"x"*180000+"--></body></html>"; Path(os.environ["POLARIS_SANDBOX_OUTPUT"],"large.html").write_text(content,encoding="utf-8")'
        )
    else:
        filename = "image.png"
        script = r'from pathlib import Path; import os,struct,zlib; chunk=lambda name,data:struct.pack("!I",len(data))+name+data+struct.pack("!I",zlib.crc32(name+data)); png=b"\x89PNG\r\n\x1a\n"+chunk(b"IHDR",struct.pack("!2I5B",320,240,8,2,0,0,0))+chunk(b"IDAT",zlib.compress((b"\0"+b"\x41\x69\xe1"*320)*240))+chunk(b"IEND",b""); Path(os.environ["POLARIS_SANDBOX_OUTPUT"],"image.png").write_bytes(png)'
    with open_command_output_capture(workspace) as output:
        produced = await tools.execute(
            "format-producer",
            "execute_command",
            {"command": "python3 -B -c " + shlex.quote(script), "output_files": [filename]},
            output_capture=output,
        )
        assert produced.ok and produced.artifacts, produced
    artifact = produced.artifacts[0]

    async def run(argv: list[str], timeout: float) -> Any:
        return await tools._process(argv, timeout=timeout, writes=())

    viewed = await capture_browser_observation(
        "format-view",
        {"artifact_ref": artifact.artifact_ref, "viewport": {"width": 320, "height": 240}, "frame_times_ms": [0]},
        workspace=workspace,
        scope=scope,
        run_process=run,
        saved_artifact=artifact,
    )
    assert viewed.ok and viewed.images, viewed
    metadata = json.loads(viewed.content)
    assert metadata["origin_mode"] == "saved_artifact" and metadata["saved_artifact"]["sha256"] == artifact.sha256
    assert artifact.data_base64 not in viewed.content
    if kind == "html-large":
        assert len(base64.b64decode(artifact.data_base64)) > 128 * 1024
        assert "ORIGINAL_HTML" in metadata["dom"]["body_text"]
    else:
        jpeg = viewed.images[0].data_base64
        with open_command_output_capture(workspace) as output:
            command = "python3 -B -c " + shlex.quote(
                'from pathlib import Path; import os,base64; Path(os.environ["POLARIS_SANDBOX_OUTPUT"],"image.jpg").write_bytes(base64.b64decode('
                + repr(jpeg)
                + "))"
            )
            produced_jpeg = await tools.execute(
                "jpeg-producer",
                "execute_command",
                {"command": command, "artifacts": ["image.jpg"]},
                output_capture=output,
            )
            assert produced_jpeg.ok and produced_jpeg.artifacts, produced_jpeg
        jpg = produced_jpeg.artifacts[0]
        jpeg_view = await capture_browser_observation(
            "jpeg-view",
            {"artifact_ref": jpg.artifact_ref, "viewport": {"width": 320, "height": 240}, "frame_times_ms": [0]},
            workspace=workspace,
            scope=scope,
            run_process=run,
            saved_artifact=jpg,
        )
        assert jpeg_view.ok and jpeg_view.images, jpeg_view


@pytest.mark.asyncio
async def test_real_timed_out_capture_is_drained_before_normal_resource_cleanup(isolated_workspace: Path) -> None:
    from polaris.cells.roles.runtime.internal.engineering_tools import EngineeringTools
    from polaris.kernelone.fs import guarded_create_directory
    from polaris.kernelone.process import open_command_output_capture

    workspace = isolated_workspace
    guarded_create_directory(str(workspace), ".polaris", parents=True, exist_ok=True)
    tools = EngineeringTools(
        workspace,
        PermissionScope((workspace,), (), frozenset({"execute_command"}), False),
        RunLimits(time.time() + 10, 1_000_000, 64_000),
        asyncio.Event(),
        artifact_binding=lambda: {"inputs": {}},
    )
    with open_command_output_capture(workspace) as output:
        root = output.root
        result = await tools.execute(
            "timeout",
            "execute_command",
            {
                "command": 'printf REAL_PARTIAL > "$POLARIS_SANDBOX_OUTPUT/report.svg"; sleep 30',
                "timeout": 1,
                "artifacts": ["report.svg"],
            },
            output_capture=output,
        )
        assert result.timed_out and not result.ok and "physical cleanup completed" in result.content
        assert output.read(["report.svg"])[0].data == b"REAL_PARTIAL"
    assert not root.exists()


@pytest.mark.asyncio
async def test_capture_observation_and_actual_revision_commit_are_idempotent_together(isolated_workspace: Path) -> None:
    from polaris.cells.roles.runtime.internal.engineering_session import EngineeringSessionStore
    from polaris.cells.roles.runtime.internal.engineering_tools import EngineeringTools
    from polaris.kernelone.process import open_command_output_capture

    workspace = isolated_workspace
    _filesystem(workspace).workspace_write_text("source.txt", "REVISION", encoding="utf-8")
    store = EngineeringSessionStore(workspace)
    scope = PermissionScope((workspace,), (workspace,), frozenset({"execute_command"}), False)
    limits = RunLimits(time.time() + 20, 1_000_000, 64_000)
    command = RoleRunCommand("atomic-revision", "director", "Atomic observation candidate test.", workspace)
    store.create_run(command, scope=scope, limits=limits, engine_version="engineering_v2", request_key=None)
    binding = {"workspace_revision": 0, "inputs": {"source": "stable"}}
    tools = EngineeringTools(workspace, scope, limits, asyncio.Event(), artifact_binding=lambda: binding)
    turn = store.append_event(command.session_id, kind="turn_started", role="director", payload={})
    store.record_tool_dispatch(
        command.session_id,
        turn_seq=turn,
        call_id="produce",
        tool="execute_command",
        arguments={"command": "actual"},
        role="director",
    )
    with open_command_output_capture(workspace) as output:
        store.append_event(
            command.session_id,
            kind="tool_output_capture_prepared",
            role="director",
            payload={"turn_seq": turn, "call_id": "produce", "scratch_path": str(output.root)},
        )
        observed = await tools.execute(
            "produce",
            "execute_command",
            {"command": 'printf actual > "$POLARIS_SANDBOX_OUTPUT/report.svg"', "artifacts": ["report.svg"]},
            output_capture=output,
        )
        assert observed.ok and observed.artifacts
        for _ in range(2):
            store.record_tool_observation(
                command.session_id,
                turn_seq=turn,
                observation=observed,
                role="director",
                capture_completion={"input_binding": binding},
                advance_workspace_revision=True,
                expected_workspace_revision=0,
            )
    row = store.load_run(command.session_id, workspace=workspace)
    events = store.load_events(command.session_id)
    assert row["workspace_revision"] == 1
    assert len([e for e in events if e["kind"] == "tool_observed"]) == 1
    completed = [e for e in events if e["kind"] == "tool_output_capture_completed"]
    assert len(completed) == 1 and completed[0]["payload"]["input_binding"]["workspace_revision"] == 1


@pytest.mark.asyncio
async def test_real_source_change_during_production_keeps_bytes_with_unknown_candidate(
    isolated_workspace: Path,
) -> None:
    from polaris.cells.roles.runtime.internal.engineering_tools import EngineeringTools
    from polaris.kernelone.fs import guarded_create_directory
    from polaris.kernelone.process import open_command_output_capture

    workspace = isolated_workspace
    filesystem = _filesystem(workspace)
    filesystem.workspace_write_text("source.txt", "SOURCE_BEFORE", encoding="utf-8")
    guarded_create_directory(str(workspace), ".polaris", parents=True, exist_ok=True)

    def binding() -> dict[str, Any]:
        return {"inputs": {"source.txt": hashlib.sha256(filesystem.workspace_read_bytes("source.txt")).hexdigest()}}

    tools = EngineeringTools(
        workspace,
        PermissionScope((workspace,), (workspace,), frozenset({"execute_command"}), False),
        RunLimits(time.time() + 15, 1_000_000, 64_000),
        asyncio.Event(),
        artifact_binding=binding,
    )
    with open_command_output_capture(workspace) as capture:
        observed = await tools.execute(
            "changed-source",
            "execute_command",
            {
                "command": 'printf SOURCE_AFTER > source.txt; printf ACTUAL_BYTES > "$POLARIS_SANDBOX_OUTPUT/report.svg"',
                "artifacts": ["report.svg"],
            },
            output_capture=capture,
        )
        assert observed.ok and observed.exit_code == 0 and observed.artifacts
        assert observed.artifacts[0].source_digest is None
        assert base64.b64decode(observed.artifacts[0].data_base64) == b"ACTUAL_BYTES"
    assert filesystem.workspace_read_text("source.txt", encoding="utf-8") == "SOURCE_AFTER"
