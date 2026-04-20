"""Pytest configuration and shared fixtures for KernelOne context tests.

This module provides:
    - KernelOne defaults (fs adapter, embedding port)
    - Temporary workspace fixtures
    - Common cache manager fixtures
    - Sample message sequences for continuity testing
    - Mock LLM provider fixtures
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator

# Backend root resolved for fixtures that need absolute paths
_BACKEND_ROOT = Path(__file__).resolve().parents[4]


# ---------------------------------------------------------------------------
# KernelOne Infrastructure Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def configure_kernelone_test_defaults():
    """Inject stable KernelOne defaults for all context tests.

    This autouse fixture ensures that:
    - FileSystem uses a local adapter (not requiring real workspace)
    - Embedding uses a stub adapter (no external calls)

    Fixes test isolation: saves and restores previous adapters to prevent
    state from persisting across test boundaries.
    """
    previous_fs_adapter = None
    previous_embedding_port = None
    adapter_set = False
    embedding_set = False

    try:
        from polaris.infrastructure.llm.adapters.stub_embedding_adapter import (
            StubEmbeddingAdapter,
        )
        from polaris.infrastructure.storage import LocalFileSystemAdapter
        from polaris.kernelone.fs import get_default_adapter, set_default_adapter
        from polaris.kernelone.llm.embedding import (
            get_default_embedding_port,
            set_default_embedding_port,
        )

        # Save previous state for restoration after test (may not exist)
        try:
            previous_fs_adapter = get_default_adapter()
        except RuntimeError:
            previous_fs_adapter = None

        try:
            previous_embedding_port = get_default_embedding_port()
        except RuntimeError:
            previous_embedding_port = None

        set_default_adapter(LocalFileSystemAdapter())
        set_default_embedding_port(StubEmbeddingAdapter())
        adapter_set = True
        embedding_set = True
    except ImportError as exc:
        pytest.skip(f"Cannot import polaris infrastructure: {exc}")

    yield

    # Restore previous state after test completes
    if adapter_set or embedding_set:
        try:
            from polaris.kernelone.fs import set_default_adapter
            from polaris.kernelone.llm.embedding import set_default_embedding_port

            if previous_fs_adapter is not None:
                set_default_adapter(previous_fs_adapter)
            if previous_embedding_port is not None:
                set_default_embedding_port(previous_embedding_port)
        except (RuntimeError, ValueError):
            # Best-effort restoration; tests may already be cleaning up
            pass


@pytest.fixture(scope="session", autouse=True)
def configure_runtime_env_for_context_tests() -> Generator[None, None, None]:
    """Force context tests to use writable in-repo runtime and temp roots.

    Windows sandbox environments can block `%LOCALAPPDATA%/Temp` or system cache
    roots. Context/cache tests depend on temp directories and runtime root
    resolution, so we pin both to backend-local `.tmp` directories.
    """
    base = _BACKEND_ROOT / ".tmp_pytest_context_runtime"
    temp_root = base / "tmp"
    runtime_root = base / "runtime"
    runtime_cache_root = base / "runtime_cache"
    for path in (temp_root, runtime_root, runtime_cache_root):
        path.mkdir(parents=True, exist_ok=True)

    previous: dict[str, str | None] = {}
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


# ---------------------------------------------------------------------------
# Workspace Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_workspace() -> Generator[Path, None, None]:
    """Create a temporary workspace directory that auto-cleans up.

    Yields:
        Path to temporary directory (auto-deleted after test).

    Example:
        def test_something(temp_workspace):
            repo_map = build_repo_map(str(temp_workspace))
            assert "root" in repo_map
    """
    base = _BACKEND_ROOT / ".tmp_pytest_context"
    base.mkdir(parents=True, exist_ok=True)
    workspace = base / f"hp_ctx_test_{uuid4().hex[:12]}"
    workspace.mkdir(parents=True, exist_ok=False)
    try:
        yield workspace
    finally:
        # Windows CI occasionally holds transient handles during fixture teardown.
        # Use best-effort cleanup to avoid hard failures unrelated to test intent.
        shutil.rmtree(workspace, ignore_errors=True)


@pytest.fixture
def tmp_path() -> Generator[Path, None, None]:
    """Override pytest tmp_path with a workspace-local writable directory."""
    base = _BACKEND_ROOT / ".tmp_pytest_context_tmp_path"
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"tmp_path_{uuid4().hex[:12]}"
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def sample_repo_structure(temp_workspace: Path) -> Generator[Path, None, None]:
    """Create a sample repository structure for testing.

    Creates:
        src/
            main.py
            utils.py
        tests/
            test_main.py
        docs/
            README.md
        pyproject.toml

    Yields:
        Path to workspace root.
    """
    (temp_workspace / "src").mkdir(exist_ok=True)
    (temp_workspace / "tests").mkdir(exist_ok=True)
    (temp_workspace / "docs").mkdir(exist_ok=True)

    # Create sample Python files
    main_py = temp_workspace / "src" / "main.py"
    main_py.write_text(
        '"""Main application module."""\n\ndef main():\n    print("Hello")\n\nclass App:\n    def run(self):\n        pass\n',
        encoding="utf-8",
    )

    utils_py = temp_workspace / "src" / "utils.py"
    utils_py.write_text(
        '"""Utility functions."""\n\ndef helper():\n    return 42\n\ndef format_data(data):\n    return str(data)\n',
        encoding="utf-8",
    )

    test_main = temp_workspace / "tests" / "test_main.py"
    test_main.write_text(
        '"""Tests for main module."""\n\ndef test_main():\n    assert True\n',
        encoding="utf-8",
    )

    readme = temp_workspace / "docs" / "README.md"
    readme.write_text("# Sample Project\n", encoding="utf-8")

    pyproject = temp_workspace / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "sample"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )

    yield temp_workspace


# ---------------------------------------------------------------------------
# Cache Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tiered_cache(temp_workspace: Path):
    """Create a TieredAssetCacheManager for testing.

    Uses short TTLs for fast test execution.
    """
    try:
        from polaris.kernelone.context.cache_manager import TieredAssetCacheManager

        cache = TieredAssetCacheManager(
            workspace=temp_workspace,
            hot_slice_ttl=5.0,
            projection_ttl=10.0,
            repo_map_ttl=15.0,
            symbol_index_ttl=15.0,
            session_continuity_ttl=30.0,
        )
        yield cache
        # Cleanup
        cache._hot_slices.clear()
        cache._session_continuity.clear()
    except ImportError as exc:
        pytest.skip(f"Cannot import TieredAssetCacheManager: {exc}")


# ---------------------------------------------------------------------------
# Continuity Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_messages() -> list[dict[str, Any]]:
    """Return a sample message sequence for continuity testing.

    Contains:
        - Low-signal greeting (should be filtered)
        - High-signal engineering messages
        - Open loop user request
        - Assistant commitment
    """
    return [
        {"role": "user", "content": "hello", "sequence": 0},
        {"role": "assistant", "content": "Hi! How can I help?", "sequence": 1},
        {"role": "user", "content": "Please fix the bug in src/main.py at line 10", "sequence": 2},
        {
            "role": "assistant",
            "content": "I'll fix the bug and then verify the tests pass.",
            "sequence": 3,
        },
        {
            "role": "user",
            "content": "Also refactor the utils module to use a class",
            "sequence": 4,
        },
        {
            "role": "assistant",
            "content": "I'll refactor utils.py and update the tests.",
            "sequence": 5,
        },
    ]


@pytest.fixture
def continuity_engine():
    """Create a SessionContinuityEngine with default policy."""
    try:
        from polaris.kernelone.context.session_continuity import SessionContinuityEngine

        return SessionContinuityEngine()
    except ImportError as exc:
        pytest.skip(f"Cannot import SessionContinuityEngine: {exc}")


# ---------------------------------------------------------------------------
# Budget Gate Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def budget_gate_128k():
    """Create a ContextBudgetGate with 128K window and 80% safety margin."""
    try:
        from polaris.kernelone.context.budget_gate import ContextBudgetGate

        return ContextBudgetGate(model_window=128_000, safety_margin=0.80)
    except ImportError as exc:
        pytest.skip(f"Cannot import ContextBudgetGate: {exc}")


@pytest.fixture
def budget_gate_tight():
    """Create a ContextBudgetGate with tight budget for boundary testing."""
    try:
        from polaris.kernelone.context.budget_gate import ContextBudgetGate

        return ContextBudgetGate(model_window=10_000, safety_margin=0.10)
    except ImportError as exc:
        pytest.skip(f"Cannot import ContextBudgetGate: {exc}")


# ---------------------------------------------------------------------------
# Mock LLM Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_llm_response():
    """Create a mock LLM response for testing."""
    response = MagicMock()
    response.content = "This is a mock LLM response."
    response.usage = {"prompt_tokens": 100, "completion_tokens": 50}
    return response


@pytest.fixture
def mock_embedding():
    """Create a mock embedding function that returns fixed vectors."""

    def embed(text: str) -> list[float]:
        # Simple hash-based pseudo-embedding for testing
        import hashlib

        h = hashlib.sha256(text.encode("utf-8")).digest()
        return [b / 255.0 for b in h[:32]]

    return embed


# ---------------------------------------------------------------------------
# Repo Map Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def repo_map_result(sample_repo_structure: Path) -> dict[str, Any]:
    """Build a repo map for the sample repository."""
    try:
        from polaris.kernelone.context.repo_map import build_repo_map

        return build_repo_map(
            str(sample_repo_structure),
            languages=["python"],
            max_files=50,
            max_lines=100,
            per_file_lines=10,
        )
    except ImportError as exc:
        pytest.skip(f"Cannot import build_repo_map: {exc}")


# ---------------------------------------------------------------------------
# Working Set Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def working_set_assembler(budget_gate_128k):
    """Create a WorkingSetAssembler for testing."""
    try:
        from polaris.kernelone.context import (
            DefaultExplorationPolicy,
            WorkingSetAssembler,
        )

        return WorkingSetAssembler(
            workspace="/fake",
            budget_gate=budget_gate_128k,
            policy=DefaultExplorationPolicy(),
        )
    except ImportError as exc:
        pytest.skip(f"Cannot import WorkingSetAssembler: {exc}")
