import asyncio
import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from polaris.cells.llm.dialogue.public.service import (
    generate_dialogue_turn as generate_docs_dialogue_turn,
    generate_dialogue_turn_streaming as generate_docs_dialogue_turn_streaming,
    generate_docs_fields as generate_docs_ai_fields,
    generate_docs_fields_stream,
)
from polaris.cells.runtime.projection.public.service import write_text_atomic
from polaris.cells.runtime.state_owner.public.service import AppState
from polaris.cells.workspace.integrity.public.service import (
    build_docs_templates,
    clear_workspace_status,
    default_qa_commands,
    detect_project_profile,
    is_safe_docs_path,
    normalize_rel_path,
    select_docs_target_root,
    workspace_has_docs,
)
from polaris.delivery.http.routers._shared import get_state, require_auth
from polaris.delivery.http.schemas import (
    DocsInitApplyPayload,
    DocsInitDialoguePayload,
    DocsInitPreviewPayload,
    DocsInitSuggestPayload,
)
from polaris.kernelone.events import emit_event
from polaris.kernelone.llm import config_store as llm_config
from polaris.kernelone.runtime.shared_types import normalize_timeout_seconds
from polaris.kernelone.storage.io_paths import build_cache_root, resolve_artifact_path

from .sse_utils import create_sse_response, sse_event_generator

router = APIRouter()
log = logging.getLogger("polaris.routers.docs")


def _sync_plan_to_runtime(workspace: str, cache_root: str) -> None:
    """Copy plan.md to runtime/contracts/plan.md so PM loop
    picks it up automatically.  Uses atomic write via ArtifactService
    to avoid partial reads by a concurrently running PM loop."""
    # Use ArtifactService for unified artifact I/O
    try:
        from polaris.cells.audit.verdict.public.service import ArtifactService

        service = ArtifactService(workspace=workspace, cache_root=cache_root)

        # Read plan from workspace docs (legacy location)
        plan_src_candidates = [
            resolve_artifact_path(workspace, cache_root, "workspace/docs/product/plan.md"),
            os.path.join(workspace, "docs", "product", "plan.md"),  # backward compatibility
        ]
        plan_src = ""
        for candidate in plan_src_candidates:
            if candidate and os.path.isfile(candidate):
                plan_src = candidate
                break

        if not plan_src:
            log.info("PLAN_SYNC_SKIP: no plan source exists")
            return

        # Read and write via ArtifactService
        with open(plan_src, encoding="utf-8") as f:
            plan_content = f.read()

        service.write_plan(plan_content)
        log.info("PLAN_SYNC_OK: %s -> runtime/contracts/plan.md", plan_src)

    except (RuntimeError, ValueError):
        log.warning("PLAN_SYNC_FAIL: could not sync plan to runtime", exc_info=True)


def _bind_docs_wizard_llm_from_architect_role(state: AppState) -> dict[str, Any]:
    """Force docs wizard to use the provider/model connected to architect role."""
    workspace = state.settings.workspace
    workspace_str = str(workspace) if not isinstance(workspace, str) else workspace
    cache_root = build_cache_root(state.settings.ramdisk_root or "", workspace_str)
    config = llm_config.load_llm_config(workspace_str, cache_root, settings=state.settings)

    roles = config.get("roles") or {}
    architect_cfg = roles.get("architect") if isinstance(roles.get("architect"), dict) else None
    docs_cfg = roles.get("docs") if isinstance(roles.get("docs"), dict) else None
    role_cfg = architect_cfg or docs_cfg
    if not isinstance(role_cfg, dict):
        raise HTTPException(status_code=409, detail="中书令角色未配置，请先在 LLM 设置中完成角色绑定。")

    provider_id = str(role_cfg.get("provider_id") or "").strip()
    model = str(role_cfg.get("model") or "").strip()
    if not provider_id or not model:
        raise HTTPException(status_code=409, detail="中书令角色缺少 provider_id/model，请先完成角色绑定。")

    providers = config.get("providers") or {}
    provider_cfg = providers.get(provider_id) if isinstance(providers.get(provider_id), dict) else None
    if not isinstance(provider_cfg, dict):
        raise HTTPException(status_code=409, detail=f"中书令绑定的提供商不存在: {provider_id}")

    provider_type = str(provider_cfg.get("type") or "").strip().lower()
    if not provider_type:
        raise HTTPException(status_code=409, detail=f"中书令绑定的提供商缺少 type: {provider_id}")

    # Keep docs wizard runtime aligned with architect role without restricting provider type.
    state.settings.architect_spec_provider = provider_type
    state.settings.docs_init_provider = provider_type
    state.settings.architect_spec_model = model
    state.settings.docs_init_model = model

    base_url = str(provider_cfg.get("base_url") or "").strip()
    if base_url:
        state.settings.architect_spec_base_url = base_url
        state.settings.docs_init_base_url = base_url

    api_path = str(provider_cfg.get("api_path") or "").strip()
    if api_path:
        state.settings.architect_spec_api_path = api_path
        state.settings.docs_init_api_path = api_path

    api_key = str(provider_cfg.get("api_key") or "").strip()
    if api_key:
        state.settings.architect_spec_api_key = api_key
        state.settings.docs_init_api_key = api_key

    timeout = normalize_timeout_seconds(provider_cfg.get("timeout"), default=0)
    if timeout > 0:
        state.settings.architect_spec_timeout = timeout
        state.settings.docs_init_timeout = timeout
    elif provider_cfg.get("timeout") is not None:
        state.settings.architect_spec_timeout = 0
        state.settings.docs_init_timeout = 0

    return {
        "provider_id": provider_id,
        "provider_type": provider_type,
        "model": model,
        "mapped_provider": provider_type,
    }


def _join_lines(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        lines = [str(item).strip() for item in value if str(item).strip()]
        return "\n".join(lines)
    return ""


@router.post("/docs/init/dialogue", dependencies=[Depends(require_auth)])
async def docs_init_dialogue(request: Request, payload: DocsInitDialoguePayload) -> dict[str, Any]:
    state = get_state(request)
    workspace = state.settings.workspace
    workspace_str = str(workspace) if not isinstance(workspace, str) else workspace
    _bind_docs_wizard_llm_from_architect_role(state)

    fields = {
        "goal": payload.goal or "",
        "in_scope": payload.in_scope or "",
        "out_of_scope": payload.out_of_scope or "",
        "constraints": payload.constraints or "",
        "definition_of_done": payload.definition_of_done or "",
        "backlog": payload.backlog or "",
    }
    history: list[dict[str, Any]] = []
    for turn in payload.history or []:
        role = str(turn.role or "").strip().lower()
        content = str(turn.content or "").strip()
        if role not in ("user", "assistant") or not content:
            continue
        questions = [str(item).strip() for item in (turn.questions or []) if str(item).strip()]
        row: dict[str, Any] = {"role": role, "content": content}
        if questions:
            row["questions"] = questions
        history.append(row)

    result = await generate_docs_dialogue_turn(
        workspace=workspace_str,
        settings=state.settings,
        fields=fields,
        history=history,
        message=str(payload.message or ""),
    )
    if not result:
        raise HTTPException(
            status_code=409,
            detail="中书令角色 LLM 奏对失败：可能是输出被截断或格式不符，请检查 max_tokens、模型输出格式与网络连通性。",
        )

    result_fields = result.get("fields")
    out_fields: dict[str, Any] = result_fields if isinstance(result_fields, dict) else {}
    return {
        "ok": True,
        "reply": str(result.get("reply") or ""),
        "questions": result.get("questions") or [],
        "tiaochen": result.get("tiaochen") or [],
        "meta": result.get("meta") or {},
        "handoffs": result.get("handoffs") or {},
        "fields": {
            "goal": _join_lines(out_fields.get("goal") or ""),
            "in_scope": _join_lines(out_fields.get("in_scope") or ""),
            "out_of_scope": _join_lines(out_fields.get("out_of_scope") or ""),
            "constraints": _join_lines(out_fields.get("constraints") or ""),
            "definition_of_done": _join_lines(out_fields.get("definition_of_done") or ""),
            "backlog": _join_lines(out_fields.get("backlog") or ""),
        },
    }


@router.post("/docs/init/dialogue/stream", dependencies=[Depends(require_auth)])
async def docs_init_dialogue_stream(request: Request, payload: DocsInitDialoguePayload):
    """Stream docs dialogue turn using Server-Sent Events (SSE).

    Emits ``thinking_chunk`` events with incremental LLM tokens,
    followed by a terminal ``complete`` event with the parsed result.
    """
    state = get_state(request)
    workspace = state.settings.workspace
    workspace_str = str(workspace) if not isinstance(workspace, str) else workspace
    _bind_docs_wizard_llm_from_architect_role(state)

    fields = {
        "goal": payload.goal or "",
        "in_scope": payload.in_scope or "",
        "out_of_scope": payload.out_of_scope or "",
        "constraints": payload.constraints or "",
        "definition_of_done": payload.definition_of_done or "",
        "backlog": payload.backlog or "",
    }
    history: list[dict[str, Any]] = []
    for turn in payload.history or []:
        role = str(turn.role or "").strip().lower()
        content = str(turn.content or "").strip()
        if role not in ("user", "assistant") or not content:
            continue
        questions = [str(item).strip() for item in (turn.questions or []) if str(item).strip()]
        row: dict[str, Any] = {"role": role, "content": content}
        if questions:
            row["questions"] = questions
        history.append(row)

    async def _run_dialogue(queue: asyncio.Queue) -> None:
        await generate_docs_dialogue_turn_streaming(
            workspace=workspace_str,
            settings=state.settings,
            fields=fields,
            history=history,
            message=str(payload.message or ""),
            output_queue=queue,
        )

    return create_sse_response(sse_event_generator(_run_dialogue))


@router.post("/docs/init/suggest", dependencies=[Depends(require_auth)])
async def docs_init_suggest(request: Request, payload: DocsInitSuggestPayload) -> dict[str, Any]:
    state = get_state(request)
    workspace = state.settings.workspace
    workspace_str = str(workspace) if not isinstance(workspace, str) else workspace
    _bind_docs_wizard_llm_from_architect_role(state)
    fields = {
        "goal": payload.goal or "",
        "in_scope": payload.in_scope or "",
        "out_of_scope": payload.out_of_scope or "",
        "constraints": payload.constraints or "",
        "definition_of_done": payload.definition_of_done or "",
        "backlog": payload.backlog or "",
    }
    ai_fields = await generate_docs_ai_fields(workspace_str, state.settings, fields)
    if not ai_fields:
        raise HTTPException(status_code=409, detail="中书令角色 LLM 不可用，请检查 provider/model 与网络连通性。")
    return {
        "ok": True,
        "fields": {
            "goal": "\n".join(ai_fields.get("goal") or []),
            "in_scope": "\n".join(ai_fields.get("in_scope") or []),
            "out_of_scope": "\n".join(ai_fields.get("out_of_scope") or []),
            "constraints": "\n".join(ai_fields.get("constraints") or []),
            "definition_of_done": "\n".join(ai_fields.get("definition_of_done") or []),
            "backlog": "\n".join(ai_fields.get("backlog") or []),
        },
    }


@router.post("/docs/init/preview", dependencies=[Depends(require_auth)])
async def docs_init_preview(request: Request, payload: DocsInitPreviewPayload) -> dict[str, Any]:
    state = get_state(request)
    workspace = state.settings.workspace
    workspace_str = str(workspace) if not isinstance(workspace, str) else workspace
    _bind_docs_wizard_llm_from_architect_role(state)
    mode = str(payload.mode or "minimal").strip().lower()
    if mode not in ("minimal",):
        mode = "minimal"
    profile = detect_project_profile(workspace_str)
    cache_root = build_cache_root(state.settings.ramdisk_root or "", workspace_str)
    qa_commands = default_qa_commands(profile)
    fields = {
        "goal": payload.goal or "",
        "in_scope": payload.in_scope or "",
        "out_of_scope": payload.out_of_scope or "",
        "constraints": payload.constraints or "",
        "definition_of_done": payload.definition_of_done or "",
        "backlog": payload.backlog or "",
    }
    ai_fields = await generate_docs_ai_fields(workspace_str, state.settings, fields)
    if not ai_fields:
        raise HTTPException(status_code=409, detail="中书令角色 LLM 不可用，请检查 provider/model 与网络连通性。")
    if ai_fields.get("goal"):
        fields["goal"] = "\n".join(ai_fields.get("goal") or [])
    if ai_fields.get("in_scope"):
        fields["in_scope"] = "\n".join(ai_fields.get("in_scope") or [])
    if ai_fields.get("out_of_scope"):
        fields["out_of_scope"] = "\n".join(ai_fields.get("out_of_scope") or [])
    if ai_fields.get("constraints"):
        fields["constraints"] = "\n".join(ai_fields.get("constraints") or [])
    if ai_fields.get("definition_of_done"):
        fields["definition_of_done"] = "\n".join(ai_fields.get("definition_of_done") or [])
    if ai_fields.get("backlog"):
        fields["backlog"] = "\n".join(ai_fields.get("backlog") or [])
    docs_map = build_docs_templates(workspace_str, mode, fields, qa_commands)
    target_root = select_docs_target_root(workspace_str)
    files: list[dict[str, Any]] = []
    for rel_path, content in docs_map.items():
        suffix = rel_path.replace("docs/", "", 1)
        target_path = target_root.rstrip("/") + "/" + suffix if target_root != "docs" else rel_path
        full_path = resolve_artifact_path(workspace_str, cache_root, normalize_rel_path(target_path))
        files.append(
            {
                "path": target_path.replace("\\", "/"),
                "content": content,
                "exists": os.path.isfile(full_path),
            }
        )
    return {
        "ok": True,
        "mode": mode,
        "target_root": target_root,
        "docs_exists": workspace_has_docs(workspace_str),
        "project": profile,
        "files": files,
    }


@router.post("/docs/init/preview/stream", dependencies=[Depends(require_auth)])
async def docs_init_preview_stream(request: Request, payload: DocsInitPreviewPayload):
    """流式生成文档预览（SSE），实时显示执行进度"""

    state = get_state(request)
    workspace = state.settings.workspace
    workspace_str = str(workspace) if not isinstance(workspace, str) else workspace
    cache_root = build_cache_root(state.settings.ramdisk_root or "", workspace_str)

    async def _generate_preview_stream(queue: asyncio.Queue) -> None:
        try:
            # 阶段 1: 初始化
            await queue.put(
                {"type": "stage", "data": {"stage": "init", "message": "初始化文档生成环境...", "progress": 5}}
            )
            _bind_docs_wizard_llm_from_architect_role(state)
            mode = str(payload.mode or "minimal").strip().lower()
            if mode not in ("minimal",):
                mode = "minimal"

            # 阶段 2: 检测项目配置
            await queue.put(
                {"type": "stage", "data": {"stage": "detect", "message": "检测项目配置...", "progress": 10}}
            )
            profile = detect_project_profile(workspace_str)
            qa_commands = default_qa_commands(profile)

            fields = {
                "goal": payload.goal or "",
                "in_scope": payload.in_scope or "",
                "out_of_scope": payload.out_of_scope or "",
                "constraints": payload.constraints or "",
                "definition_of_done": payload.definition_of_done or "",
                "backlog": payload.backlog or "",
            }

            # 阶段 3: 调用 LLM 生成字段（流式，实时返回thinking）
            await queue.put(
                {"type": "stage", "data": {"stage": "llm_start", "message": "中书令正在分析需求...", "progress": 20}}
            )

            ai_fields = None
            collected_thinking = ""
            async for event in generate_docs_fields_stream(workspace_str, state.settings, fields):
                if event["type"] == "thinking":
                    # 实时发送thinking内容
                    collected_thinking += event["content"]
                    await queue.put(
                        {"type": "thinking", "data": {"content": event["content"], "accumulated": collected_thinking}}
                    )
                elif event["type"] == "result":
                    ai_fields = event["fields"]
                elif event["type"] == "error":
                    await queue.put({"type": "error", "data": {"error": event["error"]}})
                    return

            if not ai_fields:
                await queue.put(
                    {"type": "error", "data": {"error": "中书令角色 LLM 不可用，请检查 provider/model 与网络连通性。"}}
                )
                return

            # 发送 LLM 生成结果
            await queue.put(
                {
                    "type": "stage",
                    "data": {"stage": "llm_done", "message": "需求分析完成", "progress": 60, "fields": ai_fields},
                }
            )

            # 阶段 4: 应用生成的字段
            await queue.put(
                {"type": "stage", "data": {"stage": "apply_fields", "message": "整理生成结果...", "progress": 70}}
            )
            if ai_fields.get("goal"):
                fields["goal"] = "\n".join(ai_fields.get("goal") or [])
            if ai_fields.get("in_scope"):
                fields["in_scope"] = "\n".join(ai_fields.get("in_scope") or [])
            if ai_fields.get("out_of_scope"):
                fields["out_of_scope"] = "\n".join(ai_fields.get("out_of_scope") or [])
            if ai_fields.get("constraints"):
                fields["constraints"] = "\n".join(ai_fields.get("constraints") or [])
            if ai_fields.get("definition_of_done"):
                fields["definition_of_done"] = "\n".join(ai_fields.get("definition_of_done") or [])
            if ai_fields.get("backlog"):
                fields["backlog"] = "\n".join(ai_fields.get("backlog") or [])

            # 阶段 5: 构建文档模板
            await queue.put(
                {"type": "stage", "data": {"stage": "build_templates", "message": "构建文档模板...", "progress": 80}}
            )
            docs_map = build_docs_templates(workspace_str, mode, fields, qa_commands)
            target_root = select_docs_target_root(workspace_str)

            # 阶段 6: 准备文件列表
            await queue.put(
                {"type": "stage", "data": {"stage": "prepare_files", "message": "准备文件列表...", "progress": 90}}
            )
            files: list[dict[str, Any]] = []
            for rel_path, content in docs_map.items():
                suffix = rel_path.replace("docs/", "", 1)
                target_path = target_root.rstrip("/") + "/" + suffix if target_root != "docs" else rel_path
                full_path = resolve_artifact_path(workspace_str, cache_root, normalize_rel_path(target_path))
                files.append(
                    {
                        "path": target_path.replace("\\", "/"),
                        "content": content,
                        "exists": os.path.isfile(full_path),
                    }
                )

            # 完成
            await queue.put(
                {
                    "type": "complete",
                    "data": {
                        "ok": True,
                        "mode": mode,
                        "target_root": target_root,
                        "docs_exists": workspace_has_docs(workspace_str),
                        "project": profile,
                        "files": files,
                        "progress": 100,
                    },
                }
            )
        except (RuntimeError, ValueError) as exc:
            log.exception("Preview stream error")
            await queue.put({"type": "error", "data": {"error": str(exc)}})

    return create_sse_response(sse_event_generator(_generate_preview_stream, timeout=180.0))


@router.post("/docs/init/apply", dependencies=[Depends(require_auth)])
def docs_init_apply(request: Request, payload: DocsInitApplyPayload) -> dict[str, Any]:
    state = get_state(request)
    workspace = state.settings.workspace
    workspace_str = str(workspace) if not isinstance(workspace, str) else workspace
    cache_root = build_cache_root(state.settings.ramdisk_root or "", workspace_str)
    target_root = normalize_rel_path(payload.target_root or "workspace/docs")
    if not target_root or not target_root.lower().startswith("workspace/docs"):
        raise HTTPException(status_code=400, detail="target_root must be under workspace/docs/")
    files = payload.files or []
    if not files:
        raise HTTPException(status_code=400, detail="no files to write")
    created: list[str] = []
    for item in files:
        rel_path = normalize_rel_path(item.path)
        if not is_safe_docs_path(rel_path, target_root):
            raise HTTPException(status_code=400, detail=f"invalid docs path: {item.path}")
        try:
            full_path = resolve_artifact_path(workspace_str, cache_root, rel_path)
        except (RuntimeError, ValueError):
            raise HTTPException(status_code=400, detail=f"invalid docs path: {item.path}")
        write_text_atomic(full_path, item.content or "")
        created.append(rel_path.replace("\\", "/"))
    # Record init event (best effort, with semantic suppression in emit_event)
    try:
        event_path = resolve_artifact_path(workspace_str, cache_root, "runtime/events/runtime.events.jsonl")
        emit_event(
            event_path,
            kind="observation",
            actor="System",
            name="init_docs",
            refs={"phase": "docs_init"},
            summary="Initialized docs via onboarding wizard",
            ok=True,
            output={"artifacts": created},
        )
    except (RuntimeError, ValueError) as exc:
        log.warning("init_docs_onboarding failed (non-critical): %s", exc)
    if workspace_has_docs(workspace_str):
        clear_workspace_status(workspace_str)
    # Sync plan to runtime so PM loop picks it up automatically
    try:
        _sync_plan_to_runtime(workspace_str, cache_root)
    except (RuntimeError, ValueError):
        log.warning("PLAN_SYNC_FAIL: post-apply sync failed", exc_info=True)
    return {"ok": True, "files": created}
