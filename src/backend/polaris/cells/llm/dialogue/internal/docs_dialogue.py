"""Docs Dialogue Use Case

文档对话用例，基于 Cell 公共服务实现。

✅ MIGRATION COMPLETED (2026-04-09): kernelone.llm.engine imports 已迁移到 Cell 公共服务。
"""

from __future__ import annotations

import logging
import os
import re
from typing import TYPE_CHECKING, Any

from polaris.cells.llm.provider_runtime.public.service import (
    CellAIExecutor,
    CellAIRequest,
    ResponseNormalizer,
    TaskType,
    normalize_list,
    truncate_text,
)

if TYPE_CHECKING:
    from polaris.bootstrap.config import Settings

logger = logging.getLogger(__name__)

_MAX_DIALOGUE_ITEMS = 10

_DIALOGUE_SLOTS: list[dict[str, str]] = [
    {
        "id": "delivery_form",
        "label": "交付形态",
        "question": "交付形态为何（桌面应用 / Web 应用 / API 服务 / CLI 工具）？",
    },
    {
        "id": "target_platform",
        "label": "目标平台",
        "question": "目标平台与版本约束是什么（Windows/macOS/Linux/浏览器）？",
    },
    {
        "id": "key_user_flow",
        "label": "关键链路",
        "question": "最关键的用户链路是什么（如 A -> B -> C）？",
    },
    {
        "id": "external_dependencies",
        "label": "外部依赖",
        "question": "是否依赖外部组件/命令（如 nvidia-smi）？缺失时如何降级？",
    },
    {
        "id": "acceptance_path",
        "label": "验收链路",
        "question": "验收优先链路是 API / UI / E2E / 证据占位 哪一种？",
    },
]

_DIALOGUE_SLOT_BY_ID: dict[str, dict[str, str]] = {item["id"]: item for item in _DIALOGUE_SLOTS}


def split_lines(value: str) -> list[str]:
    """分割为行列表"""
    return [line.strip() for line in str(value or "").replace("\r\n", "\n").split("\n") if line.strip()]


def _get_desktop_app_keywords() -> list[str]:
    """获取桌面应用检测关键词列表。

    返回用于识别桌面应用/HUD项目的关键词集合。
    可通过环境变量扩展或覆盖。

    Returns:
        桌面应用相关关键词列表。
    """
    # 默认关键词集合
    default_keywords = [
        "tauri",
        "tray",
        "hud",
        "desktop",
        "gpu",
        "cpu",
        "桌面",
        "托盘",
    ]

    # 允许通过环境变量扩展关键词
    extra_keywords_env = os.environ.get("KERNELONE_DESKTOP_APP_KEYWORDS", "")
    if extra_keywords_env:
        extra = [k.strip().lower() for k in extra_keywords_env.split(",") if k.strip()]
        default_keywords.extend(extra)

    return default_keywords


def _get_desktop_app_in_scope_defaults() -> list[str]:
    """获取桌面应用项目的默认范围定义。

    Returns:
        桌面应用项目默认包含的功能范围列表。
    """
    return [
        "置顶透明 HUD 展示 CPU/内存/网络/GPU",
        "托盘菜单控制显示隐藏与退出",
        "拖拽移动并限制在工作区",
        "500ms 刷新与平滑过渡",
    ]


def _get_desktop_app_constraint_defaults() -> list[str]:
    """获取桌面应用项目的默认约束定义。

    Returns:
        桌面应用项目默认约束列表。
    """
    return [
        "Windows 优先",
        "NVIDIA 指标依赖可选命令并需优雅降级",
        "窗口透明/无边框/alwaysOnTop",
    ]


def _default_dialogue_fields(fields: dict[str, str], message: str) -> dict[str, list[str]]:
    """生成默认对话字段"""
    goal_lines = split_lines(fields.get("goal") or "")
    goal = goal_lines[0] if goal_lines else (truncate_text(message, 64) or "待明确目标")
    hint_text = " ".join(split_lines(message)).lower()

    defaults = {
        "goal": [goal],
        "in_scope": ["核心功能实现", "关键用户链路可用", "基础异常与降级处理", "可验证的验收证据"],
        "out_of_scope": ["与目标无关的后台系统", "非必要重型基础设施", "超出首版范围的扩展功能"],
        "constraints": ["遵循目标平台限制", "关键依赖缺失时可优雅降级", "运行产物写入 Polaris 持久目录"],
        "definition_of_done": [
            "关键链路可运行且可演示",
            "核心指标或功能有证据产物",
            "验收链路可复现（API/UI/E2E/占位）",
        ],
        "backlog": ["初始化工程与骨架", "实现核心功能链路", "补齐验收测试与证据", "完善文档与运行说明"],
    }

    # 根据提示词调整默认值 - 使用可配置的桌面应用关键词
    desktop_app_keywords = _get_desktop_app_keywords()
    if any(token in hint_text for token in desktop_app_keywords):
        defaults["in_scope"] = _get_desktop_app_in_scope_defaults()
        defaults["constraints"] = _get_desktop_app_constraint_defaults()

    out: dict[str, list[str]] = {}
    for key in ("goal", "in_scope", "out_of_scope", "constraints", "definition_of_done", "backlog"):
        current = split_lines(fields.get(key) or "")
        out[key] = (current or defaults[key])[:_MAX_DIALOGUE_ITEMS]
    return out


def _extract_slot_answer(slot_id: str, text: str) -> str:
    """从文本中提取槽位答案"""
    body = str(text or "").strip()
    if not body:
        return ""
    low = body.lower()

    if slot_id == "delivery_form":
        if re.search(r"(tauri|electron|desktop|桌面|窗口|托盘|hud)", low):
            return "桌面应用"
        if re.search(r"(web|浏览器|react|vue|spa|前端页面)", low):
            return "Web 应用"
        if re.search(r"(api|rest|endpoint|服务|server|http)", low):
            return "API 服务"
        if re.search(r"(cli|命令行|terminal|终端|ssh)", low):
            return "CLI 工具"
        return ""

    if slot_id == "target_platform":
        platforms: list[str] = []
        if re.search(r"(windows|win11|win10)", low):
            platforms.append("Windows")
        if re.search(r"(macos|mac os|darwin)", low):
            platforms.append("macOS")
        if re.search(r"(linux|ubuntu|debian|centos)", low):
            platforms.append("Linux")
        return " / ".join(platforms) if platforms else ""

    if slot_id == "key_user_flow":
        if "->" in body or "→" in body:
            return truncate_text(body, 96)
        return ""

    if slot_id == "external_dependencies":
        deps: list[str] = []
        for token in ("nvidia-smi", "redis", "mysql", "postgres"):
            if token in low:
                deps.append(token)
        return "依赖 " + "/".join(deps) if deps else ""

    if slot_id == "acceptance_path":
        has_ui = bool(re.search(r"(ui|playwright|页面|界面|窗口|桌面|hud|tray)", low))
        has_api = bool(re.search(r"(api|contract|契约|endpoint|接口|http)", low))
        if has_ui and has_api:
            return "E2E 验收"
        if has_ui:
            return "UI 验收（Playwright）"
        if has_api:
            return "API/契约验收"
        return ""

    return ""


def build_dialogue_state(
    fields: dict[str, str],
    history: list[dict[str, Any]],
    message: str,
) -> dict[str, Any]:
    """构建对话状态"""
    user_messages = [
        str(item.get("content") or "").strip()
        for item in history
        if str(item.get("role") or "").strip().lower() == "user" and str(item.get("content") or "").strip()
    ]
    latest_message = str(message or "").strip()
    if latest_message:
        user_messages.append(latest_message)

    slot_rows: list[dict[str, Any]] = []
    answered_slot_ids: list[str] = []
    unresolved_slot_ids: list[str] = []

    for index, slot in enumerate(_DIALOGUE_SLOTS, start=1):
        slot_id = slot["id"]
        answer = ""
        source = ""

        # 从用户消息中提取
        for content in reversed(user_messages):
            candidate = _extract_slot_answer(slot_id, content)
            if candidate:
                answer = candidate
                source = "user_message"
                break

        answered = bool(answer)
        if answered:
            answered_slot_ids.append(slot_id)
        else:
            unresolved_slot_ids.append(slot_id)

        slot_rows.append(
            {
                "id": slot_id,
                "label": slot["label"],
                "question": slot["question"],
                "index": index,
                "answered": answered,
                "answer": answer,
                "source": source,
            }
        )

    return {
        "slots": slot_rows,
        "answered_slot_ids": answered_slot_ids,
        "unresolved_slot_ids": unresolved_slot_ids,
        "next_questions": [
            _DIALOGUE_SLOT_BY_ID[slot_id]["question"]
            for slot_id in unresolved_slot_ids[:3]
            if slot_id in _DIALOGUE_SLOT_BY_ID
        ],
        "phase": "ready_for_draft" if not unresolved_slot_ids else "clarifying",
    }


def build_dialogue_prompt(
    fields: dict[str, str],
    history: list[dict[str, Any]],
    message: str,
    state: dict[str, Any],
) -> str:
    """构建对话提示词"""
    normalized_fields = _default_dialogue_fields(fields, message)

    slots_desc = "\n".join(
        f"- {row['id']} ({row['label']}): {'answered' if row['answered'] else 'open'}"
        + (f" -> {row['answer']}" if row["answered"] and row["answer"] else "")
        for row in state.get("slots", [])
    )
    unresolved = state.get("unresolved_slot_ids", [])
    unresolved_text = ", ".join(unresolved) if unresolved else "(none)"

    return (
        "You are 中书令 assisting a planning council session (廷议) for software implementation.\n"
        "Return ONLY a JSON object with keys:\n"
        "reply (string), questions (string array), tiaochen (string array), fields (object), meta (object), handoffs (object).\n"
        "fields keys: goal,in_scope,out_of_scope,constraints,definition_of_done,backlog; each value must be an array of short strings.\n"
        "meta keys: phase, answered_slots, unresolved_slots.\n"
        "handoffs keys: pm, director (both string arrays for LLM-to-LLM handoff).\n"
        "Rules:\n"
        "1) Never repeat questions for answered slots.\n"
        "2) Ask at most 3 follow-up questions and only from open slots.\n"
        "3) If all slots are answered, set questions to [] and move phase to ready_for_draft.\n"
        "4) Keep outputs concrete and implementation-oriented. No markdown.\n"
        f"5) reply <= 120 chars; each array <= {_MAX_DIALOGUE_ITEMS} items.\n\n"
        "Current structured fields:\n"
        f"- goal: {normalized_fields['goal']}\n"
        f"- in_scope: {normalized_fields['in_scope']}\n"
        f"- out_of_scope: {normalized_fields['out_of_scope']}\n\n"
        "Council state:\n"
        f"{slots_desc}\n"
        f"Open slots: {unresolved_text}\n\n"
        f"Latest user message:\n{message}\n"
    )


def finalize_dialogue_payload(
    data: dict[str, Any],
    fields: dict[str, str],
    message: str,
    state: dict[str, Any],
) -> dict[str, Any]:
    """最终化对话载荷"""
    defaults = _default_dialogue_fields(fields, message)

    # 标准化 fields
    raw_fields = data.get("fields")
    fields_obj: dict[str, Any] = raw_fields if isinstance(raw_fields, dict) else {}
    normalized_fields: dict[str, list[str]] = {}
    for key in ("goal", "in_scope", "out_of_scope", "constraints", "definition_of_done", "backlog"):
        raw_field_value = fields_obj.get(key)
        normalized_fields[key] = normalize_list(
            raw_field_value if isinstance(raw_field_value, list) else defaults.get(key, [])
        )[:_MAX_DIALOGUE_ITEMS]

    unresolved_ids = state.get("unresolved_slot_ids", [])

    # 过滤问题
    questions = normalize_list(data.get("questions"))[:3]
    if not unresolved_ids:
        questions = []
    elif not questions:
        questions = state.get("next_questions", [])[:3]

    # 构建回复
    reply = str(data.get("reply") or "").strip()
    if not reply:
        reply = (
            "准奏。廷议要项已齐备，可据此拟定条陈并入卷。"
            if not unresolved_ids
            else "准奏。已收圣意，尚有关键事项待明示，请补答下列问题。"
        )

    # 构建条陈
    tiaochen = normalize_list(data.get("tiaochen"))[:_MAX_DIALOGUE_ITEMS]
    if not tiaochen:
        tiaochen = defaults.get("backlog", [])[:5]

    # 构建 meta
    meta_raw = data.get("meta")
    meta = meta_raw if isinstance(meta_raw, dict) else {}
    meta["phase"] = "ready_for_draft" if not unresolved_ids else "clarifying"
    meta["answered_slots"] = state.get("answered_slot_ids", [])
    meta["unresolved_slots"] = unresolved_ids

    # 构建 handoffs
    handoffs_raw = data.get("handoffs")
    handoffs = handoffs_raw if isinstance(handoffs_raw, dict) else {}
    handoffs.setdefault("pm", [])
    handoffs.setdefault("director", [])

    return {
        "reply": truncate_text(reply, 120),
        "questions": questions,
        "tiaochen": tiaochen,
        "fields": normalized_fields,
        "meta": meta,
        "handoffs": handoffs,
    }


def generate_dialogue_fallback(
    fields: dict[str, str],
    message: str,
    state: dict[str, Any],
) -> dict[str, Any]:
    """生成对话降级响应"""
    unresolved_ids = state.get("unresolved_slot_ids", [])
    next_questions = state.get("next_questions", [])

    if unresolved_ids:
        questions = next_questions[:3] if next_questions else ["请补充更多信息"]
        reply = "准奏。已收圣意，尚有关键事项待明示，请补答下列问题。"
    else:
        questions = []
        reply = "准奏。廷议要项已齐备，可据此拟定条陈并入卷。"

    defaults = _default_dialogue_fields(fields, message)

    return {
        "reply": reply,
        "questions": questions,
        "tiaochen": defaults.get("backlog", [])[:5],
        "fields": defaults,
        "meta": {
            "phase": "clarifying" if unresolved_ids else "ready_for_draft",
            "fallback": True,
        },
        "handoffs": {"pm": [], "director": []},
    }


async def generate_dialogue_turn(
    workspace: str,
    settings: Settings,
    fields: dict[str, str],
    history: list[dict[str, Any]],
    message: str,
) -> dict[str, Any] | None:
    """生成对话回合（非流式）"""
    executor = CellAIExecutor(workspace=workspace)

    state = build_dialogue_state(fields, history, message)
    prompt = build_dialogue_prompt(fields, history, message, state)

    request = CellAIRequest(
        task_type=TaskType.DIALOGUE,
        role="architect",
        input=prompt,
        options={"temperature": 0.1, "max_tokens": 1600},
    )

    last_output = ""

    # 尝试调用（带重试逻辑）
    for max_tokens in (1600, 2400):
        request.options["max_tokens"] = max_tokens
        response = await executor.invoke(request)

        if response.ok:
            last_output = response.output
            data = ResponseNormalizer.extract_json_object(last_output)
            if data:
                return finalize_dialogue_payload(data, fields, message, state)

        # 检查是否是截断导致的失败
        if response.ok and ResponseNormalizer.looks_truncated_json(last_output):
            continue
        break

    # 尝试修复
    if last_output and ResponseNormalizer.looks_truncated_json(last_output):
        repair_prompt = f"""Convert the following text into valid JSON only.
Required keys: reply, questions, tiaochen, fields, meta, handoffs.
fields keys: goal,in_scope,out_of_scope,constraints,definition_of_done,backlog.
Each list <= {_MAX_DIALOGUE_ITEMS} items. No markdown.

{last_output[:6000]}"""

        repair_request = CellAIRequest(
            task_type=TaskType.GENERATION,
            role="architect",
            input=repair_prompt,
            options={"temperature": 0.0, "max_tokens": 1200},
        )
        repair_response = await executor.invoke(repair_request)

        if repair_response.ok:
            data = ResponseNormalizer.extract_json_object(repair_response.output)
            if data:
                return finalize_dialogue_payload(data, fields, message, state)

    # 降级响应
    return generate_dialogue_fallback(fields, message, state)


async def generate_dialogue_turn_streaming(
    workspace: str,
    settings: Settings,
    fields: dict[str, str],
    history: list[dict[str, Any]],
    message: str,
    output_queue: Any,
) -> None:
    """生成对话回合（流式）"""

    executor = CellAIExecutor(workspace=workspace)

    state = build_dialogue_state(fields, history, message)
    prompt = build_dialogue_prompt(fields, history, message, state)

    request = CellAIRequest(
        task_type=TaskType.DIALOGUE,
        role="architect",
        input=prompt,
        options={"temperature": 0.1, "max_tokens": 1600},
    )

    collected_output = ""
    reasoning_emitted = False

    try:
        async for event in executor.invoke_stream(request):
            event_type = event.get("type")

            if event_type == "reasoning_chunk":
                reasoning_emitted = True
                await output_queue.put({"type": "reasoning_chunk", "data": {"content": event.get("reasoning", "")}})
            elif event_type == "chunk":
                chunk = event.get("chunk") or ""
                collected_output += chunk
                await output_queue.put({"type": "thinking_chunk", "data": {"content": chunk}})
            elif event_type == "complete":
                break
            elif event_type == "error":
                await output_queue.put({"type": "error", "data": {"error": event.get("error", "")}})
                return

    except (RuntimeError, ValueError) as exc:
        logger.warning("[dialogue-stream] stream error: %s", exc)
        await output_queue.put({"type": "error", "data": {"error": str(exc)}})
        return

    # 解析最终输出
    data = ResponseNormalizer.extract_json_object(collected_output)

    if not data and collected_output:
        # 尝试修复
        repair_prompt = f"Convert to JSON: {collected_output[:3000]}"
        repair_request = CellAIRequest(
            task_type=TaskType.GENERATION,
            role="architect",
            input=repair_prompt,
            options={"temperature": 0.0, "max_tokens": 1200},
        )
        repair_executor = CellAIExecutor(workspace=workspace)
        repair_response = await repair_executor.invoke(repair_request)
        if repair_response.ok:
            data = ResponseNormalizer.extract_json_object(repair_response.output)

    if data:
        result = finalize_dialogue_payload(data, fields, message, state)
    else:
        result = generate_dialogue_fallback(fields, message, state)

    # 添加 reasoning 标记
    meta = result.get("meta", {})
    if isinstance(meta, dict):
        meta["reasoning_exposed"] = reasoning_emitted
        result["meta"] = meta

    await output_queue.put({"type": "complete", "data": result})
