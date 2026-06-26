"""Canonical task execution-profile resolution.

This module is the single task-execution classifier for Director-owned runtime
work. Prompt guidance, legacy dispatch compatibility, runtime sampling, output
protocol, and audit metadata must consume this profile instead of maintaining
parallel heuristics.

All text operations MUST explicitly use UTF-8 when file I/O is involved. This
module performs no file I/O.
"""

from __future__ import annotations

import re
from typing import Any

from polaris.cells.director.tasking.internal.language_guidance import (
    LanguagePromptContext,
    select_guidance,
)
from polaris.cells.director.tasking.public.contracts import TaskExecutionProfileV1

_TASK_TYPE_ALIASES: dict[str, str] = {
    "audit": "code_review",
    "bug": "bugfix",
    "bugfix": "bugfix",
    "build": "write_code",
    "codegeneration": "write_code",
    "codereview": "code_review",
    "config": "config",
    "create": "write_code",
    "database": "database",
    "devops": "devops",
    "docs": "docs",
    "documentation": "docs",
    "feature": "write_code",
    "fix": "bugfix",
    "implementation": "write_code",
    "implement": "write_code",
    "integration": "integration",
    "observability": "observability",
    "refactor": "refactor",
    "repair": "bugfix",
    "review": "code_review",
    "security": "security",
    "test": "tests",
    "testing": "tests",
    "validation": "validation",
}

_PROJECT_TYPE_ALIASES: dict[str, str] = {
    "backend": "api",
    "db": "database",
    "documentation": "docs",
    "frontend": "frontend",
    "microservice": "service",
    "web": "frontend",
}

_TASK_TYPE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("code_review", r"\b(review|audit|reviewer|审查|评审|代码审查)\b"),
    ("bugfix", r"\b(fix|bug|defect|repair|regression|failure|failed|error|exception|broken|修复|缺陷|故障)\b"),
    ("tests", r"\b(test|tests|testing|pytest|jest|vitest|spec|unit|回归测试|测试)\b"),
    ("refactor", r"\b(refactor|migrate|migration|extract|decompose|cleanup|rename|重构|迁移|拆分)\b"),
    ("config", r"\b(config|configuration|tsconfig|package\.json|pyproject|docker|配置)\b"),
    ("docs", r"\b(docs|documentation|readme|guide|manual|文档)\b"),
    ("write_code", r"\b(implement|create|build|generate|function|class|module|api|endpoint|实现|创建|新增)\b"),
)

_QUALITY_REPAIR_MARKER_RE = re.compile(
    r"(?i)(?:"
    r"materialization\s+quality\s+repair|"
    r"director_quality_repair|"
    r"write_only_single_target|"
    r"missing\s+target\s+files|"
    r"quality\s+errors:|"
    r"failed\s+polaris\s+artifact\s+quality\s+gates"
    r")"
)

_PROJECT_TYPE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("api", r"\b(api|endpoint|route|controller|handler|rest|graphql|http|接口|端点)\b"),
    ("cli", r"\b(cli|command|terminal|argparse|cobra|commander|click|脚本|命令行)\b"),
    ("frontend", r"\b(ui|frontend|component|react|vue|screen|page|form|前端|组件|页面)\b"),
    ("database", r"\b(database|db|sql|migration|schema|index|transaction|orm|数据库|迁移)\b"),
    ("service", r"\b(service|microservice|worker|consumer|producer|adapter)\b"),
    ("library", r"\b(library|sdk|package|public api|client)\b"),
    ("devops", r"\b(devops|ci|cd|pipeline|docker|kubernetes|deploy|release|workflow)\b"),
)

_TASK_TYPE_TO_TEMPERATURE_PHASE: dict[str, str] = {
    "bugfix": "repair",
    "code_review": "code_review",
    "config": "file_write",
    "database": "implementation",
    "devops": "command_generation",
    "docs": "code_read",
    "integration": "implementation",
    "observability": "implementation",
    "refactor": "implementation",
    "security": "implementation",
    "tests": "test_generation",
    "validation": "implementation",
    "write_code": "code_generation",
}

_TEMPERATURE_BY_PHASE: dict[str, float] = {
    "code_generation": 0.15,
    "code_review": 0.25,
    "command_generation": 0.10,
    "file_write": 0.10,
    "implementation": 0.15,
    "repair": 0.05,
    "test_generation": 0.20,
}


def _normalize_token(value: Any) -> str:
    return re.sub(r"[\s_\-]+", "", str(value or "").strip().lower())


def _normalize_label(value: Any) -> str:
    return str(value or "").strip().lower().replace("_", "-")


def _coerce_metadata(metadata: Any) -> dict[str, Any]:
    return dict(metadata) if isinstance(metadata, dict) else {}


def _metadata_values_text(metadata: dict[str, Any]) -> str:
    values: list[str] = []
    for key in (
        "acceptance",
        "acceptance_criteria",
        "constraints",
        "detected_framework",
        "detected_language",
        "framework",
        "intent",
        "phase",
        "project_type",
        "quality_gates",
        "task_kind",
        "task_type",
        "verification_commands",
    ):
        raw = metadata.get(key)
        if isinstance(raw, str):
            values.append(raw)
        elif isinstance(raw, (list, tuple)):
            values.extend(str(item) for item in raw)
    tech_stack = metadata.get("tech_stack")
    if isinstance(tech_stack, dict):
        values.extend(str(value) for value in tech_stack.values())
    return " ".join(values)


def _combined_text(*, subject: str, description: str, metadata: dict[str, Any], paths: tuple[str, ...]) -> str:
    return " ".join(
        part
        for part in (
            subject,
            description,
            _metadata_values_text(metadata),
            " ".join(paths),
        )
        if str(part or "").strip()
    ).lower()


def _normalize_paths(values: Any) -> tuple[str, ...]:
    if isinstance(values, str):
        raw_items: list[Any] = [values]
    elif isinstance(values, (list, tuple, set, frozenset)):
        raw_items = list(values)
    else:
        return ()
    paths: list[str] = []
    seen: set[str] = set()
    for raw_item in raw_items:
        path = str(raw_item or "").strip().replace("\\", "/").strip("/")
        if not path or path.startswith("/") or ":" in path or ".." in path.split("/"):
            continue
        if path not in seen:
            seen.add(path)
            paths.append(path)
    return tuple(paths)


def _metadata_path_tuple(metadata: dict[str, Any], key: str) -> tuple[str, ...]:
    return _normalize_paths(metadata.get(key))


def _explicit_task_type(metadata: dict[str, Any]) -> str:
    for key in ("task_type", "task_kind", "intent", "phase"):
        mapped = _TASK_TYPE_ALIASES.get(_normalize_token(metadata.get(key)))
        if mapped:
            return mapped
    return ""


def _infer_task_type(text: str) -> str:
    for task_type, pattern in _TASK_TYPE_PATTERNS:
        if re.search(pattern, text):
            return task_type
    return "generic"


def _resolve_task_type(metadata: dict[str, Any], text: str) -> tuple[str, str]:
    if _QUALITY_REPAIR_MARKER_RE.search(text):
        return "bugfix", "quality_repair_marker"
    explicit = _explicit_task_type(metadata)
    if explicit:
        return explicit, "metadata"
    return _infer_task_type(text), "heuristic_text"


def _resolve_project_type(metadata: dict[str, Any], text: str) -> tuple[str, str]:
    raw = _normalize_token(metadata.get("project_type"))
    if raw:
        return _PROJECT_TYPE_ALIASES.get(raw, raw), "metadata"
    for project_type, pattern in _PROJECT_TYPE_PATTERNS:
        if re.search(pattern, text):
            return project_type, "heuristic_text"
    return "generic", "default"


def _resolve_phase(metadata: dict[str, Any], task_type: str) -> tuple[str, str]:
    raw_phase = _normalize_label(metadata.get("phase"))
    if raw_phase:
        return raw_phase, "metadata"
    if task_type == "bugfix":
        return "repair", "task_type"
    if task_type == "tests":
        return "test_generation", "task_type"
    if task_type == "code_review":
        return "code_review", "task_type"
    return "implementation", "default"


def _dispatch_type(*, subject: str, text: str, task_type: str, target_files: tuple[str, ...]) -> str:
    subject_lower = subject.lower()
    if "bootstrap" in subject_lower or "init" in subject_lower:
        return "bootstrap"
    if "create file" in subject_lower or "create directory" in subject_lower:
        return "file_creation"
    if target_files or task_type in {
        "bugfix",
        "config",
        "database",
        "devops",
        "docs",
        "integration",
        "observability",
        "refactor",
        "security",
        "tests",
        "validation",
        "write_code",
    }:
        return "code_generation"
    if re.search(r"\b(implement|create|build|generate|function|class|module|api|endpoint)\b", text):
        return "code_generation"
    return "generic"


def _file_roles_by_path(target_files: tuple[str, ...], file_roles: tuple[str, ...]) -> dict[str, tuple[str, ...]]:
    if not target_files:
        return {}
    if len(target_files) == 1:
        return {target_files[0]: file_roles}

    result: dict[str, tuple[str, ...]] = {}
    for path in target_files:
        lowered = path.lower()
        roles: list[str] = []
        if re.search(r"(^|/)(tests?|specs?|__tests__)/|(\.test|\.spec|_test|test_)", lowered):
            roles.append("test")
        elif re.search(r"(package\.json|tsconfig\.json|pyproject\.toml|go\.mod|cargo\.toml|dockerfile)$", lowered):
            roles.append("config")
        elif lowered.endswith((".sh", ".bash")) or "/scripts/" in lowered or lowered.startswith("scripts/"):
            roles.append("script")
        elif lowered.endswith(".sql") or "migration" in lowered or "schema" in lowered:
            roles.append("schema")
        elif lowered.endswith((".md", ".mdx", ".rst")) or "/docs/" in lowered or lowered.startswith("docs/"):
            roles.append("docs")
        elif lowered.endswith((".css", ".scss", ".sass", ".less")):
            roles.append("style")
        else:
            roles.append("source")
        result[path] = tuple(roles)
    return result


def _sampling_mode(task_type: str, phase: str) -> str:
    if task_type in {"bugfix", "code_review"} or "repair" in phase:
        return "deterministic_precise"
    if task_type in {"docs"}:
        return "controlled_explanatory"
    if task_type in {"tests"}:
        return "precise_with_coverage"
    return "precise"


def _temperature_for(task_type: str, phase: str) -> tuple[str, float]:
    phase_key = _TASK_TYPE_TO_TEMPERATURE_PHASE.get(task_type, "")
    if "repair" in phase:
        phase_key = "repair"
    if not phase_key:
        phase_key = "code_generation"
    return phase_key, _TEMPERATURE_BY_PHASE.get(phase_key, 0.15)


def resolve_director_execution_profile(
    *,
    subject: str,
    description: str = "",
    metadata: dict[str, Any] | None = None,
    target_files: list[str] | tuple[str, ...] | None = None,
    scope_paths: list[str] | tuple[str, ...] | None = None,
    workspace: str = "",
) -> TaskExecutionProfileV1:
    """Resolve the canonical task execution profile for one Director task."""

    normalized_metadata = _coerce_metadata(metadata)
    normalized_targets = _normalize_paths(target_files) or _metadata_path_tuple(normalized_metadata, "target_files")
    normalized_scopes = _normalize_paths(scope_paths) or _metadata_path_tuple(normalized_metadata, "scope_paths")
    text = _combined_text(
        subject=subject,
        description=description,
        metadata=normalized_metadata,
        paths=(*normalized_targets, *normalized_scopes),
    )
    guidance = select_guidance(
        LanguagePromptContext(
            target_files=normalized_targets,
            scope_paths=normalized_scopes,
            workspace=workspace,
            metadata=normalized_metadata,
            subject=subject,
            description=description,
        )
    )
    task_type, task_type_source = _resolve_task_type(normalized_metadata, text)
    project_type, project_type_source = _resolve_project_type(normalized_metadata, text)
    phase, phase_source = _resolve_phase(normalized_metadata, task_type)
    if task_type_source == "quality_repair_marker":
        phase = "repair"
        phase_source = "quality_repair_marker"
    temperature_phase, temperature = _temperature_for(task_type, phase)
    dispatch_type = _dispatch_type(
        subject=subject,
        text=text,
        task_type=task_type,
        target_files=normalized_targets,
    )
    return TaskExecutionProfileV1(
        dispatch_type=dispatch_type,
        task_type=task_type,
        phase=phase,
        project_type=project_type,
        language=guidance.language,
        language_display_name=guidance.language_display_name,
        framework=guidance.framework,
        framework_display_name=guidance.framework_display_name,
        task_foci=guidance.task_foci,
        task_focus_labels=guidance.task_focus_labels,
        file_roles=guidance.file_roles,
        file_role_labels=guidance.file_role_labels,
        file_roles_by_path=_file_roles_by_path(normalized_targets, guidance.file_roles),
        sampling_mode=_sampling_mode(task_type, phase),
        temperature_phase=temperature_phase,
        temperature=temperature,
        target_files=normalized_targets,
        scope_paths=normalized_scopes,
        signal_evidence={
            "task_type_source": task_type_source,
            "project_type_source": project_type_source,
            "phase_source": phase_source,
            "language_source": "guidance_selection",
            "framework_source": "guidance_selection" if guidance.framework else "none",
        },
    )
