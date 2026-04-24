"""Tests for polaris.kernelone.llm.robust_parser.states (ParserState enum)."""

from __future__ import annotations

from polaris.kernelone.llm.robust_parser.states import ParserState


class TestParserState:
    def test_all_states_present(self) -> None:
        expected = {
            "RAW_INPUT",
            "CLEAN_PHASE",
            "EXTRACT_PHASE",
            "VALIDATE_PHASE",
            "CORRECT_PHASE",
            "FALLBACK_CHAIN",
            "SAFE_NULL",
            "EXHAUSTED",
            "EXTRACT_FAILED",
            "VALIDATE_FAILED",
        }
        actual = {s.name for s in ParserState}
        assert expected.issubset(actual), f"Missing states: {expected - actual}"

    def test_is_terminal_safe_null(self) -> None:
        assert ParserState.SAFE_NULL.is_terminal() is True

    def test_is_terminal_exhausted(self) -> None:
        assert ParserState.EXHAUSTED.is_terminal() is True

    def test_is_terminal_extract_failed(self) -> None:
        assert ParserState.EXTRACT_FAILED.is_terminal() is True

    def test_is_terminal_validate_failed(self) -> None:
        assert ParserState.VALIDATE_FAILED.is_terminal() is True

    def test_is_terminal_non_terminal(self) -> None:
        non_terminal = {
            ParserState.RAW_INPUT,
            ParserState.CLEAN_PHASE,
            ParserState.EXTRACT_PHASE,
            ParserState.VALIDATE_PHASE,
            ParserState.CORRECT_PHASE,
            ParserState.FALLBACK_CHAIN,
        }
        for state in non_terminal:
            assert state.is_terminal() is False, f"{state} should not be terminal"

    def test_is_success_validate_phase(self) -> None:
        assert ParserState.VALIDATE_PHASE.is_success() is True

    def test_is_success_fallback_chain(self) -> None:
        assert ParserState.FALLBACK_CHAIN.is_success() is True

    def test_is_success_non_success(self) -> None:
        non_success = {
            ParserState.RAW_INPUT,
            ParserState.CLEAN_PHASE,
            ParserState.EXTRACT_PHASE,
            ParserState.CORRECT_PHASE,
            ParserState.SAFE_NULL,
            ParserState.EXHAUSTED,
            ParserState.EXTRACT_FAILED,
            ParserState.VALIDATE_FAILED,
        }
        for state in non_success:
            assert state.is_success() is False, f"{state} should not be success"

    def test_str_representation(self) -> None:
        assert str(ParserState.RAW_INPUT) == "RAW_INPUT"
        assert str(ParserState.SAFE_NULL) == "SAFE_NULL"

    def test_unique_auto_values(self) -> None:
        values = {s.value for s in ParserState}
        assert len(values) == len(ParserState), "Duplicate auto values"
