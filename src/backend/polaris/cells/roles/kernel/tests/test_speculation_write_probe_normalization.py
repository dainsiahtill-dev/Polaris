"""Real read probes cannot become authoritative write permissions or effects."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.kernel.internal.speculation.metrics import SpeculationMetrics
from polaris.cells.roles.kernel.internal.speculation.models import ShadowTaskState
from polaris.cells.roles.kernel.internal.speculation.registry import ShadowTaskRegistry
from polaris.cells.roles.kernel.internal.speculation.resolver import SpeculationResolver
from polaris.cells.roles.kernel.internal.speculation.write_phases import WriteToolPhases
from polaris.cells.roles.kernel.internal.speculative_executor import ShadowExecutionError, SpeculativeExecutor
from polaris.cells.roles.kernel.internal.stream_shadow_engine import StreamShadowEngine
from polaris.cells.roles.kernel.internal.tool_batch_runtime import ToolBatchRuntime, ToolExecutionStatus
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
from polaris.cells.roles.kernel.internal.transaction.stream_orchestrator import drain_speculative_tasks
from polaris.cells.roles.kernel.public.turn_contracts import ToolCallId, ToolInvocation
from polaris.kernelone.llm.toolkit.executor.core import AgentAccelToolExecutor
from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry


class _ReadProbePort:
    """Actual toolkit filesystem handler; the runtime remains the shadow owner."""

    def __init__(self, workspace: Path) -> None:
        self.executor = AgentAccelToolExecutor(
            str(workspace), allowed_tools=frozenset({"file_exists"}), capability_scope=("snapshot.txt",)
        )
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.results: list[dict[str, Any]] = []
        self.errors: list[str] = []

    async def __call__(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, dict(arguments)))
        try:
            result = self.executor.execute(name, arguments)
        except Exception as exc:
            self.errors.append(f"{type(exc).__name__}:{exc}")
            raise
        self.results.append(result)
        return {"success": result.get("ok") is True, "result": result, "error": result.get("error")}


def _engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[StreamShadowEngine, ShadowTaskRegistry, _ReadProbePort]:
    monkeypatch.setenv("KERNELONE_HOME", str(tmp_path / "platform"))
    monkeypatch.setenv("ENABLE_SPECULATIVE_EXECUTION", "true")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "snapshot.txt").write_text("original\n", encoding="utf-8")
    port = _ReadProbePort(workspace)
    registry = ShadowTaskRegistry(
        speculative_executor=SpeculativeExecutor(ToolBatchRuntime(port)), metrics=SpeculationMetrics()
    )
    return StreamShadowEngine(registry._speculative_executor, registry=registry), registry, port


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["file", "path", "file_path", "filename", "file=path"])
async def test_registered_write_operands_reach_real_complete_read_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    engine, registry, port = _engine(tmp_path, monkeypatch)
    arguments = {key: "snapshot.txt", "content": "replacement\n"}
    result = await engine.speculate_tool_call(
        tool_name="write_file", arguments=arguments, call_id="native-write", turn_id="turn"
    )
    assert result["error"] is None
    shadow = registry.lookup(
        WriteToolPhases.build_prepare_shadow_key(source_call_id="native-write", arguments=arguments).shadow_key_hash
    )
    assert shadow is not None and shadow.future is not None
    await shadow.future
    assert shadow.state is ShadowTaskState.COMPLETED
    resolver = SpeculationResolver(registry=registry, metrics=SpeculationMetrics())
    adopted = await resolver.resolve_or_execute(
        turn_id="turn", call_id="native-write", tool_name="write_file", args=arguments
    )
    assert adopted == {
        "action": "adopt",
        "result": {"ok": True, "result": {"path": "snapshot.txt", "exists": True, "is_file": True, "is_dir": False}},
        "error": None,
    }
    assert adopted["result"] == port.results[0]
    assert port.calls == [("file_exists", {"path": "snapshot.txt"})]
    assert (tmp_path / "workspace/snapshot.txt").read_text(encoding="utf-8") == "original\n"
    assert arguments == {key: "snapshot.txt", "content": "replacement\n"}


@pytest.mark.asyncio
async def test_prepare_uses_actual_registered_writer_spec(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine, registry, port = _engine(tmp_path, monkeypatch)
    captured: list[str] = []
    capture = ToolSpecRegistry.capture_effective_spec

    def observe(name: str) -> Any:
        captured.append(name)
        return capture(name)

    monkeypatch.setattr(ToolSpecRegistry, "capture_effective_spec", observe)
    arguments = {"file_path": "snapshot.txt", "search": "original", "replace": "replacement"}
    result = await engine.speculate_tool_call(
        tool_name="edit_file", arguments=arguments, call_id="native-edit", turn_id="turn"
    )
    assert result["error"] is None
    shadow = registry.lookup(
        WriteToolPhases.build_prepare_shadow_key(source_call_id="native-edit", arguments=arguments).shadow_key_hash
    )
    assert shadow is not None and shadow.future is not None
    await shadow.future
    assert shadow.state is ShadowTaskState.COMPLETED
    assert "edit_file" in captured
    assert port.calls == [("file_exists", {"path": "snapshot.txt"})]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments", [{}, {"content": "x"}, {"destination": "snapshot.txt"}, {"file": ""}, {"file": "   "}, {"file": None}]
)
async def test_missing_or_unknown_operand_rejects_prepare_without_empty_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, arguments: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    engine, registry, port = _engine(tmp_path, monkeypatch)
    ledger = TurnLedger(turn_id="turn")
    task = asyncio.create_task(
        engine.speculate_tool_call(tool_name="write_file", arguments=arguments, call_id="invalid-write", turn_id="turn")
    )
    await drain_speculative_tasks([("invalid-write", task)], ledger=ledger, shadow_engine=engine)
    result = task.result()
    assert result["result"] is None
    assert result["error"]
    assert (
        registry.lookup(
            WriteToolPhases.build_prepare_shadow_key(
                source_call_id="invalid-write", arguments=arguments
            ).shadow_key_hash
        )
        is None
    )
    assert port.calls == []
    assert ledger.speculative_attempted_call_ids == {"invalid-write"}
    assert ledger.speculative_successful_call_ids == set()
    assert "write_prepare_rejected" in caplog.text
    assert "invalid-write" in caplog.text


@pytest.mark.parametrize("tool_name", ["unknown_writer", "file_exists"])
def test_prepare_cannot_infer_file_from_unknown_or_readonly_source(tool_name: str) -> None:
    with pytest.raises(ValueError):
        WriteToolPhases.prepare_shadow_normalized_args({"file": "snapshot.txt"}, tool_name=tool_name)


@pytest.mark.asyncio
@pytest.mark.parametrize("unsafe", ["../outside.txt", "/etc/passwd", "link.txt"])
async def test_normalized_probe_keeps_existing_workspace_denials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unsafe: str
) -> None:
    engine, registry, port = _engine(tmp_path, monkeypatch)
    outside = tmp_path / "outside.txt"
    outside.write_text("private\n", encoding="utf-8")
    (tmp_path / "workspace/link.txt").symlink_to(outside)
    arguments = {"file": unsafe, "content": "forbidden\n"}
    await engine.speculate_tool_call(
        tool_name="write_file", arguments=arguments, call_id="unsafe-write", turn_id="turn"
    )
    shadow = registry.lookup(
        WriteToolPhases.build_prepare_shadow_key(source_call_id="unsafe-write", arguments=arguments).shadow_key_hash
    )
    assert shadow is not None and shadow.future is not None
    with pytest.raises(ShadowExecutionError):
        await shadow.future
    assert shadow.state is ShadowTaskState.FAILED
    assert port.calls == [("file_exists", {"path": unsafe})]
    if port.results:
        assert port.results[0]["ok"] is False
        assert "Missing or invalid path" not in str(port.results[0].get("error", ""))
    else:
        assert port.errors
        assert "symlink" in port.errors[0].lower()
    assert (tmp_path / "workspace/snapshot.txt").read_text(encoding="utf-8") == "original\n"
    assert outside.read_text(encoding="utf-8") == "private\n"


@pytest.mark.asyncio
async def test_complete_read_is_neither_write_receipt_nor_deo_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, registry, port = _engine(tmp_path, monkeypatch)
    content = "  雪\r\nline two\n\n"
    arguments = {"file": "snapshot.txt", "content": content}
    before = hashlib.sha256(json.dumps(arguments, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    await engine.speculate_tool_call(
        tool_name="write_file", arguments=arguments, call_id="protected-write", turn_id="turn"
    )
    shadow = registry.lookup(
        WriteToolPhases.build_prepare_shadow_key(source_call_id="protected-write", arguments=arguments).shadow_key_hash
    )
    assert shadow is not None and shadow.future is not None
    await shadow.future
    resolved = await SpeculationResolver(registry=registry, metrics=SpeculationMetrics()).resolve_or_execute(
        turn_id="turn", call_id="protected-write", tool_name="write_file", args=arguments
    )
    assert resolved["result"]["result"]["exists"] is True
    assert "effect_receipt" not in resolved["result"]
    invocation = ToolInvocation(call_id=ToolCallId("protected-write"), tool_name="write_file", arguments=arguments)
    commit = WriteToolPhases.build_commit_invocation(invocation)
    assert commit.arguments["content"].encode("utf-8") == content.encode("utf-8")
    assert (
        hashlib.sha256(json.dumps(commit.arguments, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        == before
    )
    denied = await ToolBatchRuntime(port)._execute_directed_effect(commit)
    assert denied.status is ToolExecutionStatus.ERROR
    assert denied.error == "directed_effect_prepared_context_unavailable"
    assert denied.directed_effect_claim_status == "not_claimed"
    with pytest.raises(ValueError, match="required directed-effect execution"):
        ToolBatchRuntime(port, directed_effect_required=True)
    with pytest.raises(ValueError, match="deo_tool_classification_mismatch: effect_type"):
        await registry._speculative_executor.execute_speculative("write_file", arguments, timeout_ms=500)
    assert port.calls == [("file_exists", {"path": "snapshot.txt"})]
    assert (tmp_path / "workspace/snapshot.txt").read_text(encoding="utf-8") == "original\n"
