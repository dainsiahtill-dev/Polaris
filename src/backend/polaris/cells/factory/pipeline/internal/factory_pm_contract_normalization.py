"""Pure PM plan contract normalization helpers.

Extracted from ``OrchestrationStageExecutor`` as part of the incremental
god-class decomposition. Every function here is pure (no ``self``, no I/O
beyond the caller-supplied ``workspace: Path``) so that the behavior is
deterministic and testable without a full executor instance.

The God-class retains thin one-line delegate wrappers so that existing
callers and the characterization-test suite continue to resolve the same
methods on ``OrchestrationStageExecutor``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

from .factory_stage_helpers import task_string, task_string_list

# Language-to-extension mapping for PM plan language consistency validation.
# Used to detect when the PM model plans files in the wrong language
# (e.g. Java files for a JavaScript project — context bleed from other projects).
_LANGUAGE_SOURCE_EXTENSIONS: dict[str, frozenset[str]] = {
    "javascript": frozenset({".js", ".mjs", ".cjs", ".jsx"}),
    "typescript": frozenset({".ts", ".tsx", ".mts", ".cts"}),
    "python": frozenset({".py"}),
    "rust": frozenset({".rs"}),
    "go": frozenset({".go"}),
    "java": frozenset({".java"}),
    "cpp": frozenset({".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".hxx"}),
    "csharp": frozenset({".cs"}),
    "ruby": frozenset({".rb"}),
    "swift": frozenset({".swift"}),
    "kotlin": frozenset({".kt", ".kts"}),
    "scala": frozenset({".scala"}),
}


def read_catalog_contract(workspace: Path) -> dict[str, Any]:
    """Read the ``.polaris/catalog_contract.json`` from the workspace root.

    Returns an empty dict if the file is absent, malformed, or not a JSON
    object. Never raises — callers depend on the graceful fallback.
    """

    catalog_path = workspace / ".polaris" / "catalog_contract.json"
    if not catalog_path.exists():
        return {}
    try:
        payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def catalog_delivery_depth_contract(catalog: dict[str, Any]) -> dict[str, Any]:
    """Project the catalog contract into a ``delivery_depth_contract`` fragment."""

    level_contract_raw = catalog.get("level_contract")
    level_contract: dict[str, Any] = level_contract_raw if isinstance(level_contract_raw, dict) else {}
    if not level_contract:
        return {}
    feature_keywords = [
        str(item).strip() for item in (catalog.get("feature_keywords") or []) if str(item or "").strip()
    ]
    minimums = dict(level_contract.get("minimums") or {})
    return {
        "schema_version": "polaris.delivery_depth_contract.v1",
        "source": "factory.catalog_contract",
        "language": str(catalog.get("primary_language") or "").strip(),
        "project_type": str(catalog.get("project_type") or "").strip(),
        "level": level_contract.get("level") or catalog.get("level"),
        "minimums": minimums,
        "required_evidence": list(level_contract.get("required_evidence") or []),
        "anti_hollow_delivery": list(level_contract.get("anti_hollow_delivery") or []),
        "level_contract": dict(level_contract),
        "product_intent": {
            "subject": str(catalog.get("project_id") or catalog.get("project_type") or "").strip(),
            "primary_entities": feature_keywords,
        },
        "behavior_contract": {
            "minimums": minimums,
            "required_behavior_tests": [
                "normal behavior",
                "boundary behavior",
                "invalid or edge-case behavior",
            ],
        },
    }


def merge_string_list(*values: Any) -> list[str]:
    """Merge multiple list/scalar sources into a de-duplicated string list."""

    rows: list[str] = []
    seen: set[str] = set()
    for value in values:
        raw_items = value if isinstance(value, (list, tuple, set, frozenset)) else [value]
        for item in raw_items:
            token = str(item or "").strip()
            if token and token not in seen:
                seen.add(token)
                rows.append(token)
    return rows


def merge_catalog_delivery_depth_contract(
    existing: dict[str, Any],
    catalog_contract: dict[str, Any],
) -> dict[str, Any]:
    """Merge a catalog-derived depth contract into an existing one.

    Existing values take precedence over catalog values; catalog fills gaps.
    """

    if not existing:
        return dict(catalog_contract)
    if not catalog_contract:
        return dict(existing)

    merged = dict(existing)
    for key in ("schema_version", "language", "project_type", "level"):
        if not merged.get(key) and catalog_contract.get(key) not in (None, ""):
            merged[key] = catalog_contract[key]

    for key in ("minimums", "level_contract"):
        existing_raw = existing.get(key)
        catalog_raw = catalog_contract.get(key)
        existing_map = cast(dict[str, Any], existing_raw) if isinstance(existing_raw, dict) else {}
        catalog_map = cast(dict[str, Any], catalog_raw) if isinstance(catalog_raw, dict) else {}
        if existing_map or catalog_map:
            merged[key] = {**catalog_map, **existing_map}

    for key in ("required_evidence", "anti_hollow_delivery"):
        merged_list = merge_string_list(
            catalog_contract.get(key),
            existing.get(key),
        )
        if merged_list:
            merged[key] = merged_list

    for key in ("product_intent", "behavior_contract", "acceptance_contract"):
        existing_raw = existing.get(key)
        catalog_raw = catalog_contract.get(key)
        existing_map = cast(dict[str, Any], existing_raw) if isinstance(existing_raw, dict) else {}
        catalog_map = cast(dict[str, Any], catalog_raw) if isinstance(catalog_raw, dict) else {}
        if not existing_map and not catalog_map:
            continue
        child = {**catalog_map, **existing_map}
        if key == "behavior_contract":
            existing_minimums_raw = existing_map.get("minimums")
            catalog_minimums_raw = catalog_map.get("minimums")
            existing_minimums = (
                cast(dict[str, Any], existing_minimums_raw) if isinstance(existing_minimums_raw, dict) else {}
            )
            catalog_minimums = (
                cast(dict[str, Any], catalog_minimums_raw) if isinstance(catalog_minimums_raw, dict) else {}
            )
            if existing_minimums or catalog_minimums:
                child["minimums"] = {**catalog_minimums, **existing_minimums}
        merged[key] = child

    return merged


def inject_catalog_delivery_depth_contract(
    context: dict[str, Any],
    catalog: dict[str, Any],
) -> None:
    """Inject/merge the catalog depth contract into a task context in place.

    ``catalog`` must already be loaded via :func:`read_catalog_contract`.
    """

    metadata_raw = context.get("metadata")
    metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
    catalog_depth_contract = catalog_delivery_depth_contract(catalog)
    context_depth_raw = context.get("delivery_depth_contract")
    metadata_depth_raw = metadata.get("delivery_depth_contract")
    if isinstance(context_depth_raw, dict):
        existing_depth_contract = dict(cast(dict[str, Any], context_depth_raw))
    elif isinstance(metadata_depth_raw, dict):
        existing_depth_contract = dict(cast(dict[str, Any], metadata_depth_raw))
    else:
        existing_depth_contract = {}
    depth_contract = merge_catalog_delivery_depth_contract(existing_depth_contract, catalog_depth_contract)
    if not depth_contract:
        return
    context["delivery_depth_contract"] = depth_contract
    level_contract_raw = depth_contract.get("level_contract")
    level_contract = dict(cast(dict[str, Any], level_contract_raw)) if isinstance(level_contract_raw, dict) else {}
    if level_contract:
        context["level_contract"] = level_contract
    language = str(depth_contract.get("language") or "").strip()
    if language and not str(context.get("language") or "").strip():
        context["language"] = language
    level = depth_contract.get("level")
    if level is not None and context.get("factory_bench_level") is None:
        context["factory_bench_level"] = level
    project_id = str(catalog.get("project_id") or "").strip()
    if project_id and not str(context.get("factory_bench_project_id") or "").strip():
        context["factory_bench_project_id"] = project_id
    title = str(catalog.get("title") or catalog.get("name") or "").strip()
    if title and not str(context.get("factory_bench_title") or "").strip():
        context["factory_bench_title"] = title
    metadata = dict(metadata)
    metadata_depth_raw = metadata.get("delivery_depth_contract")
    metadata["delivery_depth_contract"] = merge_catalog_delivery_depth_contract(
        dict(cast(dict[str, Any], metadata_depth_raw)) if isinstance(metadata_depth_raw, dict) else {},
        depth_contract,
    )
    if level_contract:
        metadata.setdefault("level_contract", level_contract)
    if language:
        metadata.setdefault("language", language)
    if level is not None:
        metadata.setdefault("factory_bench_level", level)
    if project_id:
        metadata.setdefault("factory_bench_project_id", project_id)
    if title:
        metadata.setdefault("factory_bench_title", title)
    context["metadata"] = metadata


def normalize_contract_path(value: Any) -> str:
    """Normalize a PM contract target path to a canonical forward-slash form."""

    path = str(value or "").strip().replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path


def source_target_suffixes() -> frozenset[str]:
    """Return the set of recognized source-code file suffixes."""

    suffixes: set[str] = set()
    for extensions in _LANGUAGE_SOURCE_EXTENSIONS.values():
        suffixes.update(extensions)
    return frozenset(suffixes)


def collect_pm_project_declared_target_files(tasks: list[dict[str, Any]]) -> list[str]:
    """Collect write targets from PM task contracts.

    ``target_files`` is the write/materialization surface. ``context_files``
    remains read-only evidence and must not be promoted into this union.
    """

    rows: list[str] = []
    seen: set[str] = set()
    for task in tasks:
        for path in task_string_list(task, "target_files"):
            normalized = normalize_contract_path(path)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            rows.append(normalized)
    return rows


def filter_source_target_files(paths: list[str]) -> list[str]:
    """Filter to only paths whose suffix is a recognized source-code extension."""

    source_suffixes = source_target_suffixes()
    rows: list[str] = []
    for path in paths:
        suffix = Path(path).suffix.lower()
        if suffix and suffix in source_suffixes:
            rows.append(path)
    return rows


def filter_entrypoint_like_targets(paths: list[str]) -> list[str]:
    """Filter to entrypoint-like paths (index, main, cli, etc.)."""

    rows: list[str] = []
    for path in paths:
        normalized = path.replace("\\", "/")
        filename = Path(normalized).name.lower()
        stem = Path(filename).stem.lower()
        if filename in {"package.json", "pyproject.toml", "go.mod", "cargo.toml"}:
            continue
        if stem in {"index", "main", "cli", "app", "server", "runner"}:
            rows.append(path)
    return rows


def inject_project_declared_target_contract(
    context: dict[str, Any],
    *,
    project_declared_target_files: list[str],
) -> None:
    """Inject declared target files into a task context in place."""

    if not project_declared_target_files:
        return

    source_targets = filter_source_target_files(project_declared_target_files)
    entrypoint_targets = filter_entrypoint_like_targets(project_declared_target_files)
    context["project_declared_target_files"] = merge_string_list(
        project_declared_target_files,
        context.get("project_declared_target_files"),
    )
    if source_targets:
        context["project_declared_source_targets"] = merge_string_list(
            source_targets,
            context.get("project_declared_source_targets"),
        )
    if entrypoint_targets:
        context["project_declared_entrypoint_targets"] = merge_string_list(
            entrypoint_targets,
            context.get("project_declared_entrypoint_targets"),
        )

    manifest_policy = {
        "schema_version": "polaris.manifest_entrypoint_contract.v1",
        "source": "factory.pm_plan_declared_targets",
        "allowed_local_entrypoints": list(project_declared_target_files),
        "rule": (
            "Package manifest scripts/bin/main/module local paths must reference existing files "
            "or project_declared_target_files; do not invent unowned local entrypoint files."
        ),
    }
    existing_policy_raw = context.get("manifest_entrypoint_contract")
    existing_policy = dict(cast(dict[str, Any], existing_policy_raw)) if isinstance(existing_policy_raw, dict) else {}
    existing_allowed = existing_policy.get("allowed_local_entrypoints")
    manifest_policy["allowed_local_entrypoints"] = merge_string_list(
        project_declared_target_files,
        existing_allowed,
    )
    context["manifest_entrypoint_contract"] = {**manifest_policy, **existing_policy}

    metadata_raw = context.get("metadata")
    metadata: dict[str, Any] = dict(metadata_raw) if isinstance(metadata_raw, dict) else {}
    metadata["project_declared_target_files"] = merge_string_list(
        project_declared_target_files,
        metadata.get("project_declared_target_files"),
    )
    if source_targets:
        metadata["project_declared_source_targets"] = merge_string_list(
            source_targets,
            metadata.get("project_declared_source_targets"),
        )
    if entrypoint_targets:
        metadata["project_declared_entrypoint_targets"] = merge_string_list(
            entrypoint_targets,
            metadata.get("project_declared_entrypoint_targets"),
        )
    metadata_policy_raw = metadata.get("manifest_entrypoint_contract")
    metadata_policy = dict(cast(dict[str, Any], metadata_policy_raw)) if isinstance(metadata_policy_raw, dict) else {}
    metadata_allowed = metadata_policy.get("allowed_local_entrypoints")
    metadata["manifest_entrypoint_contract"] = {
        **manifest_policy,
        **metadata_policy,
        "allowed_local_entrypoints": merge_string_list(project_declared_target_files, metadata_allowed),
    }
    context["metadata"] = metadata


# ── PM plan validation-contract normalization ────────────────────────────

_PM_TEST_COMMAND_RE = re.compile(
    r"`?(?:npm\s+(?:run\s+)?test|pnpm\s+(?:run\s+)?test|yarn\s+test|"
    r"pytest|go\s+test|cargo\s+test|vitest|jest)`?",
    re.IGNORECASE,
)
_PM_NON_TEST_COMMAND_RE = re.compile(
    r"\b(?:build|lint|start|smoke|compile|typecheck|tsc|ruff|mypy)\b",
    re.IGNORECASE,
)


def is_pm_validation_target_path(path: str) -> bool:
    """Return True if ``path`` looks like a test/spec/verify file."""

    normalized = str(path or "").strip().replace("\\", "/").lower()
    if not normalized:
        return False
    name = Path(normalized).name
    return (
        normalized.startswith(("tests/", "test/", "__tests__/"))
        or "/tests/" in normalized
        or "/__tests__/" in normalized
        or any(token in name for token in ("test", "spec", "verify"))
    )


def acceptance_without_test_commands(acceptance: list[str]) -> tuple[list[str], list[str]]:
    """Split acceptance items into (kept, removed_test_commands).

    Non-test items are always kept. Test-command items are removed, but if a
    removed item also mentions a build/lint/start command a generic placeholder
    is kept so the task still has a non-empty acceptance vector.
    """

    kept: list[str] = []
    removed: list[str] = []
    for item in acceptance:
        text = str(item or "").strip()
        if not text:
            continue
        if not _PM_TEST_COMMAND_RE.search(text):
            kept.append(text)
            continue
        removed.append(text)
        if _PM_NON_TEST_COMMAND_RE.search(text):
            kept.append("Build/start checks for this task's declared implementation targets pass.")
    return list(dict.fromkeys(kept)), removed


def normalize_pm_plan_validation_contracts(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep per-task test acceptance aligned with the task that owns test targets.

    Tasks that do not own any test target but whose acceptance references test
    commands have those test items stripped — the downstream validation task
    owns the test acceptance. A ``validation_contract_hygiene`` metadata note
    records what was removed and why.
    """

    if not tasks:
        return []

    task_ids = [
        task_string(task, "id", "task_id", "uid") or f"task-{index}" for index, task in enumerate(tasks, start=1)
    ]
    downstream_validation_by_dependency: dict[str, list[str]] = {}
    for index, task in enumerate(tasks):
        task_id = task_ids[index]
        if not task_id:
            continue
        validation_targets = [
            path for path in task_string_list(task, "target_files", "scope_paths") if is_pm_validation_target_path(path)
        ]
        if not validation_targets:
            continue
        for dependency_id in task_string_list(task, "depends_on", "dependencies"):
            normalized_dependency = str(dependency_id or "").strip()
            if not normalized_dependency:
                continue
            downstream_validation_by_dependency.setdefault(normalized_dependency, []).extend(validation_targets)

    normalized_tasks: list[dict[str, Any]] = []
    for index, task in enumerate(tasks):
        task_id = task_ids[index]
        copied = dict(task)
        acceptance_keys = [key for key in ("acceptance", "acceptance_criteria") if key in copied]
        if not acceptance_keys:
            acceptance_keys = ["acceptance"]
        acceptance = task_string_list(copied, "acceptance", "acceptance_criteria")
        has_local_validation_target = any(
            is_pm_validation_target_path(path) for path in task_string_list(copied, "target_files", "scope_paths")
        )
        downstream_validation_targets = downstream_validation_by_dependency.get(task_id, [])
        if acceptance and not has_local_validation_target and downstream_validation_targets:
            rewritten, removed = acceptance_without_test_commands(acceptance)
            if removed:
                normalized_acceptance = rewritten or [
                    "Build/start checks for this task's declared implementation targets pass."
                ]
                for acceptance_key in acceptance_keys:
                    copied[acceptance_key] = list(normalized_acceptance)
                metadata_raw = copied.get("metadata")
                metadata = dict(metadata_raw) if isinstance(metadata_raw, dict) else {}
                metadata["validation_contract_hygiene"] = {
                    "reason": "test_acceptance_deferred_to_downstream_validation_task",
                    "removed_acceptance_items": list(dict.fromkeys(removed)),
                    "downstream_validation_targets": list(dict.fromkeys(downstream_validation_targets)),
                }
                copied["metadata"] = metadata
        normalized_tasks.append(copied)
    return normalized_tasks


def pm_plan_tasks_from_payload(payload: Any) -> list[dict[str, Any]]:
    """Extract and normalize the ``tasks`` list from a PM plan payload."""

    if not isinstance(payload, dict):
        return []
    tasks = payload.get("tasks")
    if not isinstance(tasks, list):
        return []
    task_rows = [dict(item) for item in tasks if isinstance(item, dict)]
    return normalize_pm_plan_validation_contracts(task_rows)
