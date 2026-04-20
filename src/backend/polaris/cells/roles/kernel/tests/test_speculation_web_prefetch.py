from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from polaris.cells.roles.kernel.internal.speculation.chain_speculator import (
    ChainSpeculator,
    ResultExtractor,
)
from polaris.cells.roles.kernel.internal.speculation.metrics import SpeculationMetrics
from polaris.cells.roles.kernel.internal.speculation.models import (
    ShadowTaskState,
    ToolSpecPolicy,
)
from polaris.cells.roles.kernel.internal.speculation.registry import (
    EphemeralSpecCache,
    ShadowTaskRegistry,
)
from polaris.cells.roles.kernel.internal.speculative_executor import (
    SpeculativeExecutor,
)
from polaris.cells.roles.kernel.internal.tool_batch_runtime import ToolBatchRuntime


def _make_policy(tool_name: str = "fetch_url") -> ToolSpecPolicy:
    return ToolSpecPolicy(
        tool_name=tool_name,
        side_effect="readonly",
        cost="medium",
        cancellability="cooperative",
        reusability="adoptable",
        speculate_mode="speculative_allowed",
        timeout_ms=500,
        cache_ttl_ms=30000,
    )


class TestResultExtractorUrlFiltering:
    def test_blocks_localhost(self) -> None:
        extractor = ResultExtractor()
        urls = [
            "https://example.com/safe",
            "http://localhost:8080",
            "https://127.0.0.1/api",
            "http://0.0.0.0:3000",
        ]
        assert extractor._normalize_urls(urls) == ["https://example.com/safe"]

    def test_blocks_private_ips(self) -> None:
        extractor = ResultExtractor()
        urls = [
            "https://example.com/safe",
            "http://192.168.1.1/admin",
            "http://10.0.0.1/secret",
        ]
        assert extractor._normalize_urls(urls) == ["https://example.com/safe"]

    def test_blocks_admin_paths(self) -> None:
        extractor = ResultExtractor()
        urls = [
            "https://example.com/admin",
            "https://example.com/internal/dashboard",
        ]
        assert extractor._normalize_urls(urls) == []

    def test_blocks_file_protocol(self) -> None:
        extractor = ResultExtractor()
        urls = ["file:///etc/passwd", "https://example.com/ok"]
        assert extractor._normalize_urls(urls) == ["https://example.com/ok"]

    def test_blocks_long_urls(self) -> None:
        extractor = ResultExtractor()
        long_url = "https://example.com/" + "x" * 3000
        urls = ["https://example.com/short", long_url]
        assert extractor._normalize_urls(urls) == ["https://example.com/short"]

    def test_deduplicates_urls(self) -> None:
        extractor = ResultExtractor()
        urls = ["https://a.com", "https://a.com", "https://b.com"]
        assert extractor._normalize_urls(urls) == ["https://a.com", "https://b.com"]


class TestChainSpeculatorWebPrefetch:
    def test_web_search_predicts_top_2_urls(self) -> None:
        registry_mock = AsyncMock(spec=ShadowTaskRegistry)
        speculator = ChainSpeculator(registry=registry_mock)
        predicted = speculator.predict_downstream(
            "web_search",
            {
                "results": [
                    {"url": "https://a.com"},
                    {"url": "https://b.com"},
                    {"url": "https://c.com"},
                ]
            },
        )
        assert len(predicted) == 2
        assert predicted[0].arguments == {"url": "https://a.com"}
        assert predicted[1].arguments == {"url": "https://b.com"}

    def test_web_search_skips_blocked_urls(self) -> None:
        registry_mock = AsyncMock(spec=ShadowTaskRegistry)
        speculator = ChainSpeculator(registry=registry_mock)
        predicted = speculator.predict_downstream(
            "web_search",
            {
                "results": [
                    {"url": "http://localhost:3000"},
                    {"url": "https://safe.com"},
                ]
            },
        )
        assert len(predicted) == 1
        assert predicted[0].arguments == {"url": "https://safe.com"}


@pytest.fixture
def web_registry(monkeypatch: pytest.MonkeyPatch) -> ShadowTaskRegistry:
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
async def test_web_search_shadow_triggers_fetch_url_shadows(
    web_registry: ShadowTaskRegistry,
) -> None:
    speculator = ChainSpeculator(registry=web_registry)
    web_registry._on_shadow_completed = speculator.on_shadow_completed

    upstream_record = await web_registry.start_shadow_task(
        turn_id="t_web",
        candidate_id="c1",
        tool_name="web_search",
        normalized_args={"query": "FastAPI auth"},
        spec_key="spec_web_search",
        env_fingerprint="fp1",
        policy=_make_policy("web_search"),
    )
    upstream_record.result = {
        "results": [
            {"url": "https://docs.example.com/1"},
            {"url": "https://blog.example.com/2"},
        ]
    }
    upstream_record.state = ShadowTaskState.COMPLETED

    downstream_records = await speculator.on_shadow_completed(upstream_record)

    assert len(downstream_records) == 2
    assert downstream_records[0].tool_name == "fetch_url"
    assert downstream_records[0].normalized_args == {"url": "https://docs.example.com/1"}
    assert downstream_records[1].normalized_args == {"url": "https://blog.example.com/2"}

    # Allow tasks to start
    await asyncio.sleep(0.05)

    # Both should be tracked in the registry
    for record in downstream_records:
        assert record.task_id in web_registry._tasks_by_id


@pytest.mark.asyncio
async def test_web_prefetch_abandon_turn_cascades(
    web_registry: ShadowTaskRegistry,
) -> None:
    speculator = ChainSpeculator(registry=web_registry)
    web_registry._on_shadow_completed = speculator.on_shadow_completed

    upstream_record = await web_registry.start_shadow_task(
        turn_id="t_web_abandon",
        candidate_id="c1",
        tool_name="web_search",
        normalized_args={"query": "test"},
        spec_key="spec_web_search_abandon",
        env_fingerprint="fp1",
        policy=_make_policy("web_search"),
    )
    upstream_record.result = {"results": [{"url": "https://example.com"}]}
    upstream_record.state = ShadowTaskState.COMPLETED

    downstream_records = await speculator.on_shadow_completed(upstream_record)
    assert len(downstream_records) == 1
    downstream_id = downstream_records[0].task_id

    await web_registry.abandon_turn("t_web_abandon", reason="refusal")
    await asyncio.sleep(0.05)

    downstream = web_registry._tasks_by_id.get(downstream_id)
    assert downstream is not None
    assert downstream.state in {
        ShadowTaskState.CANCELLED,
        ShadowTaskState.ABANDONED,
    }
