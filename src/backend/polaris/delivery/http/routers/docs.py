import asyncio
import contextlib
import logging
import os
import re
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from polaris.cells.llm.dialogue.public.service import (
    build_default_docs_fields,
    generate_dialogue_turn as generate_docs_dialogue_turn,
    generate_dialogue_turn_streaming as generate_docs_dialogue_turn_streaming,
    generate_docs_fields as generate_docs_ai_fields,
    generate_docs_fields_stream,
)
from polaris.cells.runtime.projection.public.service import write_text_atomic
from polaris.cells.runtime.state_owner.public.service import AppState
from polaris.cells.workspace.integrity.public import (
    build_docs_templates,
    clear_workspace_status,
    default_qa_commands,
    detect_project_profile,
    is_safe_docs_path,
    normalize_rel_path,
    select_docs_target_root,
    workspace_has_docs,
)
from polaris.delivery.http.routers._shared import StructuredHTTPException, get_state, require_auth
from polaris.delivery.http.schemas import (
    DocsInitApplyPayload,
    DocsInitApplyResponse,
    DocsInitDialoguePayload,
    DocsInitDialogueResponse,
    DocsInitFile,
    DocsInitPreviewPayload,
    DocsInitPreviewResponse,
    DocsInitSuggestPayload,
    DocsInitSuggestResponse,
)
from polaris.infrastructure.messaging.nats.nats_types import create_runtime_event
from polaris.kernelone.events import emit_event
from polaris.kernelone.llm import config_store as llm_config
from polaris.kernelone.runtime.shared_types import normalize_timeout_seconds
from polaris.kernelone.storage.io_paths import build_cache_root, resolve_artifact_path

from .jetstream_utils import publish_to_jetstream

router = APIRouter()
log = logging.getLogger("polaris.routers.docs")

_DOCS_FIELD_KEYS = (
    "goal",
    "in_scope",
    "out_of_scope",
    "constraints",
    "definition_of_done",
    "backlog",
)
_DOCS_PREVIEW_LLM_TIMEOUT_SECONDS = 75.0
_create_docs_internal_task = asyncio.create_task
_BACKGROUND_DOCS_JETSTREAM_TASKS: set[asyncio.Task[None]] = set()
_SAFE_DOCS_EVENT_ID_PATTERN = re.compile(r"[^a-zA-Z0-9._-]+")
_DOCS_INIT_ACTIVE_SUFFIXES = frozenset(
    {
        "product/requirements.md",
        "product/plan.md",
        "product/interface_contract.md",
        "product/constraints.md",
    }
)


def _safe_docs_event_id(raw_value: str | None, prefix: str) -> str:
    raw = str(raw_value or "").strip() or f"{prefix}-{uuid4().hex}"
    safe = _SAFE_DOCS_EVENT_ID_PATTERN.sub("-", raw).strip(".-_")
    return safe[:96] or f"{prefix}-{uuid4().hex}"


def _track_docs_jetstream_task(task: asyncio.Task[None]) -> None:
    _BACKGROUND_DOCS_JETSTREAM_TASKS.add(task)

    def _discard(done: asyncio.Task[None]) -> None:
        _BACKGROUND_DOCS_JETSTREAM_TASKS.discard(done)
        try:
            done.result()
        except asyncio.CancelledError:
            return
        except (RuntimeError, ValueError, OSError, ConnectionError, TimeoutError) as exc:
            log.warning("docs init jetstream task failed: %s", exc)

    task.add_done_callback(_discard)


def _docs_init_channel(stream_name: str, session_id: str) -> str:
    return f"docs-init-{stream_name}:{session_id}"


def _docs_init_subject(stream_name: str, session_id: str) -> str:
    return f"hp.runtime.docs.init.{stream_name}.{session_id}"


async def _publish_docs_init_chunk(
    *,
    stream_name: str,
    session_id: str,
    chunk: dict[str, Any],
    seq: int,
) -> bool:
    envelope = create_runtime_event(
        workspace_key="docs",
        run_id=session_id,
        channel=_docs_init_channel(stream_name, session_id),
        kind=f"docs.init.{stream_name}.chunk",
        payload={
            "type": str(chunk.get("type") or "message"),
            "data": dict(chunk.get("data") or {}),
            "seq": int(seq),
        },
        meta={"source": f"docs_init_{stream_name}_jetstream"},
    )
    return await publish_to_jetstream(
        subject=_docs_init_subject(stream_name, session_id),
        payload=envelope.to_dict(),
    )


async def _drain_docs_init_queue_to_jetstream(
    *,
    stream_name: str,
    session_id: str,
    producer: asyncio.Task[None],
    queue: asyncio.Queue[dict[str, Any]],
    timeout: float = 180.0,
) -> None:
    seq = 0
    terminal_seen = False
    producer_finished = asyncio.Event()
    producer.add_done_callback(lambda _task: producer_finished.set())
    await _publish_docs_init_chunk(
        stream_name=stream_name,
        session_id=session_id,
        chunk={"type": "start", "data": {"session_id": session_id}},
        seq=seq,
    )
    seq += 1
    try:
        while True:
            if producer.done():
                try:
                    chunk = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            else:
                next_chunk = asyncio.ensure_future(queue.get())
                producer_done = asyncio.ensure_future(producer_finished.wait())
                done, _ = await asyncio.wait({producer_done, next_chunk}, return_when=asyncio.FIRST_COMPLETED)
                if producer_done in done and next_chunk not in done:
                    next_chunk.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await next_chunk
                    producer_done.result()
                    continue
                producer_done.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await producer_done
                chunk = next_chunk.result()

            if not isinstance(chunk, dict):
                continue
            await _publish_docs_init_chunk(
                stream_name=stream_name,
                session_id=session_id,
                chunk=chunk,
                seq=seq,
            )
            seq += 1
            if str(chunk.get("type") or "") in {"complete", "error"}:
                terminal_seen = True
                break
        await asyncio.wait_for(producer, timeout=timeout)
    except (RuntimeError, ValueError, OSError, ConnectionError, TimeoutError) as exc:
        log.warning("docs init %s jetstream execution failed: %s", stream_name, exc)
        await _publish_docs_init_chunk(
            stream_name=stream_name,
            session_id=session_id,
            chunk={"type": "error", "data": {"error": str(exc) or type(exc).__name__}},
            seq=seq,
        )
        terminal_seen = True
    finally:
        if not producer.done():
            producer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await producer
        if not terminal_seen:
            await _publish_docs_init_chunk(
                stream_name=stream_name,
                session_id=session_id,
                chunk={"type": "error", "data": {"error": "stream completed without a terminal event"}},
                seq=seq,
            )


def _docs_apply_active_rel_path(rel_path: str, target_root: str) -> str:
    normalized_target = normalize_rel_path(target_root).replace("\\", "/").rstrip("/")
    normalized_path = normalize_rel_path(rel_path).replace("\\", "/")
    if not normalized_target or not normalized_path.startswith(f"{normalized_target}/"):
        return ""

    suffix = normalized_path[len(normalized_target) + 1 :].strip("/")
    if suffix not in _DOCS_INIT_ACTIVE_SUFFIXES:
        return ""
    return f"workspace/docs/{suffix}"


def _materialize_active_docs_from_apply_payload(
    *,
    workspace: str,
    cache_root: str,
    target_root: str,
    files: list[DocsInitFile],
    created: list[str],
) -> None:
    seen = {path.replace("\\", "/") for path in created}
    for item in files:
        active_rel = _docs_apply_active_rel_path(item.path, target_root)
        if not active_rel or active_rel in seen:
            continue
        if not is_safe_docs_path(active_rel, "workspace/docs"):
            raise StructuredHTTPException(
                status_code=400,
                code="INVALID_DOCS_PATH",
                message="invalid docs path",
            )
        try:
            full_path = resolve_artifact_path(workspace, cache_root, active_rel)
        except (RuntimeError, ValueError) as e:
            raise StructuredHTTPException(
                status_code=400,
                code="INVALID_DOCS_PATH",
                message="invalid docs path",
            ) from e
        write_text_atomic(full_path, item.content or "")
        created.append(active_rel)
        seen.add(active_rel)


def _sync_plan_to_runtime(workspace: str, cache_root: str) -> None:
    """Copy docs-init contracts to runtime so PM loop picks them up automatically."""
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

        requirements_src_candidates = [
            resolve_artifact_path(workspace, cache_root, "workspace/docs/product/requirements.md"),
            os.path.join(workspace, "docs", "product", "requirements.md"),
        ]
        requirements_src = ""
        for candidate in requirements_src_candidates:
            if candidate and os.path.isfile(candidate):
                requirements_src = candidate
                break
        if requirements_src:
            with open(requirements_src, encoding="utf-8") as f:
                requirements_content = f.read()
            requirements_dst = resolve_artifact_path(workspace, cache_root, "runtime/contracts/requirements.md")
            write_text_atomic(requirements_dst, requirements_content)
            log.info("REQUIREMENTS_SYNC_OK: %s -> runtime/contracts/requirements.md", requirements_src)

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
        raise StructuredHTTPException(
            status_code=409,
            code="ARCHITECT_NOT_CONFIGURED",
            message="Architect角色未配置，请先在 LLM 设置中完成角色绑定。",
        )

    provider_id = str(role_cfg.get("provider_id") or "").strip()
    model = str(role_cfg.get("model") or "").strip()
    if not provider_id or not model:
        raise StructuredHTTPException(
            status_code=409,
            code="ARCHITECT_NOT_CONFIGURED",
            message="Architect角色缺少 provider_id/model，请先完成角色绑定。",
        )

    providers = config.get("providers") or {}
    provider_cfg = providers.get(provider_id) if isinstance(providers.get(provider_id), dict) else None
    if not isinstance(provider_cfg, dict):
        raise StructuredHTTPException(
            status_code=409,
            code="ARCHITECT_NOT_CONFIGURED",
            message="Architect绑定的提供商不存在",
        )

    provider_type = str(provider_cfg.get("type") or "").strip().lower()
    if not provider_type:
        raise StructuredHTTPException(
            status_code=409,
            code="ARCHITECT_NOT_CONFIGURED",
            message="Architect绑定的提供商缺少 type",
        )

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


def _build_fields(payload: DocsInitDialoguePayload | DocsInitSuggestPayload | DocsInitPreviewPayload) -> dict[str, str]:
    return {
        "goal": payload.goal or "",
        "in_scope": payload.in_scope or "",
        "out_of_scope": payload.out_of_scope or "",
        "constraints": payload.constraints or "",
        "definition_of_done": payload.definition_of_done or "",
        "backlog": payload.backlog or "",
    }


def _merge_ai_fields(fields: dict[str, str], ai_fields: dict[str, list[str]]) -> None:
    for key in _DOCS_FIELD_KEYS:
        values = ai_fields.get(key) or []
        if values:
            fields[key] = "\n".join(values)


async def _resolve_docs_preview_ai_fields(
    *,
    queue: asyncio.Queue[dict[str, Any]],
    workspace: str,
    settings: Any,
    fields: dict[str, str],
    timeout_seconds: float = _DOCS_PREVIEW_LLM_TIMEOUT_SECONDS,
) -> tuple[dict[str, list[str]], bool]:
    """Resolve Docs Init AI fields with a bounded fallback path."""
    ai_fields: dict[str, list[str]] | None = None
    collected_thinking = ""
    fallback_reason = "no result"

    try:
        async with asyncio.timeout(timeout_seconds):
            async for event in generate_docs_fields_stream(workspace, settings, fields):
                event_type = event.get("type")
                if event_type == "thinking":
                    content = str(event.get("content") or "")
                    if content:
                        collected_thinking += content
                        await queue.put(
                            {
                                "type": "thinking",
                                "data": {"content": content, "accumulated": collected_thinking},
                            }
                        )
                elif event_type == "result":
                    candidate = event.get("fields")
                    if isinstance(candidate, dict):
                        ai_fields = {
                            str(key): [str(item) for item in value]
                            for key, value in candidate.items()
                            if isinstance(value, list)
                        }
                    break
                elif event_type == "error":
                    fallback_reason = str(event.get("error") or "stream error")
                    break
    except TimeoutError:
        fallback_reason = f"timeout after {timeout_seconds:g}s"
    except Exception as exc:  # noqa: BLE001 - provider failures must fall back instead of hanging preview.
        fallback_reason = str(exc) or type(exc).__name__

    if ai_fields:
        return ai_fields, False

    log.warning("[docs] preview LLM fallback activated: %s", fallback_reason)
    fallback_fields = build_default_docs_fields(fields)
    await queue.put(
        {
            "type": "stage",
            "data": {
                "stage": "llm_fallback",
                "message": "Architect LLM unavailable; using deterministic docs fallback.",
                "progress": 60,
                "fields": fallback_fields,
                "fallback": True,
            },
        }
    )
    return fallback_fields, True


def _build_history(payload: DocsInitDialoguePayload) -> list[dict[str, Any]]:
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
    return history


async def _docs_init_dialogue_core(request: Request, payload: DocsInitDialoguePayload) -> DocsInitDialogueResponse:
    state = get_state(request)
    workspace = state.settings.workspace
    workspace_str = str(workspace) if not isinstance(workspace, str) else workspace
    _bind_docs_wizard_llm_from_architect_role(state)

    fields = _build_fields(payload)
    history = _build_history(payload)

    result = await generate_docs_dialogue_turn(
        workspace=workspace_str,
        settings=state.settings,
        fields=fields,
        history=history,
        message=str(payload.message or ""),
    )
    if not result:
        raise StructuredHTTPException(
            status_code=409,
            code="ARCHITECT_NOT_CONFIGURED",
            message="Architect role LLM Dialogue failed: output may be truncated or format mismatch; please check max_tokens, model output format, and network connectivity.",
        )

    result_fields = result.get("fields")
    out_fields: dict[str, Any] = result_fields if isinstance(result_fields, dict) else {}
    return DocsInitDialogueResponse(
        ok=True,
        reply=str(result.get("reply") or ""),
        questions=result.get("questions") or [],
        tiaochen=result.get("tiaochen") or [],
        meta=result.get("meta") or {},
        handoffs=result.get("handoffs") or {},
        fields={
            "goal": _join_lines(out_fields.get("goal") or ""),
            "in_scope": _join_lines(out_fields.get("in_scope") or ""),
            "out_of_scope": _join_lines(out_fields.get("out_of_scope") or ""),
            "constraints": _join_lines(out_fields.get("constraints") or ""),
            "definition_of_done": _join_lines(out_fields.get("definition_of_done") or ""),
            "backlog": _join_lines(out_fields.get("backlog") or ""),
        },
    )


@router.post("/v2/docs/init/dialogue", dependencies=[Depends(require_auth)], response_model=DocsInitDialogueResponse)
async def docs_init_dialogue_v2(request: Request, payload: DocsInitDialoguePayload) -> DocsInitDialogueResponse:
    """Interactive docs wizard dialogue turn (non-streaming)."""
    return await _docs_init_dialogue_core(request, payload)


async def _run_docs_init_dialogue_jetstream(
    *,
    settings: Any,
    workspace: str,
    fields: dict[str, str],
    history: list[dict[str, Any]],
    message: str,
    session_id: str,
) -> None:
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=50)
    producer = _create_docs_internal_task(
        generate_docs_dialogue_turn_streaming(
            workspace=workspace,
            settings=settings,
            fields=fields,
            history=history,
            message=message,
            output_queue=queue,
        )
    )
    await _drain_docs_init_queue_to_jetstream(
        stream_name="dialogue",
        session_id=session_id,
        producer=producer,
        queue=queue,
    )


@router.post("/v2/docs/init/dialogue/jetstream", dependencies=[Depends(require_auth)])
async def docs_init_dialogue_jetstream_v2(request: Request, payload: DocsInitDialoguePayload) -> dict[str, Any]:
    """Start docs wizard dialogue and publish chunks through runtime Nats-JetStream."""
    state = get_state(request)
    workspace = state.settings.workspace
    workspace_str = str(workspace) if not isinstance(workspace, str) else workspace
    _bind_docs_wizard_llm_from_architect_role(state)

    session_id = _safe_docs_event_id(payload.session_id, "docs-dialogue")
    task = asyncio.create_task(
        _run_docs_init_dialogue_jetstream(
            settings=state.settings,
            workspace=workspace_str,
            fields=_build_fields(payload),
            history=_build_history(payload),
            message=str(payload.message or ""),
            session_id=session_id,
        )
    )
    _track_docs_jetstream_task(task)
    return {
        "ok": True,
        "session_id": session_id,
        "status": "started",
        "channel": _docs_init_channel("dialogue", session_id),
        "subject": _docs_init_subject("dialogue", session_id),
        "transport": "nats-jetstream",
    }


async def _docs_init_suggest_core(request: Request, payload: DocsInitSuggestPayload) -> DocsInitSuggestResponse:
    state = get_state(request)
    workspace = state.settings.workspace
    workspace_str = str(workspace) if not isinstance(workspace, str) else workspace
    _bind_docs_wizard_llm_from_architect_role(state)
    fields = _build_fields(payload)
    ai_fields = await generate_docs_ai_fields(workspace_str, state.settings, fields)
    if not ai_fields:
        raise StructuredHTTPException(
            status_code=409,
            code="ARCHITECT_NOT_CONFIGURED",
            message="Architect角色 LLM 不可用，请检查 provider/model 与网络连通性。",
        )
    return DocsInitSuggestResponse(
        ok=True,
        fields={
            "goal": "\n".join(ai_fields.get("goal") or []),
            "in_scope": "\n".join(ai_fields.get("in_scope") or []),
            "out_of_scope": "\n".join(ai_fields.get("out_of_scope") or []),
            "constraints": "\n".join(ai_fields.get("constraints") or []),
            "definition_of_done": "\n".join(ai_fields.get("definition_of_done") or []),
            "backlog": "\n".join(ai_fields.get("backlog") or []),
        },
    )


@router.post("/v2/docs/init/suggest", dependencies=[Depends(require_auth)], response_model=DocsInitSuggestResponse)
async def docs_init_suggest_v2(request: Request, payload: DocsInitSuggestPayload) -> DocsInitSuggestResponse:
    """Suggest docs fields using the Architect LLM."""
    return await _docs_init_suggest_core(request, payload)


async def _docs_init_preview_core(request: Request, payload: DocsInitPreviewPayload) -> DocsInitPreviewResponse:
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
    fields = _build_fields(payload)
    ai_fields = await generate_docs_ai_fields(workspace_str, state.settings, fields)
    if not ai_fields:
        raise StructuredHTTPException(
            status_code=409,
            code="ARCHITECT_NOT_CONFIGURED",
            message="Architect角色 LLM 不可用，请检查 provider/model 与网络连通性。",
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
    docs_map = build_docs_templates(workspace_str, mode, fields, qa_commands)
    target_root = select_docs_target_root(workspace_str)
    from polaris.delivery.http.schemas.common import DocsInitPreviewFile

    files: list[DocsInitPreviewFile] = []
    for rel_path, content in docs_map.items():
        suffix = rel_path.replace("docs/", "", 1)
        target_path = target_root.rstrip("/") + "/" + suffix if target_root != "docs" else rel_path
        full_path = resolve_artifact_path(workspace_str, cache_root, normalize_rel_path(target_path))
        files.append(
            DocsInitPreviewFile(
                path=target_path.replace("\\", "/"),
                content=content,
                exists=os.path.isfile(full_path),
            )
        )
    return DocsInitPreviewResponse(
        ok=True,
        mode=mode,
        target_root=target_root,
        docs_exists=workspace_has_docs(workspace_str),
        project=profile,
        files=files,
    )


@router.post("/v2/docs/init/preview", dependencies=[Depends(require_auth)], response_model=DocsInitPreviewResponse)
async def docs_init_preview_v2(request: Request, payload: DocsInitPreviewPayload) -> DocsInitPreviewResponse:
    """Preview generated docs artifacts before applying."""
    return await _docs_init_preview_core(request, payload)


async def _generate_docs_init_preview_events(
    *,
    queue: asyncio.Queue[dict[str, Any]],
    state: AppState,
    workspace: str,
    payload: DocsInitPreviewPayload,
) -> None:
    cache_root = build_cache_root(state.settings.ramdisk_root or "", workspace)
    try:
        await queue.put({"type": "stage", "data": {"stage": "init", "message": "初始化文档生成环境...", "progress": 5}})
        _bind_docs_wizard_llm_from_architect_role(state)
        mode = str(payload.mode or "minimal").strip().lower()
        if mode not in ("minimal",):
            mode = "minimal"

        await queue.put({"type": "stage", "data": {"stage": "detect", "message": "检测项目配置...", "progress": 10}})
        profile = detect_project_profile(workspace)
        qa_commands = default_qa_commands(profile)

        fields = _build_fields(payload)
        await queue.put(
            {"type": "stage", "data": {"stage": "llm_start", "message": "Architect正在分析需求...", "progress": 20}}
        )

        ai_fields, used_fallback = await _resolve_docs_preview_ai_fields(
            queue=queue,
            workspace=workspace,
            settings=state.settings,
            fields=fields,
        )

        await queue.put(
            {
                "type": "stage",
                "data": {
                    "stage": "llm_done",
                    "message": "需求分析完成（降级模板）" if used_fallback else "需求分析完成",
                    "progress": 65 if used_fallback else 60,
                    "fields": ai_fields,
                    "fallback": used_fallback,
                },
            }
        )

        await queue.put(
            {"type": "stage", "data": {"stage": "apply_fields", "message": "整理生成结果...", "progress": 70}}
        )
        _merge_ai_fields(fields, ai_fields)

        await queue.put(
            {"type": "stage", "data": {"stage": "build_templates", "message": "构建文档模板...", "progress": 80}}
        )
        docs_map = build_docs_templates(workspace, mode, fields, qa_commands)
        target_root = select_docs_target_root(workspace)

        await queue.put(
            {"type": "stage", "data": {"stage": "prepare_files", "message": "准备文件列表...", "progress": 90}}
        )
        files: list[dict[str, Any]] = []
        for rel_path, content in docs_map.items():
            suffix = rel_path.replace("docs/", "", 1)
            target_path = target_root.rstrip("/") + "/" + suffix if target_root != "docs" else rel_path
            full_path = resolve_artifact_path(workspace, cache_root, normalize_rel_path(target_path))
            files.append(
                {
                    "path": target_path.replace("\\", "/"),
                    "content": content,
                    "exists": os.path.isfile(full_path),
                }
            )

        await queue.put(
            {
                "type": "complete",
                "data": {
                    "ok": True,
                    "mode": mode,
                    "target_root": target_root,
                    "docs_exists": workspace_has_docs(workspace),
                    "project": profile,
                    "files": files,
                    "progress": 100,
                },
            }
        )
    except (RuntimeError, ValueError) as exc:
        log.exception("Preview stream error")
        await queue.put({"type": "error", "data": {"error": str(exc)}})


async def _run_docs_init_preview_jetstream(
    *,
    state: AppState,
    workspace: str,
    payload: DocsInitPreviewPayload,
    session_id: str,
) -> None:
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=50)
    producer = _create_docs_internal_task(
        _generate_docs_init_preview_events(
            queue=queue,
            state=state,
            workspace=workspace,
            payload=payload,
        )
    )
    await _drain_docs_init_queue_to_jetstream(
        stream_name="preview",
        session_id=session_id,
        producer=producer,
        queue=queue,
        timeout=180.0,
    )


@router.post("/v2/docs/init/preview/jetstream", dependencies=[Depends(require_auth)])
async def docs_init_preview_jetstream_v2(request: Request, payload: DocsInitPreviewPayload) -> dict[str, Any]:
    """Start docs preview generation and publish chunks through runtime Nats-JetStream."""
    state = get_state(request)
    workspace = state.settings.workspace
    workspace_str = str(workspace) if not isinstance(workspace, str) else workspace
    session_id = _safe_docs_event_id(payload.session_id, "docs-preview")
    task = asyncio.create_task(
        _run_docs_init_preview_jetstream(
            state=state,
            workspace=workspace_str,
            payload=payload,
            session_id=session_id,
        )
    )
    _track_docs_jetstream_task(task)
    return {
        "ok": True,
        "session_id": session_id,
        "status": "started",
        "channel": _docs_init_channel("preview", session_id),
        "subject": _docs_init_subject("preview", session_id),
        "transport": "nats-jetstream",
    }


def _docs_init_apply_core(request: Request, payload: DocsInitApplyPayload) -> DocsInitApplyResponse:
    state = get_state(request)
    workspace = state.settings.workspace
    workspace_str = str(workspace) if not isinstance(workspace, str) else workspace
    cache_root = build_cache_root(state.settings.ramdisk_root or "", workspace_str)
    target_root = normalize_rel_path(payload.target_root or "workspace/docs")
    if not target_root or not target_root.lower().startswith("workspace/docs"):
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_DOCS_PATH",
            message="target_root must be under workspace/docs/",
        )
    files = payload.files or []
    if not files:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_REQUEST",
            message="no files to write",
        )
    created: list[str] = []
    for item in files:
        rel_path = normalize_rel_path(item.path)
        if not is_safe_docs_path(rel_path, target_root):
            raise StructuredHTTPException(
                status_code=400,
                code="INVALID_DOCS_PATH",
                message="invalid docs path",
            )
        try:
            full_path = resolve_artifact_path(workspace_str, cache_root, rel_path)
        except (RuntimeError, ValueError) as e:
            raise StructuredHTTPException(
                status_code=400,
                code="INVALID_DOCS_PATH",
                message="invalid docs path",
            ) from e
        write_text_atomic(full_path, item.content or "")
        created.append(rel_path.replace("\\", "/"))
    _materialize_active_docs_from_apply_payload(
        workspace=workspace_str,
        cache_root=cache_root,
        target_root=target_root,
        files=files,
        created=created,
    )
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
    return DocsInitApplyResponse(ok=True, files=created)


@router.post("/v2/docs/init/apply", dependencies=[Depends(require_auth)], response_model=DocsInitApplyResponse)
def docs_init_apply_v2(request: Request, payload: DocsInitApplyPayload) -> DocsInitApplyResponse:
    """Apply generated docs artifacts to the workspace."""
    return _docs_init_apply_core(request, payload)
