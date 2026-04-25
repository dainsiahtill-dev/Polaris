"""Artifact-level compression contracts for KernelOne.

This module provides public contracts for session artifact incremental compression,
enabling Cells to safely integrate with ContextOS compression capabilities without
importing internal modules.

Architecture
------------
::

    polaris/cells/roles/runtime/internal/session_artifact_store.py
            |
            v
    polaris.kernelone.context.artifact_compression (public)
            |
            v
    polaris.kernelone.context.context_os.compression_tracker (internal)

Usage
-----
::

    from polaris.kernelone.context.artifact_compression import compress_if_changed

    await compress_if_changed(
        session_id="session-123",
        original_hash="abc123",
        artifact={"type": "code", "content": "..."},
    )
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ArtifactCompressionRequest:
    """Immutable request for artifact compression."""

    session_id: str
    original_hash: str
    artifact: dict[str, Any]


@dataclass(frozen=True)
class ArtifactCompressionResult:
    """Result of artifact compression operation."""

    compressed: bool
    compression_ratio: float | None = None
    error: str | None = None


async def compress_if_changed(
    session_id: str,
    original_hash: str,
    artifact: dict[str, Any],
) -> ArtifactCompressionResult:
    """Trigger incremental compression for session artifacts.

    Only compresses if content has changed (based on hash) or
    compression is explicitly requested. This function provides a safe
    public interface to ContextOS compression capabilities.

    Args:
        session_id: Target session identifier.
        original_hash: Content hash for deduplication.
        artifact: Artifact data to compress.

    Returns:
        ArtifactCompressionResult with compression outcome.

    Note:
        This is a stub implementation. Full compression logic should
        be wired to IntelligentCompressor via CompressionStrategy.
    """
    try:
        from polaris.kernelone.context.context_os.compression_tracker import (
            CompressionStateTracker,
        )

        tracker = CompressionStateTracker()
        content = artifact.get("content", "")
        content_hash = tracker.compute_hash(content)

        # Check if already compressed
        if tracker.is_compressed(content_hash):
            return ArtifactCompressionResult(compressed=False, compression_ratio=0.0)

        # Record the hash - actual compression will be done lazily
        tracker.record(
            content_hash=content_hash,
            original_size=len(content),
            compressed_size=len(content),
            strategy="identity",
            content_type=artifact.get("type", "unknown"),
            duration_ms=0.0,
        )

        return ArtifactCompressionResult(compressed=True, compression_ratio=1.0)
    except ImportError:
        # Fallback when compression_tracker is not available
        return ArtifactCompressionResult(
            compressed=False,
            error="Compression tracker unavailable",
        )
    except Exception as exc:  # noqa: BLE001
        return ArtifactCompressionResult(
            compressed=False,
            error=str(exc),
        )


__all__ = [
    "ArtifactCompressionRequest",
    "ArtifactCompressionResult",
    "compress_if_changed",
]
