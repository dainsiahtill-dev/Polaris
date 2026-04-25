"""Unit tests for QAAdapter pure logic (no I/O, no LLM, no filesystem).

Covers:
- _coerce_task_record / _safe_int / _resolve_rework_retry_budget
- _build_qa_message
- _parse_review_result / _merge_review_result / _finalize_review_result
- _extract_json_payload / _normalize_review_payload / _strip_json_line_comments
- _extract_domain_tokens
- _coerce_int / _coerce_list / _dedupe_list
- _check_semantic_equivalence / _detect_regressions
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from polaris.cells.roles.adapters.internal.qa_adapter import QAAdapter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter(tmp_path: Any) -> QAAdapter:
    return QAAdapter(workspace=str(tmp_path))


# ---------------------------------------------------------------------------
# Coercion helpers
# ---------------------------------------------------------------------------


class TestCoerceTaskRecord:
    def test_dict_passthrough(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._coerce_task_record({"id": 1}) == {"id": 1}

    def test_object_with_to_dict(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)

        class Obj:
            def to_dict(self):
                return {"id": 2}

        assert adapter._coerce_task_record(Obj()) == {"id": 2}

    def test_object_with_attributes(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)

        class Obj:
            id = 3
            status = "pending"
            unknown = "x"

        result = adapter._coerce_task_record(Obj())
        assert result["id"] == 3
        assert result["status"] == "pending"
        assert "unknown" not in result

    def test_to_dict_exception_fallback(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)

        class Obj:
            def to_dict(self):
                raise RuntimeError("fail")

        assert adapter._coerce_task_record(Obj()) == {}


class TestSafeInt:
    def test_numeric(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._safe_int(5) == 5
        assert adapter._safe_int("7") == 7

    def test_invalid_returns_default(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._safe_int("abc") == 0
        assert adapter._safe_int("abc", default=3) == 3


class TestResolveReworkRetryBudget:
    def test_default(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._resolve_rework_retry_budget() == 3

    def test_env_override(self, tmp_path: Any, monkeypatch: Any) -> None:
        monkeypatch.setenv("KERNELONE_DIRECTOR_TASK_REWORK_MAX_RETRIES", "5")
        assert QAAdapter._resolve_rework_retry_budget() == 5

    def test_env_clamped(self, tmp_path: Any, monkeypatch: Any) -> None:
        monkeypatch.setenv("KERNELONE_DIRECTOR_TASK_REWORK_MAX_RETRIES", "99")
        assert QAAdapter._resolve_rework_retry_budget() == 20
        monkeypatch.setenv("KERNELONE_DIRECTOR_TASK_REWORK_MAX_RETRIES", "0")
        assert QAAdapter._resolve_rework_retry_budget() == 1


# ---------------------------------------------------------------------------
# Message building
# ---------------------------------------------------------------------------


class TestBuildQaMessage:
    def test_includes_review_type_and_target(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = adapter._build_qa_message("quality_gate", "Project X")
        assert "quality_gate" in msg
        assert "Project X" in msg
        assert "JSON" in msg

    def test_includes_evidence(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        review = {"evidence": ["code_file_count=5"]}
        msg = adapter._build_qa_message("quality_gate", "Project X", review_result=review)
        assert "code_file_count=5" in msg

    def test_no_evidence_fallback(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = adapter._build_qa_message("quality_gate", "Project X", review_result={})
        assert "no deterministic evidence" in msg


# ---------------------------------------------------------------------------
# Review result parsing
# ---------------------------------------------------------------------------


class TestParseReviewResult:
    def test_json_payload(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._parse_review_result('{"verdict": "PASS", "score": 90}')
        assert result["verdict"] == "PASS"
        assert result["score"] == 90

    def test_fallback_regex(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._parse_review_result('Some text "verdict": "FAIL" "score": 42')
        assert result["verdict"] == "FAIL"
        assert result["score"] == 42
        assert "qa_llm_partial_parse_recovered" in result["warnings"]

    def test_unparseable(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._parse_review_result("random text")
        assert result["parsed_json"] is False


# ---------------------------------------------------------------------------
# Review result merge
# ---------------------------------------------------------------------------


class TestMergeReviewResult:
    def test_llm_passed_inherits(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        base = {"verdict": "PASS", "score": 100, "critical_issues": [], "major_issues": [], "warnings": [], "evidence": [], "suggestions": []}
        llm = {"parsed_json": True, "verdict": "FAIL", "score": 50, "critical_issues": ["bug"], "warnings": ["slow"]}
        merged = adapter._merge_review_result(base, llm)
        assert merged["verdict"] == "FAIL"
        assert merged["score"] == 50
        assert "bug" in merged["critical_issues"]
        assert "slow" in merged["warnings"]

    def test_llm_unparsed_adds_warning(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        base = {"verdict": "PASS", "score": 100, "critical_issues": [], "major_issues": [], "warnings": [], "evidence": [], "suggestions": []}
        llm = {"parsed_json": False, "raw_excerpt": "bad json"}
        merged = adapter._merge_review_result(base, llm)
        assert "qa_llm_judgement_unavailable" in merged["warnings"]
        assert "llm_excerpt=bad json" in merged["evidence"]

    def test_dedupe(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        base = {"verdict": "PASS", "score": 100, "critical_issues": ["a"], "major_issues": [], "warnings": [], "evidence": [], "suggestions": []}
        llm = {"parsed_json": True, "verdict": "PASS", "critical_issues": ["a", "b"]}
        merged = adapter._merge_review_result(base, llm)
        assert merged["critical_issues"] == ["a", "b"]


# ---------------------------------------------------------------------------
# Finalize review result
# ---------------------------------------------------------------------------


class TestFinalizeReviewResult:
    def test_pass_when_no_issues(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        review = {"verdict": "PASS", "score": 100, "critical_issues": [], "major_issues": [], "warnings": [], "evidence": [], "suggestions": []}
        result = adapter._finalize_review_result(review)
        assert result["passed"] is True
        assert result["score"] == 100

    def test_fail_on_critical(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        review = {"verdict": "PASS", "score": 100, "critical_issues": ["bug"], "major_issues": [], "warnings": [], "evidence": [], "suggestions": []}
        result = adapter._finalize_review_result(review)
        assert result["passed"] is False
        assert result["score"] == 70

    def test_fail_on_verdict_fail(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        review = {"verdict": "FAIL", "score": 100, "critical_issues": [], "major_issues": [], "warnings": [], "evidence": [], "suggestions": []}
        result = adapter._finalize_review_result(review)
        assert result["passed"] is False

    def test_score_computed_correctly(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        review = {"verdict": "PASS", "score": 100, "critical_issues": ["a", "b"], "major_issues": ["c"], "warnings": ["d"], "evidence": [], "suggestions": []}
        result = adapter._finalize_review_result(review)
        # 100 - 2*30 - 1*10 - 1*4 = 26
        assert result["score"] == 26


# ---------------------------------------------------------------------------
# JSON payload extraction
# ---------------------------------------------------------------------------


class TestExtractJsonPayload:
    def test_plain_json(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._extract_json_payload('{"verdict": "PASS"}')
        assert result == {"verdict": "PASS"}

    def test_fenced_json(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._extract_json_payload('```json\n{"verdict": "PASS"}\n```')
        assert result == {"verdict": "PASS"}

    def test_with_comments(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._extract_json_payload('{"verdict": "PASS" // comment\n}')
        assert result == {"verdict": "PASS"}

    def test_empty_returns_none(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._extract_json_payload("") is None


# ---------------------------------------------------------------------------
# Strip JSON comments
# ---------------------------------------------------------------------------


class TestStripJsonLineComments:
    def test_removes_comments(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        text = '{"a": 1 // comment\n}'
        assert adapter._strip_json_line_comments(text) == '{"a": 1 \n}'

    def test_preserves_url_in_string(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        text = '{"url": "http://example.com"}'
        assert adapter._strip_json_line_comments(text) == '{"url": "http://example.com"}'

    def test_no_comment_unchanged(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        text = '{"a": 1}'
        assert adapter._strip_json_line_comments(text) == '{"a": 1}'


# ---------------------------------------------------------------------------
# Normalize review payload
# ---------------------------------------------------------------------------


class TestNormalizeReviewPayload:
    def test_basic(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        payload = {"verdict": "PASS", "score": 90, "critical_issues": ["a"], "findings": [{"severity": "high", "description": "bug"}]}
        result = adapter._normalize_review_payload(payload)
        assert result["verdict"] == "PASS"
        assert "bug" in result["major_issues"]

    def test_findings_critical(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        payload = {"findings": [{"severity": "critical", "description": "crash"}]}
        result = adapter._normalize_review_payload(payload)
        assert "crash" in result["critical_issues"]

    def test_summary_in_evidence(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        payload = {"summary": "overall good"}
        result = adapter._normalize_review_payload(payload)
        assert "llm_summary=overall good" in result["evidence"]


# ---------------------------------------------------------------------------
# Domain tokens
# ---------------------------------------------------------------------------


class TestExtractDomainTokens:
    def test_filters_stopwords(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._extract_domain_tokens("project quality module")
        assert "project" not in result
        assert "quality" not in result

    def test_extracts_unique(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._extract_domain_tokens("payment gateway payment")
        assert result == ["payment", "gateway"]


# ---------------------------------------------------------------------------
# Coerce / dedupe helpers
# ---------------------------------------------------------------------------


class TestCoerceInt:
    def test_numeric(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._coerce_int(5) == 5
        assert adapter._coerce_int("7") == 7

    def test_invalid_returns_zero(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._coerce_int("abc") == 0
        assert adapter._coerce_int(None) == 0


class TestCoerceList:
    def test_list_passthrough(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._coerce_list(["a", "b"]) == ["a", "b"]

    def test_string_wrap(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._coerce_list("hello") == ["hello"]

    def test_empty_returns_empty(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._coerce_list(None) == []
        assert adapter._coerce_list("") == []


class TestDedupeList:
    def test_removes_duplicates(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._dedupe_list(["a", "b", "a"]) == ["a", "b"]

    def test_non_list_returns_empty(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._dedupe_list("not a list") == []

    def test_strips_and_skips_empty(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._dedupe_list([" a ", "", "a"]) == ["a"]


# ---------------------------------------------------------------------------
# Semantic equivalence
# ---------------------------------------------------------------------------


class TestCheckSemanticEquivalence:
    def test_empty_returns_false(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._check_semantic_equivalence("", "spec")
        assert result["equivalent"] is False
        assert "missing_code_or_spec" in result["issues"]

    def test_sufficient_keyword_coverage(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        code = "def process_payment(amount): return amount * 2"
        spec = "The payment processing function should take an amount and return double"
        result = adapter._check_semantic_equivalence(code, spec)
        assert result["semantic_equivalence_checked"] is True
        assert result["confidence"] > 0

    def test_missing_return_detected(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        code = "def foo(): pass"
        spec = "Return the computed value"
        result = adapter._check_semantic_equivalence(code, spec)
        assert "missing_return_statement" in result["mismatch_indicators"]


# ---------------------------------------------------------------------------
# Regression detection
# ---------------------------------------------------------------------------


class TestDetectRegressions:
    def test_no_baseline(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._detect_regressions("code", baseline_snapshot=None, context=None)
        assert result["regressions_found"] == 0
        assert "no_baseline_available" in result["warnings"]

    def test_significant_reduction(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        baseline = {"code": "line\n" * 100}
        current = "line\n" * 30
        result = adapter._detect_regressions(current, baseline_snapshot=baseline)
        assert result["regressions_found"] == 1
        assert "significant_code_reduction" in result["regressions"][0]

    def test_api_reduction(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        baseline = {"code": "def a(): pass\ndef b(): pass\n"}
        current = "def a(): pass\n"
        result = adapter._detect_regressions(current, baseline_snapshot=baseline)
        assert "api_reduction" in result["regressions"][0]

    def test_stable(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        baseline = {"code": "def a(): pass\n"}
        current = "def a(): pass\n"
        result = adapter._detect_regressions(current, baseline_snapshot=baseline)
        assert result["status"] == "stable"

    def test_improvement(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        baseline = {"code": "def a(): pass\n"}
        current = "def a(): pass\ndef b(): pass\n"
        result = adapter._detect_regressions(current, baseline_snapshot=baseline)
        assert result["status"] == "improved"


# ---------------------------------------------------------------------------
# Adapter identity
# ---------------------------------------------------------------------------


class TestQaAdapterIdentity:
    def test_role_id(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter.role_id == "qa"

    def test_capabilities(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        caps = adapter.get_capabilities()
        assert "code_review" in caps
        assert "semantic_equivalence_checking" in caps
