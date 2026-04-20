from __future__ import annotations

import time
from typing import Any

import pytest
from polaris.cells.roles.kernel.internal.speculation.candidate_decoder import (
    CandidateDecoder,
)
from polaris.cells.roles.kernel.internal.speculation.models import CandidateToolCall
from polaris.cells.roles.kernel.internal.speculation.stability_scorer import (
    StabilityScorer,
)


def _make_candidate(
    *,
    partial_args: dict[str, Any] | None = None,
    schema_valid: bool = False,
    end_tag_seen: bool = False,
    tool_name: str | None = None,
) -> CandidateToolCall:
    return CandidateToolCall(
        candidate_id="c1",
        stream_id="s1",
        turn_id="t1",
        tool_name=tool_name,
        partial_args=partial_args or {},
        schema_valid=schema_valid,
        end_tag_seen=end_tag_seen,
        first_seen_at=time.monotonic(),
        updated_at=time.monotonic(),
    )


class TestStabilityScorerOverwriteRegression:
    """Critical field overwrite must regress parse state from SEMANTICALLY_STABLE."""

    def test_critical_overwrite_regresses_from_semantically_stable(self) -> None:
        # Use a positive quiescence window so the overwrite is considered "recent"
        scorer = StabilityScorer(quiescence_window_ms=1000.0)
        candidate = _make_candidate(
            partial_args={"path": "main.py"},
            schema_valid=True,
            end_tag_seen=True,
            tool_name="read_file",
        )
        # First evaluation: should reach semantically_stable
        state = scorer.update_parse_state(candidate)
        assert state == "semantically_stable"
        assert candidate.stability_score >= 0.82

        # Simulate critical field overwrite
        candidate.partial_args = {"path": "other.py"}
        candidate.mutation_history.append(
            type(
                "FieldMutation",
                (),
                {
                    "field_path": "path",
                    "old_value": "main.py",
                    "new_value": "other.py",
                    "ts_monotonic": time.monotonic(),
                },
            )()
        )
        candidate.last_mutation_at = time.monotonic()

        state = scorer.update_parse_state(candidate)
        assert state != "semantically_stable"
        assert candidate.parse_state == "schema_valid"


class TestStabilityScorerEndTagBoost:
    """End tag presence must boost stability score."""

    def test_end_tag_boosts_score(self) -> None:
        scorer = StabilityScorer(quiescence_window_ms=0.0)
        candidate_without = _make_candidate(
            partial_args={"path": "main.py"},
            schema_valid=True,
            end_tag_seen=False,
            tool_name="read_file",
        )
        candidate_with = _make_candidate(
            partial_args={"path": "main.py"},
            schema_valid=True,
            end_tag_seen=True,
            tool_name="read_file",
        )

        score_without = scorer.score(candidate_without)
        # Use a separate scorer for candidate_with to avoid canonical hash
        # consistency side effects from the first call
        scorer2 = StabilityScorer(quiescence_window_ms=0.0)
        score_with = scorer2.score(candidate_with)

        assert score_with > score_without
        # End tag contributes 15% weight when all other components are equal
        assert score_with - score_without == pytest.approx(0.15, rel=1e-3)


class TestStabilityScorerStableArguments:
    """Stable arguments with all positive signals must yield score \u003e= 0.82."""

    def test_stable_arguments_score_at_least_82(self) -> None:
        scorer = StabilityScorer(quiescence_window_ms=0.0)
        candidate = _make_candidate(
            partial_args={"path": "main.py", "query": "foo"},
            schema_valid=True,
            end_tag_seen=True,
            tool_name="read_file",
        )
        score = scorer.score(candidate)
        assert score >= 0.82

    def test_semantically_stable_reached_for_stable_candidate(self) -> None:
        scorer = StabilityScorer(quiescence_window_ms=0.0)
        candidate = _make_candidate(
            partial_args={"path": "main.py"},
            schema_valid=True,
            end_tag_seen=True,
            tool_name="read_file",
        )
        state = scorer.update_parse_state(candidate)
        assert state == "semantically_stable"
        assert candidate.stability_score >= 0.82


class TestCandidateDecoderIntegration:
    """CandidateDecoder incremental parsing with stability scoring."""

    def test_decoder_extracts_tool_name_and_args(self) -> None:
        decoder = CandidateDecoder(candidate_id="c1", stream_id="s1", turn_id="t1")
        decoder.feed_delta("<tool_call>\n")
        decoder.feed_delta("read_file\n")
        decoder.feed_delta('{"path": "main.py"}\n')
        decoder.feed_delta("</tool_call>")

        candidate = decoder.candidate
        assert candidate.tool_name == "read_file"
        assert candidate.partial_args == {"path": "main.py"}
        assert candidate.end_tag_seen is True

    def test_decoder_schema_validation_with_valid_schema(self) -> None:
        schema = {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        }
        decoder = CandidateDecoder(
            candidate_id="c1",
            stream_id="s1",
            turn_id="t1",
            schema=schema,
        )
        decoder.feed_delta("<tool_call>\n")
        decoder.feed_delta('{"path": "main.py"}\n')
        decoder.feed_delta("</tool_call>")

        candidate = decoder.candidate
        assert candidate.schema_valid is True
        assert candidate.parse_state == "schema_valid"

    def test_decoder_schema_validation_fails_with_invalid_schema(self) -> None:
        schema = {
            "type": "object",
            "properties": {"path": {"type": "integer"}},
            "required": ["path"],
        }
        decoder = CandidateDecoder(
            candidate_id="c1",
            stream_id="s1",
            turn_id="t1",
            schema=schema,
        )
        decoder.feed_delta("<tool_call>\n")
        decoder.feed_delta('{"path": "main.py"}\n')
        decoder.feed_delta("</tool_call>")

        candidate = decoder.candidate
        assert candidate.schema_valid is False
        assert candidate.parse_state == "syntactic_complete"

    def test_decoder_partial_parse_mid_stream(self) -> None:
        decoder = CandidateDecoder(candidate_id="c1", stream_id="s1", turn_id="t1")
        decoder.feed_delta("<tool_call>\n")
        decoder.feed_delta('{"path": "ma')
        candidate = decoder.candidate
        # Should not crash and may have partial or empty args
        assert candidate.parse_state in {"incomplete", "syntactic_complete"}

        decoder.feed_delta('in.py", "query": "foo"}\n')
        decoder.feed_delta("</tool_call>")
        candidate = decoder.candidate
        assert candidate.partial_args.get("path") == "main.py"
        assert candidate.partial_args.get("query") == "foo"

    def test_decoder_and_scorer_end_to_end_stable(self) -> None:
        decoder = CandidateDecoder(candidate_id="c1", stream_id="s1", turn_id="t1")
        scorer = StabilityScorer(quiescence_window_ms=0.0)

        decoder.feed_delta("<tool_call>\n")
        decoder.feed_delta("read_file\n")
        decoder.feed_delta('{"path": "main.py"}\n')
        decoder.feed_delta("</tool_call>")

        candidate = decoder.candidate
        state = scorer.update_parse_state(candidate)
        assert state == "semantically_stable"
        assert candidate.stability_score >= 0.82
