"""Backend resolution and invocation for loop-pm."""

import argparse
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any

from polaris.cells.context.engine.public.service import get_anthropomorphic_context_v2

# Use runtime config from polaris.delivery.cli.pm.config.
from polaris.delivery.cli.pm.config import PmRoleState, load_pm_model_config
from polaris.delivery.cli.pm.utils import _use_context_engine_v2, format_json_for_prompt
from polaris.infrastructure.compat.io_utils import (
    emit_event,
    emit_llm_event,
    ensure_codex_available,
    ensure_ollama_available,
    write_text_atomic,
)
from polaris.kernelone.memory.integration import (
    get_anthropomorphic_context,
)
from polaris.kernelone.process.codex_adapter import invoke_codex
from polaris.kernelone.process.ollama_utils import invoke_ollama
from polaris.kernelone.prompts.loader import current_profile, get_template, render_template
from polaris.kernelone.prompts.meta_prompting import build_meta_prompting_appendix
from polaris.kernelone.runtime.shared_types import strip_ansi
from polaris.kernelone.runtime.usage_metrics import (
    TokenUsage,
    UsageContext,
    track_usage,
)

logger = logging.getLogger(__name__)


@dataclass
class BackendLLMConfig:
    """Simple data class for backend LLM configuration."""

    provider_id: str
    model: str
    provider_kind: str = "generic"


def _provider_kind_from_provider_id(provider_id: str) -> str:
    """Infer backend kind from provider identifier."""
    token = str(provider_id or "").strip().lower()
    if "codex" in token:
        return "codex"
    if "ollama" in token:
        return "ollama"
    return "generic"


def _extract_iteration_from_run_id(run_id: str) -> int:
    token = str(run_id or "").strip().lower()
    if not token.startswith("pm-"):
        return 0
    suffix = token[3:]
    return int(suffix) if suffix.isdigit() else 0


def _preview_text_for_event(text: str, max_chars: int = 160) -> str:
    compact = re.sub(r"\s+", " ", str(text or "").strip())
    if len(compact) <= max_chars:
        return compact
    if max_chars <= 3:
        return compact[:max_chars]
    return compact[: max_chars - 3] + "..."


def _summarize_pm_output_for_event(output: str) -> dict[str, Any]:
    payload = _extract_json_from_llm_output(output)
    if not isinstance(payload, dict):
        return {
            "summary": _preview_text_for_event(output, max_chars=200),
            "task_count": 0,
            "task_titles": [],
        }

    tasks_raw = payload.get("tasks")
    tasks = tasks_raw if isinstance(tasks_raw, list) else []
    titles: list[str] = []
    for item in tasks:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if title:
            titles.append(title)

    focus = str(payload.get("focus") or payload.get("overall_goal") or "").strip()
    task_count = len(tasks)
    if task_count > 0:
        lead = ", ".join(titles[:2])
        summary = f"生成 {task_count} 个任务"
        if lead:
            summary += f": {lead}"
    elif focus:
        summary = f"规划焦点: {focus}"
    else:
        summary = "已返回规划响应（无任务项）"

    return {
        "summary": summary,
        "task_count": task_count,
        "task_titles": titles[:5],
        "focus": focus,
    }


def _emit_pm_llm_runtime_event(
    state: PmRoleState,
    *,
    event: str,
    usage_ctx: UsageContext | None,
    data: dict[str, Any],
    source: str = "runtime",
) -> None:
    llm_path = str(getattr(state, "llm_events_full", "") or "").strip()
    if not llm_path:
        return
    run_id = str(getattr(usage_ctx, "run_id", "") or "").strip()
    iteration = _extract_iteration_from_run_id(run_id)
    emit_llm_event(
        llm_path,
        event=event,
        role="pm",
        run_id=run_id,
        iteration=iteration,
        source=source,
        data=data if isinstance(data, dict) else {},
    )


def _resolve_role_runtime_llm_config(
    _state: PmRoleState,
    _role: str,
) -> BackendLLMConfig:
    """Resolve role runtime LLM config."""
    provider_id, model = load_pm_model_config()
    return BackendLLMConfig(
        provider_id=provider_id,
        model=model,
        provider_kind=_provider_kind_from_provider_id(provider_id),
    )


# LLM output cleaning patterns
_LLM_STRIP_PATTERNS = [
    re.compile(r"<minimax:tool_call>.*?</minimax:tool_call>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<tool_call>.*?</tool_call>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<function_calls?>.*?</function_calls?>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<invoke\b[^>]*>.*?</invoke>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<think[^>]*>.*?</think>", re.DOTALL | re.IGNORECASE),
    re.compile(r"\[tool_call\].*?\[/tool_call\]", re.DOTALL | re.IGNORECASE),
    re.compile(r"\[function_call\].*?\[/function_call\]", re.DOTALL | re.IGNORECASE),
]


def _strip_llm_xml_tags(text: str) -> str:
    """Strip XML tags from LLM output."""
    result = text
    for pat in _LLM_STRIP_PATTERNS:
        result = pat.sub("", result)
    return result.strip()


def _extract_json_from_llm_output(raw_output: str) -> dict[str, Any] | None:
    """Try to extract a JSON object from LLM output."""
    if not raw_output:
        return None
    cleaned = _strip_llm_xml_tags(strip_ansi(raw_output))
    if not cleaned:
        return None
    try:
        import json

        obj = json.loads(cleaned)
        if isinstance(obj, dict):
            return obj
    except (json.JSONDecodeError, TypeError, ValueError) as e:
        logger.debug(f"Failed to parse JSON: {e}")
    fence_match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", cleaned, re.IGNORECASE)
    if fence_match:
        try:
            import json

            obj = json.loads(fence_match.group(1))
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            logger.debug(f"Failed to parse JSON from fence: {e}")
    for m in re.finditer(r"\{", cleaned):
        start = m.start()
        depth = 0
        in_str = False
        escape = False
        for i in range(start, len(cleaned)):
            ch = cleaned[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = cleaned[start : i + 1]
                    try:
                        import json

                        obj = json.loads(candidate)
                        if isinstance(obj, dict) and ("tasks" in obj or "overall_goal" in obj or "focus" in obj):
                            return obj
                    except (json.JSONDecodeError, TypeError, ValueError) as e:
                        logger.debug(f"Failed to parse JSON candidate: {e}")
                    break
    return None


def resolve_pm_backend_kind(
    requested_backend: str,
    state: PmRoleState,
) -> tuple[str, BackendLLMConfig | None]:
    """Resolve PM backend kind from request or configuration."""
    backend = str(requested_backend or "auto").strip().lower()

    llm_cfg = _resolve_role_runtime_llm_config(state, "pm")
    provider_kind = str(getattr(llm_cfg, "provider_kind", "generic") or "generic").strip().lower()

    if backend in ("codex", "ollama"):
        return backend, llm_cfg
    if backend != "auto":
        backend = "auto"
    return provider_kind, llm_cfg


def ensure_pm_backend_available(backend_kind: str) -> None:
    """Ensure the requested backend is available."""
    if backend_kind == "codex":
        ensure_codex_available()
    elif backend_kind == "ollama":
        ensure_ollama_available()


def _invoke_generic_runtime_provider(
    state: PmRoleState,
    prompt: str,
    usage_ctx: UsageContext | None,
) -> str:
    """Invoke PM-configured runtime provider via provider registry.

    This path is fail-closed. Missing bindings or unavailable providers are
    treated as configuration errors and surfaced to the caller.
    """
    from polaris.infrastructure.llm.provider_runtime_adapter import (
        AppLLMRuntimeAdapter,
    )
    from polaris.kernelone.llm.runtime import invoke_role_runtime_provider

    provider_id, model = load_pm_model_config()
    if not provider_id or not model:
        raise RuntimeError("PM runtime provider binding is not configured.")

    provider_result = invoke_role_runtime_provider(
        role="pm",
        workspace=state.workspace_full,
        prompt=prompt,
        fallback_model=model,
        timeout=state.timeout,
        adapter=AppLLMRuntimeAdapter(),
        blocked_provider_types={"", "ollama", "codex", "codex_cli", "codex_sdk"},
    )
    if not provider_result.attempted or not provider_result.ok:
        error_message = str(provider_result.error or "").strip() or "runtime_provider_unavailable"
        raise RuntimeError(f"PM runtime provider invocation failed: {error_message}")

    output = str(provider_result.output or "")
    resolved_type = str(provider_result.provider_type or "").strip()
    resolved_model = str(provider_result.model or model).strip()

    if usage_ctx and state.events_full:
        usage_obj = provider_result.usage
        usage = TokenUsage(
            prompt_tokens=int(getattr(usage_obj, "prompt_tokens", 0) or 0),
            completion_tokens=int(getattr(usage_obj, "completion_tokens", 0) or 0),
            total_tokens=int(getattr(usage_obj, "total_tokens", 0) or 0),
            estimated=bool(getattr(usage_obj, "estimated", True)),
            prompt_chars=int(getattr(usage_obj, "prompt_chars", len(prompt)) or len(prompt)),
            completion_chars=int(getattr(usage_obj, "completion_chars", len(output)) or len(output)),
        )
        track_usage(
            state.events_full,
            usage_ctx,
            model=resolved_model,
            provider=resolved_type or "generic",
            usage=usage,
            duration_ms=int(provider_result.latency_ms or 0),
            ok=bool(provider_result.ok),
            error=str(provider_result.error or "") or None,
        )
    return output


def invoke_pm_backend(
    state: PmRoleState,
    prompt: str,
    backend_kind: str,
    args: argparse.Namespace,
    usage_ctx: UsageContext | None,
) -> str:
    """Invoke the PM backend with the given prompt."""
    started_at = time.time()
    resolved_backend = str(backend_kind or "").strip().lower() or "generic"
    _emit_pm_llm_runtime_event(
        state,
        event="invoke_start",
        usage_ctx=usage_ctx,
        data={
            "backend": resolved_backend,
            "prompt_chars": len(str(prompt or "")),
        },
    )

    try:
        if backend_kind == "codex":
            output = invoke_codex(
                prompt,
                state.ollama_full,
                state.workspace_full,
                state.show_output,
                args.codex_full_auto,
                args.codex_dangerous,
                args.codex_profile,
                state.timeout,
                None,
                usage_ctx=usage_ctx,
                events_path=state.events_full,
            )
            resolved_backend = "codex"
        elif backend_kind == "ollama":
            output = invoke_ollama(  # type: ignore[assignment]
                prompt,
                state.model,
                state.workspace_full,
                state.show_output,
                state.timeout,
                usage_ctx=usage_ctx,
                events_path=state.events_full,
            )
            resolved_backend = "ollama"
        else:
            output = _invoke_generic_runtime_provider(
                state=state,
                prompt=prompt,
                usage_ctx=usage_ctx,
            )
            if state.ollama_full:
                write_text_atomic(state.ollama_full, output or "")
            resolved_backend = "runtime_provider"

        elapsed_ms = int((time.time() - started_at) * 1000)
        summary_payload = _summarize_pm_output_for_event(output)
        output_text = str(output or "")
        output_chars = len(output_text)
        output_empty = len(output_text.strip()) == 0
        _emit_pm_llm_runtime_event(
            state,
            event="invoke_done",
            usage_ctx=usage_ctx,
            data={
                "backend": resolved_backend,
                "duration_ms": elapsed_ms,
                "output_chars": output_chars,
                "summary": str(summary_payload.get("summary") or "").strip(),
                "task_count": int(summary_payload.get("task_count") or 0),
                "task_titles": summary_payload.get("task_titles") or [],
                "preview": _preview_text_for_event(output),
            },
        )
        if output_empty:
            _emit_pm_llm_runtime_event(
                state,
                event="invoke_error",
                usage_ctx=usage_ctx,
                data={
                    "backend": resolved_backend,
                    "duration_ms": elapsed_ms,
                    "error": "empty_response",
                },
                source="runtime",
            )
            raise RuntimeError("PM backend returned empty response")
        return output
    except (OSError, RuntimeError, TimeoutError, TypeError, ValueError) as exc:
        elapsed_ms = int((time.time() - started_at) * 1000)
        _emit_pm_llm_runtime_event(
            state,
            event="invoke_error",
            usage_ctx=usage_ctx,
            data={
                "backend": resolved_backend,
                "duration_ms": elapsed_ms,
                "error": str(exc or "").strip() or "invoke_failed",
            },
            source="error",
        )
        raise


def build_pm_prompt(
    requirements: str,
    plan_text: str,
    gap_report: str,
    last_qa: str,
    last_tasks: Any,
    director_result: Any,
    pm_state: Any,
    iteration: int = 0,
    run_id: str = "",
    events_path: str = "",
    workspace_root: str = "",
) -> str:
    """Build the PM prompt with context."""
    from polaris.delivery.cli.pm.config import PROJECT_ROOT

    profile = current_profile().strip().lower()
    is_zh = profile.endswith("_zh") or profile.startswith("zh") or profile in ("zh", "chinese")
    if "armada" in profile:
        intro = "你是这个海战 MMO 仓库的项目经理。" if is_zh else "You are the project manager for a naval MMO repo."
    else:
        intro = (
            "你是这个软件项目仓库的项目经理。" if is_zh else "You are the project manager for a software project repo."
        )

    query = f"{requirements}\n{plan_text}"
    context_root = str(workspace_root or os.environ.get("POLARIS_CONTEXT_ROOT") or PROJECT_ROOT).strip()
    if not context_root or not os.path.isdir(context_root):
        context_root = PROJECT_ROOT
    context_root = os.path.abspath(context_root)

    if _use_context_engine_v2():
        anthro = get_anthropomorphic_context_v2(
            context_root,
            "pm",
            query,
            iteration,
            run_id,
            "pm.planning",
            events_path=events_path or "",
        )
    else:
        anthro = get_anthropomorphic_context(context_root, "pm", query, iteration, run_id, "pm.planning")

    if events_path:
        output = anthro["prompt_context_obj"].model_dump()
        context_pack = anthro.get("context_pack")
        if context_pack is not None:
            output["context_hash"] = getattr(context_pack, "request_hash", "")
            output["context_snapshot"] = getattr(context_pack, "snapshot_path", "")
        emit_event(
            events_path,
            kind="observation",
            actor="PM",
            name="prompt_context",
            refs={"run_id": run_id, "step": iteration},
            summary="Prompt Context Injection",
            output=output,
        )

    template = get_template("pm_prompt")
    rendered = render_template(
        template,
        {
            "pm_intro": intro,
            "requirements": requirements,
            "plan_text": plan_text,
            "gap_report": gap_report,
            "last_qa": last_qa,
            "last_tasks": format_json_for_prompt(last_tasks),
            "director_result": format_json_for_prompt(director_result),
            "pm_state": format_json_for_prompt(pm_state),
            "persona_instruction": anthro["persona_instruction"],
            "anthropomorphic_context": anthro["anthropomorphic_context"],
        },
    )
    if is_zh:
        role_boundary_rules = (
            "\n角色权限边界（必遵守）：\n"
            "- 天子/用户仅通过 UI 指令入口（廷议/快驿）下达意图，不生成直接写文件任务。\n"
            "- 中书令任务仅允许读写 `docs/`。\n"
            "- PM 只负责规划与契约编排，不直接执行代码实现。\n"
            "- 工部尚书（ChiefEngineer）负责生成代码施工图与方法级蓝图，不直接提交业务代码。\n"
            '- Director 任务必须声明代码作用域，默认 `scope_mode: "module"` + `scope_paths`。\n'
            "- QA/Auditor 为读多写少，仅可写审计结论与缺陷票据等验收产物。\n"
            "- 禁止自动回滚；回滚只允许人工确认触发。\n"
            "- 严格遵守已绑定角色->模型路由，不得跨模型越权调用。\n"
        )
    else:
        role_boundary_rules = (
            "\nRole boundary contract (mandatory):\n"
            "- Emperor/Human interacts via UI directives only (Dialogue/Express mode), not direct file-write tasks.\n"
            "- Architect tasks are docs-only and may read/write `docs/` only.\n"
            "- PM is planning-only and must not perform implementation edits.\n"
            "- ChiefEngineer tasks generate code construction blueprints (module/file/method level) and do not implement business code.\n"
            '- Director tasks must declare code scope, defaulting to `scope_mode: "module"` with `scope_paths`.\n'
            "- QA/Auditor is read-mostly and may write audit artifacts only.\n"
            "- Auto rollback is forbidden; rollback is manual-only.\n"
            "- Respect bound role->model routing; no cross-model override.\n"
        )
    meta_prompt_appendix = build_meta_prompting_appendix(
        workspace_root or context_root,
        "pm",
        limit=4,
    )
    # Enforce backlog mapping guidance even if a profile template is stale.
    return (
        rendered
        + "\n\nBacklog mapping rule (required):\n"
        + "- Each generated task SHOULD include `backlog_ref` with the original plan backlog text it derives from.\n"
        + "- If source backlog item is uncertain, set `backlog_ref` to an empty string.\n"
        + "- Never hallucinate a backlog source.\n"
        + "\nrequired_evidence contract rule (required):\n"
        + "- `required_evidence` is validation metadata only, not an instruction channel.\n"
        + "- Do NOT output `required_evidence.must_read` or `required_evidence.must_find_calls`.\n"
        + "- For Director tasks, use `required_evidence.validation_paths` only when post-run validation artifacts are needed.\n"
        + "- Return exactly one JSON object and no extra text.\n"
        + "- Do NOT emit TOOL_CALL/function-call markup.\n"
        + "\nBootstrap-first planning rule (required):\n"
        + "- If key project files are missing (e.g. package.json, pyproject.toml, src entry files), create bootstrap tasks first.\n"
        + "- Do NOT schedule verification-only tasks (build/test/lint/read-only inspection) before scaffold files exist.\n"
        + '- For Director tasks, default to `scope_mode: "module"` with `scope_paths` (module/directory prefixes).\n'
        + '- Only use `scope_mode: "exact_files"` with `target_files` when high-risk or when user explicitly requests precise file locking.\n'
        + "- In `module` mode, `target_files` is optional and acts as hints, not the primary scope boundary.\n"
        + "- Avoid exploration-only/read-only Director tasks unless explicitly requested by the user.\n"
        + "\nDocs-stage dispatch rule (required):\n"
        + "- If requirements contain `[PM_DOC_STAGE]`, tasks MUST stay within the active document scope only.\n"
        + "- Never generate synthetic bootstrap paths outside the active stage document.\n"
        + "- If file targets are unclear, emit a docs-stage convergence task bound to `active_document` instead of cross-stage code tasks.\n"
        + "\nDefect-loop and rollback rule (required):\n"
        + "- Auto rollback is forbidden. Director must use fix-forward multi-round repair.\n"
        + "- Keep retrying code repair up to 5 rounds before escalating to Tri-Council (Director -> ChiefEngineer -> PM).\n"
        + "- Request human intervention only after Tri-Council rounds are exhausted and issue remains unresolved.\n"
        + "\nVerification cadence rule (required):\n"
        + "- Do not require heavy syntax/test verification on every repair round.\n"
        + "- Prioritize final-round verification for a task and integration-level verification after task completion.\n"
        + "\nTask dependency rule (required):\n"
        + "- Emit `depends_on` when a task logically requires an earlier task output.\n"
        + "- Use `phase` hints to express execution order: bootstrap/scaffold -> core/implementation -> integration -> verification/qa -> polish.\n"
        + "- Never place test-only tasks before required implementation/bootstrap tasks.\n"
        + "- For complex coding tasks, you MAY assign `ChiefEngineer` before `Director` to produce a detailed construction blueprint.\n"
        + "\nTask detail quality rule (required):\n"
        + "- When tasks count >= 2, include at least one dependency chain via `depends_on`.\n"
        + "- Every task MUST include `phase` and `execution_checklist` (3-6 concrete steps).\n"
        + "- Every task acceptance criteria MUST include measurable signals (commands/thresholds/evidence paths), not generic prose.\n"
        + "- Every task SHOULD include `backlog_ref` mapped to the originating backlog/blueprint item text.\n"
        + "- In docs-stage, every task MUST include `metadata.doc_sections` (array) and `metadata.change_intent`.\n"
        + "- If active docs-stage is enabled, keep all task paths strictly within `active_document` (or its direct doc directory scope only when explicitly needed).\n"
        + role_boundary_rules
        + meta_prompt_appendix
    )


__all__ = [
    "_extract_json_from_llm_output",
    "_resolve_role_runtime_llm_config",
    "build_pm_prompt",
    "ensure_pm_backend_available",
    "invoke_pm_backend",
    "resolve_pm_backend_kind",
]
