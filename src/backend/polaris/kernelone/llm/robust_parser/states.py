"""Parser states for the RobustParser state machine."""

from __future__ import annotations

from enum import Enum, auto


class ParserState(Enum):
    """States in the RobustParser entropy reduction pipeline.

    State transitions:
        RAW_INPUT -> CLEAN -> EXTRACT -> VALIDATE
                                            |
                                            v
                                        CORRECT (if validation fails, has corrections left)
                                            |
                        v-------------------v-------------------v
                   FALLBACK             SAFE_NULL            EXHAUSTED
                   (try coercion)       (return null)        (max retries)

        Any state can transition to EXTRACT_FAILED if cleaning/extraction fails
    """

    RAW_INPUT = auto()
    """Initial state - raw string from LLM."""

    CLEAN_PHASE = auto()
    """Heuristic pre-processing to remove NL prefixes/suffixes."""

    EXTRACT_PHASE = auto()
    """Multi-pattern JSON extraction from cleaned text."""

    VALIDATE_PHASE = auto()
    """Pydantic schema validation of extracted data."""

    CORRECT_PHASE = auto()
    """Auto-healing retry with detailed error feedback to LLM."""

    FALLBACK_CHAIN = auto()
    """Progressive type coercion fallback."""

    SAFE_NULL = auto()
    """Terminal state - return SafeNull to prevent cascade."""

    EXHAUSTED = auto()
    """Terminal state - max retries reached, return best effort."""

    EXTRACT_FAILED = auto()
    """Terminal state - could not extract valid JSON."""

    VALIDATE_FAILED = auto()
    """Terminal state - validation failed even with fallback."""

    def is_terminal(self) -> bool:
        """Check if this is a terminal state."""
        return self in {
            ParserState.SAFE_NULL,
            ParserState.EXHAUSTED,
            ParserState.EXTRACT_FAILED,
            ParserState.VALIDATE_FAILED,
        }

    def is_success(self) -> bool:
        """Check if this represents a successful parse."""
        return self in {ParserState.VALIDATE_PHASE, ParserState.FALLBACK_CHAIN}

    def __str__(self) -> str:
        return self.name
