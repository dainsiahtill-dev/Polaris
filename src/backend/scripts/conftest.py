from __future__ import annotations

import os
import sys

import pytest

# Add backend to path before polaris imports
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from polaris.infrastructure.llm.adapters.stub_embedding_adapter import StubEmbeddingAdapter
from polaris.infrastructure.storage import LocalFileSystemAdapter
from polaris.kernelone.fs import set_default_adapter
from polaris.kernelone.llm.embedding import set_default_embedding_port


@pytest.fixture(autouse=True)
def configure_kernelone_test_defaults() -> None:
    """Inject stable KernelOne defaults for all test trees."""
    set_default_adapter(LocalFileSystemAdapter())
    set_default_embedding_port(StubEmbeddingAdapter())
    yield
