"""Independent requirement judgement cannot be replaced by runtime success."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.adapters.internal.qa_adapter import QAAdapter


def _runtime_pass() -> dict[str, Any]:
    return {
        "verdict": "PASS",
        "score": 100,
        "critical_issues": [],
        "major_issues": [],
        "warnings": [],
        "evidence": ["factory_runtime_hard_gate_passed=True"],
        "suggestions": [],
    }


@pytest.mark.parametrize("content", ["{}", "not valid json", '{"verdict":"UNKNOWN"}', 'broken "verdict": "PASS"'])
def test_missing_independent_judgement_cannot_pass(tmp_path: Path, content: str) -> None:
    adapter = QAAdapter(workspace=str(tmp_path))
    review = adapter._merge_review_result(_runtime_pass(), adapter._parse_review_result(content))
    finalized = adapter._finalize_review_result(review)
    assert finalized["passed"] is False
    assert "qa_llm_judgement_unavailable" in finalized["warnings"]


def test_semantic_failure_remains_failed_with_passing_commands(tmp_path: Path) -> None:
    adapter = QAAdapter(workspace=str(tmp_path))
    issue = "Valid input violates the original required equation."
    payload = {**_runtime_pass(), "verdict": "FAIL", "critical_issues": [issue]}
    review = adapter._merge_review_result(_runtime_pass(), adapter._parse_review_result(json.dumps(payload)))
    finalized = adapter._finalize_review_result(review)
    assert finalized["passed"] is False
    assert issue in finalized["critical_issues"]
    assert not any("verdict_downgraded" in str(item) for item in finalized["evidence"])


def test_explicit_zero_score_is_not_replaced_with_default_pass(tmp_path: Path) -> None:
    adapter = QAAdapter(workspace=str(tmp_path))
    review = adapter._merge_review_result(_runtime_pass(), adapter._parse_review_result('{"verdict":"PASS","score":0}'))
    finalized = adapter._finalize_review_result(review)
    assert finalized["passed"] is False
    assert finalized["score"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("response", ["{}", "not valid json", "semantic_failure"])
async def test_execute_persists_independent_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    response: str,
) -> None:
    adapter = QAAdapter(workspace=str(tmp_path))
    payload = {
        **_runtime_pass(),
        "verdict": "FAIL",
        "critical_issues": ["Valid input violates the original required equation."],
    }
    content = json.dumps(payload) if response == "semantic_failure" else response
    calls = []

    async def invoke(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"response": content}

    monkeypatch.setattr(adapter, "_call_role_llm", invoke)
    monkeypatch.setattr(adapter, "_run_static_review", lambda *_args, **_kwargs: _runtime_pass())
    monkeypatch.setattr(adapter, "_update_task_progress", lambda *_args, **_kwargs: None)
    observed = []
    monkeypatch.setattr(
        adapter,
        "_apply_taskboard_qa_verdict",
        lambda **kwargs: observed.append(kwargs["review_result"]) or {},
    )
    result = await adapter.execute("qa-task", {"review_target": "Original requirements"}, {"run_id": "qa-run"})
    report = json.loads(Path(result["artifacts"][0]).read_text(encoding="utf-8"))
    assert result["success"] is False
    assert result["passed"] is False
    assert report["passed"] is False
    assert observed[0]["passed"] is False
    assert len(calls) == (1 if response == "semantic_failure" else 2)


def test_qa_prompt_has_current_declared_source_and_independent_semantics(tmp_path: Path) -> None:
    (tmp_path / "module.py").write_text("VALUE = 7\n", encoding="utf-8")
    adapter = QAAdapter(workspace=str(tmp_path))
    payload = json.loads(adapter._build_qa_source_evidence({"metadata": {"target_files": ["module.py"]}}))
    assert payload["files"][0]["content"] == "VALUE = 7\n"
    assert len(payload["files"][0]["content_sha256"]) == 64
    assert payload["source_drift"] == []
    prompt = adapter._build_qa_prompt_appendix()
    assert "Factory Bench" not in prompt
    assert "requirements" in prompt
    assert "even when runtime commands passed" in prompt
