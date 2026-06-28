"""Director 角色适配器核心类

实现 Director 角色的统一编排接口。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
from dataclasses import replace
from typing import Any

from polaris.cells.director.tasking.internal.execution_profile import resolve_director_execution_profile
from polaris.cells.director.tasking.internal.execution_strategy import (
    apply_execution_strategy_overrides,
    resolve_director_execution_strategy,
)
from polaris.cells.director.tasking.public.contracts import TaskExecutionProfileV1

from ..base import BaseRoleAdapter
from ..director_execution_backend import (
    DirectorExecutionBackendRequest,
    resolve_director_execution_backend,
)
from .adapter_sequential import (
    build_sequential_config,
    execute_hybrid,
    execute_sequential,
)
from .dialogue import get_settings_safe
from .execute_method import execute_director_task
from .execution import DirectorPatchExecutor
from .helpers import (
    _DEFAULT_LLM_CALL_TIMEOUT_SECONDS,
    is_empty_role_response,
    taskboard_snapshot_brief,
)
from .state_tracking import DirectorStateTracker
from .state_utils import (
    compose_projection_requirement,
    default_projection_slug,
)

logger = logging.getLogger(__name__)


async def generate_role_response(
    *,
    workspace: str,
    settings: Any,
    role: str,
    message: str,
    context: dict[str, Any] | None = None,
    validate_output: bool = False,
    max_retries: int = 1,
    invoke_role_dialogue: Any = None,
    **_: Any,
) -> dict[str, Any]:
    """Compatibility-compatible role response invocation used by test monkeypatches.

    This wrapper keeps legacy module-level monkeypatch points alive while the
    adapter can still route through runtime-first behavior by default.
    """
    del settings, validate_output  # preserve compatibility signature

    invoke_strategy = invoke_role_dialogue
    if callable(invoke_strategy):
        return _normalize_director_role_response(await invoke_strategy(message=message, context=context))

    # Fallback for non-fallback-callers: route directly through Role Runtime
    # to keep behavior aligned with adapter policy and avoid drift from runtime path.
    from polaris.cells.roles.adapters.internal.runtime_dialogue import invoke_role_runtime_first

    runtime_payload = await invoke_role_runtime_first(
        workspace=workspace,
        role=role,
        message=message,
        context=context,
        validate_output=False,
        max_retries=max_retries,
    )
    return _normalize_director_role_response(runtime_payload)


def _copy_mapping_payload(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        if isinstance(dumped, dict):
            return dict(dumped)
    return None


def _copy_dict_list_payload(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _first_mapping_payload(*values: Any) -> dict[str, Any] | None:
    for value in values:
        copied = _copy_mapping_payload(value)
        if copied:
            return copied
    return None


def _first_dict_list_payload(*values: Any) -> list[dict[str, Any]]:
    for value in values:
        copied = _copy_dict_list_payload(value)
        if copied:
            return copied
    return []


def _string_list_payload(value: Any, *, limit: int = 8) -> list[str]:
    if isinstance(value, str):
        values = [part.strip() for part in value.split(",") if part.strip()] or (
            [value.strip()] if value.strip() else []
        )
    elif isinstance(value, (list, tuple)):
        values = [str(item or "").strip() for item in value if str(item or "").strip()]
    else:
        values = []
    return values[: max(int(limit), 0)]


def _join_limited_values(label: str, values: list[str]) -> str:
    return f"- {label}: {', '.join(values)}" if values else ""


def _path_looks_like_test_target(path: str) -> bool:
    normalized = str(path or "").replace("\\", "/").strip().lower()
    filename = normalized.rsplit("/", 1)[-1]
    return bool(
        normalized.startswith("tests/")
        or "/tests/" in normalized
        or ".test." in filename
        or ".spec." in filename
        or filename.startswith("test_")
        or "_test." in filename
        or filename.endswith("test.java")
    )


_TASK_CONTRACT_LIST_KEYS = (
    "target_files",
    "scope_paths",
    "context_files",
    "acceptance",
    "acceptance_criteria",
    "steps",
    "execution_checklist",
    "depends_on",
)
_TASK_CONTRACT_MAPPING_KEYS = (
    "pm_contract",
    "ce_blueprint",
    "ce_handoff_decision",
    "handoff_decision",
    "job_token",
    "control_plane_job_token",
    "capability_token",
)
_TASK_CONTRACT_SCALAR_KEYS = (
    "title",
    "subject",
    "description",
    "goal",
    "objective",
    "phase",
    "project_type",
    "language",
    "domain",
    "backlog_ref",
    "task_id",
    "pm_task_id",
    "source_task_id",
    "external_task_id",
    "blueprint_id",
    "chief_engineer_blueprint_id",
    "chief_engineer_handoff_id",
    "blueprint_path",
    "runtime_blueprint_path",
    "pm_contract_hash",
    "contract_hash",
    "pm_contract_ref",
    "blueprint_hash",
    "ce_blueprint_hash",
    "ce_blueprint_ref",
    "handoff_decision_hash",
    "ce_handoff_decision_hash",
    "handoff_decision_ref",
    "ce_handoff_decision_ref",
    "handoff_source",
)


def _task_contract_sources(task: dict[str, Any]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    if isinstance(task, dict):
        sources.append(task)
        metadata = task.get("metadata")
        if isinstance(metadata, dict):
            sources.append(metadata)
            nested = metadata.get("metadata")
            if isinstance(nested, dict):
                sources.append(nested)
    return sources


def _has_contract_value(payload: dict[str, Any], key: str) -> bool:
    value = payload.get(key)
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _first_contract_value(sources: list[dict[str, Any]], key: str) -> Any:
    for source in sources:
        if not isinstance(source, dict):
            continue
        value = source.get(key)
        if isinstance(value, str):
            normalized = value.strip()
            if normalized:
                return normalized
            continue
        if isinstance(value, (list, tuple, set)):
            normalized_list = [str(item).strip() for item in value if str(item or "").strip()]
            if normalized_list:
                return normalized_list
            continue
        if isinstance(value, dict):
            if value:
                return dict(value)
            continue
        if value is not None:
            return value
    return None


def _promoted_task_contract_payload(sources: list[dict[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in (*_TASK_CONTRACT_LIST_KEYS, *_TASK_CONTRACT_MAPPING_KEYS, *_TASK_CONTRACT_SCALAR_KEYS):
        value = _first_contract_value(sources, key)
        if value is not None:
            payload[key] = value

    subject = str(payload.get("subject") or "").strip()
    title = str(payload.get("title") or "").strip()
    if subject and not title:
        payload["title"] = subject
    elif title and not subject:
        payload["subject"] = title

    goal = str(payload.get("goal") or "").strip()
    objective = str(payload.get("objective") or "").strip()
    if goal and not objective:
        payload["objective"] = goal
    elif objective and not goal:
        payload["goal"] = objective
    return payload


def _normalize_contract_task_token(value: Any) -> str:
    token = str(value or "").strip().lower()
    token = re.sub(r"^(task[-_])+", "", token)
    return token


def _contract_list(value: Any) -> list[str]:
    if isinstance(value, str):
        normalized = value.strip()
        return [normalized] if normalized else []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item or "").strip()]
    return []


def _merge_contract_lists(*values: Any) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for value in values:
        for item in _contract_list(value):
            if item in seen:
                continue
            seen.add(item)
            merged.append(item)
    return merged


def _load_ce_blueprint_contract_payload(workspace: str, task: dict[str, Any]) -> dict[str, Any]:
    if not workspace or not isinstance(task, dict):
        return {}
    sources = _task_contract_sources(task)
    task_tokens = {
        token
        for source in sources
        for value in (
            source.get("id"),
            source.get("task_id"),
            source.get("pm_task_id"),
            source.get("external_task_id"),
        )
        if (token := _normalize_contract_task_token(value))
    }
    explicit_blueprint_ids = [
        item
        for source in sources
        for item in _contract_list(
            source.get("blueprint_id")
            or source.get("chief_engineer_blueprint_id")
            or source.get("ce_blueprint_id")
            or source.get("runtime_blueprint_id")
        )
    ]
    try:
        from polaris.cells.chief_engineer.blueprint.public import BlueprintPersistence
    except (ImportError, RuntimeError):
        return {}
    persistence = BlueprintPersistence(workspace, ensure_directory=False)
    candidates: list[tuple[str, str, dict[str, Any]]] = []
    for blueprint_id in dict.fromkeys([*explicit_blueprint_ids, *persistence.list_all()]):
        payload = persistence.load(blueprint_id)
        if not isinstance(payload, dict):
            continue
        payload_task = _normalize_contract_task_token(payload.get("task_id"))
        if explicit_blueprint_ids and blueprint_id in explicit_blueprint_ids:
            pass
        elif (task_tokens and payload_task not in task_tokens) or (not task_tokens and not explicit_blueprint_ids):
            continue
        updated_at = str(payload.get("updated_at") or payload.get("created_at") or "").strip()
        candidates.append((updated_at, str(blueprint_id), payload))
    if not candidates:
        return {}
    _updated_at, _blueprint_id, payload = max(candidates, key=lambda item: (item[0], item[1]))
    return payload


def _merge_ce_blueprint_contract_payload(
    contract_payload: dict[str, Any],
    blueprint_payload: dict[str, Any],
) -> dict[str, Any]:
    if not blueprint_payload:
        return contract_payload
    merged = dict(contract_payload)
    for key in _TASK_CONTRACT_LIST_KEYS:
        values = _merge_contract_lists(contract_payload.get(key), blueprint_payload.get(key))
        if values:
            merged[key] = values
    for key in _TASK_CONTRACT_MAPPING_KEYS:
        if not _has_contract_value(merged, key) and isinstance(blueprint_payload.get(key), dict):
            merged[key] = dict(blueprint_payload[key])
    for key in _TASK_CONTRACT_SCALAR_KEYS:
        if not _has_contract_value(merged, key) and blueprint_payload.get(key) is not None:
            merged[key] = blueprint_payload[key]
    merged.setdefault("ce_blueprint", dict(blueprint_payload))
    return merged


def _director_actual_interface_injection_enabled() -> bool:
    """Default ON so Director consumes actual sibling interfaces before writing."""

    raw = str(os.environ.get("KERNELONE_DIRECTOR_INJECT_WORKSPACE_INTERFACE", "")).strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _build_director_workspace_interface_lines(workspace: str) -> list[str]:
    """Inject the ACTUAL exported symbols of already-generated workspace files.

    A consumer task (e.g. the CLI/engine that imports the models a prior task
    generated) otherwise only receives the CE's PREDICTED interface or a generic
    "read the existing files first" hint, so the model guesses symbol names
    (root#4 cross-file incoherence) or read-explores siblings at runtime (no-write
    retry batches). Bounded to names + kinds + signatures, never file bodies.
    Inert on error or empty workspace.
    """
    try:
        from polaris.kernelone.quality.cross_artifact_interfaces import build_symbol_index_snapshot

        snapshot = build_symbol_index_snapshot(workspace)
    except (OSError, RuntimeError, ValueError, TypeError, ImportError):
        return []
    exports = getattr(snapshot, "physical_exports", {}) or {}
    if not exports:
        return []
    lines: list[str] = [
        "已生成文件的实际导出接口 / Actual exported interface of already-generated sibling files:",
        "(消费或引用这些文件时必须使用下列真实符号名与签名，禁止臆造；这些即真实接口，无需先 read_file 探索)",
    ]
    file_count = 0
    for path in sorted(exports):
        symbols = exports[path]
        if not symbols:
            continue
        rendered: list[str] = []
        for sym in symbols[:14]:
            name = str(getattr(sym, "name", "") or "")
            if not name:
                continue
            sig = str(getattr(sym, "signature", "") or "")
            kind = str(getattr(sym, "symbol_kind", "") or "")
            rendered.append(name + (f"({sig})" if sig else "") + (f" [{kind}]" if kind else ""))
        if rendered:
            lines.append(f"- {path}: " + ", ".join(rendered))
            file_count += 1
        if file_count >= 24:
            break
    # build_symbol_index_snapshot yields only 'class' for a class (no fields), so
    # after symbol coherence is fixed the residual runtime logic bug is the model
    # misusing a class field/type (e.g. treating a str as an object with `.name`).
    # Inject bounded actual file bodies so it sees the real fields/constructors/
    # return types. These files already exist and are correct -- the model must USE
    # their symbols, not rewrite them.
    # Budget large enough to carry full small/medium implementations: a TEST task
    # must see the WHOLE implementation it asserts against (factory_bench L1-03
    # forecast.py is 433 lines; a 60-line snippet left the model guessing expected
    # values -> 11 failing test assertions). Still capped so a huge file cannot
    # dominate the window.
    content_budget = 30000
    # build_symbol_index_snapshot omits entrypoint files (e.g. Go main.go where
    # package-level helpers like fixedClock/mustExhibit live), so a TEST task that
    # only sees `exports` redeclares them and fails to compile (factory_bench L1-04
    # main_test.go redeclared main.go symbols). Walk the workspace for real impl
    # source files too, entrypoint first, skipping the test files the task itself
    # writes and build/vendor dirs.
    _src_ext = (
        ".py", ".go", ".ts", ".tsx", ".js", ".jsx", ".rs", ".java", ".rb",
        ".c", ".cc", ".cpp", ".h", ".hpp", ".cs", ".kt", ".swift", ".php",
    )
    _skip_seg = {
        "node_modules", ".git", "dist", "build", "target", "__pycache__",
        "vendor", ".venv", "venv", ".polaris", ".mypy_cache",
    }

    def _looks_like_test(rel: str) -> bool:
        base = rel.rsplit("/", 1)[-1]
        guarded = f"/{rel}/"
        return (
            base.startswith("test_")
            or base.endswith("_test.go")
            or base.endswith("_test.py")
            or ".test." in base
            or ".spec." in base
            or "/tests/" in guarded
            or "/test/" in guarded
        )

    inject_paths: list[str] = [p for p in sorted(exports) if not _looks_like_test(p)]
    seen_paths = set(inject_paths)
    for _root, _dirs, _files in os.walk(workspace):
        _dirs[:] = [_d for _d in _dirs if _d not in _skip_seg]
        for _fn in _files:
            if not _fn.endswith(_src_ext):
                continue
            _rel = os.path.relpath(os.path.join(_root, _fn), workspace).replace("\\", "/")
            if _rel in seen_paths or _looks_like_test(_rel):
                continue
            seen_paths.add(_rel)
            inject_paths.append(_rel)
    inject_paths.sort(key=lambda p: (0 if p.rsplit("/", 1)[-1].split(".")[0] in {"main", "index", "app"} else 1, p))
    body_lines: list[str] = []
    for path in inject_paths:
        if content_budget <= 0:
            break
        try:
            with open(os.path.join(workspace, path), encoding="utf-8", errors="replace") as _fh:
                snippet = "\n".join(_fh.read().splitlines()[:400])[:content_budget]
        except OSError:
            continue
        if not snippet.strip():
            continue
        body_lines.append(f"--- {path} (已存在文件实际内容；请使用其符号，勿重复声明或重写此文件) ---")
        body_lines.append(snippet)
        content_budget -= len(snippet)
    if body_lines:
        lines.append("")
        lines.append(
            "已生成依赖文件的实际定义（据此正确使用其类字段/构造/返回类型，避免把 str 当对象等逻辑误用）:"
        )
        lines.extend(body_lines)
    return lines if file_count else []


def _build_director_blueprint_handoff_lines(workspace: str, blueprint_id: str) -> list[str]:
    resolved_blueprint_id = str(blueprint_id or "").strip()
    if not resolved_blueprint_id:
        return ["- blueprint_id: not provided"]

    lines = [f"- blueprint_id: {resolved_blueprint_id}"]
    try:
        from polaris.cells.chief_engineer.blueprint.public import (
            BlueprintPersistence,
            validate_director_handoff_from_payload,
        )
    except (ImportError, RuntimeError) as exc:
        lines.append(f"- blueprint_payload: unavailable ({type(exc).__name__})")
        return lines

    payload = BlueprintPersistence(workspace, ensure_directory=False).load(resolved_blueprint_id)
    if not isinstance(payload, dict):
        lines.append("- blueprint_payload: missing or unreadable")
        return lines

    validation = validate_director_handoff_from_payload(workspace, {"blueprint_id": resolved_blueprint_id})
    lines.append(f"- handoff_ready: {'yes' if validation.get('allowed') else 'no'} ({validation.get('reason')})")
    decision_payload = validation.get("decision_payload")
    if isinstance(decision_payload, dict):
        blockers = _string_list_payload(decision_payload.get("blockers"), limit=4)
        if blockers:
            lines.append(_join_limited_values("handoff blockers", blockers))

    for label, key, limit in (
        ("blueprint target_files", "target_files", 16),
        ("blueprint scope_paths", "scope_paths", 16),
        ("blueprint acceptance", "acceptance_criteria", 10),
        ("blueprint execution_checklist", "execution_checklist", 10),
    ):
        item = _join_limited_values(label, _string_list_payload(payload.get(key), limit=limit))
        if item:
            lines.append(item)
    test_targets = [
        item
        for item in _string_list_payload(payload.get("target_files"), limit=40)
        if _path_looks_like_test_target(item)
    ]
    test_targets.extend(
        item
        for item in _string_list_payload(payload.get("scope_paths"), limit=40)
        if _path_looks_like_test_target(item) and item not in test_targets
    )
    if test_targets:
        lines.append(_join_limited_values("blueprint required test targets", test_targets[:12]))

    module_interface_contract = payload.get("module_interface_contract")
    if isinstance(module_interface_contract, dict) and module_interface_contract:
        lines.append("- module_interface_contract: required")
        modules = module_interface_contract.get("modules")
        if isinstance(modules, list):
            for module in modules[:10]:
                if not isinstance(module, dict):
                    continue
                path = str(module.get("path") or "").strip()
                symbols = _string_list_payload(module.get("planned_public_symbols"), limit=8)
                role = str(module.get("role") or "").strip()
                if path and symbols:
                    role_suffix = f" [{role}]" if role else ""
                    lines.append(f"  - {path}{role_suffix}: exports {', '.join(symbols)}")
        rules = _string_list_payload(module_interface_contract.get("rules"), limit=4)
        for rule in rules:
            lines.append(f"  - interface rule: {rule}")

    llm_blueprint = payload.get("llm_blueprint")
    if isinstance(llm_blueprint, dict) and llm_blueprint:
        authority = str(llm_blueprint.get("authority") or "advisory_only").strip()
        lines.append(f"- ce_llm_blueprint: consumed ({authority})")
        for label, key in (
            ("ce plan phases", "implementation_phases"),
            ("ce module boundaries", "module_boundaries"),
            ("ce verification", "verification_steps"),
            ("ce scope advisory", "scope_for_apply_advisory"),
            ("ce risks", "risk_flags"),
        ):
            item = _join_limited_values(label, _string_list_payload(llm_blueprint.get(key), limit=5))
            if item:
                lines.append(item)

    completeness = payload.get("contract_completeness")
    if isinstance(completeness, dict):
        missing = _string_list_payload(completeness.get("missing_fields"), limit=6)
        semantic_blockers = _string_list_payload(completeness.get("semantic_blockers"), limit=4)
        if missing:
            lines.append(_join_limited_values("blueprint missing_fields", missing))
        if semantic_blockers:
            lines.append(_join_limited_values("blueprint semantic_blockers", semantic_blockers))
        alignment = completeness.get("semantic_alignment")
        if isinstance(alignment, dict):
            expected_terms = _string_list_payload(alignment.get("expected_terms"), limit=8)
            planning_matches = _string_list_payload(alignment.get("planning_text_matches"), limit=8)
            advisory = _string_list_payload(alignment.get("advisory"), limit=4)
            if expected_terms:
                lines.append(_join_limited_values("blueprint expected_terms", expected_terms))
            if planning_matches:
                lines.append(_join_limited_values("blueprint planning_matches", planning_matches))
            if advisory:
                lines.append(_join_limited_values("blueprint advisory", advisory))

    return lines[:40]


_VERIFICATION_COMMAND_MARKERS = (
    "go test",
    "go run",
    "go vet",
    "go build",
    "npm test",
    "npm run",
    "cargo check",
    "cargo test",
    "python -m unittest",
    "pytest",
    "ruff check",
    "mypy",
)
_BACKTICK_VERIFICATION_COMMAND_RE = re.compile(
    r"`([^`\n]*(?:" + "|".join(re.escape(item) for item in _VERIFICATION_COMMAND_MARKERS) + r")[^`\n]*)`", re.IGNORECASE
)


def _flatten_verification_command_sources(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        token = value.strip()
        return [token] if token else []
    if isinstance(value, (list, tuple, set)):
        flattened: list[str] = []
        for item in value:
            flattened.extend(_flatten_verification_command_sources(item))
        return flattened
    if isinstance(value, dict):
        flattened = []
        for key in (
            "verification_commands",
            "verify_commands",
            "quality_commands",
            "workspace_quality_commands",
            "acceptance",
            "acceptance_criteria",
            "steps",
            "execution_checklist",
            "verify",
        ):
            if key in value:
                flattened.extend(_flatten_verification_command_sources(value.get(key)))
        return flattened
    return []


def _extract_director_verification_commands(*values: Any) -> list[str]:
    commands: list[str] = []
    seen: set[str] = set()
    for source in values:
        for text in _flatten_verification_command_sources(source):
            candidates = [match.group(1).strip() for match in _BACKTICK_VERIFICATION_COMMAND_RE.finditer(text)]
            stripped = text.strip().strip("`")
            if not candidates and any(stripped.lower().startswith(marker) for marker in _VERIFICATION_COMMAND_MARKERS):
                candidates.append(stripped)
            for candidate in candidates:
                normalized = " ".join(candidate.strip().strip("`").split())
                if not normalized:
                    continue
                lowered = normalized.lower()
                if lowered not in seen:
                    seen.add(lowered)
                    commands.append(normalized)
    return commands


def _normalize_director_role_response(role_response: Any) -> dict[str, Any]:
    """Normalize role-kernel output without hiding provider/runtime failures."""

    response_payload: dict[str, Any] = role_response if isinstance(role_response, dict) else {}
    content = (
        str(response_payload.get("response") or response_payload.get("reply") or response_payload.get("content") or "")
        if response_payload
        else str(role_response or "")
    )
    content = content.strip()
    explicit_error = str(response_payload.get("error") or "").strip() if response_payload else ""
    runtime_error = _extract_director_role_runtime_error(response_payload, content)
    error = explicit_error or runtime_error
    if not error and response_payload.get("success") is False:
        error = "role_response_unsuccessful"
    provider = str(response_payload.get("provider") or response_payload.get("provider_id") or "").strip()
    model = str(response_payload.get("model") or "").strip()
    metadata_raw = response_payload.get("metadata")
    metadata = _copy_mapping_payload(metadata_raw) or {}
    execution_stats_raw = response_payload.get("execution_stats")
    execution_stats = _copy_mapping_payload(execution_stats_raw) or {}
    raw_response = (
        response_payload.get("raw_response") if isinstance(response_payload.get("raw_response"), dict) else {}
    )
    raw_metadata = raw_response.get("metadata") if isinstance(raw_response, dict) else {}
    raw_usage = raw_response.get("usage") if isinstance(raw_response, dict) else {}
    batch_receipt = _first_mapping_payload(
        response_payload.get("batch_receipt"),
        metadata.get("batch_receipt"),
        execution_stats.get("batch_receipt"),
        raw_response.get("batch_receipt") if isinstance(raw_response, dict) else None,
        raw_metadata.get("batch_receipt") if isinstance(raw_metadata, dict) else None,
        raw_usage.get("batch_receipt") if isinstance(raw_usage, dict) else None,
    )
    tool_results = _first_dict_list_payload(
        response_payload.get("tool_results"),
        metadata.get("tool_results"),
        execution_stats.get("tool_results"),
        raw_response.get("tool_results") if isinstance(raw_response, dict) else None,
        raw_metadata.get("tool_results") if isinstance(raw_metadata, dict) else None,
        raw_usage.get("tool_results") if isinstance(raw_usage, dict) else None,
    )
    tool_calls_raw = response_payload.get("tool_calls")
    tool_calls = (
        [dict(item) for item in tool_calls_raw if isinstance(item, dict)] if isinstance(tool_calls_raw, list) else []
    )
    artifacts_raw = response_payload.get("artifacts")
    artifacts = [str(item) for item in artifacts_raw if str(item).strip()] if isinstance(artifacts_raw, list) else []
    if not provider:
        provider = str(metadata.get("provider_id") or metadata.get("provider") or "").strip()
    if not model:
        model = str(metadata.get("model") or execution_stats.get("model") or "").strip()
    return {
        "content": content,
        "success": not bool(error),
        "error": error,
        "raw_response": role_response,
        "provider": provider,
        "model": model,
        "metadata": dict(metadata),
        "execution_stats": dict(execution_stats),
        "batch_receipt": dict(batch_receipt) if batch_receipt else None,
        "tool_results": tool_results,
        "tool_calls": tool_calls,
        "artifacts": artifacts,
    }


def _extract_director_role_runtime_error(response_payload: dict[str, Any], content: str) -> str:
    """Return a non-empty error when role output is a runtime failure wrapper."""

    if content.startswith("[ROLE_EXECUTION_ERROR]"):
        return content
    if content.startswith("[Cognitive Blocked]"):
        return content
    metadata = response_payload.get("metadata") if isinstance(response_payload, dict) else {}
    if isinstance(metadata, dict):
        metadata_error = str(metadata.get("error") or metadata.get("error_message") or "").strip()
        if metadata_error:
            return metadata_error
    validation = response_payload.get("validation") if isinstance(response_payload, dict) else {}
    if isinstance(validation, dict) and validation.get("success") is False:
        errors = validation.get("errors")
        if isinstance(errors, list) and errors:
            return "; ".join(str(item) for item in errors[:3] if str(item).strip())
    return ""


class DirectorAdapter(BaseRoleAdapter):
    """Director 角色适配器

    职责：
    - 任务执行
    - 代码改写
    - 验证与测试
    - 工具调用
    """

    def __init__(self, workspace: str, task_board: Any = None, task_runtime: Any = None) -> None:
        if task_board is None and task_runtime is None:
            super().__init__(workspace)
        else:
            self.workspace = workspace
            self._task_runtime = task_runtime
            self._task_board = task_board if task_board else task_runtime
        self._state_tracker = DirectorStateTracker(workspace)
        self._execution = DirectorPatchExecutor(workspace)

    @property
    def role_id(self) -> str:
        return "director"

    def get_capabilities(self) -> list[str]:
        return [
            "execute_task",
            "write_code",
            "edit_file",
            "run_command",
            "verify_result",
            "sequential_execution",
            "adaptive_strategy_selection",
            "intelligent_self_correction",
            "multi_objective_optimization",
        ]

    # -------------------------------------------------------------------------
    # Main Execute Method
    # -------------------------------------------------------------------------

    async def execute(
        self,
        task_id: str,
        input_data: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """执行 Director 任务"""
        # Phase 2.4: Pre-execution strategy selection based on task characteristics
        directive = str(input_data.get("input") or input_data.get("directive") or "").strip()
        task_data = input_data.get("task") or input_data
        selected_strategy = self._select_execution_strategy(directive, task_data, context)
        if selected_strategy != "default":
            logger.info("Director strategy selected: %s for task %s", selected_strategy, task_id)

        # Inject strategy into context for downstream use
        if context is not None:
            ctx_metadata = context.get("metadata") if isinstance(context, dict) else None
            if ctx_metadata is None:
                ctx_metadata = {}
                context["metadata"] = ctx_metadata
            if isinstance(ctx_metadata, dict):
                ctx_metadata["director_strategy"] = selected_strategy

        return await execute_director_task(self, task_id, input_data, context)

    def _select_execution_strategy(
        self,
        directive: str,
        task: dict[str, Any],
        context: dict[str, Any],
    ) -> str:
        """Phase 2.4: Select optimal execution strategy based on task characteristics.

        Args:
            directive: Task directive text
            task: Task data dictionary
            context: Execution context (may contain architect constraints)

        Returns:
            Strategy name: 'default', 'incremental', 'aggressive', 'conservative', 'focused'
        """
        strategy_factors: list[str] = []

        # Check architect constraints from context
        ctx_metadata = context.get("metadata") if isinstance(context, dict) else None
        architect_constraints = []
        if isinstance(ctx_metadata, dict):
            architect_constraints = ctx_metadata.get("architect_constraints", [])

        # Check for concerns from architect
        has_architect_concerns = any(c.get("type") == "concern" for c in architect_constraints if isinstance(c, dict))
        if has_architect_concerns:
            return "conservative"  # Be careful when architect raised concerns

        # Analyze task complexity
        if len(directive) > 300:
            strategy_factors.append("complex_directive")
        if "test" in directive.lower() or "verify" in directive.lower():
            strategy_factors.append("verification_focused")
        if "refactor" in directive.lower() or "重构" in directive:
            strategy_factors.append("refactoring")

        # Check for file targets
        target_files = task.get("target_files", []) if isinstance(task, dict) else []
        scope_files = task.get("scope_paths", []) if isinstance(task, dict) else []
        total_files = len(target_files) + len(scope_files)

        if total_files >= 10:
            strategy_factors.append("large_scope")
        elif total_files >= 5:
            strategy_factors.append("medium_scope")

        # Determine strategy
        if "large_scope" in strategy_factors and "complex_directive" in strategy_factors:
            return "incremental"
        if "refactoring" in strategy_factors:
            return "conservative"
        if "verification_focused" in strategy_factors:
            return "focused"
        if "medium_scope" in strategy_factors and "complex_directive" in strategy_factors:
            return "aggressive"
        return "default"

    def _apply_intelligent_correction(
        self,
        attempt_result: dict[str, Any],
        previous_attempts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Phase 2.4: Apply intelligent self-correction based on failure patterns.

        Args:
            attempt_result: Result of current execution attempt
            previous_attempts: List of previous attempt results

        Returns:
            Modified result with correction hints
        """
        if attempt_result.get("success", False):
            return attempt_result

        # Analyze failure patterns from previous attempts
        failure_types: dict[str, int] = {}
        for prev in previous_attempts:
            error = str(prev.get("error") or "")
            if "timeout" in error.lower():
                failure_types["timeout"] = failure_types.get("timeout", 0) + 1
            elif "syntax" in error.lower() or "语法" in error:
                failure_types["syntax_error"] = failure_types.get("syntax_error", 0) + 1
            elif "not found" in error.lower() or "找不到" in error:
                failure_types["missing_dependency"] = failure_types.get("missing_dependency", 0) + 1
            elif "permission" in error.lower() or "权限" in error:
                failure_types["permission"] = failure_types.get("permission", 0) + 1
            else:
                failure_types["unknown"] = failure_types.get("unknown", 0) + 1

        # Generate correction hints based on failure patterns
        correction_hints: list[str] = []
        for failure_type, count in failure_types.items():
            if count >= 2:
                if failure_type == "timeout":
                    correction_hints.append("Consider breaking down into smaller steps")
                elif failure_type == "syntax_error":
                    correction_hints.append("Check syntax before applying changes")
                elif failure_type == "missing_dependency":
                    correction_hints.append("Ensure all dependencies are available first")
                elif failure_type == "permission":
                    correction_hints.append("Verify file permissions before writing")

        if correction_hints:
            attempt_result["_correction_hints"] = correction_hints

        return attempt_result

    # -------------------------------------------------------------------------
    # Sequential Engine Configuration
    # -------------------------------------------------------------------------

    def _get_sequential_config(self, context: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Get Sequential configuration from settings and context."""
        settings = get_settings_safe()
        return build_sequential_config(settings, context)

    # -------------------------------------------------------------------------
    # Sequential Engine Execution
    # -------------------------------------------------------------------------

    async def _execute_sequential(
        self,
        task: dict[str, Any],
        task_id: str,
        run_id: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute task using Sequential Engine."""
        seq_config = self._get_sequential_config(context)
        if not seq_config:
            return {"success": False, "error": "Sequential not enabled"}
        return await execute_sequential(
            self.workspace,
            self.role_id,
            task,
            task_id,
            run_id,
            context,
            seq_config,
            self._invoke_role_dialogue_with_timeout,
            self._emit_task_trace_event,
            self._build_director_message,
        )

    async def _execute_hybrid(
        self,
        task: dict[str, Any],
        task_id: str,
        run_id: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute task using Hybrid Engine."""
        seq_config = self._get_sequential_config(context)
        if not seq_config:
            return {"success": False, "error": "Sequential not enabled"}
        return await execute_hybrid(
            self.workspace,
            self.role_id,
            task,
            task_id,
            run_id,
            context,
            seq_config,
            self._emit_task_trace_event,
        )

    # -------------------------------------------------------------------------
    # Role LLM Invocation
    # -------------------------------------------------------------------------

    async def _invoke_role_dialogue(
        self,
        message: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Invoke Director through the canonical role runtime first."""
        llm_max_retries = self._resolve_kernel_retry_budget(self.role_id)

        try:
            runtime_response = await self._invoke_role_runtime_session(
                message,
                context=context,
                max_retries=llm_max_retries,
            )
        except (ImportError, AttributeError) as exc:
            raise RuntimeError("director_role_runtime_boundary_unavailable") from exc
        else:
            primary = _normalize_director_role_response(runtime_response)
            if bool(primary.get("success")) and not is_empty_role_response(primary):
                return primary
            primary["error"] = str(primary.get("error") or "director_role_runtime_empty_response")
            primary["success"] = False
            return primary

    async def _invoke_role_runtime_session(
        self,
        message: str,
        *,
        context: dict[str, Any] | None,
        max_retries: int,
    ) -> dict[str, Any]:
        """Call roles.runtime so Context OS and Cognitive Runtime participate."""

        from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        context_payload = dict(context) if isinstance(context, dict) else {}
        self._ensure_director_verification_commands(
            message=message,
            context=context_payload,
        )
        metadata = self._build_role_runtime_metadata(context_payload, max_retries=max_retries)
        self._ensure_director_execution_profile(
            message=message,
            context=context_payload,
            metadata=metadata,
            workspace=str(self.workspace),
        )
        task_id = self._resolve_runtime_identity_field(
            context_payload,
            metadata,
            keys=("task_id", "pm_task_id", "target_task_id", "id"),
        )
        run_id = self._resolve_runtime_identity_field(
            context_payload,
            metadata,
            keys=("run_id", "workflow_run_id", "observer_run_id"),
        )
        session_id = self._resolve_role_runtime_session_id(
            context_payload,
            metadata=metadata,
            task_id=task_id,
            run_id=run_id,
            message=message,
        )
        command = ExecuteRoleSessionCommandV1(
            role=self.role_id,
            session_id=session_id,
            workspace=str(self.workspace),
            user_message=message,
            run_id=run_id or None,
            task_id=task_id or None,
            domain=str(metadata.get("domain") or "code"),
            history=self._normalize_role_runtime_history(context_payload),
            context=context_payload,
            metadata=metadata,
            stream=False,
            host_kind="director_adapter",
        )
        runtime = RoleRuntimeService()
        result = await runtime.execute_role_session(command)
        result_metadata = dict(getattr(result, "metadata", {}) or {})
        result_usage = dict(getattr(result, "usage", {}) or {})
        output = str(getattr(result, "output", "") or "")
        error = str(getattr(result, "error_message", "") or getattr(result, "error_code", "") or "").strip()
        batch_receipt = _first_mapping_payload(
            result_metadata.get("batch_receipt"),
            result_usage.get("batch_receipt"),
            getattr(result, "batch_receipt", None),
        )
        tool_results = _first_dict_list_payload(
            result_metadata.get("tool_results"),
            result_usage.get("tool_results"),
            getattr(result, "tool_results", None),
        )
        tool_calls = [
            {"tool": str(name), "tool_name": str(name), "status": "observed", "success": False}
            for name in tuple(getattr(result, "tool_calls", ()) or ())
            if str(name).strip()
        ]
        return {
            "content": output,
            "response": output,
            "success": bool(getattr(result, "ok", False)) and not bool(error),
            "error": error,
            "role": str(getattr(result, "role", self.role_id) or self.role_id),
            "metadata": {
                **result_metadata,
                "role_runtime_entrypoint": "roles.runtime.execute_role_session",
                "role_runtime_session_id": session_id,
                "context_os_expected": True,
            },
            "execution_stats": {
                **result_usage,
                "role_runtime_entrypoint": "roles.runtime.execute_role_session",
            },
            "batch_receipt": batch_receipt,
            "tool_results": tool_results,
            "tool_calls": tool_calls,
            "artifacts": list(getattr(result, "artifacts", ()) or ()),
            "raw_response": {
                "ok": bool(getattr(result, "ok", False)),
                "status": str(getattr(result, "status", "") or ""),
                "session_id": str(getattr(result, "session_id", "") or session_id),
                "run_id": str(getattr(result, "run_id", "") or run_id),
                "task_id": str(getattr(result, "task_id", "") or task_id),
                "metadata": result_metadata,
                "usage": result_usage,
                "batch_receipt": batch_receipt,
                "tool_results": tool_results,
                "tool_calls": list(getattr(result, "tool_calls", ()) or ()),
                "artifacts": list(getattr(result, "artifacts", ()) or ()),
                "error_code": str(getattr(result, "error_code", "") or ""),
                "error_message": str(getattr(result, "error_message", "") or ""),
            },
        }

    @staticmethod
    def _ensure_director_execution_profile(
        *,
        message: str,
        context: dict[str, Any],
        metadata: dict[str, Any],
        workspace: str,
    ) -> dict[str, Any]:
        existing = context.get("director_execution_profile")
        if not isinstance(existing, dict):
            existing = metadata.get("director_execution_profile")
        if isinstance(existing, dict) and existing:
            profile_fields = {
                key: existing[key] for key in TaskExecutionProfileV1.__dataclass_fields__ if key in existing
            }
            profile = TaskExecutionProfileV1(**profile_fields)
        else:
            profile = resolve_director_execution_profile(
                subject=str(metadata.get("title") or metadata.get("subject") or message or ""),
                description=str(
                    metadata.get("description")
                    or metadata.get("objective")
                    or metadata.get("summary")
                    or context.get("description")
                    or ""
                ),
                metadata=metadata,
                target_files=DirectorAdapter._metadata_path_list(metadata, context, "target_files"),
                scope_paths=DirectorAdapter._metadata_path_list(metadata, context, "scope_paths"),
                workspace=str(workspace or ""),
            )
        strategy = resolve_director_execution_strategy(
            profile,
            metadata=metadata,
        )
        apply_execution_strategy_overrides(
            context=context,
            metadata=metadata,
            profile=profile,
            strategy=strategy,
        )
        metadata.setdefault("task_execution_profile_source", "director.tasking.resolve_director_execution_profile")
        return profile.to_dict()

    @staticmethod
    def _metadata_path_list(
        metadata: dict[str, Any],
        context: dict[str, Any],
        key: str,
    ) -> list[str]:
        for source in (metadata, context):
            value = source.get(key)
            if isinstance(value, str):
                normalized = value.strip()
                return [normalized] if normalized else []
            if isinstance(value, (list, tuple, set)):
                return [str(item).strip() for item in value if str(item or "").strip()]
        return []

    @staticmethod
    def _build_role_runtime_metadata(context: dict[str, Any], *, max_retries: int) -> dict[str, Any]:
        raw_metadata = context.get("metadata")
        metadata: dict[str, Any] = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
        for key in ("task_id", "pm_task_id", "run_id", "session_id"):
            value = context.get(key)
            if value is not None and key not in metadata:
                metadata[key] = value
        metadata.setdefault("source", "roles.adapters.director")
        metadata.setdefault("domain", "code")
        metadata.setdefault("validate_output", False)
        metadata.setdefault("max_retries", max(0, int(max_retries)))
        metadata.setdefault("use_repo_intelligence", True)
        metadata.setdefault("repo_intel_max_files", 20)
        metadata.setdefault("repo_intel_max_symbols", 40)
        metadata["role_runtime_required"] = True
        metadata["cognitive_runtime_required"] = True
        metadata["context_os_expected"] = True
        metadata.setdefault("cognitive_runtime_approval_mode", "auto_accept")
        metadata.setdefault(
            "cognitive_runtime_approval",
            {
                "mode": "auto_accept",
                "source": "roles.adapters.director",
                "scope": "director_execution_preflight",
                "approved_by": "director_adapter",
            },
        )
        return metadata

    @staticmethod
    def _promote_task_contract_to_runtime_context(
        *,
        task: dict[str, Any],
        context: dict[str, Any],
        workspace: str,
    ) -> None:
        """Promote claimed TaskBoard contract fields into RoleRuntime metadata."""

        if not isinstance(task, dict) or not isinstance(context, dict):
            return
        sources = _task_contract_sources(task)
        contract_payload = _promoted_task_contract_payload(sources)
        blueprint_payload = _load_ce_blueprint_contract_payload(workspace, task)
        contract_payload = _merge_ce_blueprint_contract_payload(contract_payload, blueprint_payload)
        if not contract_payload:
            return

        metadata_raw = context.get("metadata")
        metadata: dict[str, Any] = dict(metadata_raw) if isinstance(metadata_raw, dict) else {}
        task_metadata_raw = task.get("metadata")
        task_metadata: dict[str, Any] = dict(task_metadata_raw) if isinstance(task_metadata_raw, dict) else {}

        for key in _TASK_CONTRACT_LIST_KEYS:
            value = contract_payload.get(key)
            if not isinstance(value, list) or not value:
                continue
            merged_task = _merge_contract_lists(task.get(key), value)
            if merged_task:
                task[key] = merged_task
            merged_task_metadata = _merge_contract_lists(task_metadata.get(key), value)
            if merged_task_metadata:
                task_metadata[key] = merged_task_metadata
            merged_context = _merge_contract_lists(context.get(key), value)
            if merged_context:
                context[key] = merged_context
            merged_metadata = _merge_contract_lists(metadata.get(key), value)
            if merged_metadata:
                metadata[key] = merged_metadata

        for key in _TASK_CONTRACT_MAPPING_KEYS:
            value = contract_payload.get(key)
            if not isinstance(value, dict) or not value:
                continue
            if not _has_contract_value(context, key):
                context[key] = dict(value)
            if not _has_contract_value(metadata, key):
                metadata[key] = dict(value)
            if not _has_contract_value(task, key):
                task[key] = dict(value)
            if not _has_contract_value(task_metadata, key):
                task_metadata[key] = dict(value)

        for key in _TASK_CONTRACT_SCALAR_KEYS:
            value = contract_payload.get(key)
            if value is None or isinstance(value, (list, dict)):
                continue
            if not _has_contract_value(context, key):
                context[key] = value
            if not _has_contract_value(metadata, key):
                metadata[key] = value
            if not _has_contract_value(task, key):
                task[key] = value
            if not _has_contract_value(task_metadata, key):
                task_metadata[key] = value

        if workspace and not _has_contract_value(context, "workspace"):
            context["workspace"] = str(workspace)
        if workspace and not _has_contract_value(metadata, "workspace"):
            metadata["workspace"] = str(workspace)
        if workspace and not _has_contract_value(task_metadata, "workspace"):
            task_metadata["workspace"] = str(workspace)

        if not _has_contract_value(metadata, "task"):
            metadata["task"] = dict(contract_payload)
        if not _has_contract_value(context, "task_contract"):
            context["task_contract"] = dict(contract_payload)
        if not _has_contract_value(task_metadata, "task_contract"):
            task_metadata["task_contract"] = dict(contract_payload)
        task["metadata"] = task_metadata
        context["metadata"] = metadata

    @staticmethod
    def _ensure_director_verification_commands(*, message: str, context: dict[str, Any]) -> list[str]:
        metadata_raw = context.get("metadata")
        metadata: dict[str, Any] = dict(metadata_raw) if isinstance(metadata_raw, dict) else {}
        commands = _extract_director_verification_commands(
            context.get("verification_commands"),
            context.get("quality_commands"),
            context.get("workspace_quality_commands"),
            context.get("acceptance"),
            context.get("acceptance_criteria"),
            context.get("steps"),
            context.get("execution_checklist"),
            context.get("construction_step"),
            metadata,
        )
        if not commands:
            commands = _extract_director_verification_commands(message)
        if commands:
            context.setdefault("verification_commands", commands)
            metadata.setdefault("verification_commands", commands)
            context["metadata"] = metadata
        return commands

    @staticmethod
    def _resolve_runtime_identity_field(
        context: dict[str, Any],
        metadata: dict[str, Any],
        *,
        keys: tuple[str, ...],
    ) -> str:
        for source in (context, metadata):
            for key in keys:
                token = str(source.get(key) or "").strip()
                if token:
                    return token
        return ""

    @classmethod
    def _resolve_role_runtime_session_id(
        cls,
        context: dict[str, Any],
        *,
        metadata: dict[str, Any],
        task_id: str,
        run_id: str,
        message: str,
    ) -> str:
        explicit = cls._resolve_runtime_identity_field(
            context,
            metadata,
            keys=("session_id", "role_runtime_session_id", "runtime_session_id"),
        )
        if explicit:
            return explicit
        seed = "|".join(
            part
            for part in (
                "director",
                run_id,
                task_id,
                hashlib.sha256(message.encode("utf-8")).hexdigest()[:12],
            )
            if part
        )
        if not seed:
            seed = "director-adhoc"
        safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in seed)
        return safe.strip("-_")[:120] or "director-adhoc"

    @staticmethod
    def _normalize_role_runtime_history(context: dict[str, Any]) -> tuple[tuple[str, str], ...]:
        raw_history = context.get("history")
        if raw_history is None:
            raw_history = context.get("messages")
        if not isinstance(raw_history, (list, tuple)):
            return ()
        normalized: list[tuple[str, str]] = []
        for item in raw_history:
            role = ""
            content = ""
            if isinstance(item, dict):
                role = str(item.get("role") or item.get("speaker") or "").strip()
                content = str(item.get("content") or item.get("message") or "").strip()
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                role = str(item[0] or "").strip()
                content = str(item[1] or "").strip()
            if role and content:
                normalized.append((role, content))
        return tuple(normalized)

    async def _invoke_direct_runtime_provider(
        self,
        message: str,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Fail closed: direct provider bypass is no longer a Director fallback."""
        del message, timeout_seconds
        raise RuntimeError("director_direct_runtime_provider_removed")

    async def _invoke_role_dialogue_with_timeout(
        self,
        message: str,
        *,
        context: dict[str, Any] | None,
        timeout_seconds: float,
        stage_label: str,
    ) -> dict[str, Any]:
        """Call role LLM with timeout."""
        timeout = max(0.1, float(timeout_seconds or _DEFAULT_LLM_CALL_TIMEOUT_SECONDS))
        try:
            response = await asyncio.wait_for(
                self._invoke_role_dialogue(message, context=context),
                timeout=timeout,
            )
            if isinstance(response, dict):
                return response
            return {
                "content": "",
                "success": False,
                "error": f"director_{stage_label}_invalid_llm_payload",
                "raw_response": response,
            }
        except asyncio.TimeoutError:
            return {
                "content": "",
                "success": False,
                "error": f"director_{stage_label}_llm_timeout",
                "raw_response": {"error": "timeout", "timeout": True},
            }
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return {
                "content": "",
                "success": False,
                "error": f"director_{stage_label}_llm_error:{exc}",
                "raw_response": {"error": str(exc), "exception_type": type(exc).__name__},
            }

    # -------------------------------------------------------------------------
    # Task Retrieval
    # -------------------------------------------------------------------------

    def _get_task(self, task_id: str) -> dict | None:
        """获取任务信息"""
        return self.task_board.get_task(task_id)

    def _select_pending_board_task(self) -> dict[str, Any] | None:
        """当编排任务没有 TaskBoard 映射时，回退到可执行的真实待办任务。"""
        return self.task_runtime.select_next_task(prefer_resumable=True)

    def _materialize_runtime_task(
        self,
        requested_task_id: str,
        input_data: dict[str, Any],
    ) -> dict[str, Any]:
        """将迁移期编排任务物化为 runtime.task_runtime 的 canonical task。"""
        input_metadata_raw = input_data.get("metadata")
        input_metadata: dict[str, Any] = input_metadata_raw if isinstance(input_metadata_raw, dict) else {}
        subject = str(
            input_data.get("subject")
            or input_metadata.get("title")
            or input_metadata.get("subject")
            or input_data.get("input")
            or ""
        ).strip()
        if not subject:
            subject = f"Director task {requested_task_id}"
        description = str(
            input_data.get("description")
            or input_metadata.get("description")
            or input_metadata.get("goal")
            or input_data.get("input")
            or ""
        ).strip()
        metadata = self._build_materialized_metadata(requested_task_id, input_data)
        return self.task_runtime.ensure_task_row(
            external_task_id=requested_task_id,
            subject=subject,
            description=description,
            metadata=metadata,
        )

    def _build_ephemeral_task(self, requested_task_id: str, input_data: dict[str, Any]) -> dict[str, Any]:
        """Build a safe ephemeral task enriched with pending board contract hints."""
        task = self._materialize_runtime_task(requested_task_id, input_data)
        input_metadata_raw = input_data.get("metadata")
        input_metadata: dict[str, Any] = input_metadata_raw if isinstance(input_metadata_raw, dict) else {}

        pending_task_raw = self._select_pending_board_task()
        pending_task: dict[str, Any] = pending_task_raw if isinstance(pending_task_raw, dict) else {}
        pending_subject = str(
            pending_task.get("subject")
            or pending_task.get("title")
            or str(input_metadata.get("title") or "").strip()
            or str(input_metadata.get("subject") or "").strip()
            or ""
        ).strip()
        pending_description = str(
            pending_task.get("description") or pending_task.get("goal") or input_metadata.get("description") or ""
        ).strip()

        snapshot = self._state_tracker.build_taskboard_observation_snapshot(self.task_runtime)
        board_brief = self._taskboard_snapshot_brief(snapshot)

        current_desc = str(task.get("description") or "").strip()
        current_desc = board_brief if not current_desc else f"{current_desc}\n{board_brief}"

        task_contract_lines: list[str] = []
        if pending_subject:
            task_contract_lines.append(f"Pending TaskBoard contract: {pending_subject}")
        if pending_description:
            task_contract_lines.append(f"Pending TaskBoard description: {pending_description}")
        if task_contract_lines:
            current_desc = f"{current_desc}\n" + "\n".join(task_contract_lines)
        else:
            current_desc = f"{current_desc}\nNo pending TaskBoard contract found; use TaskBoard pending queue first."

        task["description"] = current_desc
        task["board_snapshot_brief"] = board_brief
        task["pending_task_contract"] = {
            "subject": pending_subject,
            "description": pending_description,
        }
        return task

    def _build_materialized_metadata(self, requested_task_id: str, input_data: dict[str, Any]) -> dict[str, Any]:
        """Build metadata dict for materialized runtime task."""
        if input_data is None:
            input_data = {}
        input_metadata_raw = input_data.get("metadata")
        input_metadata: dict[str, Any] = input_metadata_raw if isinstance(input_metadata_raw, dict) else {}

        def _list_or_empty(value: Any) -> list[Any]:
            return list(value) if isinstance(value, list) else []

        scope_paths = (
            input_data.get("scope_paths")
            if isinstance(input_data.get("scope_paths"), list)
            else input_metadata.get("scope_paths")
            if isinstance(input_metadata.get("scope_paths"), list)
            else []
        )
        target_files = (
            input_data.get("target_files")
            if isinstance(input_data.get("target_files"), list)
            else input_metadata.get("target_files")
            if isinstance(input_metadata.get("target_files"), list)
            else []
        )
        execution_checklist = (
            input_data.get("execution_checklist")
            if isinstance(input_data.get("execution_checklist"), list)
            else input_metadata.get("execution_checklist")
            if isinstance(input_metadata.get("execution_checklist"), list)
            else []
        )
        acceptance_criteria = (
            input_data.get("acceptance_criteria")
            if isinstance(input_data.get("acceptance_criteria"), list)
            else input_metadata.get("acceptance_criteria")
            if isinstance(input_metadata.get("acceptance_criteria"), list)
            else input_data.get("acceptance")
            if isinstance(input_data.get("acceptance"), list)
            else input_metadata.get("acceptance")
            if isinstance(input_metadata.get("acceptance"), list)
            else []
        )
        metadata: dict[str, Any] = {
            "goal": str(input_data.get("goal") or input_metadata.get("goal") or "").strip(),
            "scope": str(input_data.get("scope") or input_metadata.get("scope") or "").strip(),
            "steps": (
                input_data.get("steps")
                if isinstance(input_data.get("steps"), list)
                else input_metadata.get("steps")
                if isinstance(input_metadata.get("steps"), list)
                else execution_checklist
            ),
            "phase": str(input_data.get("phase") or input_metadata.get("phase") or "implementation").strip(),
            "pm_task_id": str(
                input_data.get("pm_task_id")
                or input_metadata.get("pm_task_id")
                or input_metadata.get("task_id")
                or input_metadata.get("id")
                or requested_task_id
            ).strip(),
            "source": "director_adapter.materialized_orchestration_task",
            "scope_paths": _list_or_empty(scope_paths),
            "target_files": _list_or_empty(target_files),
            "execution_checklist": _list_or_empty(execution_checklist),
            "acceptance_criteria": _list_or_empty(acceptance_criteria),
            "acceptance": _list_or_empty(acceptance_criteria),
        }
        input_metadata_no_proj = (
            {k: v for k, v in input_metadata.items() if k != "projection"} if input_metadata else {}
        )
        metadata.update(input_metadata_no_proj)
        for key in ("scope_paths", "target_files", "execution_checklist", "acceptance_criteria", "acceptance"):
            metadata[key] = _list_or_empty(metadata.get(key))
        if not isinstance(metadata.get("steps"), list):
            metadata["steps"] = list(metadata["execution_checklist"])
        return metadata

    # -------------------------------------------------------------------------
    # Execution Backend Resolution
    # -------------------------------------------------------------------------

    def _resolve_execution_backend_request(
        self,
        *,
        task_id: str,
        task: dict[str, Any],
        input_data: dict[str, Any],
        context: dict[str, Any],
    ) -> DirectorExecutionBackendRequest:
        """解析执行后端请求"""
        request = resolve_director_execution_backend(
            input_data=input_data,
            task=task,
            context=context,
            default_project_slug=default_projection_slug(task_id, task, input_data),
        )
        if not request.requirement and request.execution_backend != "projection_refresh_mapping":
            request = replace(
                request,
                requirement=compose_projection_requirement(task, input_data),
            )
        return request

    def _persist_execution_backend_metadata(
        self,
        task_id: str,
        request: DirectorExecutionBackendRequest,
    ) -> None:
        """持久化执行后端元数据"""
        if not task_id:
            return
        self._update_board_task(
            task_id,
            metadata=request.to_task_metadata(),
        )

    # -------------------------------------------------------------------------
    # Director Message Building
    # -------------------------------------------------------------------------

    def _build_director_message(
        self,
        task: dict[str, Any],
        *,
        text_patch_mode: bool = False,
        context: dict[str, Any] | None = None,
    ) -> str:
        """构建 Director 角色消息"""
        subject = task.get("subject", "")
        description = DirectorStateTracker.sanitize_task_description(str(task.get("description") or ""))
        raw_metadata = task.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        runtime_context = context if isinstance(context, dict) else {}
        runtime_metadata_raw = runtime_context.get("metadata")
        runtime_metadata: dict[str, Any] = runtime_metadata_raw if isinstance(runtime_metadata_raw, dict) else {}
        goal = str(
            metadata.get("goal")
            or task.get("goal")
            or runtime_context.get("goal")
            or runtime_metadata.get("goal")
            or ""
        ).strip()

        def _first_listish(*values: Any, limit: int = 24) -> list[str]:
            for value in values:
                items = _string_list_payload(value, limit=limit)
                if items:
                    return items
            return []

        scope = _first_listish(
            metadata.get("scope"),
            task.get("scope"),
            runtime_context.get("scope"),
            runtime_metadata.get("scope"),
        )
        steps = _first_listish(
            metadata.get("steps"),
            task.get("steps"),
            runtime_context.get("steps"),
            runtime_metadata.get("steps"),
            metadata.get("execution_checklist"),
            task.get("execution_checklist"),
            runtime_context.get("execution_checklist"),
            runtime_metadata.get("execution_checklist"),
        )
        acceptance = _first_listish(
            metadata.get("acceptance"),
            task.get("acceptance"),
            runtime_context.get("acceptance"),
            runtime_metadata.get("acceptance"),
            metadata.get("acceptance_criteria"),
            task.get("acceptance_criteria"),
            runtime_context.get("acceptance_criteria"),
            runtime_metadata.get("acceptance_criteria"),
        )
        raw_adapter_result = metadata.get("adapter_result")
        adapter_result: dict[str, Any] = raw_adapter_result if isinstance(raw_adapter_result, dict) else {}
        qa_rework_reason = str(metadata.get("qa_rework_reason") or adapter_result.get("qa_rework_reason") or "").strip()
        qa_rework_evidence = metadata.get("qa_rework_evidence") or adapter_result.get("qa_rework_evidence")

        def _stringify_list(value: Any) -> list[str]:
            if isinstance(value, list):
                return [str(item or "").strip() for item in value if str(item or "").strip()]
            token = str(value or "").strip()
            if not token:
                return []
            return [part.strip() for part in token.split(",") if part.strip()] or [token]

        scope_items = _stringify_list(scope)
        target_file_items = _first_listish(
            metadata.get("target_files")
            or task.get("target_files")
            or runtime_context.get("target_files")
            or runtime_metadata.get("target_files"),
            limit=16,
        )
        scope_path_items = _first_listish(
            metadata.get("scope_paths")
            or task.get("scope_paths")
            or runtime_context.get("scope_paths")
            or runtime_metadata.get("scope_paths"),
            limit=16,
        )
        for item in [*scope_path_items, *target_file_items]:
            if item not in scope_items:
                scope_items.append(item)
        step_items = _stringify_list(steps)
        acceptance_items = _stringify_list(acceptance)
        qa_rework_items = _stringify_list(qa_rework_evidence)
        blueprint_id = str(
            metadata.get("blueprint_id")
            or task.get("blueprint_id")
            or runtime_context.get("blueprint_id")
            or runtime_metadata.get("blueprint_id")
            or ""
        ).strip()
        construction_step_raw = runtime_context.get("construction_step") or metadata.get("construction_step")
        construction_step: dict[str, Any] = construction_step_raw if isinstance(construction_step_raw, dict) else {}
        construction_target = str(construction_step.get("target_file") or "").strip()
        construction_signatures = _stringify_list(construction_step.get("signatures"))[:8]
        construction_verify = str(construction_step.get("verify") or "").strip()
        verification_commands = _extract_director_verification_commands(
            metadata.get("verification_commands"),
            task.get("verification_commands"),
            runtime_context.get("verification_commands"),
            runtime_metadata.get("verification_commands"),
            acceptance_items,
            step_items,
            construction_verify,
        )
        language_identity = ""
        language_section = ""
        try:
            from polaris.cells.director.tasking.internal.language_guidance import build_language_section

            guidance_metadata = {**metadata, **runtime_metadata}
            if construction_step:
                guidance_metadata["construction_step"] = construction_step
            language_targets = target_file_items or ([construction_target] if construction_target else [])
            language_identity, language_section = build_language_section(
                language_targets,
                str(self.workspace),
                metadata=guidance_metadata,
                subject=str(subject or ""),
                description=str(description or ""),
                scope_paths=scope_path_items,
            )
        except (RuntimeError, ValueError, ImportError) as exc:
            logger.debug("Failed to build Director language guidance: %s", exc)
        if language_identity:
            runtime_context["director_language_identity"] = language_identity
            runtime_metadata.setdefault("director_language_identity", language_identity)
            runtime_context["metadata"] = runtime_metadata
        factory_project = str(
            metadata.get("factory_bench_project_id")
            or runtime_metadata.get("factory_bench_project_id")
            or runtime_context.get("factory_bench_project_id")
            or ""
        ).strip()
        factory_title = str(
            metadata.get("factory_bench_title")
            or runtime_metadata.get("factory_bench_title")
            or runtime_context.get("factory_bench_title")
            or ""
        ).strip()
        blueprint_handoff_lines = _build_director_blueprint_handoff_lines(self.workspace, blueprint_id)

        lines = [
            "PM Task Contract / 任务合同:",
            f"任务: {subject}",
            "",
            f"描述: {description}" if description else "",
            "",
            f"目标: {goal}" if goal else "",
            f"范围: {', '.join(scope_items)}" if scope_items else "",
            f"目标文件: {', '.join(target_file_items)}" if target_file_items else "",
            (
                "目标文件覆盖硬门禁: 本任务列出的目标文件必须全部由本轮工具写入或编辑；"
                "多文件创建任务必须为每个目标文件分别发出 write/edit 工具调用，"
                "不得只写第一个 sibling 文件后结束。"
                if len(target_file_items) > 1
                else ""
            ),
            "",
            "执行步骤:",
            *[f"- {item}" for item in step_items],
            "",
            "Acceptance criteria / 验收标准:",
            *[f"- {item}" for item in acceptance_items],
            "",
            "Verification commands / 验证命令:" if verification_commands else "",
            *[f"- {item}" for item in verification_commands],
            "",
            "Director language/task identity / 语言专项身份:" if language_identity or language_section else "",
            language_identity,
            language_section.strip(),
            "",
            "Chief Engineer Blueprint / CE 蓝图交接:",
            *blueprint_handoff_lines,
            f"- construction target: {construction_target}" if construction_target else "",
            ("- construction signatures: " + "; ".join(construction_signatures) if construction_signatures else ""),
            f"- construction verify: {construction_verify}" if construction_verify else "",
            (
                f"- factory bench project: {factory_project}" + (f" - {factory_title}" if factory_title else "")
                if factory_project or factory_title
                else ""
            ),
            "",
            *(
                _build_director_workspace_interface_lines(self.workspace)
                if _director_actual_interface_injection_enabled()
                else []
            ),
            "",
            "QA 返工要求:" if qa_rework_reason else "",
            f"- 原因: {qa_rework_reason}" if qa_rework_reason else "",
            *[f"- 证据: {item}" for item in qa_rework_items],
            "必须修复 QA 证据中的真实文件并重新运行相关验证，不得仅确认既有 scope 存在。" if qa_rework_reason else "",
            "",
            "禁止输出 TODO/FIXME/NotImplemented 等占位实现。",
            "不得把示例路径当成目标文件；必须使用任务范围中的真实相对路径。",
            "生成 Python 测试时必须使用标准库 unittest，且 `python -m unittest discover -s tests -p 'test_*.py' -v` 必须至少发现并运行 1 个测试。",
            "测试只能覆盖目标、执行步骤、验收标准明确要求的能力；不得新增合同外功能断言或引入未声明第三方测试依赖。",
            "",
        ]
        if text_patch_mode:
            lines.extend(
                [
                    "当前运行时要求纯文本补丁。只输出可解析的文件块，不要解释。",
                    "创建或替换文件时使用如下格式，每个文件一个块:",
                    "relative/path.ext",
                    "```language",
                    "完整文件内容",
                    "```",
                    "修改已有文件时也可以使用 PATCH_FILE，但 PATCH_FILE 后必须是真实相对路径。",
                    "不要把 unified diff 或 ```diff 代码块当成文件内容输出；Markdown 文件块必须包含完整最终文件内容。",
                    "不要输出 `PATCH_FILE path` 后再跟 ```diff 代码块；若使用 PATCH_FILE 协议，必须使用运行时可解析的正式协议格式。",
                    "不要输出任何占位路径。",
                ]
            )
        else:
            lines.extend(
                [
                    "请通过运行时正式写入工具完成修改；若只能返回文本，输出可解析的文件块。",
                    "文本文件块格式:",
                    "relative/path.ext",
                    "```language",
                    "完整文件内容",
                    "```",
                ]
            )

        return "\n".join(line for line in lines if line != "")

    # -------------------------------------------------------------------------
    # Progress Update Methods (matching base class signatures)
    # -------------------------------------------------------------------------

    def _update_task_progress(
        self,
        task_id: str,
        phase: str,
        current_file: str | None = None,
        event_code: str | None = None,
        event_status: str | None = None,
        event_reason: str | None = None,
        event_detail: str | None = None,
        event_refs: dict[str, Any] | None = None,
    ) -> None:
        """更新任务进度"""
        if event_status:
            self._update_board_task(task_id, status=event_status)

    def _update_board_task(
        self,
        task_id: str,
        status: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """更新 TaskBoard 任务"""
        if metadata:
            self.task_board.update_task(task_id, metadata=metadata)
            return True
        elif status:
            self.task_board.update_task(task_id, status=status)
            return True
        return False

    async def _emit_task_trace_event(
        self,
        *,
        task_id: str,
        phase: str,
        step_kind: str,
        step_title: str,
        step_detail: str,
        status: str = "running",
        run_id: str = "",
        current_file: str | None = None,
        code: str | None = None,
        reason: str | None = None,
        refs: dict[str, Any] | None = None,
        attempt: int = 0,
        visibility: str = "debug",
    ) -> None:
        """发射任务追踪事件"""
        logger.debug(
            "Task trace: task_id=%s phase=%s step=%s",
            task_id,
            phase,
            step_kind,
        )

    def _append_runtime_stage_signals(
        self,
        *,
        stage: str,
        task_id: str,
        signals: list[dict[str, Any]],
        context: dict[str, Any] | None = None,
        source: str | None = None,
    ) -> str | None:
        """追加运行时阶段信号"""
        return None

    def _taskboard_snapshot_brief(self, snapshot: dict[str, Any]) -> str:
        """TaskBoard 快照简要描述"""
        return taskboard_snapshot_brief(snapshot)

    # -------------------------------------------------------------------------
    # Compatibility Proxy Methods (thin shims for test / legacy callers)
    # -------------------------------------------------------------------------

    def _collect_workspace_code_files(self) -> dict[str, str]:
        """Proxy to state tracker collect_workspace_code_files."""
        return self._state_tracker.collect_workspace_code_files()

    def _build_taskboard_observation_snapshot(self, sample_limit: int = 5) -> dict[str, Any]:
        """Proxy to state tracker build_taskboard_observation_snapshot."""
        return self._state_tracker.build_taskboard_observation_snapshot(self.task_runtime, sample_limit=sample_limit)

    async def _call_role_llm(
        self,
        message: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Compatibility shim: invoke Director LLM with disable_internal_tool_rounds logic."""
        ctx = dict(context) if isinstance(context, dict) else {}
        if not os.environ.get("KERNELONE_DIRECTOR_ENABLE_INTERNAL_TOOL_ROUNDS"):
            ctx["disable_internal_tool_rounds"] = True
        max_retries = self._resolve_kernel_retry_budget(self.role_id)
        from polaris.cells.roles.adapters.internal import director_adapter as director_adapter_module

        generate_fn = getattr(director_adapter_module, "generate_role_response", None)
        if not callable(generate_fn):
            generate_fn = generate_role_response

        raw_result = await generate_fn(
            workspace=self.workspace,
            settings=get_settings_safe(),
            role=self.role_id,
            message=message,
            context=ctx,
            validate_output=False,
            max_retries=max_retries,
            invoke_role_dialogue=self._invoke_role_dialogue,
        )
        return _normalize_director_role_response(raw_result)

    async def _call_role_llm_with_timeout(
        self,
        message: str,
        *,
        context: dict[str, Any] | None = None,
        timeout_seconds: float = _DEFAULT_LLM_CALL_TIMEOUT_SECONDS,
        stage_label: str = "call",
    ) -> dict[str, Any]:
        """Compatibility alias for _invoke_role_dialogue_with_timeout."""
        return await self._invoke_role_dialogue_with_timeout(
            message,
            context=context,
            timeout_seconds=timeout_seconds,
            stage_label=stage_label,
        )

    async def _execute_tools(
        self,
        response: str,
        task_id: str,
        *,
        allowed_tool_names: set[str] | None = None,
        allow_patch_fallback: bool = True,
    ) -> list[dict[str, Any]]:
        """Compatibility proxy for _execution.execute_tools."""
        return await self._execution.execute_tools(
            response,
            task_id,
            self._update_task_progress,
            allowed_tool_names=allowed_tool_names,
            allow_patch_fallback=allow_patch_fallback,
        )
