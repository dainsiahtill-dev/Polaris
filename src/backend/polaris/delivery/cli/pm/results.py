"""Director result handling for loop-pm."""

from datetime import datetime
from typing import Any

from polaris.delivery.cli.director_result_matcher import (
    match_director_result as _match_director_result,
    match_director_result_any as _match_director_result_any,
    match_director_result_mode as _match_director_result_mode,
    normalize_match_mode as _normalize_match_mode,
    result_timestamp_epoch as _result_timestamp_epoch,
    wait_for_director_result as _wait_for_director_result,
    wait_for_director_result_mode as _wait_for_director_result_mode,
)
from polaris.delivery.cli.pm.utils import compact_text, read_json_file
from polaris.domain.director.lifecycle import read as read_director_lifecycle
from polaris.kernelone.runtime.shared_types import normalize_path_list

DIRECTOR_LIFECYCLE_FILE = "DIRECTOR_LIFECYCLE.json"


def director_lifecycle_path(run_dir: str | None = None) -> str:
    """Get director lifecycle file path."""
    import os

    return os.path.join(str(run_dir or "").strip(), DIRECTOR_LIFECYCLE_FILE)


def wait_for_director_result(path: str, expected_task_id: str, since_ts: float, timeout_s: int) -> dict[str, Any]:
    """Wait for director result matching expected task."""
    return _wait_for_director_result(
        path,
        expected_task_id,
        since_ts,
        timeout_s,
        read_json_file,
        poll_interval_s=1.0,
        tolerance_s=0.0,
    )


def match_director_result(result: Any, expected_task_id: str, since_ts: float) -> dict[str, Any] | None:
    """Match director result to expected task."""
    return _match_director_result(result, expected_task_id, since_ts, tolerance_s=1.0)


def match_director_result_any(result: Any, expected_task_ids: list[str], since_ts: float) -> dict[str, Any] | None:
    """Match director result to any expected task."""
    return _match_director_result_any(result, expected_task_ids, since_ts, tolerance_s=1.0)


def result_timestamp_epoch(result: dict[str, Any]) -> float:
    """Get timestamp epoch from result."""
    return _result_timestamp_epoch(result)


def normalize_match_mode(value: Any) -> str:
    """Normalize match mode value."""
    return _normalize_match_mode(value)


def match_director_result_mode(
    result: Any,
    expected_task_ids: list[str],
    expected_run_id: str,
    since_ts: float,
    mode: str,
) -> dict[str, Any] | None:
    """Match director result with mode."""
    return _match_director_result_mode(
        result,
        expected_task_ids,
        expected_run_id,
        since_ts,
        mode,
        tolerance_s=1.0,
    )


def wait_for_director_result_mode(
    path: str,
    expected_task_ids: list[str],
    expected_run_id: str,
    since_ts: float,
    timeout_s: int,
    mode: str,
) -> dict[str, Any]:
    """Wait for director result with mode."""
    return _wait_for_director_result_mode(
        path,
        expected_task_ids,
        expected_run_id,
        since_ts,
        timeout_s,
        mode,
        read_json_file,
        poll_interval_s=1.0,
        tolerance_s=1.0,
    )


def is_director_done(result: Any) -> bool:
    """Check if director result indicates completion."""
    if not isinstance(result, dict):
        return False
    acceptance = result.get("acceptance")
    if acceptance is True:
        return True
    status = str(result.get("status") or "").strip().lower()
    return status == "success"


def build_director_fallback_result(
    *,
    task_id: str,
    task_title: str,
    run_id: str,
    error_code: str,
    reason: str,
) -> dict[str, Any]:
    """Build a fallback result when director fails."""
    now = datetime.now()
    return {
        "schema_version": 1,
        "timestamp": now.isoformat(),
        "timestamp_epoch": now.timestamp(),
        "status": "blocked",
        "acceptance": False,
        "error_code": error_code,
        "reason": reason,
        "task_id": task_id,
        "task_title": task_title,
        "run_id": run_id,
        "changed_files": [],
    }


def read_director_lifecycle_for_run(run_dir: str) -> dict[str, Any]:
    """Read director lifecycle for a run."""
    path = director_lifecycle_path(run_dir)
    payload = read_director_lifecycle(path)
    if not isinstance(payload, dict):
        return {}
    payload.setdefault("path", path)
    return payload


def classify_director_start_state(
    *,
    director_pid_seen: bool,
    lifecycle_payload: dict[str, Any],
) -> dict[str, bool]:
    """Classify director startup state."""
    startup_completed = bool((lifecycle_payload or {}).get("startup_completed"))
    execution_started = bool((lifecycle_payload or {}).get("execution_started"))
    if execution_started:
        return {
            "startup_completed": True,
            "execution_started": True,
            "director_started": True,
        }
    if startup_completed:
        return {
            "startup_completed": True,
            "execution_started": False,
            "director_started": True,
        }
    return {
        "startup_completed": False,
        "execution_started": False,
        "director_started": bool(director_pid_seen),
    }


def build_director_response(result: dict[str, Any], task_title: str) -> str:
    """Build director response text."""
    from polaris.delivery.cli.pm.utils import compact_text

    status = str(result.get("status") or "").strip().upper()
    acceptance = result.get("acceptance")
    error_code = str(result.get("error_code") or "").strip()
    summary = str(result.get("completion_summary") or "").strip()
    if not summary:
        summary = str(result.get("qa_summary") or result.get("reason") or "").strip()
    if not summary:
        summary = "已完成本次执行。"
    summary = compact_text(summary, 280)
    changed_files = normalize_path_list(result.get("changed_files") or [])
    changed_count = len(changed_files) if isinstance(changed_files, list) else 0
    qa_summary = compact_text(str(result.get("qa_summary") or "").strip(), 160)
    qa_next = compact_text(str(result.get("qa_next") or "").strip(), 160)
    reviewer = compact_text(str(result.get("reviewer_summary") or "").strip(), 160)
    acceptance_text = "PASS" if acceptance is True else "FAIL" if acceptance is False else "UNKNOWN"
    parts = []
    if task_title:
        parts.append(f"任务《{task_title}》执行结果：{acceptance_text}/{status or 'UNKNOWN'}。")
    else:
        parts.append(f"执行结果：{acceptance_text}/{status or 'UNKNOWN'}。")
    parts.append(f"摘要：{summary}")
    if changed_count:
        parts.append(f"改动文件数：{changed_count}")
    if error_code:
        parts.append(f"错误码：{error_code}")
    if reviewer:
        parts.append(f"Reviewer：{reviewer}")
    if qa_summary:
        parts.append(f"QA 摘要：{qa_summary}")
    if qa_next:
        parts.append(f"下一步建议：{qa_next}")
    return "\n".join(parts)


def build_pm_review(result: dict[str, Any], attempt: int, attempts: int, qa_enabled: bool) -> str:
    """Build PM review text."""
    acceptance = result.get("acceptance")
    status = str(result.get("status") or "").strip().lower()
    qa_summary = compact_text(str(result.get("qa_summary") or "").strip(), 160)
    if qa_enabled:
        if acceptance is True or status == "success":
            return f"收到，QA 已通过。{qa_summary or '这次任务完成得很好。'}"
        if acceptance is False or status == "fail":
            if attempt < attempts:
                return f"收到，QA 未通过。{qa_summary or '请继续修复未满足的验收项。'}"
            return f"收到，QA 未通过。{qa_summary or '先暂停该任务，后续我会调整要求。'}"
        return "收到，我会先交给 QA 进一步确认后再决定是否继续。"
    if acceptance is True or status == "success":
        return "收到，总体完成得很好。这次任务我确认通过。"
    if attempt < attempts:
        return "收到，当前仍未满足验收。请继续修复，下一轮优先解决未完成项。"
    return "收到，但仍未达成验收。先暂停这条任务，后续我会调整任务和要求。"


def emit_pm_director_conversation(
    dialogue_full: str,
    run_id: str,
    pm_iteration: int,
    result: dict[str, Any],
    expected_task_id: str,
    expected_task_title: str,
    attempt: int,
    attempts: int,
    qa_enabled: bool,
) -> tuple:
    """Emit PM-Director conversation to dialogue."""
    from polaris.kernelone.events import emit_dialogue

    pm_question = "请确认：刚刚分配的任务你完成了哪些部分？还有哪些未完成？"
    director_report = build_director_response(result, expected_task_title)
    pm_review = build_pm_review(result, attempt, attempts, qa_enabled)
    emit_dialogue(
        dialogue_full,
        speaker="PM",
        type="ask",
        text=pm_question,
        summary="PM follow-up",
        run_id=run_id,
        pm_iteration=pm_iteration,
        refs={"task_id": expected_task_id or None, "phase": "followup"},
    )
    emit_dialogue(
        dialogue_full,
        speaker="Director",
        type="report",
        text=director_report,
        summary="Director report",
        run_id=run_id,
        pm_iteration=pm_iteration,
        refs={"task_id": expected_task_id or None, "phase": "report"},
    )
    if qa_enabled:
        emit_dialogue(
            dialogue_full,
            speaker="PM",
            type="note",
            text="如果开启了 QA，将以 QA 结果作为最终确认依据。",
            summary="QA note",
            run_id=run_id,
            pm_iteration=pm_iteration,
            refs={"task_id": expected_task_id or None, "phase": "qa-note"},
        )
    emit_dialogue(
        dialogue_full,
        speaker="PM",
        type="review",
        text=pm_review,
        summary="PM review",
        run_id=run_id,
        pm_iteration=pm_iteration,
        refs={"task_id": expected_task_id or None, "phase": "review"},
    )
    return pm_question, director_report, pm_review


__all__ = [
    "build_director_fallback_result",
    "build_director_response",
    "build_pm_review",
    "classify_director_start_state",
    "emit_pm_director_conversation",
    "is_director_done",
    "match_director_result",
    "match_director_result_any",
    "match_director_result_mode",
    "normalize_match_mode",
    "read_director_lifecycle_for_run",
    "result_timestamp_epoch",
    "wait_for_director_result",
    "wait_for_director_result_mode",
]
