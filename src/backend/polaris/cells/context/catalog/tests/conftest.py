"""测试配置和fixture"""

import pytest
from polaris.kernelone.llm.embedding import set_default_embedding_port


class MockEmbeddingPort:
    """Mock embedding port for testing"""

    def get_embedding(self, text: str) -> list[float]:
        """Return a simple mock embedding vector"""
        # Return a fixed-size vector for testing
        return [0.1] * 384

    def get_fingerprint(self) -> str:
        """Return a mock fingerprint"""
        return "test/mock:test"


@pytest.fixture(autouse=True)
def mock_embedding_port():
    """自动为所有测试设置mock embedding port"""
    port = MockEmbeddingPort()
    set_default_embedding_port(port)
    yield port
