from __future__ import annotations

from types import SimpleNamespace
from typing import NoReturn

import pytest
from polaris.cells.director.execution.service import DirectorConfig, DirectorService
from polaris.domain.entities import TaskPriority


class SecurityService:
    @staticmethod
    def is_command_safe(command: str) -> SimpleNamespace:
        return SimpleNamespace(is_safe=True, reason="")


class TodoService:
    @staticmethod
    def on_round_complete() -> None:
        return None


class TokenService:
    pass


class TranscriptService:
    def start_session(self, session_id: str, metadata: dict | None = None) -> None:
        return None

    def end_session(self) -> None:
        return None

    def record_message(self, role: str, content: str, metadata: dict | None = None) -> None:
        return None


class MessageBus:
    async def broadcast(self, message_type, source, payload=None) -> None:
        return None

    async def subscribe(self, message_type, handler) -> None:
        return None

    async def unsubscribe(self, message_type, handler) -> None:
        return None


class TaskService:
    def __init__(self) -> None:
        self.completed: list[tuple[str, object]] = []
        self.failed: list[tuple[str, str]] = []

    async def create_task(
        self,
        *,
        subject: str,
        description: str,
        command: str | None,
        priority,
        blocked_by: list[str],
        timeout_seconds: int | None,
        metadata: dict | None,
    ):
        return SimpleNamespace(
            id="task-1",
            subject=subject,
            description=description,
            command=command,
            timeout_seconds=timeout_seconds,
            metadata=dict(metadata or {}),
        )

    async def on_task_started(self, task_id: str) -> None:
        return None

    async def on_task_completed(self, task_id: str, result) -> None:
        self.completed.append((task_id, result))

    async def on_task_failed(self, task_id: str, error: str, recoverable: bool = False) -> None:
        self.failed.append((task_id, error))


class WorkerService:
    async def initialize(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def get_workers(self) -> list[object]:
        return []


def _build_service() -> DirectorService:
    return DirectorService(
        DirectorConfig(workspace="C:/repo"),
        security=SecurityService(),  # type: ignore[arg-type]
        todo=TodoService(),  # type: ignore[arg-type]
        token=TokenService(),  # type: ignore[arg-type]
        transcript=TranscriptService(),  # type: ignore[arg-type]
        message_bus=MessageBus(),  # type: ignore[arg-type]
        task_service=TaskService(),  # type: ignore[arg-type]
        worker_service=WorkerService(),  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_submit_task_emits_cognitive_runtime_shadow_receipt(monkeypatch) -> None:
    service = _build_service()
    shadow_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        service,
        "_emit_cognitive_runtime_shadow_task_artifacts",
        lambda **kwargs: shadow_calls.append(dict(kwargs)),
    )

    task = await service.submit_task(
        subject="Governed task",
        description="Do work",
        command="echo ok",
        priority=TaskPriority.HIGH,
        metadata={"session_id": "session-1", "run_id": "run-1"},
    )

    assert task.id == "task-1"
    assert len(shadow_calls) == 1
    assert shadow_calls[0]["receipt_type"] == "director_task_submitted"
    assert shadow_calls[0]["export_handoff"] is False


@pytest.mark.asyncio
async def test_execute_task_completion_emits_cognitive_runtime_shadow_receipt(monkeypatch) -> None:
    service = _build_service()
    shadow_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        service,
        "_emit_cognitive_runtime_shadow_task_artifacts",
        lambda **kwargs: shadow_calls.append(dict(kwargs)),
    )

    async def _fake_run_command(command, timeout):
        return SimpleNamespace(
            success=True,
            duration_ms=42,
            evidence=[],
        )

    monkeypatch.setattr(
        service,
        "_run_command",
        _fake_run_command,
    )

    task = SimpleNamespace(
        id="task-2",
        command="echo ok",
        timeout_seconds=30,
        metadata={"session_id": "session-2", "run_id": "run-2"},
    )
    worker = SimpleNamespace(
        id="worker-1",
        release_task=lambda result: None,
    )

    await service._execute_task(task, worker)  # type: ignore[arg-type]

    assert len(shadow_calls) == 1
    assert shadow_calls[0]["receipt_type"] == "director_task_completed"
    assert shadow_calls[0]["export_handoff"] is True


def test_emit_shadow_task_artifacts_respects_mode_off(monkeypatch) -> None:
    service = _build_service()
    monkeypatch.setenv("KERNELONE_COGNITIVE_RUNTIME_MODE", "off")

    def _raise_if_called() -> NoReturn:
        raise AssertionError("cognitive runtime service should not be called when mode=off")

    monkeypatch.setattr(
        "polaris.cells.factory.cognitive_runtime.public.service.get_cognitive_runtime_public_service",
        _raise_if_called,
    )

    task = SimpleNamespace(
        id="task-off",
        metadata={"session_id": "session-off", "run_id": "run-off"},
    )
    service._emit_cognitive_runtime_shadow_task_artifacts(
        task=task,
        receipt_type="director_task_completed",
        payload={"success": True},
        export_handoff=True,
    )
