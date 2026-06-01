from __future__ import annotations

import os
import sys

BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS_ROOT = os.path.join(BACKEND_ROOT, "scripts")
CORE_ROOT = os.path.join(BACKEND_ROOT, "core", "polaris_loop")
for candidate in (BACKEND_ROOT, SCRIPTS_ROOT, CORE_ROOT):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from polaris.cells.orchestration.pm_planning.internal.task_quality_gate import (  # noqa: E402
    autofix_pm_contract_for_quality,
    evaluate_pm_task_quality,
)
from polaris.cells.orchestration.pm_planning.public.pipeline import (  # noqa: E402
    _should_promote_pm_quality_candidate as should_promote_pm_quality_candidate,
)
from polaris.delivery.cli.pm.tasks import normalize_tasks  # noqa: E402


def test_evaluate_pm_task_quality_rejects_prompt_leakage() -> None:
    payload = {
        "tasks": [
            {
                "id": "PM-LEAK-1",
                "title": "You are Polaris meta architect",
                "goal": "No Yapping and think before you code",
                "assigned_to": "Director",
                "scope_paths": ["src/game"],
                "acceptance_criteria": ["add compile checks", "emit evidence logs"],
            }
        ]
    }
    report = evaluate_pm_task_quality(payload, docs_stage={})
    assert report["ok"] is False
    issues = "\n".join(report.get("critical_issues") or []).lower()
    assert "leakage" in issues


def test_evaluate_pm_task_quality_rejects_docs_stage_scope_violation() -> None:
    payload = {
        "tasks": [
            {
                "id": "PM-DOCS-1",
                "title": "Refine requirements acceptance clauses",
                "goal": "expand verification paths and measurable acceptance constraints",
                "assigned_to": "Director",
                "target_files": [
                    "workspace/docs/product/requirements.md",
                    "workspace/docs/architecture/plan.md",
                ],
                "acceptance_criteria": [
                    "document records explicit acceptance evidence",
                    "all updates remain in active document scope",
                ],
            }
        ]
    }
    docs_stage = {
        "enabled": True,
        "active_doc_path": "workspace/docs/product/requirements.md",
    }
    report = evaluate_pm_task_quality(payload, docs_stage=docs_stage)
    assert report["ok"] is False
    issues = "\n".join(report.get("critical_issues") or []).lower()
    assert "scope violation" in issues


def test_evaluate_pm_task_quality_rejects_docs_stage_without_section_intent() -> None:
    payload = {
        "tasks": [
            {
                "id": "PM-DOCS-2",
                "title": "Refine requirement sections",
                "goal": "enhance measurable constraints in requirements document",
                "assigned_to": "Director",
                "target_files": ["workspace/docs/product/requirements.md"],
                "phase": "scaffold",
                "execution_checklist": ["step1", "step2", "step3"],
                "acceptance_criteria": [
                    "run command and verify evidence path",
                    "verify updated thresholds exist in document",
                ],
                "backlog_ref": "Refine constraints and verification matrix",
            },
            {
                "id": "PM-DOCS-3",
                "title": "Refine verification matrix",
                "goal": "add verification matrix and evidence mapping",
                "assigned_to": "Director",
                "target_files": ["workspace/docs/product/requirements.md"],
                "phase": "verification",
                "depends_on": ["PM-DOCS-2"],
                "execution_checklist": ["step1", "step2", "step3"],
                "acceptance_criteria": [
                    "run command and verify coverage report path",
                    "verify matrix rows contain command and threshold",
                ],
                "backlog_ref": "Build verification matrix",
            },
        ]
    }
    docs_stage = {
        "enabled": True,
        "active_doc_path": "workspace/docs/product/requirements.md",
    }
    report = evaluate_pm_task_quality(payload, docs_stage=docs_stage)
    assert report["ok"] is False
    issues = "\n".join(report.get("critical_issues") or []).lower()
    assert "doc_sections" in issues


def test_evaluate_pm_task_quality_rejects_natural_language_scope_paths() -> None:
    payload = {
        "tasks": [
            {
                "id": "PM-PATH-1",
                "title": "Implement asset service layer",
                "goal": "build asset import and local storage behavior with deterministic validation",
                "assigned_to": "Director",
                "scope_paths": ["backend asset API routes and frontend upload panel"],
                "phase": "implementation",
                "execution_checklist": [
                    "Inspect existing app structure",
                    "Implement bounded changes",
                    "Run verification commands",
                ],
                "acceptance_criteria": [
                    "Run `npm test` and verify the asset workflow test passes",
                    "Run `npm run build` and verify the renderer compiles",
                ],
            }
        ]
    }

    report = evaluate_pm_task_quality(payload, docs_stage={})

    assert report["ok"] is False
    issues = "\n".join(report.get("critical_issues") or []).lower()
    assert "concrete relative scope paths" in issues


def test_normalize_tasks_moves_directory_targets_to_scope_paths() -> None:
    tasks = normalize_tasks(
        [
            {
                "id": "T01",
                "title": "Build FashionGen shell",
                "goal": "create renderer and backend scaffolding",
                "target_files": [
                    "src/renderer",
                    "src/shared/generationSpec.ts",
                    "package.json",
                ],
                "acceptance_criteria": ["npm test passes"],
            }
        ],
        iteration=1,
    )

    task = tasks[0]
    assert task["target_files"] == ["src/shared/generationSpec.ts", "package.json"]
    assert task["scope_paths"] == ["src/renderer"]


def test_evaluate_pm_task_quality_accepts_specific_actionable_tasks() -> None:
    payload = {
        "tasks": [
            {
                "id": "PM-Q1",
                "title": "Define gateway session contract",
                "goal": "design session join and reconnect contract fields with deterministic validation points",
                "assigned_to": "ChiefEngineer",
                "scope_paths": ["docs/product"],
                "target_files": ["docs/product/interface_contract.md"],
                "phase": "bootstrap",
                "execution_checklist": [
                    "Read docs/product/interface_contract.md and capture current contract gaps",
                    "Define request/response/error fields with version constraints",
                    "Write evidence path mapping for downstream QA validation",
                ],
                "acceptance_criteria": [
                    'Run `rg -n "gateway|session|error" docs/product/interface_contract.md` and verify required sections exist',
                    "Contract table includes request, response, error model, idempotency, and evidence artifact path",
                ],
            },
            {
                "id": "PM-Q2",
                "title": "Build verification checklist for dispatch",
                "goal": "create executable verification checklist and required evidence mapping for PM dispatch quality",
                "assigned_to": "Director",
                "scope_paths": ["docs/product"],
                "target_files": ["docs/product/plan.md"],
                "phase": "verification",
                "depends_on": ["PM-Q1"],
                "execution_checklist": [
                    "Create command checklist with expected stdout signals",
                    "Map each command to evidence artifact path",
                    "Verify checklist entries are deterministic and executable",
                ],
                "acceptance_criteria": [
                    "Checklist includes command, expected signal, evidence path, and pass/fail threshold",
                    "Evidence path section documents stdout/stderr capture locations",
                ],
            },
        ]
    }
    report = evaluate_pm_task_quality(payload, docs_stage={})
    assert report["ok"] is True
    assert (report.get("score") or 0) >= 70


def test_evaluate_pm_task_quality_accepts_chinese_actionable_checklists() -> None:
    payload = {
        "tasks": [
            {
                "id": "PM-ZH-1",
                "title": "运行时面板偏好持久化闭环",
                "goal": "把折叠状态与视图密度纳入 runtime manifest，并保证冷启动回读一致。",
                "assigned_to": "Director",
                "scope_paths": ["src/store", "src/main", "src/shared"],
                "target_files": [
                    "src/store/useStudioStore.ts",
                    "src/main/runtimeStore.ts",
                    "src/shared/runtimePreferences.ts",
                ],
                "phase": "implementation",
                "execution_checklist": [
                    "定义最小字段契约与非法值清洗规则。",
                    "接入 store 更新动作并保持状态隔离。",
                    "补齐冷启动回读测试。",
                ],
                "acceptance_criteria": [
                    "执行 `npm test -- src/store/useStudioStore.test.ts src/main/runtimeStore.test.ts` 通过。",
                    "证据文档 `.polaris/evidence/runtime-preferences.md` 记录字段契约与回读结果。",
                ],
                "backlog_ref": "面板级偏好需要进入 runtime manifest。",
            },
            {
                "id": "PM-ZH-2",
                "title": "批量预检失败阻断与 API 边界统一",
                "goal": "统一批量行与 API 入口的预检边界，失败时不创建无效 job。",
                "assigned_to": "Director",
                "scope_paths": ["services/api", "src/main", "tests"],
                "target_files": [
                    "services/api/jobs.py",
                    "src/main/jobClient.ts",
                    "tests/test_job_preflight.py",
                ],
                "phase": "verification",
                "depends_on": ["PM-ZH-1"],
                "execution_checklist": [
                    "抽取统一预检校验器。",
                    "接入批量提交与 API 提交入口。",
                    "补充失败路径测试与证据记录。",
                ],
                "acceptance_criteria": [
                    "执行 `pytest tests/test_job_preflight.py -v` 通过。",
                    "失败响应必须包含 422 status 与 error code。",
                ],
                "backlog_ref": "预检失败不能生成无效 jobId。",
            },
        ]
    }
    report = evaluate_pm_task_quality(payload, docs_stage={})
    assert report["ok"] is True
    assert (report.get("score") or 0) >= 80


def test_evaluate_pm_task_quality_rejects_missing_detail_chain() -> None:
    payload = {
        "tasks": [
            {
                "id": "PM-R1",
                "title": "Refine requirements",
                "goal": "improve requirement clarity for future implementation",
                "assigned_to": "Director",
                "scope_paths": ["workspace/docs/product"],
                "target_files": ["workspace/docs/product/requirements.md"],
                "acceptance_criteria": ["update the doc", "improve quality"],
            },
            {
                "id": "PM-R2",
                "title": "Refine validation",
                "goal": "improve validation coverage in requirement doc",
                "assigned_to": "Director",
                "scope_paths": ["workspace/docs/product"],
                "target_files": ["workspace/docs/product/requirements.md"],
                "acceptance_criteria": ["add more checks", "enhance confidence"],
            },
        ]
    }
    report = evaluate_pm_task_quality(payload, docs_stage={})
    assert report["ok"] is False
    issues = "\n".join(report.get("critical_issues") or []).lower()
    assert "dependency chain" in issues
    assert "execution_checklist" in issues


def test_pm_quality_autofix_recovers_missing_execution_fields() -> None:
    payload = {
        "tasks": [
            {
                "id": "PM-AF-1",
                "title": "Implement gateway session refresh path",
                "goal": "implement deterministic token refresh behavior for gateway sessions",
                "assigned_to": "Director",
                "target_files": ["src/backend/app/gateway/session.py"],
                "scope_paths": ["src/backend/app/gateway"],
            },
            {
                "id": "PM-AF-2",
                "title": "Verify refresh flow integration behavior",
                "goal": "verify refresh path and failure handling with integration evidence",
                "assigned_to": "Director",
                "target_files": ["src/backend/tests/test_gateway_session.py"],
                "scope_paths": ["src/backend/tests"],
            },
        ]
    }

    stats = autofix_pm_contract_for_quality(payload, workspace_full=BACKEND_ROOT)
    assert stats["phases_added"] >= 2
    assert stats["checklists_added"] >= 2
    assert stats["deps_added"] >= 1
    assert stats["acceptance_added"] >= 2

    report = evaluate_pm_task_quality(payload, docs_stage={})
    assert report["ok"] is True
    assert (report.get("score") or 0) >= 70


def test_pm_quality_retry_keeps_previous_non_empty_candidate() -> None:
    best_payload = {
        "focus": "implementation_ready",
        "tasks": [
            {
                "id": "PM-BEST-1",
                "title": "Implement auth middleware",
            }
        ],
        "notes": "usable candidate",
    }
    parse_failed_payload = {
        "focus": "parse_failed",
        "tasks": [],
        "notes": "PM JSON parse failed.",
    }

    assert (
        should_promote_pm_quality_candidate(
            best_payload,
            {"ok": False, "score": 78},
            {},
            {},
        )
        is True
    )
    assert (
        should_promote_pm_quality_candidate(
            parse_failed_payload,
            {"ok": False, "score": 0},
            best_payload,
            {"ok": False, "score": 78},
        )
        is False
    )
