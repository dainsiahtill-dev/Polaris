from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from polaris.cells.roles.kernel.internal.speculation.fingerprints import (
    build_env_fingerprint,
    build_spec_key,
    normalize_args,
)
from polaris.cells.roles.kernel.internal.speculation.metrics import SpeculationMetrics
from polaris.cells.roles.kernel.internal.speculation.models import (
    ToolSpecPolicy,
)
from polaris.cells.roles.kernel.internal.speculation.registry import (
    EphemeralSpecCache,
    ShadowTaskRegistry,
)
from polaris.cells.roles.kernel.internal.speculation.resolver import SpeculationResolver
from polaris.cells.roles.kernel.internal.speculation.write_phases import WriteToolPhases
from polaris.cells.roles.kernel.internal.speculative_executor import (
    SpeculativeExecutor,
)
from polaris.cells.roles.kernel.internal.tool_batch_runtime import ToolBatchRuntime
from polaris.cells.roles.kernel.public.turn_contracts import (
    ToolCallId,
    ToolEffectType,
    ToolExecutionMode,
    ToolInvocation,
)


def _make_policy(tool_name: str = "file_exists") -> ToolSpecPolicy:
    return ToolSpecPolicy(
        tool_name=tool_name,
        side_effect="readonly",
        cost="cheap",
        cancellability="cooperative",
        reusability="adoptable",
        speculate_mode="speculative_allowed",
        timeout_ms=500,
        cache_ttl_ms=30000,
    )


class TestWriteToolPhases:
    def test_is_write_tool_detects_write_file(self) -> None:
        assert WriteToolPhases.is_write_tool("write_file") is True
        assert WriteToolPhases.is_write_tool("write-file") is True

    def test_is_write_tool_rejects_readonly(self) -> None:
        assert WriteToolPhases.is_write_tool("read_file") is False
        assert WriteToolPhases.is_write_tool("repo_rg") is False

    def test_build_prepare_invocation_maps_to_file_exists(self) -> None:
        invocation = ToolInvocation(
            call_id=ToolCallId("call_1"),
            tool_name="write_file",
            arguments={"path": "src/auth.ts", "content": "hello"},
            effect_type=ToolEffectType.WRITE,
            execution_mode=ToolExecutionMode.WRITE_SERIAL,
        )
        prepare = WriteToolPhases.build_prepare_invocation(invocation)
        assert prepare.tool_name == "file_exists"
        assert prepare.arguments.get("path") == "src/auth.ts"
        assert prepare.arguments.get("content_length") == 5
        assert prepare.execution_mode == ToolExecutionMode.READONLY_PARALLEL

    def test_build_commit_invocation_restores_write(self) -> None:
        invocation = ToolInvocation(
            call_id=ToolCallId("call_1"),
            tool_name="write_file",
            arguments={"path": "src/auth.ts", "content": "hello"},
            effect_type=ToolEffectType.WRITE,
            execution_mode=ToolExecutionMode.WRITE_SERIAL,
        )
        commit = WriteToolPhases.build_commit_invocation(invocation)
        assert commit.tool_name == "write_file"
        assert commit.arguments == {"path": "src/auth.ts", "content": "hello"}
        assert commit.effect_type == ToolEffectType.WRITE
        assert commit.execution_mode == ToolExecutionMode.WRITE_SERIAL


@pytest.fixture
def write_registry(monkeypatch: pytest.MonkeyPatch) -> ShadowTaskRegistry:
    monkeypatch.setenv("ENABLE_SPECULATIVE_EXECUTION", "true")
    executor = AsyncMock(return_value={"success": True, "result": "ok"})
    runtime = ToolBatchRuntime(executor)
    se = SpeculativeExecutor(runtime)
    return ShadowTaskRegistry(
        speculative_executor=se,
        metrics=SpeculationMetrics(),
        cache=EphemeralSpecCache(),
    )


@pytest.mark.asyncio
async def test_resolver_adopts_prepare_shadow_for_write_tool(
    write_registry: ShadowTaskRegistry,
) -> None:
    # Seed a prepare shadow
    prepare_inv = WriteToolPhases.build_prepare_invocation(
        ToolInvocation(
            call_id=ToolCallId("call_1"),
            tool_name="write_file",
            arguments={"path": "src/auth.ts", "content": "hello"},
            effect_type=ToolEffectType.WRITE,
            execution_mode=ToolExecutionMode.WRITE_SERIAL,
        )
    )
    spec_key = build_spec_key(
        tool_name=prepare_inv.tool_name,
        normalized_args=normalize_args(prepare_inv.tool_name, prepare_inv.arguments),
        env_fingerprint=build_env_fingerprint(),
    )
    record = await write_registry.start_shadow_task(
        turn_id="t_write",
        candidate_id="prepare_call_1",
        tool_name=prepare_inv.tool_name,
        normalized_args=normalize_args(prepare_inv.tool_name, prepare_inv.arguments),
        spec_key=spec_key,
        env_fingerprint=build_env_fingerprint(),
        policy=_make_policy(),
    )
    assert record.future is not None
    await record.future

    resolver = SpeculationResolver(registry=write_registry, metrics=SpeculationMetrics())
    resolution = await resolver.resolve_or_execute(
        turn_id="t_write",
        call_id="call_1",
        tool_name="write_file",
        args={"path": "src/auth.ts", "content": "hello"},
    )

    assert resolution["action"] == "adopt"
    assert resolution["result"] == "ok"


@pytest.mark.asyncio
async def test_resolver_replay_when_no_prepare_shadow() -> None:
    registry = ShadowTaskRegistry(
        speculative_executor=AsyncMock(spec=SpeculativeExecutor),
        metrics=SpeculationMetrics(),
    )
    resolver = SpeculationResolver(registry=registry, metrics=SpeculationMetrics())
    resolution = await resolver.resolve_or_execute(
        turn_id="t_write",
        call_id="call_1",
        tool_name="write_file",
        args={"path": "src/auth.ts", "content": "hello"},
    )
    assert resolution["action"] == "replay"


@pytest.mark.asyncio
async def test_write_tool_commit_never_speculative(
    write_registry: ShadowTaskRegistry,
) -> None:
    """write_file 本身的 shadow 不应存在,resolver 必须回退到 replay."""
    resolver = SpeculationResolver(registry=write_registry, metrics=SpeculationMetrics())
    resolution = await resolver.resolve_or_execute(
        turn_id="t_write",
        call_id="call_1",
        tool_name="write_file",
        args={"path": "src/auth.ts", "content": "hello"},
    )
    # 由于没有 prepare shadow,应回退到 replay
    assert resolution["action"] == "replay"
