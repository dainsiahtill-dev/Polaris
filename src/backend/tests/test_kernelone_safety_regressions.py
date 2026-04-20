from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.policy.protocol import PolicyContractError, PolicyRuntime
from polaris.cells.runtime.task_runtime.public.task_board_contract import TaskBoard
from polaris.kernelone.events.file_event_broadcaster import (
    broadcast_file_written,
    replace_in_file_with_broadcast,
)
from polaris.kernelone.events.message_bus import Message, MessageBus, MessageType
from polaris.kernelone.memory.integration import (
    get_memory_store,
    init_anthropomorphic_modules,
)
from polaris.kernelone.prompts.catalog import PatchBlock, apply_patch_blocks
from polaris.kernelone.runtime.shared_types import FILE_BLOCK_RE
from polaris.kernelone.storage.layout import StorageLayout
from polaris.kernelone.workflow.contracts import WorkflowContract


def test_storage_layout_rejects_artifact_path_traversal(tmp_path: Path) -> None:
    layout = StorageLayout(tmp_path, tmp_path / "runtime-cache")
    with pytest.raises(ValueError):
        layout.resolve_artifact_path("workspace/../../etc/passwd")


def test_shared_types_file_block_regex_matches_real_newline_blocks() -> None:
    content = '<file path="hello.py">\nprint("ok")\n</file>'
    match = FILE_BLOCK_RE.search(content)
    assert match is not None
    assert match.group(1) == "hello.py"
    assert 'print("ok")' in match.group(2)


def test_policy_allow_implementation_requires_approval(tmp_path: Path) -> None:
    runtime = PolicyRuntime.create(str(tmp_path), run_id="run-approve-guard")
    runtime.hp_start_run("goal", ["acceptance"])
    runtime.hp_create_blueprint("workspace/docs/plan.md", "S1", {"token": 1000})
    with pytest.raises(PolicyContractError, match="approval"):
        runtime.hp_allow_implementation("token-1")


def test_apply_patch_blocks_rejects_workspace_escape(tmp_path: Path) -> None:
    target = tmp_path / "inside.py"
    target.write_text("print('inside')\n", encoding="utf-8")

    result = apply_patch_blocks(
        [PatchBlock(file="../outside.py", search="", replace="print('x')\n")],
        str(tmp_path),
        strict=False,
    )
    assert result.ok is False
    assert result.changed_files == []
    assert result.applied_count == 0
    assert result.skipped_count == 1
    assert any("Invalid patch target" in item for item in result.errors)


@pytest.mark.asyncio
async def test_message_bus_records_direct_queue_drops() -> None:
    bus = MessageBus()
    queue = await bus.register_actor("actor-1")
    for _ in range(queue.maxsize):
        queue.put_nowait(
            Message(
                type=MessageType.TASK_STARTED,
                sender="seed",
                recipient="actor-1",
                payload={},
            )
        )

    await bus.send(MessageType.TASK_STARTED, "sender", "actor-1", {"id": "overflow"})
    assert bus.dropped_messages == 1


def test_task_board_get_returns_copy_not_live_reference(tmp_path: Path) -> None:
    board = TaskBoard(str(tmp_path))
    created = board.create(subject="original")
    fetched = board.get(created.id)
    assert fetched is not None
    fetched.subject = "mutated"
    reloaded = board.get(created.id)
    assert reloaded is not None
    assert reloaded.subject == "original"


def test_workflow_contract_cycle_check_handles_long_chains_without_recursion_error() -> None:
    task_count = 1100
    tasks = []
    for index in range(task_count):
        task_id = f"t{index}"
        depends_on = [] if index == 0 else [f"t{index - 1}"]
        tasks.append(
            {
                "id": task_id,
                "type": "activity",
                "handler": "noop_handler",
                "depends_on": depends_on,
                "input": {"i": index},
            }
        )
    payload = {"orchestration": {"tasks": tasks}}
    contract = WorkflowContract.from_payload(payload)
    assert contract.task_count == task_count


def test_memory_integration_keeps_stores_isolated_per_workspace(tmp_path: Path) -> None:
    ws_a = tmp_path / "ws-a"
    ws_b = tmp_path / "ws-b"
    ws_a.mkdir(parents=True, exist_ok=True)
    ws_b.mkdir(parents=True, exist_ok=True)

    init_anthropomorphic_modules(str(ws_a))
    store_a = get_memory_store()
    init_anthropomorphic_modules(str(ws_b))
    store_b = get_memory_store()
    init_anthropomorphic_modules(str(ws_a))
    store_a_again = get_memory_store()

    assert store_a is not None
    assert store_b is not None
    assert store_a_again is not None
    assert store_a is not store_b
    assert store_a_again is store_a


def test_replace_in_file_with_broadcast_honors_count(tmp_path: Path) -> None:
    target = tmp_path / "demo.txt"
    target.write_text("a a a", encoding="utf-8")
    result = replace_in_file_with_broadcast(
        workspace=str(tmp_path),
        file_path=str(target),
        old_text="a",
        new_text="x",
        count=1,
        message_bus=None,
    )
    assert result["ok"] is True
    assert result["replacements"] == 1
    assert target.read_text(encoding="utf-8") == "x a a"


def test_file_event_broadcast_without_running_loop_is_safe_drop() -> None:
    class _DummyBus:
        async def broadcast(self, *_args, **_kwargs):
            return None

    ok = broadcast_file_written(
        file_path="src/demo.py",
        operation="modify",
        content_size=10,
        patch="+x",
        message_bus=_DummyBus(),
    )
    assert ok is False
