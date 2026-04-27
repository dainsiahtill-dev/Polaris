"""Tri-Council coordination module for Polaris engine.

This module handles multi-role coordination when tasks fail QA verification,
implementing escalation chains through ChiefEngineer, PM, Architect, and Human.
"""

from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING, Any, cast

from polaris.delivery.cli.pm.engine.helpers import (
    _env_non_negative_int,
    _join_non_empty,
    _normalize_bool,
    _normalize_failure_detail,
    _safe_int,
)
from polaris.delivery.cli.pm.utils import normalize_path_list, normalize_str_list
from polaris.kernelone.events import emit_dialogue, emit_event
from polaris.kernelone.fs.jsonl.ops import append_jsonl
from polaris.kernelone.fs.text_ops import write_json_atomic

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)

_DEFAULT_TRI_COUNCIL_MAX_ROUNDS = 2
_DEFAULT_COORDINATION_STAGE_RETRY_BUDGET = 2
_DEFAULT_TRI_COUNCIL_START_RETRY = 0
_DEFAULT_MAX_DIRECTOR_RETRIES = 5
_COORDINATION_ESCALATION_CHAIN: tuple[str, ...] = (
    "ChiefEngineer",
    "PM",
    "Architect",
    "Human",
)


def _resolve_tri_council_policy(
    *,
    qa_contract: dict[str, Any],
    enabled_override: str = "",
    max_rounds_override: str = "",
) -> dict[str, Any]:
    """Resolve tri-council policy from QA contract and environment."""
    _raw_policy_raw = qa_contract.get("coordination") if isinstance(qa_contract.get("coordination"), dict) else {}
    raw_policy = cast("dict[str, Any]", _raw_policy_raw)
    enabled = _normalize_bool(raw_policy.get("enabled"), default=True)
    override_token = str(enabled_override or "").strip().lower()
    if override_token in {"1", "true", "yes", "on", "0", "false", "no", "off"}:
        enabled = _normalize_bool(override_token, default=enabled)

    max_rounds = _safe_int(
        raw_policy.get("max_rounds"),
        default=_DEFAULT_TRI_COUNCIL_MAX_ROUNDS,
    )
    if max_rounds < 1:
        max_rounds = _DEFAULT_TRI_COUNCIL_MAX_ROUNDS
    if str(max_rounds_override or "").strip():
        override_rounds = _safe_int(max_rounds_override, default=max_rounds)
        if override_rounds > 0:
            max_rounds = override_rounds

    raw_triggers = raw_policy.get("triggers")
    if isinstance(raw_triggers, list):
        triggers = [str(item or "").strip().lower() for item in raw_triggers if str(item or "").strip()]
    else:
        triggers = []
    if not triggers:
        triggers = ["complex_task", "qa_fail", "qa_inconclusive"]
    deduped: list[str] = []
    for trigger in triggers:
        if trigger not in deduped:
            deduped.append(trigger)

    return {
        "enabled": enabled,
        "max_rounds": max_rounds,
        "triggers": deduped,
    }


def _looks_complex_for_council(
    *,
    task: dict[str, Any],
    qa_contract: dict[str, Any],
    changed_files: Sequence[str],
) -> bool:
    """Check if task is complex enough to trigger council coordination."""
    if _normalize_bool(task.get("complex_task"), default=False):
        return True

    task_type = str(qa_contract.get("task_type") or task.get("task_type") or task.get("kind") or "").strip().lower()
    if task_type.startswith("ui_") or task_type.startswith("3d_"):
        return True
    if task_type in {"ui_canvas", "frontend", "e2e", "workflow", "integration"}:
        return True

    scope_paths = normalize_str_list(task.get("scope_paths") or [])
    target_files = normalize_path_list(task.get("target_files") or [])
    if len(scope_paths) >= 3 or len(target_files) >= 6:
        return True
    if len(changed_files) >= 8:
        return True

    combined = " ".join(
        [
            str(task.get("title") or ""),
            str(task.get("goal") or ""),
            str(task.get("spec") or ""),
        ]
    ).lower()
    complex_keywords = (
        "cross module",
        "migration",
        "workflow",
        "multi step",
        "3d",
        "canvas",
        "playwright",
        "e2e",
    )
    return any(keyword in combined for keyword in complex_keywords)


def _tri_council_action_for_failure(
    *,
    qa_verdict: str,
    diagnostics: str,
    missing_evidence: Sequence[str],
    failed_gates: Sequence[str],
    resolution_role: str,
    round_count: int,
    max_rounds: int,
) -> tuple[str, str]:
    """Determine tri-council action for QA failure."""
    verdict = str(qa_verdict or "").strip().upper()
    role_token = str(resolution_role or "").strip().lower() or "chiefengineer"

    if round_count >= max_rounds and verdict in {"FAIL", "INCONCLUSIVE"}:
        # Escalate to Architect for system-level replanning instead of blocking
        return "escalate_to_architect", "tri_council_round_limit_reached"

    if verdict == "INCONCLUSIVE":
        if missing_evidence:
            return "retry_with_evidence", f"{role_token}_qa_missing_evidence"
        return "replan_required", f"{role_token}_qa_inconclusive_requires_strategy_change"

    if verdict == "FAIL":
        if failed_gates:
            return "retry_with_fix", f"{role_token}_qa_failed_gates"
        return "retry_with_fix", f"{role_token}_qa_fail"

    if verdict == "PASS":
        return "accept_pass", "qa_pass"

    return "replan_required", f"{role_token}_qa_unknown_verdict"


def _clamp_coordination_stage(value: Any) -> int:
    """Clamp coordination stage to valid range."""
    max_stage = max(0, len(_COORDINATION_ESCALATION_CHAIN) - 1)
    stage = _safe_int(value, default=0)
    if stage < 0:
        return 0
    if stage > max_stage:
        return max_stage
    return stage


def _coordination_role_for_stage(stage: int) -> str:
    """Get coordination role for given escalation stage."""
    index = _clamp_coordination_stage(stage)
    return _COORDINATION_ESCALATION_CHAIN[index]


def _coordination_participants_for_role(role: str) -> list[str]:
    """Get participants for coordination role."""
    role_token = str(role or "").strip()
    if role_token == "ChiefEngineer":
        return ["Auditor", "Director", "ChiefEngineer"]
    if role_token == "PM":
        return ["Auditor", "Director", "ChiefEngineer", "PM"]
    if role_token == "Architect":
        return ["Auditor", "Director", "ChiefEngineer", "PM", "Architect"]
    if role_token == "Human":
        return ["Auditor", "Director", "ChiefEngineer", "PM", "Architect", "Human"]
    return ["Auditor", "Director"]


def _meeting_improvement_hint(
    *,
    reason: str,
    diagnostics: str,
    next_role: str,
) -> str:
    """Generate improvement hint from meeting outcome."""
    reason_token = str(reason or "").strip().lower()
    diagnostics_token = str(diagnostics or "").strip().lower()
    if "qa_route_capability_missing" in reason_token or "qa_plugin_unavailable" in diagnostics_token:
        return "补齐 QA 路由/插件能力，避免 INCONCLUSIVE 持续升级。"
    if "missing_evidence" in reason_token:
        return "增强证据规划与 required_evidence 生成规则，减少缺证据失败。"
    if "failed_gates" in reason_token:
        return "加强 Director 前置自检与Chief Engineer施工图对齐，降低硬门禁失败率。"
    if "tri_council_round_limit_reached" in reason_token:
        return "升级链路耗尽：需要人工复盘该失败模式并更新 Polaris 策略模板。"
    if str(next_role or "").strip() == "Architect":
        return "需求/验收契约存在歧义，需Architect补充文档契约与边界定义。"
    if str(next_role or "").strip() == "PM":
        return "当前由 PM 进行任务重排与策略重规划。"
    if str(next_role or "").strip() == "ChiefEngineer":
        return "由Chief Engineer补齐施工图（模块/文件/方法）并缩小修复范围。"
    return "记录该会议样本并纳入 Polaris 改进知识库。"


def _runtime_learning_paths(
    *,
    run_dir: str,
    workspace_full: str,
) -> tuple[list[str], list[str]]:
    """Resolve learning dataset paths for meeting records."""
    from polaris.delivery.cli.pm.engine.helpers import _dedupe_paths
    from polaris.kernelone.storage.io_paths import resolve_artifact_path

    sample_paths: list[str] = []
    improve_paths: list[str] = []
    if run_dir:
        sample_paths.append(os.path.join(run_dir, "engine", "learning", "meeting.training_samples.jsonl"))
        improve_paths.append(os.path.join(run_dir, "engine", "learning", "polaris.improvements.jsonl"))
    if workspace_full:
        sample_paths.append(
            resolve_artifact_path(
                workspace_full,
                "",
                "runtime/learning/meeting.training_samples.jsonl",
            )
        )
        improve_paths.append(
            resolve_artifact_path(
                workspace_full,
                "",
                "runtime/learning/polaris.improvements.jsonl",
            )
        )
    return _dedupe_paths(sample_paths), _dedupe_paths(improve_paths)


def _persist_meeting_learning_records(
    *,
    workspace_full: str,
    run_dir: str,
    payload: dict[str, Any],
    decision_path: str,
) -> dict[str, Any]:
    """Persist meeting records to learning datasets."""
    enabled = _normalize_bool(
        os.environ.get("KERNELONE_MEETING_DATASET_ENABLED"),
        default=True,
    )
    if not enabled:
        return {"enabled": False, "sample_paths": [], "improvement_paths": []}

    _scope_raw = payload.get("coordination_scope")
    scope = cast("dict[str, Any]", _scope_raw if isinstance(_scope_raw, dict) else {})
    _messages_raw = payload.get("messages")
    messages = cast("dict[str, Any]", _messages_raw if isinstance(_messages_raw, dict) else {})
    next_role = str(scope.get("next_role") or "").strip()
    diagnostics = str(payload.get("qa_diagnostics") or "").strip()
    reason = str(payload.get("reason") or "").strip()
    sample_id = _join_non_empty(
        [
            str(payload.get("run_id") or ""),
            str(payload.get("task_id") or ""),
            str(payload.get("stage") or ""),
            f"r{_safe_int(payload.get('round_count'), default=0)}",
        ]
    ).replace("; ", ":")

    training_record = {
        "schema_version": 1,
        "record_type": "meeting_training_sample",
        "timestamp_epoch": time.time(),
        "sample_id": sample_id,
        "source": "tri_council",
        "run_id": payload.get("run_id"),
        "pm_iteration": payload.get("pm_iteration"),
        "task_id": payload.get("task_id"),
        "task_title": payload.get("task_title"),
        "trigger": payload.get("trigger"),
        "stage": payload.get("stage"),
        "discussion_scope": scope,
        "problem": {
            "qa_verdict": payload.get("qa_verdict"),
            "error_code": payload.get("error_code"),
            "failure_detail": payload.get("failure_detail"),
            "diagnostics": diagnostics,
            "failed_gates": payload.get("qa_failed_gates", []),
            "missing_evidence": payload.get("qa_missing_evidence", []),
        },
        "conversation": messages,
        "decision": {
            "action": payload.get("action"),
            "reason": reason,
            "round_count": payload.get("round_count"),
            "max_rounds": payload.get("max_rounds"),
            "decision_path": decision_path,
        },
        "labels": {
            "target_action": payload.get("action"),
            "target_role": next_role,
        },
        "evidence_refs": [decision_path] if decision_path else [],
    }

    improvement_record = {
        "schema_version": 1,
        "record_type": "polaris_improvement_item",
        "timestamp_epoch": time.time(),
        "source": "tri_council",
        "run_id": payload.get("run_id"),
        "pm_iteration": payload.get("pm_iteration"),
        "task_id": payload.get("task_id"),
        "task_title": payload.get("task_title"),
        "trigger": payload.get("trigger"),
        "reason": reason,
        "next_role": next_role,
        "suggested_improvement": _meeting_improvement_hint(
            reason=reason,
            diagnostics=diagnostics,
            next_role=next_role,
        ),
        "evidence_refs": [decision_path] if decision_path else [],
    }

    sample_paths, improve_paths = _runtime_learning_paths(
        run_dir=run_dir,
        workspace_full=workspace_full,
    )
    for path in sample_paths:
        append_jsonl(path, training_record, buffered=False)
    for path in improve_paths:
        append_jsonl(path, improvement_record, buffered=False)

    return {
        "enabled": True,
        "sample_paths": sample_paths,
        "improvement_paths": improve_paths,
    }


def _run_tri_council_round(
    *,
    stage: str,
    workspace_full: str,
    task: dict[str, Any],
    qa_contract: dict[str, Any],
    qa_result: dict[str, Any],
    qa_verdict: str,
    task_root: str,
    run_dir: str,
    run_id: str,
    pm_iteration: int,
    task_id: str,
    task_title: str,
    events_path: str,
    dialogue_path: str,
    director_status: str,
    changed_files: Sequence[str],
    coordination_policy: dict[str, Any],
    error_code: str,
    failure_detail: str,
    qa_retry_count: int,
    max_director_retries: int,
) -> dict[str, Any]:
    """Run a tri-council coordination round."""
    stage_token = str(stage or "").strip().lower() or "unknown"
    discuss_after_retry = _env_non_negative_int(
        "KERNELONE_TRI_COUNCIL_START_RETRY",
        _DEFAULT_TRI_COUNCIL_START_RETRY,
    )
    if discuss_after_retry < 0:
        discuss_after_retry = _DEFAULT_TRI_COUNCIL_START_RETRY
    if max(qa_retry_count, 0) < discuss_after_retry:
        # Keep fix-forward loop active before entering multi-role coordination.
        return {}

    _policy_raw = coordination_policy if isinstance(coordination_policy, dict) else {}
    policy = cast("dict[str, Any]", _policy_raw)
    if not _normalize_bool(policy.get("enabled"), default=True):
        return {}
    _triggers_raw = policy.get("triggers")
    triggers = cast(
        "list[Any]",
        _triggers_raw if isinstance(_triggers_raw, list) else ["complex_task", "qa_fail", "qa_inconclusive"],
    )
    triggers_set = {str(item or "").strip().lower() for item in triggers if str(item or "").strip()}
    max_rounds = _safe_int(
        policy.get("max_rounds"),
        default=_DEFAULT_TRI_COUNCIL_MAX_ROUNDS,
    )
    if max_rounds < 1:
        max_rounds = _DEFAULT_TRI_COUNCIL_MAX_ROUNDS

    trigger = ""
    verdict = str(qa_verdict or "").strip().upper()
    is_complex = _looks_complex_for_council(
        task=task,
        qa_contract=qa_contract,
        changed_files=changed_files,
    )
    if stage_token == "pre_dispatch":
        if is_complex and "complex_task" in triggers_set:
            trigger = "complex_task"
    elif stage_token == "post_qa_failure":
        if verdict == "FAIL" and "qa_fail" in triggers_set:
            trigger = "qa_fail"
        elif verdict == "INCONCLUSIVE" and "qa_inconclusive" in triggers_set:
            trigger = "qa_inconclusive"
    if not trigger:
        return {}

    previous_round = _safe_int(task.get("tri_council_round_count"), default=0)
    round_count = previous_round
    if stage_token == "post_qa_failure":
        round_count = previous_round + 1
        task["tri_council_round_count"] = round_count
    current_stage = _clamp_coordination_stage(task.get("coordination_escalation_stage"))
    current_role = _coordination_role_for_stage(current_stage)

    failed_gates = qa_result.get("failed_gates") if isinstance(qa_result, dict) else []
    failed_gates = failed_gates if isinstance(failed_gates, list) else []
    missing_evidence = qa_result.get("missing_evidence") if isinstance(qa_result, dict) else []
    missing_evidence = missing_evidence if isinstance(missing_evidence, list) else []
    diagnostics = str(qa_result.get("diagnostics") or "").strip() if isinstance(qa_result, dict) else ""

    if stage_token == "pre_dispatch":
        action = "align_strategy"
        reason = "complex_task_requires_alignment"
    else:
        action, reason = _tri_council_action_for_failure(
            qa_verdict=verdict,
            diagnostics=diagnostics,
            missing_evidence=missing_evidence,
            failed_gates=failed_gates,
            resolution_role=current_role,
            round_count=round_count,
            max_rounds=max_rounds,
        )

    auditor_message = ""
    if stage_token == "pre_dispatch":
        auditor_message = (
            f"Complex task detected for {task_id}. Recommend pre-aligning acceptance path "
            f"(task_type={qa_contract.get('task_type') or 'generic'!s})."
        )
    else:
        auditor_message = _join_non_empty(
            [
                f"QA verdict={verdict}",
                f"diagnostics={diagnostics}" if diagnostics else "",
                ("failed_gates=" + ",".join(str(item) for item in failed_gates) if failed_gates else ""),
                ("missing_evidence=" + ",".join(str(item) for item in missing_evidence) if missing_evidence else ""),
            ]
        )
    if not auditor_message:
        auditor_message = "QA provided no additional diagnostics."

    changed_preview = [str(item) for item in list(changed_files)[:5] if str(item or "").strip()]
    director_message = _join_non_empty(
        [
            f"Director status={director_status or 'unknown'}",
            ("changed_files_preview=" + ",".join(changed_preview) if changed_preview else "changed_files_preview=none"),
            f"qa_retry={max(qa_retry_count, 0)}/{max(max_director_retries, 0)}"
            if stage_token == "post_qa_failure"
            else "",
        ]
    )
    pm_message = _join_non_empty(
        [
            f"PM decision: {action}",
            f"reason={reason}" if reason else "",
            f"round={round_count}/{max_rounds}" if stage_token == "post_qa_failure" else "",
        ]
    )

    payload: dict[str, Any] = {
        "schema_version": 1,
        "stage": stage_token,
        "trigger": trigger,
        "action": action,
        "reason": reason,
        "run_id": run_id,
        "pm_iteration": int(pm_iteration or 0),
        "task_id": task_id,
        "task_title": task_title,
        "qa_verdict": verdict,
        "error_code": str(error_code or "").strip(),
        "failure_detail": _normalize_failure_detail(failure_detail),
        "qa_failed_gates": failed_gates,
        "qa_missing_evidence": missing_evidence,
        "qa_diagnostics": diagnostics,
        "director_status": str(director_status or "").strip().lower(),
        "changed_files": [str(item) for item in list(changed_files)[:20]],
        "round_count": round_count,
        "max_rounds": max_rounds,
        "coordination_policy": policy,
        "messages": {
            "auditor": auditor_message,
            "director": director_message,
            "pm": pm_message,
        },
    }

    decision_path = ""
    if task_root:
        round_token = max(1, round_count)
        decision_path = os.path.join(
            task_root,
            "coordination",
            f"tri_council.{stage_token}.r{round_token:02d}.json",
        )
        payload["decision_path"] = decision_path
        write_json_atomic(decision_path, payload)

    if run_dir:
        council_log = os.path.join(run_dir, "engine", "coordination", "tri_council.decisions.jsonl")
        append_jsonl(
            council_log,
            {
                "timestamp_epoch": time.time(),
                "run_id": run_id,
                "pm_iteration": int(pm_iteration or 0),
                "task_id": task_id,
                "stage": stage_token,
                "trigger": trigger,
                "action": action,
                "reason": reason,
                "round_count": round_count,
                "max_rounds": max_rounds,
                "decision_path": decision_path,
            },
            buffered=False,
        )

    emit_event(
        events_path,
        kind="status",
        actor="Engine",
        name="tri_council_round",
        refs={
            "task_id": task_id,
            "phase": "tri_council",
            "files": [decision_path] if decision_path else [],
        },
        summary=f"Tri-Council {stage_token} decision: {action}",
        ok=(action not in {"request_human"}),
        output={
            "stage": stage_token,
            "trigger": trigger,
            "action": action,
            "reason": reason,
            "round_count": round_count,
            "max_rounds": max_rounds,
        },
        error="" if action != "request_human" else "TRI_COUNCIL_ESCALATED",
    )
    emit_dialogue(
        dialogue_path,
        speaker="Auditor",
        type="coordination",
        text=auditor_message,
        summary=f"Tri-Council {stage_token}: Auditor input",
        run_id=run_id,
        pm_iteration=int(pm_iteration or 0),
        refs={"task_id": task_id, "phase": "tri_council"},
        meta={"trigger": trigger, "stage": stage_token},
    )
    emit_dialogue(
        dialogue_path,
        speaker="Director",
        type="coordination",
        text=director_message,
        summary=f"Tri-Council {stage_token}: Director input",
        run_id=run_id,
        pm_iteration=int(pm_iteration or 0),
        refs={"task_id": task_id, "phase": "tri_council"},
        meta={"trigger": trigger, "stage": stage_token},
    )
    emit_dialogue(
        dialogue_path,
        speaker="PM",
        type="council",
        text=pm_message,
        summary=f"Tri-Council {stage_token}: PM decision {action}",
        run_id=run_id,
        pm_iteration=int(pm_iteration or 0),
        refs={"task_id": task_id, "phase": "tri_council"},
        meta={
            "trigger": trigger,
            "stage": stage_token,
            "action": action,
            "reason": reason,
            "round_count": round_count,
            "max_rounds": max_rounds,
        },
    )
    return payload


__all__ = [
    "_COORDINATION_ESCALATION_CHAIN",
    "_DEFAULT_TRI_COUNCIL_MAX_ROUNDS",
    "_DEFAULT_TRI_COUNCIL_START_RETRY",
    "_clamp_coordination_stage",
    "_coordination_participants_for_role",
    "_coordination_role_for_stage",
    "_looks_complex_for_council",
    "_meeting_improvement_hint",
    "_persist_meeting_learning_records",
    "_resolve_tri_council_policy",
    "_run_tri_council_round",
    "_runtime_learning_paths",
    "_tri_council_action_for_failure",
]
