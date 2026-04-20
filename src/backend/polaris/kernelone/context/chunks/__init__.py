"""KernelOne Prompt Chunk subsystem.

This package provides chunk-aware prompt assembly for KernelOne runtime.

Key components:
- taxonomy: Chunk type definitions and metadata
- budget: Chunk-level token budget tracking
- receipt: Final request debug receipt
- assembler: Main chunk assembler

Usage::

    from polaris.kernelone.context.chunks import (
        PromptChunkAssembler,
        AssemblyContext,
        ChunkType,
        CacheControl,
    )

    assembler = PromptChunkAssembler(model_window=128_000)

    # Add system prompt
    assembler.add_chunk(
        ChunkType.SYSTEM,
        "You are Polaris...",
        source="role_profile",
    )

    # Add continuity
    assembler.add_continuity(
        "Previous context: user was implementing feature X...",
        source_messages=10,
    )

    # Add current turn
    assembler.add_chunk(
        ChunkType.CURRENT_TURN,
        "Continue implementing feature X...",
        source="user_input",
    )

    # Assemble
    result = assembler.assemble(
        context=AssemblyContext(
            role_id="director",
            session_id="sess_123",
            model="claude-opus-4-5",
            provider="anthropic",
        )
    )

    # Get messages for LLM
    messages = result.messages

    # Get debug receipt
    print(result.receipt.to_human_readable())
"""

from .assembler import (
    AssemblyContext,
    AssemblyResult,
    PromptChunkAssembler,
)
from .budget import (
    ChunkBudget,
    ChunkBudgetTracker,
)
from .receipt import (
    ChunkTokenStats,
    CompressionDecision,
    ContextOSReceipt,
    ContinuityDecision,
    FinalRequestReceipt,
    StrategyMetadata,
)
from .taxonomy import (
    CacheControl,
    ChunkMetadata,
    ChunkType,
    PromptChunk,
)

__all__ = [
    # assembler
    "AssemblyContext",
    "AssemblyResult",
    # taxonomy
    "CacheControl",
    # budget
    "ChunkBudget",
    "ChunkBudgetTracker",
    "ChunkMetadata",
    # receipt
    "ChunkTokenStats",
    "ChunkType",
    "CompressionDecision",
    "ContextOSReceipt",
    "ContinuityDecision",
    "FinalRequestReceipt",
    "PromptChunk",
    "PromptChunkAssembler",
    "StrategyMetadata",
]
