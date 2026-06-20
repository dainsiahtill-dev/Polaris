"""Role Dialogue Use Case - 通用角色对话套件

⚠️ 防重复造轮子提示 ⚠️
═══════════════════════════════════════════════════════════════════

这是 llm.dialogue 兼容 facade 的角色对话适配层。生产角色调用必须进入
roles.runtime；本模块不得作为新的生产入口扩散。

如果你需要：
  1. 添加新角色 → 在 ROLE_PROMPT_TEMPLATES 中增加条目
  2. 修改现有角色 → 直接修改对应角色的模板字符串
  3. 给角色添加工具能力 → 复用 llm_toolkit.ROLE_TOOL_INTEGRATIONS

禁止行为：
  ✗ 新建 pm_dialogue.py / architect_dialogue.py 等单独文件
  ✗ 在 role_agent/ 下内嵌角色提示词
  ✗ 创建新的 "generate_xxx_response" 函数或从生产入口直接调用 role dialogue 兼容 facade

相关文件：
  - 工具系统: core/llm_toolkit/
  - 角色集成: core/llm_toolkit/integrations.py
  - 生产入口: polaris.cells.roles.runtime.public.service

提示词设计原则：
1. 结构化输出：要求LLM输出特定格式的结构化数据
2. 质量控制：内置自检机制，要求LLM验证输出
3. 安全边界：明确禁止的行为和输出
4. Few-shot示例：提供参考示例指导LLM
5. 渐进式细化：复杂任务分解为多个步骤
═══════════════════════════════════════════════════════════════════
"""

import asyncio
import json
import logging
import os
import re
from typing import Any

# ACGA-compliant: Import from public facade instead of internal module
from polaris.cells.roles.kernel.public.prompt_templates_facade import (
    ROLE_PROMPT_TEMPLATES,
    SHARED_SECURITY_BOUNDARY,
)
from polaris.kernelone.quality.role_output_markers import DEBT_MARKERS
from polaris.kernelone.security.dangerous_patterns import (
    is_dangerous_command,
    is_path_traversal,
)

# 工具系统集成 - 延迟加载以避免循环导入
_ROLE_MODEL_LOOKUP_AVAILABLE = False


logger = logging.getLogger(__name__)

_DEFAULT_ROLE_TOOL_ROUNDS = 4
_ROLE_TOOL_ROUND_OVERRIDES = {
    "pm": 2,
    "architect": 3,
    "chief_engineer": 3,
    "director": 4,
    "qa": 3,
}


def _resolve_role_tool_rounds(role: str) -> int:
    normalized_role = str(role or "").strip().lower()
    role_default = _ROLE_TOOL_ROUND_OVERRIDES.get(normalized_role, _DEFAULT_ROLE_TOOL_ROUNDS)
    role_env_key = f"KERNELONE_ROLE_TOOL_ROUNDS_{normalized_role.upper()}" if normalized_role else ""
    raw_value = ""
    if role_env_key:
        raw_value = str(os.environ.get(role_env_key, "")).strip()
    if not raw_value:
        raw_value = str(os.environ.get("KERNELONE_ROLE_TOOL_ROUNDS", str(role_default))).strip()
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError):
        return role_default
    return max(1, min(parsed, 8))


def _normalize_tool_results(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            normalized.append(dict(item))
    return normalized


def _coerce_runtime_history(value: Any) -> tuple[tuple[str, str], ...]:
    if value is None or isinstance(value, str | bytes):
        return ()
    try:
        iterator = iter(value)
    except TypeError:
        return ()

    normalized: list[tuple[str, str]] = []
    for item in iterator:
        role = ""
        content = ""
        if isinstance(item, dict):
            role = str(item.get("role") or "").strip()
            content = str(item.get("content") or item.get("message") or "").strip()
        elif isinstance(item, (tuple, list)) and len(item) >= 2:
            role = str(item[0] or "").strip()
            content = str(item[1] or "").strip()
        if role and content:
            normalized.append((role, content))
    return tuple(normalized)


def _resolve_runtime_identifier(context: dict[str, Any], metadata: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(context.get(key) or "").strip()
        if value:
            return value
        value = str(metadata.get(key) or "").strip()
        if value:
            return value
    return ""


def _resolve_role_provider_model(role: str, workspace: str = ".") -> tuple[str, str]:
    """通过 Cell 公共服务解析角色的 provider 和 model.

    Args:
        role: 角色标识
        workspace: 工作区路径

    Returns:
        (provider_id, model_name) 元组
    """
    try:
        # 懒加载以避免循环导入
        from polaris.cells.llm.provider_config.public import (
            resolve_provider_request_context,
        )

        context = resolve_provider_request_context(
            workspace=workspace,
            cache_root="",
            provider_id=role,
            api_key=None,
            headers=None,
        )
        return (
            str(context.provider_cfg.get("provider_id") or context.provider_type or "unknown"),
            str(context.provider_cfg.get("model") or "unknown"),
        )
    except (RuntimeError, ValueError) as exc:
        logger.debug("_resolve_role_provider_model failed for %s: %s", role, exc)
        return ("unknown", "unknown")


def _has_pending_tool_turn(result: Any) -> bool:
    tool_calls = getattr(result, "tool_calls", None)
    is_complete = bool(getattr(result, "is_complete", True))
    return isinstance(tool_calls, list) and bool(tool_calls) and not is_complete


def _build_tool_feedback(tool_results: list[dict[str, Any]]) -> str:
    if not tool_results:
        return "- 无工具结果"
    lines: list[str] = []
    for index, item in enumerate(tool_results[:10], start=1):
        tool_name = str(item.get("tool") or item.get("name") or "unknown").strip() or "unknown"
        success = bool(item.get("success", False))
        line = f"{index}. {tool_name}: {'success' if success else 'failed'}"
        error_text = str(item.get("error") or "").strip()
        result_value = item.get("result")
        if error_text:
            line += f"; error={error_text[:180]}"
        elif isinstance(result_value, dict):
            keys = [str(k) for k in list(result_value.keys())[:4]]
            if keys:
                line += f"; result_keys={','.join(keys)}"
        elif result_value is not None:
            line += f"; result={str(result_value)[:180]}"
        lines.append(line)
    return "\n".join(lines)


def _collect_missing_read_paths(tool_results: list[dict[str, Any]]) -> list[str]:
    missing_paths: list[str] = []
    for item in tool_results:
        if not isinstance(item, dict):
            continue
        tool_name = str(item.get("tool") or item.get("name") or "").strip().lower()
        if tool_name != "read_file":
            continue
        if bool(item.get("success", False)):
            continue
        error_text = str(item.get("error") or "").strip()
        if "file not found" not in error_text.lower():
            continue
        path_match = re.search(
            r"file not found:\s*(.+)$",
            error_text,
            flags=re.IGNORECASE,
        )
        path_value = ""
        if path_match:
            path_value = str(path_match.group(1) or "").strip()
        if not path_value:
            args = item.get("args")
            if isinstance(args, dict):
                path_value = str(args.get("file") or args.get("path") or "").strip()
        if path_value:
            missing_paths.append(path_value)
    deduped: list[str] = []
    seen: set[str] = set()
    for value in missing_paths:
        token = str(value).strip()
        if not token:
            continue
        lowered = token.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        deduped.append(token)
    return deduped[:6]


def _normalize_tool_call_signatures(result: Any) -> tuple[str, ...]:
    tool_calls = getattr(result, "tool_calls", None)
    if not isinstance(tool_calls, list):
        return ()
    normalized: list[str] = []
    for item in tool_calls:
        if not isinstance(item, dict):
            continue
        name = str(item.get("tool") or item.get("name") or "").strip().lower()
        args = item.get("args") or item.get("arguments") or {}
        if not name or not isinstance(args, dict):
            continue
        try:
            payload = json.dumps(args, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except TypeError:
            payload = str(args)
        normalized.append(f"{name}:{payload}")
    normalized.sort()
    return tuple(normalized)


def _is_redundant_exploration_loop(signatures: tuple[str, ...]) -> bool:
    if not signatures:
        return False
    exploration_tools = {"glob", "list_directory", "file_exists", "read_file", "search_code", "grep", "ripgrep"}
    for signature in signatures:
        tool_name = signature.split(":", 1)[0].strip().lower()
        if tool_name not in exploration_tools:
            return False
    return True


def _execute_pending_tool_calls_via_orchestrator(
    workspace: str,
    pending_tool_calls: Any,
    *,
    max_tool_calls: int,
) -> list[dict[str, Any]]:
    if not isinstance(pending_tool_calls, list) or not pending_tool_calls:
        return []

    normalized_pending: list[dict[str, Any]] = []
    allowed_tools: list[str] = []
    for item in pending_tool_calls:
        if not isinstance(item, dict):
            continue
        name = str(item.get("tool") or item.get("name") or "").strip().lower()
        if not name:
            continue
        args = item.get("args")
        if not isinstance(args, dict):
            args = item.get("arguments")
        if not isinstance(args, dict):
            args = {}
        normalized_pending.append(
            {
                "id": str(item.get("id") or item.get("tool_call_id") or ""),
                "tool": name,
                "args": dict(args),
            }
        )
        if name not in allowed_tools:
            allowed_tools.append(name)

    if not normalized_pending:
        return []

    try:
        from polaris.cells.llm.tool_runtime.public.service import RoleToolRoundOrchestrator

        orchestrator = RoleToolRoundOrchestrator()
        round_result = orchestrator.run_round(
            workspace=workspace,
            preparsed_tool_calls=normalized_pending,
            allowed_tools=allowed_tools,
            max_tool_calls=max(1, min(int(max_tool_calls or 0), 32)),
            fail_fast=False,
        )
        return _normalize_tool_results(round_result.tool_results)
    except (RuntimeError, ValueError) as exc:
        logger.debug("fallback tool round via orchestrator failed: %s", exc)
        return []


# 角色输出质量分数阈值，可通过环境变量配置
# 低于此分数的输出将被视为不成功
try:
    _ROLE_QUALITY_SCORE_THRESHOLD = int(os.environ.get("KERNELONE_ROLE_QUALITY_THRESHOLD", "60"))
except (TypeError, ValueError):
    _ROLE_QUALITY_SCORE_THRESHOLD = 60
if not 0 <= _ROLE_QUALITY_SCORE_THRESHOLD <= 100:
    logger.warning(f"Invalid KERNELONE_ROLE_QUALITY_THRESHOLD value: {_ROLE_QUALITY_SCORE_THRESHOLD}, using default 60")
    _ROLE_QUALITY_SCORE_THRESHOLD = 60


SECURITY_BOUNDARY = SHARED_SECURITY_BOUNDARY
OUTPUT_FORMAT_GUIDE = (
    "【运行时契约】\n工具调用和结构化输出由运行时/API 管理；默认不要在可见文本中输出自定义工具包装器或格式样板。"
)


async def generate_role_response(
    workspace: str,
    settings: Any,
    role: str,
    message: str,
    context: dict[str, Any] | None = None,
    validate_output: bool = True,
    max_retries: int = 1,
    prompt_appendix: str | None = None,
    enable_cognitive: bool | None = None,
) -> dict[str, Any]:
    """生成角色回复（非流式）。

    兼容 facade：实际执行必须进入 RoleRuntimeService。

    Args:
        workspace: 工作区路径
        settings: 应用设置
        role: 角色标识 (pm, architect, director, qa, 等)
        message: 用户消息
        context: 可选的上下文信息
        validate_output: 是否验证输出格式
        max_retries: 验证失败时重试次数
        prompt_appendix: 追加提示词（仅追加，不覆盖核心提示词）
        enable_cognitive: 兼容参数；实际认知运行时由 RoleRuntimeService 统一控制

    Returns:
        {
            "response": str,
            "thinking": str | None,
            "role": str,
            "model": str,
            "provider": str,
            "profile_version": str,
            "prompt_fingerprint": str,
            "tool_policy_id": str,
            "validation": {
                "success": bool,
                "data": dict | None,
                "errors": [str],
                "quality_score": float,
                "suggestions": [str],
            }
        }
    """
    _ = settings, enable_cognitive
    return await _generate_with_role_runtime(
        workspace=workspace,
        role=role,
        message=message,
        context=context,
        prompt_appendix=prompt_appendix,
        validate_output=validate_output,
        max_retries=max_retries,
    )


async def _generate_with_kernel(
    workspace: str,
    settings: Any,
    role: str,
    message: str,
    context: dict[str, Any] | None = None,
    prompt_appendix: str | None = None,
    validate_output: bool = True,
    max_retries: int = 1,
) -> dict[str, Any]:
    """Compatibility wrapper for old imports; delegates to RoleRuntimeService."""
    _ = settings
    return await _generate_with_role_runtime(
        workspace=workspace,
        role=role,
        message=message,
        context=context,
        prompt_appendix=prompt_appendix,
        validate_output=validate_output,
        max_retries=max_retries,
    )


async def _generate_with_role_runtime(
    workspace: str,
    role: str,
    message: str,
    context: dict[str, Any] | None = None,
    prompt_appendix: str | None = None,
    validate_output: bool = True,
    max_retries: int = 1,
) -> dict[str, Any]:
    """Use RoleRuntimeService for role dialogue compatibility calls."""
    from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
    from polaris.cells.roles.runtime.public.service import RoleRuntimeService

    workspace_token = str(workspace or "").strip()
    role_token = str(role or "").strip()
    message_text = str(message or "")
    if not workspace_token:
        raise ValueError("workspace must be a non-empty string")
    if not role_token:
        raise ValueError("role must be a non-empty string")
    if not message_text.strip():
        raise ValueError("message must be a non-empty string")

    context_payload = dict(context or {})
    metadata = dict(context_payload.get("metadata") or {})
    metadata.update(
        {
            "source": "llm.dialogue.internal.role_dialogue",
            "role_runtime_required": True,
            "cognitive_runtime_required": True,
            "context_os_expected": True,
            "runtime_fallback_used": False,
            "fallback_policy": "fail_closed",
            "llm_dialogue_internal_compat": True,
            "validate_output": bool(validate_output),
            "max_retries": max(0, int(max_retries)),
        }
    )
    if prompt_appendix:
        metadata["prompt_appendix"] = str(prompt_appendix)

    run_id = _resolve_runtime_identifier(context_payload, metadata, "run_id", "factory_run_id")
    task_id = _resolve_runtime_identifier(context_payload, metadata, "task_id")
    session_id = _resolve_runtime_identifier(context_payload, metadata, "session_id", "runtime_session_id")
    if not session_id:
        session_basis = run_id or task_id or str(abs(hash((workspace_token, role_token, message_text))) & 0xFFFFFFFF)
        session_id = f"{role_token}-dialogue-{session_basis}"

    result = await RoleRuntimeService().execute_role_session(
        ExecuteRoleSessionCommandV1(
            role=role_token,
            session_id=session_id,
            workspace=workspace_token,
            user_message=message_text,
            run_id=run_id or None,
            task_id=task_id or None,
            domain=str(context_payload.get("domain") or metadata.get("domain") or "general").strip() or "general",
            history=_coerce_runtime_history(context_payload.get("history")),
            context=context_payload,
            metadata=metadata,
            stream=False,
            host_kind="llm_dialogue_internal_compat",
        )
    )

    result_metadata = dict(getattr(result, "metadata", {}) or {})
    result_metadata.update(
        {
            "role_runtime_entrypoint": "roles.runtime.execute_role_session",
            "role_runtime_session_id": session_id,
            "role_runtime_required": True,
            "cognitive_runtime_required": True,
            "context_os_expected": True,
            "runtime_fallback_used": False,
            "fallback_policy": "fail_closed",
            "llm_dialogue_internal_compat": True,
        }
    )
    execution_stats = dict(getattr(result, "usage", {}) or {})
    provider_id, model_name = _resolve_role_provider_model(role_token, workspace_token)
    provider_id = str(result_metadata.get("provider_id") or execution_stats.get("provider_id") or provider_id)
    model_name = str(result_metadata.get("model") or execution_stats.get("model") or model_name)
    error_message = str(getattr(result, "error_message", "") or getattr(result, "error_code", "") or "").strip()
    response_text = str(getattr(result, "output", "") or "").strip()
    if not response_text and error_message:
        response_text = f"[ROLE_EXECUTION_ERROR] {error_message}"

    response: dict[str, Any] = {
        "response": response_text,
        "thinking": getattr(result, "thinking", None),
        "role": role_token,
        "model": model_name,
        "provider": provider_id,
        "profile_version": str(
            result_metadata.get("profile_version") or result_metadata.get("role_profile_version") or ""
        ),
        "prompt_fingerprint": result_metadata.get("prompt_fingerprint"),
        "tool_policy_id": str(result_metadata.get("tool_policy_id") or ""),
        "metadata": result_metadata,
        "execution_stats": execution_stats,
        "artifacts": list(getattr(result, "artifacts", ()) or ()),
    }
    if validate_output:
        response["validation"] = validate_and_parse_role_output(role_token, response_text)
    if error_message:
        response["error"] = error_message

    tool_calls = list(getattr(result, "tool_calls", ()) or ())
    if tool_calls:
        response["tool_calls"] = tool_calls
        response["tool_results"] = tool_calls

    return response


async def generate_role_response_streaming(
    workspace: str,
    settings: Any,
    role: str,
    message: str,
    output_queue: asyncio.Queue,
    context: dict[str, Any] | None = None,
    prompt_appendix: str | None = None,
    session_id: str | None = None,
    history: list[tuple[str, str]] | tuple[tuple[str, str], ...] | None = None,
) -> None:
    """生成角色回复（流式）

    使用 roles.runtime facade 统一内核执行。

    Args:
        workspace: 工作区路径
        settings: 应用设置
        role: 角色标识
        message: 用户消息
        output_queue: 输出队列，用于发送事件
        context: 可选的上下文信息
        prompt_appendix: 追加提示词（仅追加，不覆盖核心提示词）
        session_id: 可选的角色会话 ID；缺省时创建新的 ad-hoc session
        history: 可选的历史消息 [(role, content), ...]
    """
    from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
    from polaris.cells.roles.runtime.public.service import (
        stream_role_session_command,
    )
    from polaris.cells.roles.session.public import RoleSessionService
    from polaris.cells.roles.session.public.service import RoleHostKind

    final_appendix = prompt_appendix or ""
    runtime_session_id = str(session_id or "").strip()
    if not runtime_session_id:
        ad_hoc_session = RoleSessionService.create_ad_hoc_session(
            role=role,
            workspace=workspace,
            host_kind=RoleHostKind.API_SERVER.value,
        )
        runtime_session_id = str(ad_hoc_session.id)

    metadata: dict[str, Any] = {}
    if final_appendix:
        metadata["prompt_appendix"] = final_appendix

    try:
        # 确保 history 是 tuple 类型（ExecuteRoleSessionCommandV1 要求）
        history_tuple: tuple[tuple[str, str], ...] = tuple(history) if history else ()
        command = ExecuteRoleSessionCommandV1(
            role=role,
            session_id=runtime_session_id,
            workspace=workspace,
            user_message=message,
            history=history_tuple,
            context=context or {},
            metadata=metadata,
            stream=True,
        )

        async for event in stream_role_session_command(command):
            event_type = event.get("type")

            if event_type == "thinking_chunk":
                await output_queue.put(
                    {
                        "type": "thinking_chunk",
                        "data": {"content": event.get("content", "")},
                    }
                )

            elif event_type == "content_chunk":
                await output_queue.put(
                    {
                        "type": "content_chunk",
                        "data": {"content": event.get("content", "")},
                    }
                )

            elif event_type == "tool_call":
                await output_queue.put(
                    {
                        "type": "tool_call",
                        "data": {"tool": event.get("tool"), "args": event.get("args")},
                    }
                )

            elif event_type == "tool_result":
                await output_queue.put(
                    {
                        "type": "tool_result",
                        "data": event.get("result", {}),
                    }
                )

            elif event_type == "fingerprint":
                fingerprint = event.get("fingerprint")
                if fingerprint:
                    await output_queue.put(
                        {
                            "type": "fingerprint",
                            "data": {"fingerprint": fingerprint.full_hash},
                        }
                    )

            elif event_type == "complete":
                result = event.get("result")
                if result:
                    await output_queue.put(
                        {
                            "type": "complete",
                            "data": {
                                "content": result.content,
                                "thinking": result.thinking,
                                "profile_version": result.profile_version,
                                "tool_policy_id": result.tool_policy_id,
                            },
                        }
                    )

            elif event_type == "error":
                await output_queue.put(
                    {
                        "type": "error",
                        "data": {"error": event.get("error", "未知错误")},
                    }
                )
                return

    finally:
        # 发送结束标记
        await output_queue.put({"type": "complete"})


def _build_role_prompt(
    role: str,
    message: str,
    context: dict[str, Any] | None,
    system_prompt: str | None = None,
) -> str:
    """构建角色对话的prompt"""

    # 使用自定义系统提示词或角色模板
    if system_prompt:
        base_prompt = system_prompt
    else:
        base_prompt = ROLE_PROMPT_TEMPLATES.get(role, f"你是 {role.upper()} 角色，请专业地回答用户问题。")
        base_prompt = "\n\n".join(
            part for part in (base_prompt, SECURITY_BOUNDARY, OUTPUT_FORMAT_GUIDE) if str(part or "").strip()
        )

    # 添加上下文信息
    context_parts = []
    history_lines = []

    if context:
        workspace = context.get("workspace", "")
        task_count = context.get("task_count", 0)

        if workspace:
            context_parts.append(f"工作区: {workspace}")
        if task_count:
            context_parts.append(f"任务数: {task_count}")

        # 添加其他上下文字段（排除 history 和特殊字段）
        for key, value in context.items():
            if key not in ("workspace", "task_count", "history") and value is not None:
                context_parts.append(f"{key}: {value}")

        # 处理对话历史
        history = context.get("history")
        if isinstance(history, list) and history:
            for turn in history:
                if isinstance(turn, dict):
                    turn_role = turn.get("role", "")
                    turn_content = turn.get("content", "")
                    if turn_role and turn_content:
                        role_label = "用户" if turn_role == "user" else "助手"
                        history_lines.append(f"{role_label}: {turn_content}")

    if context_parts:
        base_prompt += "\n\n当前上下文：\n" + "\n".join(f"- {p}" for p in context_parts)

    # 构建完整 prompt
    full_prompt = base_prompt

    # 添加对话历史
    if history_lines:
        full_prompt += "\n\n=== 对话历史 ===\n" + "\n".join(history_lines)

    # 添加当前用户消息
    full_prompt += f"\n\n用户: {message}\n\n请回复："

    return full_prompt


def register_role_template(role: str, template: str) -> None:
    """注册新的角色提示词模板

    Args:
        role: 角色标识
        template: 系统提示词模板
    """
    ROLE_PROMPT_TEMPLATES[role] = template


def get_registered_roles() -> list[str]:
    """获取所有已注册的角色列表"""
    return list(ROLE_PROMPT_TEMPLATES.keys())


# ============================================================================
# 输出解析与验证
# ============================================================================


class RoleOutputParser:
    """角色输出解析器

    负责从LLM原始输出中提取结构化数据。
    """

    # 角色对应的JSON Schema
    ROLE_SCHEMAS: dict[str, dict] = {
        "pm": {
            "type": "object",
            "required": ["tasks"],
            "properties": {
                "tasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["id", "title", "description"],
                        "properties": {
                            "id": {"type": "string"},
                            "title": {"type": "string"},
                            "description": {"type": "string"},
                            "target_files": {"type": "array", "items": {"type": "string"}},
                            "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
                            "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                            "phase": {"type": "string"},
                            "estimated_effort": {"type": "number"},
                            "dependencies": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
                "analysis": {"type": "object"},
            },
        },
        "chief_engineer": {
            "type": "object",
            "required": ["blueprint_version", "task_id", "construction_plan"],
            "properties": {
                "blueprint_version": {"type": "string"},
                "blueprint_id": {"type": "string"},
                "task_id": {"type": "string"},
                "doc_id": {"type": "string"},
                "analysis": {"type": "object"},
                "construction_plan": {"type": "object"},
                "scope_for_apply": {"type": "array", "items": {"type": "string"}},
                "dependencies": {"type": "object"},
                "constraints": {"type": "array", "items": {"type": "string"}},
                "risk_flags": {"type": "array"},
            },
        },
        "qa": {
            "type": "object",
            "required": ["review_id", "verdict"],
            "properties": {
                "review_id": {"type": "string"},
                "verdict": {"type": "string", "enum": ["PASS", "CONDITIONAL", "FAIL", "BLOCKED"]},
                "confidence": {"type": "string"},
                "summary": {"type": "string"},
                "findings": {"type": "array"},
                "metrics": {"type": "object"},
                "checklist_results": {"type": "object"},
                "risks": {"type": "array"},
                "recommendations": {"type": "array", "items": {"type": "string"}},
            },
        },
        "architect": {
            "type": "object",
            "required": ["project_overview", "architecture_design"],
            "properties": {
                "project_overview": {"type": "string"},
                "architecture_design": {"type": "string"},
                "technology_stack": {"type": "array"},
                "module_design": {"type": "array"},
                "risks": {"type": "array"},
            },
        },
        "director": {
            "type": "object",
            "required": ["execution_plan", "file_changes"],
            "properties": {
                "execution_plan": {"type": "array"},
                "file_changes": {"type": "array"},
                "verification": {"type": "array"},
                "status": {"type": "string"},
                "summary": {"type": "string"},
            },
        },
    }

    @classmethod
    def extract_json(cls, text: str) -> tuple[dict | None, list[str]]:
        """从文本中提取JSON对象

        Returns:
            (parsed_json, errors)
        """
        errors = []

        if not text or not text.strip():
            return None, ["Empty text"]

        text = text.strip()

        # 尝试直接解析
        try:
            return json.loads(text), []
        except json.JSONDecodeError:
            pass

        # 尝试从代码块中提取
        patterns = [
            r"```json\s*(.*?)\s*```",
            r"```\s*(.*?)\s*```",
        ]

        for pattern in patterns:
            matches = re.findall(pattern, text, re.DOTALL)
            for match in matches:
                try:
                    return json.loads(match), []
                except json.JSONDecodeError as e:
                    errors.append(f"JSON parse error in code block: {e}")
                    continue

        # 尝试从 output 标签中提取
        output_match = re.search(r"<output>(.*?)\u003c/output>", text, re.DOTALL)
        if output_match:
            content = output_match.group(1).strip()
            try:
                return json.loads(content), []
            except json.JSONDecodeError:
                # 可能output标签内还有代码块
                for pattern in patterns:
                    matches = re.findall(pattern, content, re.DOTALL)
                    for match in matches:
                        try:
                            return json.loads(match), []
                        except json.JSONDecodeError:
                            continue

        # 尝试从第一个 { 到最后一个 }
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and start < end:
            try:
                return json.loads(text[start : end + 1]), []
            except json.JSONDecodeError as e:
                errors.append(f"JSON parse error in extracted object: {e}")

        return None, errors if errors else ["No valid JSON found"]

    @classmethod
    def extract_patch_blocks(cls, text: str) -> tuple[list[dict[str, Any]], list[str]]:
        """提取代码补丁块

        Returns:
            (list of patches, list of errors)
        """
        patches: list[dict[str, Any]] = []
        errors: list[str] = []

        if not text:
            return patches, errors

        # 匹配 PATCH_FILE 块
        pattern = r"PATCH_FILE:\s*(.+?)\n(.*?)END PATCH_FILE"
        matches = re.findall(pattern, text, re.DOTALL | re.IGNORECASE)

        for file_path, content in matches:
            file_path = file_path.strip()

            # 安全检查：路径遍历
            if ".." in file_path or file_path.startswith("/"):
                errors.append(f"Unsafe file path detected: {file_path}")
                continue

            # 提取 SEARCH/REPLACE 块
            sr_pattern = r"<<<<<<<\s*SEARCH\n(.*?)=======\n(.*?)\u003e>>>>>>\s*REPLACE"
            sr_matches = re.findall(sr_pattern, content, re.DOTALL)

            for search, replace in sr_matches:
                patches.append(
                    {
                        "file": file_path,
                        "search": search,
                        "replace": replace,
                    }
                )

        return patches, errors

    @classmethod
    def validate_role_output(cls, role: str, text: str) -> tuple[bool, dict | None, list[str]]:
        """验证角色输出

        Returns:
            (is_valid, parsed_data, errors)
        """
        errors = []

        # 检查是否有安全违规标记
        if "该请求超出我的职责范围或违反安全策略" in text:
            return True, {"security_blocked": True}, []

        # 根据角色类型选择验证方式
        if role in ["pm", "chief_engineer", "qa"]:
            # 这些角色需要JSON输出
            data, parse_errors = cls.extract_json(text)
            if data is None:
                return False, None, parse_errors

            # 基础schema验证
            schema = cls.ROLE_SCHEMAS.get(role)
            if schema:
                schema_errors = cls._validate_schema(data, schema)
                errors.extend(schema_errors)

            return len(errors) == 0, data, errors

        elif role == "director":
            schema = cls.ROLE_SCHEMAS.get("director")
            if schema:
                data, parse_errors = cls.extract_json(text)
                if data is not None:
                    schema_errors = cls._validate_schema(data, schema)
                    if not schema_errors:
                        return True, data, []
                    errors.extend(schema_errors)

            # Director需要验证补丁格式
            patches, patch_errors = cls.extract_patch_blocks(text)
            if not patches and not patch_errors:
                # 可能只有执行报告
                data, parse_errors = cls.extract_json(text)
                if data is not None:
                    return True, data, []
                merged_errors: list[str] = []
                merged_errors.extend(errors)
                merged_errors.extend(parse_errors)
                if not merged_errors:
                    merged_errors = ["No valid patches or JSON found"]
                return False, None, merged_errors

            return len(patch_errors) == 0, {"patches": patches}, patch_errors

        elif role == "architect":
            schema = cls.ROLE_SCHEMAS.get("architect")
            if schema:
                data, parse_errors = cls.extract_json(text)
                if data is not None:
                    schema_errors = cls._validate_schema(data, schema)
                    if not schema_errors:
                        return True, data, []
                    errors.extend(schema_errors)

            # Architect输出是文档格式，检查关键章节
            required_sections = ["架构", "技术栈", "模块"]
            missing = [s for s in required_sections if s not in text]
            if missing:
                errors.append(f"Missing sections: {missing}")
            return len(errors) == 0, {"text": text}, errors

        else:
            # 其他角色默认通过
            return True, {"text": text}, []

    @classmethod
    def _validate_schema(cls, data: dict, schema: dict) -> list[str]:
        """简单schema验证"""
        errors = []

        required = schema.get("required", [])
        for field in required:
            if field not in data:
                errors.append(f"Missing required field: {field}")

        properties = schema.get("properties", {})
        for field, value in data.items():
            if field in properties:
                prop_schema = properties[field]
                expected_type = prop_schema.get("type")
                if expected_type:
                    if expected_type == "array" and not isinstance(value, list):
                        errors.append(f"Field {field} should be array")
                    elif expected_type == "object" and not isinstance(value, dict):
                        errors.append(f"Field {field} should be object")
                    elif expected_type == "string" and not isinstance(value, str):
                        errors.append(f"Field {field} should be string")

                # Enum conformance: honestly reflect declared enum membership.
                enum = prop_schema.get("enum")
                if enum is not None and value not in enum:
                    errors.append(f"Field {field} must be one of {enum}, got {value!r}")

        return errors


class RoleOutputQualityChecker:
    """角色输出质量检查器"""

    @classmethod
    def check_output(cls, role: str, text: str, parsed_data: dict | None) -> tuple[float, list[str]]:
        """检查输出质量，返回得分和建议

        Returns:
            (score, suggestions)
        """
        score = 100.0
        suggestions: list[str] = []

        if role == "pm":
            return cls._check_pm_output(text, parsed_data)
        elif role == "architect":
            return cls._check_architect_output(text, parsed_data)
        elif role == "chief_engineer":
            return cls._check_ce_output(text, parsed_data)
        elif role == "director":
            return cls._check_director_output(text, parsed_data)
        elif role == "qa":
            return cls._check_qa_output(text, parsed_data)

        return score, suggestions

    @classmethod
    def _check_pm_output(cls, text: str, data: dict | None) -> tuple[float, list[str]]:
        score = 100.0
        suggestions = []

        if not data:
            return 0, ["Failed to parse output"]

        tasks = data.get("tasks", [])
        if not tasks:
            score -= 50
            suggestions.append("No tasks generated")
        else:
            if len(tasks) > 20:
                score -= 10
                suggestions.append("Too many tasks, consider splitting")

            for task in tasks:
                # 检查验收标准
                criteria = task.get("acceptance_criteria", [])
                if not criteria:
                    score -= 5
                    suggestions.append(f"Task {task.get('id')} missing acceptance criteria")

                # 检查目标文件
                files = task.get("target_files", [])
                for f in files:
                    if ".." in f or f.startswith("/"):
                        score -= 10
                        suggestions.append(f"Unsafe path in task {task.get('id')}: {f}")

        # 检查模糊词汇
        vague_words = ["适当的", "合适的", "根据需要", "等等"]
        for word in vague_words:
            if word in text:
                score -= 3
                suggestions.append(f"Avoid vague word: {word}")

        return max(0, score), suggestions

    @classmethod
    def _check_architect_output(cls, text: str, data: dict | None) -> tuple[float, list[str]]:
        score = 100.0
        suggestions = []

        required_sections = ["架构", "技术栈", "模块"]
        for section in required_sections:
            if section not in text:
                score -= 15
                suggestions.append(f"Missing section: {section}")

        # 检查技术债务标记（canonical 源头: polaris.kernelone.quality.role_output_markers）
        for marker in DEBT_MARKERS:
            if marker.lower() in text.lower():
                suggestions.append(f"Warning: found debt marker '{marker}'")

        return max(0, score), suggestions

    @classmethod
    def _check_ce_output(cls, text: str, data: dict | None) -> tuple[float, list[str]]:
        score = 100.0
        suggestions = []

        if not data:
            return 0, ["Failed to parse output"]

        plan = data.get("construction_plan", {})
        if not plan:
            score -= 30
            suggestions.append("Missing construction_plan")

        scope = data.get("scope_for_apply", [])
        if not scope:
            score -= 20
            suggestions.append("Missing scope_for_apply")

        # 检查风险标记
        risks = data.get("risk_flags", [])
        if not risks:
            suggestions.append("No risk assessment provided")

        return max(0, score), suggestions

    @classmethod
    def _check_director_output(cls, text: str, data: dict | None) -> tuple[float, list[str]]:
        score = 100.0
        suggestions: list[str] = []

        # 检查补丁格式
        if "PATCH_FILE:" not in text and "<<<<<<< SEARCH" not in text:
            # 可能是执行报告
            if data and "execution_status" in data:
                return score, suggestions
            score -= 50
            suggestions.append("No valid patch format found")
        else:
            # 验证SEARCH/REPLACE完整性
            search_count = text.count("<<<<<<< SEARCH")
            replace_count = text.count(">>>>>>> REPLACE")
            if search_count != replace_count:
                score -= 30
                suggestions.append("Mismatched SEARCH/REPLACE blocks")

        # 安全检查 - 使用 canonical 源头
        if is_path_traversal(text):
            score -= 50
            suggestions.append("Dangerous pattern found: path traversal")
        if is_dangerous_command(text):
            score -= 50
            suggestions.append("Dangerous pattern found: dangerous command")

        return max(0, score), suggestions

    @classmethod
    def _check_qa_output(cls, text: str, data: dict | None) -> tuple[float, list[str]]:
        score = 100.0
        suggestions = []

        if not data:
            return 0, ["Failed to parse output"]

        verdict = data.get("verdict")
        if not verdict:
            score -= 30
            suggestions.append("Missing verdict")
        elif verdict not in ["PASS", "CONDITIONAL", "FAIL", "BLOCKED"]:
            score -= 15
            suggestions.append(f"Invalid verdict: {verdict}")

        findings = data.get("findings", [])
        if verdict == "FAIL" and not findings:
            score -= 30
            suggestions.append("FAIL verdict without findings")

        return max(0, score), suggestions


__all__ = [
    "ROLE_PROMPT_TEMPLATES",
    "RoleOutputParser",
    "RoleOutputQualityChecker",
    "generate_role_response",
    "generate_role_response_streaming",
    "get_registered_roles",
    "register_role_template",
    "validate_and_parse_role_output",
]


def validate_and_parse_role_output(role: str, output: str) -> dict[str, Any]:
    """验证并解析角色输出的便捷函数

    Returns:
        {
            "success": bool,
            "data": parsed_data or None,
            "errors": [错误列表],
            "quality_score": float,
            "suggestions": [建议列表]
        }
    """
    is_valid, data, errors = RoleOutputParser.validate_role_output(role, output)
    score, suggestions = RoleOutputQualityChecker.check_output(role, output, data)
    gate_passed = is_valid and score >= _ROLE_QUALITY_SCORE_THRESHOLD

    return {
        # success 仅表示解析与结构检查结果，不用于动作裁决
        "success": is_valid,
        # gate_passed 保留为信号，供上层统一策略决策使用
        "gate_passed": gate_passed,
        "gate_threshold": _ROLE_QUALITY_SCORE_THRESHOLD,
        "data": data,
        "errors": errors,
        "quality_score": score,
        "suggestions": suggestions,
    }
