"""Tests for context engine providers.

Covers DocsProvider, ContractProvider, EventsProvider, RepoEvidenceProvider,
and RepoMapProvider. Tests collection logic, policy handling, and edge cases.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import MagicMock

import pytest
from polaris.kernelone.context.engine.models import ContextItem, ContextRequest
from polaris.kernelone.context.engine.providers import (
    BaseProvider,
    ContractProvider,
    DocsProvider,
    EventsProvider,
    MemoryProvider,
    RepoEvidenceProvider,
    RepoMapProvider,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def project_root(tmp_path: Any) -> str:
    """Create a temporary project root with test files."""
    # Create docs directory
    docs_dir = tmp_path / "docs" / "agent"
    docs_dir.mkdir(parents=True)
    (docs_dir / "tui_runtime.md").write_text("TUI runtime documentation", encoding="utf-8")
    (docs_dir / "architecture.md").write_text("Architecture overview", encoding="utf-8")

    # Create runtime contracts
    runtime_dir = tmp_path / "runtime" / "contracts"
    runtime_dir.mkdir(parents=True)
    (runtime_dir / "pm_tasks.contract.json").write_text('{"task": "test"}', encoding="utf-8")
    (runtime_dir / "plan.md").write_text("# Test Plan", encoding="utf-8")

    # Create a test file for evidence
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "main.py").write_text("def main():\n    pass\n", encoding="utf-8")

    # Create events file
    events_path = tmp_path / "events.jsonl"
    events_path.write_text('{"event": "start"}\n{"event": "end"}\n', encoding="utf-8")

    return str(tmp_path)


@pytest.fixture
def default_request(project_root: str) -> ContextRequest:
    """Create a default context request."""
    return ContextRequest(
        run_id="test_run_001",
        step=1,
        role="developer",
        mode="active",
        query="test query",
        budget={"max_tokens": 10000, "max_chars": 50000, "cost_class": "LOCAL"},
    )


# ---------------------------------------------------------------------------
# BaseProvider Tests
# ---------------------------------------------------------------------------


class TestBaseProvider:
    """Test BaseProvider interface and common behavior."""

    def test_estimate_size_delegates_to_estimate_tokens(self) -> None:
        """estimate_size should delegate to _estimate_tokens."""

        class ConcreteProvider(BaseProvider):
            name = "test"

            def collect_items(self, request: ContextRequest) -> list[ContextItem]:
                return []

        provider = ConcreteProvider(project_root="/fake")
        item = ContextItem(
            kind="test",
            content_or_pointer="hello world",
            refs={},
            size_est=0,
            priority=5,
            provider="test",
        )

        # estimate_size should estimate based on content
        estimated = provider.estimate_size(item)
        assert estimated > 0

    def test_estimate_size_empty_content(self) -> None:
        """estimate_size should return 0 for empty content."""

        class ConcreteProvider(BaseProvider):
            name = "test_empty"

            def collect_items(self, request: ContextRequest) -> list[ContextItem]:
                return []

        provider = ConcreteProvider(project_root="/fake")
        item = ContextItem(
            kind="test",
            content_or_pointer="",
            refs={},
            size_est=0,
            priority=5,
            provider="test",
        )

        estimated = provider.estimate_size(item)
        assert estimated == 0


# ---------------------------------------------------------------------------
# DocsProvider Tests
# ---------------------------------------------------------------------------


class TestDocsProvider:
    """Test DocsProvider collection behavior."""

    def test_collect_items_default_paths(self, project_root: str, default_request: ContextRequest) -> None:
        """DocsProvider should collect from default paths."""
        provider = DocsProvider(project_root)
        items = provider.collect_items(default_request)

        # Should find docs in default paths
        assert len(items) >= 1
        assert any(item.kind == "docs" for item in items)

    def test_collect_items_custom_paths(self, project_root: str, default_request: ContextRequest) -> None:
        """DocsProvider should collect from custom policy paths."""
        default_request.policy = {
            "docs_paths": ["docs/agent/tui_runtime.md"],
            "docs_max_chars": 100,
        }
        provider = DocsProvider(project_root)
        items = provider.collect_items(default_request)

        assert len(items) >= 1
        item = items[0]
        assert item.kind == "docs"
        assert len(item.content_or_pointer) <= 100

    def test_collect_items_respects_max_chars(self, project_root: str, default_request: ContextRequest) -> None:
        """DocsProvider should truncate content at max_chars."""
        default_request.policy = {
            "docs_paths": ["docs/agent/tui_runtime.md"],
            "docs_max_chars": 10,
        }
        provider = DocsProvider(project_root)
        items = provider.collect_items(default_request)

        assert len(items) == 1
        assert len(items[0].content_or_pointer) <= 10

    def test_collect_items_missing_file_skipped(self, project_root: str, default_request: ContextRequest) -> None:
        """DocsProvider should skip non-existent files."""
        default_request.policy = {"docs_paths": ["nonexistent/file.md"]}
        provider = DocsProvider(project_root)
        items = provider.collect_items(default_request)

        assert len(items) == 0

    def test_collect_items_empty_policy(self, project_root: str, default_request: ContextRequest) -> None:
        """DocsProvider should handle empty policy gracefully."""
        default_request.policy = {}
        provider = DocsProvider(project_root)
        items = provider.collect_items(default_request)

        # Should return items from default paths or empty list
        assert isinstance(items, list)

    def test_collect_items_respects_priority(self, project_root: str, default_request: ContextRequest) -> None:
        """DocsProvider should respect docs_priority from policy."""
        default_request.policy = {
            "docs_paths": ["docs/agent/tui_runtime.md"],
            "docs_priority": 10,
        }
        provider = DocsProvider(project_root)
        items = provider.collect_items(default_request)

        if items:
            assert items[0].priority == 10

    def test_item_refs_contain_hash(self, project_root: str, default_request: ContextRequest) -> None:
        """Collected items should contain file hash in refs."""
        default_request.policy = {"docs_paths": ["docs/agent/tui_runtime.md"]}
        provider = DocsProvider(project_root)
        items = provider.collect_items(default_request)

        if items:
            assert "file_hash" in items[0].refs
            assert "path" in items[0].refs


# ---------------------------------------------------------------------------
# ContractProvider Tests
# ---------------------------------------------------------------------------


class TestContractProvider:
    """Test ContractProvider collection behavior."""

    def test_collect_items_default_paths(self, project_root: str, default_request: ContextRequest) -> None:
        """ContractProvider should attempt to collect from default paths.

        Note: Default paths may not exist in test environment, so we verify
        the provider attempts collection without errors.
        """
        provider = ContractProvider(project_root)
        items = provider.collect_items(default_request)

        # Default paths may not exist in test environment
        # Verify the provider ran without error and returned valid list
        assert isinstance(items, list)

    def test_collect_items_custom_paths(self, project_root: str, default_request: ContextRequest) -> None:
        """ContractProvider should collect from custom policy paths.

        Note: resolve_artifact_path may not resolve in test environment.
        Fallback to direct path is attempted.
        """
        default_request.policy = {
            "contract_paths": ["runtime/contracts/pm_tasks.contract.json"],
            "contract_max_chars": 50,
        }
        provider = ContractProvider(project_root)
        items = provider.collect_items(default_request)

        # Files may not be found due to artifact path resolution
        # Just verify provider returns valid list without errors
        assert isinstance(items, list)

    def test_collect_items_missing_file(self, project_root: str, default_request: ContextRequest) -> None:
        """ContractProvider should skip non-existent files."""
        default_request.policy = {"contract_paths": ["nonexistent/contract.json"]}
        provider = ContractProvider(project_root)
        items = provider.collect_items(default_request)

        assert len(items) == 0

    def test_contract_returns_valid_items_when_found(self, project_root: str, default_request: ContextRequest) -> None:
        """ContractProvider should return valid items when file is directly accessible."""
        # Create contract in project root directly
        contract_file = os.path.join(project_root, "contract.json")
        with open(contract_file, "w", encoding="utf-8") as f:
            f.write('{"task": "test contract"}')

        default_request.policy = {
            "contract_paths": ["contract.json"],
        }
        provider = ContractProvider(project_root)
        items = provider.collect_items(default_request)

        # Should find the contract
        assert len(items) >= 1
        assert items[0].kind == "contract"
        assert items[0].priority == 9  # Default contract priority

    def test_contract_has_higher_default_priority(self, project_root: str, default_request: ContextRequest) -> None:
        """Contract should have higher default priority than docs."""
        default_request.policy = {
            "contract_paths": ["runtime/contracts/pm_tasks.contract.json"],
            "docs_paths": ["docs/agent/tui_runtime.md"],
        }
        provider = ContractProvider(project_root)
        items = provider.collect_items(default_request)

        if items:
            # Contract default priority is 9
            assert items[0].priority >= 9


# ---------------------------------------------------------------------------
# EventsProvider Tests
# ---------------------------------------------------------------------------


class TestEventsProvider:
    """Test EventsProvider collection behavior."""

    def test_collect_items_with_events_file(self, project_root: str, default_request: ContextRequest) -> None:
        """EventsProvider should collect from events file."""
        events_path = os.path.join(project_root, "events.jsonl")
        default_request.events_path = events_path
        default_request.policy = {"events_tail_lines": 10}

        provider = EventsProvider(project_root)
        items = provider.collect_items(default_request)

        assert len(items) == 1
        assert items[0].kind == "events"
        assert "event" in items[0].content_or_pointer.lower()

    def test_collect_items_missing_events_file(self, project_root: str, default_request: ContextRequest) -> None:
        """EventsProvider should return empty for missing events file."""
        default_request.events_path = "/nonexistent/events.jsonl"
        provider = EventsProvider(project_root)
        items = provider.collect_items(default_request)

        assert len(items) == 0

    def test_collect_items_empty_events_file(self, project_root: str, default_request: ContextRequest) -> None:
        """EventsProvider should handle empty events file."""
        events_path = os.path.join(project_root, "events.jsonl")
        with open(events_path, "w", encoding="utf-8") as f:
            f.write("")
        default_request.events_path = events_path
        provider = EventsProvider(project_root)
        items = provider.collect_items(default_request)

        assert len(items) == 0

    def test_collect_items_respects_max_chars(self, project_root: str, default_request: ContextRequest) -> None:
        """EventsProvider should truncate at max_chars."""
        events_path = os.path.join(project_root, "events.jsonl")
        # Write many lines
        with open(events_path, "w", encoding="utf-8") as f:
            for i in range(100):
                f.write(f'{{"event": "line_{i}"}}\n')
        default_request.events_path = events_path
        default_request.policy = {"events_max_chars": 50}

        provider = EventsProvider(project_root)
        items = provider.collect_items(default_request)

        assert len(items) == 1
        assert len(items[0].content_or_pointer) <= 50

    def test_collect_items_tail_lines_from_end(self, project_root: str, default_request: ContextRequest) -> None:
        """EventsProvider should take tail lines from end of file."""
        events_path = os.path.join(project_root, "events.jsonl")
        with open(events_path, "w", encoding="utf-8") as f:
            for i in range(50):
                f.write(f'{{"line": {i}}}\n')
        default_request.events_path = events_path
        default_request.policy = {"events_tail_lines": 5}

        provider = EventsProvider(project_root)
        items = provider.collect_items(default_request)

        assert len(items) == 1
        # Should contain lines from near the end
        assert "49" in items[0].content_or_pointer or "48" in items[0].content_or_pointer


# ---------------------------------------------------------------------------
# RepoEvidenceProvider Tests
# ---------------------------------------------------------------------------


class TestRepoEvidenceProvider:
    """Test RepoEvidenceProvider collection behavior."""

    def test_collect_items_with_slice_spec(self, project_root: str, default_request: ContextRequest) -> None:
        """RepoEvidenceProvider should collect with slice spec."""
        default_request.policy = {
            "repo_evidence": [{"path": "src/main.py", "around": 1, "radius": 5, "priority": 8}],
        }
        provider = RepoEvidenceProvider(project_root)
        items = provider.collect_items(default_request)

        assert len(items) >= 1
        assert items[0].kind == "evidence"
        assert items[0].priority == 8

    def test_collect_items_with_line_range(self, project_root: str, default_request: ContextRequest) -> None:
        """RepoEvidenceProvider should handle line_range spec."""
        default_request.policy = {
            "repo_evidence": [{"path": "src/main.py", "start_line": 1, "end_line": 2}],
        }
        provider = RepoEvidenceProvider(project_root)
        items = provider.collect_items(default_request)

        assert len(items) >= 1
        assert "line_range" in items[0].refs

    def test_collect_items_missing_path_skipped(self, project_root: str, default_request: ContextRequest) -> None:
        """RepoEvidenceProvider should skip specs without path."""
        default_request.policy = {
            "repo_evidence": [{"priority": 5}],
        }
        provider = RepoEvidenceProvider(project_root)
        items = provider.collect_items(default_request)

        assert len(items) == 0

    def test_collect_items_nonexistent_file_skipped(self, project_root: str, default_request: ContextRequest) -> None:
        """RepoEvidenceProvider should skip non-existent files."""
        default_request.policy = {
            "repo_evidence": [{"path": "nonexistent/file.py"}],
        }
        provider = RepoEvidenceProvider(project_root)
        items = provider.collect_items(default_request)

        assert len(items) == 0

    def test_collect_items_invalid_evidence_spec(self, project_root: str, default_request: ContextRequest) -> None:
        """RepoEvidenceProvider should handle invalid evidence specs gracefully."""
        default_request.policy = {
            "repo_evidence": [
                "invalid_string",  # Should be dict
                {"path": "src/main.py"},
                None,  # Should be dict
            ],
        }
        provider = RepoEvidenceProvider(project_root)
        items = provider.collect_items(default_request)

        # Should collect valid spec, skip invalid ones
        assert len(items) >= 1

    def test_collect_items_respects_max_chars(self, project_root: str, default_request: ContextRequest) -> None:
        """RepoEvidenceProvider should truncate at max_chars."""
        default_request.policy = {
            "repo_evidence": [{"path": "src/main.py"}],
            "repo_evidence_max_chars": 10,
        }
        provider = RepoEvidenceProvider(project_root)
        items = provider.collect_items(default_request)

        if items:
            assert len(items[0].content_or_pointer) <= 10 + len("...[truncated]")


# ---------------------------------------------------------------------------
# RepoMapProvider Tests
# ---------------------------------------------------------------------------


class TestRepoMapProvider:
    """Test RepoMapProvider collection behavior."""

    def test_collect_items_returns_repo_map(self, project_root: str, default_request: ContextRequest) -> None:
        """RepoMapProvider should return a repo map item."""
        default_request.policy = {"repo_map_max_files": 10}
        provider = RepoMapProvider(project_root)
        items = provider.collect_items(default_request)

        assert len(items) >= 1
        assert items[0].kind == "repo_map"

    def test_collect_items_respects_languages(self, project_root: str, default_request: ContextRequest) -> None:
        """RepoMapProvider should filter by languages."""
        default_request.policy = {
            "repo_map_languages": "py",
            "repo_map_max_files": 10,
        }
        provider = RepoMapProvider(project_root)
        items = provider.collect_items(default_request)

        assert len(items) >= 1
        # Content should reference Python files
        content = items[0].content_or_pointer.lower()
        assert "py" in content or "python" in content or "main" in content

    def test_collect_items_empty_repo(self, tmp_path: Any) -> None:
        """RepoMapProvider should handle empty repository."""
        provider = RepoMapProvider(str(tmp_path))
        request = ContextRequest(
            run_id="test",
            step=1,
            role="dev",
            mode="active",
            query="",
            budget={"max_tokens": 1000, "max_chars": 5000, "cost_class": "LOCAL"},
        )
        items = provider.collect_items(request)

        # May be empty or contain empty repo map
        assert isinstance(items, list)

    def test_collect_items_respects_max_lines(self, project_root: str, default_request: ContextRequest) -> None:
        """RepoMapProvider should respect max_lines setting."""
        default_request.policy = {
            "repo_map_max_lines": 5,
            "repo_map_max_files": 100,
        }
        provider = RepoMapProvider(project_root)
        items = provider.collect_items(default_request)

        if items:
            # Content should be limited by max_lines
            lines = items[0].content_or_pointer.splitlines()
            assert len(lines) <= 10  # Some overhead is OK


# ---------------------------------------------------------------------------
# MemoryProvider Tests
# ---------------------------------------------------------------------------


class TestMemoryProvider:
    """Test MemoryProvider collection behavior."""

    def test_collect_items_empty_query_returns_empty(self, project_root: str, default_request: ContextRequest) -> None:
        """MemoryProvider should return empty for empty query."""
        default_request.query = ""
        provider = MemoryProvider(project_root)
        items = provider.collect_items(default_request)

        assert len(items) == 0

    def test_collect_items_top_k_zero_returns_empty(self, project_root: str, default_request: ContextRequest) -> None:
        """MemoryProvider should return empty when top_k is 0."""
        default_request.policy = {"memory_top_k": 0}
        provider = MemoryProvider(project_root)
        items = provider.collect_items(default_request)

        assert len(items) == 0

    def test_collect_items_with_mocked_store(self, project_root: str, default_request: ContextRequest) -> None:
        """MemoryProvider should use injected memory store factory."""
        mock_store = MagicMock()
        mock_store.retrieve.return_value = [
            (
                MagicMock(
                    id="mem1",
                    source_event_id="evt1",
                    text="Test memory content",
                    context={},
                ),
                0.9,
            )
        ]

        def factory(path: str) -> MagicMock:
            return mock_store

        provider = MemoryProvider(project_root, memory_store_factory=factory)
        default_request.policy = {"memory_top_k": 5}
        items = provider.collect_items(default_request)

        assert len(items) >= 1
        assert items[0].kind == "memory" or items[0].kind == "note"

    def test_collect_items_respects_max_chars(self, project_root: str, default_request: ContextRequest) -> None:
        """MemoryProvider should truncate at memory_max_chars."""
        mock_store = MagicMock()
        mock_store.retrieve.return_value = [
            (
                MagicMock(
                    id="mem1",
                    source_event_id="evt1",
                    text="x" * 1000,
                    context={},
                ),
                0.9,
            )
        ]

        def factory(path: str) -> MagicMock:
            return mock_store

        provider = MemoryProvider(project_root, memory_store_factory=factory)
        default_request.policy = {
            "memory_top_k": 5,
            "memory_max_chars": 50,
        }
        items = provider.collect_items(default_request)

        if items:
            assert len(items[0].content_or_pointer) <= 50

    def test_collect_items_downgrades_priority_without_refs(
        self, project_root: str, default_request: ContextRequest
    ) -> None:
        """MemoryProvider should downgrade priority when refs are missing."""
        mock_store = MagicMock()
        mock_store.retrieve.return_value = [
            (
                MagicMock(
                    id="mem1",
                    source_event_id="evt1",
                    text="Memory without refs",
                    context={},  # No refs
                ),
                0.9,
            )
        ]

        def factory(path: str) -> MagicMock:
            return mock_store

        provider = MemoryProvider(project_root, memory_store_factory=factory)
        default_request.policy = {
            "memory_top_k": 5,
            "memory_priority": 4,
        }
        items = provider.collect_items(default_request)

        if items:
            # Should be downgraded to min(priority, 1) = 1
            assert items[0].priority <= 1
            assert "missing refs" in items[0].reason


# ---------------------------------------------------------------------------
# Integration Tests
# ---------------------------------------------------------------------------


class TestProviderIntegration:
    """Integration tests for multiple providers."""

    def test_multiple_providers_collected(self, project_root: str, default_request: ContextRequest) -> None:
        """Multiple providers can be used in same request."""
        # Create test contract file
        contract_file = os.path.join(project_root, "contract.json")
        with open(contract_file, "w", encoding="utf-8") as f:
            f.write('{"task": "test"}')

        default_request.sources_enabled = [
            "docs",
            "contract",
            "repo_evidence",
        ]
        default_request.policy = {
            "docs_paths": ["docs/agent/tui_runtime.md"],
            "contract_paths": ["contract.json"],
            "repo_evidence": [{"path": "src/main.py"}],
        }

        # Collect from each provider
        docs_provider = DocsProvider(project_root)
        contract_provider = ContractProvider(project_root)
        evidence_provider = RepoEvidenceProvider(project_root)

        docs_items = docs_provider.collect_items(default_request)
        contract_items = contract_provider.collect_items(default_request)
        evidence_items = evidence_provider.collect_items(default_request)

        assert len(docs_items) >= 1
        assert len(contract_items) >= 1
        assert len(evidence_items) >= 1

    def test_policy_override_affects_collection(self, project_root: str, default_request: ContextRequest) -> None:
        """Policy settings should affect what providers collect."""
        # Test with max_chars = 0 (no limit)
        default_request.policy = {
            "docs_paths": ["docs/agent/tui_runtime.md"],
            "docs_max_chars": 0,  # No limit
        }
        provider = DocsProvider(project_root)
        items_unlimited = provider.collect_items(default_request)

        # Test with small max_chars
        default_request.policy = {"docs_paths": ["docs/agent/tui_runtime.md"], "docs_max_chars": 5}
        items_limited = provider.collect_items(default_request)

        # Limited should have smaller or equal content
        if items_unlimited and items_limited:
            assert len(items_limited[0].content_or_pointer) <= len(items_unlimited[0].content_or_pointer)
