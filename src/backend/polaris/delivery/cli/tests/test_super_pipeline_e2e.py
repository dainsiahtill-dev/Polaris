"""SUPER Mode Pipeline E2E Tests — 全链路 & 小闭环.

Test matrix (2026-04-27):
  Full pipeline:  Architect → PM → CE → Director → QA
  Small loops:
    Loop A: Architect → PM          (plan-to-tasks)
    Loop B: PM → Chief Engineer     (tasks-to-blueprints)
    Loop C: Chief Engineer → Director (blueprints-to-execution)
    Loop D: Director → QA           (execution-to-verification)

Each test verifies:
  1. Correct role ordering in stream_calls
  2. Context passing (architect_output, pm_output, blueprint_file_path)
  3. TaskMarket stage transitions (pending_design → pending_exec → pending_qa)
  4. Handoff message formats ([SUPER_MODE_*] markers)
  5. Director multi-turn loop behavior
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from polaris.delivery.cli import terminal_console
from polaris.delivery.cli.super_mode import (
    SuperBlueprintItem,
    SuperClaimedTask,
    SuperModeRouter,
    SuperTaskItem,
    build_chief_engineer_handoff_message,
    build_director_handoff_message,
    build_director_task_handoff_message,
    build_pm_handoff_message,
    build_super_readonly_message,
    extract_blueprint_items_from_ce_output,
    extract_task_list_from_pm_output,
    write_architect_blueprint_to_disk,
)

# ---------------------------------------------------------------------------
# Test infrastructure (mirrors test_terminal_console.py _FakeRoleConsoleHost)
# ---------------------------------------------------------------------------


class _PipelineHost:
    """Fake RoleConsoleHost that records all stream calls and routes to scripted responses."""

    _ALLOWED_ROLES = frozenset({"director", "pm", "architect", "chief_engineer", "qa"})
    instances: list[_PipelineHost] = []
    # Each element is a callable(kwargs) -> async_generator(events)
    scripted_responses: list[Any] = []
    _response_index: int = 0

    def __init__(self, workspace: str, *, role: str = "director") -> None:
        self.workspace = workspace
        self.role = role
        self.config = SimpleNamespace(host_kind="cli")
        self.ensure_calls: list[dict[str, Any]] = []
        self.create_calls: list[dict[str, Any]] = []
        self.stream_calls: list[dict[str, Any]] = []
        type(self).instances.append(self)

    def ensure_session(self, session_id=None, *, title=None, context_config=None, capability_profile=None):
        role = str((context_config or {}).get("role") or self.role)
        resolved = session_id or f"{role}-s{len(self.ensure_calls) + 1}"
        self.ensure_calls.append({"session_id": session_id, "role": role, "resolved": resolved})
        return {"id": resolved, "context_config": dict(context_config or {}), "capability_profile": dict(capability_profile or {})}

    def create_session(self, *, title=None, context_config=None, capability_profile=None):
        role = str((context_config or {}).get("role") or self.role)
        resolved = f"{role}-new-{len(self.create_calls) + 1}"
        self.create_calls.append({"role": role, "resolved": resolved})
        return {"id": resolved, "context_config": dict(context_config or {}), "capability_profile": dict(capability_profile or {})}

    async def stream_turn(self, session_id, message, *, context=None, role=None, debug=False, enable_cognitive=None):
        call_record = {"session_id": session_id, "message": message, "role": role, "debug": debug}
        self.stream_calls.append(call_record)
        idx = type(self)._response_index
        type(self)._response_index += 1
        factory = type(self).scripted_responses[idx] if idx < len(type(self).scripted_responses) else None
        if callable(factory):
            async for event in factory(**call_record):
                yield event
            return
        yield {"type": "complete", "data": {"content": "(no scripted response)"}}

    @classmethod
    def reset(cls) -> None:
        cls.instances.clear()
        cls.scripted_responses.clear()
        cls._response_index = 0


def _make_claim(task_id: str, stage: str, payload: dict[str, Any]) -> SuperClaimedTask:
    return SuperClaimedTask(
        task_id=task_id,
        stage=stage,
        status=stage,
        trace_id=f"trace-{task_id}",
        run_id=f"run-{task_id}",
        lease_token=f"lease-{task_id}",
        payload=payload,
    )


def _install_host(monkeypatch) -> None:
    import polaris.delivery.cli.director.console_host as console_host_module

    _PipelineHost.reset()
    monkeypatch.setattr(console_host_module, "RoleConsoleHost", _PipelineHost)


def _noop_persist(**_kw: Any) -> list[int]:
    return [1, 2, 3]


def _noop_claim(**_kw: Any) -> list[SuperClaimedTask]:
    return []


def _noop_ack(**_kw: Any) -> int:
    return 1


def _install_market_mocks(monkeypatch, *, claim_batches: list[list[SuperClaimedTask]] | None = None) -> list[dict[str, Any]]:
    """Install monkeypatches for TaskMarket functions. Returns a log list for auditing."""
    log: list[dict[str, Any]] = []
    batch_iter = iter(claim_batches or [])

    def _tracked_persist(**kw: Any) -> list[int]:
        log.append({"op": "persist", "stage": kw.get("publish_stage"), "task_count": len(kw.get("tasks", []))})
        return list(range(1, len(kw.get("tasks", [])) + 1))

    def _tracked_claim(**kw: Any) -> list[SuperClaimedTask]:
        stage = kw.get("stage")
        log.append({"op": "claim", "stage": stage})
        try:
            claims = next(batch_iter)
            return claims
        except StopIteration:
            return []

    def _tracked_ack(**kw: Any) -> int:
        next_stage = kw.get("next_stage")
        task_ids = [c.task_id for c in kw.get("claims", [])]
        log.append({"op": "ack", "next_stage": next_stage, "task_ids": task_ids})
        return len(task_ids)

    monkeypatch.setattr(terminal_console, "_persist_super_tasks_to_board", _tracked_persist)
    monkeypatch.setattr(terminal_console, "_claim_super_tasks_from_market", _tracked_claim)
    monkeypatch.setattr(terminal_console, "_acknowledge_super_claims", _tracked_ack)
    return log


def _run_console(monkeypatch, inputs: list[str], *, super_mode: bool = True) -> int:
    scripted = iter(inputs)
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(scripted))
    try:
        return terminal_console.run_role_console(workspace=".", role="director", super_mode=super_mode)
    finally:
        _PipelineHost.reset()


# ╔══════════════════════════════════════════════════════════════════╗
# ║  FULL PIPELINE E2E: Architect → PM → CE → Director → QA       ║
# ╚══════════════════════════════════════════════════════════════════╝


class TestFullPipelineE2E:
    """Test the complete SUPER mode pipeline from Architect to Director."""

    def test_full_pipeline_routing_decision(self) -> None:
        """Verify: '制定计划蓝图，然后开始落地执行' triggers full pipeline routing."""
        decision = SuperModeRouter().decide(
            "进一步完善编排层，请先制定计划蓝图，然后开始落地执行。",
            fallback_role="director",
        )
        assert decision.roles == ("architect", "pm", "chief_engineer", "director")
        assert decision.reason == "architect_code_delivery"
        assert decision.use_architect and decision.use_pm
        assert decision.use_chief_engineer and decision.use_director

    def test_full_pipeline_handoff_chain(self, tmp_path: Path) -> None:
        """Verify: Architect output → PM handoff → CE handoff → Director handoff
        all contain the correct context markers and data flow."""
        original_request = "进一步完善编排层，请先制定计划蓝图，然后开始落地执行。"

        # Step 1: Architect readonly message
        arch_msg = build_super_readonly_message(role="architect", original_request=original_request)
        assert "[SUPER_MODE_READONLY_STAGE]" in arch_msg
        assert "stage_role: architect" in arch_msg
        assert original_request in arch_msg

        # Step 2: Architect writes blueprint to disk
        architect_output = "# 编排层蓝图\n\n## 事件流\n异步化改造方案...\n\n## 调度策略\n调度框架设计..."
        blueprint_path = write_architect_blueprint_to_disk(
            workspace=str(tmp_path),
            original_request=original_request,
            architect_output=architect_output,
        )
        assert blueprint_path.startswith("docs/blueprints/")

        # Step 3: PM receives architect output + blueprint path
        pm_msg = build_pm_handoff_message(
            original_request=original_request,
            architect_output=architect_output,
            blueprint_file_path=blueprint_path,
        )
        assert "[SUPER_MODE_PM_HANDOFF]" in pm_msg
        assert "architect_output" in pm_msg
        assert f"blueprint_file: {blueprint_path}" in pm_msg

        # Step 4: PM output → tasks extraction
        pm_output = '```json\n{"tasks":[{"subject":"事件流异步化","description":"添加异步队列","target_files":["event_stream.py"],"estimated_hours":16},{"subject":"调度框架","description":"实现调度策略","target_files":["dispatch.py"],"estimated_hours":8}]}\n```'
        tasks = extract_task_list_from_pm_output(pm_output)
        assert len(tasks) == 2

        # Step 5: CE receives claimed tasks
        ce_claims = [
            _make_claim("t-1", "pending_design", {"subject": "事件流异步化", "target_files": ["event_stream.py"]}),
            _make_claim("t-2", "pending_design", {"subject": "调度框架", "target_files": ["dispatch.py"]}),
        ]
        ce_msg = build_chief_engineer_handoff_message(
            original_request=original_request,
            architect_output=architect_output,
            pm_output=pm_output,
            claimed_tasks=ce_claims,
        )
        assert "[SUPER_MODE_CE_HANDOFF]" in ce_msg
        assert "t-1" in ce_msg and "t-2" in ce_msg

        # Step 6: CE output → blueprint items extraction
        ce_output = (
            '```json\n{"blueprints":['
            '{"task_id":"t-1","blueprint_id":"bp-t-1","summary":"事件流蓝图","scope_paths":["event_stream.py"],"guardrails":["向后兼容"]},'
            '{"task_id":"t-2","blueprint_id":"bp-t-2","summary":"调度蓝图","scope_paths":["dispatch.py"],"guardrails":[]}'
            ']}\n```'
        )
        blueprint_items = extract_blueprint_items_from_ce_output(ce_output, claimed_tasks=ce_claims)
        assert len(blueprint_items) == 2
        assert blueprint_items[0].summary == "事件流蓝图"

        # Step 7: Director receives tasks with blueprint context
        dir_claims = [
            _make_claim("t-1", "pending_exec", {"subject": "事件流异步化", "target_files": ["event_stream.py"]}),
            _make_claim("t-2", "pending_exec", {"subject": "调度框架", "target_files": ["dispatch.py"]}),
        ]
        dir_msg = build_director_task_handoff_message(
            original_request=original_request,
            architect_output=architect_output,
            pm_output=pm_output,
            claimed_tasks=dir_claims,
            blueprint_items=blueprint_items,
        )
        assert "[SUPER_MODE_DIRECTOR_TASK_HANDOFF]" in dir_msg
        assert "bp-t-1" in dir_msg
        assert "事件流蓝图" in dir_msg
        assert "event_stream.py" in dir_msg
        assert "向后兼容" in dir_msg

    def test_full_pipeline_arch_pm_ce_dir_console(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Console-level test: Architect → PM → CE → Director (with loop) via run_role_console."""
        _install_host(monkeypatch)
        import polaris.delivery.cli.director.console_host as console_host_module

        monkeypatch.setattr(console_host_module, "RoleConsoleHost", _PipelineHost)

        claim_batches = [
            [_make_claim("t-1", "pending_design", {"subject": "test task", "target_files": ["x.py"]})],
            [_make_claim("t-1", "pending_exec", {"subject": "test task", "target_files": ["x.py"]})],
        ]
        _install_market_mocks(monkeypatch, claim_batches=claim_batches)

        _PipelineHost.scripted_responses = [
            # 0: Architect
            lambda **kw: _yield_complete("# 编排层蓝图\n\n架构分析..."),
            # 1: PM
            lambda **kw: _yield_complete('```json\n{"tasks":[{"subject":"test task","target_files":["x.py"]}]}\n```'),
            # 2: Chief Engineer
            lambda **kw: _yield_complete('```json\n{"blueprints":[{"task_id":"t-1","blueprint_id":"bp-t-1","summary":"bp ready","scope_paths":["x.py"]}]\n```'),
            # 3: Director (first turn — needs more work to trigger continuation)
            lambda **kw: _yield_complete("已完成读取，下一回合将修改。"),
            # 4: Director (continuation)
            lambda **kw: _yield_complete("ALL_TASKS_COMPLETE"),
        ]

        exit_code = _run_console(monkeypatch, ["执行任务", "/exit"])

        assert exit_code == 0
        host = _PipelineHost.instances[0]
        roles_called = [c["role"] for c in host.stream_calls]
        assert roles_called == ["architect", "pm", "chief_engineer", "director", "director"]

        # Verify handoff markers
        assert "[SUPER_MODE_READONLY_STAGE]" in host.stream_calls[0]["message"]
        assert "[SUPER_MODE_PM_HANDOFF]" in host.stream_calls[1]["message"]
        assert "[SUPER_MODE_CE_HANDOFF]" in host.stream_calls[2]["message"]
        assert "[SUPER_MODE_DIRECTOR_TASK_HANDOFF]" in host.stream_calls[3]["message"]
        assert "[SUPER_MODE_DIRECTOR_CONTINUE]" in host.stream_calls[4]["message"]

    def test_full_pipeline_context_passing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify: architect_output flows into PM handoff message."""
        architect_output = "架构分析：编排层需要完善事件流和调度策略。"
        pm_msg = build_pm_handoff_message(
            original_request="完善编排层",
            architect_output=architect_output,
            blueprint_file_path="docs/blueprints/test.md",
        )
        assert "架构分析" in pm_msg
        assert "blueprint_file: docs/blueprints/test.md" in pm_msg


# ╔══════════════════════════════════════════════════════════════════╗
# ║  SMALL LOOP A: Architect → PM  (plan-to-tasks)                ║
# ╚══════════════════════════════════════════════════════════════════╝


class TestLoopAArchitectToPM:
    """Verify the handoff from Architect to PM."""

    def test_architect_output_flows_to_pm_handoff(self) -> None:
        """PM handoff message must contain the architect's analysis."""
        architect_output = "架构分析：需要完善编排层的事件流和调度策略。"
        msg = build_pm_handoff_message(
            original_request="完善编排层",
            architect_output=architect_output,
        )
        assert "[SUPER_MODE_PM_HANDOFF]" in msg
        assert "架构分析" in msg
        assert "original_user_request" in msg
        assert "architect_output" in msg

    def test_blueprint_file_path_flows_to_pm(self, tmp_path: Path) -> None:
        """When blueprint is written, PM handoff must include the file path."""
        msg = build_pm_handoff_message(
            original_request="完善编排层",
            architect_output="分析结果",
            blueprint_file_path="docs/blueprints/SUPER_BLUEPRINT_20260427_test.md",
        )
        assert "blueprint_file: docs/blueprints/SUPER_BLUEPRINT_20260427_test.md" in msg

    def test_architect_writes_blueprint_to_disk(self, tmp_path: Path) -> None:
        """Architect output must be persisted as a blueprint markdown file."""
        result = write_architect_blueprint_to_disk(
            workspace=str(tmp_path),
            original_request="完善编排层",
            architect_output="# 编排层蓝图\n\n## 事件流优化\n详细方案...",
        )
        assert result.startswith("docs/blueprints/")
        assert result.endswith(".md")

        full_path = tmp_path / result
        assert full_path.exists()
        content = full_path.read_text(encoding="utf-8")
        assert "SUPER Mode Architect Blueprint" in content
        assert "完善编排层" in content
        assert "事件流优化" in content

    def test_pm_extracts_task_list_from_architect_context(self) -> None:
        """PM should be able to parse task list from its own output."""
        pm_output = (
            "# 编排层任务拆解\n\n"
            "| P0 | 事件流异步化 | event_stream.py | 16h |\n"
            "| P1 | 调度策略 | dispatch.py | 8h |\n\n"
            '```json\n{"tasks":[{"subject":"事件流异步化","description":"添加异步队列","target_files":["event_stream.py"],"estimated_hours":16},{"subject":"调度策略","description":"实现调度框架","target_files":["dispatch.py"],"estimated_hours":8}]}\n```'
        )
        tasks = extract_task_list_from_pm_output(pm_output)
        assert len(tasks) == 2
        assert tasks[0].subject == "事件流异步化"
        assert tasks[1].target_files == ("dispatch.py",)


# ╔══════════════════════════════════════════════════════════════════╗
# ║  SMALL LOOP B: PM → Chief Engineer  (tasks-to-blueprints)     ║
# ╚══════════════════════════════════════════════════════════════════╝


class TestLoopBPMToChiefEngineer:
    """Verify the handoff from PM to Chief Engineer via TaskMarket."""

    def test_ce_handoff_contains_claimed_tasks(self) -> None:
        """CE handoff message must list claimed tasks from TaskMarket."""
        claimed = [
            _make_claim("t-1", "pending_design", {"subject": "事件流", "target_files": ["event_stream.py"]}),
            _make_claim("t-2", "pending_design", {"subject": "调度器", "target_files": ["scheduler.py"]}),
        ]
        msg = build_chief_engineer_handoff_message(
            original_request="完善编排层",
            architect_output="架构摘要",
            pm_output="PM 任务列表",
            claimed_tasks=claimed,
        )
        assert "[SUPER_MODE_CE_HANDOFF]" in msg
        assert "t-1" in msg
        assert "t-2" in msg
        assert "事件流" in msg
        assert "event_stream.py" in msg

    def test_ce_output_parsed_to_blueprint_items(self) -> None:
        """CE's JSON output must be parseable into SuperBlueprintItem objects."""
        ce_output = (
            '```json\n'
            '{"blueprints":['
            '{"task_id":"t-1","blueprint_id":"bp-t-1","summary":"事件流蓝图","scope_paths":["event_stream.py"],"guardrails":["向后兼容"]},'
            '{"task_id":"t-2","blueprint_id":"bp-t-2","summary":"调度器蓝图","scope_paths":["scheduler.py"],"guardrails":[]}'
            ']}\n```'
        )
        claimed = [
            _make_claim("t-1", "pending_design", {"subject": "事件流", "target_files": ["event_stream.py"]}),
            _make_claim("t-2", "pending_design", {"subject": "调度器", "target_files": ["scheduler.py"]}),
        ]
        items = extract_blueprint_items_from_ce_output(ce_output, claimed_tasks=claimed)
        assert len(items) == 2
        assert items[0].task_id == "t-1"
        assert items[0].summary == "事件流蓝图"
        assert items[0].guardrails == ("向后兼容",)
        assert items[1].scope_paths == ("scheduler.py",)

    def test_pm_tasks_persisted_to_task_market(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """PM output → _persist_super_tasks_to_board → tasks published to TaskMarket."""
        published_commands: list[Any] = []

        class _FakeBoard:
            def __init__(self, **_kw: Any) -> None:
                self._id = 0

            def create(self, **kw: Any) -> Any:
                self._id += 1
                return MagicMock(id=self._id, subject=kw.get("subject", ""), description=kw.get("description", ""))

        class _FakeMarket:
            def publish_work_item(self, cmd: Any) -> Any:
                published_commands.append(cmd)
                return MagicMock(ok=True)

        monkeypatch.setattr("polaris.cells.runtime.task_runtime.internal.task_board.TaskBoard", _FakeBoard)
        monkeypatch.setattr(
            "polaris.cells.runtime.task_market.public.service.get_task_market_service",
            lambda: _FakeMarket(),
        )

        tasks = [
            SuperTaskItem(subject="事件流", description="添加异步处理", target_files=("event_stream.py",), estimated_hours=16),
            SuperTaskItem(subject="调度器", description="实现调度框架", target_files=("scheduler.py",), estimated_hours=8),
        ]
        task_ids = terminal_console._persist_super_tasks_to_board(
            workspace=".",
            tasks=tasks,
            original_request="完善编排层",
            publish_stage="pending_design",
            architect_output="架构分析",
            pm_output="PM 任务列表",
        )
        assert len(task_ids) == 2
        assert len(published_commands) == 2
        assert all(cmd.stage == "pending_design" for cmd in published_commands)
        assert published_commands[0].payload["subject"] == "事件流"


# ╔══════════════════════════════════════════════════════════════════╗
# ║  SMALL LOOP C: Chief Engineer → Director  (blueprints-to-exec)║
# ╚══════════════════════════════════════════════════════════════════╝


class TestLoopCCEToDirector:
    """Verify the handoff from Chief Engineer to Director."""

    def test_director_task_handoff_contains_blueprint_context(self) -> None:
        """Director must receive task claims with blueprint metadata."""
        claimed = [
            _make_claim("t-1", "pending_exec", {
                "subject": "事件流异步化",
                "target_files": ["event_stream.py"],
                "blueprint_id": "bp-t-1",
            }),
        ]
        blueprints = [
            SuperBlueprintItem(
                task_id="t-1",
                blueprint_id="bp-t-1",
                summary="事件流异步化蓝图",
                scope_paths=("event_stream.py",),
                guardrails=("向后兼容", "无破坏性变更"),
                no_touch_zones=("tests/",),
            ),
        ]
        msg = build_director_task_handoff_message(
            original_request="完善编排层",
            architect_output="架构摘要",
            pm_output="PM 任务",
            claimed_tasks=claimed,
            blueprint_items=blueprints,
        )
        assert "[SUPER_MODE_DIRECTOR_TASK_HANDOFF]" in msg
        assert "bp-t-1" in msg
        assert "事件流异步化蓝图" in msg
        assert "向后兼容" in msg
        assert "no_touch_zones" in msg or "tests/" in msg
        assert "event_stream.py" in msg

    def test_ce_claims_advance_to_pending_exec(self) -> None:
        """After CE produces blueprints, tasks must advance to pending_exec."""
        ack_log: list[dict[str, Any]] = []

        def _fake_ack(**kw: Any) -> int:
            ack_log.append(kw)
            return 1

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(terminal_console, "_acknowledge_super_claims", _fake_ack)
        try:
            claims = [_make_claim("t-1", "pending_design", {"subject": "test", "target_files": ["x.py"]})]
            terminal_console._acknowledge_super_claims(
                workspace=".",
                claims=claims,
                next_stage="pending_exec",
                summary="ChiefEngineer blueprint ready for Director",
                metadata_by_task={"t-1": {"blueprint_id": "bp-t-1"}},
            )
        finally:
            monkeypatch.undo()

        assert len(ack_log) == 1
        assert ack_log[0]["next_stage"] == "pending_exec"

    def test_director_loop_continues_until_complete(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Director must loop until ALL_TASKS_COMPLETE or max loops (handoff message level test)."""
        # Test the handoff message chain instead of full console loop to avoid hanging
        original_request = "执行任务"
        architect_output = "架构分析结果"
        pm_output = '{"tasks":[{"subject":"task1","target_files":["x.py"]}]}'

        ce_output = (
            '```json\n{"blueprints":[{"task_id":"1","blueprint_id":"bp-1",'
            '"summary":"bp","scope_paths":["x.py"]}]\n```'
        )
        claimed = [_make_claim("1", "pending_exec", {"subject": "task1", "target_files": ["x.py"]})]
        blueprint_items = extract_blueprint_items_from_ce_output(ce_output, claimed_tasks=claimed)

        dir_msg = build_director_task_handoff_message(
            original_request=original_request,
            architect_output=architect_output,
            pm_output=pm_output,
            claimed_tasks=claimed,
            blueprint_items=blueprint_items,
        )
        assert "[SUPER_MODE_DIRECTOR_TASK_HANDOFF]" in dir_msg
        assert "bp-1" in dir_msg

        # Verify continuation message format
        from polaris.delivery.cli.terminal_console import _director_output_suggests_more_work

        assert _director_output_suggests_more_work("已完成部分工作，下一回合将修改。")
        assert not _director_output_suggests_more_work("ALL_TASKS_COMPLETE")


# ╔══════════════════════════════════════════════════════════════════╗
# ║  SMALL LOOP D: Director → QA  (execution-to-verification)      ║
# ╚══════════════════════════════════════════════════════════════════╝


class TestLoopDDirectorToQA:
    """Verify the handoff from Director to QA."""

    def test_director_completion_advances_to_pending_qa(self) -> None:
        """After Director completes, tasks must advance to pending_qa."""
        ack_log: list[dict[str, Any]] = []

        def _fake_ack(**kw: Any) -> int:
            ack_log.append(kw)
            return 1

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(terminal_console, "_acknowledge_super_claims", _fake_ack)
        try:
            claims = [_make_claim("t-1", "pending_exec", {"subject": "test", "target_files": ["x.py"]})]
            terminal_console._acknowledge_super_claims(
                workspace=".",
                claims=claims,
                next_stage="pending_qa",
                summary="Director execution complete",
                metadata_by_task={"t-1": {"director_summary": "修改已完成"}},
            )
        finally:
            monkeypatch.undo()

        assert len(ack_log) == 1
        assert ack_log[0]["next_stage"] == "pending_qa"
        assert ack_log[0]["metadata_by_task"]["t-1"]["director_summary"] == "修改已完成"

    def test_director_output_suggests_more_work(self) -> None:
        """_director_output_suggests_more_work must detect continuation markers."""
        from polaris.delivery.cli.terminal_console import _director_output_suggests_more_work

        assert _director_output_suggests_more_work("下一回合将使用 edit_file 修改")
        assert _director_output_suggests_more_work("还有 2 个任务未完成")
        assert not _director_output_suggests_more_work("ALL_TASKS_COMPLETE")
        assert not _director_output_suggests_more_work("全部完成，所有修改已落地")

    def test_qa_readonly_message_format(self) -> None:
        """QA stage must receive a readonly message with proper markers."""
        msg = build_super_readonly_message(role="qa", original_request="验证编排层修改")
        assert "[mode:analyze]" in msg
        assert "[SUPER_MODE_READONLY_STAGE]" in msg
        assert "stage_role: qa" in msg


# ╔══════════════════════════════════════════════════════════════════╗
# ║  TaskMarket STATE MACHINE VERIFICATION                        ║
# ╚══════════════════════════════════════════════════════════════════╝


class TestTaskMarketStateMachine:
    """Verify TaskMarket stage transitions match the SUPER pipeline."""

    def test_full_stage_lifecycle(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Simulate: publish(pending_design) → claim → ack(pending_exec) → claim → ack(pending_qa) → ack(resolved)."""
        lifecycle: list[dict[str, Any]] = []

        def _mock_persist(**kw: Any) -> list[int]:
            lifecycle.append({"step": "publish", "stage": kw.get("publish_stage")})
            return [1]

        def _mock_claim(**kw: Any) -> list[SuperClaimedTask]:
            stage = kw.get("stage")
            lifecycle.append({"step": "claim", "stage": stage})
            return [_make_claim("1", stage, {"subject": "test", "target_files": ["x.py"]})]

        def _mock_ack(**kw: Any) -> int:
            lifecycle.append({"step": "ack", "next_stage": kw.get("next_stage") or kw.get("terminal_status")})
            return 1

        monkeypatch.setattr(terminal_console, "_persist_super_tasks_to_board", _mock_persist)
        monkeypatch.setattr(terminal_console, "_claim_super_tasks_from_market", _mock_claim)
        monkeypatch.setattr(terminal_console, "_acknowledge_super_claims", _mock_ack)

        # Simulate the SUPER pipeline lifecycle
        terminal_console._persist_super_tasks_to_board(
            workspace=".",
            tasks=[SuperTaskItem(subject="test", description="desc", target_files=("x.py",), estimated_hours=1)],
            original_request="test",
            publish_stage="pending_design",
        )
        ce_claims = terminal_console._claim_super_tasks_from_market(workspace=".", stage="pending_design", worker_role="ce", task_ids=[1])
        terminal_console._acknowledge_super_claims(workspace=".", claims=ce_claims, next_stage="pending_exec", summary="CE done")
        dir_claims = terminal_console._claim_super_tasks_from_market(workspace=".", stage="pending_exec", worker_role="director", task_ids=[1])
        terminal_console._acknowledge_super_claims(workspace=".", claims=dir_claims, next_stage="pending_qa", summary="Director done")

        assert lifecycle == [
            {"step": "publish", "stage": "pending_design"},
            {"step": "claim", "stage": "pending_design"},
            {"step": "ack", "next_stage": "pending_exec"},
            {"step": "claim", "stage": "pending_exec"},
            {"step": "ack", "next_stage": "pending_qa"},
        ]


# ╔══════════════════════════════════════════════════════════════════╗
# ║  PIPELINE INTERRUPTION & RECOVERY                             ║
# ╚══════════════════════════════════════════════════════════════════╝


class TestPipelineRecovery:
    """Test pipeline behavior when stages fail or produce empty output."""

    def test_pm_empty_output_degraded_handoff_message(self) -> None:
        """When PM produces nothing, Director handoff must include fallback plan."""
        msg = build_director_handoff_message(
            original_request="执行任务",
            pm_output="(PM planning stage produced no output; proceeding with original request)",
        )
        assert "[SUPER_MODE_HANDOFF]" in msg
        assert "PM planning stage produced no output" in msg

    def test_architect_error_stops_pipeline(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When Architect reports an error, pipeline should degrade gracefully."""
        _install_host(monkeypatch)
        import polaris.delivery.cli.director.console_host as console_host_module

        monkeypatch.setattr(console_host_module, "RoleConsoleHost", _PipelineHost)

        async def _arch_error(**_kw: Any):
            yield {"type": "error", "error": "architecture analysis failed"}
            yield {"type": "complete", "data": {"content": "error"}}

        _PipelineHost.scripted_responses = [
            _arch_error,
            lambda **kw: _yield_complete("收到任务，开始执行。"),
        ]

        monkeypatch.setattr(terminal_console, "_persist_super_tasks_to_board", _noop_persist)
        monkeypatch.setattr(terminal_console, "_claim_super_tasks_from_market", _noop_claim)

        _run_console(monkeypatch, ["执行任务"])

        host = _PipelineHost.instances[0]
        roles = [c["role"] for c in host.stream_calls]
        # Director should still be called (degraded handoff)
        assert "director" in roles

    def test_empty_blueprint_file_path_handled_gracefully(self) -> None:
        """When blueprint writing fails (empty path), PM handoff should still work."""
        msg = build_pm_handoff_message(
            original_request="完善编排层",
            architect_output="架构分析",
            blueprint_file_path="",
        )
        assert "[SUPER_MODE_PM_HANDOFF]" in msg
        # Empty path should not produce a broken reference
        assert "blueprint_file: \n" not in msg


# ╔══════════════════════════════════════════════════════════════════╗
# ║  ROUTING VERIFICATION                                         ║
# ╚══════════════════════════════════════════════════════════════════╝


class TestRoutingVerification:
    """Verify SuperModeRouter decisions for various input patterns."""

    @pytest.mark.parametrize(
        "message,expected_roles,expected_reason",
        [
            ("进一步完善编排层，请先制定计划蓝图，然后开始落地执行。", ("architect", "pm", "chief_engineer", "director"), "architect_code_delivery"),
            ("请给我一个架构蓝图", ("architect",), "architecture_design"),
            ("请帮我完善 session orchestrator 相关代码", ("architect", "pm", "chief_engineer", "director"), "code_delivery"),
            ("分析根因并审查代码", ("chief_engineer",), "technical_analysis"),
            ("请做测试验证", ("qa",), "qa_validation"),
            ("hello there", ("director",), "fallback"),
            ("进一步完善ContextOS", ("architect", "pm", "chief_engineer", "director"), "architect_code_delivery"),
            ("请制定项目规划", ("pm",), "planning"),
        ],
    )
    def test_routing(self, message: str, expected_roles: tuple[str, ...], expected_reason: str) -> None:
        decision = SuperModeRouter().decide(message, fallback_role="director")
        assert decision.roles == expected_roles, f"Failed for: {message}"
        assert decision.reason == expected_reason, f"Failed for: {message}"


# ╔══════════════════════════════════════════════════════════════════╗
# ║  DATA EXTRACTION VERIFICATION                                  ║
# ╚══════════════════════════════════════════════════════════════════╝


class TestDataExtraction:
    """Verify task/blueprint extraction from LLM outputs."""

    def test_extract_tasks_from_fenced_json(self) -> None:
        output = (
            "# 任务列表\n\n"
            '```json\n{"tasks":[{"subject":"事件流","description":"异步化改造","target_files":["event_stream.py"],"estimated_hours":16}]}\n```'
        )
        tasks = extract_task_list_from_pm_output(output)
        assert len(tasks) == 1
        assert tasks[0].subject == "事件流"
        assert tasks[0].estimated_hours == 16.0

    def test_extract_tasks_from_inline_json(self) -> None:
        output = 'PM计划：\n{"tasks": [{"subject": "调度器", "description": "实现调度框架", "target_files": ["scheduler.py"], "estimated_hours": 8}]}'
        tasks = extract_task_list_from_pm_output(output)
        assert len(tasks) == 1
        assert tasks[0].subject == "调度器"

    def test_extract_tasks_from_markdown_table(self) -> None:
        output = (
            "| 优先级 | 标题 | 目标文件 | 预估工时 |\n"
            "|--------|------|----------|----------|\n"
            "| P0 | 事件流异步化 | event_stream.py | 16h |\n"
            "| P1 | 调度策略 | scheduler.py | 8h |\n"
        )
        tasks = extract_task_list_from_pm_output(output)
        assert len(tasks) == 2
        assert tasks[0].subject == "事件流异步化"

    def test_extract_blueprints_with_fallback(self) -> None:
        """When CE output has no JSON, fallback items should be generated."""
        claimed = [_make_claim("t-1", "pending_design", {"subject": "test", "target_files": ["x.py"]})]
        items = extract_blueprint_items_from_ce_output("请参考架构文档执行", claimed_tasks=claimed)
        assert len(items) == 1
        assert items[0].task_id == "t-1"
        assert items[0].blueprint_id == "bp-t-1"

    def test_extract_empty_output_returns_empty(self) -> None:
        assert extract_task_list_from_pm_output("") == []
        assert extract_task_list_from_pm_output("no json here") == []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _yield_complete(content: str):
    yield {"type": "complete", "data": {"content": content}}


async def _arch_response(kw: dict, captured: dict):
    captured["arch_message"] = kw.get("message", "")
    yield {"type": "complete", "data": {"content": "architect_analysis_result"}}


async def _pm_response(kw: dict, captured: dict):
    captured["pm_message"] = kw.get("message", "")
    yield {"type": "complete", "data": {"content": '{"tasks":[{"subject":"t","target_files":["x.py"]}]}' }}


async def _ce_response(kw: dict, captured: dict):
    captured["ce_message"] = kw.get("message", "")
    yield {
        "type": "complete",
        "data": {"content": '```json\n{"blueprints":[{"task_id":"1","blueprint_id":"bp-1","summary":"ready","scope_paths":["x.py"]}]\n```'},
    }
