"""PM memo management for loop-pm."""

import os
from datetime import datetime
from typing import Any

from polaris.delivery.cli.pm.utils import compact_text
from polaris.kernelone.fs.jsonl.ops import append_jsonl
from polaris.kernelone.storage.io_paths import resolve_artifact_path
from polaris.kernelone.fs.text_ops import write_text_atomic
from polaris.kernelone.runtime.shared_types import normalize_path_list


def build_pm_memo(
    run_id: str,
    pm_iteration: int,
    attempt: int,
    attempts: int,
    expected_task_id: str,
    expected_task_title: str,
    result: dict[str, Any],
    qa_enabled: bool,
    pm_question: str,
    director_report: str,
    pm_review: str,
) -> str:
    """Build PM memo content."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    status = str(result.get("status") or "").strip().upper()
    acceptance = result.get("acceptance")
    error_code = str(result.get("error_code") or "").strip()
    completion_summary = compact_text(str(result.get("completion_summary") or "").strip(), 400)
    qa_summary = compact_text(str(result.get("qa_summary") or "").strip(), 200)
    qa_next = compact_text(str(result.get("qa_next") or "").strip(), 200)
    reviewer_summary = compact_text(str(result.get("reviewer_summary") or "").strip(), 200)
    changed_files = normalize_path_list(result.get("changed_files") or [])
    changed_count = len(changed_files) if isinstance(changed_files, list) else 0
    changed_list = ""
    if isinstance(changed_files, list) and changed_files:
        changed_list = ", ".join([str(x) for x in changed_files[:20]])
        if len(changed_files) > 20:
            changed_list += f" ... (+{len(changed_files) - 20})"
    acceptance_text = "PASS" if acceptance is True else "FAIL" if acceptance is False else "UNKNOWN"

    lines = [
        "# PM 备忘录",
        f"- 时间: {ts}",
        f"- run_id: {run_id}",
        f"- PM 轮次: {pm_iteration}",
        f"- Director 尝试: {attempt}/{attempts}",
        f"- 任务 ID: {expected_task_id}",
        f"- 任务标题: {expected_task_title}",
        f"- 结果: {acceptance_text}/{status or 'UNKNOWN'}",
        f"- 变更文件数: {changed_count}",
        f"- QA 启用: {'是' if qa_enabled else '否'}",
    ]
    if error_code:
        lines.append(f"- 错误码: {error_code}")
    if completion_summary:
        lines.append(f"- 完成摘要: {completion_summary}")
    if qa_summary:
        lines.append(f"- QA 摘要: {qa_summary}")
    if qa_next:
        lines.append(f"- QA 建议: {qa_next}")
    if reviewer_summary:
        lines.append(f"- Reviewer 摘要: {reviewer_summary}")
    if changed_list:
        lines.append(f"- 变更文件: {changed_list}")

    lines.extend(
        [
            "",
            "## PM 追问",
            pm_question,
            "",
            "## Director 汇报",
            director_report,
            "",
            "## PM 决策",
            pm_review,
        ]
    )
    return "\n".join(lines) + "\n"


def write_pm_memo(
    workspace_full: str,
    cache_root_full: str,
    run_id: str,
    attempt: int,
    content: str,
) -> tuple[str, str]:
    """Write PM memo to file."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    rel_path = os.path.join("runtime", "memos", f"PM_MEMO-{run_id}-a{attempt}-{stamp}.md")
    memo_path = resolve_artifact_path(workspace_full, cache_root_full, rel_path)
    write_text_atomic(memo_path, content)
    return memo_path, rel_path


def write_pm_memo_index(
    workspace_full: str,
    cache_root_full: str,
    record: dict[str, Any],
) -> str:
    """Write PM memo index entry."""
    rel_path = os.path.join("runtime", "memos", "index.jsonl")
    index_path = resolve_artifact_path(workspace_full, cache_root_full, rel_path)
    append_jsonl(index_path, record)
    return index_path


def write_pm_memo_summary(
    workspace_full: str,
    cache_root_full: str,
    block: str,
) -> tuple[str, str]:
    """Write PM memo summary."""
    rel_path = os.path.join("runtime", "memos", "PM_MEMO_SUMMARY.md")
    summary_path = resolve_artifact_path(workspace_full, cache_root_full, rel_path)
    from polaris.delivery.cli.pm.utils import append_text

    append_text(summary_path, block)
    export_path = os.path.join(workspace_full, "docs", "PM_MEMO_SUMMARY.md")
    append_text(export_path, block)
    return summary_path, export_path


__all__ = [
    "build_pm_memo",
    "write_pm_memo",
    "write_pm_memo_index",
    "write_pm_memo_summary",
]
