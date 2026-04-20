"""RobustParser - Adaptive LLM Output Parsing with Entropy Reduction Matrix.

This module provides the main RobustParser class that orchestrates the
multi-stage parsing pipeline: cleaning → extraction → validation → correction → fallback.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from polaris.kernelone.constants import DEFAULT_SHORT_TIMEOUT_SECONDS
from polaris.kernelone.llm.robust_parser.cleaners import CleaningResult, HeuristicCleaner
from polaris.kernelone.llm.robust_parser.correctors import ValidationErrorCorrector
from polaris.kernelone.llm.robust_parser.extractors import ExtractionResult, JSONExtractor
from polaris.kernelone.llm.robust_parser.fallbacks import FallbackChain, SafeNull
from polaris.kernelone.llm.robust_parser.states import ParserState
from pydantic import BaseModel, ValidationError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class ParseResult(Generic[T]):
    """Result of RobustParser.parse().

    Attributes:
        success: Whether parsing succeeded with valid data.
        data: Parsed data instance (None if failed).
        raw_content: Original content that was parsed.
        state: Final parser state.
        correction_attempts: Number of correction retries attempted.
        error: Error message if parsing failed.
        safe_null: True if result is SafeNull (caller should handle gracefully).
    """

    success: bool
    data: T | None
    raw_content: str
    state: ParserState
    correction_attempts: int = 0
    error: str | None = None
    safe_null: bool = False
    cleaning_metadata: dict[str, Any] = field(default_factory=dict)
    extraction_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_safe_fallback(self) -> bool:
        """True if result is a safe fallback (SafeNull or exhausted)."""
        return self.safe_null or self.state in {
            ParserState.EXHAUSTED,
            ParserState.SAFE_NULL,
        }

    def to_dict(self) -> dict[str, Any]:
        """Serialize for logging/debugging."""
        return {
            "success": self.success,
            "data": self.data.model_dump() if self.data else None,
            "raw_content": self.raw_content[:500] if self.raw_content else "",
            "state": str(self.state),
            "correction_attempts": self.correction_attempts,
            "error": self.error,
            "safe_null": self.safe_null,
            "cleaning_metadata": self.cleaning_metadata,
            "extraction_metadata": self.extraction_metadata,
        }


class RobustParser(Generic[T]):
    """Adaptive parser with multi-stage fallback chain.

    This parser implements the Entropy Reduction Matrix for handling LLM output:
    1. Heuristic cleaning (remove NL prefixes/suffixes)
    2. Multi-pattern JSON extraction
    3. Pydantic validation
    4. Auto-healing retry with detailed error feedback (optional)
    5. Progressive fallback chain with type coercion
    6. SafeNull pattern to prevent cascade failures

    Example:
        parser = RobustParser[MySchema]()

        result = await parser.parse(
            llm_response,
            schema=MySchema,
            llm_corrector=lambda prompt: await llm.call(prompt)
        )

        if result.success:
            data = result.data
        elif result.safe_null:
            handle_fallback(result.raw_content, result.error)
    """

    def __init__(
        self,
        *,
        max_correction_turns: int = 3,
        max_fallback_attempts: int = 3,
        enable_cleaning: bool = True,
        enable_correction: bool = True,
        enable_fallback: bool = True,
        enable_safe_null: bool = True,
    ) -> None:
        """Initialize RobustParser.

        Args:
            max_correction_turns: Max auto-healing retry attempts.
            max_fallback_attempts: Max fallback chain attempts per correction turn.
            enable_cleaning: Run heuristic pre-processing.
            enable_correction: Enable auto-healing retry with LLM corrector.
            enable_fallback: Enable type coercion fallback.
            enable_safe_null: Return SafeNull on complete failure.
        """
        self._max_correction_turns = max_correction_turns
        self._max_fallback_attempts = max_fallback_attempts
        self._enable_cleaning = enable_cleaning
        self._enable_correction = enable_correction
        self._enable_fallback = enable_fallback
        self._enable_safe_null = enable_safe_null

        self._cleaner = HeuristicCleaner()
        self._extractor = JSONExtractor()
        self._corrector = ValidationErrorCorrector()
        self._fallback_chain = FallbackChain(max_attempts=max_fallback_attempts)

    async def parse(
        self,
        response: str,
        *,
        schema: type[T],
        llm_corrector: Callable[[str], Awaitable[str]] | None = None,
    ) -> ParseResult[T]:
        """Parse LLM response with auto-healing retry.

        Args:
            response: Raw LLM output string.
            schema: Target Pydantic model type.
            llm_corrector: Optional async callable to re-call LLM with correction prompt.
                          If None, correction phase is skipped.

        Returns:
            ParseResult with parsed data or safe fallback.

        Raises:
            asyncio.CancellationError: If the parse operation is cancelled.
        """
        if not response or not response.strip():
            logger.info("[RobustParser] RAW_INPUT: empty response")
            return ParseResult(
                success=False,
                data=None,
                raw_content=response or "",
                state=ParserState.RAW_INPUT,
                error="Empty response",
                safe_null=self._enable_safe_null,
            )

        current_content = response
        correction_attempts = 0
        last_error: ValidationError | None = None
        last_extraction_error: str | None = None

        # Phase 1: Cleaning
        if self._enable_cleaning:
            clean_result = self._clean(current_content)
            current_content = clean_result.cleaned
            cleaning_meta = {"applied_rules": clean_result.applied_rules}

            if not current_content:
                logger.info(
                    "[RobustParser] CLEAN_PHASE: cleaning produced empty content (rules=%s)",
                    clean_result.applied_rules,
                )
                return ParseResult(
                    success=False,
                    data=None,
                    raw_content=response,
                    state=ParserState.CLEAN_PHASE,
                    error="Cleaning produced empty content",
                    safe_null=self._enable_safe_null,
                    cleaning_metadata=cleaning_meta,
                )
        else:
            cleaning_meta = {}

        # Phase 2: Extraction
        extract_result = self._extract(current_content)
        extraction_meta: dict[str, Any] = {
            "format_found": extract_result.format_found,
            "error": extract_result.error,
        }

        if extract_result.data is None:
            last_extraction_error = extract_result.error or "Extraction failed"
            logger.info(
                "[RobustParser] EXTRACT_FAILED: format=%s, error=%s",
                extract_result.format_found,
                extract_result.error,
            )
            return ParseResult(
                success=False,
                data=None,
                raw_content=response,
                state=ParserState.EXTRACT_FAILED,
                error=last_extraction_error,
                safe_null=self._enable_safe_null,
                cleaning_metadata=cleaning_meta,
                extraction_metadata=extraction_meta,
            )

        # Phase 3: Validation with possible correction
        while correction_attempts < self._max_correction_turns:
            validated, data, last_error = await self._validate_with_fallback(
                extract_result.data,
                schema,
            )

            if validated and data is not None:
                logger.info(
                    "[RobustParser] VALIDATE_PHASE: schema=%s, attempts=%d",
                    schema.__name__,
                    correction_attempts,
                )
                return ParseResult(
                    success=True,
                    data=data,
                    raw_content=response,
                    state=ParserState.VALIDATE_PHASE,
                    correction_attempts=correction_attempts,
                    cleaning_metadata=cleaning_meta,
                    extraction_metadata=extraction_meta,
                )

            # Validation failed - check if we should try correction
            if (
                self._enable_correction
                and llm_corrector is not None
                and last_error is not None
                and correction_attempts < self._max_correction_turns
            ):
                logger.debug(
                    "[RobustParser] VALIDATE_FAILED: attempt=%d, error=%s",
                    correction_attempts,
                    last_error_str(last_error),
                )
                # Build correction prompt and call LLM
                correction_prompt = self._corrector.build_correction_prompt(last_error, schema)
                correction_attempts += 1

                try:
                    corrected_response = await asyncio.wait_for(
                        llm_corrector(correction_prompt.to_message()),
                        timeout=DEFAULT_SHORT_TIMEOUT_SECONDS,
                    )

                    # Re-clean and re-extract from corrected response
                    if self._enable_cleaning:
                        clean_result = self._clean(corrected_response)
                        corrected_response = clean_result.cleaned
                        cleaning_meta = {"applied_rules": clean_result.applied_rules}

                    extract_result = self._extract(corrected_response)
                    last_extraction_error = extract_result.error
                    extraction_meta = {
                        "format_found": extract_result.format_found,
                        "error": extract_result.error,
                        "correction_turn": correction_attempts,
                    }

                    if extract_result.data is None:
                        logger.debug(
                            "[RobustParser] CORRECT_PHASE: extraction failed on corrected response, attempt=%d",
                            correction_attempts,
                        )
                        # Extraction failed - clear last_error so Phase 5 uses extraction error
                        last_error = None
                        break

                except asyncio.CancelledError:
                    # Propagate cancellation to caller
                    raise
                except asyncio.TimeoutError:
                    logger.warning("LLM correction timed out after 30s")
                    break
                except (RuntimeError, ValueError) as e:
                    logger.warning("LLM correction failed: %s", e)
                    break
            else:
                # No more correction attempts or disabled
                if last_error is not None:
                    logger.debug(
                        "[RobustParser] VALIDATE_FAILED: no correction, error=%s",
                        last_error_str(last_error),
                    )
                break

        # Phase 4: Final fallback or SafeNull
        if self._enable_fallback and isinstance(extract_result.data, dict):
            fallback_result = await self._try_fallback(extract_result.data, schema)
            if fallback_result is not None:
                if isinstance(fallback_result, SafeNull):
                    logger.info(
                        "[RobustParser] SAFE_NULL: error=%s, partial_data_keys=%s",
                        fallback_result.parse_error,
                        list(fallback_result.partial_data.keys()),
                    )
                    return ParseResult(
                        success=False,
                        data=None,
                        raw_content=response,
                        state=ParserState.SAFE_NULL,
                        correction_attempts=correction_attempts,
                        error=fallback_result.parse_error,
                        safe_null=True,
                        cleaning_metadata=cleaning_meta,
                        extraction_metadata=extraction_meta,
                    )
                else:
                    logger.info(
                        "[RobustParser] FALLBACK_CHAIN: schema=%s, attempts=%d",
                        schema.__name__,
                        correction_attempts,
                    )
                    return ParseResult(
                        success=True,
                        data=fallback_result,
                        raw_content=response,
                        state=ParserState.FALLBACK_CHAIN,
                        correction_attempts=correction_attempts,
                        cleaning_metadata=cleaning_meta,
                        extraction_metadata=extraction_meta,
                    )

        # Phase 5: SafeNull
        if self._enable_safe_null:
            # Determine the best error message
            if last_error:
                final_error = last_error_str(last_error)
            elif last_extraction_error:
                final_error = last_extraction_error
            elif extract_result.data is not None and not isinstance(extract_result.data, dict):
                final_error = f"Expected object, got {type(extract_result.data).__name__}"
            else:
                final_error = "Max corrections reached"
            logger.info(
                "[RobustParser] EXHAUSTED: error=%s, attempts=%d",
                final_error,
                correction_attempts,
            )
            return ParseResult(
                success=False,
                data=None,
                raw_content=response,
                state=ParserState.EXHAUSTED,
                correction_attempts=correction_attempts,
                error=final_error,
                safe_null=True,
                cleaning_metadata=cleaning_meta,
                extraction_metadata=extraction_meta,
            )

        # No SafeNull enabled - return error result
        final_error = last_error_str(last_error) if last_error else last_extraction_error or "Parsing failed"
        logger.info(
            "[RobustParser] EXHAUSTED (no SafeNull): error=%s, attempts=%d",
            final_error,
            correction_attempts,
        )
        return ParseResult(
            success=False,
            data=None,
            raw_content=response,
            state=ParserState.EXHAUSTED,
            correction_attempts=correction_attempts,
            error=final_error,
            safe_null=False,
            cleaning_metadata=cleaning_meta,
            extraction_metadata=extraction_meta,
        )

    def _clean(self, text: str) -> CleaningResult:
        """Apply heuristic cleaning."""
        return self._cleaner.clean(text)

    def _extract(self, text: str) -> ExtractionResult:
        """Extract JSON from text."""
        return self._extractor.extract(text)

    async def _validate_with_fallback(
        self,
        data: dict[str, Any] | list[Any],
        schema: type[T],
    ) -> tuple[bool, T | None, ValidationError | None]:
        """Try validation with fallback.

        Returns:
            Tuple of (success, data_or_none, error_or_none)
        """
        if not isinstance(data, dict):
            return (False, None, None)
        try:
            instance = schema.model_validate(data)
            return (True, instance, None)
        except ValidationError as ve:
            return (False, None, ve)
        except (RuntimeError, ValueError):
            # Non-validation error (shouldn't happen with Pydantic)
            return (False, None, None)

    async def _try_fallback(
        self,
        data: dict[str, Any],
        schema: type[T],
    ) -> T | SafeNull[T] | None:
        """Try fallback chain."""
        return self._fallback_chain.try_parse(
            data,
            schema,
            attempt_number=1,  # First fallback attempt
        )


def last_error_str(error: ValidationError | None) -> str:
    """Format ValidationError as string for logging."""
    if error is None:
        return "Unknown error"
    errors = error.errors()
    if not errors:
        return str(error)
    first = errors[0]
    loc = ".".join(str(loc_part) for loc_part in first["loc"])
    return f"{first['msg']} at '{loc}'"
