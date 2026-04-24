"""Local conftest for governance tests.

This conftest provides minimal fixtures that avoid importing polaris modules
which may have syntax errors, allowing tests to run independently.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def governance_workspace(tmp_path_factory):
    """Provide a session-scoped temporary workspace."""
    return tmp_path_factory.mktemp("governance_workspace")


@pytest.fixture
def temp_workspace():
    """Create a temporary workspace with minimal structure for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        # Create minimal docs structure
        docs_dir = workspace / "docs"
        migration_dir = docs_dir / "migration"
        graph_dir = docs_dir / "graph"
        catalog_dir = graph_dir / "catalog"

        migration_dir.mkdir(parents=True, exist_ok=True)
        catalog_dir.mkdir(parents=True, exist_ok=True)

        yield workspace


@pytest.fixture(autouse=True)
def mock_env_vars(monkeypatch):
    """Set default environment variables for testing."""
    monkeypatch.setenv("PYTHONUTF8", "1")
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    yield


# Override the autouse fixture from tests/conftest.py to avoid polaris imports
# Governance tests run external scripts and don't need DI singleton resets
@pytest.fixture(autouse=True)
def reset_singletons():
    """Override parent fixture - governance tests use subprocess, not DI."""
    yield
