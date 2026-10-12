"""Planning prose is advisory; executable acceptance remains a hard obligation."""

from copy import deepcopy

import pytest
from polaris.cells.orchestration.pm_planning.public.service import evaluate_pm_task_quality


def _document_task() -> dict:
    return {
        "id": "PLAN",
        "title": "Describe the module interfaces and delivery checks",
        "goal": "Document concrete module boundaries and real build and test obligations for implementation",
        "assigned_to": "ChiefEngineer",
        "phase": "planning",
        "execution_backend": "code_edit",
        "target_files": ["docs/design.md"],
        "scope_paths": ["requirements.md", "docs"],
        "steps": ["Read requirements.md", "Specify module interfaces", "Write docs/design.md"],
        "acceptance": [
            "docs/design.md 存在并包含至少 3 条行为规则。",
            "蓝图禁止空 manifest 占位脚本，要求 build/test/start 实际驱动产品入口或核心规则验证。",
        ],
        "verification_commands": [],
        "depends_on": [],
    }


def test_document_planning_prose_warning_does_not_block_actual_execution() -> None:
    task = _document_task()
    original = deepcopy(task)
    report = evaluate_pm_task_quality({"tasks": [task]})
    assert not any("acceptance permits placeholder" in issue for issue in report["critical_issues"])
    assert any("acceptance prose" in warning for warning in report["warnings"])
    assert task == original


def test_advisory_prose_does_not_indirectly_reduce_planning_admission_score() -> None:
    task = _document_task()
    baseline = deepcopy(task)
    baseline["acceptance"][1] = "build/test/start 实际驱动产品入口或核心规则验证。"
    with_hint = evaluate_pm_task_quality({"tasks": [task]})
    without_hint = evaluate_pm_task_quality({"tasks": [baseline]})
    assert with_hint["score"] == without_hint["score"]
    assert len(with_hint["warnings"]) > len(without_hint["warnings"])


@pytest.mark.parametrize("phase,target", [("implementation", "src/app.ts"), ("qa", "reports/qa.md")])
def test_executable_and_qa_placeholder_acceptance_remains_blocked(phase: str, target: str) -> None:
    task = _document_task()
    task.update(phase=phase, target_files=[target], scope_paths=[target], assigned_to="Director")
    task["acceptance"] = [f"{target} 存在，验收允许 manifest-only 占位脚本。"]
    report = evaluate_pm_task_quality({"tasks": [task]})
    assert any("acceptance permits placeholder" in issue for issue in report["critical_issues"])


def test_document_planning_still_requires_a_measurable_artifact_anchor() -> None:
    task = _document_task()
    task["acceptance"] = ["蓝图禁止空 manifest 占位脚本。"]
    report = evaluate_pm_task_quality({"tasks": [task]})
    assert any(
        "acceptance requires executable command or file evidence" in issue for issue in report["critical_issues"]
    )


def test_qa_prohibition_is_advisory_without_changing_real_verifiers() -> None:
    task = _document_task()
    task.update(phase="qa", assigned_to="QA")
    task["acceptance"] = [
        "Verify docs/design.md contains at least 3 behavior rules.",
        "QA 拒绝 manifest-only / echo-only / 仅检查文件存在的脚本；确认 package.json scripts 实际驱动 tsc / node / node--test。",
    ]
    task["verification_commands"] = [{"modality": "test", "argv": ["npm", "test"], "cwd": "."}]
    original = deepcopy(task)
    report = evaluate_pm_task_quality({"tasks": [task]})
    assert not any("acceptance permits placeholder" in issue for issue in report["critical_issues"])
    assert task == original


@pytest.mark.parametrize("target", ["docs/design.md", "src/readme.py", "src/readme.ts"])
def test_explicit_weak_delivery_permission_has_no_document_or_filename_exemption(target: str) -> None:
    task = _document_task()
    task.update(target_files=[target], scope_paths=[target])
    task["acceptance"] = [f"Verify {target} contains at least 3 behavior rules.", "验收允许 manifest-only 占位脚本。"]
    report = evaluate_pm_task_quality({"tasks": [task]})
    assert any("acceptance permits placeholder" in issue for issue in report["critical_issues"])


@pytest.mark.parametrize(
    "text,blocked",
    [
        ("Use stub-only checks for acceptance.", True),
        ("允许 README 附录说明并拒绝 manifest-only 验收。", False),
    ],
)
def test_weak_permission_applies_to_its_own_object(text: str, blocked: bool) -> None:
    task = _document_task()
    task.update(phase="qa", assigned_to="QA")
    task["acceptance"] = ["Verify docs/design.md contains at least 3 behavior rules.", text]
    report = evaluate_pm_task_quality({"tasks": [task]})
    assert any("acceptance permits placeholder" in issue for issue in report["critical_issues"]) is blocked
