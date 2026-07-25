"""Unit tests for Factory failure code classification (provider quota fail-closed)."""

from __future__ import annotations

from polaris.delivery.http.routers.factory import (
    _classify_factory_failure_code,
    _factory_failure_suggestion,
)


def test_classify_provider_quota_403_as_provider_block() -> None:
    code = _classify_factory_failure_code(
        stage="chief_engineer_review",
        detail=(
            "403, message='Forbidden', url='https://api.kimi.com/coding/v1/messages' "
            "You've reached your usage limit for this billing cycle."
        ),
    )
    assert code == "PROVIDER_QUOTA_OR_AUTH_BLOCKED"
    suggestion = _factory_failure_suggestion(code)
    assert "quota" in suggestion.lower() or "provider" in suggestion.lower()


def test_classify_generic_stage_failure() -> None:
    code = _classify_factory_failure_code(
        stage="director_dispatch",
        detail="Stage director_dispatch failed: blueprint missing",
    )
    assert code == "FACTORY_STAGE_FAILED"


def test_classify_qa_llm_unavailable() -> None:
    code = _classify_factory_failure_code(
        stage="quality_gate",
        detail="qa_llm_judgement_unavailable: provider offline",
    )
    assert code == "QA_LLM_JUDGEMENT_UNAVAILABLE"


def test_classify_rejects_bare_403_line_number_false_positive() -> None:
    """Path/line numbers containing 403 must not become provider quota blocks."""
    code = _classify_factory_failure_code(
        stage="director_dispatch",
        detail="Stage failed at src/engine/handler.go:403: missing import",
    )
    assert code == "FACTORY_STAGE_FAILED"


def test_classify_rejects_generic_forbidden_path_message() -> None:
    code = _classify_factory_failure_code(
        stage="pm_planning",
        detail="forbidden path traversal outside workspace",
    )
    assert code == "FACTORY_STAGE_FAILED"


def test_classify_rejects_disk_quota_word_alone() -> None:
    code = _classify_factory_failure_code(
        stage="director_dispatch",
        detail="disk quota exceeded writing artifact",
    )
    assert code == "FACTORY_STAGE_FAILED"
