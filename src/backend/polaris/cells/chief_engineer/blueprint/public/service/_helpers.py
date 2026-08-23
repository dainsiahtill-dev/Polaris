"""Shared helpers/constants for chief_engineer.blueprint public service."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from polaris.cells.control_plane.run_ledger.public import stable_hash
from polaris.kernelone.tasks.task_tokens import normalize_task_token

from ...internal.architecture_decisions import (
    normalize_architecture_decisions,
)
from ...internal.blueprint_persistence import BlueprintPersistence
from ..contracts import (
    ChiefEngineerBlueprintErrorV1,
    ProjectCompletionContractV1,
    QueryBlueprintProvenanceV1,
    TaskBlueprintProvenanceSnapshotV1,
    _strict_provenance_target_paths,
)

_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")

_LOWER_SHA256_RE = re.compile(r"[0-9a-f]{64}")

_PROJECT_COMPLETION_PREDICATE_VERSION = "polaris.project_completion_predicate.v1"

_SEMANTIC_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*")

_CAMEL_TOKEN_RE = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|[0-9]+")

_WINDOWS_DRIVE_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")

_CE_OWNED_TOPOLOGY_AUTHORITY = "chief_engineer"
_TOOLCHAIN_BASENAMES = frozenset(
    {
        "package.json",
        "tsconfig.json",
        "go.mod",
        "cargo.toml",
        "cmakelists.txt",
        "pom.xml",
        "requirements.txt",
        "readme.md",
        "index.html",
    }
)
_SOURCE_TOPOLOGY_SUFFIXES = frozenset(
    {
        ".ts",
        ".tsx",
        ".mts",
        ".cts",
        ".js",
        ".mjs",
        ".cjs",
        ".py",
        ".go",
        ".rs",
        ".c",
        ".cc",
        ".cpp",
        ".cxx",
        ".h",
        ".hh",
        ".hpp",
        ".hxx",
        ".java",
    }
)
_LANGUAGE_SOURCE_SUFFIXES: dict[str, frozenset[str]] = {
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


def chief_engineer_source_suffixes_for_language(primary_language: str) -> tuple[str, ...]:
    """Return immutable CE-owned source suffix authority for one PM language."""

    return tuple(sorted(_LANGUAGE_SOURCE_SUFFIXES.get(str(primary_language or "").strip().lower(), ())))


def _pm_task_declares_ce_owned_topology(task: Mapping[str, Any]) -> bool:
    metadata = task.get("metadata")
    if not isinstance(metadata, Mapping):
        return False
    return str(metadata.get("topology_authority") or "").strip() == _CE_OWNED_TOPOLOGY_AUTHORITY


def _is_ce_source_topology_path(path: str, *, allowed_source_suffixes: tuple[str, ...] = ()) -> bool:
    normalized = str(path or "").strip().replace("\\", "/")
    if not normalized:
        return False
    basename = normalized.rsplit("/", 1)[-1].lower()
    if basename in _TOOLCHAIN_BASENAMES:
        return False
    suffix = f".{basename.rsplit('.', 1)[-1]}" if "." in basename else ""
    allowed = frozenset(str(value).strip().lower() for value in allowed_source_suffixes if str(value).strip())
    return suffix in _SOURCE_TOPOLOGY_SUFFIXES and (not allowed or suffix in allowed)


def _is_ce_test_topology_path(path: str) -> bool:
    normalized = str(path or "").strip().replace("\\", "/")
    parts = tuple(part.lower() for part in normalized.split("/") if part)
    if not parts:
        return False
    basename = parts[-1]
    return bool(
        any(part in {"test", "tests", "__tests__", "spec", "specs"} for part in parts[:-1])
        or basename.startswith("test_")
        or "_test." in basename
        or ".test." in basename
        or ".spec." in basename
    )


def _is_ce_production_source_path(path: str, *, allowed_source_suffixes: tuple[str, ...] = ()) -> bool:
    return _is_ce_source_topology_path(
        path,
        allowed_source_suffixes=allowed_source_suffixes,
    ) and not _is_ce_test_topology_path(path)


def _ce_delegated_artifact_roles(required_source_kinds: tuple[str, ...]) -> frozenset[str]:
    kinds = set(required_source_kinds)
    roles: set[str] = set()
    if kinds.intersection({"domain_modules", "public_api", "public_headers"}):
        roles.add("source")
    if "entrypoint" in kinds:
        roles.add("entrypoint")
    if kinds.intersection({"test", "tests"}):
        roles.add("test")
    return frozenset(roles)


def _ce_artifact_role_matches_path(
    *,
    semantic_role: str,
    path: str,
    allowed_source_suffixes: tuple[str, ...] = (),
) -> bool:
    if semantic_role == "test":
        return _is_ce_test_topology_path(path) and _is_ce_source_topology_path(
            path,
            allowed_source_suffixes=allowed_source_suffixes,
        )
    if semantic_role in {"source", "entrypoint"}:
        return _is_ce_production_source_path(path, allowed_source_suffixes=allowed_source_suffixes)
    return True


def _ce_topology_authorizes_artifact(
    *,
    topology_authority: str,
    required_source_kinds: tuple[str, ...],
    allowed_source_suffixes: tuple[str, ...],
    semantic_role: str,
    path: str,
) -> bool:
    if topology_authority != _CE_OWNED_TOPOLOGY_AUTHORITY:
        return False
    if semantic_role not in _ce_delegated_artifact_roles(required_source_kinds):
        return False
    if not allowed_source_suffixes:
        return False
    return _ce_artifact_role_matches_path(
        semantic_role=semantic_role,
        path=path,
        allowed_source_suffixes=allowed_source_suffixes,
    )


_BLUEPRINT_FILE_PATH_KEYS = frozenset(
    {
        "path",
        "file",
        "filename",
        "target_file",
        "target_path",
        "source_path",
        "output_path",
    }
)

_BLUEPRINT_FILE_CONTAINER_KEYS = frozenset(
    {
        "files",
        "file_plans",
        "target_files",
        "scope_paths",
        "outputs",
        "artifacts",
    }
)

_COMMON_EXTENSIONLESS_FILES = frozenset({"dockerfile", "makefile", "procfile", "readme", "license"})

_GENERIC_SEMANTIC_TOKENS = frozenset(
    {
        "api",
        "app",
        "build",
        "cli",
        "code",
        "component",
        "config",
        "core",
        "data",
        "engine",
        "entry",
        "file",
        "files",
        "handler",
        "helper",
        "input",
        "integration",
        "lib",
        "main",
        "model",
        "models",
        "module",
        "output",
        "package",
        "product",
        "readme",
        "rule",
        "rules",
        "runner",
        "script",
        "service",
        "src",
        "system",
        "test",
        "tests",
        "tool",
        "user",
        "util",
        "utils",
        "validation",
        "web",
    }
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_token(value: str) -> str:
    token = _SAFE_ID_RE.sub("_", str(value or "").strip()).strip("._-")
    return token[:80] or "task"


def _blueprint_path(blueprint_id: str) -> str:
    return f"runtime/blueprints/{blueprint_id}.json"


_BLUEPRINT_HASH_IGNORED_KEYS = frozenset({"blueprint_hash", "capability_token", "job_token"})

_PORTFOLIO_HASH_IGNORED_KEYS = _BLUEPRINT_HASH_IGNORED_KEYS | {"portfolio_hash"}

_BLUEPRINT_PROVENANCE_SCHEMA_VERSION = "chief_engineer.blueprint_provenance.v1"

_BLUEPRINT_PROVENANCE_HASH_SCHEME = "chief_engineer.blueprint_hash.v1"


def _hashable_blueprint_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _hashable_blueprint_payload(item)
            for key, item in value.items()
            if str(key) not in _BLUEPRINT_HASH_IGNORED_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_hashable_blueprint_payload(item) for item in value]
    return value


def _blueprint_hash(payload: dict[str, Any]) -> str:
    return stable_hash(_hashable_blueprint_payload(payload))


def _blueprint_provenance_text(
    payload: Mapping[str, Any],
    field_name: str,
    *,
    error_code: str,
) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value or value != value.strip():
        raise ChiefEngineerBlueprintErrorV1(
            f"blueprint provenance requires exact non-empty {field_name}",
            code=error_code,
            details={"field": field_name},
        )
    return value


def query_blueprint_provenance(
    query: QueryBlueprintProvenanceV1,
) -> TaskBlueprintProvenanceSnapshotV1:
    """Validate one strict-parsed immutable blueprint mapping without I/O."""

    if not isinstance(query, QueryBlueprintProvenanceV1):
        raise TypeError("QueryBlueprintProvenanceV1 required")

    payload = dict(query.blueprint)
    blueprint_schema_version = _blueprint_provenance_text(
        payload,
        "schema_version",
        error_code="blueprint_provenance_schema_mismatch",
    )
    if blueprint_schema_version != "chief_engineer.blueprint.v1":
        raise ChiefEngineerBlueprintErrorV1(
            "blueprint provenance schema does not match chief_engineer.blueprint.v1",
            code="blueprint_provenance_schema_mismatch",
            details={"observed_schema_version": blueprint_schema_version},
        )

    blueprint_id = _blueprint_provenance_text(
        payload,
        "blueprint_id",
        error_code="blueprint_provenance_blueprint_id_mismatch",
    )
    if blueprint_id != query.expected_blueprint_id:
        raise ChiefEngineerBlueprintErrorV1(
            "blueprint provenance blueprint id does not match expected identity",
            code="blueprint_provenance_blueprint_id_mismatch",
            details={"expected": query.expected_blueprint_id, "observed": blueprint_id},
        )

    task_id = _blueprint_provenance_text(
        payload,
        "task_id",
        error_code="blueprint_provenance_task_id_mismatch",
    )
    if task_id != query.expected_task_id:
        raise ChiefEngineerBlueprintErrorV1(
            "blueprint provenance task id does not match expected identity",
            code="blueprint_provenance_task_id_mismatch",
            details={"expected": query.expected_task_id, "observed": task_id},
        )

    factory_run_id = _blueprint_provenance_text(
        payload,
        "run_id",
        error_code="blueprint_provenance_run_id_mismatch",
    )
    if factory_run_id != query.expected_factory_run_id:
        raise ChiefEngineerBlueprintErrorV1(
            "blueprint provenance run id does not match expected Factory run",
            code="blueprint_provenance_run_id_mismatch",
            details={"expected": query.expected_factory_run_id, "observed": factory_run_id},
        )

    canonical_logical_path = _blueprint_path(query.expected_blueprint_id)
    if query.expected_logical_path != canonical_logical_path:
        raise ChiefEngineerBlueprintErrorV1(
            "blueprint provenance logical path is not canonical",
            code="blueprint_provenance_logical_path_mismatch",
            details={"expected": canonical_logical_path, "observed": query.expected_logical_path},
        )

    embedded_blueprint_hash = payload.get("blueprint_hash")
    if not isinstance(embedded_blueprint_hash, str) or _LOWER_SHA256_RE.fullmatch(embedded_blueprint_hash) is None:
        raise ChiefEngineerBlueprintErrorV1(
            "blueprint provenance embedded hash must be lower-case 64-hex",
            code="blueprint_provenance_hash_invalid",
            details={"field": "blueprint_hash"},
        )
    try:
        recomputed_blueprint_hash = _blueprint_hash(payload)
    except (TypeError, ValueError) as exc:
        raise ChiefEngineerBlueprintErrorV1(
            "blueprint provenance payload cannot be hashed with producer v1 semantics",
            code="blueprint_provenance_payload_invalid",
        ) from exc
    matches = embedded_blueprint_hash == recomputed_blueprint_hash
    if not matches:
        raise ChiefEngineerBlueprintErrorV1(
            "blueprint provenance embedded hash does not match independently recomputed hash",
            code="blueprint_provenance_hash_mismatch",
            details={
                "embedded_blueprint_hash": embedded_blueprint_hash,
                "recomputed_blueprint_hash": recomputed_blueprint_hash,
            },
        )

    raw_pm_task = payload.get("pm_task")
    if not isinstance(raw_pm_task, Mapping) or not raw_pm_task:
        raise ChiefEngineerBlueprintErrorV1(
            "blueprint provenance requires one non-empty canonical pm_task payload",
            code="blueprint_provenance_pm_task_invalid",
            details={"field": "pm_task"},
        )
    pm_task = dict(raw_pm_task)
    expected_pm_task = dict(query.expected_pm_task)
    try:
        pm_task_target_files = _strict_provenance_target_paths(
            "pm_task.target_files",
            pm_task.get("target_files"),
            require_list=True,
        )
    except (TypeError, ValueError) as exc:
        raise ChiefEngineerBlueprintErrorV1(
            "blueprint provenance canonical pm_task target_files are invalid",
            code="blueprint_provenance_pm_target_files_invalid",
            details={"field": "pm_task.target_files", "reason": str(exc)},
        ) from exc
    try:
        expected_target_files = _strict_provenance_target_paths(
            "expected_pm_task.target_files",
            expected_pm_task.get("target_files"),
            require_list=True,
        )
    except (TypeError, ValueError) as exc:
        raise ChiefEngineerBlueprintErrorV1(
            "blueprint provenance expected PM target_files are invalid",
            code="blueprint_provenance_expected_target_files_invalid",
            details={"field": "expected_pm_task.target_files", "reason": str(exc)},
        ) from exc
    if pm_task_target_files != expected_target_files:
        raise ChiefEngineerBlueprintErrorV1(
            "blueprint provenance pm_task target_files do not match expected PM targets",
            code="blueprint_provenance_pm_task_mismatch",
            details={
                "expected_target_files": list(expected_target_files),
                "observed_pm_task_target_files": list(pm_task_target_files),
            },
        )
    try:
        target_files = _strict_provenance_target_paths(
            "target_files",
            payload.get("target_files"),
            require_list=True,
        )
    except (TypeError, ValueError) as exc:
        raise ChiefEngineerBlueprintErrorV1(
            "blueprint provenance target_files are invalid",
            code="blueprint_provenance_target_files_invalid",
            details={"field": "target_files", "reason": str(exc)},
        ) from exc
    completion_contract: ProjectCompletionContractV1 | None = None
    raw_completion_contract = payload.get("project_completion_contract")
    if isinstance(raw_completion_contract, Mapping):
        try:
            completion_contract = ProjectCompletionContractV1.from_dict(raw_completion_contract)
        except (TypeError, ValueError) as exc:
            raise ChiefEngineerBlueprintErrorV1(
                "blueprint provenance project completion contract is invalid",
                code="blueprint_provenance_completion_contract_invalid",
                details={"reason": str(exc)},
            ) from exc

    target_file_set = set(target_files)
    missing_pm_targets = [path for path in expected_target_files if path not in target_file_set]
    completion_owner_by_path = (
        {
            obligation.path: obligation.owner_task_id
            for obligation in completion_contract.obligations.artifacts
        }
        if completion_contract is not None
        else {}
    )
    delegated_pm_targets = {
        path
        for path in missing_pm_targets
        if _pm_task_declares_ce_owned_topology(pm_task)
        and completion_owner_by_path.get(path) not in {None, "", task_id}
    }
    unresolved_missing_pm_targets = [
        path for path in missing_pm_targets if path not in delegated_pm_targets
    ]
    if unresolved_missing_pm_targets:
        raise ChiefEngineerBlueprintErrorV1(
            "blueprint provenance target_files dropped required PM artifacts",
            code="blueprint_provenance_target_files_mismatch",
            details={
                "expected_target_files": list(expected_target_files),
                "observed_target_files": list(target_files),
                "missing_pm_targets": missing_pm_targets,
                "delegated_pm_targets": sorted(delegated_pm_targets),
                "unresolved_missing_pm_targets": unresolved_missing_pm_targets,
            },
        )
    extra_targets = [path for path in target_files if path not in set(expected_target_files)]
    owned_completion_targets: tuple[str, ...] = ()
    project_completion_source_targets: tuple[str, ...] = ()
    if completion_contract is not None:
        owned_completion_targets = tuple(
            obligation.path
            for obligation in completion_contract.obligations.artifacts
            if obligation.owner_task_id == task_id
        )
        project_completion_source_targets = tuple(
            obligation.path
            for obligation in completion_contract.obligations.artifacts
            if _is_ce_source_topology_path(obligation.path)
        )
    missing_owned_completion_targets = [path for path in owned_completion_targets if path not in set(target_files)]
    if missing_owned_completion_targets:
        raise ChiefEngineerBlueprintErrorV1(
            "blueprint provenance target_files dropped CE-owned completion artifacts",
            code="blueprint_provenance_completion_targets_mismatch",
            details={
                "observed_target_files": list(target_files),
                "owned_completion_targets": list(owned_completion_targets),
                "missing_owned_completion_targets": missing_owned_completion_targets,
            },
        )

    ce_source_topology_named = any(_is_ce_source_topology_path(path) for path in extra_targets) or bool(
        project_completion_source_targets
    )
    if _pm_task_declares_ce_owned_topology(pm_task) and not ce_source_topology_named:
        raise ChiefEngineerBlueprintErrorV1(
            "blueprint provenance requires Chief Engineer to name source topology beyond PM toolchain artifacts",
            code="blueprint_provenance_ce_topology_required",
            details={
                "expected_target_files": list(expected_target_files),
                "observed_target_files": list(target_files),
                "extra_targets": extra_targets,
                "project_completion_source_targets": list(project_completion_source_targets),
            },
        )
    try:
        pm_task_canonical_hash = stable_hash(pm_task)
        expected_pm_task_canonical_hash = stable_hash(expected_pm_task)
        recomputed_pm_contract_hash = _blueprint_hash(pm_task)
    except (TypeError, ValueError) as exc:
        raise ChiefEngineerBlueprintErrorV1(
            "blueprint provenance PM task payload cannot be canonically hashed",
            code="blueprint_provenance_pm_task_invalid",
        ) from exc
    if pm_task_canonical_hash != expected_pm_task_canonical_hash:
        raise ChiefEngineerBlueprintErrorV1(
            "blueprint provenance pm_task does not match expected PM task payload",
            code="blueprint_provenance_pm_task_mismatch",
            details={
                "expected_pm_task_canonical_hash": expected_pm_task_canonical_hash,
                "observed_pm_task_canonical_hash": pm_task_canonical_hash,
            },
        )

    pm_contract_hash = _blueprint_provenance_text(
        payload,
        "pm_contract_hash",
        error_code="blueprint_provenance_pm_contract_hash_invalid",
    )
    if _LOWER_SHA256_RE.fullmatch(pm_contract_hash) is None:
        raise ChiefEngineerBlueprintErrorV1(
            "blueprint provenance PM contract hash must be lower-case 64-hex",
            code="blueprint_provenance_pm_contract_hash_invalid",
        )
    if pm_contract_hash != recomputed_pm_contract_hash:
        raise ChiefEngineerBlueprintErrorV1(
            "blueprint provenance PM contract hash does not match canonical pm_task",
            code="blueprint_provenance_pm_contract_hash_mismatch",
            details={
                "embedded_pm_contract_hash": pm_contract_hash,
                "recomputed_pm_contract_hash": recomputed_pm_contract_hash,
            },
        )
    return TaskBlueprintProvenanceSnapshotV1(
        schema_version=_BLUEPRINT_PROVENANCE_SCHEMA_VERSION,
        blueprint_schema_version=blueprint_schema_version,
        hash_scheme=_BLUEPRINT_PROVENANCE_HASH_SCHEME,
        logical_path=canonical_logical_path,
        factory_run_id=factory_run_id,
        task_id=task_id,
        blueprint_id=blueprint_id,
        embedded_blueprint_hash=embedded_blueprint_hash,
        recomputed_blueprint_hash=recomputed_blueprint_hash,
        matches=matches,
        pm_contract_hash=pm_contract_hash,
        recomputed_pm_contract_hash=recomputed_pm_contract_hash,
        pm_task_canonical_hash=pm_task_canonical_hash,
        target_files=target_files,
    )


def _hashable_portfolio_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _hashable_portfolio_payload(item)
            for key, item in value.items()
            if str(key) not in _PORTFOLIO_HASH_IGNORED_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_hashable_portfolio_payload(item) for item in value]
    return value


def _portfolio_hash(payload: Mapping[str, Any]) -> str:
    """Hash canonical portfolio content without self-referential hash fields."""

    return stable_hash(_hashable_portfolio_payload(dict(payload)))


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    rows: list[str] = []
    for item in value:
        token = ""
        if isinstance(item, str):
            token = item.strip()
        elif isinstance(item, dict):
            token = str(
                item.get("path")
                or item.get("file")
                or item.get("target_file")
                or item.get("module")
                or item.get("phase")
                or item.get("action")
                or item.get("command")
                or item.get("verify")
                or item.get("description")
                or item.get("mitigation")
                or item.get("text")
                or item.get("title")
                or item.get("name")
                or item.get("id")
                or item.get("value")
                or ""
            ).strip()
        else:
            token = str(item or "").strip()
        if token:
            rows.append(token)
    return rows


def _semantic_tokens_from_text(value: Any) -> set[str]:
    tokens: set[str] = set()
    text = str(value or "")
    for raw in _SEMANTIC_TOKEN_RE.findall(text):
        for part in _CAMEL_TOKEN_RE.findall(raw):
            token = part.casefold()
            if len(token) < 3 or token in _GENERIC_SEMANTIC_TOKENS:
                continue
            tokens.add(token)
    return tokens


def _semantic_tokens_from_values(*values: Any) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        if isinstance(value, dict):
            tokens.update(_semantic_tokens_from_values(*value.values()))
        elif isinstance(value, (list, tuple, set)):
            tokens.update(_semantic_tokens_from_values(*value))
        else:
            tokens.update(_semantic_tokens_from_text(value))
    return tokens


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _compact_llm_blueprint_value(value: Any, *, depth: int = 0) -> Any:
    """Return a bounded JSON-safe value for advisory CE LLM blueprint data."""
    if depth > 3:
        return str(value)[:240]
    if isinstance(value, dict):
        compact: dict[str, Any] = {}
        for key, item in list(value.items())[:24]:
            token = str(key or "").strip()
            if not token:
                continue
            compact[token] = _compact_llm_blueprint_value(item, depth=depth + 1)
        return compact
    if isinstance(value, (list, tuple)):
        return [_compact_llm_blueprint_value(item, depth=depth + 1) for item in list(value)[:24]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, str):
            return value.strip()[:800]
        return value
    return str(value)[:240]


def _plan_field_strings(plan: dict[str, Any], *keys: str, limit: int = 10) -> list[str]:
    rows: list[str] = []
    seen: set[str] = set()
    for key in keys:
        for item in _string_list(plan.get(key)):
            token = str(item or "").strip()
            marker = token.casefold()
            if not token or marker in seen:
                continue
            seen.add(marker)
            rows.append(token)
            if len(rows) >= limit:
                return rows
    return rows


def _normalize_llm_blueprint_overlay(value: Any) -> dict[str, Any]:
    """Normalize CE LLM output into an advisory-only persisted overlay.

    The overlay enriches handoff context but never owns PM contract fields such
    as target files, dependencies, acceptance criteria, or gate authority.
    """
    raw = _mapping(value)
    if not raw:
        return {}

    construction_plan = _mapping(raw.get("construction_plan"))
    scope_for_apply = _string_list(raw.get("scope_for_apply"))
    risk_flags = _string_list(raw.get("risk_flags"))
    verification_steps = _plan_field_strings(
        construction_plan,
        "verification",
        "verification_steps",
        "verification_commands",
        "quality_gates",
        "gate_commands",
        "tests",
        limit=8,
    )
    implementation_phases = _plan_field_strings(
        construction_plan,
        "preparation",
        "implementation",
        "phases",
        "steps",
        "implementation_steps",
        "milestones",
        limit=8,
    )
    module_boundaries = _plan_field_strings(
        construction_plan,
        "module_boundaries",
        "modules",
        "file_plans",
        "files",
        limit=8,
    )

    overlay: dict[str, Any] = {
        "schema_version": "chief_engineer.llm_blueprint_overlay.v1",
        "source": "chief_engineer.llm_output",
        "authoritative": False,
        "authority": "advisory_only",
        "construction_plan": _compact_llm_blueprint_value(construction_plan),
        "scope_for_apply_advisory": scope_for_apply[:16],
        "risk_flags": risk_flags[:16],
        "implementation_phases": implementation_phases,
        "module_boundaries": module_boundaries,
        "verification_steps": verification_steps,
        "consumed_keys": sorted(str(key) for key in raw),
        "non_overridable_contract_fields": [
            "target_files",
            "scope_paths",
            "acceptance_criteria",
            "execution_checklist",
            "dependencies",
            "handoff_ready",
        ],
    }
    return overlay


def _delivery_product_subject(
    *,
    delivery_depth_contract: dict[str, Any] | None,
    delivery_plan_document: dict[str, Any] | None,
) -> str:
    depth_contract = _mapping(delivery_depth_contract)
    plan_document = _mapping(delivery_plan_document)
    product_intent = _mapping(depth_contract.get("product_intent"))
    product_summary = _mapping(plan_document.get("product_summary"))
    for candidate in (
        product_intent.get("subject"),
        plan_document.get("title"),
        product_summary.get("intent"),
    ):
        token = str(candidate or "").strip()
        if token:
            return token
    return ""


def _planning_contains_delivery_product_subject(
    *,
    objective: str,
    title: str,
    delivery_depth_contract: dict[str, Any] | None,
    delivery_plan_document: dict[str, Any] | None,
) -> bool:
    subject = _delivery_product_subject(
        delivery_depth_contract=delivery_depth_contract,
        delivery_plan_document=delivery_plan_document,
    )
    needle = subject.casefold()
    if len(needle) < 2:
        return False
    haystack = f"{objective}\n{title}".casefold()
    return needle in haystack


def _semantic_terms_from_delivery_contracts(
    *,
    delivery_depth_contract: dict[str, Any] | None,
    delivery_plan_document: dict[str, Any] | None,
) -> list[str]:
    depth_contract = _mapping(delivery_depth_contract)
    plan_document = _mapping(delivery_plan_document)
    product_intent = _mapping(depth_contract.get("product_intent"))
    product_summary = _mapping(plan_document.get("product_summary"))

    candidates: list[Any] = [
        product_intent.get("primary_entities"),
        product_summary.get("core_terms"),
    ]
    terms = _semantic_tokens_from_values(*candidates)
    if not terms:
        terms = _semantic_tokens_from_values(product_intent.get("subject"))
    ordered = sorted(terms)
    return ordered


def _pascal_case(token: str) -> str:
    parts = [part for part in re.split(r"[^A-Za-z0-9]+", str(token or "")) if part]
    return "".join(part[:1].upper() + part[1:] for part in parts)


def _snake_case(token: str) -> str:
    parts = [part.lower() for part in re.split(r"[^A-Za-z0-9]+", str(token or "")) if part]
    return "_".join(parts)


def _module_role_from_path(path: str) -> str:
    normalized = path.replace("\\", "/").lower()
    if "/models/" in normalized or normalized.startswith("models/") or normalized.endswith("_model.py"):
        return "domain_model"
    if "/engine/" in normalized or normalized.startswith("engine/") or "/core/" in normalized:
        return "core_engine"
    if normalized.endswith("main.py") or normalized.endswith("main.go") or "/cmd/" in normalized:
        return "entrypoint"
    if "test" in normalized:
        return "test"
    return "module"


def _module_stem(path: str) -> str:
    normalized = path.replace("\\", "/").rstrip("/")
    filename = normalized.rsplit("/", 1)[-1]
    stem = filename.rsplit(".", 1)[0]
    return stem.strip()


def _module_owner_terms(path: str, semantic_terms: list[str]) -> list[str]:
    normalized = path.replace("\\", "/").lower()
    matches = [term for term in semantic_terms if term and term.lower() in normalized]
    if matches:
        return matches[:4]
    stem = _module_stem(path)
    token = _snake_case(stem).replace("_", " ").strip()
    return [token] if token else []


def _planned_public_symbols(*, path: str, language: str, role: str, owner_terms: list[str]) -> list[str]:
    language_token = language.strip().lower()
    symbols: list[str] = []
    for term in owner_terms:
        if language_token == "javascript" and role in {"domain_model", "module"}:
            pascal = _pascal_case(term)
            candidates = [f"create{pascal}", f"validate{pascal}"] if pascal else []
            if "queue" in term.lower() and pascal:
                candidates.append(pascal)
            for candidate in candidates:
                if candidate and candidate not in symbols:
                    symbols.append(candidate)
            candidate = ""
        elif role == "domain_model":
            candidate = _pascal_case(term)
        elif language_token in {"python", "go", "typescript", "javascript"}:
            candidate = _snake_case(term)
        else:
            candidate = _module_stem(path)
        if candidate and candidate not in symbols:
            symbols.append(candidate)
    stem = _module_stem(path)
    if language_token == "javascript" and role in {"domain_model", "module"}:
        fallback = _snake_case(stem)
    elif role == "domain_model":
        fallback = _pascal_case(stem)
    elif role == "entrypoint":
        fallback = "main"
    else:
        fallback = _snake_case(stem)
    if fallback and fallback not in symbols:
        symbols.append(fallback)
    return symbols[:6]


def _public_symbols_from_export_summary(summary: Any) -> list[str]:
    text = str(summary or "")
    if not text.strip():
        return []
    symbols: list[str] = []

    def add(name: str) -> None:
        token = str(name or "").strip()
        if token and token not in symbols:
            symbols.append(token)

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        for pattern in (
            r"(?:export\s+)?(?:const\s+)?enum\s+([A-Za-z_$][\w$]*)",
            r"(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)",
            r"(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)",
            r"(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)",
            r"(?:export\s+)?interface\s+([A-Za-z_$][\w$]*)",
            r"(?:export\s+)?type\s+([A-Za-z_$][\w$]*)\s*=",
            r"exports\.([A-Za-z_$][\w$]*)",
        ):
            match = re.match(pattern, line)
            if match:
                add(match.group(1))
                break
        export_list = re.match(r"export\s+\{([^}]+)\}", line)
        if export_list:
            for item in export_list.group(1).split(","):
                name = item.strip()
                if not name:
                    continue
                if " as " in name:
                    name = name.rsplit(" as ", 1)[-1].strip()
                add(name)
        module_exports = re.match(r"module\.exports\s*=\s*\{([^}]+)\}", line)
        if module_exports:
            for item in module_exports.group(1).split(","):
                name = item.strip().split(":", 1)[0].strip()
                add(name)
    return symbols[:16]


def _existing_export_symbols_by_path(context: dict[str, Any]) -> dict[str, list[str]]:
    rows = context.get("existing_target_files")
    if not isinstance(rows, (list, tuple)):
        return {}
    by_path: dict[str, list[str]] = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        path = _normalize_blueprint_file_path(item.get("path"))
        if not path:
            continue
        symbols = _public_symbols_from_export_summary(item.get("exports") or item.get("summary"))
        if symbols:
            by_path[path] = symbols
    return by_path


def _summary_line_for_interface_symbol(symbol: Any) -> str:
    name = str(getattr(symbol, "name", "") or "").strip()
    if not name:
        return ""
    kind = str(getattr(symbol, "symbol_kind", "") or "").strip().lower()
    if kind in {"class", "struct"}:
        return f"export class {name}"
    if kind in {"function", "method"}:
        return f"export function {name}"
    if kind in {"interface"}:
        return f"export interface {name}"
    if kind in {"type", "type_alias"}:
        return f"export type {name} ="
    if kind in {"enum"}:
        return f"export enum {name}"
    return f"export const {name}"


def _workspace_existing_target_file_summaries(workspace: str | Path, *, limit: int = 64) -> list[dict[str, str]]:
    """Return bounded actual export summaries from the workspace symbol index.

    Factory normally injects ``existing_target_files`` before CE runs. Direct CE
    callers can bypass that path, so CE keeps its own read-only fallback to avoid
    regressing to heuristic interface guesses.
    """

    try:
        from polaris.kernelone.quality.cross_artifact_interfaces import build_symbol_index_snapshot

        snapshot = build_symbol_index_snapshot(workspace)
    except (OSError, RuntimeError, ValueError, TypeError, ImportError):
        return []
    exports = getattr(snapshot, "namespace_exports", {}) or getattr(snapshot, "physical_exports", {}) or {}
    summaries: list[dict[str, str]] = []
    for path in sorted(exports):
        symbols = exports[path]
        lines: list[str] = []
        seen: set[str] = set()
        for symbol in symbols:
            name = str(getattr(symbol, "name", "") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            line = _summary_line_for_interface_symbol(symbol)
            if line:
                lines.append(line)
            if len(lines) >= 16:
                break
        if lines:
            summaries.append(
                {
                    "path": str(path).replace("\\", "/").strip("/"),
                    "exports": "\n".join(lines),
                    "source": "workspace_symbol_index",
                }
            )
        if len(summaries) >= limit:
            break
    return summaries


def _merge_existing_target_file_summaries(
    primary: Any,
    fallback: list[dict[str, str]],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    if isinstance(primary, (list, tuple)):
        for item in primary:
            if not isinstance(item, dict):
                continue
            path = _normalize_blueprint_file_path(item.get("path"))
            if not path or path in seen:
                continue
            seen.add(path)
            rows.append(dict(item))
    for item in fallback:
        path = _normalize_blueprint_file_path(item.get("path"))
        if not path or path in seen:
            continue
        seen.add(path)
        rows.append(dict(item))
    return rows


_INTERFACE_SNAPSHOT_SOURCE_SUFFIXES = (
    ".py",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".go",
    ".rs",
    ".java",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
)


def _needs_workspace_interface_snapshot(target_files: list[str]) -> bool:
    return any(
        str(path or "").replace("\\", "/").lower().endswith(_INTERFACE_SNAPSHOT_SOURCE_SUFFIXES)
        for path in target_files
    )


def _owner_terms_overlap(left: list[str], right: list[str]) -> bool:
    left_set = {str(item or "").strip().lower() for item in left if str(item or "").strip()}
    right_set = {str(item or "").strip().lower() for item in right if str(item or "").strip()}
    return bool(left_set and right_set and left_set.intersection(right_set))


def _module_interface_contract(
    *,
    target_files: list[str],
    delivery_depth_contract: dict[str, Any] | None,
    delivery_plan_document: dict[str, Any] | None,
    context: dict[str, Any],
) -> dict[str, Any]:
    semantic_terms = _semantic_terms_from_delivery_contracts(
        delivery_depth_contract=delivery_depth_contract,
        delivery_plan_document=delivery_plan_document,
    )
    language = str(
        context.get("language")
        or _mapping(delivery_plan_document).get("language")
        or _mapping(delivery_depth_contract).get("language")
        or ""
    ).strip()
    modules: list[dict[str, Any]] = []
    interface_conflicts: list[dict[str, Any]] = []
    existing_symbols_by_path = _existing_export_symbols_by_path(context)
    raw_existing_rows = context.get("existing_target_files")
    existing_rows = raw_existing_rows if isinstance(raw_existing_rows, (list, tuple)) else ()
    actual_sources = sorted(
        {
            str(item.get("source") or "context_existing_target_files").strip() or "context_existing_target_files"
            for item in existing_rows
            if isinstance(item, dict)
        }
    )
    existing_owner_entries = [
        (path, _module_owner_terms(path, semantic_terms), symbols) for path, symbols in existing_symbols_by_path.items()
    ]
    for path in target_files:
        normalized = str(path or "").replace("\\", "/").strip("/")
        if not normalized:
            continue
        role = _module_role_from_path(normalized)
        owner_terms = _module_owner_terms(normalized, semantic_terms)
        planned_symbols = _planned_public_symbols(
            path=normalized,
            language=language,
            role=role,
            owner_terms=owner_terms,
        )
        module = {
            "path": normalized,
            "role": role,
            "owner_terms": owner_terms,
            "planned_public_symbols": planned_symbols,
            "symbol_source": "heuristic_path_guess",
            "symbol_confidence": 0.35,
        }
        actual_symbols = existing_symbols_by_path.get(normalized)
        if actual_symbols:
            module["actual_public_symbols"] = actual_symbols
            module["symbol_source"] = "actual_export_summary"
            module["symbol_confidence"] = 1.0
        else:
            for existing_path, existing_terms, existing_symbols in existing_owner_entries:
                if existing_path == normalized or not _owner_terms_overlap(owner_terms, existing_terms):
                    continue
                conflict = {
                    "planned_path": normalized,
                    "actual_owner_path": existing_path,
                    "owner_terms": owner_terms,
                    "actual_public_symbols": existing_symbols,
                    "reason": "semantic_owner_already_has_actual_export_summary",
                }
                module["interface_conflict"] = conflict
                module["symbol_source"] = "heuristic_path_guess_with_actual_owner_conflict"
                module["symbol_confidence"] = 0.1
                interface_conflicts.append(conflict)
                break
        modules.append(module)
    if not modules:
        return {}
    contract = {
        "schema_version": "chief_engineer.module_interface_contract.v1",
        "source": "chief_engineer.generate_task_blueprint",
        "authority": "handoff_guidance_not_scope_authority",
        "language": language,
        "modules": modules,
        "actual_interface_snapshot_sources": actual_sources,
        "actual_interface_snapshot_file_count": len(existing_symbols_by_path),
        "rules": [
            "Every symbol imported from a sibling target module must be defined by that module in the same task.",
            "Shared domain types should have one owner module; dependent files must import from that owner instead of redefining.",
            "When implementation needs different symbol names, update the owner module and all import sites together.",
        ],
    }
    if interface_conflicts:
        contract["interface_conflicts"] = interface_conflicts
    return contract


_SEMANTIC_SUPPORT_BOUNDARY_FILENAMES = frozenset(
    {
        "__init__.py",
        "index.js",
        "index.jsx",
        "index.ts",
        "index.tsx",
        "main.go",
        "main.js",
        "main.py",
        "main.rs",
        "main.ts",
        "package-lock.json",
        "package.json",
        "pnpm-lock.yaml",
        "pyproject.toml",
        "requirements.txt",
        "tsconfig.json",
        "yarn.lock",
        # Language-neutral build/module manifests. A sub-task scoped to a
        # manifest boundary (e.g. "project manifest and build contract only")
        # is a STRUCTURAL support boundary, not a semantic domain task, so its
        # generic planning text must not be judged by domain-term similarity.
        # Aligned with the PM language gate's _LANGUAGE_NEUTRAL_FILENAMES.
        "go.mod",
        "go.sum",
        "cmakelists.txt",
        "cargo.toml",
    }
)

_SEMANTIC_SUPPORT_BOUNDARY_SUFFIXES = (
    ".config.js",
    ".config.mjs",
    ".config.ts",
    ".lock",
    ".toml",
    ".yaml",
    ".yml",
)


def _is_semantic_support_boundary_path(path: str) -> bool:
    normalized = str(path or "").replace("\\", "/").strip("/").casefold()
    if not normalized:
        return False
    basename = normalized.rsplit("/", 1)[-1]
    if basename in _SEMANTIC_SUPPORT_BOUNDARY_FILENAMES:
        return True
    if basename.endswith(_SEMANTIC_SUPPORT_BOUNDARY_SUFFIXES):
        return True
    # Behavior/test files are structural support: they verify domain behavior
    # rather than implement it, so a boundary scoped to a manifest plus its
    # behavior test carries no domain implementation. Mirrors the path test in
    # _path_looks_like_test (defined later in this module) without a forward ref.
    return bool(
        normalized.startswith("tests/")
        or "/tests/" in normalized
        or ".test." in basename
        or ".spec." in basename
        or basename.startswith("test_")
        or "_test." in basename
        or basename.endswith("test.java")
    )


def _is_semantic_support_boundary(*path_groups: list[str]) -> bool:
    paths = [path for group in path_groups for path in group if str(path or "").strip()]
    return bool(paths) and all(_is_semantic_support_boundary_path(path) for path in paths)


def _semantic_alignment_audit(
    *,
    objective: str,
    title: str,
    target_files: list[str],
    scope_paths: list[str],
    acceptance_criteria: list[str],
    execution_checklist: list[str],
    delivery_depth_contract: dict[str, Any] | None,
    delivery_plan_document: dict[str, Any] | None,
    llm_blueprint_overlay: dict[str, Any] | None = None,
) -> dict[str, Any]:
    expected_terms = _semantic_terms_from_delivery_contracts(
        delivery_depth_contract=delivery_depth_contract,
        delivery_plan_document=delivery_plan_document,
    )
    if not expected_terms:
        return {
            "ready": True,
            "expected_terms": [],
            "required_term_count": 0,
            "target_file_matches": [],
            "planning_text_matches": [],
            "blueprint_text_matches": [],
            "advisory": [],
            "blockers": [],
        }

    required_term_count = min(len(expected_terms), 3)
    target_tokens = _semantic_tokens_from_values(target_files, scope_paths)
    planning_tokens = _semantic_tokens_from_values(objective, title, acceptance_criteria, execution_checklist)
    blueprint_tokens = _semantic_tokens_from_values(llm_blueprint_overlay or {})
    expected_set = set(expected_terms)
    target_matches = sorted(expected_set & target_tokens)
    planning_matches = sorted(expected_set & planning_tokens)
    blueprint_matches = sorted(expected_set & blueprint_tokens)
    support_boundary = _is_semantic_support_boundary(target_files, scope_paths)

    blockers: list[str] = []
    advisory: list[str] = []
    minimum_target_matches = min(required_term_count, 2)
    if len(target_matches) < minimum_target_matches:
        advisory.append(
            "semantic_alignment.target_files: "
            f"matched {len(target_matches)}/{minimum_target_matches} required domain terms"
        )
    if len(planning_matches) < required_term_count:
        if _planning_contains_delivery_product_subject(
            objective=objective,
            title=title,
            delivery_depth_contract=delivery_depth_contract,
            delivery_plan_document=delivery_plan_document,
        ):
            # Live L2-11: PM/CE planning text names 星际失物招领站 while
            # core_terms stay English (lost/alien/galaxy/clue). ASCII-only
            # tokens then score 0/3 and block a correctly decomposed engine
            # slice. Product-subject identity is on-domain, unlike an
            # English off-domain objective ("flavor recipe" vs "treasure").
            advisory.append(
                "semantic_alignment.plan_text covered by delivery product subject identity: "
                f"matched {len(planning_matches)}/{required_term_count} latin domain terms"
            )
        elif len(target_matches) >= required_term_count:
            advisory.append(
                "semantic_alignment.plan_text covered by scoped domain target files: "
                f"matched {len(target_matches)}/{required_term_count} required domain terms"
            )
        elif len(blueprint_matches) >= required_term_count:
            advisory.append(
                "semantic_alignment.plan_text covered by CE blueprint overlay: "
                f"matched {len(blueprint_matches)}/{required_term_count} required domain terms"
            )
        elif support_boundary:
            advisory.append(
                "semantic_alignment.plan_text deferred to delivery context for support boundary: "
                f"matched {len(planning_matches)}/{required_term_count} required domain terms"
            )
        elif planning_matches and len(set(planning_matches) | set(blueprint_matches)) >= required_term_count:
            # Partial coverage in both PM-scoped planning text and the CE overlay:
            # neither layer covers the domain alone, but their DISTINCT terms
            # together do. The planning text is correct-but-generic for its
            # boundary (e.g. a manifest/go.mod or entrypoint/main.go sub-task
            # whose objective is intentionally scoped to "project manifest and
            # build contract only"), and the CE overlay supplies the remaining
            # domain terms from upstream interface contracts. Use the UNION of
            # distinct matched terms, not the sum, so a term present in both
            # layers is not double-counted into a false pass. Blocking here
            # would punish a correct decomposed plan; a zero-match planning
            # text still blocks.
            advisory.append(
                "semantic_alignment.plan_text partially covered with CE blueprint overlay: "
                f"matched {len(planning_matches)}+{len(blueprint_matches)}/{required_term_count} required domain terms"
            )
        else:
            blockers.append(
                f"semantic_alignment.plan_text: matched {len(planning_matches)}/{required_term_count} required domain terms"
            )

    return {
        "ready": not blockers,
        "expected_terms": expected_terms,
        "required_term_count": required_term_count,
        "target_file_matches": target_matches,
        "planning_text_matches": planning_matches,
        "blueprint_text_matches": blueprint_matches,
        "support_boundary": support_boundary,
        "advisory": advisory,
        "blockers": blockers,
    }


def _first_string_list(*values: Any) -> list[str]:
    for value in values:
        rows = _string_list(value)
        if rows:
            return rows
    return []


def _merge_string_lists(*values: Any) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for value in values:
        for item in _string_list(value):
            token = str(item or "").strip()
            key = token.casefold()
            if not token or key in seen:
                continue
            seen.add(key)
            merged.append(token)
    return merged


def _normalize_blueprint_file_path(value: Any) -> str:
    token = str(value or "").replace("\\", "/").strip()
    if not token or any(char.isspace() for char in token):
        return ""
    if token.startswith(("/", "~")) or "://" in token or "\x00" in token:
        return ""
    if _WINDOWS_DRIVE_PATH_RE.match(token):
        return ""
    while token.startswith("./"):
        token = token[2:]
    parts = [part for part in token.split("/") if part]
    if not parts or any(part == ".." for part in parts):
        return ""
    basename = parts[-1]
    if not ("/" in token or "." in basename or basename.casefold() in _COMMON_EXTENSIONLESS_FILES):
        return ""
    return "/".join(parts)


def _blueprint_declared_file_paths(value: Any, *, parent_key: str = "") -> list[str]:
    rows: list[str] = []
    if isinstance(value, dict):
        for raw_key, item in value.items():
            key = str(raw_key or "").strip().casefold()
            if key in _BLUEPRINT_FILE_PATH_KEYS:
                path = _normalize_blueprint_file_path(item)
                if path:
                    rows.append(path)
                continue
            rows.extend(_blueprint_declared_file_paths(item, parent_key=key))
        return _merge_string_lists(rows)
    if isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, str) and parent_key in _BLUEPRINT_FILE_CONTAINER_KEYS:
                path = _normalize_blueprint_file_path(item)
                if path:
                    rows.append(path)
                continue
            rows.extend(_blueprint_declared_file_paths(item, parent_key=parent_key))
    return _merge_string_lists(rows)


def _task_payload_from_context(context: dict[str, Any]) -> dict[str, Any]:
    if "pm_task_contract" in context:
        pm_task_contract = context.get("pm_task_contract")
        if not isinstance(pm_task_contract, Mapping) or not pm_task_contract:
            raise ChiefEngineerBlueprintErrorV1(
                "explicit Factory pm_task_contract must be a non-empty mapping",
                code="blueprint_pm_task_contract_invalid",
                details={
                    "field": "pm_task_contract",
                    "observed_type": type(pm_task_contract).__name__,
                },
            )
        return dict(pm_task_contract)
    for key in ("task", "pm_task", "source_task", "contract_task"):
        nested = _mapping(context.get(key))
        if nested:
            return nested
    return {}


def _normalize_delivery_depth_payload(payload: dict[str, Any], *, source: str) -> dict[str, Any]:
    if not payload:
        return {}
    if _mapping(payload.get("behavior_contract")):
        return dict(payload)
    if str(payload.get("schema_version") or "") == "polaris.delivery_depth_contract.v1":
        return dict(payload)
    if any(
        key in payload
        for key in (
            "rule_matrix",
            "sample_dataset",
            "edge_cases",
            "required_behavior_tests",
            "behavior_rules",
        )
    ):
        return {
            "schema_version": "polaris.delivery_depth_contract.v1",
            "source": source,
            "behavior_contract": dict(payload),
        }
    return dict(payload)


def _target_files_from_context(context: dict[str, Any]) -> list[str]:
    task_payload = _task_payload_from_context(context)
    target_like = _merge_string_lists(
        context.get("target_files"),
        task_payload.get("target_files"),
        context.get("files"),
        task_payload.get("files"),
        context.get("affected_files"),
        task_payload.get("affected_files"),
    )
    if target_like:
        return target_like
    return _merge_string_lists(context.get("scope_paths"), task_payload.get("scope_paths"))


def _path_looks_like_test(path: str) -> bool:
    normalized = str(path or "").replace("\\", "/").strip().lower()
    name = normalized.rsplit("/", 1)[-1]
    return bool(
        normalized.startswith("tests/")
        or "/tests/" in normalized
        or ".test." in name
        or ".spec." in name
        or name.startswith("test_")
        or "_test." in name
        or name.endswith("test.java")
    )


def _delivery_depth_minimums(delivery_depth_contract: dict[str, Any] | None) -> dict[str, Any]:
    contract = _mapping(delivery_depth_contract)
    for candidate in (
        contract.get("minimums"),
        _mapping(contract.get("level_contract")).get("minimums"),
        _mapping(contract.get("behavior_contract")).get("minimums"),
    ):
        minimums = _mapping(candidate)
        if minimums:
            return minimums
    return {}


def _positive_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _infer_language_from_targets(target_files: list[str]) -> str:
    suffix_to_language = (
        ((".ts", ".tsx"), "typescript"),
        ((".js", ".jsx", ".mjs", ".cjs"), "javascript"),
        ((".py",), "python"),
        ((".go",), "go"),
        ((".rs",), "rust"),
        ((".java",), "java"),
        ((".cpp", ".cc", ".cxx", ".hpp", ".hxx"), "cpp"),
        ((".html",), "html"),
    )
    lowered_paths = [str(path or "").lower() for path in target_files]
    for suffixes, language in suffix_to_language:
        if any(path.endswith(suffixes) for path in lowered_paths):
            return language
    return ""


def _default_delivery_depth_test_target(language: str) -> str:
    normalized = str(language or "").strip().lower()
    if normalized in {"typescript", "ts", "tsx"}:
        return "tests/behavior.test.ts"
    if normalized in {"javascript", "js", "jsx", "node"}:
        return "tests/behavior.test.js"
    if normalized in {"python", "py"}:
        return "tests/test_behavior.py"
    if normalized in {"go", "golang"}:
        return "behavior_test.go"
    if normalized in {"rust", "rs"}:
        return "tests/behavior.rs"
    if normalized == "java":
        return "src/test/java/BehaviorTest.java"
    if normalized in {"cpp", "c++", "cc", "cxx"}:
        return "tests/behavior_test.cpp"
    if normalized in {"html", "html5"}:
        return "tests/behavior.test.js"
    return "tests/behavior.test"


def _delivery_depth_test_target_candidates(language: str, minimum_count: int) -> tuple[str, ...]:
    """Return deterministic, distinct test targets for a project depth contract.

    The delivery-depth verifier counts physical test files.  A CE handoff must
    therefore authorize at least ``min_test_files`` distinct targets; merely
    mentioning the numeric requirement in acceptance text creates an
    impossible Director contract.  Candidate generation stays generic and
    language-shaped: it does not prescribe product source topology.
    """
    primary = _default_delivery_depth_test_target(language)
    path = Path(primary)
    candidates = [primary]
    for index in range(2, max(1, minimum_count) + 1):
        candidates.append(str(path.with_name(f"{path.stem}_{index}{path.suffix}")))
    return tuple(candidates)


def _apply_delivery_depth_test_targets(
    *,
    target_files: list[str],
    scope_paths: list[str],
    acceptance_criteria: list[str],
    execution_checklist: list[str],
    delivery_depth_contract: dict[str, Any] | None,
    context: dict[str, Any],
) -> None:
    minimums = _delivery_depth_minimums(delivery_depth_contract)
    min_test_files = _positive_int(minimums.get("min_test_files"))
    min_test_assertions = _positive_int(minimums.get("min_test_assertions"))
    if min_test_files <= 0 and min_test_assertions <= 0:
        return

    original_target_files = list(target_files)
    original_scope_paths = list(scope_paths)
    language = str(
        context.get("language")
        or _mapping(delivery_depth_contract).get("language")
        or _infer_language_from_targets(target_files)
    ).strip()
    project_declared_test_targets = [
        path for path in _string_list(context.get("project_declared_target_files")) if _path_looks_like_test(path)
    ]
    current_task_test_targets = [path for path in target_files if _path_looks_like_test(path)]
    support_boundary = _is_semantic_support_boundary(original_target_files, original_scope_paths)
    owns_delivery_test_boundary = bool(current_task_test_targets) or (
        not project_declared_test_targets and not support_boundary
    )
    declared_test_targets = list(dict.fromkeys([*project_declared_test_targets, *current_task_test_targets]))
    missing_test_targets = max(0, min_test_files - len(declared_test_targets))
    if owns_delivery_test_boundary and missing_test_targets > 0:
        for candidate in _delivery_depth_test_target_candidates(language, min_test_files):
            if candidate in declared_test_targets:
                continue
            target_files.append(candidate)
            declared_test_targets.append(candidate)
            missing_test_targets -= 1
            if missing_test_targets <= 0:
                break
    if min_test_files > 0:
        for test_target in [path for path in target_files if _path_looks_like_test(path)]:
            if test_target not in scope_paths:
                scope_paths.append(test_target)

    requirement = (
        "Delivery depth contract requires real behavior tests"
        f" (min_test_files={min_test_files}, min_test_assertions={min_test_assertions})."
    )
    if requirement not in acceptance_criteria:
        acceptance_criteria.append(requirement)
    checklist_item = (
        "Create executable behavior tests that assert normal, boundary, and invalid/core-rule outcomes "
        f"and satisfy min_test_assertions={min_test_assertions}."
    )
    if checklist_item not in execution_checklist:
        execution_checklist.append(checklist_item)


def _qa_acceptance_from_task(task_payload: dict[str, Any]) -> list[str]:
    qa_contract = _mapping(task_payload.get("qa_contract"))
    return _first_string_list(qa_contract.get("acceptance_criteria"), qa_contract.get("acceptance"))


def _delivery_depth_contract_from_context(context: dict[str, Any]) -> dict[str, Any]:
    task_payload = _task_payload_from_context(context)
    context_metadata = _mapping(context.get("metadata"))
    task_metadata = _mapping(task_payload.get("metadata"))
    for source, value in (
        ("context.delivery_depth_contract", context.get("delivery_depth_contract")),
        ("context.metadata.delivery_depth_contract", context_metadata.get("delivery_depth_contract")),
        ("task.delivery_depth_contract", task_payload.get("delivery_depth_contract")),
        ("task.metadata.delivery_depth_contract", task_metadata.get("delivery_depth_contract")),
        ("context.behavior_contract", context.get("behavior_contract")),
        ("context.metadata.behavior_contract", context_metadata.get("behavior_contract")),
        ("task.behavior_contract", task_payload.get("behavior_contract")),
        ("task.metadata.behavior_contract", task_metadata.get("behavior_contract")),
    ):
        payload = _normalize_delivery_depth_payload(_mapping(value), source=source)
        if payload:
            return payload
    return {}


def _delivery_plan_document_from_context(context: dict[str, Any]) -> dict[str, Any]:
    task_payload = _task_payload_from_context(context)
    context_metadata = _mapping(context.get("metadata"))
    task_metadata = _mapping(task_payload.get("metadata"))
    for value in (
        context.get("delivery_plan_document"),
        context_metadata.get("delivery_plan_document"),
        task_payload.get("delivery_plan_document"),
        task_metadata.get("delivery_plan_document"),
    ):
        payload = _mapping(value)
        if payload:
            return payload
    return {}


def _blueprint_contract_fields(context: dict[str, Any]) -> dict[str, Any]:
    task_payload = _task_payload_from_context(context)
    acceptance_criteria = _first_string_list(
        context.get("acceptance_criteria"),
        context.get("acceptance"),
        task_payload.get("acceptance_criteria"),
        task_payload.get("acceptance"),
        _qa_acceptance_from_task(task_payload),
    )
    execution_checklist = _first_string_list(
        context.get("execution_checklist"),
        context.get("steps"),
        task_payload.get("execution_checklist"),
        task_payload.get("steps"),
    )
    scope_paths = _first_string_list(
        context.get("scope_paths"),
        context.get("scope"),
        task_payload.get("scope_paths"),
        task_payload.get("scope"),
    )
    dependencies = _first_string_list(
        context.get("dependencies"),
        context.get("depends_on"),
        context.get("blocked_by"),
        task_payload.get("dependencies"),
        task_payload.get("depends_on"),
        task_payload.get("blocked_by"),
    )
    risks = _first_string_list(context.get("risks"), task_payload.get("risks"))
    architecture_decisions = normalize_architecture_decisions(
        context.get("architecture_decisions")
        or context.get("architectureDecision")
        or task_payload.get("architecture_decisions")
        or task_payload.get("architectureDecision")
    )
    delivery_depth_contract = _delivery_depth_contract_from_context(context)
    delivery_plan_document = _delivery_plan_document_from_context(context)
    return {
        "task": task_payload,
        "acceptance_criteria": acceptance_criteria,
        "execution_checklist": execution_checklist,
        "scope_paths": scope_paths,
        "dependencies": dependencies,
        "risks": risks,
        "architecture_decisions": architecture_decisions,
        "delivery_plan_document": delivery_plan_document,
        "delivery_depth_contract": delivery_depth_contract,
    }


def _contract_completeness(
    *,
    objective: str = "",
    title: str = "",
    target_files: list[str],
    scope_paths: list[str] | None = None,
    acceptance_criteria: list[str],
    execution_checklist: list[str],
    delivery_depth_contract: dict[str, Any] | None = None,
    delivery_plan_document: dict[str, Any] | None = None,
    llm_blueprint_overlay: dict[str, Any] | None = None,
) -> dict[str, Any]:
    missing_fields: list[str] = []
    advisory_missing_fields: list[str] = []
    if not target_files:
        missing_fields.append("target_files")
    if not acceptance_criteria:
        missing_fields.append("acceptance_criteria")
    if not execution_checklist:
        missing_fields.append("execution_checklist")
    if not delivery_depth_contract:
        advisory_missing_fields.append("delivery_depth_contract")
    if not delivery_plan_document:
        advisory_missing_fields.append("delivery_plan_document")
    semantic_alignment = _semantic_alignment_audit(
        objective=objective,
        title=title,
        target_files=target_files,
        scope_paths=list(scope_paths or []),
        acceptance_criteria=acceptance_criteria,
        execution_checklist=execution_checklist,
        delivery_depth_contract=delivery_depth_contract,
        delivery_plan_document=delivery_plan_document,
        llm_blueprint_overlay=llm_blueprint_overlay,
    )
    semantic_blockers = list(semantic_alignment["blockers"])
    return {
        "handoff_ready": not missing_fields and not semantic_blockers,
        "missing_fields": missing_fields,
        "advisory_missing_fields": advisory_missing_fields,
        "semantic_alignment": semantic_alignment,
        "semantic_blockers": semantic_blockers,
        "depth_contract_ready": not advisory_missing_fields,
        "requires": ["target_files", "acceptance_criteria", "execution_checklist"],
        "advisory_requires": ["delivery_plan_document", "delivery_depth_contract"],
    }


def _tuple_from_payload(value: Any) -> tuple[str, ...]:
    return tuple(_string_list(value))


def _existing_target_files_from_payload(payload: dict[str, Any]) -> tuple[dict[str, str], ...]:
    raw = payload.get("existing_target_files") or _mapping(payload.get("context")).get("existing_target_files")
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(dict(item) for item in raw if isinstance(item, dict))


def _normalize_task_token(token: str) -> str:
    """Normalize task identifiers for comparison.

    Delegates to the canonical SSoT in
    ``polaris.kernelone.tasks.task_tokens`` (§9.5). Strips common prefixes
    (``TASK-``, ``task-``, ``task_``) so that ``"TASK-1"`` and ``"1"`` compare
    equal. This bridges the PM taskboard (numeric IDs) and CE blueprints
    (``TASK-N`` prefixed IDs).
    """
    return normalize_task_token(token)


def _latest_blueprint_for_task(
    persistence: BlueprintPersistence,
    *,
    task_id: str,
    run_id: str | None,
) -> tuple[str, dict[str, Any]] | None:
    matches: list[tuple[str, str, dict[str, Any]]] = []
    normalized_query = _normalize_task_token(task_id)
    for blueprint_id in persistence.list_all():
        payload = persistence.load(blueprint_id)
        if not isinstance(payload, dict):
            continue
        stored_task_id = str(payload.get("task_id") or "").strip()
        # Exact match first, then normalized match for TASK-N ↔ N bridging.
        if stored_task_id != task_id and _normalize_task_token(stored_task_id) != normalized_query:
            continue
        payload_run_id = str(payload.get("run_id") or "").strip()
        if run_id and payload_run_id != run_id:
            continue
        updated_at = str(payload.get("updated_at") or payload.get("created_at") or "").strip()
        matches.append((updated_at, blueprint_id, payload))
    if not matches:
        return None
    _updated_at, blueprint_id, payload = max(matches, key=lambda item: (item[0], item[1]))
    return blueprint_id, payload


logger = logging.getLogger(__name__)
