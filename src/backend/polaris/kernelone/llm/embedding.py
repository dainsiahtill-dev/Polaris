"""KernelOne embedding port and adapter.

This module provides the KernelEmbeddingPort interface and global adapter
management for text embedding services.

For test isolation, use reset_default_embedding_port() to clear the singleton.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class KernelEmbeddingPort(ABC):
    """Port for text embedding services."""

    @abstractmethod
    def get_embedding(self, text: str, model: str | None = None) -> list[float]:
        """Generate a vector embedding for the given text."""
        pass

    @abstractmethod
    def get_fingerprint(self) -> str:
        """Get a unique fingerprint for the current embedding runtime (model + device)."""
        pass


_default_embedding_port: KernelEmbeddingPort | None = None


def set_default_embedding_port(port: KernelEmbeddingPort) -> None:
    """Set the default embedding port."""
    global _default_embedding_port
    _default_embedding_port = port


def get_default_embedding_port() -> KernelEmbeddingPort:
    """Get the default embedding port.

    Raises:
        RuntimeError: If port is not set.
    """
    global _default_embedding_port
    if _default_embedding_port is None:
        raise RuntimeError("Default KernelEmbeddingPort not set. It must be injected by the bootstrap layer.")
    return _default_embedding_port


def reset_default_embedding_port() -> None:
    """Reset the default embedding port.

    This function is primarily for test isolation. It clears the singleton
    so tests can inject fresh adapters without state pollution.
    """
    global _default_embedding_port
    _default_embedding_port = None
