from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from polaris.kernelone.llm.providers import (
    BaseProvider,
    ProviderInfo,
    ValidationResult,
    get_provider_manager,
)
from polaris.kernelone.llm.types import HealthResult, InvokeResult, ModelListResult, Usage
from polaris.kernelone.memory.memory_store import MemoryStore
from polaris.kernelone.memory.schema import MemoryItem
from polaris.kernelone.process.background_manager import BackgroundManagerV2


class _CountingProvider(BaseProvider):
    init_count = 0

    def __init__(self) -> None:
        type(self).init_count += 1

    @classmethod
    def get_provider_info(cls) -> ProviderInfo:
        return ProviderInfo(
            name="Counting Provider",
            type="kernelone-counting",
            description="test provider",
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


def test_kernelone_provider_manager_is_thread_safe() -> None:
    provider_type = "kernelone_counting_provider"
    provider_manager = get_provider_manager()
    provider_manager.register_provider(provider_type, _CountingProvider)
    _CountingProvider.init_count = 0

    with ThreadPoolExecutor(max_workers=16) as pool:
        instances = list(
            pool.map(
                lambda _: provider_manager.get_provider_instance(provider_type),
                range(50),
            )
        )

    first = instances[0]
    assert first is not None
    assert all(instance is first for instance in instances)
    assert _CountingProvider.init_count == 1


def test_memory_store_delete_reloads_disk_before_rewrite(tmp_path: Path) -> None:
    memory_file = tmp_path / "runtime" / "memory" / "MEMORY.jsonl"
    store_a = MemoryStore(str(memory_file), enable_cache=False)
    store_b = MemoryStore(str(memory_file), enable_cache=False)

    item_a = MemoryItem(
        source_event_id="event-a",
        step=1,
        timestamp=datetime.now(),
        role="director",
        type="observation",
        kind="info",
        text="first",
        importance=1,
        keywords=[],
        hash="hash-a",
        context={"run_id": "run-a", "event_id": "evt-a"},
    )
    item_b = MemoryItem(
        source_event_id="event-b",
        step=2,
        timestamp=datetime.now(),
        role="director",
        type="observation",
        kind="info",
        text="second",
        importance=1,
        keywords=[],
        hash="hash-b",
        context={"run_id": "run-b", "event_id": "evt-b"},
    )

    store_a.append(item_a)
    store_a.append(item_b)

    assert store_b.delete(item_a.id) is True

    reloaded = MemoryStore(str(memory_file), enable_cache=False)
    remaining_ids = [item.id for item in reloaded.memories]
    assert item_a.id not in remaining_ids
    assert item_b.id in remaining_ids


def test_background_manager_state_merge_preserves_other_writer_tasks(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(BackgroundManagerV2, "_start_queue_processor", lambda self: None)
    manager_a = BackgroundManagerV2(str(tmp_path), max_concurrent=1)
    manager_b = BackgroundManagerV2(str(tmp_path), max_concurrent=1)

    result_a = manager_a.submit(command="python --version")
    result_b = manager_b.submit(command="git --version")

    reloaded = BackgroundManagerV2(str(tmp_path), max_concurrent=1)
    tasks = reloaded.list(include_output=False)
    task_ids = {str(item.get("id")) for item in tasks}

    assert str(result_a["task_id"]) in task_ids
    assert str(result_b["task_id"]) in task_ids
