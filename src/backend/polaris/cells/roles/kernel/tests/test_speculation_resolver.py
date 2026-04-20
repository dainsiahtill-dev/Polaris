from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from polaris.cells.roles.kernel.internal.speculation.metrics import SpeculationMetrics
from polaris.cells.roles.kernel.internal.speculation.models import ShadowTaskRecord, ShadowTaskState
from polaris.cells.roles.kernel.internal.speculation.registry import ShadowTaskRegistry
from polaris.cells.roles.kernel.internal.speculation.resolver import SpeculationResolver


@pytest.fixture
def mock_registry() -> AsyncMock:
    return AsyncMock(spec=ShadowTaskRegistry)


@pytest.fixture
def resolver(mock_registry: AsyncMock) -> SpeculationResolver:
    return SpeculationResolver(registry=mock_registry, metrics=SpeculationMetrics())


@pytest.mark.asyncio
async def test_resolve_miss_returns_replay(resolver: SpeculationResolver, mock_registry: AsyncMock) -> None:
    mock_registry.lookup.return_value = None
    result = await resolver.resolve_or_execute(turn_id="t1", call_id="c1", tool_name="read_file", args={"path": "a.py"})
    assert result["action"] == "replay"
    assert result["result"] is None
    assert result["error"] is None


@pytest.mark.asyncio
async def test_resolve_completed_adopts(resolver: SpeculationResolver, mock_registry: AsyncMock) -> None:
    task = ShadowTaskRecord(
        task_id="task_1",
        origin_turn_id="t1",
        origin_candidate_id="cand_1",
        tool_name="read_file",
        normalized_args={},
        spec_key="spec_1",
        env_fingerprint="fp",
        policy_snapshot=AsyncMock(),  # type: ignore[arg-type]
        state=ShadowTaskState.COMPLETED,
    )
    mock_registry.lookup.return_value = task
    mock_registry.adopt.return_value = {"result": "adopted_data"}

    result = await resolver.resolve_or_execute(turn_id="t1", call_id="c1", tool_name="read_file", args={"path": "a.py"})
    assert result["action"] == "adopt"
    assert result["result"] == {"result": "adopted_data"}
    assert result["error"] is None
    mock_registry.adopt.assert_awaited_once_with("task_1", "c1")


@pytest.mark.asyncio
async def test_resolve_starting_joins(resolver: SpeculationResolver, mock_registry: AsyncMock) -> None:
    task = ShadowTaskRecord(
        task_id="task_1",
        origin_turn_id="t1",
        origin_candidate_id="cand_1",
        tool_name="read_file",
        normalized_args={},
        spec_key="spec_1",
        env_fingerprint="fp",
        policy_snapshot=AsyncMock(),  # type: ignore[arg-type]
        state=ShadowTaskState.STARTING,
    )
    mock_registry.lookup.return_value = task
    mock_registry.join.return_value = {"result": "joined_data"}

    result = await resolver.resolve_or_execute(turn_id="t1", call_id="c1", tool_name="read_file", args={"path": "a.py"})
    assert result["action"] == "join"
    assert result["result"] == {"result": "joined_data"}
    mock_registry.join.assert_awaited_once_with("task_1", "c1")


@pytest.mark.asyncio
async def test_resolve_running_joins(resolver: SpeculationResolver, mock_registry: AsyncMock) -> None:
    task = ShadowTaskRecord(
        task_id="task_1",
        origin_turn_id="t1",
        origin_candidate_id="cand_1",
        tool_name="read_file",
        normalized_args={},
        spec_key="spec_1",
        env_fingerprint="fp",
        policy_snapshot=AsyncMock(),  # type: ignore[arg-type]
        state=ShadowTaskState.RUNNING,
    )
    mock_registry.lookup.return_value = task
    mock_registry.join.return_value = {"result": "joined_data"}

    result = await resolver.resolve_or_execute(turn_id="t1", call_id="c1", tool_name="read_file", args={"path": "a.py"})
    assert result["action"] == "join"


@pytest.mark.asyncio
async def test_resolve_failed_replays(resolver: SpeculationResolver, mock_registry: AsyncMock) -> None:
    task = ShadowTaskRecord(
        task_id="task_1",
        origin_turn_id="t1",
        origin_candidate_id="cand_1",
        tool_name="read_file",
        normalized_args={},
        spec_key="spec_1",
        env_fingerprint="fp",
        policy_snapshot=AsyncMock(),  # type: ignore[arg-type]
        state=ShadowTaskState.FAILED,
    )
    mock_registry.lookup.return_value = task
    result = await resolver.resolve_or_execute(turn_id="t1", call_id="c1", tool_name="read_file", args={"path": "a.py"})
    assert result["action"] == "replay"
    assert result["result"] is None


@pytest.mark.asyncio
async def test_resolve_cancelled_replays(resolver: SpeculationResolver, mock_registry: AsyncMock) -> None:
    task = ShadowTaskRecord(
        task_id="task_1",
        origin_turn_id="t1",
        origin_candidate_id="cand_1",
        tool_name="read_file",
        normalized_args={},
        spec_key="spec_1",
        env_fingerprint="fp",
        policy_snapshot=AsyncMock(),  # type: ignore[arg-type]
        state=ShadowTaskState.CANCELLED,
    )
    mock_registry.lookup.return_value = task
    result = await resolver.resolve_or_execute(turn_id="t1", call_id="c1", tool_name="read_file", args={"path": "a.py"})
    assert result["action"] == "replay"


@pytest.mark.asyncio
async def test_resolve_expired_replays(resolver: SpeculationResolver, mock_registry: AsyncMock) -> None:
    task = ShadowTaskRecord(
        task_id="task_1",
        origin_turn_id="t1",
        origin_candidate_id="cand_1",
        tool_name="read_file",
        normalized_args={},
        spec_key="spec_1",
        env_fingerprint="fp",
        policy_snapshot=AsyncMock(),  # type: ignore[arg-type]
        state=ShadowTaskState.EXPIRED,
    )
    mock_registry.lookup.return_value = task
    result = await resolver.resolve_or_execute(turn_id="t1", call_id="c1", tool_name="read_file", args={"path": "a.py"})
    assert result["action"] == "replay"


@pytest.mark.asyncio
async def test_resolve_abandoned_replays(resolver: SpeculationResolver, mock_registry: AsyncMock) -> None:
    task = ShadowTaskRecord(
        task_id="task_1",
        origin_turn_id="t1",
        origin_candidate_id="cand_1",
        tool_name="read_file",
        normalized_args={},
        spec_key="spec_1",
        env_fingerprint="fp",
        policy_snapshot=AsyncMock(),  # type: ignore[arg-type]
        state=ShadowTaskState.ABANDONED,
    )
    mock_registry.lookup.return_value = task
    result = await resolver.resolve_or_execute(turn_id="t1", call_id="c1", tool_name="read_file", args={"path": "a.py"})
    assert result["action"] == "replay"


@pytest.mark.asyncio
async def test_resolve_adopt_failure_replays_with_error(
    resolver: SpeculationResolver, mock_registry: AsyncMock
) -> None:
    task = ShadowTaskRecord(
        task_id="task_1",
        origin_turn_id="t1",
        origin_candidate_id="cand_1",
        tool_name="read_file",
        normalized_args={},
        spec_key="spec_1",
        env_fingerprint="fp",
        policy_snapshot=AsyncMock(),  # type: ignore[arg-type]
        state=ShadowTaskState.COMPLETED,
    )
    mock_registry.lookup.return_value = task
    mock_registry.adopt.side_effect = RuntimeError("adopt_failed")

    result = await resolver.resolve_or_execute(turn_id="t1", call_id="c1", tool_name="read_file", args={"path": "a.py"})
    assert result["action"] == "replay"
    assert "adopt_failed" in str(result["error"])


@pytest.mark.asyncio
async def test_resolve_join_failure_replays_with_error(resolver: SpeculationResolver, mock_registry: AsyncMock) -> None:
    task = ShadowTaskRecord(
        task_id="task_1",
        origin_turn_id="t1",
        origin_candidate_id="cand_1",
        tool_name="read_file",
        normalized_args={},
        spec_key="spec_1",
        env_fingerprint="fp",
        policy_snapshot=AsyncMock(),  # type: ignore[arg-type]
        state=ShadowTaskState.RUNNING,
    )
    mock_registry.lookup.return_value = task
    mock_registry.join.side_effect = RuntimeError("join_failed")

    result = await resolver.resolve_or_execute(turn_id="t1", call_id="c1", tool_name="read_file", args={"path": "a.py"})
    assert result["action"] == "replay"
    assert "join_failed" in str(result["error"])


@pytest.mark.asyncio
async def test_resolve_unexpected_state_replays(resolver: SpeculationResolver, mock_registry: AsyncMock) -> None:
    task = ShadowTaskRecord(
        task_id="task_1",
        origin_turn_id="t1",
        origin_candidate_id="cand_1",
        tool_name="read_file",
        normalized_args={},
        spec_key="spec_1",
        env_fingerprint="fp",
        policy_snapshot=AsyncMock(),  # type: ignore[arg-type]
        state=ShadowTaskState.CREATED,
    )
    mock_registry.lookup.return_value = task
    result = await resolver.resolve_or_execute(turn_id="t1", call_id="c1", tool_name="read_file", args={"path": "a.py"})
    assert result["action"] == "replay"
    assert result["error"] is None
