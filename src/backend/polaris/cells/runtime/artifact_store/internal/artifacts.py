import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Path helpers: same cell (artifact_store.internal) — no cross-cell violation.
from polaris.cells.runtime.artifact_store.internal.artifact_paths import (
    resolve_artifact_path,
    select_latest_artifact,
)
from polaris.cells.runtime.state_owner.public import AppState
from polaris.domain.director.constants import (
    AGENTS_DRAFT_REL,
    AGENTS_FEEDBACK_REL,
    DEFAULT_DIALOGUE,
    DEFAULT_DIRECTOR_SUBPROCESS_LOG,
    DEFAULT_OLLAMA,
    DEFAULT_PLANNER,
    DEFAULT_PM_LOG,
    DEFAULT_PM_OUT,
    DEFAULT_PM_REPORT,
    DEFAULT_PM_SUBPROCESS_LOG,
    DEFAULT_QA,
    DEFAULT_RUNLOG,
)
from polaris.kernelone.runtime.defaults import DEFAULT_WORKSPACE
from polaris.kernelone.storage.io_paths import build_cache_root

logger = logging.getLogger("polaris.artifact_store")


def read_json(path: str) -> dict[str, Any] | None:
    if not path or not os.path.isfile(path):
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def read_file_tail(
    path: str,
    max_lines: int = 400,
    max_chars: int = 20000,
    *,
    allow_fallback: bool = True,
) -> str:
    if not path or not os.path.isfile(path):
        return ""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return ""
    lines = text.splitlines()
    if max_lines > 0 and len(lines) > max_lines:
        lines = lines[-max_lines:]
    content = "\n".join(lines)
    if max_chars > 0 and len(content) > max_chars:
        content = content[-max_chars:]
    return content


def read_file_head(path: str, max_chars: int = 20000, *, allow_fallback: bool = True) -> str:
    if not path or not os.path.isfile(path):
        return ""
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")[:max_chars]
    except (OSError, ValueError):
        return ""


def format_mtime(path: str) -> str:
    if not path or not os.path.exists(path):
        return "missing"
    try:
        ts = os.path.getmtime(path)
        return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, ValueError, OverflowError):
        return "unknown"


def get_git_status(workspace: str) -> dict[str, Any]:
    workspace_path = os.path.abspath(str(workspace or ""))
    git_path = os.path.join(workspace_path, ".git")
    present = os.path.isdir(git_path) or os.path.isfile(git_path)
    return {
        "present": present,
        "root": workspace_path if present else "",
    }


def _read_json_dict(path: str) -> dict[str, Any]:
    payload = read_json(path)
    return dict(payload) if isinstance(payload, dict) else {}


def _load_runtime_task_rows(workspace: str, cache_root: str) -> list[dict[str, Any]]:
    tasks_dir = resolve_artifact_path(workspace, cache_root, "runtime/tasks")
    if not tasks_dir or not os.path.isdir(tasks_dir):
        return []
    rows: list[dict[str, Any]] = []
    for task_file in sorted(Path(tasks_dir).glob("task_*.json")):
        if str(task_file).endswith(".session.json"):
            continue
        payload = _read_json_dict(str(task_file))
        if payload:
            rows.append(payload)
    return rows


def get_workflow_runtime_status(workspace: str, cache_root: str) -> dict[str, Any]:
    tasks = _load_runtime_task_rows(workspace, cache_root)
    pm_state = _read_json_dict(resolve_artifact_path(workspace, cache_root, "runtime/state/pm.state.json"))
    engine_state = _read_json_dict(resolve_artifact_path(workspace, cache_root, "runtime/status/engine.status.json"))

    running = bool(pm_state.get("running") or engine_state.get("running"))
    state = str(
        pm_state.get("state")
        or pm_state.get("status")
        or engine_state.get("state")
        or engine_state.get("phase")
        or ("running" if running else "idle")
    ).strip()
    stage = str(pm_state.get("workflow_stage") or pm_state.get("stage") or engine_state.get("phase") or "").strip()

    return {
        "source": "runtime.artifact_store",
        "running": running,
        "state": state,
        "stage": stage,
        "workflow_id": str(pm_state.get("workflow_id") or pm_state.get("run_id") or "").strip(),
        "workflow_status": str(
            pm_state.get("workflow_status") or state or stage or ("running" if running else "idle")
        ).strip(),
        "tasks": tasks,
        "task_count": len(tasks),
    }


def build_memory_payload(workspace: str, cache_root: str) -> dict[str, Any] | None:
    path = select_latest_artifact(workspace, cache_root, "runtime/memory/last_state.json")
    if not path:
        return None
    content = read_file_tail(path, max_lines=200, max_chars=20000)
    return {"content": content, "mtime": format_mtime(path)}


def build_success_stats_payload(workspace: str, cache_root: str) -> dict[str, Any]:
    path = select_latest_artifact(
        workspace,
        cache_root,
        "runtime/results/director.result.json",
    )
    result = read_json(path) if path else None

    # helper for compute_success_stats is not in utils?
    # I verified utils.py content, compute_success_stats was NOT in it.
    # It was in server.py around line 750. I missed it in utils.py.
    # I should add it to utils.py or defining it here.
    # It seems general enough, but only used here. Defining here is fine.
    return compute_success_stats_local(result)


def compute_success_stats_local(result: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {"successes": None, "total": None, "rate": None}
    if isinstance(result.get("results"), list):
        total = len(result["results"])
        successes = len([r for r in result["results"] if str(r.get("status") or "").lower() == "success"])
        rate = successes / total if total > 0 else None
        return {"successes": successes, "total": total, "rate": rate}
    if isinstance(result.get("successes"), (int, float)) and isinstance(result.get("total"), (int, float)):
        total_val = int(result["total"])
        successes_val = int(result["successes"])
        rate_val = result.get("success_rate")
        if not isinstance(rate_val, (int, float)) and total_val > 0:
            rate_val = successes_val / total_val
        return {"successes": successes_val, "total": total_val, "rate": rate_val}
    return {"successes": None, "total": None, "rate": None}


def _extract_goals(md_text: str) -> list[str]:
    """Extract goal items from markdown text by scanning headings and bullet lists."""
    if not md_text:
        return []
    lines = md_text.splitlines()
    goals: list[str] = []
    in_goals = False
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            heading = line.lstrip("#").strip().lower()
            if ("goal" in heading) or ("??" in heading):
                in_goals = True
                continue
            if in_goals:
                break
        if not in_goals:
            lowered = line.lower()
            if lowered.startswith("goal:") or lowered.startswith("goals:"):
                item = line.split(":", 1)[1].strip()
                if item:
                    goals.append(item)
            elif line.startswith("??"):
                parts = line.split("?", 1) if "?" in line else line.split(":", 1)
                if len(parts) > 1:
                    item = parts[1].strip()
                    if item:
                        goals.append(item)
            continue
        if line.startswith(("-", "*")):
            item = line[1:].strip()
            if item:
                goals.append(item)
            continue
        if line[0].isdigit() and "." in line:
            parts = line.split(".", 1)
            item = parts[1].strip() if len(parts) > 1 else ""
            if item:
                goals.append(item)
            continue
        if line:
            goals.append(line)
    return goals


def _load_goals(workspace: str) -> list[str]:
    """Load goals from workspace documentation files."""
    candidates = [
        os.path.join(workspace, "docs", "00_overview.md"),
        os.path.join(workspace, "docs", "product", "requirements.md"),
    ]
    for candidate in candidates:
        text = read_file_head(candidate, max_chars=20000)
        goals = _extract_goals(text)
        if goals:
            return goals
    return []


def _resolve_snapshot_paths(workspace: str, cache_root: str, state: AppState) -> dict[str, str]:
    """Resolve all artifact paths needed for the snapshot."""
    agents_draft_path = resolve_artifact_path(workspace, cache_root, AGENTS_DRAFT_REL)
    agents_feedback_path = resolve_artifact_path(workspace, cache_root, AGENTS_FEEDBACK_REL)
    return {
        "pm_out": resolve_artifact_path(workspace, cache_root, DEFAULT_PM_OUT),
        "pm_report": resolve_artifact_path(workspace, cache_root, DEFAULT_PM_REPORT),
        "pm_log": resolve_artifact_path(workspace, cache_root, state.settings.json_log_path or DEFAULT_PM_LOG),
        "pm_subprocess_log": resolve_artifact_path(workspace, cache_root, DEFAULT_PM_SUBPROCESS_LOG),
        "director_subprocess_log": resolve_artifact_path(workspace, cache_root, DEFAULT_DIRECTOR_SUBPROCESS_LOG),
        "dialogue_path": resolve_artifact_path(workspace, cache_root, DEFAULT_DIALOGUE),
        "pm_state_path": resolve_artifact_path(workspace, cache_root, "runtime/state/pm.state.json"),
        "director_state_path": resolve_artifact_path(workspace, cache_root, "runtime/memory/last_state.json"),
        "plan_path": resolve_artifact_path(workspace, cache_root, "runtime/contracts/plan.md"),
        "planner_path": resolve_artifact_path(workspace, cache_root, DEFAULT_PLANNER),
        "ollama_path": resolve_artifact_path(workspace, cache_root, DEFAULT_OLLAMA),
        "qa_path": resolve_artifact_path(workspace, cache_root, DEFAULT_QA),
        "runlog_path": resolve_artifact_path(workspace, cache_root, DEFAULT_RUNLOG),
        "agents_draft_path": agents_draft_path,
        "agents_feedback_path": agents_feedback_path,
        "agents_draft_actual": select_latest_artifact(workspace, cache_root, AGENTS_DRAFT_REL) or agents_draft_path,
        "agents_feedback_actual": select_latest_artifact(workspace, cache_root, AGENTS_FEEDBACK_REL)
        or agents_feedback_path,
        "agents_target_path": os.path.join(workspace, "AGENTS.md"),
    }


def _build_file_entries(paths: dict[str, str], workspace: str, cache_root: str) -> list:
    """Build the list of (label, path) file entries for the snapshot."""
    return [
        ("pm_tasks.contract.json", paths["pm_out"]),
        ("pm.report.md", paths["pm_report"]),
        ("pm.events.jsonl", paths["pm_log"]),
        ("pm.process.log", paths["pm_subprocess_log"]),
        ("director.process.log", paths["director_subprocess_log"]),
        ("pm.state.json", paths["pm_state_path"]),
        ("last_state.json", paths["director_state_path"]),
        (
            "pm.task_history.events.jsonl",
            resolve_artifact_path(
                workspace,
                cache_root,
                "runtime/events/pm.task_history.events.jsonl",
            ),
        ),
        ("planner.output.md", paths["planner_path"]),
        ("director_llm.output.md", paths["ollama_path"]),
        ("qa.review.md", paths["qa_path"]),
        ("director.runlog.md", paths["runlog_path"]),
        (
            "director.result.json",
            resolve_artifact_path(
                workspace,
                cache_root,
                "runtime/results/director.result.json",
            ),
        ),
        ("dialogue.transcript.jsonl", paths["dialogue_path"]),
    ]


def _build_agents_review(paths: dict[str, str]) -> dict[str, Any] | None:
    """Build the AGENTS.md review payload, or None if no review is needed."""
    has_agents = os.path.isfile(paths["agents_target_path"])
    has_draft = os.path.isfile(paths["agents_draft_actual"]) if paths["agents_draft_actual"] else False
    has_feedback = os.path.isfile(paths["agents_feedback_actual"]) if paths["agents_feedback_actual"] else False
    if not ((not has_agents) or has_draft or has_feedback):
        return None
    draft_failed = False
    if has_draft:
        preview = read_file_head(paths["agents_draft_actual"], max_chars=2000)
        lowered = preview.lower()
        draft_failed = ("generation failed" in lowered) or ("failed to write last message file" in lowered)
    return {
        "needs_review": not has_agents,
        "has_agents": has_agents,
        "draft_path": AGENTS_DRAFT_REL if has_draft else None,
        "feedback_path": AGENTS_FEEDBACK_REL if has_feedback else None,
        "draft_mtime": format_mtime(paths["agents_draft_actual"]) if has_draft else None,
        "feedback_mtime": format_mtime(paths["agents_feedback_actual"]) if has_feedback else None,
        "draft_failed": draft_failed,
    }


def _parse_iteration(value: Any) -> int | None:
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except (RuntimeError, ValueError):
            return None
    return None


def _map_workflow_runtime_phase(stage: str) -> str:
    token = str(stage or "").strip().lower()
    if not token:
        return ""
    if token.startswith("pm_"):
        if token in {"pm_completed"}:
            return "completed"
        if token in {"pm_failed"}:
            return "failed"
        return "planning"
    if token.startswith("director_"):
        if token in {"director_completed"}:
            return "verification"
        if token in {"director_deadlock", "director_task_failed"}:
            return "failed"
        return "implementation"
    if token.startswith("qa_"):
        if token in {"qa_completed"}:
            return "completed"
        if token in {"qa_skipped"}:
            return "blocked"
        return "qa_gate"
    return ""


def _workflow_director_status_label(summary: dict[str, Any]) -> str:
    state = str(summary.get("state") or "").strip().lower()
    if state == "completed":
        return "success"
    if state == "failed":
        return "failed"
    if state in {"running", "queued"}:
        return "running"
    return state


def build_snapshot(
    state: Any = None,
    *,
    workspace: str | None = None,
    cache_root: str | None = None,
) -> dict[str, Any]:
    """Build snapshot - now a thin wrapper around RuntimeProjection.

        This function maintains backward compatibility while delegating
    to RuntimeProjectionService for unified state aggregation.
    """
    # Import here to avoid circular imports at module load time.
    from polaris.cells.runtime.projection.public.service import (
        RuntimeProjectionService,
        build_snapshot_payload_from_projection,
    )

    # Get workspace
    ws = str(workspace or (state.settings.workspace if state else "") or DEFAULT_WORKSPACE).strip()
    if not ws:
        ws = DEFAULT_WORKSPACE

    # Get cache root
    cr = str(cache_root or "").strip() or build_cache_root(
        (state.settings.ramdisk_root if state else "") or "",
        ws,
    )

    # Build projection
    projection = RuntimeProjectionService.build(workspace=ws, cache_root=Path(cr), state=state)

    # Convert to snapshot payload
    return build_snapshot_payload_from_projection(
        projection=projection,
        state=state,
        workspace=ws,
        cache_root=Path(cr),
    )


def build_runtime_snapshot_v2(
    state: AppState,
    *,
    workspace: str | None = None,
    cache_root: str | None = None,
) -> dict[str, Any]:
    """构建 V2 运行时快照

    返回符合 runtime_v2 协议的状态快照，包含完整的角色、任务、Worker 信息。
    """
    from polaris.cells.runtime.projection.public.service import (
        RoleState,
        RoleType,
        RuntimeRoleState,
        RuntimeSnapshotV2,
        RuntimeSummary,
        RuntimeTaskNode,
        RuntimeWorkerState,
        TaskState,
        WorkerState,
        build_director_runtime_status,
        get_workflow_stage,
        summarize_workflow_tasks,
    )

    workspace = str(workspace or state.settings.workspace or DEFAULT_WORKSPACE).strip()
    if not workspace:
        workspace = DEFAULT_WORKSPACE
    cache_root = str(cache_root or "").strip() or build_cache_root(
        state.settings.ramdisk_root or "",
        workspace,
    )

    # 获取基本状态
    snapshot_payload = build_snapshot(state, workspace=workspace, cache_root=cache_root)
    pm_status = _build_pm_status(state)
    workflow_status = (
        pm_status.get("workflow")
        if isinstance(pm_status, dict) and isinstance(pm_status.get("workflow"), dict)
        else None
    )
    director_status = build_director_runtime_status(state, workspace, cache_root)
    workflow_summary = summarize_workflow_tasks(
        workflow_status,
        base_tasks=snapshot_payload.get("tasks") if isinstance(snapshot_payload, dict) else [],
        workspace=workspace,
        cache_root=cache_root,
    )
    if isinstance(workflow_status, dict) and not bool((director_status or {}).get("running")):
        director_status = dict(director_status or {})
        director_status["running"] = str(workflow_summary.get("state") or "").strip().lower() in {"running", "queued"}
        director_status["workers"] = (
            [
                {
                    "id": "workflow-worker",
                    "status": "running" if int(workflow_summary.get("active") or 0) > 0 else "idle",
                    "task_id": None,
                }
            ]
            if int(workflow_summary.get("total") or 0) > 0
            else []
        )
    engine_status = _build_engine_status_v2(workspace, cache_root, pm_status, director_status)

    # 确定当前阶段
    phase = _map_workflow_runtime_phase(get_workflow_stage(workflow_status))
    if not phase:
        phase = "pending"
        if engine_status:
            phase = str(engine_status.get("phase", "pending")).lower()

    # 构建角色状态
    roles = {}

    # PM 状态
    pm_state = "idle"
    if pm_status and pm_status.get("running"):
        pm_state = "analyzing" if phase in ["intake", "docs_check", "architect", "planning"] else "executing"
    if phase in ["completed", "handover"]:
        pm_state = "completed"
    elif phase in ["failed"]:
        pm_state = "failed"
    elif phase in ["blocked"]:
        pm_state = "blocked"

    roles[RoleType.PM] = RuntimeRoleState(
        role=RoleType.PM,
        state=RoleState(pm_state),
        task_id=None,
        task_title="Project Management",
        detail=pm_status.get("mode", "") if pm_status else None,
    )

    # ChiefEngineer 状态
    ce_state = "idle"
    if phase in ["architect", "planning"]:
        ce_state = "analyzing"
    elif phase in ["implementation", "verification", "qa_gate"]:
        ce_state = "executing"
    elif phase in ["completed", "handover"]:
        ce_state = "completed"
    elif phase in ["failed"]:
        ce_state = "failed"
    elif phase in ["blocked"]:
        ce_state = "blocked"

    roles[RoleType.CHIEF_ENGINEER] = RuntimeRoleState(
        role=RoleType.CHIEF_ENGINEER,
        state=RoleState(ce_state),
        task_id=None,
        task_title="Architecture & Planning",
    )

    # Director 状态
    dir_state = "idle"
    if (director_status and director_status.get("running")) or phase in ["implementation", "verification"]:
        dir_state = "executing"
    elif phase in ["completed", "handover"]:
        dir_state = "completed"
    elif phase in ["failed"]:
        dir_state = "failed"
    elif phase in ["blocked"]:
        dir_state = "blocked"

    roles[RoleType.DIRECTOR] = RuntimeRoleState(
        role=RoleType.DIRECTOR,
        state=RoleState(dir_state),
        task_id=None,
        task_title="Code Implementation",
    )

    # QA 状态
    qa_state = "idle"
    if phase in ["qa_gate"]:
        qa_state = "verification"
    elif phase in ["verification"]:
        qa_state = "executing"
    elif phase in ["completed", "handover"]:
        qa_state = "completed"
    elif phase in ["failed"]:
        qa_state = "failed"
    elif phase in ["blocked"]:
        qa_state = "blocked"

    roles[RoleType.QA] = RuntimeRoleState(
        role=RoleType.QA,
        state=RoleState(qa_state),
        task_id=None,
        task_title="Quality Assurance",
    )

    # 构建 Worker 列表
    workers = []
    if director_status and isinstance(director_status.get("workers"), list):
        for w in director_status["workers"]:
            w_state = WorkerState.IDLE
            w_status = str(w.get("status", "")).lower()
            if w_status in ["busy", "working", "running"]:
                w_state = WorkerState.IN_PROGRESS
            elif w_status in ["completed", "done", "success"]:
                w_state = WorkerState.COMPLETED
            elif w_status in ["failed", "error"]:
                w_state = WorkerState.FAILED

            workers.append(
                RuntimeWorkerState(
                    id=str(w.get("id", "")),
                    state=w_state,
                    task_id=w.get("task_id"),
                )
            )

    # 构建任务列表
    tasks = []
    payload = snapshot_payload if isinstance(snapshot_payload, dict) else {}
    pm_tasks = payload.get("tasks", []) if isinstance(payload, dict) else []

    for idx, t in enumerate(pm_tasks):
        if not isinstance(t, dict):
            continue

        t_state = TaskState.PENDING
        t_status = str(t.get("status", t.get("state", ""))).lower()
        if t_status in ["done", "completed", "success"]:
            t_state = TaskState.COMPLETED
        elif t_status in ["failed", "error"]:
            t_state = TaskState.FAILED
        elif t_status in ["in_progress", "running", "working"]:
            t_state = TaskState.IN_PROGRESS
        elif t_status in ["blocked"]:
            t_state = TaskState.BLOCKED
        elif t_status in ["ready"]:
            t_state = TaskState.READY

        # 解析 blocked_by
        blocked_by = []
        if t.get("blocked_by"):
            blocked_by = t["blocked_by"] if isinstance(t["blocked_by"], list) else [str(t["blocked_by"])]

        tasks.append(
            RuntimeTaskNode(
                id=str(t.get("id", f"task-{idx}")),
                title=str(t.get("title", t.get("summary", "Untitled Task"))),
                level=int(t.get("level", 1)),
                parent_id=t.get("parent_id"),
                state=t_state,
                blocked_by=blocked_by,
                progress=float(t.get("progress", 0)),
            )
        )

    # 构建摘要
    total = len(tasks)
    completed = sum(1 for t in tasks if t.state == TaskState.COMPLETED)
    failed = sum(1 for t in tasks if t.state == TaskState.FAILED)
    blocked = sum(1 for t in tasks if t.state == TaskState.BLOCKED)

    summary = RuntimeSummary(
        total=total,
        completed=completed,
        failed=failed,
        blocked=blocked,
    )

    # 运行 ID
    run_id = ""
    if isinstance(payload, dict):
        run_id = str(payload.get("run_id", "")).strip()

    # 构建 V2 快照
    snapshot = RuntimeSnapshotV2(
        run_id=run_id,
        phase=phase,
        roles=dict(roles.items()),
        workers=workers,
        tasks=tasks,
        summary=summary,
    )

    return snapshot.model_dump(mode="json")


def _build_pm_status(state: AppState) -> dict[str, Any]:
    """Build PM status using PMService as the authoritative source."""
    import asyncio
    import concurrent.futures

    running = False
    pid = None
    mode = ""
    started_at = None
    workflow_status = None

    # Get status from PMService (authoritative source)
    pm_status = None
    try:
        from polaris.cells.orchestration.pm_planning.public.service import PMService
        from polaris.infrastructure.di.container import get_container

        async def get_pm_service_status() -> Any | None:
            try:
                container = await get_container()
                pm_service = await container.resolve_async(PMService)
                return pm_service.get_status()
            except (RuntimeError, ValueError) as exc:
                logger.debug("PM service status query (async) failed: %s", exc)
                return None

        # Run in thread pool to avoid blocking
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            future = executor.submit(asyncio.run, get_pm_service_status())
            pm_status = future.result(timeout=10)
    except (RuntimeError, ValueError) as exc:
        logger.debug("PM service status query failed (non-critical): %s", exc)

    if pm_status and isinstance(pm_status, dict):
        running = bool(pm_status.get("running"))
        pid = pm_status.get("pid")
        mode = str(pm_status.get("mode", ""))
        started_at = pm_status.get("started_at")

    # Check workflow status for additional context
    if not running:
        from polaris.cells.runtime.projection.public.service import (
            get_workflow_runtime_status,
        )

        workspace = str(state.settings.workspace or DEFAULT_WORKSPACE)
        ramdisk_root = getattr(state.settings, "ramdisk_root", "") or ""
        if workspace and ramdisk_root:
            cache_root = build_cache_root(ramdisk_root, workspace)
            workflow_status = get_workflow_runtime_status(workspace, cache_root)

    return {
        "running": running,
        "pid": pid,
        "mode": mode or ("workflow" if workflow_status else ""),
        "started_at": started_at,
        "workflow": workflow_status,
    }


def _build_engine_status_v2(
    workspace: str,
    cache_root: str,
    pm_status: dict[str, Any] | None = None,
    director_status: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """构建引擎状态 V2"""

    path = resolve_artifact_path(workspace, cache_root, "runtime/status/engine.status.json")
    if not path or not os.path.isfile(path):
        return None

    payload = read_json(path)
    if not isinstance(payload, dict):
        return None

    running = bool(payload.get("running"))
    phase = str(payload.get("phase") or "").strip().lower()
    pm_running = bool((pm_status or {}).get("running"))
    director_running = bool((director_status or {}).get("running"))

    # 检查是否孤立运行
    stale_running = (
        running
        and phase in {"planning", "dispatching", "running", "in_progress"}
        and not pm_running
        and not director_running
    )

    if stale_running:
        payload = dict(payload)
        payload["running"] = False
        payload["phase"] = "failed"
        payload["error"] = "ENGINE_ORPHANED"

    return payload
