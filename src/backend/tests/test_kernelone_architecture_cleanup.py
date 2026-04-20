from __future__ import annotations

import asyncio
import contextvars
import importlib
from datetime import datetime as real_datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from polaris.cells.context.engine.public.service import get_anthropomorphic_context_v2
from polaris.kernelone.context.engine.models import ContextBudget, ContextItem, ContextPack
from polaris.kernelone.events.message_bus import Message, MessageBus, MessageType
from polaris.kernelone.llm.providers import (
    BaseProvider,
    ProviderInfo,
    ValidationResult,
    get_provider_manager,
    get_provider_registry,
    reset_provider_runtime,
)
from polaris.kernelone.llm.toolkit.contracts import ServiceLocator
from polaris.kernelone.llm.types import HealthResult, InvokeResult, ModelListResult, Usage

BACKEND_ROOT = Path(__file__).resolve().parents[1]
KERNELONE_ROOT = BACKEND_ROOT / "polaris" / "kernelone"


def _python_sources(root: Path) -> list[Path]:
    return [path for path in root.rglob("*.py") if path.is_file()]


def test_kernelone_internal_modules_do_not_import_io_utils_directly() -> None:
    offenders: list[str] = []
    for path in _python_sources(KERNELONE_ROOT):
        text = path.read_text(encoding="utf-8")
        if (
            "from polaris.infrastructure.compat.io_utils import" in text
            or "import polaris.kernelone.tools.io_utils" in text
        ):
            offenders.append(str(path.relative_to(BACKEND_ROOT)).replace("\\", "/"))

    assert offenders == []


def test_memory_package_does_not_import_context_modules() -> None:
    offenders: list[str] = []
    memory_root = KERNELONE_ROOT / "memory"
    patterns = (
        "from polaris.kernelone.context",
        "import polaris.kernelone.context",
        "from ..context",
        "from .context",
    )

    for path in _python_sources(memory_root):
        text = path.read_text(encoding="utf-8")
        if any(pattern in text for pattern in patterns):
            offenders.append(str(path.relative_to(BACKEND_ROOT)).replace("\\", "/"))

    assert offenders == []


def test_kernelone_no_longer_contains_io_utils_facade() -> None:
    io_utils_path = KERNELONE_ROOT / "tools" / "io_utils.py"
    assert io_utils_path.exists() is False


def test_anthropomorphic_context_v2_uses_context_engine(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def _fake_init(project_root: str) -> None:
        captured["project_root"] = project_root

    def _fake_persona(role: str, *, project_root: str | None = None) -> str:
        captured["role"] = role
        captured["persona_root"] = project_root
        return "persona"

    fake_pack = ContextPack(
        request_hash="ctx-hash",
        items=[
            ContextItem(id="mem-1", kind="memory", content_or_pointer="memory"),
            ContextItem(id="ref-1", kind="reflection", content_or_pointer="reflection"),
        ],
        rendered_prompt="rendered",
        total_tokens=42,
        total_chars=8,
    )

    def _fake_build(*args, **kwargs):
        captured["build_args"] = args
        captured["build_kwargs"] = kwargs
        return fake_pack, {}, ContextBudget(max_tokens=1000, max_chars=4000), ["memory"]

    monkeypatch.setattr(
        "polaris.cells.context.engine.public.service.init_anthropomorphic_modules",
        _fake_init,
    )
    monkeypatch.setattr(
        "polaris.cells.context.engine.public.service.get_persona_text",
        _fake_persona,
    )
    monkeypatch.setattr(
        "polaris.cells.context.engine.public.service.build_context_window",
        _fake_build,
    )

    bundle = get_anthropomorphic_context_v2(
        str(tmp_path),
        "pm",
        "query",
        3,
        "run-1",
        "pm.planning",
        events_path="runtime/events/llm.jsonl",
    )

    assert captured["project_root"] == str(tmp_path)
    assert captured["role"] == "pm"
    assert bundle["persona_instruction"] == "persona"
    assert bundle["anthropomorphic_context"] == "rendered"
    assert bundle["context_pack"] is fake_pack
    assert bundle["prompt_context_obj"].retrieved_mem_ids == ["mem-1"]
    assert bundle["prompt_context_obj"].retrieved_ref_ids == ["ref-1"]


class _CleanupProvider(BaseProvider):
    @classmethod
    def get_provider_info(cls) -> ProviderInfo:
        return ProviderInfo(
            name="Cleanup Provider",
            type="cleanup",
            description="cleanup provider",
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
        return ValidationResult(valid=True, errors=[], warnings=[], normalized_config={})

    def health(self, config: dict[str, object]) -> HealthResult:
        del config
        return HealthResult(ok=True, latency_ms=1)

    def list_models(self, config: dict[str, object]) -> ModelListResult:
        del config
        return ModelListResult(ok=True, models=[])

    def invoke(self, prompt: str, model: str, config: dict[str, object]) -> InvokeResult:
        del prompt, model, config
        return InvokeResult(ok=True, output="ok", latency_ms=1, usage=Usage())


def test_provider_runtime_has_single_registry_truth() -> None:
    reset_provider_runtime()
    registry = get_provider_registry()
    provider_manager = get_provider_manager()
    provider_manager.register_provider("cleanup_provider", _CleanupProvider)

    try:
        assert registry.get_provider("cleanup_provider") is _CleanupProvider
        instance = provider_manager.get_provider_instance("cleanup_provider")
        assert instance is not None
        assert isinstance(instance, _CleanupProvider)
    finally:
        reset_provider_runtime()


def test_service_locator_lazily_provides_default_token_estimator() -> None:
    ServiceLocator.reset()

    estimator = ServiceLocator.get_token_estimator()

    assert estimator is not None
    assert estimator.estimate_tokens("hello world") > 0


def test_encoding_module_has_no_import_time_side_effect(monkeypatch) -> None:
    import polaris.kernelone.fs.encoding as encoding_module

    stdout_calls: list[str] = []
    stderr_calls: list[str] = []

    class _FakeStream:
        def __init__(self, sink: list[str]) -> None:
            self._sink = sink

        def reconfigure(self, **kwargs) -> None:
            self._sink.append(str(kwargs.get("encoding") or ""))

    monkeypatch.setattr(encoding_module.sys, "stdout", _FakeStream(stdout_calls))
    monkeypatch.setattr(encoding_module.sys, "stderr", _FakeStream(stderr_calls))

    importlib.reload(encoding_module)

    assert stdout_calls == []
    assert stderr_calls == []


def test_kernelone_modules_do_not_call_enforce_utf8_at_import_time() -> None:
    targets = [
        KERNELONE_ROOT / "fs" / "encoding.py",
        KERNELONE_ROOT / "tools" / "__init__.py",
    ]

    for path in targets:
        text = path.read_text(encoding="utf-8")
        lines = [
            line.strip()
            for line in text.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        assert "enforce_utf8()" not in lines


async def _slow_handler(_message: Message) -> None:
    await asyncio.sleep(0.2)


def test_message_bus_async_handler_timeout_does_not_block(monkeypatch) -> None:
    monkeypatch.setattr(
        "polaris.kernelone.events.message_bus._ASYNC_HANDLER_TIMEOUT_SECONDS",
        0.01,
    )

    async def _exercise() -> float:
        bus = MessageBus()
        await bus.subscribe(MessageType.TASK_STARTED, _slow_handler)
        started = asyncio.get_running_loop().time()
        await bus.publish(
            Message(type=MessageType.TASK_STARTED, sender="tester", payload={})
        )
        return asyncio.get_running_loop().time() - started

    elapsed = asyncio.run(_exercise())
    assert elapsed < 0.1


def test_message_bus_can_be_used_across_event_loops() -> None:
    bus = MessageBus()

    async def _subscribe() -> bool:
        async def _handler(_message: Message) -> None:
            return None

        return await bus.subscribe(MessageType.TASK_STARTED, _handler)

    async def _publish() -> int:
        await bus.publish(Message(type=MessageType.TASK_STARTED, sender="tester"))
        return bus.subscriber_count(MessageType.TASK_STARTED)

    assert asyncio.run(_subscribe()) is True
    assert asyncio.run(_publish()) == 1


@pytest.mark.asyncio
async def test_task_queue_honors_priority_before_fifo() -> None:
    from polaris.kernelone.workflow.task_queue import TaskQueue

    queue = TaskQueue("runtime")
    await queue.add_task("director", "low", {}, priority=1)
    await queue.add_task("director", "high-1", {}, priority=10)
    await queue.add_task("director", "high-2", {}, priority=10)
    await queue.add_task("director", "medium", {}, priority=5)

    tasks = await queue.poll_tasks_batch("director", max_count=4, timeout=0.05)

    assert [task.task_id for task in tasks] == ["high-1", "high-2", "medium", "low"]


@pytest.mark.asyncio
async def test_timer_wheel_uses_monotonic_clock_for_dispatch(monkeypatch) -> None:
    import polaris.kernelone.workflow.timer_wheel as timer_wheel_module

    frozen_now = real_datetime(2026, 3, 22, 12, 0, 0)

    class _FrozenDateTime:
        @classmethod
        def now(cls) -> real_datetime:
            return frozen_now

    monkeypatch.setattr(timer_wheel_module, "datetime", _FrozenDateTime)

    wheel = timer_wheel_module.TimerWheel(tick_interval=0.01)
    fired = asyncio.Event()

    async def _callback() -> None:
        fired.set()

    await wheel.start()
    try:
        await wheel.schedule_timer(
            timer_id="timer-1",
            workflow_id="wf-1",
            delay_seconds=0.05,
            callback=_callback,
        )
        await asyncio.wait_for(fired.wait(), timeout=0.2)
    finally:
        await wheel.stop()


def test_retrieval_reranker_avoids_lambda_mutation_and_cache_key_collisions(
    monkeypatch,
) -> None:
    from polaris.kernelone.memory.retrieval_ranker import AdaptiveDiversityReranker, MMRReranker

    class _EmbeddingPort:
        def get_embedding(self, text: str, *, model: str) -> list[float]:
            del model
            return [float(len(text))]

    monkeypatch.setattr(
        "polaris.kernelone.memory.retrieval_ranker.get_default_embedding_port",
        lambda: _EmbeddingPort(),
    )

    prefix = "x" * 100
    item_a = SimpleNamespace(
        id="a",
        text=prefix + "A",
        keywords=["a"],
        kind="error",
        role="pm",
        step=10,
    )
    item_b = SimpleNamespace(
        id="b",
        text=prefix + "B",
        keywords=["b"],
        kind="info",
        role="qa",
        step=20,
    )

    reranker = MMRReranker(lambda_=0.5)
    reranker._get_embedding(item_a.text)
    reranker._get_embedding(item_b.text)
    assert len(reranker._embedding_cache) == 2

    adaptive = AdaptiveDiversityReranker()
    default_lambda = adaptive._mmr.lambda_
    adaptive.rerank(
        [item_a, item_b],
        {"a": 1.0, "b": 0.8},
        query_type="history",
        current_step=25,
        top_k=2,
    )
    assert adaptive._mmr.lambda_ == default_lambda


def test_subagent_isolated_workspace_is_cleaned_up(monkeypatch, tmp_path: Path) -> None:
    from polaris.kernelone.single_agent.subagent_runtime import SubagentConfig, SubagentSpawner

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    isolated_dir = tmp_path / "isolated-subagent"

    def _fake_mkdtemp(prefix: str) -> str:
        del prefix
        isolated_dir.mkdir()
        return str(isolated_dir)

    class _TextBlock:
        type = "text"
        text = "done"

    class _MessagesAPI:
        def create(self, **kwargs):
            del kwargs
            return SimpleNamespace(content=[_TextBlock()])

    class _LLMClient:
        messages = _MessagesAPI()

    monkeypatch.setattr(
        "polaris.kernelone.single_agent.subagent_runtime.tempfile.mkdtemp",
        _fake_mkdtemp,
    )

    spawner = SubagentSpawner(
        workspace=str(workspace),
        llm_client=_LLMClient(),
        model="test-model",
    )
    result = spawner.spawn(
        task_description="Return once",
        context={},
        config=SubagentConfig(
            max_iterations=1,
            timeout_seconds=1,
            isolated_workspace=True,
        ),
    )

    assert result.success is True
    assert isolated_dir.exists() is False


@pytest.mark.asyncio
async def test_copy_context_to_task_runs_in_captured_context() -> None:
    from polaris.kernelone.trace.async_utils import copy_context_to_task

    marker = contextvars.ContextVar("kernelone-copy-context-marker", default="unset")

    async def _read_marker() -> str:
        return marker.get()

    marker.set("captured")
    wrapped = copy_context_to_task(_read_marker())
    marker.set("mutated")

    assert await wrapped == "captured"


def test_error_mapping_distinguishes_permission_from_unknown_errors() -> None:
    from polaris.kernelone.llm.engine.error_mapping import NoRetryCategory, map_error_to_category

    permission_category, permission_retryable, permission_hint = map_error_to_category(
        RuntimeError("forbidden by policy")
    )
    unknown_category, unknown_retryable, unknown_hint = map_error_to_category(
        RuntimeError("opaque backend malfunction")
    )

    assert permission_category is NoRetryCategory.PERMISSION_DENIED
    assert permission_retryable is False
    assert permission_hint == "权限不足，请检查权限配置"
    assert unknown_category is NoRetryCategory.UNKNOWN_ERROR
    assert unknown_retryable is False
    assert unknown_hint == "发生未知错误"
