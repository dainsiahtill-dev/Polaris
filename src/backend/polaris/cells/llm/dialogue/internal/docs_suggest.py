"""Docs Suggest Use Case

文档建议用例，生成初始字段建议。

✅ MIGRATION COMPLETED (2026-04-09): kernelone.llm.engine imports 已迁移到 Cell 公共服务。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from polaris.cells.llm.provider_runtime.public.service import (
    CellAIExecutor,
    CellAIRequest,
    ResponseNormalizer,
    TaskType,
    split_lines,
    truncate_text,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from polaris.bootstrap.config import Settings

logger = logging.getLogger(__name__)

_FIELD_KEYS = (
    "goal",
    "in_scope",
    "out_of_scope",
    "constraints",
    "definition_of_done",
    "backlog",
)
_MAX_FIELD_ITEMS = 10


def build_docs_prompt(fields: dict[str, str]) -> str:
    """构建文档生成提示词"""
    goal = fields.get("goal") or ""
    in_scope = fields.get("in_scope") or ""
    out_scope = fields.get("out_of_scope") or ""
    constraints = fields.get("constraints") or ""
    definition_of_done = fields.get("definition_of_done") or ""
    backlog = fields.get("backlog") or ""

    return (
        "You are helping draft initial project documentation for PM/Director/Auditor collaboration. "
        "Return ONLY a JSON object with keys: goal, in_scope, out_of_scope, constraints, definition_of_done, backlog. "
        "Each value must be an array of concrete, implementation-ready short strings. "
        "Prefer measurable statements and include acceptance-path hints in definition_of_done when possible "
        "(API/UI/E2E/evidence-placeholder). Do not include markdown or extra text.\n\n"
        f"Goal: {goal}\n"
        f"In Scope: {in_scope}\n"
        f"Out of Scope: {out_scope}\n"
        f"Constraints: {constraints}\n"
        f"Definition of Done: {definition_of_done}\n"
        f"Backlog: {backlog}\n"
    )


def _normalize_field_items(value: Any, *, limit: int = _MAX_FIELD_ITEMS) -> list[str]:
    items: list[str] = []

    def _append(candidate: Any) -> None:
        if isinstance(candidate, str):
            fragments = split_lines(candidate)
            if fragments:
                items.extend(fragments)
                return
            text = candidate.strip()
            if text:
                items.append(text)
            return
        if isinstance(candidate, (list, tuple)):
            for item in candidate:
                _append(item)
            return
        if isinstance(candidate, dict):
            for item in candidate.values():
                _append(item)
            return
        text = str(candidate or "").strip()
        if text:
            items.append(text)

    _append(value)

    unique_items: list[str] = []
    seen: set[str] = set()
    for item in items:
        normalized = item.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique_items.append(normalized)
        if len(unique_items) >= limit:
            break
    return unique_items


def _build_default_docs_fields(fields: dict[str, str]) -> dict[str, list[str]]:
    goal_lines = _normalize_field_items(fields.get("goal"))
    goal = goal_lines[0] if goal_lines else "待明确目标"

    compact_hint = " ".join(
        item
        for key in ("goal", "constraints", "definition_of_done", "backlog")
        for item in _normalize_field_items(fields.get(key))
    ).lower()

    defaults: dict[str, list[str]] = {
        "goal": [truncate_text(goal, 96) or "待明确目标"],
        "in_scope": [
            "核心功能实现",
            "关键用户链路可用",
            "基础异常与降级处理",
            "可验证的验收证据",
        ],
        "out_of_scope": [
            "与目标无关的后台系统",
            "非必要重型基础设施",
            "超出首版范围的扩展功能",
        ],
        "constraints": [
            "遵循目标平台限制",
            "关键依赖缺失时可优雅降级",
            "运行产物写入 Polaris 持久目录",
        ],
        "definition_of_done": [
            "关键链路可运行且可演示",
            "核心指标或功能有证据产物",
            "验收链路可复现（API/UI/E2E/占位）",
        ],
        "backlog": [
            "初始化工程与骨架",
            "实现核心功能链路",
            "补齐验收测试与证据",
            "完善文档与运行说明",
        ],
    }

    if any(token in compact_hint for token in ("tauri", "tray", "hud", "desktop", "桌面", "托盘", "gpu", "cpu")):
        defaults["in_scope"] = [
            "置顶透明 HUD 展示 CPU/内存/网络/GPU",
            "托盘菜单控制显示隐藏与退出",
            "拖拽移动并限制在工作区",
            "500ms 刷新与平滑过渡",
        ]
        defaults["constraints"] = [
            "Windows 优先",
            "NVIDIA 指标依赖可选命令并需优雅降级",
            "窗口透明、无边框并保持置顶",
        ]

    normalized: dict[str, list[str]] = {}
    for key in _FIELD_KEYS:
        current = _normalize_field_items(fields.get(key))
        normalized[key] = (current or defaults[key])[:_MAX_FIELD_ITEMS]
    return normalized


def build_default_docs_fields(fields: dict[str, str]) -> dict[str, list[str]]:
    """Build deterministic docs fields when LLM enrichment is unavailable."""
    return _build_default_docs_fields(fields)


def _coerce_docs_fields(payload: Any, fields: dict[str, str]) -> dict[str, list[str]] | None:
    if not isinstance(payload, dict):
        return None

    source = payload.get("fields") if isinstance(payload.get("fields"), dict) else payload
    if not isinstance(source, dict) or not any(key in source for key in _FIELD_KEYS):
        return None

    fallback = _build_default_docs_fields(fields)
    return {
        key: (_normalize_field_items(source.get(key)) or list(fallback[key]))[:_MAX_FIELD_ITEMS] for key in _FIELD_KEYS
    }


def _build_repair_prompt(output: str) -> str:
    return (
        "Convert the following text into valid JSON only.\n"
        "Required keys: goal, in_scope, out_of_scope, constraints, definition_of_done, backlog.\n"
        "Each key must be an array of short strings.\n"
        "Do not include markdown or commentary.\n\n"
        f"{output[:6000]}"
    )


async def _repair_docs_fields(
    executor: CellAIExecutor,
    output: str,
    fields: dict[str, str],
) -> dict[str, list[str]] | None:
    if not str(output or "").strip():
        return None

    repair_request = CellAIRequest(
        task_type=TaskType.GENERATION,
        role="architect",
        input=_build_repair_prompt(output),
        options={"temperature": 0.0, "max_tokens": 1200},
    )
    repair_response = await executor.invoke(repair_request)
    if not repair_response.ok:
        return None
    return _coerce_docs_fields(
        ResponseNormalizer.extract_json_object(repair_response.output),
        fields,
    )


async def generate_docs_fields(
    workspace: str,
    settings: Settings,
    fields: dict[str, str],
) -> dict[str, list[str]] | None:
    """生成文档字段建议"""
    executor = CellAIExecutor(workspace=workspace)

    prompt = build_docs_prompt(fields)
    request = CellAIRequest(
        task_type=TaskType.GENERATION,
        role="architect",
        input=prompt,
        options={"temperature": 0.2, "max_tokens": 4096},
    )

    response = await executor.invoke(request)

    if not response.ok:
        return None

    parsed = _coerce_docs_fields(
        ResponseNormalizer.extract_json_object(response.output),
        fields,
    )
    if parsed:
        return parsed

    repaired = await _repair_docs_fields(executor, response.output, fields)
    if repaired:
        return repaired

    logger.warning("[docs-suggest] failed to parse docs fields, using fallback defaults")
    return _build_default_docs_fields(fields)


async def generate_docs_fields_stream(
    workspace: str,
    settings: Settings,
    fields: dict[str, str],
) -> AsyncGenerator[dict[str, Any], None]:
    """流式生成文档字段建议，实时返回thinking和结果

    Yields:
        {"type": "thinking", "content": str} - LLM思考内容
        {"type": "result", "fields": dict} - 最终结果
        {"type": "error", "error": str} - 错误信息
    """
    executor = CellAIExecutor(workspace=workspace)

    prompt = build_docs_prompt(fields)
    request = CellAIRequest(
        task_type=TaskType.GENERATION,
        role="architect",
        input=prompt,
        options={"temperature": 0.2, "max_tokens": 4096},
    )

    collected_output = ""
    complete_meta: dict[str, Any] = {}

    try:
        async for event in executor.invoke_stream(request):
            event_type = event.get("type")

            if event_type == "reasoning_chunk":
                # 思考内容
                content = event.get("reasoning") or ""
                if content:
                    yield {"type": "thinking", "content": content}

            elif event_type == "chunk":
                # 输出内容
                content = event.get("chunk") or ""
                if content:
                    collected_output += content

            elif event_type == "complete":
                complete_meta = dict(event.get("meta") or {}) if isinstance(event.get("meta"), dict) else {}
                meta_output = complete_meta.get("output")
                if not collected_output and isinstance(meta_output, str):
                    collected_output = meta_output
                break

            elif event_type == "error":
                yield {"type": "error", "error": event.get("error") or "流式调用失败"}
                return

        parsed = None
        structured = complete_meta.get("structured")
        if isinstance(structured, dict):
            parsed = _coerce_docs_fields(structured, fields)

        if not parsed:
            parsed = _coerce_docs_fields(
                ResponseNormalizer.extract_json_object(collected_output),
                fields,
            )

        if not parsed:
            repair_executor = CellAIExecutor(workspace=workspace)
            parsed = await _repair_docs_fields(repair_executor, collected_output, fields)

        if parsed:
            yield {"type": "result", "fields": parsed}
            return

        logger.warning("[docs-suggest] preview stream parse failed, using fallback defaults")
        yield {"type": "result", "fields": _build_default_docs_fields(fields), "fallback": True}
    except (RuntimeError, ValueError) as exc:
        yield {"type": "error", "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - provider adapters can raise transport-specific exceptions.
        logger.warning("[docs-suggest] streaming provider failed: %s", exc)
        yield {"type": "error", "error": str(exc) or type(exc).__name__}
