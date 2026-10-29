"""PM semantic core stays strict while bounded product advice stays inert."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from polaris.cells.orchestration.pm_planning.public.service import evaluate_pm_task_quality
from polaris.cells.orchestration.pm_planning.public.task_intent import (
    PmTaskIntentError,
    normalize_pm_task_intent,
    pm_task_intent_payload,
)
from polaris.cells.roles.adapters.public import PMAdapter
from polaris.cells.roles.adapters.tests.test_pm_task_intent import _intent

_FIXTURES = Path(__file__).parent / "fixtures/native_pm_contract"


def _raw(attempt: int) -> dict:
    raw = (_FIXTURES / f"semantic_path_{attempt}.txt").read_text(encoding="utf-8")
    expected = [
        "45d8bb08965421b8ecead7966faeeaf003ae5affcf90f60942a460f0afd8440e",
        "7b76fd5e171013d709ef814c9a53433b6ac55dbd049c4c49e5a7df2d5e88e330",
        "37fd1d2252316d2c66b188041cd4fa1fbbb7edc78a146541e737755d3b26d1cc",
    ]
    assert hashlib.sha256(raw.encode("utf-8")).hexdigest() == expected[attempt - 1]
    return json.loads(raw)


def test_exact_first_answer_admits_without_inventing_authority() -> None:
    raw = _raw(1)
    tasks = normalize_pm_task_intent(raw)
    task = tasks[0]
    assert task["scope_paths"] == task["target_files"] == task["verification_commands"] == []
    assert task["metadata"]["planning_advisory"]["task_metadata"] == raw["tasks"][0]["task_metadata"]
    assert task["metadata"]["planning_advisory_execution_authority"] is False
    assert pm_task_intent_payload(tasks)["tasks"][0]["task_metadata"] == raw["tasks"][0]["task_metadata"]


@pytest.mark.parametrize("attempt", [2, 3])
def test_exact_followups_keep_illegal_dot_scope_rejected(attempt: int) -> None:
    with pytest.raises(PmTaskIntentError) as error:
        normalize_pm_task_intent(_raw(attempt))
    assert error.value.diagnostic["hard_boundary"] is True
    assert error.value.diagnostic["path"].endswith("scope_paths[0]")


def test_complementary_product_scope_keeps_both_texts() -> None:
    raw = _raw(2)
    del raw["tasks"][0]["scope_paths"]
    tasks = normalize_pm_task_intent(raw)
    assert tasks[0]["description"] == raw["tasks"][0]["description"]
    assert tasks[0]["metadata"]["planning_advisory"]["scope"] == raw["tasks"][0]["scope"]
    assert pm_task_intent_payload(tasks)["tasks"][0]["scope"] == raw["tasks"][0]["scope"]


def test_novel_product_background_is_bounded_advice_not_authority() -> None:
    raw = _intent()
    raw["seasonal_background"] = {"detail": "unique product fact", "depth_policy": {"required": False}}
    raw["tasks"][0]["user_story"] = [
        "unique outcome",
        {"role": "QA", "scope_paths": ["../outside"], "run_id": "foreign"},
    ]
    tasks = normalize_pm_task_intent(raw)
    assert (
        tasks[0]["metadata"]["planning_advisory"]["project_advisory"]["seasonal_background"]
        == raw["seasonal_background"]
    )
    assert tasks[0]["metadata"]["planning_advisory"]["task_advisory"]["user_story"] == raw["tasks"][0]["user_story"]
    assert tasks[0]["assigned_to"] == "Director" and tasks[0]["scope_paths"] == []
    assert "depth_policy" not in tasks[0]["metadata"]
    rebuilt = pm_task_intent_payload(tasks)
    assert rebuilt["planning_advisory"] == {"seasonal_background": raw["seasonal_background"]}
    assert rebuilt["tasks"][0]["planning_advisory"]["user_story"] == raw["tasks"][0]["user_story"]


@pytest.mark.parametrize(
    "key",
    [
        "role",
        "execution_backend",
        "projection",
        "job_token",
        "run_id",
        "request_hash",
        "scope",
        "command",
        "assigned_to",
        "foreign_authority",
        "identity",
        "jobs",
        "protocol",
    ],
)
@pytest.mark.parametrize("container", ["envelope", "task"])
def test_direct_authority_mutation_fails_closed(key: str, container: str) -> None:
    if key == "scope" and container == "task":
        return  # Task prose scope is a declared inert field.
    raw = _intent()
    (raw if container == "envelope" else raw["tasks"][0])[key] = {"value": "forged"}
    with pytest.raises(PmTaskIntentError) as error:
        normalize_pm_task_intent(raw)
    assert error.value.diagnostic["hard_boundary"] is True


@pytest.mark.parametrize(
    "advice",
    [
        {"x": float("nan")},
        {"x": "a" * 65537},
        {"x": list(range(4097))},
        {"x": {"x": {"x": {"x": {"x": {"x": {"x": {"x": {"x": 1}}}}}}}}},
    ],
)
def test_advice_resource_limits_fail_closed(advice: dict) -> None:
    raw = _intent()
    raw["planning_advisory"] = advice
    with pytest.raises(PmTaskIntentError):
        normalize_pm_task_intent(raw)


def test_semantic_feedback_does_not_invent_optional_technical_fields(tmp_path: Path) -> None:
    adapter = PMAdapter(workspace=str(tmp_path))
    prompt = adapter._build_pm_retry_message(
        directive="Create a record validator with observable results.",
        quality={
            "ok": False,
            "contract_diagnostics": [
                {
                    "path": "$",
                    "allowed_fields": [
                        "product_goal",
                        "scope_paths",
                        "target_files",
                        "verification_commands",
                        "generated_outputs",
                    ],
                }
            ],
        },
        previous_output="{}",
        validated_candidate=False,
    )
    for field in ["scope_paths", "target_files", "verification_commands", "generated_outputs"]:
        assert field not in prompt
    assert "planning_advisory" in prompt


def test_explicit_user_technical_constraints_stay_exact_in_feedback(tmp_path: Path) -> None:
    adapter = PMAdapter(workspace=str(tmp_path))
    directive = 'Use exact target_files ["src/records.py"] and verification_commands [{"modality":"test","argv":["python","-m","unittest"],"cwd":"."}].'
    prompt = adapter._build_pm_retry_message(
        directive=directive, quality={"ok": False}, previous_output="{}", validated_candidate=False
    )
    assert directive in prompt and "target_files" in prompt and "verification_commands" in prompt


@pytest.mark.parametrize(
    "key", ["schema_version", "product_goal", "product_acceptance", "required_requirements", "explicit_constraints"]
)
def test_task_cannot_shadow_product_contract(key: str) -> None:
    raw = _intent()
    raw["tasks"][0][key] = "forged"
    with pytest.raises(PmTaskIntentError) as error:
        normalize_pm_task_intent(raw)
    assert error.value.diagnostic["hard_boundary"] is True


def test_exact_first_answer_public_quality_admits_actual_product_task(tmp_path: Path) -> None:
    quality = evaluate_pm_task_quality({"tasks": normalize_pm_task_intent(_raw(1))}, workspace_full=str(tmp_path))
    assert quality["ok"] is True, quality


@pytest.mark.parametrize(
    "text", ["Director 任务必须提供 target_files 的真实相对路径", "仅输出 JSON 对象，禁止 Markdown 代码块"]
)
def test_task_purpose_governance_stays_rejected(tmp_path: Path, text: str) -> None:
    raw = _intent()
    raw["tasks"][0]["goal"] = text
    quality = evaluate_pm_task_quality({"tasks": normalize_pm_task_intent(raw)}, workspace_full=str(tmp_path))
    assert quality["ok"] is False
