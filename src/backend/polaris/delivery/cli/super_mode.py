from __future__ import annotations

import contextlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

SUPER_ROLE = "super"

_CODE_ACTION_KEYWORDS = (
    "修复",
    "完善",
    "修改",
    "实现",
    "开发",
    "重构",
    "优化",
    "fix",
    "implement",
    "improve",
    "refactor",
    "build",
)
_CODE_TARGET_KEYWORDS = (
    "代码",
    "文件",
    "模块",
    "函数",
    "类",
    "接口",
    "bug",
    "code",
    "file",
    "module",
    "function",
    "class",
    ".py",
    ".ts",
    ".js",
)
_CODE_ARTIFACT_HINTS = (
    "orchestrator",
    "controller",
    "service",
    "runtime",
    "session",
    "pipeline",
    "adapter",
    "handler",
    "kernel",
    "cli",
)
_ARCHITECT_DELIVERY_HINTS = (
    "contextos",
    "context os",
    "context plane",
    "kernelone",
    "handoff",
    "contract",
    "descriptor",
    "semantic",
    "governance",
)
_ARCHITECT_KEYWORDS = (
    "架构",
    "蓝图",
    "设计",
    "方案",
    "adr",
    "architecture",
    "design",
)
_CHIEF_ENGINEER_KEYWORDS = (
    "根因",
    "分析",
    "审查",
    "评审",
    "排查",
    "review",
    "troubleshoot",
    "root cause",
)
_QA_KEYWORDS = (
    "测试",
    "验证",
    "回归",
    "验收",
    "qa",
    "test",
    "verify",
    "validation",
)
_PM_KEYWORDS = (
    "计划",
    "规划",
    "拆分",
    "排期",
    "roadmap",
    "plan",
)


@dataclass(frozen=True, slots=True)
class SuperRouteDecision:
    roles: tuple[str, ...]
    reason: str
    fallback_role: str
    use_architect: bool = False
    use_pm: bool = False
    use_chief_engineer: bool = False
    use_director: bool = False


@dataclass(frozen=True, slots=True)
class SuperPipelineContext:
    """Turn-local orchestration context for SUPER mode full pipeline.

    This context is NOT persisted; it only holds transient state
    for a single SUPER turn across Architect -> PM -> CE -> Director.
    """

    original_request: str
    architect_output: str = ""
    pm_output: str = ""
    extracted_tasks: tuple[SuperTaskItem, ...] = ()
    published_task_ids: tuple[int, ...] = ()
    ce_claims: tuple[SuperClaimedTask, ...] = ()
    director_claims: tuple[SuperClaimedTask, ...] = ()
    blueprint_items: tuple[SuperBlueprintItem, ...] = ()


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _has_code_artifact_hint(text: str) -> bool:
    if _contains_any(text, _CODE_TARGET_KEYWORDS):
        return True
    if _contains_any(text, _CODE_ARTIFACT_HINTS):
        return True
    return bool(re.search(r"[a-zA-Z_][\w/.-]*\.(py|ts|js|jsx|tsx|java|go|rs|cpp|c|h|yaml|yml|json|md)\b", text))


def _has_explicit_file_target(text: str) -> bool:
    return bool(re.search(r"[a-zA-Z_][\w/.-]*\.(py|ts|js|jsx|tsx|java|go|rs|cpp|c|h|yaml|yml|json|md)\b", text))


def _should_route_via_architect(text: str) -> bool:
    return _contains_any(text, _ARCHITECT_DELIVERY_HINTS)


def build_super_readonly_message(*, role: str, original_request: str) -> str:
    clean_request = str(original_request or "").strip()
    clean_role = str(role or "").strip() or "unknown"
    return (
        "[mode:analyze]\n"
        "[SUPER_MODE_READONLY_STAGE]\n"
        f"stage_role: {clean_role}\n"
        "stage_type: readonly_planning\n\n"
        "instructions:\n"
        "- This stage is read-only.\n"
        "- Do not attempt to satisfy a write contract in this stage.\n"
        "- Use only tools exposed to your current role.\n"
        "- Produce role-appropriate planning or analysis output for the next stage or the user.\n"
        "- IMPORTANT: End your response with a structured TASK_LIST in JSON format.\n\n"
        "structured_output_format:\n"
        "```json\n"
        "{\n"
        '  "tasks": [\n'
        "    {\n"
        '      "subject": "concise task title",\n'
        '      "description": "detailed implementation steps",\n'
        '      "target_files": ["path/to/file.py"],\n'
        '      "estimated_hours": 0.5\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "```\n\n"
        f"original_user_request:\n{clean_request}\n"
        "[/SUPER_MODE_READONLY_STAGE]"
    )


def _truncate_text(text: str, limit: int = 4000) -> str:
    cleaned = str(text or "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[:limit].rstrip()}..."


@dataclass(frozen=True, slots=True)
class SuperClaimedTask:
    """Claimed task payload used by SUPER-mode role handoffs."""

    task_id: str
    stage: str
    status: str
    trace_id: str
    run_id: str
    lease_token: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class SuperBlueprintItem:
    """Blueprint data extracted from Chief Engineer output."""

    task_id: str
    blueprint_id: str
    summary: str
    scope_paths: tuple[str, ...]
    guardrails: tuple[str, ...]
    no_touch_zones: tuple[str, ...]


class SuperModeRouter:
    """Deterministic intent router for CLI SUPER mode."""

    def decide(self, message: str, *, fallback_role: str) -> SuperRouteDecision:
        text = str(message or "").strip().lower()

        if _contains_any(text, _ARCHITECT_KEYWORDS):
            return SuperRouteDecision(
                roles=("architect",),
                reason="architecture_design",
                fallback_role=fallback_role,
                use_architect=True,
            )

        has_code_action = _contains_any(text, _CODE_ACTION_KEYWORDS)
        has_explicit_file_target = _has_explicit_file_target(text)
        code_delivery = has_code_action and _has_code_artifact_hint(text)
        architect_delivery = has_code_action and not has_explicit_file_target and _should_route_via_architect(text)
        if architect_delivery:
            return SuperRouteDecision(
                roles=("architect", "pm", "chief_engineer", "director"),
                reason="architect_code_delivery",
                fallback_role=fallback_role,
                use_architect=True,
                use_pm=True,
                use_chief_engineer=True,
                use_director=True,
            )
        if code_delivery:
            return SuperRouteDecision(
                roles=("architect", "pm", "chief_engineer", "director"),
                reason="code_delivery",
                fallback_role=fallback_role,
                use_architect=True,
                use_pm=True,
                use_chief_engineer=True,
                use_director=True,
            )
        if _contains_any(text, _CHIEF_ENGINEER_KEYWORDS):
            return SuperRouteDecision(
                roles=("chief_engineer",),
                reason="technical_analysis",
                fallback_role=fallback_role,
                use_chief_engineer=True,
            )
        if _contains_any(text, _QA_KEYWORDS):
            return SuperRouteDecision(
                roles=("qa",),
                reason="qa_validation",
                fallback_role=fallback_role,
            )
        if _contains_any(text, _PM_KEYWORDS):
            return SuperRouteDecision(
                roles=("pm",),
                reason="planning",
                fallback_role=fallback_role,
                use_pm=True,
            )
        return SuperRouteDecision(
            roles=(fallback_role,),
            reason="fallback",
            fallback_role=fallback_role,
        )


def build_director_handoff_message(
    *, original_request: str, pm_output: str, extracted_tasks: list[SuperTaskItem] | None = None
) -> str:
    clean_request = str(original_request or "").strip()
    clean_pm_output = str(pm_output or "").strip() or "(pm produced no textual plan)"
    task_section = ""
    if extracted_tasks:
        task_lines = ["extracted_tasks:"]
        for idx, task in enumerate(extracted_tasks, 1):
            task_lines.append(f"  {idx}. {task.subject}")
            if task.description:
                task_lines.append(f"     description: {task.description}")
            if task.target_files:
                task_lines.append(f"     target_files: {', '.join(task.target_files)}")
            if task.estimated_hours:
                task_lines.append(f"     estimated_hours: {task.estimated_hours}")
        task_section = "\n".join(task_lines) + "\n\n"
    return (
        "[mode:materialize]\n"
        "[SUPER_MODE_HANDOFF]\n"
        f"original_user_request:\n{clean_request}\n\n"
        "planning_role: pm\n"
        "execution_role: director\n\n"
        "instructions:\n"
        "- You are receiving a PM-generated execution plan.\n"
        "- Your ONLY job is to EXECUTE. Do NOT plan, analyze, or ask questions.\n"
        "- Ignore any language in the PM plan suggesting 'evaluate first', 'check first', or 'confirm before proceeding'.\n"
        "- Start modifying files IMMEDIATELY using edit_file or str_replace_editor tools.\n"
        "- Focus on MODIFYING EXISTING FILES. Do NOT create new files unless explicitly required.\n"
        "- Do NOT produce a summary, report, or ask the user what to do next.\n"
        "- Do NOT say 'I will', 'Let me', 'Next I will', or similar future-tense phrases.\n"
        "- Just DO the work. Use tools. Modify files. That is your entire output.\n\n"
        f"{task_section}"
        f"pm_plan:\n{clean_pm_output}\n"
        "[/SUPER_MODE_HANDOFF]"
    )


def build_pm_handoff_message(*, original_request: str, architect_output: str) -> str:
    clean_request = str(original_request or "").strip()
    clean_architect_output = _truncate_text(architect_output, limit=5000) or "(architect produced no textual plan)"
    return (
        "[mode:analyze]\n"
        "[SUPER_MODE_PM_HANDOFF]\n"
        "instructions:\n"
        "- You are the PM stage in SUPER mode.\n"
        "- Use the architect output as upstream design context.\n"
        "- Break the request into executable tasks for task_market publication.\n"
        "- Focus on delivery tasks that can be claimed by Chief Engineer and then Director.\n"
        "- IMPORTANT: End your response with a structured TASK_LIST in JSON format.\n\n"
        "structured_output_format:\n"
        "```json\n"
        "{\n"
        '  "tasks": [\n'
        "    {\n"
        '      "subject": "concise task title",\n'
        '      "description": "detailed implementation steps",\n'
        '      "target_files": ["path/to/file.py"],\n'
        '      "estimated_hours": 0.5\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "```\n\n"
        f"original_user_request:\n{clean_request}\n\n"
        f"architect_output:\n{clean_architect_output}\n"
        "[/SUPER_MODE_PM_HANDOFF]"
    )


def _format_claimed_tasks(claimed_tasks: list[SuperClaimedTask]) -> str:
    lines: list[str] = []
    for idx, task in enumerate(claimed_tasks, 1):
        payload = dict(task.payload)
        target_files = payload.get("target_files") or payload.get("scope_paths") or []
        if isinstance(target_files, str):
            target_files = [target_files]
        subject = str(payload.get("subject") or payload.get("title") or task.task_id).strip()
        description = str(payload.get("description") or payload.get("goal") or "").strip()
        lines.extend(
            [
                f"{idx}. task_id: {task.task_id}",
                f"   stage: {task.stage}",
                f"   subject: {subject}",
                f"   description: {description or subject}",
                f"   target_files: {', '.join(str(item).strip() for item in target_files if str(item).strip()) or '(none)'}",
            ]
        )
    return "\n".join(lines)


def build_chief_engineer_handoff_message(
    *,
    original_request: str,
    architect_output: str,
    pm_output: str,
    claimed_tasks: list[SuperClaimedTask],
) -> str:
    clean_request = str(original_request or "").strip()
    clean_architect_output = _truncate_text(architect_output, limit=3000) or "(architect produced no textual plan)"
    clean_pm_output = _truncate_text(pm_output, limit=4000) or "(pm produced no textual plan)"
    task_section = _format_claimed_tasks(claimed_tasks) or "(no claimed tasks)"
    return (
        "[mode:analyze]\n"
        "[SUPER_MODE_CE_HANDOFF]\n"
        "instructions:\n"
        "- You are receiving tasks already claimed from runtime.task_market stage pending_design.\n"
        "- Produce blueprint-level guidance, guardrails, and scope boundaries for each task.\n"
        "- Do not modify code in this stage.\n"
        "- IMPORTANT: End your response with a structured BLUEPRINT_RESULT JSON format.\n\n"
        "structured_output_format:\n"
        "```json\n"
        "{\n"
        '  "blueprints": [\n'
        "    {\n"
        '      "task_id": "task id",\n'
        '      "blueprint_id": "bp-task-id",\n'
        '      "summary": "short blueprint summary",\n'
        '      "scope_paths": ["path/to/file.py"],\n'
        '      "guardrails": ["constraint"],\n'
        '      "no_touch_zones": ["path/to/avoid.py"]\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "```\n\n"
        f"original_user_request:\n{clean_request}\n\n"
        f"architect_output:\n{clean_architect_output}\n\n"
        f"pm_output:\n{clean_pm_output}\n\n"
        f"claimed_tasks:\n{task_section}\n"
        "[/SUPER_MODE_CE_HANDOFF]"
    )


def build_director_task_handoff_message(
    *,
    original_request: str,
    architect_output: str,
    pm_output: str,
    claimed_tasks: list[SuperClaimedTask],
    blueprint_items: list[SuperBlueprintItem],
) -> str:
    clean_request = str(original_request or "").strip()
    clean_architect_output = _truncate_text(architect_output, limit=2400) or "(architect produced no textual plan)"
    clean_pm_output = _truncate_text(pm_output, limit=3200) or "(pm produced no textual plan)"
    blueprint_by_task = {item.task_id: item for item in blueprint_items}
    task_lines = ["claimed_exec_tasks:"]
    for idx, task in enumerate(claimed_tasks, 1):
        payload = dict(task.payload)
        blueprint = blueprint_by_task.get(task.task_id)
        target_files = payload.get("target_files") or payload.get("scope_paths") or ()
        if isinstance(target_files, str):
            target_files = [target_files]
        task_lines.append(f"  {idx}. {str(payload.get('subject') or payload.get('title') or task.task_id).strip()}")
        task_lines.append(f"     task_id: {task.task_id}")
        if blueprint:
            task_lines.append(f"     blueprint_id: {blueprint.blueprint_id}")
            task_lines.append(f"     blueprint_summary: {blueprint.summary}")
            if blueprint.guardrails:
                task_lines.append(f"     guardrails: {', '.join(blueprint.guardrails)}")
            if blueprint.no_touch_zones:
                task_lines.append(f"     no_touch_zones: {', '.join(blueprint.no_touch_zones)}")
        task_lines.append(
            "     target_files: "
            + (", ".join(str(item).strip() for item in target_files if str(item).strip()) or "(none)")
        )
    task_body = "\n".join(task_lines)
    return (
        "[mode:materialize]\n"
        "[SUPER_MODE_DIRECTOR_TASK_HANDOFF]\n"
        f"original_user_request:\n{clean_request}\n\n"
        "planning_role: pm\n"
        "blueprint_role: chief_engineer\n"
        "execution_role: director\n\n"
        "instructions:\n"
        "- You are receiving claimed execution tasks from runtime.task_market stage pending_exec.\n"
        "- The tasks have already passed Architect and ChiefEngineer stages.\n"
        "- Your ONLY job is to EXECUTE code modifications.\n"
        "- Start modifying files IMMEDIATELY using edit_file, write_file, or equivalent write tools.\n"
        "- Do NOT produce a summary, report, or ask the user what to do next.\n"
        "- Do NOT say 'I will', 'Let me', or similar future-tense phrases.\n"
        "- Just DO the work. Use tools. Modify files. That is your entire output.\n\n"
        f"architect_output:\n{clean_architect_output}\n\n"
        f"pm_output:\n{clean_pm_output}\n\n"
        f"{task_body}\n"
        "[/SUPER_MODE_DIRECTOR_TASK_HANDOFF]"
    )


@dataclass(frozen=True, slots=True)
class SuperTaskItem:
    """Structured task extracted from PM output."""

    subject: str
    description: str
    target_files: tuple[str, ...]
    estimated_hours: float


def _extract_tasks_from_markdown_table(text: str) -> list[SuperTaskItem]:
    """Extract tasks from markdown tables that PM commonly outputs.

    Handles tables with columns like:
    | 优先级 | 任务 ID | 标题 | 目标文件 | 描述/验收标准 |
    |--------|---------|------|----------|---------------|
    | **P0** | T-001 | 拆分 cells.yaml | docs/graph/catalog/cells.yaml | ... |
    """
    items: list[SuperTaskItem] = []
    # Find all markdown tables
    table_pattern = re.compile(r"(\|.*\|[\r\n]+\|[-\s|:]+\|[\r\n]+(?:\|.*\|[\r\n]*)+)", re.MULTILINE)
    for table_match in table_pattern.finditer(text):
        table_text = table_match.group(1)
        lines = [line.strip() for line in table_text.splitlines() if line.strip()]
        if len(lines) < 2:
            continue
        # Parse header
        header_line = lines[0]
        headers = [h.strip().lower() for h in header_line.strip("|").split("|")]
        # Map common column names to field indices
        subject_idx = _find_column_index(headers, ("标题", "任务", "任务名称", "subject", "title", "task"))
        desc_idx = _find_column_index(
            headers, ("描述", "说明", "详情", "验收标准", "description", "desc", "details", "acceptance")
        )
        files_idx = _find_column_index(
            headers, ("目标文件", "文件", "路径", "target_files", "files", "path", "target file")
        )
        hours_idx = _find_column_index(
            headers, ("预估工时", "工时", "时间", "estimated_hours", "hours", "estimate", "time")
        )
        if subject_idx is None:
            continue
        for line in lines[2:]:  # Skip header and separator
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) < 2:
                continue
            subject = _clean_markdown_cell(cells[subject_idx]) if subject_idx < len(cells) else ""
            if not subject:
                continue
            description = (
                _clean_markdown_cell(cells[desc_idx]) if desc_idx is not None and desc_idx < len(cells) else ""
            )
            target_files_str = (
                _clean_markdown_cell(cells[files_idx]) if files_idx is not None and files_idx < len(cells) else ""
            )
            target_files = (
                tuple(f.strip() for f in target_files_str.split(",") if f.strip()) if target_files_str else ()
            )
            hours_text = (
                _clean_markdown_cell(cells[hours_idx]) if hours_idx is not None and hours_idx < len(cells) else ""
            )
            estimated_hours = _parse_hours(hours_text)
            items.append(
                SuperTaskItem(
                    subject=subject,
                    description=description or subject,
                    target_files=target_files,
                    estimated_hours=estimated_hours,
                )
            )
    logger.info("extract_task_list: markdown-table parser found %d tasks", len(items))
    return items


def _find_column_index(headers: list[str], candidates: tuple[str, ...]) -> int | None:
    """Find column index, preferring exact matches over substring matches."""
    # First pass: exact match
    for idx, h in enumerate(headers):
        for cand in candidates:
            if h == cand:
                return idx
    # Second pass: substring match (lower priority)
    for idx, h in enumerate(headers):
        for cand in candidates:
            if cand in h:
                return idx
    return None


def _clean_markdown_cell(text: str) -> str:
    """Remove markdown formatting like **bold**, `code`, etc."""
    text = text.strip()
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"\*(.*?)\*", r"\1", text)
    text = re.sub(r"`(.*?)`", r"\1", text)
    text = re.sub(r"~~(.*?)~~", r"\1", text)
    return text.strip()


def _parse_hours(text: str) -> float:
    """Parse hour estimates from text like '0.5h', '2小时', '1.5'."""
    text = text.strip().lower()
    if not text:
        return 0.0
    # Remove common suffixes
    text = re.sub(r"[h小时]$", "", text).strip()
    try:
        return float(text)
    except ValueError:
        # Try to extract first number
        match = re.search(r"(\d+(?:\.\d+)?)", text)
        if match:
            return float(match.group(1))
        return 0.0


def extract_task_list_from_pm_output(pm_output: str) -> list[SuperTaskItem]:
    """Extract structured task list from PM's output.

    Tries multiple strategies in order:
    1. Fenced ```json blocks containing a 'tasks' array
    2. Inline JSON with bracket-depth counting
    3. Markdown tables (PM commonly outputs these)
    4. Fallback: any JSON object in the last 2KB

    Returns empty list if no valid task structure found.
    """
    text = str(pm_output or "").strip()
    if not text:
        return []

    # Try fenced json block first
    fenced_match = re.search(r"```json\s+(\{[\s\S]+?\})\s+```", text)
    if fenced_match:
        try:
            data = json.loads(fenced_match.group(1))
            tasks = _parse_task_data(data)
            if tasks:
                return tasks
        except json.JSONDecodeError:
            logger.debug("extract_task_list: fenced JSON parse failed")

    # Try inline JSON object with tasks key (balanced bracket matching)
    tasks_start = text.find('"tasks"')
    if tasks_start == -1:
        tasks_start = text.find("'tasks'")
    if tasks_start != -1:
        colon_idx = text.find(":", tasks_start)
        if colon_idx != -1:
            bracket_start = text.find("[", colon_idx)
            if bracket_start != -1:
                depth = 0
                bracket_end = -1
                for i, ch in enumerate(text[bracket_start:], start=bracket_start):
                    if ch == "[":
                        depth += 1
                    elif ch == "]":
                        depth -= 1
                        if depth == 0:
                            bracket_end = i + 1
                            break
                if bracket_end != -1:
                    try:
                        data = {"tasks": json.loads(text[bracket_start:bracket_end])}
                        tasks = _parse_task_data(data)
                        if tasks:
                            return tasks
                    except json.JSONDecodeError:
                        logger.debug("extract_task_list: inline JSON parse failed")

    # Strategy 3: Parse markdown tables (PM commonly outputs these instead of JSON)
    table_tasks = _extract_tasks_from_markdown_table(text)
    if table_tasks:
        return table_tasks

    # Fallback: try to find any JSON object in the last 2KB of output
    last_chunk = text[-2048:]
    brace_match = re.search(r"(\{[\s\S]*\"tasks\"[\s\S]*\})", last_chunk)
    if brace_match:
        try:
            data = json.loads(brace_match.group(1))
            tasks = _parse_task_data(data)
            if tasks:
                return tasks
        except json.JSONDecodeError:
            logger.debug("extract_task_list: fallback JSON parse failed")

    return []


def _parse_task_data(data: dict[str, Any]) -> list[SuperTaskItem]:
    """Parse task list from decoded JSON data."""
    items: list[SuperTaskItem] = []
    raw_tasks = data.get("tasks")
    if not isinstance(raw_tasks, list):
        return items
    for task in raw_tasks:
        if not isinstance(task, dict):
            continue
        subject = str(task.get("subject", "")).strip()
        if not subject:
            continue
        description = str(task.get("description", "")).strip()
        target_files = task.get("target_files", [])
        if isinstance(target_files, str):
            target_files = [target_files]
        elif not isinstance(target_files, list):
            target_files = []
        estimated = float(task.get("estimated_hours", 0.0) or 0.0)
        items.append(
            SuperTaskItem(
                subject=subject,
                description=description,
                target_files=tuple(str(f).strip() for f in target_files if str(f).strip()),
                estimated_hours=estimated,
            )
        )
    logger.info("extract_task_list: parsed %d tasks from PM output", len(items))
    return items


def extract_blueprint_items_from_ce_output(
    ce_output: str,
    *,
    claimed_tasks: list[SuperClaimedTask] | None = None,
) -> list[SuperBlueprintItem]:
    """Extract structured blueprint items from Chief Engineer output."""
    claimed = claimed_tasks or []
    text = str(ce_output or "").strip()
    if not text:
        return _fallback_blueprint_items(text, claimed)

    fenced_match = re.search(r"```json\s+(\{[\s\S]+?\})\s+```", text)
    if fenced_match:
        with contextlib.suppress(json.JSONDecodeError):
            items = _parse_blueprint_data(json.loads(fenced_match.group(1)), claimed)
            if items:
                return items

    blueprint_anchor = text.find('"blueprints"')
    if blueprint_anchor == -1:
        blueprint_anchor = text.find('"blueprint_id"')
    if blueprint_anchor != -1:
        brace_start = text.rfind("{", 0, blueprint_anchor)
        if brace_start != -1:
            depth = 0
            brace_end = -1
            for idx, ch in enumerate(text[brace_start:], start=brace_start):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        brace_end = idx + 1
                        break
            if brace_end != -1:
                with contextlib.suppress(json.JSONDecodeError):
                    items = _parse_blueprint_data(json.loads(text[brace_start:brace_end]), claimed)
                    if items:
                        return items

    return _fallback_blueprint_items(text, claimed)


def _parse_blueprint_data(data: dict[str, Any], claimed_tasks: list[SuperClaimedTask]) -> list[SuperBlueprintItem]:
    claimed_by_id = {task.task_id: task for task in claimed_tasks}
    raw_items = data.get("blueprints")
    if isinstance(raw_items, dict):
        raw_items = [raw_items]
    if raw_items is None and "blueprint_id" in data:
        raw_items = [data]
    if not isinstance(raw_items, list):
        return []

    items: list[SuperBlueprintItem] = []
    for index, raw_item in enumerate(raw_items):
        if not isinstance(raw_item, dict):
            continue
        task_id = str(raw_item.get("task_id") or "").strip()
        if not task_id and claimed_tasks:
            task_id = claimed_tasks[min(index, len(claimed_tasks) - 1)].task_id
        if not task_id:
            continue
        claimed = claimed_by_id.get(task_id)
        fallback_scope = _claim_scope_paths(claimed) if claimed else ()
        scope_paths = _normalize_string_sequence(raw_item.get("scope_paths")) or fallback_scope
        guardrails = _normalize_string_sequence(raw_item.get("guardrails"))
        no_touch_zones = _normalize_string_sequence(raw_item.get("no_touch_zones"))
        summary = str(raw_item.get("summary") or raw_item.get("description") or "").strip()
        items.append(
            SuperBlueprintItem(
                task_id=task_id,
                blueprint_id=str(raw_item.get("blueprint_id") or f"bp-{task_id}").strip(),
                summary=summary or f"Blueprint ready for task {task_id}",
                scope_paths=scope_paths,
                guardrails=guardrails,
                no_touch_zones=no_touch_zones,
            )
        )
    return items


def _fallback_blueprint_items(text: str, claimed_tasks: list[SuperClaimedTask]) -> list[SuperBlueprintItem]:
    summary = _truncate_text(text, limit=300) or "Chief Engineer blueprint stage completed."
    items: list[SuperBlueprintItem] = []
    for task in claimed_tasks:
        items.append(
            SuperBlueprintItem(
                task_id=task.task_id,
                blueprint_id=f"bp-{task.task_id}",
                summary=summary,
                scope_paths=_claim_scope_paths(task),
                guardrails=(),
                no_touch_zones=(),
            )
        )
    return items


def _claim_scope_paths(task: SuperClaimedTask | None) -> tuple[str, ...]:
    if task is None:
        return ()
    payload = dict(task.payload)
    return _normalize_string_sequence(payload.get("scope_paths") or payload.get("target_files"))


def _normalize_string_sequence(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, (list, tuple, set, frozenset)):
        values = list(value)
    else:
        return ()
    normalized: list[str] = []
    seen: set[str] = set()
    for item in values:
        token = str(item or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        normalized.append(token)
    return tuple(normalized)


__all__ = [
    "SUPER_ROLE",
    "SuperBlueprintItem",
    "SuperClaimedTask",
    "SuperModeRouter",
    "SuperPipelineContext",
    "SuperRouteDecision",
    "SuperTaskItem",
    "build_chief_engineer_handoff_message",
    "build_director_handoff_message",
    "build_director_task_handoff_message",
    "build_pm_handoff_message",
    "build_super_readonly_message",
    "extract_blueprint_items_from_ce_output",
    "extract_task_list_from_pm_output",
]
