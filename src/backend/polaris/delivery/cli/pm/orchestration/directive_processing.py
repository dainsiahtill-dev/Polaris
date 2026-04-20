"""Directive processing utilities for orchestration."""

import logging
import re

logger = logging.getLogger(__name__)

# Keyword constants for directive processing
_META_DIRECTIVE_KEYWORDS = (
    "核心指令",
    "角色设定",
    "行为规范",
    "编码要求",
    "关键指令",
    "绝对禁令",
    "核心目标",
    "工作目录",
    "隔离规则",
    "输出格式",
    "think before you code",
    "no yapping",
    "modern standards",
    "coding agent guidance",
    "agents.md instructions",
    "how to use skills",
    "skills",
    "governance roles",
    "system prompt",
    "meta-architect",
    "提示词",
)

_PROJECT_ACTION_KEYWORDS = (
    "生成",
    "创建",
    "孵化",
    "构建",
    "搭建",
    "开发",
    "实现",
    "交付",
    "build",
    "create",
    "generate",
    "scaffold",
    "bootstrap",
    "implement",
    "deliver",
)

_PROMPT_LEAKAGE_KEYWORDS = (
    "你是",
    "you are",
    "角色设定",
    "行为规范",
    "no yapping",
    "think before you code",
    "system prompt",
    "提示词",
    "coding agent guidance",
    "agents.md instructions",
    "<instructions>",
    "</instructions>",
)

_DEFAULT_BACKLOG_ITEMS = (
    "拆解核心子系统边界并定义模块间接口契约",
    "补充验证命令、证据路径与失败回路处理策略",
    "规划迭代里程碑、风险清单与回滚触发条件",
)


def _strip_list_prefix(line: str) -> str:
    """Strip list prefix markers from line."""
    token = str(line or "").strip()
    token = re.sub(r"^(?:[-*+]|#{1,6}|\d+[.)])\s*", "", token)
    return token.strip()


def _sanitize_directive_fragment(fragment: str) -> str:
    """Sanitize directive fragment for processing."""
    token = _strip_list_prefix(fragment).strip("`").strip()
    token = re.sub(r"\s+", " ", token)
    if not token:
        return ""
    token = re.sub(
        r"^(?:核心指令|关键指令|任务目标|目标|goal|objective)\s*[:：]\s*",
        "",
        token,
        flags=re.IGNORECASE,
    )
    token = re.sub(
        r"^(?:角色设定|行为规范|编码要求|constraints?|rules?)\s*[:：]\s*",
        "",
        token,
        flags=re.IGNORECASE,
    )
    token = re.sub(
        r"^(?:你是|you are)\s+[^:：。；;]+$",
        "",
        token,
        flags=re.IGNORECASE,
    )
    return token.strip(" -:：")


def _looks_like_meta_directive_line(line: str) -> bool:
    """Check if line looks like meta directive content."""
    token = _strip_list_prefix(line)
    if not token:
        return True
    lowered = token.lower()
    if lowered.startswith(("你是 ", "you are ", "<instructions>", "</instructions>")):
        return True
    if lowered.startswith(("##", "###")):
        return True
    if any(keyword in lowered for keyword in _META_DIRECTIVE_KEYWORDS):
        return True
    return bool(token.endswith(":") and len(token) <= 24)


def _contains_prompt_leakage(text: str) -> bool:
    """Check if text contains prompt leakage markers."""
    token = str(text or "").strip().lower()
    if not token:
        return False
    if token.startswith(("你是 ", "you are ")):
        return True
    return any(marker in token for marker in _PROMPT_LEAKAGE_KEYWORDS)


def _trim_goal_clause(text: str) -> str:
    """Trim goal clause from text."""
    token = re.sub(r"\s+", " ", str(text or "")).strip(" ，,。；;:-")
    if not token:
        return ""
    separators = (
        "，先",
        ",先",
        "，并",
        ",并",
        "，要求",
        ",要求",
        "，确保",
        ",确保",
        " and then ",
        " then ",
    )
    lowered = token.lower()
    cut_index = len(token)
    for sep in separators:
        idx = lowered.find(sep.lower())
        if idx > 0:
            cut_index = min(cut_index, idx)
    token = token[:cut_index].strip(" ，,。；;:-")
    return token


def _collect_clean_lines(text: str, *, limit: int = 8) -> list[str]:
    """Collect clean actionable lines from text."""
    source = str(text or "").strip()
    if not source:
        return []
    items: list[str] = []
    seen: set[str] = set()
    for raw_line in source.splitlines():
        fragments = re.split(r"[。；;\n]", str(raw_line or ""))
        for fragment in fragments:
            candidate = _sanitize_directive_fragment(fragment)
            if not candidate:
                continue
            candidate = _trim_goal_clause(candidate)
            if not candidate:
                continue
            if _looks_like_meta_directive_line(candidate):
                continue
            if _contains_prompt_leakage(candidate):
                continue
            normalized = re.sub(r"\s+", " ", candidate).strip(" -:：，,。；;")
            if len(normalized) < 6:
                continue
            lowered = normalized.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            items.append(normalized[:220])
            if len(items) >= max(1, int(limit)):
                return items
    return items


def _extract_actionable_directive_lines(directive: str, *, limit: int = 12) -> list[str]:
    """Extract actionable lines from directive."""
    text = str(directive or "").strip()
    if not text:
        return []
    seen: set[str] = set()
    points: list[str] = []
    for raw in text.splitlines():
        fragments = re.split(r"[。；;]", str(raw or ""))
        for fragment in fragments:
            candidate = _sanitize_directive_fragment(fragment)
            if not candidate:
                continue
            if _looks_like_meta_directive_line(candidate):
                continue
            lowered = candidate.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            points.append(candidate)
            if len(points) >= max(1, int(limit)):
                break
        if len(points) >= max(1, int(limit)):
            break
    return points


def _distill_project_goal(directive: str) -> str:
    """Distill project goal from directive text."""
    actionable = _extract_actionable_directive_lines(directive, limit=24)
    candidates = [_trim_goal_clause(item) for item in actionable]
    candidates = [item for item in candidates if item and not _contains_prompt_leakage(item)]
    for line in candidates:
        lowered = line.lower()
        if any(keyword in lowered for keyword in _PROJECT_ACTION_KEYWORDS):
            return line[:220]
    if candidates:
        return candidates[0][:220]
    fallback = _collect_clean_lines(str(directive or ""), limit=1)
    return fallback[0][:220] if fallback else ""


def _extract_project_goal_from_directive(directive: str) -> str:
    """Extract project goal from directive."""
    goal = _distill_project_goal(directive)
    if goal:
        return goal[:240]
    compact = re.sub(r"\s+", " ", str(directive or "")).strip()
    compact = _trim_goal_clause(compact)
    compact = _sanitize_directive_fragment(compact)
    if _contains_prompt_leakage(compact):
        return ""
    return compact[:240]


def _build_architect_plan_from_directive(directive: str) -> str:
    """Build architect plan from directive."""
    text = str(directive or "").strip()
    if not text:
        return ""
    points = _extract_actionable_directive_lines(text, limit=12)
    if not points:
        compact = " ".join(text.split())
        if compact:
            points = [compact[:200]]
    backlog_lines = "\n".join(f"- {item}" for item in points) if points else "- TBD"
    return (
        "# Architect Plan (In-Memory)\n\n"
        "## Source\n"
        "- Generated from one-shot CLI directive in memory.\n"
        "- No directive text is persisted to workspace docs by this step.\n\n"
        "## Backlog\n"
        f"{backlog_lines}\n"
    )


def _build_backlog_from_directive(directive: str) -> str:
    """Build backlog from directive text."""
    text = str(directive or "").strip()
    if not text:
        return ""
    points = _collect_clean_lines(text, limit=12)
    goal = _distill_project_goal(text)
    normalized: list[str] = []
    seen: set[str] = set()
    for point in points:
        lowered = point.lower()
        if goal and lowered == goal.lower():
            continue
        if lowered in seen:
            continue
        normalized.append(point)
        seen.add(lowered)

    if normalized:
        if len(normalized) == 1:
            for fallback_item in _DEFAULT_BACKLOG_ITEMS:
                lowered = fallback_item.lower()
                if lowered in seen:
                    continue
                normalized.append(fallback_item)
                seen.add(lowered)
        return "\n".join(normalized[:8])

    # Fallback for extremely noisy or unstructured directives.
    fallback_points: list[str] = list(_DEFAULT_BACKLOG_ITEMS)
    compact = _trim_goal_clause(re.sub(r"\s+", " ", text))
    compact = _sanitize_directive_fragment(compact)
    if compact and not _contains_prompt_leakage(compact):
        fallback_points.insert(0, compact[:200])
    return "\n".join(fallback_points[:8])


def _sanitize_fields_for_templates(fields: dict[str, str]) -> dict[str, str]:
    """Sanitize fields for template rendering."""
    payload = {key: str(value or "").strip() for key, value in (fields or {}).items()}

    goal = _distill_project_goal(payload.get("goal", ""))
    if not goal:
        goal = _distill_project_goal(payload.get("backlog", ""))
    if not goal:
        goal = "定义可验证的项目交付目标与验收链路"

    in_scope_items = _collect_clean_lines(payload.get("in_scope", ""), limit=8)
    backlog_items = _collect_clean_lines(payload.get("backlog", ""), limit=12)

    if not in_scope_items:
        in_scope_items = backlog_items[:8]

    out_scope_items = _collect_clean_lines(payload.get("out_of_scope", ""), limit=6)
    constraints_items = _collect_clean_lines(payload.get("constraints", ""), limit=8)
    done_items = _collect_clean_lines(payload.get("definition_of_done", ""), limit=8)

    if not out_scope_items:
        out_scope_items = ["未在需求中明确要求的扩展功能"]
    if not constraints_items:
        constraints_items = [
            "所有文本文件读写必须显式使用 UTF-8",
            "顺序执行，禁止并发运行多个测试项目",
            "运行证据统一写入 Polaris runtime 目录",
        ]
    if not done_items:
        done_items = [
            "至少包含可运行入口、核心业务模块、测试文件",
            "关键验证命令执行通过并产生可追溯证据",
            "交付内容与需求目标一致且无占位符实现",
        ]

    seen_backlog: set[str] = set()
    normalized_backlog: list[str] = []
    for item in backlog_items:
        lowered = item.lower()
        if goal and lowered == goal.lower():
            continue
        if lowered in seen_backlog:
            continue
        seen_backlog.add(lowered)
        normalized_backlog.append(item)
    for fallback_item in _DEFAULT_BACKLOG_ITEMS:
        lowered = fallback_item.lower()
        if lowered in seen_backlog:
            continue
        normalized_backlog.append(fallback_item)
        seen_backlog.add(lowered)
        if len(normalized_backlog) >= 8:
            break
    if not normalized_backlog:
        normalized_backlog = list(_DEFAULT_BACKLOG_ITEMS)

    def _to_list_block(items: list[str]) -> str:
        return "\n".join(f"- {item}" for item in items if str(item).strip())

    return {
        "goal": goal,
        "in_scope": _to_list_block(in_scope_items[:8]),
        "out_of_scope": _to_list_block(out_scope_items[:6]),
        "constraints": _to_list_block(constraints_items[:8]),
        "definition_of_done": _to_list_block(done_items[:8]),
        "backlog": "\n".join(normalized_backlog[:8]),
    }


__all__ = [
    "_DEFAULT_BACKLOG_ITEMS",
    # Constants
    "_META_DIRECTIVE_KEYWORDS",
    "_PROJECT_ACTION_KEYWORDS",
    "_PROMPT_LEAKAGE_KEYWORDS",
    "_build_architect_plan_from_directive",
    "_build_backlog_from_directive",
    "_collect_clean_lines",
    "_contains_prompt_leakage",
    "_distill_project_goal",
    "_extract_actionable_directive_lines",
    "_extract_project_goal_from_directive",
    "_looks_like_meta_directive_line",
    "_sanitize_directive_fragment",
    "_sanitize_fields_for_templates",
    # Directive processing functions
    "_strip_list_prefix",
    "_trim_goal_clause",
]
