"""Tests for ChainSpeculator and ResultExtractor — downstream speculation."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from polaris.cells.roles.kernel.internal.speculation.chain_speculator import (
    ChainSpeculator,
    PredictedInvocation,
    ResultExtractor,
)
from polaris.cells.roles.kernel.internal.speculation.registry import ShadowTaskRegistry


class TestResultExtractorFilePaths:
    """Tests for ResultExtractor.extract_file_paths()."""

    def test_extracts_from_files_field(self) -> None:
        extractor = ResultExtractor()
        result = {"files": ["a.py", "b.py", "c.py"]}
        paths = extractor.extract_file_paths(result)
        assert paths == ["a.py", "b.py", "c.py"]

    def test_extracts_from_matches_field(self) -> None:
        extractor = ResultExtractor()
        result = {"matches": [{"path": "src/main.py"}, {"path": "src/utils.py"}]}
        paths = extractor.extract_file_paths(result)
        assert paths == ["src/main.py", "src/utils.py"]

    def test_extracts_from_results_field(self) -> None:
        extractor = ResultExtractor()
        result = {"results": [{"path": "pkg/app.go"}, {"path": "pkg/config.go"}]}
        paths = extractor.extract_file_paths(result)
        assert paths == ["pkg/app.go", "pkg/config.go"]

    def test_extracts_from_paths_field(self) -> None:
        extractor = ResultExtractor()
        result = {"paths": ["lib/module.py", "lib/helpers.py"]}
        paths = extractor.extract_file_paths(result)
        assert paths == ["lib/module.py", "lib/helpers.py"]

    def test_extracts_from_list_of_dicts(self) -> None:
        extractor = ResultExtractor()
        result = [{"path": "foo.txt"}, {"path": "bar.txt"}]
        paths = extractor.extract_file_paths(result)
        assert paths == ["foo.txt", "bar.txt"]

    def test_extracts_from_list_of_strings(self) -> None:
        extractor = ResultExtractor()
        result = ["dir/file1.py", "dir/file2.py"]
        paths = extractor.extract_file_paths(result)
        assert paths == ["dir/file1.py", "dir/file2.py"]

    def test_extracts_from_plain_string_single_line(self) -> None:
        """Plain string treated as one line - entire string is returned if it has path-like segments."""
        extractor = ResultExtractor()
        # Entire string is returned as one path (line-based, not word-based)
        result = "some/path/file.txt and another/path/here.py"
        paths = extractor.extract_file_paths(result)
        # Entire line is returned since it contains path indicators
        assert len(paths) == 1

    def test_extracts_from_multiline_string(self) -> None:
        """Multiple lines are processed separately, each treated as a path candidate."""
        extractor = ResultExtractor()
        result = "dir/file1.py\ndir/file2.py"
        paths = extractor.extract_file_paths(result)
        assert "dir/file1.py" in paths
        assert "dir/file2.py" in paths

    def test_deduplicates_paths(self) -> None:
        extractor = ResultExtractor()
        result = {"files": ["a.py", "a.py", "b.py"]}
        paths = extractor.extract_file_paths(result)
        assert paths == ["a.py", "b.py"]

    def test_strips_whitespace(self) -> None:
        extractor = ResultExtractor()
        result = {"files": ["  space.py  ", "no_space.py"]}
        paths = extractor.extract_file_paths(result)
        assert "space.py" in paths
        assert "no_space.py" in paths

    def test_handles_empty_input(self) -> None:
        extractor = ResultExtractor()
        assert extractor.extract_file_paths({}) == []
        assert extractor.extract_file_paths([]) == []
        assert extractor.extract_file_paths("") == []
        assert extractor.extract_file_paths(None) == []


class TestResultExtractorUrls:
    """Tests for ResultExtractor.extract_urls()."""

    def test_extracts_from_urls_field(self) -> None:
        extractor = ResultExtractor()
        result = {"urls": ["https://example.com", "https://api.example.com"]}
        urls = extractor.extract_urls(result)
        assert "https://example.com" in urls
        assert "https://api.example.com" in urls

    def test_extracts_from_results_field(self) -> None:
        extractor = ResultExtractor()
        result = {"results": [{"url": "https://docs.example.com"}, {"url": "https://ref.example.com"}]}
        urls = extractor.extract_urls(result)
        assert "https://docs.example.com" in urls
        assert "https://ref.example.com" in urls

    def test_extracts_from_links_field(self) -> None:
        extractor = ResultExtractor()
        result = {"links": ["https://link1.com", "https://link2.com"]}
        urls = extractor.extract_urls(result)
        assert "https://link1.com" in urls
        assert "https://link2.com" in urls

    def test_extracts_from_list_of_dicts(self) -> None:
        extractor = ResultExtractor()
        result = [{"url": "https://a.com"}, {"url": "https://b.com"}]
        urls = extractor.extract_urls(result)
        assert "https://a.com" in urls
        assert "https://b.com" in urls

    def test_extracts_from_plain_string_with_regex(self) -> None:
        extractor = ResultExtractor()
        result = "Check https://example.com and http://test.org for details"
        urls = extractor.extract_urls(result)
        assert "https://example.com" in urls
        assert "http://test.org" in urls

    def test_blocks_localhost_urls(self) -> None:
        extractor = ResultExtractor()
        result = {"urls": ["https://example.com", "http://localhost:8080", "https://127.0.0.1"]}
        urls = extractor.extract_urls(result)
        assert "https://example.com" in urls
        assert "http://localhost:8080" not in urls
        assert "https://127.0.0.1" not in urls

    def test_blocks_private_ip_ranges(self) -> None:
        extractor = ResultExtractor()
        result = {"urls": ["https://example.com", "http://192.168.1.1", "http://10.0.0.1", "http://172.16.0.1"]}
        urls = extractor.extract_urls(result)
        assert "https://example.com" in urls
        assert "http://192.168.1.1" not in urls
        assert "http://10.0.0.1" not in urls
        assert "http://172.16.0.1" not in urls

    def test_blocks_admin_internal_paths(self) -> None:
        extractor = ResultExtractor()
        result = {"urls": ["https://example.com/public", "https://example.com/admin"]}
        urls = extractor.extract_urls(result)
        assert "https://example.com/public" in urls
        assert "https://example.com/admin" not in urls

    def test_blocks_file_scheme(self) -> None:
        extractor = ResultExtractor()
        result = {"urls": ["https://example.com", "file:///etc/passwd"]}
        urls = extractor.extract_urls(result)
        assert "https://example.com" in urls
        assert "file:///etc/passwd" not in urls

    def test_deduplicates_urls(self) -> None:
        extractor = ResultExtractor()
        result = {"urls": ["https://example.com", "https://example.com", "https://other.com"]}
        urls = extractor.extract_urls(result)
        assert urls.count("https://example.com") == 1

    def test_filters_long_urls(self) -> None:
        extractor = ResultExtractor()
        long_url = "https://example.com/" + "a" * 3000
        result = {"urls": ["https://short.com", long_url]}
        urls = extractor.extract_urls(result)
        assert "https://short.com" in urls
        assert long_url not in urls


class TestChainSpeculatorPredictDownstream:
    """Tests for ChainSpeculator.predict_downstream()."""

    @pytest.fixture
    def mock_registry(self) -> AsyncMock:
        return AsyncMock(spec=ShadowTaskRegistry)

    @pytest.fixture
    def speculator(self, mock_registry: AsyncMock) -> ChainSpeculator:
        return ChainSpeculator(registry=mock_registry)

    def test_repo_rg_predicts_read_file(self, speculator: ChainSpeculator) -> None:
        result = {"matches": [{"path": "src/app.py"}, {"path": "src/config.py"}, {"path": "src/main.go"}]}
        predicted = speculator.predict_downstream("repo_rg", result)
        assert len(predicted) == 3
        assert all(p.tool_name == "read_file" for p in predicted)
        assert all(p.arguments.get("path") is not None for p in predicted)

    def test_search_code_predicts_read_file(self, speculator: ChainSpeculator) -> None:
        result = {"matches": [{"path": "handlers/user.py"}]}
        predicted = speculator.predict_downstream("search_code", result)
        assert len(predicted) == 1
        assert predicted[0].tool_name == "read_file"

    def test_web_search_predicts_fetch_url(self, speculator: ChainSpeculator) -> None:
        result = {"urls": ["https://docs.python.org", "https://example.com"]}
        predicted = speculator.predict_downstream("web_search", result)
        assert len(predicted) == 2
        assert all(p.tool_name == "fetch_url" for p in predicted)

    def test_respects_top_k_limit(self, speculator: ChainSpeculator) -> None:
        result = {"matches": [{"path": f"file{i}.py"} for i in range(10)]}
        predicted = speculator.predict_downstream("repo_rg", result)
        # repo_rg has top_k=3
        assert len(predicted) == 3

    def test_web_search_respects_top_k_limit(self, speculator: ChainSpeculator) -> None:
        result = {"urls": [f"https://example.com/{i}" for i in range(10)]}
        predicted = speculator.predict_downstream("web_search", result)
        # web_search has top_k=2
        assert len(predicted) == 2

    def test_unknown_tool_returns_empty(self, speculator: ChainSpeculator) -> None:
        predicted = speculator.predict_downstream("unknown_tool", {"files": ["a.py"]})
        assert predicted == []

    def test_normalizes_tool_name_with_hyphens(self, speculator: ChainSpeculator) -> None:
        result = {"matches": [{"path": "a.py"}]}
        predicted = speculator.predict_downstream("search-code", result)
        assert len(predicted) == 1
        assert predicted[0].tool_name == "read_file"

    def test_predicted_invocation_has_correct_fields(self, speculator: ChainSpeculator) -> None:
        result = {"matches": [{"path": "main.py"}]}
        predicted = speculator.predict_downstream("repo_rg", result)
        assert len(predicted) == 1
        inv = predicted[0]
        assert isinstance(inv, PredictedInvocation)
        assert inv.tool_name == "read_file"
        assert inv.arguments == {"path": "main.py"}
        assert inv.predicted_by_tool == "repo_rg"


class TestChainSpeculatorOnShadowCompleted:
    """Tests for ChainSpeculator.on_shadow_completed()."""

    @pytest.fixture
    def mock_registry(self) -> AsyncMock:
        registry = AsyncMock(spec=ShadowTaskRegistry)
        registry.exists_active.return_value = False
        registry.start_shadow_task = AsyncMock()
        return registry

    @pytest.fixture
    def speculator(self, mock_registry: AsyncMock) -> ChainSpeculator:
        return ChainSpeculator(registry=mock_registry)

    @pytest.mark.asyncio
    async def test_non_completed_task_returns_empty(
        self, speculator: ChainSpeculator, mock_registry: AsyncMock
    ) -> None:
        """Non-completed tasks should not trigger downstream speculation."""
        from polaris.cells.roles.kernel.internal.speculation.models import (
            ShadowTaskRecord,
            ShadowTaskState,
            ToolSpecPolicy,
        )

        record = ShadowTaskRecord(
            task_id="task_1",
            origin_turn_id="turn_1",
            origin_candidate_id="cand_1",
            tool_name="repo_rg",
            normalized_args={},
            spec_key="spec_1",
            env_fingerprint="fp",
            policy_snapshot=ToolSpecPolicy(
                tool_name="repo_rg",
                side_effect="readonly",
                cost="cheap",
                cancellability="cooperative",
                reusability="adoptable",
                speculate_mode="speculative_allowed",
            ),
            state=ShadowTaskState.RUNNING,
            result={"matches": [{"path": "a.py"}]},
        )
        result = await speculator.on_shadow_completed(record)
        assert result == []
        mock_registry.start_shadow_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_completed_task_triggers_downstream(
        self, speculator: ChainSpeculator, mock_registry: AsyncMock
    ) -> None:
        """Completed tasks should create downstream shadow tasks."""
        from polaris.cells.roles.kernel.internal.speculation.models import (
            ShadowTaskRecord,
            ShadowTaskState,
            ToolSpecPolicy,
        )

        record = ShadowTaskRecord(
            task_id="task_1",
            origin_turn_id="turn_1",
            origin_candidate_id="cand_1",
            tool_name="repo_rg",
            normalized_args={},
            spec_key="spec_1",
            env_fingerprint="fp",
            policy_snapshot=ToolSpecPolicy(
                tool_name="repo_rg",
                side_effect="readonly",
                cost="cheap",
                cancellability="cooperative",
                reusability="adoptable",
                speculate_mode="speculative_allowed",
            ),
            state=ShadowTaskState.COMPLETED,
            result={"matches": [{"path": "a.py"}]},
        )
        mock_registry.start_shadow_task.return_value = record

        created = await speculator.on_shadow_completed(record)
        assert len(created) == 1
        mock_registry.start_shadow_task.assert_awaited()

    @pytest.mark.asyncio
    async def test_skips_already_active_downstream(self, speculator: ChainSpeculator, mock_registry: AsyncMock) -> None:
        """Should not create downstream if already active."""
        from polaris.cells.roles.kernel.internal.speculation.models import (
            ShadowTaskRecord,
            ShadowTaskState,
            ToolSpecPolicy,
        )

        record = ShadowTaskRecord(
            task_id="task_1",
            origin_turn_id="turn_1",
            origin_candidate_id="cand_1",
            tool_name="repo_rg",
            normalized_args={},
            spec_key="spec_1",
            env_fingerprint="fp",
            policy_snapshot=ToolSpecPolicy(
                tool_name="repo_rg",
                side_effect="readonly",
                cost="cheap",
                cancellability="cooperative",
                reusability="adoptable",
                speculate_mode="speculative_allowed",
            ),
            state=ShadowTaskState.COMPLETED,
            result={"matches": [{"path": "a.py"}]},
        )
        # Simulate downstream already exists
        mock_registry.exists_active.return_value = True

        created = await speculator.on_shadow_completed(record)
        assert created == []
        mock_registry.start_shadow_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_budget_denied_continues_to_next(self, speculator: ChainSpeculator, mock_registry: AsyncMock) -> None:
        """Should skip tasks that fail budget admission."""
        from polaris.cells.roles.kernel.internal.speculation.models import (
            ShadowTaskRecord,
            ShadowTaskState,
            ToolSpecPolicy,
        )

        record = ShadowTaskRecord(
            task_id="task_1",
            origin_turn_id="turn_1",
            origin_candidate_id="cand_1",
            tool_name="repo_rg",
            normalized_args={},
            spec_key="spec_1",
            env_fingerprint="fp",
            policy_snapshot=ToolSpecPolicy(
                tool_name="repo_rg",
                side_effect="readonly",
                cost="cheap",
                cancellability="cooperative",
                reusability="adoptable",
                speculate_mode="speculative_allowed",
            ),
            state=ShadowTaskState.COMPLETED,
            result={"matches": [{"path": "a.py"}, {"path": "b.py"}]},
        )
        # First call raises RuntimeError (budget denied), second succeeds
        mock_registry.start_shadow_task.side_effect = [RuntimeError("budget_denied"), record]

        created = await speculator.on_shadow_completed(record)
        # Only the second downstream task should succeed
        assert len(created) == 1
        assert mock_registry.start_shadow_task.call_count == 2
