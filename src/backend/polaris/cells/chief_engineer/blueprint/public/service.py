"""Stable public service exports for `chief_engineer.blueprint`."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal

from polaris.cells.control_plane.run_ledger.public import stable_hash
from polaris.cells.director.tasking.public.service import (
    build_director_execution_profile_snapshot,
)
from polaris.kernelone.quality.file_ownership_ledger import record_task_file_owners

from ..internal.adr_log import ADRDecisionLog, build_adr_event
from ..internal.architecture_decisions import (
    infer_architecture_decisions,
    merge_architecture_decisions,
    normalize_architecture_decisions,
    selected_libraries_from_decisions,
)
from ..internal.blueprint_persistence import BlueprintPersistence
from ..internal.ce_consumer import CEConsumer
from ..internal.chief_engineer_agent import ChiefEngineerAgent
from ..internal.chief_engineer_preflight import run_pre_dispatch_chief_engineer
from ..internal.handoff import build_handoff_decision
from ..internal.post_mortem import PostMortemLog, build_post_mortem_event
from ..internal.quality_gate import evaluate_quality_gate
from ..internal.release_readiness import build_release_readiness
from ..internal.risks import RiskRegister, build_risk_event
from ..internal.rollback_guard import create_rollback_guard
from ..internal.rollback_link import build_rollback_link
from ..internal.tech_debt import TechDebtLedger, build_tech_debt_event
from ..internal.tech_radar import TechRadarLedger, build_tech_radar_event
from .contracts import (
    ADRRecordV1,
    BuildChiefEngineerBlueprintPortfolioCommandV1,
    CeHandoffDecisionBindingsV1,
    CeHandoffDecisionV1,
    ChiefEngineerBlueprintErrorV1,
    ChiefEngineerBlueprintPortfolioV1,
    ChiefEngineerPortfolioTaskV1,
    ChiefEngineerProjectInterfaceContractV1,
    GenerateTaskBlueprintCommandV1,
    GetBlueprintStatusQueryV1,
    GovernanceSummaryV1,
    HandoffDecisionV1,
    ListADRsQueryV1,
    ListPostMortemsQueryV1,
    ListRisksQueryV1,
    ListTechDebtQueryV1,
    ListTechRadarQueryV1,
    PostMortemRecordV1,
    QueryBlueprintProvenanceV1,
    RegisterADRCommandV1,
    RegisterPostMortemCommandV1,
    RegisterRiskCommandV1,
    RegisterTechDebtCommandV1,
    RegisterTechRadarCommandV1,
    ReleaseReadinessV1,
    RiskRecordV1,
    StackPolicyViolationV1,
    TaskBlueprintProvenanceSnapshotV1,
    TaskBlueprintResultV1,
    TechDebtRecordV1,
    TechRadarEntryV1,
    UpdateADRStatusCommandV1,
    UpdatePostMortemStatusCommandV1,
    UpdateRiskStatusCommandV1,
    UpdateTechDebtStatusCommandV1,
    UpdateTechRadarRingCommandV1,
    _strict_provenance_target_paths,
)

_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_LOWER_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_SEMANTIC_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*")
_CAMEL_TOKEN_RE = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|[0-9]+")
_WINDOWS_DRIVE_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")
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
    if target_files != expected_target_files:
        raise ChiefEngineerBlueprintErrorV1(
            "blueprint provenance target_files do not match expected PM targets",
            code="blueprint_provenance_target_files_mismatch",
            details={
                "expected_target_files": list(expected_target_files),
                "observed_target_files": list(target_files),
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
        if len(target_matches) >= required_term_count:
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
    promote_default_test_target = (
        min_test_files > 0
        and not any(_path_looks_like_test(path) for path in target_files)
        and not project_declared_test_targets
        and not _is_semantic_support_boundary(original_target_files, original_scope_paths)
    )
    if promote_default_test_target:
        test_target = _default_delivery_depth_test_target(language)
        if test_target not in target_files:
            target_files.append(test_target)
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

    Strips common prefixes (``TASK-``, ``task-``, ``task_``) so that
    ``"TASK-1"`` and ``"1"`` compare equal.  This bridges the PM taskboard
    (numeric IDs) and CE blueprints (``TASK-N`` prefixed IDs).
    """
    import re

    t = str(token or "").strip().lower()
    t = re.sub(r"^(task[-_])+", "", t)
    return t


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


@dataclass(frozen=True)
class _PortfolioLlmBlueprint:
    shared_plan: dict[str, Any]
    task_plans: dict[str, dict[str, Any]]
    scope_paths: tuple[str, ...]
    scope_rejections: tuple[dict[str, str], ...]
    risk_flags: tuple[str, ...]
    provider_declarations: tuple[dict[str, Any], ...]
    consumer_declarations: tuple[dict[str, Any], ...]
    consumed: bool


def _portfolio_contract_error(
    message: str,
    *,
    code: str = "invalid_blueprint_portfolio_input",
    details: Mapping[str, Any] | None = None,
) -> ChiefEngineerBlueprintErrorV1:
    return ChiefEngineerBlueprintErrorV1(message, code=code, details=details)


def _portfolio_array(value: Any, *, field_name: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise _portfolio_contract_error(
            f"{field_name} must be a JSON array",
            details={"field": field_name, "actual_type": type(value).__name__},
        )
    return list(value)


def _portfolio_mapping(value: Any, *, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _portfolio_contract_error(
            f"{field_name} must be a JSON object",
            details={"field": field_name, "actual_type": type(value).__name__},
        )

    result: dict[str, Any] = {}
    for raw_key, item in value.items():
        key = str(raw_key).strip()
        if not key:
            raise _portfolio_contract_error(
                f"{field_name} contains an empty object key",
                details={"field": field_name},
            )
        if key in result:
            raise _portfolio_contract_error(
                f"{field_name} contains duplicate normalized key {key!r}",
                details={"field": field_name, "key": key},
            )
        result[key] = item
    return result


def _normalize_portfolio_advisory_path(value: str) -> tuple[str, str]:
    token = str(value).strip()
    if not token:
        return "", "empty_path"
    if "\x00" in token:
        return "", "null_byte"
    if "://" in token:
        return "", "uri_not_workspace_path"
    if token.startswith("~"):
        return "", "home_expansion_not_allowed"

    windows_path = PureWindowsPath(token)
    path = PurePosixPath(token.replace("\\", "/"))
    if windows_path.drive or windows_path.root or path.is_absolute():
        return "", "absolute_path_not_allowed"
    if any(part == ".." for part in path.parts):
        return "", "parent_traversal_not_allowed"

    parts = tuple(part for part in path.parts if part not in {"", "."})
    if not parts:
        return "", "workspace_root_not_allowed"
    return PurePosixPath(*parts).as_posix(), ""


def _scope_entry_text(item: Any) -> tuple[str, str]:
    if isinstance(item, str):
        return item, ""
    if isinstance(item, Mapping):
        for key in ("path", "file", "target_file", "target_path", "scope_path", "value"):
            candidate = item.get(key)
            if isinstance(candidate, str):
                return candidate, ""
        keys = ",".join(sorted(str(key) for key in item))[:240]
        return f"<mapping:{keys}>", "missing_path_field"
    return f"<{type(item).__name__}>", "unsupported_scope_entry_type"


def _parse_scope_suggestions(
    value: Any,
    *,
    field_name: str,
    source: str,
) -> tuple[tuple[str, ...], tuple[dict[str, str], ...]]:
    entries = _portfolio_array(value, field_name=field_name)
    accepted: list[str] = []
    rejected: list[dict[str, str]] = []
    seen_paths: set[str] = set()
    seen_rejections: set[tuple[str, str, str]] = set()

    for item in entries:
        display, entry_error = _scope_entry_text(item)
        path, path_error = _normalize_portfolio_advisory_path(display) if not entry_error else ("", "")
        reason = entry_error or path_error
        if reason:
            rejection = {"path": display[:800], "reason": reason, "source": source}
            marker = (rejection["path"], reason, source)
            if marker not in seen_rejections:
                seen_rejections.add(marker)
                rejected.append(rejection)
            continue
        if path not in seen_paths:
            seen_paths.add(path)
            accepted.append(path)
    return tuple(accepted), tuple(rejected)


def _merge_scope_paths(*values: tuple[str, ...]) -> tuple[str, ...]:
    rows: list[str] = []
    seen: set[str] = set()
    for value in values:
        for path in value:
            if path in seen:
                continue
            seen.add(path)
            rows.append(path)
    return tuple(rows)


def _merge_scope_rejections(
    *values: tuple[dict[str, str], ...],
) -> tuple[dict[str, str], ...]:
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for value in values:
        for rejection in value:
            marker = (
                str(rejection.get("path") or ""),
                str(rejection.get("reason") or ""),
                str(rejection.get("source") or ""),
            )
            if marker in seen:
                continue
            seen.add(marker)
            rows.append(dict(rejection))
    return tuple(rows)


def _plan_path_suggestions(
    value: Any,
    *,
    parent_key: str = "",
) -> tuple[tuple[str, ...], tuple[dict[str, str], ...]]:
    candidates: list[Any] = []
    if isinstance(value, Mapping):
        valid_paths: list[str] = []
        rejected_paths: list[dict[str, str]] = []
        for raw_key, item in value.items():
            key = str(raw_key).strip().casefold()
            if key in _BLUEPRINT_FILE_PATH_KEYS:
                candidates.append(item)
                continue
            nested_valid, nested_rejected = _plan_path_suggestions(item, parent_key=key)
            valid_paths.extend(nested_valid)
            rejected_paths.extend(nested_rejected)
        direct_valid, direct_rejected = _parse_scope_suggestions(
            candidates,
            field_name="construction_plan paths",
            source="construction_plan",
        )
        return (
            _merge_scope_paths(tuple(valid_paths), direct_valid),
            _merge_scope_rejections(tuple(rejected_paths), direct_rejected),
        )
    if isinstance(value, (list, tuple)):
        valid_paths = []
        rejected_paths = []
        for item in value:
            if isinstance(item, str) and parent_key in _BLUEPRINT_FILE_CONTAINER_KEYS:
                candidates.append(item)
                continue
            nested_valid, nested_rejected = _plan_path_suggestions(item, parent_key=parent_key)
            valid_paths.extend(nested_valid)
            rejected_paths.extend(nested_rejected)
        direct_valid, direct_rejected = _parse_scope_suggestions(
            candidates,
            field_name="construction_plan paths",
            source="construction_plan",
        )
        return (
            _merge_scope_paths(tuple(valid_paths), direct_valid),
            _merge_scope_rejections(tuple(rejected_paths), direct_rejected),
        )
    return (), ()


def _readable_portfolio_value(value: Any, *, depth: int = 0) -> str:
    if depth > 4:
        raise _portfolio_contract_error("risk flag mapping exceeds the supported nesting depth")
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Mapping):
        parts = [
            f"{str(key).strip()}={_readable_portfolio_value(item, depth=depth + 1)}"
            for key, item in sorted(value.items(), key=lambda row: str(row[0]))
            if str(key).strip()
        ]
        return ", ".join(part for part in parts if part)
    if isinstance(value, (list, tuple)):
        return ", ".join(part for part in (_readable_portfolio_value(item, depth=depth + 1) for item in value) if part)
    raise _portfolio_contract_error(
        "risk flag contains a non-JSON value",
        details={"actual_type": type(value).__name__},
    )


def _portfolio_risk_flag(item: Any, *, field_name: str) -> str:
    if isinstance(item, str):
        token = item.strip()
        if not token:
            raise _portfolio_contract_error(f"{field_name} contains an empty risk string")
        return token[:1200]
    if not isinstance(item, Mapping):
        raise _portfolio_contract_error(
            f"{field_name} entries must be strings or objects",
            details={"field": field_name, "actual_type": type(item).__name__},
        )

    risk = _portfolio_mapping(item, field_name=f"{field_name} entry")
    severity_value = risk.get("severity") or risk.get("risk_level") or risk.get("level")
    label_value = (
        risk.get("title")
        or risk.get("name")
        or risk.get("risk")
        or risk.get("description")
        or risk.get("message")
        or risk.get("summary")
    )
    mitigation_value = risk.get("mitigation") or risk.get("response") or risk.get("control")
    severity = _readable_portfolio_value(severity_value).strip() if severity_value is not None else ""
    label = _readable_portfolio_value(label_value).strip() if label_value is not None else ""
    mitigation = _readable_portfolio_value(mitigation_value).strip() if mitigation_value is not None else ""
    if label:
        prefix = f"[{severity.casefold()}] " if severity else ""
        suffix = f" (mitigation: {mitigation})" if mitigation else ""
        return f"{prefix}{label}{suffix}"[:1200]

    fallback = _readable_portfolio_value(risk).strip()
    if not fallback:
        raise _portfolio_contract_error(f"{field_name} contains an empty risk object")
    return fallback[:1200]


def _normalize_portfolio_risk_flags(value: Any, *, field_name: str) -> tuple[str, ...]:
    rows: list[str] = []
    seen: set[str] = set()
    for item in _portfolio_array(value, field_name=field_name):
        risk = _portfolio_risk_flag(item, field_name=field_name)
        marker = risk.casefold()
        if marker in seen:
            continue
        seen.add(marker)
        rows.append(risk)
    return tuple(rows)


def _merge_risk_flags(*values: tuple[str, ...]) -> tuple[str, ...]:
    rows: list[str] = []
    seen: set[str] = set()
    for value in values:
        for risk in value:
            marker = risk.casefold()
            if marker in seen:
                continue
            seen.add(marker)
            rows.append(risk)
    return tuple(rows)


def _merge_portfolio_construction_plan(
    shared_plan: Mapping[str, Any],
    task_plan: Mapping[str, Any],
) -> dict[str, Any]:
    merged = _mapping(_compact_llm_blueprint_value(dict(shared_plan)))
    for raw_key, item in task_plan.items():
        key = str(raw_key).strip()
        if not key:
            raise _portfolio_contract_error("task construction plan contains an empty key")
        current = merged.get(key)
        if isinstance(current, Mapping) and isinstance(item, Mapping):
            merged[key] = _merge_portfolio_construction_plan(current, item)
        elif isinstance(current, list) and isinstance(item, (list, tuple)):
            merged[key] = _compact_llm_blueprint_value([*current, *item])
        else:
            merged[key] = _compact_llm_blueprint_value(item)
    return merged


def _normalize_interface_declarations(value: Any, *, field_name: str) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in _portfolio_array(value, field_name=field_name):
        if isinstance(item, str):
            token = item.strip()
            if not token:
                raise _portfolio_contract_error(f"{field_name} contains an empty declaration")
            declaration: dict[str, Any] = {"declaration": token}
        elif isinstance(item, Mapping):
            declaration = _mapping(_compact_llm_blueprint_value(dict(item)))
            if not declaration:
                raise _portfolio_contract_error(f"{field_name} contains an empty declaration object")
        else:
            raise _portfolio_contract_error(
                f"{field_name} entries must be strings or objects",
                details={"field": field_name, "actual_type": type(item).__name__},
            )
        marker = stable_hash(declaration)
        if marker in seen:
            continue
        seen.add(marker)
        rows.append(declaration)
    return tuple(rows)


def _parse_portfolio_llm_blueprint(
    command: BuildChiefEngineerBlueprintPortfolioCommandV1,
) -> _PortfolioLlmBlueprint:
    raw = dict(command.llm_blueprint)
    if not raw:
        return _PortfolioLlmBlueprint({}, {}, (), (), (), (), (), False)

    allowed_top_level = {"construction_plan", "risk_flags", "scope_for_apply"}
    unknown_top_level = sorted(str(key) for key in raw if str(key) not in allowed_top_level)
    if unknown_top_level:
        raise _portfolio_contract_error(
            "LLM portfolio blueprint contains unknown top-level fields",
            details={"unknown_fields": unknown_top_level},
        )

    construction_plan = _portfolio_mapping(
        raw.get("construction_plan", {}),
        field_name="construction_plan",
    )
    raw_task_plans = construction_plan.pop("task_plans", {})
    task_plan_mapping = _portfolio_mapping(raw_task_plans, field_name="construction_plan.task_plans")
    task_ids = {task.task_id for task in command.tasks}
    unknown_task_ids = sorted(set(task_plan_mapping) - task_ids)
    if unknown_task_ids:
        raise _portfolio_contract_error(
            "construction_plan.task_plans references unknown PM tasks",
            code="unknown_blueprint_portfolio_task_plan",
            details={"unknown_task_ids": unknown_task_ids, "task_ids": sorted(task_ids)},
        )

    task_plans: dict[str, dict[str, Any]] = {}
    for task_id, task_plan in task_plan_mapping.items():
        task_plans[task_id] = _portfolio_mapping(
            task_plan,
            field_name=f"construction_plan.task_plans[{task_id!r}]",
        )

    interface_payload = _portfolio_mapping(
        construction_plan.pop("project_interface_contract", {}),
        field_name="construction_plan.project_interface_contract",
    )
    allowed_interface_keys = {
        "consumer_declarations",
        "consumers",
        "provider_declarations",
        "providers",
    }
    unknown_interface_keys = sorted(set(interface_payload) - allowed_interface_keys)
    if unknown_interface_keys:
        raise _portfolio_contract_error(
            "project_interface_contract contains unsupported fields",
            details={"unknown_fields": unknown_interface_keys},
        )
    providers = _normalize_interface_declarations(
        interface_payload.get("provider_declarations", interface_payload.get("providers", [])),
        field_name="project_interface_contract.provider_declarations",
    )
    consumers = _normalize_interface_declarations(
        interface_payload.get("consumer_declarations", interface_payload.get("consumers", [])),
        field_name="project_interface_contract.consumer_declarations",
    )
    scope_paths, scope_rejections = _parse_scope_suggestions(
        raw.get("scope_for_apply", []),
        field_name="scope_for_apply",
        source="shared_scope_for_apply",
    )
    risk_flags = _normalize_portfolio_risk_flags(
        raw.get("risk_flags", []),
        field_name="risk_flags",
    )
    shared_plan = _mapping(_compact_llm_blueprint_value(construction_plan))
    consumed = bool(
        shared_plan or task_plans or scope_paths or scope_rejections or risk_flags or providers or consumers
    )
    return _PortfolioLlmBlueprint(
        shared_plan=shared_plan,
        task_plans=task_plans,
        scope_paths=scope_paths,
        scope_rejections=scope_rejections,
        risk_flags=risk_flags,
        provider_declarations=providers,
        consumer_declarations=consumers,
        consumed=consumed,
    )


def _task_plan_components(
    task_id: str,
    task_plan: Mapping[str, Any],
) -> tuple[dict[str, Any], tuple[str, ...], tuple[dict[str, str], ...], tuple[str, ...]]:
    plan = dict(task_plan)
    if "project_interface_contract" in plan:
        raise _portfolio_contract_error(
            "project_interface_contract must be shared, not task-local",
            details={"task_id": task_id},
        )
    scope_paths, scope_rejections = _parse_scope_suggestions(
        plan.pop("scope_for_apply", []),
        field_name=f"construction_plan.task_plans[{task_id!r}].scope_for_apply",
        source=f"task_plan:{task_id}",
    )
    risk_flags = _normalize_portfolio_risk_flags(
        plan.pop("risk_flags", []),
        field_name=f"construction_plan.task_plans[{task_id!r}].risk_flags",
    )
    return plan, scope_paths, scope_rejections, risk_flags


def _scope_advisory_for_task(
    task: ChiefEngineerPortfolioTaskV1,
    *,
    requested_paths: tuple[str, ...],
    rejected_suggestions: tuple[dict[str, str], ...],
    construction_plan: Mapping[str, Any],
) -> tuple[tuple[str, ...], dict[str, Any]]:
    pm_authorized_paths = {*task.target_files, *task.scope_paths}
    accepted_paths = tuple(path for path in requested_paths if path in pm_authorized_paths)
    rejected = list(rejected_suggestions)
    for path in requested_paths:
        if path not in pm_authorized_paths:
            rejected.append(
                {
                    "path": path,
                    "reason": "outside_pm_task_authority",
                    "source": "scope_for_apply",
                }
            )

    plan_paths, invalid_plan_paths = _plan_path_suggestions(construction_plan)
    plan_paths_outside_authority = tuple(path for path in plan_paths if path not in pm_authorized_paths)
    advisory = {
        "schema_version": "chief_engineer.blueprint_portfolio.scope_advisory.v1",
        "task_id": task.task_id,
        "authority": "pm_task_contract",
        "requested_paths": list(requested_paths),
        "authorized_advisory_paths": list(accepted_paths),
        "rejected_suggestions": [dict(item) for item in _merge_scope_rejections(tuple(rejected))],
        "pm_target_files": list(task.target_files),
        "pm_scope_paths": list(task.scope_paths),
        "construction_plan_declared_paths": list(plan_paths),
        "construction_plan_paths_outside_pm_authority": list(plan_paths_outside_authority),
        "construction_plan_rejected_paths": [dict(item) for item in invalid_plan_paths],
        "scope_expansion_allowed": False,
    }
    return accepted_paths, advisory


def _deterministic_portfolio_plan(task: ChiefEngineerPortfolioTaskV1) -> dict[str, Any]:
    return {
        "source": "chief_engineer.deterministic_pm_task_projection",
        "diagnostic_only": True,
        "objective": task.objective,
        "target_files": list(task.target_files),
        "scope_paths": list(task.scope_paths),
        "dependencies": list(task.dependencies),
    }


def _project_interface_seed(
    tasks: tuple[ChiefEngineerPortfolioTaskV1, ...],
    *,
    provider_declarations: tuple[dict[str, Any], ...],
    consumer_declarations: tuple[dict[str, Any], ...],
) -> tuple[dict[str, Any], dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
    task_file_ownership = {task.task_id: task.target_files for task in tasks}
    file_owners: dict[str, list[str]] = {}
    for task in tasks:
        for path in task.target_files:
            file_owners.setdefault(path, []).append(task.task_id)
    file_task_ownership = {path: tuple(task_ids) for path, task_ids in file_owners.items()}
    seed = {
        "schema_version": "chief_engineer.project_interface_contract.v1",
        "task_file_ownership": {task_id: list(paths) for task_id, paths in task_file_ownership.items()},
        "file_task_ownership": {path: list(task_ids) for path, task_ids in file_task_ownership.items()},
        "provider_declarations": [dict(item) for item in provider_declarations],
        "consumer_declarations": [dict(item) for item in consumer_declarations],
        "ownership_authority": "pm_authoritative_tasks",
        "interface_declaration_authority": "chief_engineer_advisory_only",
        "authoritative": False,
    }
    return seed, task_file_ownership, file_task_ownership


def _bind_portfolio_task_overlays(
    task_overlays: Mapping[str, Mapping[str, Any]],
    *,
    portfolio_id: str,
    portfolio_path: str,
    portfolio_hash: str,
    project_interface_contract_ref: str,
    project_interface_contract_hash: str,
    llm_blueprint_consumed: bool,
    usage_mode: Literal["advisory_overlay", "offline_diagnostic_only"],
) -> dict[str, dict[str, Any]]:
    bound: dict[str, dict[str, Any]] = {}
    for task_id, overlay in task_overlays.items():
        reference = {
            "schema_version": "chief_engineer.blueprint_portfolio.task_reference.v1",
            "task_id": task_id,
            "portfolio_id": portfolio_id,
            "portfolio_path": portfolio_path,
            "portfolio_hash": portfolio_hash,
            "project_interface_contract_ref": project_interface_contract_ref,
            "project_interface_contract_hash": project_interface_contract_hash,
        }
        bound[task_id] = {
            "construction_plan": _mapping(overlay.get("construction_plan")),
            "scope_for_apply": list(_string_list(overlay.get("scope_for_apply"))),
            "risk_flags": list(_string_list(overlay.get("risk_flags"))),
            "portfolio_id": portfolio_id,
            "portfolio_path": portfolio_path,
            "portfolio_hash": portfolio_hash,
            "project_interface_contract_ref": project_interface_contract_ref,
            "project_interface_contract_hash": project_interface_contract_hash,
            "reference": reference,
            "llm_blueprint_consumed": llm_blueprint_consumed,
            "usage_mode": usage_mode,
            "authority": "advisory_only",
            "handoff_ready": False,
            "execution_authorized": False,
        }
    return bound


def _persist_immutable_blueprint_portfolio(portfolio: ChiefEngineerBlueprintPortfolioV1) -> None:
    persistence = BlueprintPersistence(portfolio.workspace)
    expected_payload = portfolio.to_dict()
    try:
        existing_ids = set(persistence.list_all())
        existing_payload = persistence.load(portfolio.portfolio_id)
    except OSError as exc:
        raise _portfolio_contract_error(
            "failed to inspect existing blueprint portfolio",
            code="blueprint_portfolio_persistence_failed",
            details={"portfolio_id": portfolio.portfolio_id, "operation": "inspect"},
        ) from exc

    if portfolio.portfolio_id in existing_ids and existing_payload is None:
        raise _portfolio_contract_error(
            "existing blueprint portfolio is unreadable and cannot be replaced",
            code="blueprint_portfolio_immutability_conflict",
            details={"portfolio_id": portfolio.portfolio_id},
        )
    if existing_payload is not None:
        existing_hash = str(existing_payload.get("portfolio_hash") or "").strip()
        if (
            existing_hash != portfolio.portfolio_hash
            or _portfolio_hash(existing_payload) != portfolio.portfolio_hash
            or existing_payload != expected_payload
        ):
            raise _portfolio_contract_error(
                "immutable blueprint portfolio content conflict",
                code="blueprint_portfolio_immutability_conflict",
                details={
                    "portfolio_id": portfolio.portfolio_id,
                    "expected_hash": portfolio.portfolio_hash,
                    "existing_hash": existing_hash,
                },
            )
        return

    try:
        persistence.save(portfolio.portfolio_id, expected_payload)
    except OSError as exc:
        raise _portfolio_contract_error(
            "failed to persist blueprint portfolio",
            code="blueprint_portfolio_persistence_failed",
            details={"portfolio_id": portfolio.portfolio_id, "operation": "save"},
        ) from exc

    persisted_payload = persistence.load(portfolio.portfolio_id)
    if persisted_payload != expected_payload or _portfolio_hash(persisted_payload or {}) != portfolio.portfolio_hash:
        raise _portfolio_contract_error(
            "persisted blueprint portfolio failed hash verification",
            code="blueprint_portfolio_persistence_verification_failed",
            details={"portfolio_id": portfolio.portfolio_id},
        )


def build_chief_engineer_blueprint_portfolio(
    command: BuildChiefEngineerBlueprintPortfolioCommandV1,
) -> ChiefEngineerBlueprintPortfolioV1:
    """Build and immutably persist one advisory portfolio for PM tasks.

    The canonical LLM payload is consumed once. Every task receives a merged
    shared/task plan, while scope suggestions are intersected with that task's
    PM-authoritative target and scope paths. A no-LLM command creates an
    explicitly offline diagnostic object and never grants handoff authority.
    """

    parsed = _parse_portfolio_llm_blueprint(command)
    usage_mode: Literal["advisory_overlay", "offline_diagnostic_only"] = (
        "advisory_overlay" if parsed.consumed else "offline_diagnostic_only"
    )
    task_overlays: dict[str, dict[str, Any]] = {}
    scope_advisory: dict[str, dict[str, Any]] = {}
    portfolio_risks: tuple[str, ...] = parsed.risk_flags

    for task in command.tasks:
        if parsed.consumed:
            task_plan, task_scope, task_rejections, task_risks = _task_plan_components(
                task.task_id,
                parsed.task_plans.get(task.task_id, {}),
            )
            construction_plan = _merge_portfolio_construction_plan(parsed.shared_plan, task_plan)
            requested_scope = _merge_scope_paths(parsed.scope_paths, task_scope)
            rejected_scope = _merge_scope_rejections(parsed.scope_rejections, task_rejections)
            risks = _merge_risk_flags(parsed.risk_flags, task_risks)
        else:
            construction_plan = _deterministic_portfolio_plan(task)
            requested_scope = _merge_scope_paths(task.target_files, task.scope_paths)
            rejected_scope = ()
            risks = ()

        authorized_scope, task_scope_advisory = _scope_advisory_for_task(
            task,
            requested_paths=requested_scope,
            rejected_suggestions=rejected_scope,
            construction_plan=construction_plan,
        )
        task_overlays[task.task_id] = {
            "construction_plan": construction_plan,
            "scope_for_apply": list(authorized_scope),
            "risk_flags": list(risks),
        }
        scope_advisory[task.task_id] = task_scope_advisory
        portfolio_risks = _merge_risk_flags(portfolio_risks, risks)

    interface_seed, task_file_ownership, file_task_ownership = _project_interface_seed(
        command.tasks,
        provider_declarations=parsed.provider_declarations,
        consumer_declarations=parsed.consumer_declarations,
    )
    interface_hash = stable_hash(interface_seed)
    interface_id = f"ce_project_interface_{interface_hash[:24]}"
    identity_seed = {
        "schema_version": "chief_engineer.blueprint_portfolio.identity.v1",
        "workspace": command.workspace,
        "run_id": command.run_id,
        "tasks": [task.to_dict() for task in command.tasks],
        "task_overlays": task_overlays,
        "scope_advisory": scope_advisory,
        "risk_flags": list(portfolio_risks),
        "project_interface_contract_hash": interface_hash,
        "llm_blueprint_consumed": parsed.consumed,
        "usage_mode": usage_mode,
    }
    portfolio_id = f"ce_portfolio_{stable_hash(identity_seed)[:32]}"
    portfolio_path = _blueprint_path(portfolio_id)
    interface_ref = f"{portfolio_path}#project_interface_contract"
    project_interface_contract = ChiefEngineerProjectInterfaceContractV1(
        contract_id=interface_id,
        contract_ref=interface_ref,
        contract_hash=interface_hash,
        task_file_ownership=task_file_ownership,
        file_task_ownership=file_task_ownership,
        provider_declarations=parsed.provider_declarations,
        consumer_declarations=parsed.consumer_declarations,
    )

    provisional_hash = "pending"
    provisional = ChiefEngineerBlueprintPortfolioV1(
        portfolio_id=portfolio_id,
        workspace=command.workspace,
        run_id=command.run_id,
        portfolio_path=portfolio_path,
        portfolio_hash=provisional_hash,
        task_ids=tuple(task.task_id for task in command.tasks),
        task_overlays=_bind_portfolio_task_overlays(
            task_overlays,
            portfolio_id=portfolio_id,
            portfolio_path=portfolio_path,
            portfolio_hash=provisional_hash,
            project_interface_contract_ref=interface_ref,
            project_interface_contract_hash=interface_hash,
            llm_blueprint_consumed=parsed.consumed,
            usage_mode=usage_mode,
        ),
        scope_advisory=scope_advisory,
        project_interface_contract=project_interface_contract,
        project_interface_contract_ref=interface_ref,
        project_interface_contract_hash=interface_hash,
        risk_flags=portfolio_risks,
        llm_blueprint_consumed=parsed.consumed,
        usage_mode=usage_mode,
    )
    portfolio_hash = _portfolio_hash(provisional.to_dict())
    portfolio = ChiefEngineerBlueprintPortfolioV1(
        portfolio_id=portfolio_id,
        workspace=command.workspace,
        run_id=command.run_id,
        portfolio_path=portfolio_path,
        portfolio_hash=portfolio_hash,
        task_ids=tuple(task.task_id for task in command.tasks),
        task_overlays=_bind_portfolio_task_overlays(
            task_overlays,
            portfolio_id=portfolio_id,
            portfolio_path=portfolio_path,
            portfolio_hash=portfolio_hash,
            project_interface_contract_ref=interface_ref,
            project_interface_contract_hash=interface_hash,
            llm_blueprint_consumed=parsed.consumed,
            usage_mode=usage_mode,
        ),
        scope_advisory=scope_advisory,
        project_interface_contract=project_interface_contract,
        project_interface_contract_ref=interface_ref,
        project_interface_contract_hash=interface_hash,
        risk_flags=portfolio_risks,
        llm_blueprint_consumed=parsed.consumed,
        usage_mode=usage_mode,
    )
    if _portfolio_hash(portfolio.to_dict()) != portfolio.portfolio_hash:
        raise _portfolio_contract_error(
            "blueprint portfolio hash did not stabilize",
            code="blueprint_portfolio_hash_invariant_failed",
            details={"portfolio_id": portfolio.portfolio_id},
        )
    _persist_immutable_blueprint_portfolio(portfolio)
    return portfolio


def project_chief_engineer_task_blueprint(
    portfolio: ChiefEngineerBlueprintPortfolioV1,
    task_id: str,
    *,
    allow_offline_diagnostic: bool = False,
) -> dict[str, Any]:
    """Project one portfolio task into ``generate_task_blueprint`` LLM shape.

    Offline deterministic portfolios are rejected by default so callers cannot
    mistake a diagnostic PM projection for a mainline CE LLM result.
    """

    normalized_task_id = str(task_id).strip()
    if not normalized_task_id:
        raise ValueError("task_id must be a non-empty string")
    overlay = portfolio.task_overlays.get(normalized_task_id)
    if not isinstance(overlay, Mapping):
        raise _portfolio_contract_error(
            f"task {normalized_task_id!r} is not present in blueprint portfolio",
            code="blueprint_portfolio_task_not_found",
            details={"portfolio_id": portfolio.portfolio_id, "task_ids": list(portfolio.task_ids)},
        )
    if not portfolio.llm_blueprint_consumed and not allow_offline_diagnostic:
        raise _portfolio_contract_error(
            "offline diagnostic portfolio cannot be projected into the mainline CE handoff",
            code="blueprint_portfolio_offline_diagnostic_only",
            details={"portfolio_id": portfolio.portfolio_id, "task_id": normalized_task_id},
        )
    return {
        "construction_plan": _mapping(_compact_llm_blueprint_value(overlay.get("construction_plan"))),
        "scope_for_apply": list(_string_list(overlay.get("scope_for_apply"))),
        "risk_flags": list(_string_list(overlay.get("risk_flags"))),
    }


def _project_blueprint_portfolio_context(
    context: Mapping[str, Any],
    *,
    task_id: str,
    target_files: list[str],
) -> dict[str, Any]:
    canonical_fields = (
        "blueprint_portfolio_ref",
        "blueprint_portfolio_hash",
        "project_interface_contract_ref",
        "project_interface_contract_hash",
        "project_interface_contract",
    )
    present_fields = [field for field in canonical_fields if field in context]
    if not present_fields:
        return {}
    missing_fields = [field for field in canonical_fields if field not in context]
    if missing_fields:
        raise _portfolio_contract_error(
            "task blueprint portfolio context is incomplete",
            code="invalid_blueprint_portfolio_context",
            details={"task_id": task_id, "missing_fields": missing_fields},
        )

    portfolio_ref = str(context.get("blueprint_portfolio_ref") or "").strip()
    portfolio_hash = str(context.get("blueprint_portfolio_hash") or "").strip()
    interface_ref = str(context.get("project_interface_contract_ref") or "").strip()
    interface_hash = str(context.get("project_interface_contract_hash") or "").strip()
    normalized_portfolio_ref, portfolio_ref_error = _normalize_portfolio_advisory_path(portfolio_ref)
    interface_path, separator, interface_fragment = interface_ref.partition("#")
    normalized_interface_path, interface_ref_error = _normalize_portfolio_advisory_path(interface_path)
    if (
        portfolio_ref_error
        or normalized_portfolio_ref != portfolio_ref
        or not portfolio_hash
        or interface_ref_error
        or separator != "#"
        or interface_fragment != "project_interface_contract"
        or f"{normalized_interface_path}#project_interface_contract" != interface_ref
        or normalized_interface_path != portfolio_ref
        or not interface_hash
    ):
        raise _portfolio_contract_error(
            "task blueprint portfolio references are invalid",
            code="invalid_blueprint_portfolio_context",
            details={"task_id": task_id},
        )

    interface_contract = _portfolio_mapping(
        context.get("project_interface_contract"),
        field_name="project_interface_contract",
    )
    if interface_contract.get("project_interface_contract_ref") != interface_ref:
        raise _portfolio_contract_error(
            "project interface contract ref does not match its context binding",
            code="invalid_blueprint_portfolio_context",
            details={"task_id": task_id, "field": "project_interface_contract_ref"},
        )
    if interface_contract.get("project_interface_contract_hash") != interface_hash:
        raise _portfolio_contract_error(
            "project interface contract hash does not match its context binding",
            code="invalid_blueprint_portfolio_context",
            details={"task_id": task_id, "field": "project_interface_contract_hash"},
        )

    interface_hash_payload = {
        key: item
        for key, item in interface_contract.items()
        if key
        not in {
            "project_interface_contract_id",
            "project_interface_contract_ref",
            "project_interface_contract_hash",
        }
    }
    if stable_hash(interface_hash_payload) != interface_hash:
        raise _portfolio_contract_error(
            "project interface contract content hash is invalid",
            code="invalid_blueprint_portfolio_context",
            details={"task_id": task_id, "field": "project_interface_contract_hash"},
        )

    for declarations_field in ("provider_declarations", "consumer_declarations"):
        if not isinstance(interface_contract.get(declarations_field), list):
            raise _portfolio_contract_error(
                f"project interface contract must explicitly define {declarations_field}",
                code="invalid_blueprint_portfolio_context",
                details={"task_id": task_id, "field": declarations_field},
            )

    task_file_ownership = interface_contract.get("task_file_ownership")
    if not isinstance(task_file_ownership, Mapping):
        raise _portfolio_contract_error(
            "project interface contract task_file_ownership is invalid",
            code="invalid_blueprint_portfolio_context",
            details={"task_id": task_id, "field": "task_file_ownership"},
        )
    owned_files = task_file_ownership.get(task_id)
    if not isinstance(owned_files, list) or tuple(_string_list(owned_files)) != tuple(target_files):
        raise _portfolio_contract_error(
            "project interface ownership does not match the PM task target files",
            code="blueprint_portfolio_pm_authority_mismatch",
            details={
                "task_id": task_id,
                "pm_target_files": list(target_files),
                "interface_target_files": _string_list(owned_files),
            },
        )

    return {
        "blueprint_portfolio_ref": portfolio_ref,
        "blueprint_portfolio_hash": portfolio_hash,
        "project_interface_contract_ref": interface_ref,
        "project_interface_contract_hash": interface_hash,
        "project_interface_contract": interface_contract,
    }


def generate_task_blueprint(command: GenerateTaskBlueprintCommandV1) -> TaskBlueprintResultV1:
    """Generate and persist a task-level Chief Engineer blueprint."""

    now = _utc_now()
    blueprint_id = f"ce_{_safe_token(command.task_id)}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
    context = dict(command.context)
    constraints = dict(command.constraints)
    contract_fields = _blueprint_contract_fields(context)
    target_files = _target_files_from_context(context)
    blueprint_portfolio_projection = _project_blueprint_portfolio_context(
        context,
        task_id=command.task_id,
        target_files=target_files,
    )
    title = str(context.get("task_title") or context.get("title") or command.objective).strip()
    summary = f"Chief Engineer blueprint for {command.task_id}: {command.objective}"
    acceptance_criteria = list(contract_fields["acceptance_criteria"])
    execution_checklist = list(contract_fields["execution_checklist"])
    scope_paths = list(contract_fields["scope_paths"])
    dependencies = list(contract_fields["dependencies"])
    delivery_plan_document = dict(contract_fields["delivery_plan_document"])
    delivery_depth_contract = dict(contract_fields["delivery_depth_contract"])
    llm_blueprint_overlay = _normalize_llm_blueprint_overlay(command.llm_blueprint)
    llm_declared_target_files = _blueprint_declared_file_paths(_mapping(command.llm_blueprint).get("construction_plan"))
    if llm_declared_target_files:
        llm_blueprint_overlay["projected_target_files"] = llm_declared_target_files[:32]
        llm_blueprint_overlay["projected_target_file_authority"] = "advisory_only_not_scope_authority"
        unpromoted = [path for path in llm_declared_target_files if path not in target_files]
        if unpromoted:
            llm_blueprint_overlay["advisory_target_files_not_promoted"] = unpromoted[:32]
    _apply_delivery_depth_test_targets(
        target_files=target_files,
        scope_paths=scope_paths,
        acceptance_criteria=acceptance_criteria,
        execution_checklist=execution_checklist,
        delivery_depth_contract=delivery_depth_contract,
        context=context,
    )
    inferred_decisions = infer_architecture_decisions(
        objective=command.objective,
        context=context,
        constraints=constraints,
        target_files=target_files,
        scope_paths=scope_paths,
        dependencies=dependencies,
    )
    architecture_decisions = merge_architecture_decisions(
        tuple(contract_fields["architecture_decisions"]),
        inferred_decisions,
    )
    architecture_decision_payloads = [decision.to_dict() for decision in architecture_decisions]
    selected_libraries = list(selected_libraries_from_decisions(architecture_decisions))
    workspace_existing_target_files = (
        _workspace_existing_target_file_summaries(command.workspace)
        if _needs_workspace_interface_snapshot(target_files)
        else []
    )
    merged_existing_target_files = _merge_existing_target_file_summaries(
        context.get("existing_target_files"),
        workspace_existing_target_files,
    )
    if merged_existing_target_files:
        context["existing_target_files"] = merged_existing_target_files
    module_interface_contract = _module_interface_contract(
        target_files=target_files,
        delivery_depth_contract=delivery_depth_contract,
        delivery_plan_document=delivery_plan_document,
        context=context,
    )
    contract_completeness = _contract_completeness(
        objective=command.objective,
        title=title,
        target_files=target_files,
        scope_paths=scope_paths,
        acceptance_criteria=acceptance_criteria,
        execution_checklist=execution_checklist,
        delivery_depth_contract=delivery_depth_contract,
        delivery_plan_document=delivery_plan_document,
        llm_blueprint_overlay=llm_blueprint_overlay,
    )
    interface_conflicts = list(module_interface_contract.get("interface_conflicts") or [])
    if interface_conflicts:
        blocker = "module_interface_contract owner conflict: " + "; ".join(
            f"{item.get('planned_path')} conflicts with actual owner {item.get('actual_owner_path')}"
            for item in interface_conflicts[:4]
            if isinstance(item, dict)
        )
        semantic_blockers = list(contract_completeness.get("semantic_blockers") or [])
        if blocker not in semantic_blockers:
            semantic_blockers.append(blocker)
        contract_completeness["semantic_blockers"] = semantic_blockers
        contract_completeness["handoff_ready"] = False
        semantic_alignment = contract_completeness.get("semantic_alignment")
        if isinstance(semantic_alignment, dict):
            semantic_alignment["ready"] = False
            blockers = list(semantic_alignment.get("blockers") or [])
            if blocker not in blockers:
                blockers.append(blocker)
            semantic_alignment["blockers"] = blockers
    context["acceptance_criteria"] = acceptance_criteria
    context["execution_checklist"] = execution_checklist
    context["target_files"] = target_files
    context["scope_paths"] = scope_paths
    context["dependencies"] = dependencies
    context["architecture_decisions"] = architecture_decision_payloads
    context["selected_libraries"] = selected_libraries
    if module_interface_contract:
        context.setdefault("module_interface_contract", module_interface_contract)
    if delivery_plan_document:
        context.setdefault("delivery_plan_document", delivery_plan_document)
    if delivery_depth_contract:
        context.setdefault("delivery_depth_contract", delivery_depth_contract)
    if llm_blueprint_overlay:
        context.setdefault("llm_blueprint_overlay", llm_blueprint_overlay)
    recommendations = (
        "Validate PM acceptance criteria before Director execution.",
        "Keep implementation scope within the recorded target files.",
        "Verify delivery_depth_contract behavior rules and edge cases before marking the task complete.",
    )
    risks = tuple(_merge_string_lists(contract_fields["risks"], llm_blueprint_overlay.get("risk_flags")))
    if "pm_task_contract" in context:
        pm_contract_hash = _blueprint_hash(dict(contract_fields["task"]))
        context["pm_contract_hash"] = pm_contract_hash
        context["contract_hash"] = pm_contract_hash
    else:
        pm_contract_hash = str(context.get("pm_contract_hash") or context.get("contract_hash") or "").strip()
        if not pm_contract_hash:
            pm_contract_hash = _blueprint_hash(dict(contract_fields["task"]))
    profile_metadata = {
        **context,
        "contract_hash": pm_contract_hash,
        "pm_contract_hash": pm_contract_hash,
        "target_files": target_files,
        "scope_paths": scope_paths,
        "acceptance_criteria": acceptance_criteria,
        "execution_checklist": execution_checklist,
    }
    profile_snapshot = build_director_execution_profile_snapshot(
        subject=title,
        description=command.objective,
        metadata=profile_metadata,
        target_files=target_files,
        scope_paths=scope_paths,
        workspace=command.workspace,
    )
    director_execution_profile = dict(profile_snapshot["profile"])
    execution_profile_hash = str(profile_snapshot["profile_hash"])
    execution_profile_ref = str(profile_snapshot["profile_ref"])
    payload: dict[str, Any] = {
        "schema_version": "chief_engineer.blueprint.v1",
        "role": "ChiefEngineer",
        "blueprint_id": blueprint_id,
        "task_id": command.task_id,
        "run_id": command.run_id,
        "title": title,
        "objective": command.objective,
        "summary": summary,
        "status": "generated",
        "source": "chief_engineer.generate_task_blueprint",
        "target_files": target_files,
        "scope_paths": scope_paths,
        "acceptance_criteria": acceptance_criteria,
        "execution_checklist": execution_checklist,
        "dependencies": dependencies,
        "architecture_decisions": architecture_decision_payloads,
        "selected_libraries": selected_libraries,
        "existing_target_files": merged_existing_target_files,
        "module_interface_contract": module_interface_contract,
        "delivery_plan_document": delivery_plan_document,
        "delivery_depth_contract": delivery_depth_contract,
        "behavior_contract": _mapping(delivery_depth_contract.get("behavior_contract")),
        "constraints": constraints,
        "context": context,
        "pm_task": contract_fields["task"],
        "pm_contract_hash": pm_contract_hash,
        "contract_hash": pm_contract_hash,
        **blueprint_portfolio_projection,
        "director_execution_profile": director_execution_profile,
        "task_execution_profile": director_execution_profile,
        "execution_profile_ref": execution_profile_ref,
        "execution_profile_hash": execution_profile_hash,
        "director_execution_profile_hash": execution_profile_hash,
        "task_execution_profile_hash": execution_profile_hash,
        "llm_blueprint": llm_blueprint_overlay,
        "ce_handoff": {
            "schema_version": "chief_engineer.handoff_context.v1",
            "llm_blueprint_consumed": bool(llm_blueprint_overlay),
            "llm_blueprint_authority": "advisory_only",
            "contract_authority": "pm_task_contract",
            "scope_authority": "runtime_target_files_or_declared_scopes",
        },
        "contract_completeness": contract_completeness,
        "handoff_ready": bool(contract_completeness["handoff_ready"]),
        "recommendations": list(recommendations),
        "risks": list(risks),
        "created_at": now,
        "updated_at": now,
    }

    # Governance determines whether the blueprint may be handed off. Compute it
    # in memory so an allowed blueprint cannot reach disk before its authoritative
    # target-file ownership facts have been durably recorded and verified.
    attach_governance_to_blueprint(command.workspace, blueprint_id, payload, persist=False)
    if bool(payload.get("handoff_ready")):
        record_task_file_owners(
            command.workspace,
            str(context.get("cache_root") or ""),
            target_files,
            task_id=command.task_id,
        )
    blueprint_hash = _blueprint_hash(payload)
    payload["blueprint_hash"] = blueprint_hash
    BlueprintPersistence(command.workspace).save(blueprint_id, payload)

    return TaskBlueprintResultV1(
        ok=True,
        task_id=command.task_id,
        workspace=command.workspace,
        status="generated",
        blueprint_id=blueprint_id,
        blueprint_path=_blueprint_path(blueprint_id),
        blueprint_hash=blueprint_hash,
        summary=summary,
        recommendations=recommendations,
        risks=risks,
        target_files=tuple(target_files),
        acceptance_criteria=tuple(acceptance_criteria),
        execution_checklist=tuple(execution_checklist),
        scope_paths=tuple(scope_paths),
        objective=command.objective,
        dependencies=tuple(dependencies),
        architecture_decisions=architecture_decisions,
        selected_libraries=tuple(selected_libraries),
        existing_target_files=tuple(dict(item) for item in merged_existing_target_files if isinstance(item, dict)),
        module_interface_contract=module_interface_contract,
    )


def get_blueprint_status(query: GetBlueprintStatusQueryV1) -> TaskBlueprintResultV1:
    """Return the latest persisted Chief Engineer blueprint status for a task."""

    persistence = BlueprintPersistence(query.workspace, ensure_directory=False)
    match = _latest_blueprint_for_task(
        persistence,
        task_id=query.task_id,
        run_id=query.run_id,
    )
    if match is None:
        return TaskBlueprintResultV1(
            ok=False,
            task_id=query.task_id,
            workspace=query.workspace,
            status="missing",
            summary="No Chief Engineer blueprint has been generated for this task.",
        )

    blueprint_id, payload = match
    status = str(payload.get("status") or "generated").strip() or "generated"
    blueprint_hash = str(payload.get("blueprint_hash") or "").strip() or _blueprint_hash(payload)
    return TaskBlueprintResultV1(
        ok=True,
        task_id=query.task_id,
        workspace=query.workspace,
        status=status,
        blueprint_id=blueprint_id,
        blueprint_path=_blueprint_path(blueprint_id),
        blueprint_hash=blueprint_hash,
        summary=str(payload.get("summary") or "").strip(),
        recommendations=_tuple_from_payload(payload.get("recommendations")),
        risks=_tuple_from_payload(payload.get("risks")),
        # D-05: Rich blueprint fields for Director context injection
        target_files=_tuple_from_payload(payload.get("target_files")),
        acceptance_criteria=_tuple_from_payload(payload.get("acceptance_criteria")),
        execution_checklist=_tuple_from_payload(payload.get("execution_checklist")),
        scope_paths=_tuple_from_payload(payload.get("scope_paths")),
        objective=str(payload.get("objective") or "").strip(),
        dependencies=_tuple_from_payload(payload.get("dependencies")),
        architecture_decisions=normalize_architecture_decisions(payload.get("architecture_decisions")),
        selected_libraries=_tuple_from_payload(payload.get("selected_libraries")),
        existing_target_files=_existing_target_files_from_payload(payload),
        module_interface_contract=_mapping(payload.get("module_interface_contract")),
    )


# ═══════════════════════════════════════════════════════════════════════
# Tier-1 governance surface (Risk Register, Tech-Debt Ledger, Quality
# Gate, Rollback Link, Governance Summary). All functions are additive;
# they do not change existing service signatures.
# ═══════════════════════════════════════════════════════════════════════


def register_risk(command: RegisterRiskCommandV1) -> RiskRecordV1:
    """Register a new entry in the workspace Risk Register."""

    register = RiskRegister(command.workspace)
    record = register.register(command)
    event = build_risk_event(
        risk_id=record.risk_id,
        workspace=command.workspace,
        action="registered",
        actor=command.owner,
    )
    logger.info(
        "chief_engineer.risk_registered risk_id=%s task_id=%s severity=%s event_id=%s",
        record.risk_id,
        record.task_id,
        record.severity.value,
        event.event_id,
    )
    return record


def list_risks(query: ListRisksQueryV1) -> list[RiskRecordV1]:
    """List Risk Register entries for the workspace with optional filters."""

    return RiskRegister(query.workspace).list(
        task_id=query.task_id,
        severity=query.severity,
        status=query.status,
    )


def update_risk_status(
    command: UpdateRiskStatusCommandV1,
    *,
    actor: str = "system",
) -> RiskRecordV1:
    """Transition a risk to a new status; append a history entry."""

    record = RiskRegister(command.workspace).update_status(command, actor)
    event = build_risk_event(
        risk_id=record.risk_id,
        workspace=command.workspace,
        action=f"status:{record.status.value}",
        actor=actor,
        note=command.note,
    )
    logger.info(
        "chief_engineer.risk_status_changed risk_id=%s status=%s event_id=%s",
        record.risk_id,
        record.status.value,
        event.event_id,
    )
    return record


def register_tech_debt(command: RegisterTechDebtCommandV1) -> TechDebtRecordV1:
    """Register a new entry in the workspace Tech-Debt Ledger."""

    ledger = TechDebtLedger(command.workspace)
    record = ledger.register(command)
    event = build_tech_debt_event(
        debt_id=record.debt_id,
        workspace=command.workspace,
        action="registered",
        actor=command.owner,
    )
    logger.info(
        "chief_engineer.tech_debt_registered debt_id=%s surface=%s severity=%s event_id=%s",
        record.debt_id,
        record.surface,
        record.severity.value,
        event.event_id,
    )
    return record


def list_tech_debt(query: ListTechDebtQueryV1) -> list[TechDebtRecordV1]:
    """List Tech-Debt Ledger entries for the workspace with optional filters."""

    return TechDebtLedger(query.workspace).list_for_query(query)


def update_tech_debt_status(
    command: UpdateTechDebtStatusCommandV1,
    *,
    actor: str = "system",
) -> TechDebtRecordV1:
    """Transition a tech-debt entry to a new status; append a history entry."""

    record = TechDebtLedger(command.workspace).update_status(command, actor)
    event = build_tech_debt_event(
        debt_id=record.debt_id,
        workspace=command.workspace,
        action=f"status:{record.status.value}",
        actor=actor,
        note=command.note,
    )
    logger.info(
        "chief_engineer.tech_debt_status_changed debt_id=%s status=%s event_id=%s",
        record.debt_id,
        record.status.value,
        event.event_id,
    )
    return record


def register_adr(command: RegisterADRCommandV1) -> ADRRecordV1:
    """Record a new Architecture Decision Record in the workspace."""

    record = ADRDecisionLog(command.workspace).register(command)
    event = build_adr_event(
        adr_id=record.adr_id,
        workspace=command.workspace,
        action="proposed",
        actor=command.owner,
    )
    logger.info(
        "chief_engineer.adr_registered adr_id=%s title=%s event_id=%s",
        record.adr_id,
        record.title,
        event.event_id,
    )
    return record


def list_adrs(query: ListADRsQueryV1) -> list[ADRRecordV1]:
    """List Architecture Decision Records for the workspace with optional filters."""

    return ADRDecisionLog(query.workspace, ensure_directory=False).list(
        status=query.status,
        task_id=query.task_id,
    )


def update_adr_status(
    command: UpdateADRStatusCommandV1,
    *,
    actor: str = "chief_engineer",
) -> ADRRecordV1:
    """Transition an ADR to a new status; append a history entry."""

    record = ADRDecisionLog(command.workspace).update_status(command, actor)
    event = build_adr_event(
        adr_id=record.adr_id,
        workspace=command.workspace,
        action=f"status:{record.status.value}",
        actor=actor,
        note=command.note,
    )
    logger.info(
        "chief_engineer.adr_status_changed adr_id=%s status=%s event_id=%s",
        record.adr_id,
        record.status.value,
        event.event_id,
    )
    return record


def summarize_adrs(workspace: str) -> dict[str, Any]:
    """Return aggregate counts for the workspace Architecture Decision Log."""

    return ADRDecisionLog(workspace, ensure_directory=False).summarize()


def summarize_risks(workspace: str, *, task_id: str | None = None) -> dict[str, Any]:
    """Return aggregate counts for the workspace Risk Register."""

    return RiskRegister(workspace, ensure_directory=False).summarize(task_id=task_id)


def summarize_tech_debt(workspace: str, *, surface: str | None = None) -> dict[str, Any]:
    """Return aggregate counts for the workspace Tech-Debt Ledger."""

    return TechDebtLedger(workspace, ensure_directory=False).summarize(surface=surface)


def register_tech_radar(command: RegisterTechRadarCommandV1) -> TechRadarEntryV1:
    """Place a library on a Tech-Radar ring for the workspace."""

    record = TechRadarLedger(command.workspace).register(command)
    event = build_tech_radar_event(
        entry_id=record.entry_id,
        workspace=command.workspace,
        action=f"ring:{record.ring.value}",
        actor=command.owner,
    )
    logger.info(
        "chief_engineer.tech_radar_registered entry_id=%s library=%s ring=%s event_id=%s",
        record.entry_id,
        record.library,
        record.ring.value,
        event.event_id,
    )
    return record


def list_tech_radar(query: ListTechRadarQueryV1) -> list[TechRadarEntryV1]:
    """List Tech-Radar entries for the workspace with an optional ring filter."""

    return TechRadarLedger(query.workspace, ensure_directory=False).list(ring=query.ring)


def update_tech_radar_ring(
    command: UpdateTechRadarRingCommandV1,
    *,
    actor: str = "chief_engineer",
) -> TechRadarEntryV1:
    """Move a Tech-Radar entry to a new ring; append a history entry."""

    record = TechRadarLedger(command.workspace).update_ring(command, actor)
    event = build_tech_radar_event(
        entry_id=record.entry_id,
        workspace=command.workspace,
        action=f"ring:{record.ring.value}",
        actor=actor,
        note=command.note,
    )
    logger.info(
        "chief_engineer.tech_radar_ring_changed entry_id=%s ring=%s event_id=%s",
        record.entry_id,
        record.ring.value,
        event.event_id,
    )
    return record


def summarize_tech_radar(workspace: str) -> dict[str, Any]:
    """Return aggregate counts for the workspace Tech Radar."""

    return TechRadarLedger(workspace, ensure_directory=False).summarize()


def check_stack_policy(workspace: str, libraries: list[str]) -> list[StackPolicyViolationV1]:
    """Return a stack-policy violation for each library on a hold/deprecated ring."""

    return TechRadarLedger(workspace, ensure_directory=False).check_stack_policy(libraries)


def register_post_mortem(command: RegisterPostMortemCommandV1) -> PostMortemRecordV1:
    """Record a new post-mortem / incident review for the workspace."""

    record = PostMortemLog(command.workspace).register(command)
    event = build_post_mortem_event(
        incident_id=record.incident_id,
        workspace=command.workspace,
        action="recorded",
        actor=command.owner,
    )
    logger.info(
        "chief_engineer.post_mortem_recorded incident_id=%s severity=%s event_id=%s",
        record.incident_id,
        record.severity.value,
        event.event_id,
    )
    return record


def list_post_mortems(query: ListPostMortemsQueryV1) -> list[PostMortemRecordV1]:
    """List post-mortems for the workspace with optional filters."""

    return PostMortemLog(query.workspace, ensure_directory=False).list_for_query(query)


def update_post_mortem_status(
    command: UpdatePostMortemStatusCommandV1,
    *,
    actor: str = "chief_engineer",
) -> PostMortemRecordV1:
    """Transition a post-mortem to a new status; append a history entry."""

    record = PostMortemLog(command.workspace).update_status(command, actor)
    event = build_post_mortem_event(
        incident_id=record.incident_id,
        workspace=command.workspace,
        action=f"status:{record.status.value}",
        actor=actor,
        note=command.note,
    )
    logger.info(
        "chief_engineer.post_mortem_status_changed incident_id=%s status=%s event_id=%s",
        record.incident_id,
        record.status.value,
        event.event_id,
    )
    return record


def summarize_post_mortems(workspace: str) -> dict[str, Any]:
    """Return aggregate counts for the workspace post-mortem log."""

    return PostMortemLog(workspace, ensure_directory=False).summarize()


def assess_release_readiness(
    workspace: str,
    *,
    blueprint_ids: list[str] | None = None,
    libraries: list[str] | None = None,
) -> ReleaseReadinessV1:
    """Synthesize an executive release GO / NO-GO from the governance surface.

    The Tier-2 capstone: aggregates open blocker/critical risks, per-blueprint
    quality-gate blockers, open sev1/sev2 incidents, stack-policy violations,
    and unpaid fatal/severe tech debt into one decision. Read-time and
    fail-closed (a blocking signal => ``no_go``).
    """
    decision = build_release_readiness(
        workspace,
        blueprint_ids=blueprint_ids,
        libraries=libraries,
    )
    logger.info(
        "chief_engineer.release_readiness_assessed workspace=%s decision=%s blockers=%d warnings=%d",
        workspace,
        decision.decision.value,
        decision.blocker_count,
        decision.warning_count,
    )
    return decision


def get_blueprint_governance(workspace: str, blueprint_id: str) -> GovernanceSummaryV1 | None:
    """Read the governance summary for a persisted blueprint.

    This is the Tier-1 consumption API for the PM / Director / QA loop:
    given a blueprint id, return its freshly-evaluated governance summary
    (risk + tech-debt summary, quality gate, rollback link). The summary
    is recomputed deterministically from the on-disk payload and the
    current Risk Register / Tech-Debt Ledger, so a caller always sees the
    latest gate verdict (e.g. after a blocker risk was resolved) without
    re-running blueprint generation.

    Args:
        workspace: Root workspace path.
        blueprint_id: Persisted blueprint id.

    Returns:
        A :class:`GovernanceSummaryV1`, or ``None`` when the blueprint is
        not found / unreadable (fail-closed: callers must treat ``None``
        as "not handoff-ready").
    """
    payload = BlueprintPersistence(workspace, ensure_directory=False).load(blueprint_id)
    if not isinstance(payload, dict):
        return None
    return build_blueprint_governance(workspace, blueprint_id, payload)


def evaluate_handoff_decision(
    workspace: str,
    *,
    blueprint: dict[str, Any],
    blueprint_id: str = "",
    task_id: str = "",
) -> HandoffDecisionV1:
    """Decide whether a blueprint may be handed to the Director.

    The enforcement primitive that closes the quality-gate loop. A handoff
    is blocked when the deterministic quality gate has blockers OR when the
    workspace Risk Register has open critical/blocker risks for the task.

    Args:
        workspace: Root workspace path.
        blueprint: Blueprint payload (must carry the construction contract
            fields target_files / acceptance_criteria / ...).
        blueprint_id: Owning blueprint id (falls back to ``blueprint``).
        task_id: Owning PM task id (falls back to ``blueprint``).

    Returns:
        A :class:`HandoffDecisionV1`. Fail-closed: a malformed blueprint
        evaluates to ``allowed=False``.
    """
    return build_handoff_decision(
        workspace,
        blueprint=blueprint,
        blueprint_id=blueprint_id,
        task_id=task_id,
    )


def evaluate_handoff_decision_for_blueprint(workspace: str, blueprint_id: str) -> HandoffDecisionV1 | None:
    """Load a persisted blueprint and decide whether it may be handed off.

    Returns ``None`` (fail-closed: caller treats as "not ready") when the
    blueprint is missing or unreadable.
    """
    payload = BlueprintPersistence(workspace, ensure_directory=False).load(blueprint_id)
    if not isinstance(payload, dict):
        return None
    return evaluate_handoff_decision(workspace, blueprint=payload, blueprint_id=blueprint_id)


_CE_HANDOFF_POLICY_VERSION = "chief_engineer.handoff.v1"
_MISSING_HASH_PREFIX = "missing:"


def _binding_hash_or_missing(payload: dict[str, Any], *keys: str, fallback: Any = None) -> str:
    for key in keys:
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    if fallback:
        return stable_hash(fallback)
    return f"{_MISSING_HASH_PREFIX}{keys[0]}"


def _execution_profile_hash_from_blueprint(blueprint: dict[str, Any]) -> str:
    explicit = str(
        blueprint.get("execution_profile_hash")
        or blueprint.get("task_execution_profile_hash")
        or blueprint.get("director_execution_profile_hash")
        or ""
    ).strip()
    if explicit:
        return explicit
    for key in ("execution_profile", "task_execution_profile", "director_execution_profile"):
        candidate = blueprint.get(key)
        if isinstance(candidate, dict) and candidate:
            return stable_hash(candidate)
    return "missing:execution_profile_hash"


def build_ce_handoff_decision(
    workspace: str,
    *,
    blueprint: dict[str, Any],
    blueprint_id: str = "",
    task_id: str = "",
    base_decision: HandoffDecisionV1 | None = None,
) -> CeHandoffDecisionV1:
    """Build the strict `ce_handoff_decision.v1` object.

    This complements the base `HandoffDecisionV1` without changing existing
    callers. The strict decision fails closed when required hash bindings are
    missing, making it suitable for the future execution envelope.
    """

    base_handoff_decision = base_decision or evaluate_handoff_decision(
        workspace,
        blueprint=blueprint,
        blueprint_id=blueprint_id,
        task_id=task_id,
    )
    resolved_blueprint_id = str(
        blueprint_id or base_handoff_decision.blueprint_id or blueprint.get("blueprint_id") or ""
    ).strip()
    resolved_task_id = str(task_id or base_handoff_decision.task_id or blueprint.get("task_id") or "").strip()
    bindings = CeHandoffDecisionBindingsV1(
        pm_contract_ref=str(blueprint.get("pm_contract_ref") or blueprint.get("pm_contract_path") or "").strip(),
        pm_contract_hash=_binding_hash_or_missing(
            blueprint,
            "pm_contract_hash",
            "contract_hash",
            fallback=blueprint.get("pm_contract"),
        ),
        blueprint_ref=str(blueprint.get("blueprint_ref") or _blueprint_path(resolved_blueprint_id)).strip(),
        blueprint_hash=_binding_hash_or_missing(
            blueprint,
            "blueprint_hash",
            fallback=blueprint,
        ),
        execution_profile_ref=str(
            blueprint.get("execution_profile_ref")
            or blueprint.get("task_execution_profile_ref")
            or blueprint.get("director_execution_profile_ref")
            or ""
        ).strip(),
        execution_profile_hash=_execution_profile_hash_from_blueprint(blueprint),
    )
    binding_values = bindings.to_dict()
    missing_bindings = [
        key
        for key in ("pm_contract_hash", "blueprint_hash", "execution_profile_hash")
        if str(binding_values.get(key) or "").startswith(_MISSING_HASH_PREFIX)
    ]
    blockers = [*base_handoff_decision.blockers]
    blockers.extend(f"missing required handoff binding: {key}" for key in missing_bindings)
    allowed = bool(base_handoff_decision.allowed and not missing_bindings)
    risk_assessment: dict[str, Any] = {
        "blocking_risks": (
            list(base_handoff_decision.blockers) if base_handoff_decision.open_blocker_risk_count else []
        ),
        "non_blocking_warnings": [],
    }
    evidence_refs = [
        str(ref)
        for ref in (binding_values.get("pm_contract_ref"), binding_values.get("blueprint_ref"))
        if str(ref or "").strip()
    ]
    payload_without_hash: dict[str, Any] = {
        "schema_version": "polaris.ce_handoff_decision.v1",
        "task_id": resolved_task_id,
        "blueprint_id": resolved_blueprint_id,
        "allowed": allowed,
        "reason": base_handoff_decision.reason,
        "blockers": blockers,
        "warnings": [],
        "risk_assessment": risk_assessment,
        "evaluated_at": base_handoff_decision.evaluated_at or _utc_now(),
        "evaluator": "chief_engineer.blueprint.handoff",
        "policy_version": _CE_HANDOFF_POLICY_VERSION,
        "bindings": binding_values,
        "evidence_refs": evidence_refs,
    }
    decision_hash = stable_hash(payload_without_hash)
    return CeHandoffDecisionV1(
        decision_id=f"ce-handoff-{decision_hash[:24]}",
        task_id=resolved_task_id,
        blueprint_id=resolved_blueprint_id,
        allowed=allowed,
        reason=str(base_handoff_decision.reason or ""),
        blockers=tuple(blockers),
        warnings=(),
        risk_assessment=risk_assessment,
        evaluated_at=str(payload_without_hash["evaluated_at"]),
        evaluator=str(payload_without_hash["evaluator"]),
        policy_version=_CE_HANDOFF_POLICY_VERSION,
        bindings=bindings,
        evidence_refs=tuple(evidence_refs),
        decision_hash=decision_hash,
    )


def evaluate_ce_handoff_decision_for_blueprint(
    workspace: str,
    blueprint_id: str,
) -> CeHandoffDecisionV1 | None:
    """Load a persisted blueprint and build strict `ce_handoff_decision.v1`."""

    payload = BlueprintPersistence(workspace, ensure_directory=False).load(blueprint_id)
    if not isinstance(payload, dict):
        return None
    return build_ce_handoff_decision(workspace, blueprint=payload, blueprint_id=blueprint_id)


def _merged_payload_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    metadata_raw = payload.get("metadata")
    metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
    merged = dict(payload)
    merged.update(metadata)
    return merged


def _blueprint_id_from_payload(payload: dict[str, Any]) -> str:
    merged = _merged_payload_metadata(payload)
    for key in ("blueprint_id", "chief_engineer_blueprint_id", "chief_engineer_handoff_id"):
        token = str(merged.get(key) or "").strip()
        if token:
            return Path(token).stem if token.endswith(".json") else token
    for key in ("blueprint_path", "runtime_blueprint_path"):
        token = str(merged.get(key) or "").strip()
        if token:
            return Path(token).stem
    return ""


def _task_id_from_payload(payload: dict[str, Any]) -> str:
    merged = _merged_payload_metadata(payload)
    for key in ("task_id", "pm_task_id", "source_task_id", "external_task_id", "id"):
        token = str(merged.get(key) or "").strip()
        if token:
            return token
    return ""


def _handoff_validation_result(
    *,
    allowed: bool,
    reason: str,
    task_id: str = "",
    blueprint_id: str = "",
    blueprint_task_id: str = "",
    base_handoff_decision: HandoffDecisionV1 | None = None,
    strict_decision: CeHandoffDecisionV1 | None = None,
    require_strict: bool = False,
) -> dict[str, Any]:
    base_payload = base_handoff_decision.to_dict() if base_handoff_decision is not None else {}
    strict_payload = strict_decision.to_dict() if strict_decision is not None else {}
    return {
        "schema_version": "chief_engineer.director_handoff_validation.v1",
        "allowed": allowed,
        "reason": str(reason or "").strip(),
        "task_id": task_id,
        "blueprint_id": blueprint_id,
        "blueprint_task_id": blueprint_task_id,
        "base_allowed": bool(base_handoff_decision.allowed) if base_handoff_decision is not None else False,
        "strict_allowed": bool(strict_decision.allowed) if strict_decision is not None else False,
        "require_strict": require_strict,
        "decision_payload": base_payload,
        "strict_decision_payload": strict_payload,
    }


def validate_director_handoff_from_payload(
    workspace: str,
    payload: dict[str, Any],
    *,
    require_strict: bool = False,
) -> dict[str, Any]:
    """Validate whether a payload may enter Director dispatch.

    This is the shared pre-Director policy seam for PM dispatch, task-market
    consumers, CLI loops, and future execution-envelope creation. The default
    remains transition-safe: base handoff authorization is authoritative,
    while the strict `ce_handoff_decision.v1` is still computed and exposed for
    audit. Callers can opt into `require_strict=True` once their dispatch path
    always carries immutable PM/CE/profile hash bindings.
    """

    workspace_token = str(workspace or "").strip()
    if not workspace_token:
        return _handoff_validation_result(
            allowed=False,
            reason="workspace is required for Chief Engineer handoff validation",
            require_strict=require_strict,
        )
    task_id = _task_id_from_payload(payload)
    blueprint_id = _blueprint_id_from_payload(payload)
    if not blueprint_id:
        return _handoff_validation_result(
            allowed=False,
            reason="missing Chief Engineer blueprint id",
            task_id=task_id,
            require_strict=require_strict,
        )

    blueprint = BlueprintPersistence(workspace_token, ensure_directory=False).load(blueprint_id)
    if not isinstance(blueprint, dict):
        return _handoff_validation_result(
            allowed=False,
            reason=f"Chief Engineer blueprint {blueprint_id} missing or unreadable",
            task_id=task_id,
            blueprint_id=blueprint_id,
            require_strict=require_strict,
        )

    blueprint_task_id = str(blueprint.get("task_id") or blueprint.get("pm_task_id") or "").strip()
    if task_id and blueprint_task_id and task_id != blueprint_task_id:
        return _handoff_validation_result(
            allowed=False,
            reason=f"Chief Engineer blueprint {blueprint_id} belongs to {blueprint_task_id}, not {task_id}",
            task_id=task_id,
            blueprint_id=blueprint_id,
            blueprint_task_id=blueprint_task_id,
            require_strict=require_strict,
        )

    base_handoff_decision = evaluate_handoff_decision(
        workspace_token,
        blueprint=blueprint,
        blueprint_id=blueprint_id,
        task_id=task_id,
    )
    strict_decision = build_ce_handoff_decision(
        workspace_token,
        blueprint=blueprint,
        blueprint_id=blueprint_id,
        task_id=task_id,
        base_decision=base_handoff_decision,
    )
    if not base_handoff_decision.allowed:
        return _handoff_validation_result(
            allowed=False,
            reason=base_handoff_decision.reason,
            task_id=task_id,
            blueprint_id=blueprint_id,
            blueprint_task_id=blueprint_task_id,
            base_handoff_decision=base_handoff_decision,
            strict_decision=strict_decision,
            require_strict=require_strict,
        )
    if require_strict and not strict_decision.allowed:
        return _handoff_validation_result(
            allowed=False,
            reason=strict_decision.reason or "strict Chief Engineer handoff decision blocked Director dispatch",
            task_id=task_id,
            blueprint_id=blueprint_id,
            blueprint_task_id=blueprint_task_id,
            base_handoff_decision=base_handoff_decision,
            strict_decision=strict_decision,
            require_strict=require_strict,
        )
    return _handoff_validation_result(
        allowed=True,
        reason=base_handoff_decision.reason,
        task_id=task_id,
        blueprint_id=blueprint_id,
        blueprint_task_id=blueprint_task_id,
        base_handoff_decision=base_handoff_decision,
        strict_decision=strict_decision,
        require_strict=require_strict,
    )


def assert_handoff_ready(
    workspace: str,
    *,
    blueprint: dict[str, Any],
    blueprint_id: str = "",
    task_id: str = "",
) -> HandoffDecisionV1:
    """Raise when a blueprint must not be handed to the Director.

    Fail-closed enforcement helper for callers that want a hard gate: on a
    blocked decision it raises :class:`ChiefEngineerBlueprintErrorV1` with
    code ``handoff_blocked`` and the decision in ``details``.
    """
    decision = evaluate_handoff_decision(
        workspace,
        blueprint=blueprint,
        blueprint_id=blueprint_id,
        task_id=task_id,
    )
    if not decision.allowed:
        raise ChiefEngineerBlueprintErrorV1(
            f"handoff blocked: {decision.reason}",
            code="handoff_blocked",
            details=decision.to_dict(),
        )
    return decision


def build_blueprint_governance(
    workspace: str,
    blueprint_id: str,
    blueprint: dict[str, Any],
) -> GovernanceSummaryV1:
    """Compute the governance summary for a blueprint payload.

    Pulls risks from the workspace register, evaluates the quality gate,
    and assembles a :class:`GovernanceSummaryV1`. The function is pure
    except for the Risk Register read; pass an explicit ``risks`` list
    on the blueprint to make it fully deterministic.
    """
    task_id = str(blueprint.get("task_id") or "").strip()
    risk_register = RiskRegister(workspace, ensure_directory=False)
    risks = risk_register.list(task_id=task_id) if task_id else risk_register.list()
    gate = evaluate_quality_gate(blueprint, risks=risks)
    rollback = build_rollback_link(
        workspace=workspace,
        blueprint_id=blueprint_id,
        blueprint=blueprint,
        risks=risks,
    )
    return GovernanceSummaryV1(
        blueprint_id=blueprint_id,
        risk_summary=risk_register.summarize(task_id=task_id),
        tech_debt_summary=TechDebtLedger(workspace, ensure_directory=False).summarize(),
        quality_gate=gate,
        rollback=rollback,
    )


def attach_governance_to_blueprint(
    workspace: str,
    blueprint_id: str,
    blueprint: dict[str, Any],
    *,
    persist: bool = True,
) -> GovernanceSummaryV1:
    """Compute governance and optionally persist it for a blueprint.

    The governance summary is computed from the current payload and the
    workspace's Risk Register / Tech-Debt Ledger, then optionally written back
    to the blueprint JSON under the ``governance`` key. ``blueprint`` is mutated
    in place: the ``governance`` field is added and ``handoff_ready`` is
    recomputed from the quality gate. ``persist=False`` supports transaction-like
    callers that must establish another durable prerequisite before a handoff-ready
    blueprint is visible. This call is idempotent and safe to invoke from blueprint
    regeneration paths.
    """
    summary = build_blueprint_governance(workspace, blueprint_id, blueprint)
    blueprint["governance"] = summary.to_dict()
    blueprint["handoff_ready"] = bool(summary.quality_gate.passed)
    if persist:
        BlueprintPersistence(workspace).save(blueprint_id, blueprint)
    return summary


# Required for governance logger; defined at module bottom to avoid a
# top-level `logging.getLogger` if any future refactor reorders imports.
import logging  # noqa: E402

logger = logging.getLogger(__name__)


# Contract types (dataclasses, enums, errors) are owned by contracts.py and
# re-exported through public/__init__.py from there — they are intentionally
# NOT listed here. service.__all__ exposes only service functions and the
# re-exported agent/consumer classes, uniformly across all ledgers.
__all__ = [
    "CEConsumer",
    "ChiefEngineerAgent",
    "assert_handoff_ready",
    "assess_release_readiness",
    "attach_governance_to_blueprint",
    "build_blueprint_governance",
    "build_ce_handoff_decision",
    "build_chief_engineer_blueprint_portfolio",
    "check_stack_policy",
    "create_rollback_guard",
    "evaluate_ce_handoff_decision_for_blueprint",
    "evaluate_handoff_decision",
    "evaluate_handoff_decision_for_blueprint",
    "generate_task_blueprint",
    "get_blueprint_governance",
    "get_blueprint_status",
    "list_adrs",
    "list_post_mortems",
    "list_risks",
    "list_tech_debt",
    "list_tech_radar",
    "project_chief_engineer_task_blueprint",
    "query_blueprint_provenance",
    "register_adr",
    "register_post_mortem",
    "register_risk",
    "register_tech_debt",
    "register_tech_radar",
    "run_pre_dispatch_chief_engineer",
    "summarize_adrs",
    "summarize_post_mortems",
    "summarize_risks",
    "summarize_tech_debt",
    "summarize_tech_radar",
    "update_adr_status",
    "update_post_mortem_status",
    "update_risk_status",
    "update_tech_debt_status",
    "update_tech_radar_ring",
    "validate_director_handoff_from_payload",
]
