"""Context compressors for intelligent code and text compression.

This module provides layered compression strategies:
- CodeStructureCompressor: AST-aware compression preserving signatures
- SemanticCompressor: LLM-based semantic summarization
- CompressionPipeline: Orchestrates multi-layer compression

Design constraints:
- All compressors are pure functions (no side effects)
- Compression decisions are tracked for auditability
- Token estimation is consistent with ChunkBudgetTracker
"""

from __future__ import annotations

from .code_structure_compressor import (
    CodeCompressionResult,
    CodeStructureCompressor,
    CompressionLevel,
)
from .pipeline import CompressionPipeline, CompressionStage

__all__ = [
    "CodeCompressionResult",
    "CodeStructureCompressor",
    "CompressionLevel",
    "CompressionPipeline",
    "CompressionStage",
]
