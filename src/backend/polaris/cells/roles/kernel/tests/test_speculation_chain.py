from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from polaris.cells.roles.kernel.internal.speculation.chain_speculator import (
    ChainSpeculator,
    ResultExtractor,
)
from polaris.cells.roles.kernel.internal.speculation.fingerprints import (
    build_env_fingerprint,
    build_spec_key,
    normalize_args,
)
from polaris.cells.roles.kernel.internal.speculation.metrics import SpeculationMetrics
from polaris.cells.roles.kernel.internal.speculation.models import (
    ShadowTaskRecord,
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


def _make_policy(tool_name: str = "read_file") -> ToolSpecPolicy:
    return ToolSpecPolicy(
        tool_name=tool_name,
        side_effect="readonly",
        cost="cheap",
        cancellability="cooperative",
        reusability="adoptable",
        speculate_mode="speculative_allowed",
        timeout_ms=500,
        cache_ttl_ms=30000,
    )


class TestResultExtractor:
    def test_extract_file_paths_from_dict_files(self) -> None:
        extractor = ResultExtractor()
        result = {"files": ["src/auth.ts", "src/middleware.ts"]}
        assert extractor.extract_file_paths(result) == ["src/auth.ts", "src/middleware.ts"]

    def test_extract_file_paths_from_dict_matches(self) -> None:
        extractor = ResultExtractor()
        result = {"matches": [{"path": "a.py"}, {"path": "b.py"}]}
        assert extractor.extract_file_paths(result) == ["a.py", "b.py"]

    def test_extract_file_paths_from_list(self) -> None:
        extractor = ResultExtractor()
        result = [{"path": "x.py"}, {"path": "y.py"}]
        assert extractor.extract_file_paths(result) == ["x.py", "y.py"]

    def test_extract_urls_from_dict_urls(self) -> None:
        extractor = ResultExtractor()
        result = {"urls": ["https://example.com/1", "https://example.com/2"]}
        assert extractor.extract_urls(result) == [
            "https://example.com/1",
            "https://example.com/2",
        ]

    def test_extract_urls_filters_localhost(self) -> None:
        extractor = ResultExtractor()
        result = {
            "urls": [
                "https://example.com/ok",
                "http://localhost:3000/admin",
                "https://127.0.0.1/secret",
            ]
        }
        assert extractor.extract_urls(result) == ["https://example.com/ok"]

    def test_extract_urls_from_string(self) -> None:
        extractor = ResultExtractor()
        result = "Check https://example.com and http://test.org for details."
        assert extractor.extract_urls(result) == [
            "https://example.com",
            "http://test.org",
        ]


class TestChainSpeculatorPredictDownstream:
    def test_repo_rg_predicts_read_file(self) -> None:
        registry_mock = AsyncMock(spec=ShadowTaskRegistry)
        speculator = ChainSpeculator(registry=registry_mock)
        predicted = speculator.predict_downstream(
            "repo_rg",
            {"matches": [{"path": "a.py"}, {"path": "b.py"}, {"path": "c.py"}, {"path": "d.py"}]},
        )
        assert len(predicted) == 3
        assert all(inv.tool_name == "read_file" for inv in predicted)
        assert predicted[0].arguments == {"path": "a.py"}

    def test_search_code_predicts_read_file(self) -> None:
        registry_mock = AsyncMock(spec=ShadowTaskRegistry)
        speculator = ChainSpeculator(registry=registry_mock)
        predicted = speculator.predict_downstream(
            "search_code",
            {"results": [{"path": "foo.py"}]},
        )
        assert len(predicted) == 1
        assert predicted[0].tool_name == "read_file"

    def test_web_search_predicts_fetch_url(self) -> None:
        registry_mock = AsyncMock(spec=ShadowTaskRegistry)
        speculator = ChainSpeculator(registry=registry_mock)
        predicted = speculator.predict_downstream(
            "web_search",
            {"results": [{"url": "https://a.com"}, {"url": "https://b.com"}]},
        )
        assert len(predicted) == 2
        assert all(inv.tool_name == "fetch_url" for inv in predicted)

    def test_unknown_tool_returns_empty(self) -> None:
        registry_mock = AsyncMock(spec=ShadowTaskRegistry)
        speculator = ChainSpeculator(registry=registry_mock)
        assert speculator.predict_downstream("unknown_tool", {"x": 1}) == []


@pytest.fixture
def chain_registry(monkeypatch: pytest.MonkeyPatch) -> ShadowTaskRegistry:
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
async def test_on_shadow_completed_triggers_downstream(
    chain_registry: ShadowTaskRegistry,
) -> None:
    speculator = ChainSpeculator(registry=chain_registry)
    chain_registry._on_shadow_completed = speculator.on_shadow_completed

    upstream_record = await chain_registry.start_shadow_task(
        turn_id="t_chain",
        candidate_id="c1",
        tool_name="repo_rg",
        normalized_args={"query": "auth"},
        spec_key="spec_repo_rg",
        env_fingerprint="fp1",
        policy=_make_policy("repo_rg"),
    )
    upstream_record.result = {"matches": [{"path": "src/auth.ts"}]}
    upstream_record.state = ShadowTaskState.COMPLETED

    downstream_records = await speculator.on_shadow_completed(upstream_record)

    assert len(downstream_records) == 1
    assert downstream_records[0].tool_name == "read_file"
    assert downstream_records[0].normalized_args == {"path": "src/auth.ts"}
    assert downstream_records[0].origin_turn_id == "t_chain"

    # Verify chain index
    assert downstream_records[0].task_id in chain_registry._chain_index.get(upstream_record.task_id, set())


@pytest.mark.asyncio
async def test_cascade_cancel_abandons_downstream(
    chain_registry: ShadowTaskRegistry,
) -> None:
    speculator = ChainSpeculator(registry=chain_registry)
    chain_registry._on_shadow_completed = speculator.on_shadow_completed

    upstream_record = await chain_registry.start_shadow_task(
        turn_id="t_cascade",
        candidate_id="c1",
        tool_name="repo_rg",
        normalized_args={"query": "auth"},
        spec_key="spec_repo_rg_cascade",
        env_fingerprint="fp1",
        policy=_make_policy("repo_rg"),
    )
    upstream_record.result = {"matches": [{"path": "src/auth.ts"}]}
    upstream_record.state = ShadowTaskState.COMPLETED

    downstream_records = await speculator.on_shadow_completed(upstream_record)
    assert len(downstream_records) == 1
    downstream_id = downstream_records[0].task_id

    # Cancel upstream should cascade cancel downstream
    await chain_registry.cancel(upstream_record.task_id, reason="upstream_cancelled")

    # Allow async cancel to propagate
    await asyncio.sleep(0.05)

    downstream_record = chain_registry._tasks_by_id.get(downstream_id)
    assert downstream_record is not None
    assert downstream_record.state in {ShadowTaskState.CANCELLED, ShadowTaskState.CANCEL_REQUESTED}


@pytest.mark.asyncio
async def test_chain_does_not_duplicate_existing_spec_key(
    chain_registry: ShadowTaskRegistry,
) -> None:
    speculator = ChainSpeculator(registry=chain_registry)

    # Pre-seed a read_file shadow
    args = {"path": "src/auth.ts"}
    spec_key = build_spec_key(
        tool_name="read_file",
        normalized_args=normalize_args("read_file", args),
        env_fingerprint=build_env_fingerprint(),
    )
    existing = await chain_registry.start_shadow_task(
        turn_id="t_dup",
        candidate_id="c0",
        tool_name="read_file",
        normalized_args=normalize_args("read_file", args),
        spec_key=spec_key,
        env_fingerprint=build_env_fingerprint(),
        policy=_make_policy(),
    )

    upstream_record = ShadowTaskRecord(
        task_id="upstream_1",
        origin_turn_id="t_dup",
        origin_candidate_id="c1",
        tool_name="repo_rg",
        normalized_args={"query": "auth"},
        spec_key="spec_repo_rg_dup",
        env_fingerprint="fp1",
        policy_snapshot=_make_policy("repo_rg"),
        state=ShadowTaskState.COMPLETED,
        result={"matches": [{"path": "src/auth.ts"}]},
    )

    downstream_records = await speculator.on_shadow_completed(upstream_record)
    assert len(downstream_records) == 0
    # Existing record should remain unchanged
    assert chain_registry._active_spec_index.get(spec_key) == existing.task_id
