"""Architect stage execution for orchestration."""

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

from polaris.infrastructure.compat.io_utils import (
    resolve_artifact_path,
    workspace_has_docs,
    write_json_atomic,
)

from .directive_processing import (
    _DEFAULT_BACKLOG_ITEMS,
    _build_backlog_from_directive,
    _collect_clean_lines,
    _contains_prompt_leakage,
    _extract_project_goal_from_directive,
    _sanitize_fields_for_templates,
)
from .doc_rendering import (
    _normalize_doc_markdown,
    _render_llm_authored_docs,
)
from .docs_pipeline import (
    _auto_initialize_docs,
    _sync_plan_to_runtime,
    _write_architect_docs_pipeline,
)
from .helpers import (
    _ARCHITECT_READY_REL,
    _load_cli_directive,
    _resolve_docs_init_mode,
    _role_llm_docs_required,
    _role_llm_fields_enabled,
)

logger = logging.getLogger(__name__)


def run_architect_docs_stage(
    workspace_full: str,
    cache_root_full: str,
    args: Any,
    *,
    directive_text: str = "",
) -> int:
    """Run architect docs generation stage."""
    directive = str(directive_text or "").strip() or _load_cli_directive(args)
    if not directive:
        logger.info(
            "[architect] Empty directive. Provide --directive/--directive-file/--directive-stdin for architect stage."
        )
        return 2
    goal_seed = _extract_project_goal_from_directive(directive)
    if not goal_seed:
        fallback_goal = _collect_clean_lines(directive, limit=1)
        goal_seed = fallback_goal[0] if fallback_goal else "定义可验证的项目交付目标与验收链路"

    try:
        from polaris.bootstrap.config import Settings
        from polaris.cells.llm.dialogue.public import generate_docs_fields
        from polaris.cells.runtime.projection.public.service import write_text_atomic
        from polaris.cells.workspace.integrity.public.service import (
            build_docs_templates,
            default_qa_commands,
            detect_project_profile,
            normalize_rel_path,
            select_docs_target_root,
        )
        from polaris.kernelone.storage.io_paths import (
            resolve_artifact_path as app_resolve_artifact_path,
        )
    except (RuntimeError, ValueError) as exc:
        logger.error(f"[architect] Failed to import docs generation modules: {exc}")
        return 1

    backlog_seed = _build_backlog_from_directive(directive)
    backlog_items = [str(item or "").strip() for item in backlog_seed.splitlines() if str(item or "").strip()]
    if not backlog_items:
        backlog_items = list(_DEFAULT_BACKLOG_ITEMS)
    in_scope_seed = "\n".join(f"- {item}" for item in backlog_items[:8])
    fields_input: dict[str, str] = {
        "goal": goal_seed,
        "in_scope": in_scope_seed,
        "out_of_scope": "- 未在需求中明确要求的扩展功能",
        "constraints": (
            "- 所有文本文件读写必须显式使用 UTF-8\n"
            "- 顺序执行，禁止并发运行多个测试项目\n"
            "- 运行证据统一写入 Polaris runtime 目录"
        ),
        "definition_of_done": (
            "- 至少包含可运行入口、核心业务模块、测试文件\n"
            "- 关键验证命令执行通过并产生可追溯证据\n"
            "- 交付内容与需求目标一致且无占位符实现"
        ),
        "backlog": backlog_seed,
    }

    settings = Settings()
    try:
        settings.workspace = workspace_full  # type: ignore[assignment]
    except (RuntimeError, ValueError) as e:
        logger.debug(f"Failed to set workspace: {e}")

    ai_fields = None
    if _role_llm_fields_enabled():
        try:
            import asyncio

            # 检测是否已有运行中的event loop，避免RuntimeError
            try:
                asyncio.get_running_loop()
                # 已有running loop，创建新loop在单独线程执行
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(
                        asyncio.run,
                        generate_docs_fields(workspace_full, settings, fields_input),
                    )
                    ai_fields = future.result(timeout=120)
            except RuntimeError:
                # 无running loop，直接使用asyncio.run
                ai_fields = asyncio.run(generate_docs_fields(workspace_full, settings, fields_input))
        except (RuntimeError, ValueError) as exc:
            logger.warning(f"[architect] Failed to generate AI fields: {exc}")
    fields_for_templates: dict[str, str] = dict(fields_input)
    if isinstance(ai_fields, dict):
        for key in (
            "goal",
            "in_scope",
            "out_of_scope",
            "constraints",
            "definition_of_done",
            "backlog",
        ):
            value = ai_fields.get(key)
            if isinstance(value, list):
                text = "\n".join(str(item).strip() for item in value if str(item).strip())
            else:
                text = str(value or "").strip()
            if text:
                fields_for_templates[key] = text

    fields_for_templates = _sanitize_fields_for_templates(fields_for_templates)

    profile = detect_project_profile(workspace_full) or {}
    qa_commands = default_qa_commands(
        profile,
        hint_text=str(fields_for_templates.get("goal") or goal_seed),
    )
    template_docs_map = build_docs_templates(
        workspace_full,
        "minimal",
        fields_for_templates,
        qa_commands,
    )
    docs_map = dict(template_docs_map)
    docs_map, llm_docs_meta = _render_llm_authored_docs(
        workspace_full=workspace_full,
        docs_map=docs_map,
        fields=fields_for_templates,
        qa_commands=qa_commands,
        fallback_model=str(getattr(settings, "model", "") or ""),
    )
    if _role_llm_docs_required():
        attempted_docs = int(llm_docs_meta.get("attempted") or 0)
        accepted_docs = int(llm_docs_meta.get("accepted") or 0)
        if attempted_docs <= 0 or accepted_docs < attempted_docs:
            logger.warning(
                "[architect] LLM-authored docs required, but generation is incomplete "
                + f"(accepted={accepted_docs}, attempted={attempted_docs}). "
                "Disable strict mode or fix provider/timeout and retry."
            )
            return 2
    leakage_blocked_docs: list[str] = []
    for rel_path, generated in list(docs_map.items()):
        normalized = _normalize_doc_markdown(generated)
        if _contains_prompt_leakage(normalized):
            fallback_doc = _normalize_doc_markdown(template_docs_map.get(rel_path, ""))
            normalized = fallback_doc
            leakage_blocked_docs.append(rel_path)
        docs_map[rel_path] = normalized
    if leakage_blocked_docs and isinstance(llm_docs_meta, dict):
        llm_docs_meta["leakage_blocked_docs"] = leakage_blocked_docs

    target_root = select_docs_target_root(workspace_full)

    created: list[str] = []
    for rel_path, content in docs_map.items():
        suffix = rel_path.replace("docs/", "", 1) if rel_path.startswith("docs/") else rel_path
        target_path = target_root.rstrip("/") + "/" + suffix if target_root != "docs" else rel_path
        normalized_path = normalize_rel_path(target_path)
        full_path = app_resolve_artifact_path(workspace_full, cache_root_full, normalized_path)
        write_text_atomic(full_path, content)
        created.append(normalized_path.replace("\\", "/"))

    _sync_plan_to_runtime(workspace_full, cache_root_full)
    docs_pipeline_meta = _write_architect_docs_pipeline(
        workspace_full,
        cache_root_full,
        created,
    )

    marker_path = resolve_artifact_path(workspace_full, cache_root_full, _ARCHITECT_READY_REL)
    write_json_atomic(
        marker_path,
        {
            "schema_version": 1,
            "status": "ready",
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": "loop-pm:architect-stage",
            "docs_files": created,
            "docs_pipeline_path": str(docs_pipeline_meta.get("pipeline_path") or ""),
            "docs_pipeline_stage_count": int(docs_pipeline_meta.get("stage_count") or 0),
            "directive_sha256": hashlib.sha256(directive.encode("utf-8")).hexdigest(),
            "llm_docs": llm_docs_meta,
        },
    )
    logger.info(
        f"[architect] docs generated: {len(created)} files; "
        + f"llm_docs={int(llm_docs_meta.get('accepted') or 0)}/"
        + f"{int(llm_docs_meta.get('attempted') or 0)}; "
        f"stages={int(docs_pipeline_meta.get('stage_count') or 0)}; marker={marker_path}"
    )
    return 0


def ensure_docs_ready(workspace_full: str) -> int | None:
    """Ensure docs directory exists."""
    from polaris.cells.workspace.integrity.public.service import (
        clear_workspace_status,
        write_workspace_status,
    )

    if not workspace_has_docs(workspace_full):
        if _resolve_docs_init_mode() == "auto":
            try:
                docs_root = _auto_initialize_docs(workspace_full)
                clear_workspace_status(workspace_full)
                logger.info(f"[workspace] docs/ missing at {workspace_full}; auto-initialized docs at {docs_root}.")
                return None
            except (RuntimeError, ValueError) as exc:
                write_workspace_status(
                    workspace_full,
                    status="NEEDS_DOCS_INIT",
                    reason=f"docs/ directory not found and auto init failed: {exc}",
                    actions=["INIT_DOCS_WIZARD"],
                )
                logger.error(f"[workspace] docs/ missing at {workspace_full} and auto init failed: {exc}")
                return 2
        write_workspace_status(
            workspace_full,
            status="NEEDS_DOCS_INIT",
            reason="docs/ directory not found",
            actions=["INIT_DOCS_WIZARD"],
        )
        logger.info(f"[workspace] docs/ not found at {workspace_full}. Run docs init and retry.")
        return 2
    clear_workspace_status(workspace_full)
    return None


__all__ = [
    "ensure_docs_ready",
    "run_architect_docs_stage",
]
