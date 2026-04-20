"""RobustParser - Adaptive LLM Output Parsing with Entropy Reduction.

This module provides a multi-stage parsing pipeline that handles LLM output
with increasing resilience:
1. Heuristic cleaning (remove NL prefixes/suffixes)
2. Multi-pattern JSON extraction
3. Pydantic validation with detailed error analysis
4. Auto-healing retry with correction prompts
5. Progressive fallback chain with type coercion
6. SafeNull pattern to prevent cascade failures

Usage:
    parser = RobustParser[MySchema]()

    result = await parser.parse(
        llm_response,
        schema=MySchema,
        llm_corrector=lambda prompt: await llm.call(prompt)
    )

    if result.success:
        data = result.data
    elif result.safe_null:
        handle_fallback(result.raw_content)
"""

from __future__ import annotations

import logging

from .cleaners import HeuristicCleaner
from .core import ParseResult, RobustParser
from .correctors import ValidationErrorCorrector
from .extractors import JSONExtractor
from .fallbacks import FallbackChain, SafeNull
from .states import ParserState

logger = logging.getLogger(__name__)

__all__ = [
    "FallbackChain",
    "HeuristicCleaner",
    "JSONExtractor",
    "ParseResult",
    "ParserState",
    "RobustParser",
    "SafeNull",
    "ValidationErrorCorrector",
]
