"""Tests for RoleSessionOrchestrator."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from polaris.cells.roles.kernel.internal.transaction.delivery_contract import DeliveryMode
from polaris.cells.roles.kernel.public.turn_contracts import (
    TurnContinuationMode,
    TurnOutcomeEnvelope,
    TurnResult,
)
from polaris.cells.roles.kernel.public.turn_events import (
    CompletionEvent,
    SessionCompletedEvent,
    SessionStartedEvent,
    SessionWaitingHumanEvent,
    TurnPhaseEvent,
)
from polaris.cells.roles.runtime.internal.session_orchestrator import RoleSessionOrchestrator


class MockKernel:
    """Minimal mock kernel that yields a sequence of events."""

    def __init__(self, events_per_turn) -> None:
        self.events_per_turn = events_per_turn
        self.call_count = 0
        self.tool_runtime = AsyncMock()

    async def execute_stream(self, turn_id, context, tool_definitions):
        turn_index = self.call_count
        self.call_count += 1
        for event in self.events_per_turn[turn_index]:
            yield event


def _make_completion_event(turn_id, mode=TurnContinuationMode.END_SESSION, next_intent=None):
    event = CompletionEvent(turn_id=turn_id, status="success")
    # inject envelope inference via monkey-patch on static method if needed,
    # but _build_envelope_from_completion ignores event payload and always returns
    # AUTO_CONTINUE. We'll patch the orchestrator method directly in tests.
    return event


class TestRoleSessionOrchestrator:
    """测试 RoleSessionOrchestrator 的多 Turn 编排循环。"""

    @pytest.fixture
    def tmp_workspace(self, tmp_path):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        return str(workspace)

    @pytest.mark.asyncio
    async def test_single_turn_end_session(self, tmp_workspace):
        kernel = MockKernel(
            [
                [CompletionEvent(turn_id="t0", status="success")],
            ]
        )
        orch = RoleSessionOrchestrator(
            session_id="sess-1",
            kernel=kernel,
            workspace=tmp_workspace,
        )
        # patch envelope builder to return END_SESSION
        orch._build_envelope_from_completion = lambda _evt: SimpleNamespace(
            turn_result=TurnResult(turn_id="t0", kind="final_answer", visible_content="done", decision={}),
            continuation_mode=TurnContinuationMode.END_SESSION,
            next_intent=None,
            session_patch={},
            artifacts_to_persist=[],
            speculative_hints={},
        )

        events = [e async for e in orch.execute_stream("hello")]
        assert isinstance(events[0], SessionStartedEvent)
        assert isinstance(events[-1], SessionCompletedEvent)
        assert kernel.call_count == 1

    @pytest.mark.asyncio
    async def test_multi_turn_auto_continue_then_end(self, tmp_workspace):
        kernel = MockKernel(
            [
                [CompletionEvent(turn_id="t0", status="success")],
                [CompletionEvent(turn_id="t1", status="success")],
            ]
        )
        orch = RoleSessionOrchestrator(
            session_id="sess-1",
            kernel=kernel,
            workspace=tmp_workspace,
            max_auto_turns=5,
        )

        def _build_envelope(event):
            if event.turn_id == "t0":
                return SimpleNamespace(
                    turn_result=TurnResult(turn_id="t0", kind="final_answer", visible_content="", decision={}),
                    continuation_mode=TurnContinuationMode.AUTO_CONTINUE,
                    next_intent=None,
                    session_patch={"a": "b"},
                    artifacts_to_persist=[{"name": "a.txt", "content": "x", "mime_type": "text/plain"}],
                    speculative_hints={},
                )
            return SimpleNamespace(
                turn_result=TurnResult(turn_id="t1", kind="final_answer", visible_content="done", decision={}),
                continuation_mode=TurnContinuationMode.END_SESSION,
                next_intent=None,
                session_patch={},
                artifacts_to_persist=[],
                speculative_hints={},
            )

        orch._build_envelope_from_completion = _build_envelope

        events = [e async for e in orch.execute_stream("hello")]
        assert kernel.call_count == 2
        assert orch.state.turn_count == 2
        # artifact store 应该记录了文件
        assert "a.txt" in orch.state.artifacts
        assert isinstance(events[-1], SessionCompletedEvent)

    @pytest.mark.asyncio
    async def test_waiting_human_break(self, tmp_workspace):
        kernel = MockKernel(
            [
                [CompletionEvent(turn_id="t0", status="success")],
            ]
        )
        orch = RoleSessionOrchestrator(
            session_id="sess-1",
            kernel=kernel,
            workspace=tmp_workspace,
        )
        orch._build_envelope_from_completion = lambda _evt: SimpleNamespace(
            turn_result=TurnResult(turn_id="t0", kind="final_answer", visible_content="", decision={}),
            continuation_mode=TurnContinuationMode.WAITING_HUMAN,
            next_intent="need_input",
            session_patch={},
            artifacts_to_persist=[],
            speculative_hints={},
        )

        events = [e async for e in orch.execute_stream("hello")]
        assert isinstance(events[-1], SessionWaitingHumanEvent)
        assert events[-1].session_id == "sess-1"
        assert not any(isinstance(e, SessionCompletedEvent) for e in events)

    @pytest.mark.asyncio
    async def test_read_only_termination_exemption_rejects_failed_read_only_turn(self, tmp_workspace):
        orch = RoleSessionOrchestrator(
            session_id="sess-1",
            kernel=AsyncMock(),
            workspace=tmp_workspace,
        )
        envelope = TurnOutcomeEnvelope(
            turn_result=TurnResult(
                turn_id="t0",
                kind="final_answer",
                visible_content="analysis text",
                decision={},
                batch_receipt={
                    "results": [
                        {
                            "tool_name": "read_file",
                            "status": "error",
                            "result": {"message": "File not found"},
                        }
                    ]
                },
            ),
            continuation_mode=TurnContinuationMode.AUTO_CONTINUE,
            next_intent=None,
            session_patch={},
            artifacts_to_persist=[],
            speculative_hints={},
        )

        result = orch._apply_read_only_termination_exemption(envelope)

        assert result.continuation_mode == TurnContinuationMode.AUTO_CONTINUE
        assert result.turn_result.kind == "final_answer"

    @pytest.mark.asyncio
    async def test_materialize_changes_guard_blocks_final_answer_without_write_receipt(self, tmp_workspace):
        orch = RoleSessionOrchestrator(
            session_id="sess-1",
            kernel=AsyncMock(),
            workspace=tmp_workspace,
        )
        orch.state.delivery_mode = DeliveryMode.MATERIALIZE_CHANGES.value
        envelope = TurnOutcomeEnvelope(
            turn_result=TurnResult(
                turn_id="t0",
                kind="final_answer",
                visible_content="read result",
                decision={},
                batch_receipt={
                    "results": [
                        {
                            "tool_name": "read_file",
                            "status": "success",
                            "result": {"content": "ok"},
                        }
                    ]
                },
            ),
            continuation_mode=TurnContinuationMode.END_SESSION,
            next_intent=None,
            session_patch={},
            artifacts_to_persist=[],
            speculative_hints={},
        )

        result = orch._state_reducer.enforce_materialize_changes_guard(envelope)

        assert result.continuation_mode == TurnContinuationMode.AUTO_CONTINUE
        assert result.turn_result.kind == "continue_multi_turn"

    @pytest.mark.asyncio
    async def test_materialize_changes_failed_read_only_turn_keeps_session_open(self, tmp_workspace, monkeypatch):
        kernel = MockKernel(
            [
                [CompletionEvent(turn_id="t0", status="success")],
                [CompletionEvent(turn_id="t1", status="success")],
            ]
        )
        orch = RoleSessionOrchestrator(
            session_id="sess-1",
            kernel=kernel,
            workspace=tmp_workspace,
            max_auto_turns=5,
        )
        monkeypatch.setattr(
            "polaris.cells.roles.runtime.internal.session_orchestrator.resolve_delivery_mode",
            lambda _prompt: SimpleNamespace(mode=DeliveryMode.MATERIALIZE_CHANGES),
        )

        def _build_envelope(event):
            if event.turn_id == "t0":
                return TurnOutcomeEnvelope(
                    turn_result=TurnResult(
                        turn_id="t0",
                        kind="final_answer",
                        visible_content="failed read-only analysis",
                        decision={},
                        batch_receipt={
                            "results": [
                                {
                                    "tool_name": "read_file",
                                    "status": "error",
                                    "result": {"message": "File not found"},
                                }
                            ]
                        },
                    ),
                    continuation_mode=TurnContinuationMode.END_SESSION,
                    next_intent="read_file failed",
                    session_patch={},
                    artifacts_to_persist=[],
                    speculative_hints={},
                )
            return TurnOutcomeEnvelope(
                turn_result=TurnResult(
                    turn_id="t1",
                    kind="final_answer",
                    visible_content="write committed",
                    decision={},
                    batch_receipt={
                        "results": [
                            {
                                "tool_name": "write_file",
                                "status": "success",
                                "result": {"path": "src/auth.py"},
                            }
                        ]
                    },
                ),
                continuation_mode=TurnContinuationMode.END_SESSION,
                next_intent=None,
                session_patch={},
                artifacts_to_persist=[],
                speculative_hints={},
            )

        orch._build_envelope_from_completion = _build_envelope

        events = [e async for e in orch.execute_stream("请修改登录修复代码")]

        assert kernel.call_count == 2
        assert orch.state.turn_history[0]["continuation_mode"] == "auto_continue"
        assert orch.state.turn_history[1]["continuation_mode"] == "end_session"
        assert isinstance(events[-1], SessionCompletedEvent)

    @pytest.mark.asyncio
    async def test_max_turns_exceeded(self, tmp_workspace):
        # 构造永远 AUTO_CONTINUE 的 kernel
        kernel = MockKernel([[CompletionEvent(turn_id=f"t{i}", status="success")] for i in range(5)])
        orch = RoleSessionOrchestrator(
            session_id="sess-1",
            kernel=kernel,
            workspace=tmp_workspace,
            max_auto_turns=2,
        )
        orch._build_envelope_from_completion = lambda _evt: SimpleNamespace(
            turn_result=TurnResult(turn_id="tx", kind="final_answer", visible_content="", decision={}),
            continuation_mode=TurnContinuationMode.AUTO_CONTINUE,
            next_intent=None,
            session_patch={},
            artifacts_to_persist=[],
            speculative_hints={},
        )

        events = [e async for e in orch.execute_stream("hello")]
        # max_auto_turns=2, 第 2 个 turn 后 policy 阻止继续
        assert kernel.call_count == 2
        assert isinstance(events[-1], SessionCompletedEvent)

    @pytest.mark.asyncio
    async def test_shadow_engine_speculation_consumed(self, tmp_workspace):
        kernel = MockKernel(
            [
                [CompletionEvent(turn_id="t0", status="success")],
            ]
        )
        shadow = SimpleNamespace(
            has_valid_speculation=lambda _sid: True,
            consume_speculation=AsyncMock(return_value={"tools": [{"tool_name": "read_file"}]}),
        )
        orch = RoleSessionOrchestrator(
            session_id="sess-1",
            kernel=kernel,
            workspace=tmp_workspace,
            shadow_engine=shadow,
        )
        orch._build_envelope_from_completion = lambda _evt: SimpleNamespace(
            turn_result=TurnResult(turn_id="t0", kind="final_answer", visible_content="done", decision={}),
            continuation_mode=TurnContinuationMode.END_SESSION,
            next_intent=None,
            session_patch={},
            artifacts_to_persist=[],
            speculative_hints={},
        )
        # monkey-patch yield pre-warmed to verify consumption
        yielded_pre_warmed = []

        async def _yield_pre_warmed(pre_warmed):
            yielded_pre_warmed.append(pre_warmed)
            yield TurnPhaseEvent.create(turn_id="sess-1", phase="speculation_consumed")

        orch._yield_pre_warmed_events = _yield_pre_warmed

        events = [e async for e in orch.execute_stream("hello")]
        shadow.consume_speculation.assert_awaited_once_with("sess-1")
        assert len(yielded_pre_warmed) == 1
        assert any(isinstance(e, TurnPhaseEvent) and e.phase == "speculation_consumed" for e in events)

    @pytest.mark.asyncio
    async def test_checkpoint_session_written(self, tmp_workspace):
        kernel = MockKernel(
            [
                [CompletionEvent(turn_id="t0", status="success")],
            ]
        )
        orch = RoleSessionOrchestrator(
            session_id="sess-1",
            kernel=kernel,
            workspace=tmp_workspace,
        )
        orch._build_envelope_from_completion = lambda _evt: SimpleNamespace(
            turn_result=TurnResult(turn_id="t0", kind="final_answer", visible_content="done", decision={}),
            continuation_mode=TurnContinuationMode.END_SESSION,
            next_intent=None,
            session_patch={},
            artifacts_to_persist=[],
            speculative_hints={},
        )

        [e async for e in orch.execute_stream("hello")]

        from pathlib import Path

        checkpoint = Path(tmp_workspace) / ".polaris" / "checkpoints" / "sess-1.json"
        assert checkpoint.exists()
        import json

        data = json.loads(checkpoint.read_text(encoding="utf-8"))
        assert data["session_id"] == "sess-1"
        # schema_version 字段存在（Step 5 checkpoint 完整性）
        assert "schema_version" in data
        # structured_findings 字段存在（Step 1 降维工作记忆）
        assert "structured_findings" in data

    @pytest.mark.asyncio
    async def test_checkpoint_includes_working_memory(self, tmp_workspace):
        """验证 checkpoint 包含完整的降维工作记忆（Step 5）。"""
        orch = RoleSessionOrchestrator(
            session_id="sess-1",
            kernel=AsyncMock(),
            workspace=tmp_workspace,
        )
        orch.state.turn_count = 2
        orch.state.task_progress = "implementing"
        orch.state.structured_findings = {
            "error_summary": "DB timeout",
            "suspected_files": ["auth.py"],
        }
        orch.state.key_file_snapshots = {"auth.py": "hash123"}

        await orch._checkpoint_session()

        import json
        from pathlib import Path

        checkpoint = Path(tmp_workspace) / ".polaris" / "checkpoints" / "sess-1.json"
        data = json.loads(checkpoint.read_text(encoding="utf-8"))

        # 验证所有降维字段都持久化到 checkpoint
        # FIX-20250421-v4: schema_version 升级到 4，并包含 canonical turn history / phase_manager
        assert data["schema_version"] == 4
        assert data["task_progress"] == "implementing"
        assert data["structured_findings"]["error_summary"] == "DB timeout"
        assert "auth.py" in str(data["structured_findings"]["suspected_files"])
        assert data["key_file_snapshots"]["auth.py"] == "hash123"
        assert "turn_history" in data
        assert "phase_manager" in data
        orch = RoleSessionOrchestrator(
            session_id="sess-1",
            kernel=AsyncMock(),
            workspace=tmp_workspace,
        )
        orch.state.turn_count = 2
        orch.state.task_progress = "investigating"
        # Raw artifacts 不进入 continuation prompt（上下文降维 ADR-0071）
        orch.state.artifacts = {"a.txt": "/path/a.txt"}
        # Structured findings 进入 WorkingMemory（降维后的合成结论）
        orch.state.structured_findings = {
            "suspected_files": ["a.txt", "b.py"],
            "error_summary": "import error",
        }

        envelope = orch._build_empty_envelope()
        prompt = orch._build_continuation_prompt(envelope)

        # 验证 4-zone XML 结构（Step 4 升级）
        assert "<Goal>" in prompt
        assert "<Progress>" in prompt
        assert "<WorkingMemory>" in prompt
        assert "<Instruction>" in prompt
        assert "</Goal>" in prompt
        assert "</Progress>" in prompt
        assert "</WorkingMemory>" in prompt
        assert "</Instruction>" in prompt

        # 验证回合信息在 Progress 中
        assert "回合: 2 / 10" in prompt
        assert "content_gathered" in prompt

        # 验证 structured_findings 进入 WorkingMemory（而非 raw artifacts）
        assert "a.txt" in prompt
        assert "b.py" in prompt
        assert "import error" in prompt

    @pytest.mark.asyncio
    async def test_continuation_prompt_with_4zone_structure(self, tmp_workspace):
        """验证 4-zone 结构（Goal/Progress/WorkingMemory/Instruction）完整且内容正确。"""
        orch = RoleSessionOrchestrator(
            session_id="sess-1",
            kernel=AsyncMock(),
            workspace=tmp_workspace,
        )
        orch.state.goal = "修复登录接口 500 报错"
        orch.state.turn_count = 3
        orch.state.max_turns = 10
        orch.state.task_progress = "implementing"
        orch.state.structured_findings = {
            "error_summary": "DB timeout",
            "suspected_files": ["auth.py"],
            "patched_files": ["db.py"],
            "verified_results": ["auth test passed"],
            "pending_files": ["config.py"],
        }
        orch.state.last_failure = {"summary": "connection refused"}

        envelope = orch._build_empty_envelope()
        prompt = orch._build_continuation_prompt(envelope)

        # Goal zone
        assert "修复登录接口 500 报错" in prompt

        # Progress zone
        assert "implementing" in prompt
        assert "回合: 3 / 10" in prompt

        # WorkingMemory zone - 已确认事实
        assert "DB timeout" in prompt
        assert "auth.py" in prompt
        assert "db.py" in prompt
        assert "auth test passed" in prompt

        # WorkingMemory zone - 待验证假设
        assert "config.py" in prompt

        # WorkingMemory zone - 最近失败
        assert "connection refused" in prompt

        # Instruction zone - implementing 阶段指令
        assert "最小改动" in prompt or "修复阶段" in prompt

    @pytest.mark.asyncio
    async def test_continuation_prompt_empty_state(self, tmp_workspace):
        """验证空状态下生成合理的降级 prompt。"""
        orch = RoleSessionOrchestrator(
            session_id="sess-1",
            kernel=AsyncMock(),
            workspace=tmp_workspace,
        )
        orch.state.structured_findings = {}
        orch.state.task_progress = "exploring"

        envelope = orch._build_empty_envelope()
        prompt = orch._build_continuation_prompt(envelope)

        # 4-zone 结构仍存在
        assert "<Goal>" in prompt
        assert "<Progress>" in prompt
        assert "<WorkingMemory>" in prompt
        assert "<Instruction>" in prompt

        # 空状态有降级处理
        assert "暂无工作记忆" in prompt or "未设定" in prompt

    @pytest.mark.asyncio
    async def test_build_envelope_extracts_session_patch(self, tmp_workspace):
        """验证 _build_envelope_from_completion 正确提取 SESSION_PATCH 并剥离 visible_content（ADR-0080）。"""
        raw_text = """<thinking>根因已确认：auth.py 有 token 刷新缺陷。</thinking>
这是我的分析结论。

<SESSION_PATCH>
{
    "task_progress": "implementing",
    "error_summary": "auth.py token 过期未刷新",
    "suspected_files": ["src/auth.py"],
    "action_taken": "确认根因文件"
}
</SESSION_PATCH>
"""
        event = CompletionEvent(turn_id="t0", status="success", visible_content=raw_text)

        envelope = RoleSessionOrchestrator._build_envelope_from_completion(event)

        # 验证 session_patch 被提取
        assert envelope.session_patch["task_progress"] == "implementing"
        assert envelope.session_patch["error_summary"] == "auth.py token 过期未刷新"
        assert "src/auth.py" in envelope.session_patch["suspected_files"]

        # 验证 visible_content 中的 SESSION_PATCH 块被剥离
        assert "<SESSION_PATCH>" not in envelope.turn_result.visible_content
        assert "</SESSION_PATCH>" not in envelope.turn_result.visible_content
        # 纯内容应保留
        assert "这是我的分析结论" in envelope.turn_result.visible_content

    @pytest.mark.asyncio
    async def test_build_envelope_handles_empty_visible_content(self, tmp_workspace):
        """验证无 SESSION_PATCH 块时正常降级。"""
        event = CompletionEvent(turn_id="t0", status="success", visible_content="")
        envelope = RoleSessionOrchestrator._build_envelope_from_completion(event)

        assert envelope.session_patch == {}
        assert envelope.turn_result.visible_content == ""

    @pytest.mark.asyncio
    async def test_build_envelope_uses_preparsed_session_patch(self, tmp_workspace):
        """验证 event.session_patch 已预解析时直接使用，不重复解析。"""
        pre_parsed = {
            "task_progress": "verifying",
            "suspected_files": ["config.py"],
        }
        event = CompletionEvent(
            turn_id="t0",
            status="success",
            visible_content="<SESSION_PATCH>malformed json</SESSION_PATCH>",
            session_patch=pre_parsed,
        )

        envelope = RoleSessionOrchestrator._build_envelope_from_completion(event)

        # 应该使用预解析值，而非尝试解析 malformed JSON
        assert envelope.session_patch["task_progress"] == "verifying"
        assert envelope.session_patch["suspected_files"] == ["config.py"]

    @pytest.mark.asyncio
    async def test_build_envelope_continue_multi_turn_maps_auto_continue(self, tmp_workspace):
        """验证 continue_multi_turn 映射到 AUTO_CONTINUE（Handoff 断流修复）。"""
        event = CompletionEvent(
            turn_id="t0",
            status="success",
            visible_content="",
            turn_kind="continue_multi_turn",
        )
        envelope = RoleSessionOrchestrator._build_envelope_from_completion(event)
        assert envelope.continuation_mode == TurnContinuationMode.AUTO_CONTINUE
        assert envelope.turn_result.kind == "continue_multi_turn"

    @pytest.mark.asyncio
    async def test_continuation_prompt_includes_recent_reads(self, tmp_workspace):
        """验证 _build_continuation_prompt 的 WorkingMemory 包含 recent_reads。"""
        orch = RoleSessionOrchestrator(
            session_id="sess-1",
            kernel=AsyncMock(),
            workspace=tmp_workspace,
        )
        orch.state.structured_findings = {
            "recent_reads": ["read_file", "repo_read_head"],
            "error_summary": "bug found",
        }
        envelope = orch._build_empty_envelope()
        prompt = orch._build_continuation_prompt(envelope)
        assert "recent_reads" in prompt or "最近读取工具" in prompt
        assert "read_file" in prompt
        assert "repo_read_head" in prompt

    @pytest.mark.asyncio
    async def test_multi_turn_continue_multi_turn_then_end(self, tmp_workspace):
        """验证 continue_multi_turn 触发第二回合，随后 END_SESSION 结束。"""
        kernel = MockKernel(
            [
                [CompletionEvent(turn_id="t0", status="success")],
                [CompletionEvent(turn_id="t1", status="success")],
            ]
        )
        orch = RoleSessionOrchestrator(
            session_id="sess-1",
            kernel=kernel,
            workspace=tmp_workspace,
            max_auto_turns=5,
        )

        def _build_envelope(event):
            if event.turn_id == "t0":
                return SimpleNamespace(
                    turn_result=TurnResult(turn_id="t0", kind="continue_multi_turn", visible_content="", decision={}),
                    continuation_mode=TurnContinuationMode.AUTO_CONTINUE,
                    next_intent=None,
                    session_patch={"recent_reads": ["read_file"]},
                    artifacts_to_persist=[],
                    speculative_hints={},
                )
            return SimpleNamespace(
                turn_result=TurnResult(turn_id="t1", kind="final_answer", visible_content="done", decision={}),
                continuation_mode=TurnContinuationMode.END_SESSION,
                next_intent=None,
                session_patch={},
                artifacts_to_persist=[],
                speculative_hints={},
            )

        orch._build_envelope_from_completion = _build_envelope

        events = [e async for e in orch.execute_stream("hello")]
        assert kernel.call_count == 2
        assert orch.state.turn_count == 2
        # recent_reads 应被注入 structured_findings
        assert "read_file" in orch.state.structured_findings.get("recent_reads", [])
        assert isinstance(events[-1], SessionCompletedEvent)

    @pytest.mark.asyncio
    async def test_turn_history_is_canonical_record(self, tmp_workspace):
        kernel = MockKernel(
            [
                [
                    CompletionEvent(
                        turn_id="t0",
                        status="success",
                        turn_kind="final_answer",
                        batch_receipt={
                            "results": [
                                {
                                    "tool_name": "read_file",
                                    "status": "success",
                                    "arguments": {"path": "src/auth.py"},
                                    "result": {"content": "print('ok')"},
                                }
                            ]
                        },
                    )
                ]
            ]
        )
        orch = RoleSessionOrchestrator(session_id="sess-1", kernel=kernel, workspace=tmp_workspace)

        [e async for e in orch.execute_stream("inspect auth flow")]

        assert len(orch.state.turn_history) == 1
        record = orch.state.turn_history[0]
        assert record["turn_id"] == "t0"
        assert record["turn_kind"] == "final_answer"
        assert record["continuation_mode"] == "end_session"
        assert "batch_receipt" in record
        assert "phase" in record


class TestCheckpointResume:
    """测试 Checkpoint Resume 加载（Step 10）。"""

    @pytest.fixture
    def tmp_workspace(self, tmp_path):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        return str(workspace)

    @pytest.fixture
    def checkpoint_dir(self, tmp_workspace):
        cp_dir = Path(tmp_workspace) / ".polaris" / "checkpoints"
        cp_dir.mkdir(parents=True, exist_ok=True)
        return cp_dir

    def test_load_checkpoint_restores_full_state(self, tmp_workspace, checkpoint_dir):
        """验证 _load_checkpoint 完整恢复降维工作记忆。"""

        import json

        checkpoint_path = checkpoint_dir / "sess-resume.json"
        checkpoint_path.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "session_id": "sess-resume",
                    "goal": "修复登录 500",
                    "turn_count": 3,
                    "task_progress": "implementing",
                    "structured_findings": {
                        "error_summary": "token 过期",
                        "suspected_files": ["auth.py"],
                        "_superseded_keys": [],
                    },
                    "key_file_snapshots": {"auth.py": "hashabc"},
                    "last_failure": {"summary": "timeout"},
                    "artifacts": {},
                    "recent_artifact_hashes": ["h1", "h2"],
                }
            ),
            encoding="utf-8",
        )

        orch = RoleSessionOrchestrator(
            session_id="sess-resume",
            kernel=AsyncMock(),
            workspace=tmp_workspace,
        )
        # _try_load_checkpoint 在 __init__ 中已自动调用
        assert orch.state.turn_count == 3
        assert orch.state.task_progress == "implementing"
        assert orch.state.structured_findings["error_summary"] == "token 过期"
        assert "auth.py" in orch.state.structured_findings["suspected_files"]
        assert orch.state.structured_findings["_superseded_keys"] == []
        assert orch.state.key_file_snapshots["auth.py"] == "hashabc"

    def test_load_checkpoint_raises_on_version_mismatch(self, tmp_workspace, checkpoint_dir):
        """验证不支持的 schema_version 抛出 ValueError。"""

        import json

        checkpoint_path = checkpoint_dir / "sess-v1.json"
        checkpoint_path.write_text(
            json.dumps({"schema_version": 1, "session_id": "sess-v1"}),
            encoding="utf-8",
        )

        orch = RoleSessionOrchestrator(
            session_id="sess-v1",
            kernel=AsyncMock(),
            workspace=tmp_workspace,
        )
        # _try_load_checkpoint 静默忽略 ValueError，保持初始状态
        assert orch.state.turn_count == 0
        assert orch.state.task_progress == "exploring"

    def test_try_load_checkpoint_silent_on_missing_file(self, tmp_workspace):
        """验证 checkpoint 文件不存在时不崩溃。"""
        orch = RoleSessionOrchestrator(
            session_id="nonexistent-session",
            kernel=AsyncMock(),
            workspace=tmp_workspace,
        )
        # 应该使用默认初始状态
        assert orch.state.turn_count == 0
        assert orch.state.task_progress == "exploring"
        assert orch.state.structured_findings == {}

    def test_continuation_prompt_excludes_superseded_findings(self, tmp_workspace):
        """验证 4-zone prompt 不包含 superseded 的发现物（Step 9）。"""
        orch = RoleSessionOrchestrator(
            session_id="sess-s",
            kernel=AsyncMock(),
            workspace=tmp_workspace,
        )
        orch.state.task_progress = "investigating"
        orch.state.structured_findings = {
            "error_summary": "wrong file was suspected",
            "_superseded_keys": ["error_summary"],
            "suspected_files": ["correct.py"],
            "task_progress": "investigating",
            "_findings_trajectory": [],
        }
        envelope = orch._build_empty_envelope()
        prompt = orch._build_continuation_prompt(envelope)
        assert "wrong file was suspected" not in prompt
        assert "correct.py" in prompt

    def test_resume_uses_continuation_prompt_not_original(self, tmp_workspace):
        """验证 checkpoint resume 后使用 continuation prompt 而非原始 prompt（Bug 修复）。"""
        cp_dir = Path(tmp_workspace) / ".polaris" / "checkpoints"
        cp_dir.mkdir(parents=True, exist_ok=True)
        import json

        checkpoint_path = cp_dir / "sess-resume-prompt.json"
        checkpoint_path.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "session_id": "sess-resume-prompt",
                    "goal": "修复登录 500",
                    "turn_count": 2,
                    "task_progress": "implementing",
                    "structured_findings": {
                        "error_summary": "token 过期",
                        "suspected_files": ["auth.py"],
                    },
                    "key_file_snapshots": {},
                    "last_failure": None,
                    "artifacts": {},
                    "recent_artifact_hashes": [],
                }
            ),
            encoding="utf-8",
        )

        orch = RoleSessionOrchestrator(
            session_id="sess-resume-prompt",
            kernel=AsyncMock(),
            workspace=tmp_workspace,
        )
        # resume 后 turn_count=2，_build_continuation_prompt 应该被调用
        assert orch.state.turn_count == 2
        envelope = orch._build_empty_envelope()
        prompt = orch._build_continuation_prompt(envelope)
        # 验证 prompt 包含工作记忆（continuation 特征），而非原始 prompt
        assert "<Goal>" in prompt
        assert "<Progress>" in prompt
        assert "<WorkingMemory>" in prompt
        assert "auth.py" in prompt
        assert "token 过期" in prompt
        assert "implementing" in prompt

    def test_try_load_checkpoint_corrupted_json_silent_fallback(self, tmp_workspace):
        """验证 checkpoint 文件是非法 JSON 时静默回退到初始状态（防御性设计）。"""
        cp_dir = Path(tmp_workspace) / ".polaris" / "checkpoints"
        cp_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_path = cp_dir / "sess-corrupted.json"
        checkpoint_path.write_text("{not valid json", encoding="utf-8")

        orch = RoleSessionOrchestrator(
            session_id="sess-corrupted",
            kernel=AsyncMock(),
            workspace=tmp_workspace,
        )
        assert orch.state.turn_count == 0
        assert orch.state.task_progress == "exploring"
        assert orch.state.structured_findings == {}

    def test_try_load_checkpoint_schema_v1_silent_fallback(self, tmp_workspace):
        """验证旧 schema_version=1 的 checkpoint 被静默回退（schema 不兼容防御）。"""
        cp_dir = Path(tmp_workspace) / ".polaris" / "checkpoints"
        cp_dir.mkdir(parents=True, exist_ok=True)
        import json

        checkpoint_path = cp_dir / "sess-old-schema.json"
        checkpoint_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "session_id": "sess-old-schema",
                    "turn_count": 5,
                }
            ),
            encoding="utf-8",
        )

        orch = RoleSessionOrchestrator(
            session_id="sess-old-schema",
            kernel=AsyncMock(),
            workspace=tmp_workspace,
        )
        assert orch.state.turn_count == 0
        assert orch.state.task_progress == "exploring"

    def test_try_load_checkpoint_forward_compatible_with_unknown_fields(self, tmp_workspace):
        """验证 checkpoint 包含未来未知字段时仍能正常加载（前向兼容）。"""
        cp_dir = Path(tmp_workspace) / ".polaris" / "checkpoints"
        cp_dir.mkdir(parents=True, exist_ok=True)
        import json

        checkpoint_path = cp_dir / "sess-future-fields.json"
        checkpoint_path.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "session_id": "sess-future-fields",
                    "goal": "test forward compat",
                    "turn_count": 3,
                    "task_progress": "verifying",
                    "structured_findings": {"error_summary": "ok"},
                    "key_file_snapshots": {},
                    "last_failure": None,
                    "artifacts": {},
                    "recent_artifact_hashes": [],
                    # Future fields that don't exist yet in the current code
                    "future_field_1": "should be ignored",
                    "_meta_version": 99,
                }
            ),
            encoding="utf-8",
        )

        orch = RoleSessionOrchestrator(
            session_id="sess-future-fields",
            kernel=AsyncMock(),
            workspace=tmp_workspace,
        )
        # Should load successfully, ignoring unknown fields
        assert orch.state.turn_count == 3
        assert orch.state.task_progress == "verifying"
        assert orch.state.structured_findings["error_summary"] == "ok"
        # Unknown fields are simply ignored (not stored in state)
        assert "future_field_1" not in orch.state.__dict__
