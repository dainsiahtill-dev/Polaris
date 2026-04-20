"""Minimum test suite for `audit.evidence` public contracts.

Tests cover:
- AppendEvidenceEventCommandV1: construction
- QueryEvidenceEventsV1: default limit
- VerifyEvidenceChainV1: optional start_at
- EvidenceQueryResultV1 / EvidenceVerificationResultV1: structure
- EvidenceAppendedEventV1: non-empty guards
- EvidenceAuditError: exception
- EvidenceService: detect_language, build_file_evidence, build_error_evidence
- detect_language: language-from-extension mapping
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest
from polaris.cells.audit.evidence.public.contracts import (
    AppendEvidenceEventCommandV1,
    EvidenceAppendedEventV1,
    EvidenceAuditError,
    EvidenceQueryResultV1,
    EvidenceVerificationResultV1,
    QueryEvidenceEventsV1,
    VerifyEvidenceChainV1,
)
from polaris.cells.audit.evidence.task_service import (
    EvidenceService,
    build_error_evidence,
    build_file_evidence,
    detect_language,
)

# ---------------------------------------------------------------------------
# Happy path: command / query construction
# ---------------------------------------------------------------------------


class TestAppendEvidenceEventCommandV1:
    """Command carries kind and payload."""

    def test_basic_construction(self) -> None:
        cmd = AppendEvidenceEventCommandV1(kind="task_completed", payload={"task_id": "t-1"})
        assert cmd.kind == "task_completed"
        assert cmd.payload == {"task_id": "t-1"}

    def test_empty_payload_accepted(self) -> None:
        cmd = AppendEvidenceEventCommandV1(kind="error", payload={})
        assert cmd.payload == {}


class TestQueryEvidenceEventsV1:
    """Default limit is 50."""

    def test_default_limit(self) -> None:
        q = QueryEvidenceEventsV1()
        assert q.limit == 50

    def test_custom_limit(self) -> None:
        q = QueryEvidenceEventsV1(limit=100)
        assert q.limit == 100


class TestVerifyEvidenceChainV1:
    """start_at is optional."""

    def test_default_start_at_none(self) -> None:
        q = VerifyEvidenceChainV1()
        assert q.start_at is None

    def test_explicit_start_at(self) -> None:
        q = VerifyEvidenceChainV1(start_at="2026-01-01T00:00:00Z")
        assert q.start_at == "2026-01-01T00:00:00Z"


# ---------------------------------------------------------------------------
# Happy path: result structures
# ---------------------------------------------------------------------------


class TestEvidenceQueryResultV1:
    """Result carries events and total count."""

    def test_basic_construction(self) -> None:
        result = EvidenceQueryResultV1(events=({"event_id": "e-1"}, {"event_id": "e-2"}), total=2)
        assert len(result.events) == 2
        assert result.total == 2

    def test_events_are_tuple(self) -> None:
        result = EvidenceQueryResultV1(events=({"id": "e-3"},), total=1)
        assert isinstance(result.events, tuple)
        assert result.events[0]["id"] == "e-3"


class TestEvidenceVerificationResultV1:
    """Result carries ok flag and checked count."""

    def test_verification_ok(self) -> None:
        result = EvidenceVerificationResultV1(ok=True, checked_events=10)
        assert result.ok is True
        assert result.checked_events == 10

    def test_verification_failed(self) -> None:
        result = EvidenceVerificationResultV1(ok=False, checked_events=3)
        assert result.ok is False


# ---------------------------------------------------------------------------
# Edge cases: EvidenceAppendedEventV1 — frozen dataclass, no post_init guard
# (kind and receipt_path are required but dataclass allows empty strings;
#  callers are responsible for validation.)
# ---------------------------------------------------------------------------


class TestEvidenceAppendedEventV1EdgeCases:
    """EvidenceAppendedEventV1 is a frozen dataclass; field validation is caller responsibility."""

    def test_frozen_prevents_modification(self) -> None:
        ev = EvidenceAppendedEventV1(kind="task_completed", receipt_path="/ws/evidence/e1.jsonl")
        with pytest.raises(dataclasses.FrozenInstanceError):
            ev.kind = "modified"  # type: ignore[misc]

    def test_constructs_with_any_string_values(self) -> None:
        # Empty strings are accepted at dataclass level; callers must guard.
        ev = EvidenceAppendedEventV1(kind="", receipt_path="")
        assert ev.kind == ""
        assert ev.receipt_path == ""


# ---------------------------------------------------------------------------
# EvidenceAuditError
# ---------------------------------------------------------------------------


class TestEvidenceAuditError:
    """Structured exception from evidence boundary."""

    def test_default_construction(self) -> None:
        err = EvidenceAuditError("append failed")
        assert str(err) == "append failed"

    def test_raised_and_caught(self) -> None:
        err = EvidenceAuditError("chain broken")
        with pytest.raises(EvidenceAuditError) as exc_info:
            raise err
        assert exc_info.value.args[0] == "chain broken"


# ---------------------------------------------------------------------------
# Happy path: EvidenceService static helpers
# ---------------------------------------------------------------------------


class TestDetectLanguage:
    """Language detection from file path extension."""

    def test_python_py_file_detected(self) -> None:
        assert detect_language("src/main.py") == "python"

    def test_typescript_tsx_detected(self) -> None:
        assert detect_language("app/main.tsx") == "typescript"

    def test_rust_detected(self) -> None:
        assert detect_language("src/lib.rs") == "rust"

    def test_go_detected(self) -> None:
        assert detect_language("cmd/server.go") == "go"

    def test_yaml_detected(self) -> None:
        assert detect_language(".github/workflows/ci.yaml") == "yaml"

    def test_json_detected(self) -> None:
        assert detect_language("package.json") == "json"

    def test_empty_path_returns_unknown(self) -> None:
        lang = detect_language("")
        assert lang in ("unknown", "")


class TestBuildFileEvidence:
    """File evidence construction from list of file dicts."""

    def test_single_file_creates_one_evidence(self) -> None:
        files = [{"path": "src/main.py", "content": "print('hello')"}]
        evidence = build_file_evidence(files)
        assert len(evidence) == 1
        assert evidence[0].type == "file"

    def test_path_extracted(self) -> None:
        files = [{"path": "src/main.py", "content": "code"}]
        evidence = build_file_evidence(files)
        assert evidence[0].path == "src/main.py"

    def test_language_from_path(self) -> None:
        files = [{"path": "src/main.py", "content": "pass"}]
        evidence = build_file_evidence(files)
        assert evidence[0].metadata["language"] == "python"

    def test_skips_non_dict_entry(self) -> None:
        files: list[dict[str, Any]] = [{"path": "a.py", "content": "a"}, None, "not a dict"]  # type: ignore[list-item]
        evidence = build_file_evidence(files)
        assert len(evidence) == 1

    def test_missing_content_field(self) -> None:
        files = [{"path": "b.py"}]
        evidence = build_file_evidence(files)
        assert len(evidence) == 1
        assert evidence[0].content == ""

    def test_content_truncated_at_1000_chars(self) -> None:
        files = [{"path": "big.py", "content": "x" * 2000}]
        evidence = build_file_evidence(files)
        assert len(evidence[0].content or "") == 1000

    def test_multiple_files(self) -> None:
        files = [
            {"path": "a.py", "content": "a"},
            {"path": "b.ts", "content": "b"},
        ]
        evidence = build_file_evidence(files)
        assert len(evidence) == 2


class TestBuildErrorEvidence:
    """Error evidence construction."""

    def test_single_evidence_created(self) -> None:
        evidence = build_error_evidence("FileNotFoundError: /tmp/x", duration_ms=150)
        assert len(evidence) == 1
        assert evidence[0].type == "error"

    def test_duration_in_metadata(self) -> None:
        evidence = build_error_evidence("boom", duration_ms=42)
        assert evidence[0].metadata["duration_ms"] == 42

    def test_content_is_error_text(self) -> None:
        msg = "TimeoutError: LLM call timed out"
        evidence = build_error_evidence(msg, duration_ms=5000)
        assert evidence[0].content == msg

    def test_empty_error_text(self) -> None:
        evidence = build_error_evidence("", duration_ms=0)
        assert evidence[0].content == ""


# ---------------------------------------------------------------------------
# Edge cases: service-level wrappers delegate correctly
# ---------------------------------------------------------------------------


class TestEvidenceServiceDelegation:
    """EvidenceService delegates to module-level helpers."""

    def test_detect_language_delegates(self) -> None:
        lang = EvidenceService.detect_language("main.rs")
        assert lang == "rust"

    def test_build_file_evidence_delegates(self) -> None:
        evidence = EvidenceService.build_file_evidence([{"path": "x.py", "content": "y"}])
        assert len(evidence) == 1

    def test_build_error_evidence_delegates(self) -> None:
        evidence = EvidenceService.build_error_evidence("fail", duration_ms=100)
        assert len(evidence) == 1
        assert evidence[0].type == "error"
