from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from polaris.cells.runtime.task_runtime.public.task_board_contract import TaskBoard, TaskStatus
from polaris.kernelone.audit.invariant_sentinel import run_invariant_sentinel
from polaris.kernelone.llm.providers import (
    BaseProvider,
    ProviderInfo,
    ValidationResult,
    get_provider_manager,
    reset_provider_runtime,
)
from polaris.kernelone.llm.types import HealthResult, InvokeResult, ModelListResult, Usage
from polaris.kernelone.memory.reflection import ReflectionGenerator
from polaris.kernelone.memory.schema import MemoryItem
from polaris.kernelone.process import runtime_control
from polaris.kernelone.process.background_manager import BackgroundManagerV2
from polaris.kernelone.process.ollama_utils import (
    KernelOllamaAdapter,
    get_embedding,
    set_default_ollama_adapter,
)
from polaris.kernelone.storage import resolve_runtime_path


class _FailingOllamaAdapter(KernelOllamaAdapter):
    def generate(
        self,
        *,
        prompt: str,
        model: str,
        timeout_seconds: int,
        host: str,
    ) -> dict[str, object]:
        del prompt, model, timeout_seconds, host
        raise RuntimeError("generate failed")

    def embed(
        self,
        *,
        text: str,
        model: str,
        timeout_seconds: int,
        host: str,
    ) -> list[float]:
        del text, model, timeout_seconds, host
        raise RuntimeError("embed failed")


class _FallbackEmbeddingPort:
    def __init__(self, result: list[float] | None = None, *, should_fail: bool = False) -> None:
        self._result = list(result or [])
        self._should_fail = should_fail

    def get_embedding(self, text: str, model: str | None = None) -> list[float]:
        del text, model
        if self._should_fail:
            raise RuntimeError("fallback failed")
        return list(self._result)


class _BrokenProvider(BaseProvider):
    @classmethod
    def get_provider_info(cls) -> ProviderInfo:
        return ProviderInfo(
            name="Broken Provider",
            type="broken",
            description="broken for tests",
            version="1.0",
            author="tests",
            documentation_url="",
            supported_features=[],
            cost_class="LOCAL",
            provider_category="LLM",
            autonomous_file_access=False,
            requires_file_interfaces=False,
            model_listing_method="NONE",
        )

    @classmethod
    def get_default_config(cls) -> dict[str, object]:
        return {}

    @classmethod
    def validate_config(cls, config: dict[str, object]) -> ValidationResult:
        del config
        raise RuntimeError("validation exploded")

    def health(self, config: dict[str, object]) -> HealthResult:
        del config
        return HealthResult(ok=True, latency_ms=1)

    def list_models(self, config: dict[str, object]) -> ModelListResult:
        del config
        return ModelListResult(ok=True, models=[])

    def invoke(self, prompt: str, model: str, config: dict[str, object]) -> InvokeResult:
        del prompt, model, config
        return InvokeResult(ok=True, output="ok", latency_ms=1, usage=Usage())


def _build_memory_item() -> MemoryItem:
    return MemoryItem(
        source_event_id="evt_1",
        step=1,
        timestamp=datetime.now(),
        role="pm",
        type="observation",
        kind="info",
        text="important context",
        importance=5,
        keywords=["important"],
        hash="hash_1",
        context={"run_id": "run_1"},
    )


def test_invariant_sentinel_logs_unreadable_director_result(
    monkeypatch,
    caplog,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("polaris.kernelone.audit.invariant_sentinel.emit_event", lambda *args, **kwargs: None)
    events_path = tmp_path / "runtime" / "events" / "runtime.events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_text("", encoding="utf-8")

    director_result_path = tmp_path / "runtime" / "state" / "director_result.json"
    director_result_path.parent.mkdir(parents=True, exist_ok=True)
    director_result_path.write_text("{not-json", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        result = run_invariant_sentinel(
            events_path=str(events_path),
            run_id="run-observe-1",
            step=1,
            director_result_path=str(director_result_path),
        )

    assert result["ok"] is True
    assert "could not parse director result" in caplog.text


def test_get_embedding_logs_and_uses_fallback(monkeypatch, caplog) -> None:
    set_default_ollama_adapter(_FailingOllamaAdapter())
    monkeypatch.setattr(
        "polaris.kernelone.process.ollama_utils.get_default_embedding_port",
        lambda: _FallbackEmbeddingPort([0.1, 0.2, 0.3]),
    )

    try:
        with caplog.at_level(logging.WARNING):
            result = get_embedding("hello", "demo-model")
    finally:
        set_default_ollama_adapter(None)

    assert result == [0.1, 0.2, 0.3]
    assert "falling back to embedding port" in caplog.text


def test_get_embedding_logs_when_all_paths_fail(monkeypatch, caplog) -> None:
    set_default_ollama_adapter(_FailingOllamaAdapter())
    monkeypatch.setattr(
        "polaris.kernelone.process.ollama_utils.get_default_embedding_port",
        lambda: _FallbackEmbeddingPort(should_fail=True),
    )

    try:
        with caplog.at_level(logging.WARNING):
            result = get_embedding("hello", "demo-model")
    finally:
        set_default_ollama_adapter(None)

    assert result == []
    assert "falling back to embedding port" in caplog.text
    assert "fallback failed" in caplog.text


def test_reflection_generator_logs_exhausted_retries(
    monkeypatch,
    caplog,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "polaris.kernelone.memory.reflection.get_template",
        lambda _name: "{{memories_text}}",
    )
    monkeypatch.setattr(
        "polaris.kernelone.memory.reflection.invoke_ollama",
        lambda *args, **kwargs: SimpleNamespace(output="not-json", metadata={"error": "boom"}),
    )
    monkeypatch.setattr("polaris.kernelone.memory.reflection.time.sleep", lambda _seconds: None)

    generator = ReflectionGenerator(model="demo", workspace_root=str(tmp_path))

    with caplog.at_level(logging.WARNING):
        result = generator.generate([_build_memory_item()], current_step=5)

    assert result == []
    assert "attempt failed" in caplog.text
    assert "exhausted retries" in caplog.text


def test_background_manager_logs_corrupt_task_state(
    caplog,
    tmp_path: Path,
) -> None:
    seed_manager = BackgroundManagerV2(str(tmp_path), auto_start=False, load_state=False)
    state_path = Path(seed_manager.state_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "tasks": {
                    "bad-task": {
                        "id": "bad-task",
                        "status": "queued",
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING):
        reloaded = BackgroundManagerV2(str(tmp_path), auto_start=False, load_state=True)

    assert reloaded.list() == []
    assert "Skipping corrupt background task state record" in caplog.text
    reloaded.close(cancel_running=True)
    seed_manager.close(cancel_running=True)


def test_background_manager_rejects_submit_after_close(tmp_path: Path) -> None:
    manager = BackgroundManagerV2(str(tmp_path), auto_start=False, load_state=False)
    manager.close(cancel_running=True)

    result = manager.submit(command="echo hello")

    assert result["ok"] is False
    assert result["error"] == "manager_closed"


def test_provider_manager_logs_validation_failures(caplog) -> None:
    reset_provider_runtime()
    provider_manager = get_provider_manager()
    provider_manager.register_provider("broken", _BrokenProvider)

    try:
        with caplog.at_level(logging.WARNING):
            result = provider_manager.validate_provider_config("broken", {})
    finally:
        reset_provider_runtime()

    assert result is False
    assert "Error validating config for broken" in caplog.text


def test_runtime_control_logs_pid_termination_failures(monkeypatch, caplog) -> None:
    def _raise_os_error(pid: int, sig: int) -> None:
        del pid, sig
        raise OSError("termination failed")

    monkeypatch.setattr(runtime_control.os, "kill", _raise_os_error)

    with caplog.at_level(logging.WARNING):
        result = runtime_control.terminate_pid(123)

    assert result is False
    assert "Failed to terminate pid=123" in caplog.text or "taskkill failed for pid=123" in caplog.text


def test_task_board_terminal_event_write_does_not_spawn_thread(monkeypatch, tmp_path: Path) -> None:
    def _unexpected_thread(*args, **kwargs):
        del args, kwargs
        raise AssertionError("TaskBoard should not spawn background thread for terminal event write")

    monkeypatch.setattr("polaris.cells.runtime.task_runtime.internal.task_board.threading.Thread", _unexpected_thread)

    board = TaskBoard(str(tmp_path))
    task = board.create(subject="t1")
    updated = board.update_status(task.id, TaskStatus.COMPLETED, result_summary="done")

    assert updated is not None
    events_path = Path(resolve_runtime_path(str(tmp_path), "runtime/events/taskboard.terminal.events.jsonl"))
    assert events_path.is_file()
    content = events_path.read_text(encoding="utf-8")
    assert "task_id" in content
