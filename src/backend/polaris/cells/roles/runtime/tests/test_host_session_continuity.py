from __future__ import annotations

from types import SimpleNamespace
from typing import Any, NoReturn, cast

import pytest
from polaris.cells.roles.profile.public.service import RoleTurnResult
from polaris.cells.roles.runtime.public.contracts import (
    ExecuteRoleSessionCommandV1,
    ExecuteRoleTaskCommandV1,
    RoleExecutionResultV1,
)
from polaris.cells.roles.runtime.public.service import RoleRuntimeService
from polaris.cells.roles.session.internal.conversation import Base
from polaris.cells.roles.session.internal.role_session_service import RoleSessionService
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


class TestRoleRuntimeServiceHostContinuity:
    @pytest.mark.asyncio
    async def test_project_host_history_compacts_stale_meta_chatter(self) -> None:
        history = (
            ("user", "你好"),
            ("assistant", "你好，我是当前会话助手。"),
            ("user", "你能换个名字吗，叫二郎"),
            ("user", "先写计划文档，蓝图，然后开工"),
            ("assistant", "我会先写计划文档和蓝图。"),
            ("user", "继续抽离 Session Continuity Engine"),
            ("assistant", "继续处理中。"),
            ("user", "补测试和治理资产"),
            ("assistant", "会补测试和治理资产。"),
            ("user", "继续"),
            ("assistant", "继续处理中。"),
            ("user", "把 host 链路也统一掉"),
        )

        projected_history, projected_context, persisted = await RoleRuntimeService._project_host_history(
            session_id="sess-host",
            role="director",
            workspace="C:/repo",
            history=history,
            context={"host_kind": "runtime_interactive", "role": "director"},
            session_context_config={},
            history_limit=4,
            session_title="Director interactive session",
        )

        assert len(projected_history) == 4
        continuity = projected_context.get("session_continuity")
        assert isinstance(continuity, dict)
        assert "session continuity engine" in str(continuity.get("summary") or "").lower()
        assert "二郎" not in str(continuity.get("summary") or "")
        assert continuity.get("stable_facts")
        assert continuity.get("open_loops")
        state_first = projected_context.get("state_first_context_os")
        assert isinstance(state_first, dict)
        assert isinstance(state_first.get("run_card"), dict)
        assert isinstance(state_first.get("context_slice_plan"), dict)
        assert persisted.get("session_continuity") == continuity


@pytest.mark.asyncio
async def test_execute_role_session_persists_transcript_and_context_os(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)
    db = session_factory()
    try:
        role_session_service = RoleSessionService(db=db)
        session = role_session_service.create_session(role="director", context_config={})

        import polaris.cells.roles.session.internal.role_session_service as session_service_module
        import polaris.cells.roles.session.public as session_public_module

        def _session_factory():
            return role_session_service

        monkeypatch.setattr(session_service_module, "RoleSessionService", _session_factory)
        monkeypatch.setattr(session_public_module, "RoleSessionService", _session_factory)

        class _FakeKernel:
            async def run(self, role: str, request):
                assert role == "director"
                assert str((request.metadata or {}).get("session_id") or "") == session.id
                return RoleTurnResult(
                    content="我会继续推进 context engine。",
                    thinking="先持久化 session continuity。",
                    tool_calls=[{"name": "search_memory"}],
                    tool_results=[
                        {
                            "name": "search_memory",
                            "ok": True,
                            "tool_call_id": "call-session-1",
                            "result": {"matches": []},
                            "metadata": {
                                "dispatch_status": "dispatched",
                                "operation_id": "tool.search_memory.session-1",
                                "effect_receipt": {
                                    "schema_version": "tool_effect_receipt.v1",
                                    "tool_name": "search_memory",
                                    "operation_id": "tool.search_memory.session-1",
                                    "side_effect_class": "read",
                                    "committed": True,
                                },
                            },
                        },
                    ],
                    batch_receipt={
                        "schema_version": "tool_batch_receipt.v1",
                        "results": [
                            {
                                "tool_name": "search_memory",
                                "tool_call_id": "call-session-1",
                                "ok": True,
                                "committed": True,
                            }
                        ],
                    },
                    is_complete=True,
                    execution_stats={"platform_retry_count": 0},
                    turn_history=[
                        ("user", "继续推进 context engine"),
                        ("assistant", "我会继续推进 context engine。"),
                    ],
                    metadata={
                        "turn_id": "turn-session-1",
                        "turn_envelope": {
                            "turn_id": "turn-session-1",
                            "projection_version": "state_first_context_os.v1",
                            "role": "director",
                            "session_id": session.id,
                        },
                    },
                )

        runtime = RoleRuntimeService()
        shadow_calls: list[dict[str, object]] = []
        monkeypatch.setattr(runtime, "_get_kernel", lambda _workspace: _FakeKernel())
        monkeypatch.setattr(
            runtime,
            "_emit_cognitive_runtime_shadow_artifacts",
            lambda **kwargs: shadow_calls.append(dict(kwargs)),
        )

        result = await runtime.execute_role_session(
            ExecuteRoleSessionCommandV1(
                role="director",
                session_id=session.id,  # type: ignore[arg-type]
                workspace="C:/repo",
                user_message="继续推进 context engine",
                history=(
                    ("user", "先把 session continuity 下沉到 kernelone。"),
                    ("assistant", "我会先收口 continuity engine。"),
                    ("user", "继续修复 context compaction。"),
                    ("assistant", "我会保留 open loops 和 stable facts。"),
                    ("user", "补 roles.session 持久化。"),
                    ("assistant", "我会把派生 continuity 回写 session。"),
                    ("user", "加 restore tools。"),
                    ("assistant", "我会暴露 search_memory 和 read_artifact。"),
                ),
                context={},
                stream=False,
            )
        )

        assert result.ok is True
        messages = role_session_service.get_messages(session.id)  # type: ignore[arg-type]
        assert [item.role for item in messages] == ["user", "assistant"]
        assert messages[0].content == "继续推进 context engine"
        assert "context engine" in messages[1].content

        context_config = role_session_service.get_context_config_dict(session.id)  # type: ignore[arg-type]
        assert context_config is not None
        assert "state_first_context_os" in context_config
        assert len(shadow_calls) == 1
        assert shadow_calls[0]["source"] == "roles.runtime.execute_role_session"
        assert shadow_calls[0]["session_id"] == session.id
        assert result.metadata["turn_id"] == "turn-session-1"
        assert result.metadata["turn_envelope"]["projection_version"] == "state_first_context_os.v1"
    finally:
        db.close()
        engine.dispose()


@pytest.mark.asyncio
async def test_persist_session_turn_state_uses_session_continuity_engine_policy(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)
    db = session_factory()
    try:
        role_session_service = RoleSessionService(db=db)
        session = role_session_service.create_session(
            role="director",
            context_config={"session_continuity": {"recent_window_messages": "invalid"}},
        )

        import polaris.cells.roles.session.internal.role_session_service as session_service_module
        import polaris.cells.roles.session.public as session_public_module

        def _session_factory():
            return role_session_service

        monkeypatch.setattr(session_service_module, "RoleSessionService", _session_factory)
        monkeypatch.setattr(session_public_module, "RoleSessionService", _session_factory)

        await RoleRuntimeService._persist_session_turn_state(
            ExecuteRoleSessionCommandV1(
                role="director",
                session_id=session.id,  # type: ignore[arg-type]
                workspace="C:/repo",
                user_message="继续保留 Context OS 投影",
                context={},
                stream=True,
            ),
            turn_history=[
                ("user", "继续保留 Context OS 投影"),
                ("assistant", "我会把状态写回 session。"),
            ],
        )

        context_config = role_session_service.get_context_config_dict(session.id)  # type: ignore[arg-type]
        assert context_config is not None
        assert "session_continuity" in context_config
        assert "state_first_context_os" in context_config
        assert role_session_service.get_messages(session.id)  # type: ignore[arg-type]
    finally:
        db.close()
        engine.dispose()


@pytest.mark.asyncio
async def test_persist_session_turn_state_degrades_when_session_store_uninitialized(monkeypatch) -> None:
    import polaris.cells.roles.session.public as session_public_module

    class _BrokenRoleSessionService:
        def __enter__(self) -> _BrokenRoleSessionService:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def get_session(self, _session_id: str) -> NoReturn:
            raise LookupError("no such table: conversations")

    monkeypatch.setattr(session_public_module, "RoleSessionService", _BrokenRoleSessionService)

    await RoleRuntimeService._persist_session_turn_state(
        ExecuteRoleSessionCommandV1(
            role="director",
            session_id="director-task-1",
            workspace="C:/repo",
            user_message="materialize",
            context={},
            stream=False,
        ),
        turn_history=[("assistant", "done")],
    )


def test_cognitive_runtime_shadow_artifacts_degrade_when_handoff_store_uninitialized(monkeypatch) -> None:
    import polaris.cells.factory.cognitive_runtime.public.service as cognitive_service_module

    class _FakeCognitiveRuntimeService:
        def record_runtime_receipt(self, _command: object) -> object:
            return SimpleNamespace(ok=True, receipt=SimpleNamespace(receipt_id="receipt-1"))

        def export_handoff_pack(self, _command: object) -> NoReturn:
            raise LookupError("no such table: conversations")

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        cognitive_service_module,
        "get_cognitive_runtime_public_service",
        lambda: _FakeCognitiveRuntimeService(),
    )

    runtime = RoleRuntimeService()
    evidence = runtime._emit_cognitive_runtime_shadow_artifacts(
        source="test",
        workspace="C:/repo",
        role="director",
        task_id="task-1",
        session_id="director-task-1",
        run_id="run-1",
        result=RoleExecutionResultV1(
            ok=True,
            status="ok",
            role="director",
            workspace="C:/repo",
            task_id="task-1",
            session_id="director-task-1",
            run_id="run-1",
            output="done",
        ),
        metadata={},
        context={},
    )

    assert evidence["receipt_recorded"] is True
    assert evidence["handoff_exported"] is False
    assert "no such table" in str(evidence.get("error_message") or "")


@pytest.mark.asyncio
async def test_execute_role_task_emits_cognitive_runtime_shadow_receipt(monkeypatch) -> None:
    class _FakeKernel:
        async def run(self, role: str, request):
            assert role == "director"
            assert str(request.task_id or "") == "task-1"
            return RoleTurnResult(
                content="Task execution finished.",
                tool_calls=[{"name": "apply_patch"}],
                tool_results=[
                    {
                        "name": "apply_patch",
                        "ok": True,
                        "tool_call_id": "call-task-1",
                        "result": {"applied": True, "patch_id": "patch-1"},
                        "metadata": {
                            "dispatch_status": "dispatched",
                            "operation_id": "tool.apply_patch.task-1",
                            "effect_receipt": {
                                "schema_version": "tool_effect_receipt.v1",
                                "tool_name": "apply_patch",
                                "operation_id": "tool.apply_patch.task-1",
                                "side_effect_class": "write",
                                "committed": True,
                            },
                        },
                    },
                ],
                batch_receipt={
                    "schema_version": "tool_batch_receipt.v1",
                    "results": [
                        {
                            "tool_name": "apply_patch",
                            "tool_call_id": "call-task-1",
                            "ok": True,
                            "committed": True,
                        }
                    ],
                },
                is_complete=True,
                execution_stats={"platform_retry_count": 0},
                metadata={
                    "turn_id": "turn-task-1",
                    "turn_envelope": {
                        "turn_id": "turn-task-1",
                        "lease_id": "lease-1",
                        "validation_id": "validation-1",
                        "role": "director",
                        "task_id": "task-1",
                    },
                },
            )

    runtime = RoleRuntimeService()
    shadow_calls: list[dict[str, object]] = []
    monkeypatch.setattr(runtime, "_get_kernel", lambda _workspace: _FakeKernel())
    monkeypatch.setattr(
        runtime,
        "_emit_cognitive_runtime_shadow_artifacts",
        lambda **kwargs: shadow_calls.append(dict(kwargs)),
    )

    result = await runtime.execute_role_task(
        ExecuteRoleTaskCommandV1(
            role="director",
            task_id="task-1",
            workspace="C:/repo",
            objective="Finish the governed task",
            run_id="run-1",
            session_id="session-1",
        )
    )

    assert result.ok is True
    assert result.status == "ok"
    assert len(shadow_calls) == 1
    assert shadow_calls[0]["source"] == "roles.runtime.execute_role_task"
    assert shadow_calls[0]["task_id"] == "task-1"
    assert shadow_calls[0]["run_id"] == "run-1"
    assert result.metadata["turn_envelope"]["turn_id"] == "turn-task-1"


def test_emit_cognitive_runtime_shadow_artifacts_respects_mode_off(monkeypatch) -> None:
    runtime = RoleRuntimeService()
    monkeypatch.setenv("KERNELONE_COGNITIVE_RUNTIME_MODE", "off")

    def _raise_if_called() -> NoReturn:
        raise AssertionError("cognitive runtime service should not be called when mode=off")

    monkeypatch.setattr(
        "polaris.cells.factory.cognitive_runtime.public.service.get_cognitive_runtime_public_service",
        _raise_if_called,
    )

    runtime._emit_cognitive_runtime_shadow_artifacts(
        source="roles.runtime.test",
        workspace="C:/repo",
        role="director",
        task_id="task-1",
        session_id="session-1",
        run_id="run-1",
        result=RoleExecutionResultV1(
            ok=True,
            status="ok",
            role="director",
            workspace="C:/repo",
            task_id="task-1",
            session_id="session-1",
            run_id="run-1",
            output="done",
        ),
    )


def test_emit_cognitive_runtime_shadow_artifacts_propagates_turn_envelope(monkeypatch) -> None:
    runtime = RoleRuntimeService()
    calls: dict[str, object] = {}

    class _FakeService:
        def record_runtime_receipt(self, command):
            calls["receipt"] = command

            class _ReceiptResult:
                ok = True
                receipt = type("Receipt", (), {"receipt_id": "receipt-1"})()

            return _ReceiptResult()

        def export_handoff_pack(self, command):
            calls["handoff"] = command
            return object()

        def close(self) -> None:
            calls["closed"] = True

    monkeypatch.setattr(
        "polaris.cells.factory.cognitive_runtime.public.service.get_cognitive_runtime_public_service",
        _FakeService,
    )

    runtime._emit_cognitive_runtime_shadow_artifacts(
        source="roles.runtime.test",
        workspace="C:/repo",
        role="director",
        task_id="task-1",
        session_id="session-1",
        run_id="run-1",
        result=RoleExecutionResultV1(
            ok=True,
            status="ok",
            role="director",
            workspace="C:/repo",
            task_id="task-1",
            session_id="session-1",
            run_id="run-1",
            output="done",
            metadata={
                "turn_id": "turn-1",
                "turn_envelope": {
                    "turn_id": "turn-1",
                    "projection_version": "state_first_context_os.v1",
                    "lease_id": "lease-1",
                    "validation_id": "validation-1",
                },
            },
        ),
    )

    receipt_command = cast("Any", calls["receipt"])
    handoff_command = cast("Any", calls["handoff"])
    assert receipt_command.turn_envelope["turn_id"] == "turn-1"
    assert receipt_command.turn_envelope["lease_id"] == "lease-1"
    assert handoff_command.turn_envelope["turn_id"] == "turn-1"
    assert "receipt-1" in handoff_command.turn_envelope["receipt_ids"]


def test_build_session_request_rehydrates_handoff_into_context_override(monkeypatch) -> None:
    class _FakeService:
        def rehydrate_handoff_pack(self, command):
            class _Result:
                ok = True
                rehydration = type(
                    "Rehydration",
                    (),
                    {
                        "rehydration_id": "rehydration-1",
                        "context_override": {
                            "state_first_context_os": {
                                "mode": "state_first_context_os.handoff_rehydrate",
                                "run_card": {
                                    "current_goal": "continue writer handoff",
                                    "open_loops": ["finish draft"],
                                },
                            },
                            "cognitive_runtime_handoff": {
                                "handoff_id": command.handoff_id,
                                "source_session_id": "session-director-1",
                            },
                        },
                        "metadata_patch": {
                            "handoff_rehydrated": True,
                            "handoff_source_session_id": "session-director-1",
                        },
                    },
                )()

            return _Result()

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        "polaris.cells.factory.cognitive_runtime.public.service.get_cognitive_runtime_public_service",
        _FakeService,
    )

    request = RoleRuntimeService._build_session_request(
        ExecuteRoleSessionCommandV1(
            role="writer",
            session_id="session-writer-1",
            workspace="C:/repo",
            user_message="继续写作",
            metadata={"handoff_id": "handoff-1"},
            context={
                "state_first_context_os": {
                    "run_card": {
                        "next_action_hint": "preserve explicit next step",
                    }
                }
            },
            stream=False,
        )
    )

    context_override_raw = request.context_override
    assert context_override_raw is not None, "context_override should not be None"
    context_override: dict[str, Any] = context_override_raw if isinstance(context_override_raw, dict) else {}
    state_first_raw = context_override.get("state_first_context_os")
    assert state_first_raw is not None, "state_first_context_os should not be None"
    state_first: dict[str, Any] = state_first_raw if isinstance(state_first_raw, dict) else {}
    assert state_first.get("mode") == "state_first_context_os.handoff_rehydrate"
    run_card_raw = state_first.get("run_card")
    assert run_card_raw is not None, "run_card should not be None"
    run_card: dict[str, Any] = run_card_raw if isinstance(run_card_raw, dict) else {}
    assert run_card.get("current_goal") == "continue writer handoff"
    assert run_card.get("next_action_hint") == "preserve explicit next step"
    metadata_raw = request.metadata
    assert metadata_raw is not None, "metadata should not be None"
    metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
    assert metadata.get("handoff_rehydrated") is True
    assert metadata.get("handoff_source_session_id") == "session-director-1"
    assert metadata.get("handoff_id") == "handoff-1"
