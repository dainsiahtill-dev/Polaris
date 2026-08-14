"""Portfolio construction and project-completion contract helpers."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal

from polaris.cells.control_plane.run_ledger.public import stable_hash

from ...internal.blueprint_persistence import BlueprintPersistence
from ...internal.project_completion_contract import build_project_completion_contract
from ..contracts import (
    ArtifactObligationV1,
    BuildChiefEngineerBlueprintPortfolioCommandV1,
    ChiefEngineerBlueprintErrorV1,
    ChiefEngineerBlueprintPortfolioV1,
    ChiefEngineerPortfolioTaskV1,
    ChiefEngineerProjectInterfaceContractV1,
    EntrypointObligationV1,
    ProjectCompletionContractV1,
    ProjectCompletionObligationsV1,
    ProjectKindAuthorityV1,
    QueryProjectCompletionContractV1,
    VerificationObligationV1,
    _ChiefEngineerPortfolioAuthorityCarrierV1,
    _portfolio_authority_receipt_hash,
    _verify_chief_engineer_portfolio_authority_carrier,
    project_completion_catalog_snapshot_hash,
)
from ._helpers import (
    _BLUEPRINT_FILE_CONTAINER_KEYS,
    _BLUEPRINT_FILE_PATH_KEYS,
    _PROJECT_COMPLETION_PREDICATE_VERSION,
    _blueprint_path,
    _compact_llm_blueprint_value,
    _mapping,
    _portfolio_hash,
    _string_list,
)


@dataclass(frozen=True)
class _PortfolioLlmBlueprint:
    shared_plan: dict[str, Any]
    task_plans: dict[str, dict[str, Any]]
    scope_paths: tuple[str, ...]
    scope_rejections: tuple[dict[str, str], ...]
    risk_flags: tuple[str, ...]
    provider_declarations: tuple[dict[str, Any], ...]
    consumer_declarations: tuple[dict[str, Any], ...]
    project_completion_requirements: dict[str, Any] | None
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


def _strict_completion_mapping(
    value: Any,
    *,
    field_name: str,
    expected_fields: set[str],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _portfolio_contract_error(
            f"{field_name} must be a JSON object",
            code="invalid_project_completion_contract",
            details={"field": field_name, "actual_type": type(value).__name__},
        )
    payload = dict(value)
    unknown_fields = sorted(str(key) for key in payload if str(key) not in expected_fields)
    missing_fields = sorted(expected_fields - {str(key) for key in payload})
    if unknown_fields or missing_fields:
        raise _portfolio_contract_error(
            f"{field_name} fields must match the completion contract schema exactly",
            code="invalid_project_completion_contract",
            details={
                "field": field_name,
                "unknown_fields": unknown_fields,
                "missing_fields": missing_fields,
            },
        )
    return payload


def _strict_completion_rows(
    value: Any,
    *,
    field_name: str,
    expected_fields: set[str],
) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list):
        raise _portfolio_contract_error(
            f"{field_name} must be a JSON array",
            code="invalid_project_completion_contract",
            details={"field": field_name, "actual_type": type(value).__name__},
        )
    return tuple(
        _strict_completion_mapping(
            item,
            field_name=f"{field_name}[{index}]",
            expected_fields=expected_fields,
        )
        for index, item in enumerate(value)
    )


def _completion_path_is_within_scope(*, path: str, scope_path: str) -> bool:
    """Return component-safe PM scope containment, never raw string-prefix containment."""

    path_parts = PurePosixPath(path).parts
    scope_parts = PurePosixPath(scope_path).parts
    return len(path_parts) >= len(scope_parts) and path_parts[: len(scope_parts)] == scope_parts


def _pm_target_semantic_role(*, path: str, entrypoint_paths: set[str]) -> str:
    """Classify one exact PM target without widening artifact authority."""

    normalized = PurePosixPath(path)
    name = normalized.name.casefold()
    suffix = normalized.suffix.casefold()
    parts = {part.casefold() for part in normalized.parts}
    if path in entrypoint_paths:
        return "entrypoint"
    if "tests" in parts or "test" in parts or name.startswith("test_") or "_test." in name:
        return "test"
    if name in {
        "cargo.toml",
        "composer.json",
        "deno.json",
        "deno.jsonc",
        "gemfile",
        "go.mod",
        "mix.exs",
        "package.json",
        "pom.xml",
        "pyproject.toml",
        "requirements.txt",
    }:
        return "manifest"
    if name.startswith("readme") or suffix in {".md", ".rst"}:
        return "docs"
    if suffix in {".json", ".toml", ".yaml", ".yml", ".ini", ".cfg", ".conf", ".lock"}:
        return "config"
    if suffix in {
        ".avif",
        ".bmp",
        ".gif",
        ".ico",
        ".jpeg",
        ".jpg",
        ".mp3",
        ".mp4",
        ".ogg",
        ".png",
        ".svg",
        ".wav",
        ".webp",
    }:
        return "assets"
    return "source"


def _pm_entrypoint_kind(
    *,
    path: str,
    command: str,
    project_kind: str,
    catalog_snapshot: Mapping[str, Any],
) -> str:
    """Classify an exact PM entrypoint from committed project and command facts."""

    if project_kind == "library":
        return "library"
    project_type = str(catalog_snapshot.get("project_type") or "").strip().casefold()
    joined = f"{path} {command} {project_type}".casefold()
    if any(token in joined for token in ("index.html", "vite", "webpack", " web", "frontend")):
        return "web"
    if any(token in joined for token in ("uvicorn", "gunicorn", "fastapi", " api", "server")):
        return "api"
    return "cli"


def derive_project_kind_authority_from_catalog_snapshot(
    *,
    project_id: str,
    run_id: str,
    pm_contract_hash: str,
    catalog_snapshot: Mapping[str, Any],
    catalog_snapshot_hash: str,
) -> ProjectKindAuthorityV1:
    """Derive exemption-bearing project kind from one identity-bound catalog snapshot.

    The returned value is never accepted from a portfolio caller.  Both Factory and
    this owner service derive it from the same snapshot; the portfolio service also
    re-reads the workspace catalog before accepting the command.
    """

    snapshot = dict(catalog_snapshot)
    observed_snapshot_hash = project_completion_catalog_snapshot_hash(snapshot)
    if observed_snapshot_hash != catalog_snapshot_hash:
        raise ValueError("catalog_snapshot_hash does not bind catalog_snapshot")
    catalog_project_id = str(snapshot.get("project_id") or "").strip()
    if catalog_project_id and catalog_project_id != project_id:
        raise ValueError("catalog snapshot project_id does not match portfolio project_id")
    explicit_kind = str(snapshot.get("project_kind") or "").strip().casefold()
    project_type = str(snapshot.get("project_type") or "").strip().casefold()
    if explicit_kind and explicit_kind not in {"application", "library"}:
        raise ValueError("catalog project_kind must be application or library")

    library_type_tokens = {"library", "package", "sdk", "crate"}
    library_suffixes = ("_library", "_package", "_sdk", "_crate")
    if explicit_kind:
        project_kind = explicit_kind
        justification = f"catalog_explicit_project_kind:{explicit_kind}"
    elif project_type in library_type_tokens or project_type.endswith(library_suffixes):
        project_kind = "library"
        justification = f"catalog_explicit_library_project_type:{project_type}"
    else:
        project_kind = "application"
        justification = "conservative_application_without_explicit_library_authority"

    source_hash = hashlib.sha256(
        json.dumps(
            {
                "schema_version": "polaris.ce_project_kind_source.v2",
                "project_id": project_id,
                "run_id": run_id,
                "pm_contract_hash": pm_contract_hash,
                "catalog_snapshot_hash": catalog_snapshot_hash,
                "catalog_project_id": catalog_project_id,
                "catalog_project_kind": explicit_kind,
                "catalog_project_type": project_type,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return ProjectKindAuthorityV1(
        project_kind=project_kind,  # type: ignore[arg-type]
        source_ref="chief_engineer.committed_pm_catalog_snapshot",
        source_hash=source_hash,
        justification=justification,
    )


def _read_portfolio_catalog_snapshot(workspace: str) -> dict[str, Any]:
    catalog_path = Path(workspace) / ".polaris" / "catalog_contract.json"
    if not catalog_path.exists():
        return {}
    try:
        before = catalog_path.stat()
        raw = catalog_path.read_text(encoding="utf-8")
        after = catalog_path.stat()
        before_identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        if before_identity != after_identity:
            raise ValueError("workspace catalog changed while being read")
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ValueError("workspace catalog snapshot is unreadable or invalid JSON") from exc
    if type(payload) is not dict:
        raise ValueError("workspace catalog snapshot must be an exact JSON object")
    return payload


def _revalidate_portfolio_authority_carrier(
    command: BuildChiefEngineerBlueprintPortfolioCommandV1,
) -> _ChiefEngineerPortfolioAuthorityCarrierV1:
    carrier = command.authority_carrier
    if type(carrier) is not _ChiefEngineerPortfolioAuthorityCarrierV1:
        raise _portfolio_contract_error(
            "portfolio completion authority was not issued by the Factory owner",
            code="invalid_project_completion_authority_carrier",
        )
    if not _verify_chief_engineer_portfolio_authority_carrier(carrier):
        raise _portfolio_contract_error(
            "portfolio authority carrier signature is invalid",
            code="invalid_project_completion_authority_carrier",
        )
    if carrier.workspace != command.workspace or carrier.run_id != command.run_id or carrier.tasks != command.tasks:
        raise _portfolio_contract_error(
            "portfolio authority carrier identity does not match command",
            code="invalid_project_completion_authority_carrier",
        )
    expected_catalog_receipt = _portfolio_authority_receipt_hash(
        domain="catalog",
        workspace=carrier.workspace,
        run_id=carrier.run_id,
        project_id=carrier.project_id,
        pm_stage_event_id=carrier.pm_stage_event_id,
        pm_contract_hash=carrier.pm_contract_hash,
        evidence_hash=carrier.catalog_snapshot_hash,
    )
    expected_policy_receipt = _portfolio_authority_receipt_hash(
        domain="verifier_policy",
        workspace=carrier.workspace,
        run_id=carrier.run_id,
        project_id=carrier.project_id,
        pm_stage_event_id=carrier.pm_stage_event_id,
        pm_contract_hash=carrier.pm_contract_hash,
        evidence_hash=carrier.verifier_policy_snapshot_hash,
    )
    if carrier.catalog_receipt_hash != expected_catalog_receipt or (
        carrier.verifier_policy_receipt_hash != expected_policy_receipt
    ):
        raise _portfolio_contract_error(
            "portfolio authority receipt binding is invalid",
            code="invalid_project_completion_authority_carrier",
        )
    if carrier.catalog_version != f"sha256:{carrier.catalog_snapshot_hash}":
        raise _portfolio_contract_error(
            "portfolio catalog version does not match owner receipt",
            code="invalid_project_completion_authority_carrier",
        )
    live_catalog_snapshot = _read_portfolio_catalog_snapshot(command.workspace)
    if live_catalog_snapshot != dict(carrier.catalog_snapshot):
        raise _portfolio_contract_error(
            "workspace catalog drifted after committed PM authority capture",
            code="invalid_project_completion_contract",
        )
    if project_completion_catalog_snapshot_hash(live_catalog_snapshot) != carrier.catalog_snapshot_hash:
        raise _portfolio_contract_error(
            "workspace catalog hash no longer matches owner receipt",
            code="invalid_project_completion_authority_carrier",
        )
    return carrier


def _build_portfolio_completion_contract(
    command: BuildChiefEngineerBlueprintPortfolioCommandV1,
    requirements: Mapping[str, Any],
) -> ProjectCompletionContractV1:
    carrier = _revalidate_portfolio_authority_carrier(command)
    try:
        project_kind_authority = derive_project_kind_authority_from_catalog_snapshot(
            project_id=carrier.project_id,
            run_id=carrier.run_id,
            pm_contract_hash=carrier.pm_contract_hash,
            catalog_snapshot=carrier.catalog_snapshot,
            catalog_snapshot_hash=carrier.catalog_snapshot_hash,
        )
    except (OSError, TypeError, ValueError) as exc:
        raise _portfolio_contract_error(
            f"invalid committed project catalog authority: {exc}",
            code="invalid_project_completion_contract",
            details={"error_type": type(exc).__name__},
        ) from exc
    payload = _strict_completion_mapping(
        requirements,
        field_name="project_completion_contract",
        expected_fields={"obligations"},
    )
    obligations_payload = _strict_completion_mapping(
        payload["obligations"],
        field_name="project_completion_contract.obligations",
        expected_fields={"artifacts", "entrypoints", "verification"},
    )
    artifact_rows = _strict_completion_rows(
        obligations_payload["artifacts"],
        field_name="project_completion_contract.obligations.artifacts",
        expected_fields={"obligation_id", "path", "semantic_role", "applicability", "owner_task_id"},
    )
    entrypoint_rows = _strict_completion_rows(
        obligations_payload["entrypoints"],
        field_name="project_completion_contract.obligations.entrypoints",
        expected_fields={
            "obligation_id",
            "kind",
            "applicability",
            "owner_task_id",
            "source_path",
            "runtime_path",
            "command",
        },
    )
    verification_rows = _strict_completion_rows(
        obligations_payload["verification"],
        field_name="project_completion_contract.obligations.verification",
        expected_fields={
            "obligation_id",
            "modality",
            "command_authority_hash",
            "applicability",
            "covers_obligation_ids",
            "owner_task_id",
        },
    )
    try:
        command_authorities = tuple(carrier.verification_command_authority)
        command_authority_by_hash = {item.authority_hash: item for item in command_authorities}

        pm_target_owners: dict[str, set[str]] = {}
        pm_scope_owners: list[tuple[str, str]] = []
        pm_dependencies = {task.task_id: set(task.dependencies) for task in command.tasks}
        for task in command.tasks:
            for path in task.target_files:
                pm_target_owners.setdefault(path, set()).add(task.task_id)
            pm_scope_owners.extend((path, task.task_id) for path in task.scope_paths)

        def owners_for_path(path: str) -> set[str]:
            owners = set(pm_target_owners.get(path, set()))
            owners.update(
                task_id
                for scope_path, task_id in pm_scope_owners
                if _completion_path_is_within_scope(path=path, scope_path=scope_path)
            )
            return owners

        def transitively_depends_on(task_id: str, possible_ancestor: str) -> bool:
            pending = list(pm_dependencies.get(task_id, set()))
            visited: set[str] = set()
            while pending:
                dependency = pending.pop()
                if dependency == possible_ancestor:
                    return True
                if dependency in visited:
                    continue
                visited.add(dependency)
                pending.extend(pm_dependencies.get(dependency, set()))
            return False

        def unique_terminal_owner(path: str) -> str | None:
            """Return the sole final writer in a PM-authorized shared-path dependency chain."""

            authorized = owners_for_path(path)
            terminal = {
                owner
                for owner in authorized
                if not any(other != owner and transitively_depends_on(other, owner) for other in authorized)
            }
            return next(iter(terminal)) if len(terminal) == 1 else None

        entrypoint_paths = {path for task in command.tasks for path in task.entrypoint_targets}
        ordered_pm_paths = tuple(dict.fromkeys(path for task in command.tasks for path in task.target_files))

        def project_pm_target_row(path: str, *, index: int) -> dict[str, Any]:
            authorized = owners_for_path(path)
            owner_task_id = next(iter(authorized)) if len(authorized) == 1 else unique_terminal_owner(path)
            if owner_task_id is None:
                raise ValueError(
                    "PM target artifact has no unique authorized terminal owner; "
                    f"path={path!r}; owners={sorted(authorized)!r}"
                )
            return {
                "obligation_id": f"artifact-pm-{index:03d}",
                "path": path,
                "semantic_role": _pm_target_semantic_role(
                    path=path,
                    entrypoint_paths=entrypoint_paths,
                ),
                "applicability": "required",
                "owner_task_id": owner_task_id,
            }

        if not artifact_rows:
            # Artifact paths and owners are PM authority, not creative CE
            # content. Some providers repeatedly return an empty artifact list
            # even after a bounded schema-repair turn. Project every exact PM
            # target once and retain fail-closed ownership: shared paths are
            # accepted only when their dependency graph has one terminal writer.
            artifact_rows = tuple(
                project_pm_target_row(path, index=index) for index, path in enumerate(ordered_pm_paths, start=1)
            )

        # Creative CE extras must not expand delivery obligations. Drop paths
        # outside exact PM target / component-safe scope, then project every
        # missing exact PM target. Owner lies on authorized paths stay
        # fail-closed below.
        dropped_unauthorized_artifact_ids: set[str] = set()
        kept_artifact_rows: list[dict[str, Any]] = []
        for row in artifact_rows:
            if row["applicability"] == "not_applicable":
                kept_artifact_rows.append(dict(row))
                continue
            path = str(row.get("path") or "")
            if path and owners_for_path(path):
                kept_artifact_rows.append(dict(row))
                continue
            dropped_unauthorized_artifact_ids.add(str(row["obligation_id"]))
        required_artifact_paths = {
            str(row["path"]) for row in kept_artifact_rows if row["applicability"] != "not_applicable"
        }
        next_projected_index = 1
        for path in ordered_pm_paths:
            if path in required_artifact_paths:
                continue
            kept_artifact_rows.append(project_pm_target_row(path, index=next_projected_index))
            next_projected_index += 1
        artifact_rows = tuple(kept_artifact_rows)

        def normalize_owner_rows(
            rows: tuple[dict[str, Any], ...],
            *,
            path_field: str,
        ) -> tuple[dict[str, Any], ...]:
            """Repair only model owner drift that PM authority can resolve uniquely.

            Shared paths remain set-valued in PM authority.  A scalar completion
            owner is projected only when those owners form a dependency chain with
            one terminal writer.  Parallel/incomparable owners remain ambiguous and
            therefore fail closed in the validation below.
            """

            normalized: list[dict[str, Any]] = []
            for row in rows:
                normalized_row = dict(row)
                if row["applicability"] != "not_applicable":
                    path_value = row.get(path_field)
                    path = str(path_value) if path_value is not None else ""
                    authorized = owners_for_path(path) if path else set()
                    if len(authorized) > 1 and row["owner_task_id"] not in authorized:
                        terminal_owner = unique_terminal_owner(path) if path else None
                        if terminal_owner is not None:
                            normalized_row["owner_task_id"] = terminal_owner
                normalized.append(normalized_row)
            return tuple(normalized)

        artifact_rows = normalize_owner_rows(artifact_rows, path_field="path")
        entrypoint_rows = normalize_owner_rows(entrypoint_rows, path_field="source_path")

        artifact_owner_by_id = {
            str(row["obligation_id"]): str(row["owner_task_id"])
            for row in artifact_rows
            if row["applicability"] != "not_applicable" and row["owner_task_id"] is not None
        }
        entrypoint_artifact_owner_by_path = {
            str(row["path"]): str(row["owner_task_id"])
            for row in artifact_rows
            if row["applicability"] != "not_applicable"
            and row["semantic_role"] == "entrypoint"
            and row["owner_task_id"] is not None
        }
        pm_entrypoint_owners_by_path: dict[str, set[str]] = {}
        for task in command.tasks:
            for path in task.entrypoint_targets:
                pm_entrypoint_owners_by_path.setdefault(path, set()).add(task.task_id)
        explicit_pm_entrypoint_owners = {task.task_id for task in command.tasks if task.entrypoint_targets}

        def authorities_for(*, modality: str, owner_task_id: str | None = None) -> tuple[Any, ...]:
            return tuple(
                item
                for item in command_authorities
                if item.modality == modality and (owner_task_id is None or item.task_id == owner_task_id)
            )

        if not entrypoint_rows:
            # Entrypoint paths and executable commands are already exact PM
            # authority. An empty CE advisory list must not erase them.
            ordered_entrypoints = tuple(
                dict.fromkeys(path for task in command.tasks for path in task.entrypoint_targets)
            )
            projected_entrypoints: list[dict[str, Any]] = []
            for index, path in enumerate(ordered_entrypoints, start=1):
                authorized = owners_for_path(path)
                owner_task_id = next(iter(authorized)) if len(authorized) == 1 else unique_terminal_owner(path)
                if owner_task_id is None:
                    raise ValueError(
                        "PM entrypoint has no unique authorized terminal owner; "
                        f"path={path!r}; owners={sorted(authorized)!r}"
                    )
                matching = authorities_for(
                    modality="entrypoint",
                    owner_task_id=owner_task_id,
                )
                if len(matching) != 1:
                    raise ValueError(
                        "PM entrypoint owner must have exactly one committed command authority; "
                        f"path={path!r}; owner_task_id={owner_task_id!r}; matches={len(matching)}"
                    )
                command_authority = matching[0]
                projected_entrypoints.append(
                    {
                        "obligation_id": f"entrypoint-pm-{index:03d}",
                        "kind": _pm_entrypoint_kind(
                            path=path,
                            command=command_authority.command,
                            project_kind=project_kind_authority.project_kind,
                            catalog_snapshot=carrier.catalog_snapshot,
                        ),
                        "applicability": "required",
                        "owner_task_id": owner_task_id,
                        "source_path": path,
                        "runtime_path": None,
                        "command": command_authority.command,
                    }
                )
            if not projected_entrypoints and project_kind_authority.project_kind == "library":
                # Libraries deliberately have no executable entrypoint.  When
                # CE omits this advisory declaration, preserve the catalog-owned
                # project kind as an explicit not_applicable obligation instead
                # of failing on an empty list.
                projected_entrypoints.append(
                    {
                        "obligation_id": "entrypoint-library-na",
                        "kind": "library",
                        "applicability": "not_applicable",
                        "owner_task_id": None,
                        "source_path": None,
                        "runtime_path": None,
                        "command": None,
                    }
                )
            entrypoint_rows = tuple(projected_entrypoints)

        def authority_for_row(row: Mapping[str, Any]) -> Any:
            """Bind advisory verifier semantics to committed PM command authority.

            CE owns semantic coverage; it does not own opaque hashes or command
            authorization.  Prefer the exact hash, then exact owner/modality,
            then an unambiguous covered-artifact owner.  The final modality-wide
            fallback is allowed only when every PM authority exposes the same
            argv/cwd command.  No branch can invent or widen command authority.
            """

            authority = command_authority_by_hash.get(str(row["command_authority_hash"] or ""))
            if authority is not None:
                if authority.modality != row["modality"]:
                    raise ValueError(
                        "active verification command authority modality mismatch; "
                        f"obligation_id={row['obligation_id']!r}"
                    )
                return authority

            owner_candidates = authorities_for(
                modality=str(row["modality"]),
                owner_task_id=str(row["owner_task_id"]) if row["owner_task_id"] is not None else None,
            )
            if len(owner_candidates) == 1:
                return owner_candidates[0]

            covered_owners = {
                artifact_owner_by_id[obligation_id]
                for obligation_id in row["covers_obligation_ids"]
                if obligation_id in artifact_owner_by_id
            }
            if len(covered_owners) == 1:
                covered_owner_candidates = authorities_for(
                    modality=str(row["modality"]),
                    owner_task_id=next(iter(covered_owners)),
                )
                if len(covered_owner_candidates) == 1:
                    return covered_owner_candidates[0]

            modality_candidates = authorities_for(modality=str(row["modality"]))
            command_keys = {(item.argv, item.cwd) for item in modality_candidates}
            if len(command_keys) == 1 and modality_candidates:
                return sorted(modality_candidates, key=lambda item: (item.task_id, item.authority_hash))[0]
            raise ValueError(
                "active verification cannot be bound to one committed PM command authority; "
                f"obligation_id={row['obligation_id']!r}; modality={row['modality']!r}; "
                f"candidate_count={len(modality_candidates)}; command_count={len(command_keys)}"
            )

        def verification_obligation(row: Mapping[str, Any]) -> VerificationObligationV1:
            applicability = row["applicability"]
            authority_hash = row["command_authority_hash"]
            modality_authorities = authorities_for(modality=str(row["modality"]))
            if applicability != "not_applicable" and not modality_authorities:
                # CE verification rows are semantic advice; only PM owns the
                # executable command surface.  A live CE may propose a useful
                # verifier (for example ``go vet``) whose modality has no
                # committed PM command authority.  Keeping that row active
                # would either invent authority or fail the whole otherwise
                # valid portfolio.  Preserve it as an explicit, non-executable
                # N/A declaration instead.  Ambiguous PM authority is still a
                # hard failure in ``authority_for_row`` below.
                applicability = "not_applicable"
                authority_hash = None
                verifier_command = None
                covers_obligation_ids: tuple[str, ...] = ()
                owner_task_id = None
            elif applicability == "not_applicable":
                if authority_hash is not None:
                    raise ValueError("not_applicable verification must use command_authority_hash=null")
                verifier_command = None
                covers_obligation_ids = row["covers_obligation_ids"]
                owner_task_id = row["owner_task_id"]
            else:
                authority = authority_for_row(row)
                authority_hash = authority.authority_hash
                verifier_command = authority.command
                covers_obligation_ids = row["covers_obligation_ids"]
                owner_task_id = authority.task_id
            return VerificationObligationV1(
                obligation_id=row["obligation_id"],
                modality=row["modality"],
                command=verifier_command,
                applicability=applicability,
                covers_obligation_ids=covers_obligation_ids,
                owner_task_id=owner_task_id,
                command_authority_hash=authority_hash,
            )

        # Entrypoints are semantic CE suggestions but executable commands are
        # PM-owned authority.  An exact command match is accepted directly. If
        # the CE describes a PM-owned source path but phrases the invocation
        # differently, one unambiguous owner-scoped PM authority normalizes the
        # command instead of deleting the application's sole entrypoint.  This
        # does not widen authority: the source must either carry explicit PM
        # entrypoint-target authority, or use the legacy CE entrypoint artifact
        # marker when no explicit PM target was committed. Exact PM target/scope
        # ownership is still validated below. Live L1-02 committed
        # ``src/index.js`` plus ``npm start`` but labelled the artifact as
        # generic source; requiring a second model-authored semantic label
        # silently deleted that valid entrypoint.
        normalized_entrypoint_rows: list[Mapping[str, Any]] = []
        dropped_unexecutable_entrypoint_ids: set[str] = set()

        def normalize_advisory_runtime_path(row: Mapping[str, Any]) -> dict[str, Any]:
            """Drop malformed CE runtime locators while preserving PM command authority.

            Runtime paths are advisory deployment locators, not execution
            authority.  A source path plus an exact PM command is sufficient for
            an executable entrypoint.  Provider values such as ``./binary`` must
            therefore degrade to an omitted locator instead of failing the
            authority-owned completion contract.
            """

            normalized = dict(row)
            raw_runtime_path = normalized.get("runtime_path")
            if raw_runtime_path is None:
                return normalized
            path_parts = str(raw_runtime_path).replace("\\", "/").split("/")
            if any(part in {"", ".", ".."} for part in path_parts):
                normalized["runtime_path"] = None
            return normalized

        for row in entrypoint_rows:
            if row["applicability"] == "not_applicable":
                normalized_entrypoint_rows.append(row)
                continue
            source_path = str(row["source_path"]) if row["source_path"] is not None else None
            owner_task_id = str(row["owner_task_id"]) if row["owner_task_id"] is not None else None
            matching = authorities_for(
                modality="entrypoint",
                owner_task_id=owner_task_id,
            )
            exact_matches = tuple(item for item in matching if item.command == row["command"])
            if len(exact_matches) == 1:
                normalized_entrypoint_rows.append(normalize_advisory_runtime_path(row))
                continue
            if source_path is not None:
                path_authorized_owners = owners_for_path(source_path)
                path_exact_matches = tuple(
                    item
                    for item in authorities_for(modality="entrypoint")
                    if item.task_id in path_authorized_owners and item.command == row["command"]
                )
                if len(path_exact_matches) == 1:
                    # CE may select an upstream writer for a PM-shared
                    # entrypoint while only the downstream owner holds the
                    # executable command authority.  The path remains PM-owned;
                    # normalize only to the sole exact command owner.
                    normalized_row = normalize_advisory_runtime_path(row)
                    normalized_row["owner_task_id"] = path_exact_matches[0].task_id
                    normalized_entrypoint_rows.append(normalized_row)
                    continue
            if (
                len(matching) == 1
                and source_path is not None
                and (
                    owner_task_id in pm_entrypoint_owners_by_path.get(source_path, set())
                    or (
                        owner_task_id not in explicit_pm_entrypoint_owners
                        and entrypoint_artifact_owner_by_path.get(source_path) == owner_task_id
                    )
                )
            ):
                normalized_row = normalize_advisory_runtime_path(row)
                normalized_row["command"] = matching[0].command
                normalized_entrypoint_rows.append(normalized_row)
                continue
            if row["applicability"] != "not_applicable":
                dropped_unexecutable_entrypoint_ids.add(str(row["obligation_id"]))
            continue

        normalized_verification_rows: list[dict[str, Any]] = []
        for row in verification_rows:
            if row["modality"] not in {"build", "lint"}:
                continue
            normalized_row = dict(row)
            # A commandless CE entrypoint is advisory-only and was deliberately
            # removed above because it cannot bind to committed PM command
            # authority.  Its verifier references must be removed in the same
            # normalization transaction; otherwise Polaris creates its own
            # dangling obligation id and rejects an otherwise valid portfolio.
            # Truly unknown ids remain untouched and therefore still fail closed
            # in ProjectCompletionContractV1.
            normalized_row["covers_obligation_ids"] = [
                obligation_id
                for obligation_id in row["covers_obligation_ids"]
                if str(obligation_id) not in dropped_unexecutable_entrypoint_ids
                and str(obligation_id) not in dropped_unauthorized_artifact_ids
            ]
            normalized_verification_rows.append(normalized_row)
        used_verification_ids = {str(row["obligation_id"]) for row in normalized_verification_rows}
        required_test_seed_rows = [
            row for row in verification_rows if row["modality"] == "test" and row["applicability"] == "required"
        ]
        known_artifact_ids = {str(row["obligation_id"]) for row in artifact_rows}
        model_test_coverage = {
            str(obligation_id)
            for row in required_test_seed_rows
            for obligation_id in row["covers_obligation_ids"]
            if str(obligation_id) in known_artifact_ids
        }

        required_test_artifacts_by_owner: dict[str, list[str]] = {}
        not_applicable_test_artifacts = [
            str(row["obligation_id"])
            for row in artifact_rows
            if row["semantic_role"] == "test" and row["applicability"] == "not_applicable"
        ]
        for row in artifact_rows:
            if row["semantic_role"] != "test" or row["applicability"] != "required":
                continue
            required_test_artifacts_by_owner.setdefault(str(row["owner_task_id"]), []).append(str(row["obligation_id"]))
        for index, (owner_task_id, covered_ids) in enumerate(
            sorted(required_test_artifacts_by_owner.items()),
            start=1,
        ):
            test_authorities = authorities_for(modality="test", owner_task_id=owner_task_id)
            if len(test_authorities) != 1:
                raise ValueError(
                    "required test artifact owner must have exactly one committed PM test authority; "
                    f"owner_task_id={owner_task_id!r}; matches={len(test_authorities)}"
                )
            seed_id = (
                str(required_test_seed_rows[0]["obligation_id"])
                if index == 1 and required_test_seed_rows
                else f"verification-authority-test-{index:03d}"
            )
            if seed_id in used_verification_ids:
                seed_id = f"verification-authority-test-{index:03d}"
            used_verification_ids.add(seed_id)
            normalized_verification_rows.append(
                {
                    "obligation_id": seed_id,
                    "modality": "test",
                    "command_authority_hash": test_authorities[0].authority_hash,
                    "applicability": "required",
                    "covers_obligation_ids": sorted(set(covered_ids) | model_test_coverage),
                    "owner_task_id": owner_task_id,
                }
            )
        if not required_test_artifacts_by_owner and not_applicable_test_artifacts:
            normalized_verification_rows.append(
                {
                    "obligation_id": "verification-authority-test-na",
                    "modality": "test",
                    "command_authority_hash": None,
                    "applicability": "not_applicable",
                    "covers_obligation_ids": [],
                    "owner_task_id": None,
                }
            )

        environment_candidates: list[tuple[int, str, Any, str]] = []
        for row in artifact_rows:
            if row["applicability"] != "required" or row["owner_task_id"] is None:
                continue
            owner_task_id = str(row["owner_task_id"])
            for authority in authorities_for(modality="environment_prep", owner_task_id=owner_task_id):
                semantic_priority = 0 if row["semantic_role"] in {"manifest", "config"} else 1
                environment_candidates.append((semantic_priority, str(row["obligation_id"]), authority, owner_task_id))

        if project_kind_authority.project_kind == "application":
            if not environment_candidates:
                raise ValueError("application has no PM-authorized environment_prep command for a required artifact")
            _, environment_artifact_id, environment_authority, environment_owner = sorted(
                environment_candidates,
                key=lambda item: (item[0], item[1], item[2].authority_hash),
            )[0]
            environment_seed_rows = [
                row
                for row in verification_rows
                if row["modality"] == "environment_prep" and row["applicability"] == "required"
            ]
            environment_obligation_id = (
                str(environment_seed_rows[0]["obligation_id"])
                if environment_seed_rows
                else "verification-authority-environment-001"
            )
            if environment_obligation_id in used_verification_ids:
                environment_obligation_id = "verification-authority-environment-001"
            used_verification_ids.add(environment_obligation_id)
            normalized_verification_rows.append(
                {
                    "obligation_id": environment_obligation_id,
                    "modality": "environment_prep",
                    "command_authority_hash": environment_authority.authority_hash,
                    "applicability": "required",
                    "covers_obligation_ids": [environment_artifact_id],
                    "owner_task_id": environment_owner,
                }
            )
        else:
            environment_rows = [dict(row) for row in verification_rows if row["modality"] == "environment_prep"]
            if environment_rows:
                normalized_verification_rows.extend(environment_rows)
            elif environment_candidates:
                # Library/package CE output may omit advisory completion rows.
                # A committed PM environment command is still project authority,
                # so project it exactly instead of turning omission into a fatal
                # contract gap.  Binding stays owner/path scoped and no command is
                # invented by CE or this normalization layer.
                _, environment_artifact_id, environment_authority, environment_owner = sorted(
                    environment_candidates,
                    key=lambda item: (item[0], item[1], item[2].authority_hash),
                )[0]
                normalized_verification_rows.append(
                    {
                        "obligation_id": "verification-authority-environment-001",
                        "modality": "environment_prep",
                        "command_authority_hash": environment_authority.authority_hash,
                        "applicability": "required",
                        "covers_obligation_ids": [environment_artifact_id],
                        "owner_task_id": environment_owner,
                    }
                )
            else:
                normalized_verification_rows.append(
                    {
                        "obligation_id": "verification-authority-environment-na",
                        "modality": "environment_prep",
                        "command_authority_hash": None,
                        "applicability": "not_applicable",
                        "covers_obligation_ids": [],
                        "owner_task_id": None,
                    }
                )

        for index, entrypoint_row in enumerate(normalized_entrypoint_rows, start=1):
            if entrypoint_row["applicability"] == "not_applicable":
                continue
            entrypoint_authorities = tuple(
                item
                for item in authorities_for(
                    modality="entrypoint",
                    owner_task_id=str(entrypoint_row["owner_task_id"]),
                )
                if item.command == entrypoint_row["command"]
            )
            if len(entrypoint_authorities) != 1:
                raise ValueError(
                    "active entrypoint must bind exactly one committed PM entrypoint authority; "
                    f"obligation_id={entrypoint_row['obligation_id']!r}; matches={len(entrypoint_authorities)}"
                )
            entrypoint_seed_rows = [
                candidate
                for candidate in verification_rows
                if candidate["modality"] == "entrypoint"
                and entrypoint_row["obligation_id"] in candidate["covers_obligation_ids"]
            ]
            entrypoint_obligation_id = (
                str(entrypoint_seed_rows[0]["obligation_id"])
                if entrypoint_seed_rows
                else f"verification-authority-entrypoint-{index:03d}"
            )
            if entrypoint_obligation_id in used_verification_ids:
                entrypoint_obligation_id = f"verification-authority-entrypoint-{index:03d}"
            used_verification_ids.add(entrypoint_obligation_id)
            normalized_verification_rows.append(
                {
                    "obligation_id": entrypoint_obligation_id,
                    "modality": "entrypoint",
                    "command_authority_hash": entrypoint_authorities[0].authority_hash,
                    "applicability": "required",
                    "covers_obligation_ids": [entrypoint_row["obligation_id"]],
                    "owner_task_id": entrypoint_row["owner_task_id"],
                }
            )

        obligations = ProjectCompletionObligationsV1(
            artifacts=tuple(
                ArtifactObligationV1(
                    obligation_id=row["obligation_id"],
                    path=row["path"],
                    semantic_role=row["semantic_role"],
                    applicability=row["applicability"],
                    owner_task_id=row["owner_task_id"],
                )
                for row in artifact_rows
            ),
            entrypoints=tuple(
                EntrypointObligationV1(
                    obligation_id=row["obligation_id"],
                    kind=row["kind"],
                    applicability=row["applicability"],
                    owner_task_id=row["owner_task_id"],
                    source_path=row["source_path"],
                    runtime_path=row["runtime_path"],
                    command=row["command"],
                )
                for row in normalized_entrypoint_rows
            ),
            verification=tuple(verification_obligation(row) for row in normalized_verification_rows),
        )
        for artifact in obligations.artifacts:
            if artifact.applicability == "not_applicable":
                continue
            authorized_owners = owners_for_path(artifact.path)
            if not authorized_owners:
                raise ValueError(
                    "active artifact path is outside exact PM target or component-safe scope authority; "
                    f"obligation_id={artifact.obligation_id!r}:path={artifact.path!r}"
                )
            if artifact.owner_task_id not in authorized_owners:
                raise ValueError(
                    "active artifact owner_task_id does not own its PM target/scope path; "
                    f"obligation_id={artifact.obligation_id!r}:path={artifact.path!r}:"
                    f"owner_task_id={artifact.owner_task_id!r}:authorized_owners={sorted(authorized_owners)}"
                )

        for entrypoint in obligations.entrypoints:
            if entrypoint.applicability == "not_applicable":
                continue
            source_owners = owners_for_path(entrypoint.source_path) if entrypoint.source_path is not None else set()
            if entrypoint.source_path is not None:
                if not source_owners:
                    raise ValueError(
                        "active entrypoint path is outside exact PM target or component-safe scope authority; "
                        f"obligation_id={entrypoint.obligation_id!r}:source_path={entrypoint.source_path!r}"
                    )
                if entrypoint.owner_task_id not in source_owners:
                    raise ValueError(
                        "active entrypoint owner_task_id does not own its PM target/scope path; "
                        f"obligation_id={entrypoint.obligation_id!r}:source_path={entrypoint.source_path!r}:"
                        f"owner_task_id={entrypoint.owner_task_id!r}:"
                        f"authorized_owners={sorted(source_owners)}"
                    )

            if entrypoint.runtime_path is not None:
                runtime_owners = owners_for_path(entrypoint.runtime_path)
                if runtime_owners:
                    if entrypoint.owner_task_id not in runtime_owners:
                        raise ValueError(
                            "active entrypoint owner_task_id does not own its PM target/scope path; "
                            f"obligation_id={entrypoint.obligation_id!r}:runtime_path={entrypoint.runtime_path!r}:"
                            f"owner_task_id={entrypoint.owner_task_id!r}:"
                            f"authorized_owners={sorted(runtime_owners)}"
                        )
                elif entrypoint.source_path is None or entrypoint.owner_task_id not in source_owners:
                    # Compilers legitimately place runnable output outside PM
                    # source targets (for example ``src/main.ts`` becomes
                    # ``dist/main.js``). The exact PM entrypoint command was
                    # already bound above, so a safe relative runtime locator
                    # may derive from an owner-authorized source entrypoint.
                    # Runtime-only guesses without such a source remain
                    # fail-closed.
                    raise ValueError(
                        "active entrypoint path is outside exact PM target or component-safe scope authority; "
                        f"obligation_id={entrypoint.obligation_id!r}:runtime_path={entrypoint.runtime_path!r}"
                    )

        required_artifact_paths = {item.path for item in obligations.artifacts if item.applicability == "required"}
        pm_target_paths = {path for task in command.tasks for path in task.target_files}
        missing_pm_target_paths = sorted(pm_target_paths - required_artifact_paths)
        if missing_pm_target_paths:
            raise ValueError(
                "completion contract must declare every PM target file as a required artifact; "
                f"missing={missing_pm_target_paths}"
            )
        return build_project_completion_contract(
            project_id=carrier.project_id,
            run_id=command.run_id,
            project_kind=project_kind_authority.project_kind,
            project_kind_authority=project_kind_authority,
            pm_contract_hash=carrier.pm_contract_hash,
            covered_task_ids=tuple(task.task_id for task in command.tasks),
            obligations=obligations,
            completion_predicate_version=_PROJECT_COMPLETION_PREDICATE_VERSION,
            verifier_policy_hash=carrier.verifier_policy_hash,
            verifier_policy_snapshot_hash=carrier.verifier_policy_snapshot_hash,
            verification_command_authority=carrier.verification_command_authority,
        )
    except (TypeError, ValueError) as exc:
        raise _portfolio_contract_error(
            f"invalid project completion contract: {exc}",
            code="invalid_project_completion_contract",
            details={"error_type": type(exc).__name__},
        ) from exc


def _parse_portfolio_llm_blueprint(
    command: BuildChiefEngineerBlueprintPortfolioCommandV1,
) -> _PortfolioLlmBlueprint:
    raw = dict(command.llm_blueprint)
    if not raw:
        return _PortfolioLlmBlueprint({}, {}, (), (), (), (), (), None, False)

    allowed_top_level = {
        "construction_plan",
        "project_completion_contract",
        "risk_flags",
        "scope_for_apply",
    }
    unknown_top_level = sorted(str(key) for key in raw if str(key) not in allowed_top_level)
    if unknown_top_level:
        raise _portfolio_contract_error(
            "LLM portfolio blueprint contains unknown top-level fields",
            details={"unknown_fields": unknown_top_level},
        )
    if "project_completion_contract" not in raw:
        raise _portfolio_contract_error(
            "advisory CE portfolio must define project_completion_contract",
            code="invalid_project_completion_contract",
            details={"missing_fields": ["project_completion_contract"]},
        )
    completion_requirements = _strict_completion_mapping(
        raw["project_completion_contract"],
        field_name="project_completion_contract",
        expected_fields={"obligations"},
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
    consumed = True
    return _PortfolioLlmBlueprint(
        shared_plan=shared_plan,
        task_plans=task_plans,
        scope_paths=scope_paths,
        scope_rejections=scope_rejections,
        risk_flags=risk_flags,
        provider_declarations=providers,
        consumer_declarations=consumers,
        project_completion_requirements=completion_requirements,
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


def _pm_authoritative_task_plan(task: ChiefEngineerPortfolioTaskV1) -> dict[str, Any]:
    """Project the non-negotiable PM task boundary into every CE overlay.

    Per-task LLM plans are advisory detail.  Providers may omit one or all of
    them, but that omission must not erase the PM objective, target ownership,
    dependency order, or entrypoint authority already validated by Factory.
    """

    return {
        "objective": task.objective,
        "target_files": list(task.target_files),
        "scope_paths": list(task.scope_paths),
        "dependencies": list(task.dependencies),
        "entrypoint_targets": list(task.entrypoint_targets),
    }


def _deterministic_portfolio_plan(task: ChiefEngineerPortfolioTaskV1) -> dict[str, Any]:
    return {
        "source": "chief_engineer.deterministic_pm_task_projection",
        "diagnostic_only": True,
        **_pm_authoritative_task_plan(task),
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
    project_completion_contract_ref: str | None,
    project_completion_contract_hash: str | None,
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
            "project_completion_contract_ref": project_completion_contract_ref,
            "project_completion_contract_hash": project_completion_contract_hash,
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
            "project_completion_contract_ref": project_completion_contract_ref,
            "project_completion_contract_hash": project_completion_contract_hash,
            "reference": reference,
            "llm_blueprint_consumed": llm_blueprint_consumed,
            "usage_mode": usage_mode,
            "authority": "advisory_only",
            "handoff_ready": False,
            "execution_authorized": False,
        }
    return bound


def _persist_immutable_blueprint_portfolio(
    portfolio: ChiefEngineerBlueprintPortfolioV1,
    *,
    reject_existing: bool = False,
    require_existing: bool = False,
) -> None:
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
        if reject_existing:
            raise _portfolio_contract_error(
                "Factory-issued portfolio authority was already consumed",
                code="project_completion_authority_replay",
                details={"portfolio_id": portfolio.portfolio_id},
            )
        return

    if require_existing:
        raise _portfolio_contract_error(
            "Exact immutable blueprint portfolio was not found for revalidation",
            code="blueprint_portfolio_revalidation_target_missing",
            details={"portfolio_id": portfolio.portfolio_id},
        )

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


def query_project_completion_contract(
    query: QueryProjectCompletionContractV1,
) -> ProjectCompletionContractV1:
    """Read one exact immutable completion contract through the CE owner port."""

    if type(query) is not QueryProjectCompletionContractV1:
        raise TypeError("query must be exact QueryProjectCompletionContractV1")
    persistence = BlueprintPersistence(query.workspace, ensure_directory=False)
    matches: list[ProjectCompletionContractV1] = []
    for portfolio_id in persistence.list_all():
        payload = persistence.load(portfolio_id)
        if not isinstance(payload, Mapping) or payload.get("kind") != "chief_engineer_blueprint_portfolio":
            continue
        completion_payload = payload.get("project_completion_contract")
        if not isinstance(completion_payload, Mapping):
            continue
        if (
            completion_payload.get("project_id") != query.project_id
            or completion_payload.get("run_id") != query.run_id
            or completion_payload.get("contract_hash") != query.contract_hash
        ):
            continue
        embedded_portfolio_hash = str(payload.get("portfolio_hash") or "")
        if _portfolio_hash(payload) != embedded_portfolio_hash:
            raise _portfolio_contract_error(
                "completion contract portfolio hash is invalid",
                code="project_completion_contract_integrity_failed",
                details={"portfolio_id": portfolio_id},
            )
        try:
            contract = ProjectCompletionContractV1.from_dict(completion_payload)
        except (TypeError, ValueError) as exc:
            raise _portfolio_contract_error(
                f"persisted project completion contract is invalid: {exc}",
                code="project_completion_contract_integrity_failed",
                details={"portfolio_id": portfolio_id},
            ) from exc
        expected_ref = f"{payload.get('portfolio_path')}#project_completion_contract"
        reference = payload.get("reference")
        if (
            payload.get("project_completion_contract_ref") != expected_ref
            or payload.get("project_completion_contract_hash") != contract.contract_hash
            or not isinstance(reference, Mapping)
            or reference.get("project_completion_contract_ref") != expected_ref
            or reference.get("project_completion_contract_hash") != contract.contract_hash
            or Path(str(payload.get("workspace") or "")).resolve() != Path(query.workspace).resolve()
        ):
            raise _portfolio_contract_error(
                "completion contract portfolio binding is invalid",
                code="project_completion_contract_integrity_failed",
                details={"portfolio_id": portfolio_id},
            )
        matches.append(contract)

    if not matches:
        raise _portfolio_contract_error(
            "project completion contract was not found for the exact authority identity",
            code="project_completion_contract_not_found",
            details={
                "project_id": query.project_id,
                "run_id": query.run_id,
                "contract_hash": query.contract_hash,
            },
        )
    if len(matches) != 1:
        raise _portfolio_contract_error(
            "multiple project completion contracts matched one authority identity",
            code="project_completion_contract_ambiguous",
            details={"match_count": len(matches), "contract_hash": query.contract_hash},
        )
    return matches[0]


def build_chief_engineer_blueprint_portfolio(
    command: BuildChiefEngineerBlueprintPortfolioCommandV1,
    *,
    revalidate_existing: bool = False,
) -> ChiefEngineerBlueprintPortfolioV1:
    """Build and immutably persist one advisory portfolio for PM tasks.

    The canonical LLM payload is consumed once. Every task receives a merged
    shared/task plan, while scope suggestions are intersected with that task's
    PM-authoritative target and scope paths. A no-LLM command creates an
    explicitly offline diagnostic object and never grants handoff authority.
    """

    if type(command) is not BuildChiefEngineerBlueprintPortfolioCommandV1:
        raise TypeError("command must be exact BuildChiefEngineerBlueprintPortfolioCommandV1")
    parsed = _parse_portfolio_llm_blueprint(command)
    usage_mode: Literal["advisory_overlay", "offline_diagnostic_only"] = (
        "advisory_overlay" if parsed.consumed else "offline_diagnostic_only"
    )
    task_overlays: dict[str, dict[str, Any]] = {}
    scope_advisory: dict[str, dict[str, Any]] = {}
    portfolio_risks: tuple[str, ...] = parsed.risk_flags
    # Late-bound via package so tests can monkeypatch public.service._build_portfolio_completion_contract.
    if parsed.project_completion_requirements is not None:
        import sys as _sys

        project_completion_contract = _sys.modules[__package__]._build_portfolio_completion_contract(
            command, parsed.project_completion_requirements
        )
    else:
        project_completion_contract = None

    for task in command.tasks:
        if parsed.consumed:
            task_plan, task_scope, task_rejections, task_risks = _task_plan_components(
                task.task_id,
                parsed.task_plans.get(task.task_id, {}),
            )
            construction_plan = _merge_portfolio_construction_plan(parsed.shared_plan, task_plan)
            # The LLM contributes advisory implementation detail; PM remains
            # the task-boundary authority even when the provider omits a
            # task-local overlay or attempts to restate an owned field.
            construction_plan.update(_pm_authoritative_task_plan(task))
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
        "project_completion_contract_hash": (
            project_completion_contract.contract_hash if project_completion_contract is not None else None
        ),
        "llm_blueprint_consumed": parsed.consumed,
        "usage_mode": usage_mode,
    }
    portfolio_id = f"ce_portfolio_{stable_hash(identity_seed)[:32]}"
    portfolio_path = _blueprint_path(portfolio_id)
    interface_ref = f"{portfolio_path}#project_interface_contract"
    completion_ref = (
        f"{portfolio_path}#project_completion_contract" if project_completion_contract is not None else None
    )
    completion_hash = project_completion_contract.contract_hash if project_completion_contract is not None else None
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
            project_completion_contract_ref=completion_ref,
            project_completion_contract_hash=completion_hash,
            llm_blueprint_consumed=parsed.consumed,
            usage_mode=usage_mode,
        ),
        scope_advisory=scope_advisory,
        project_interface_contract=project_interface_contract,
        project_interface_contract_ref=interface_ref,
        project_interface_contract_hash=interface_hash,
        project_completion_contract=project_completion_contract,
        project_completion_contract_ref=completion_ref,
        project_completion_contract_hash=completion_hash,
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
            project_completion_contract_ref=completion_ref,
            project_completion_contract_hash=completion_hash,
            llm_blueprint_consumed=parsed.consumed,
            usage_mode=usage_mode,
        ),
        scope_advisory=scope_advisory,
        project_interface_contract=project_interface_contract,
        project_interface_contract_ref=interface_ref,
        project_interface_contract_hash=interface_hash,
        project_completion_contract=project_completion_contract,
        project_completion_contract_ref=completion_ref,
        project_completion_contract_hash=completion_hash,
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
    if command.llm_blueprint:
        _revalidate_portfolio_authority_carrier(command)
    _persist_immutable_blueprint_portfolio(
        portfolio,
        reject_existing=bool(command.llm_blueprint) and not revalidate_existing,
        require_existing=revalidate_existing,
    )
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
        "project_completion_contract_ref",
        "project_completion_contract_hash",
        "project_completion_contract",
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
    completion_ref = str(context.get("project_completion_contract_ref") or "").strip()
    completion_hash = str(context.get("project_completion_contract_hash") or "").strip()
    normalized_portfolio_ref, portfolio_ref_error = _normalize_portfolio_advisory_path(portfolio_ref)
    interface_path, separator, interface_fragment = interface_ref.partition("#")
    normalized_interface_path, interface_ref_error = _normalize_portfolio_advisory_path(interface_path)
    completion_path, completion_separator, completion_fragment = completion_ref.partition("#")
    normalized_completion_path, completion_ref_error = _normalize_portfolio_advisory_path(completion_path)
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
        or completion_ref_error
        or completion_separator != "#"
        or completion_fragment != "project_completion_contract"
        or f"{normalized_completion_path}#project_completion_contract" != completion_ref
        or normalized_completion_path != portfolio_ref
        or not completion_hash
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

    try:
        completion_contract = ProjectCompletionContractV1.from_dict(
            _portfolio_mapping(
                context.get("project_completion_contract"),
                field_name="project_completion_contract",
            )
        )
    except (TypeError, ValueError) as exc:
        raise _portfolio_contract_error(
            f"project completion contract is invalid: {exc}",
            code="invalid_blueprint_portfolio_context",
            details={"task_id": task_id, "field": "project_completion_contract"},
        ) from exc
    if completion_contract.contract_hash != completion_hash:
        raise _portfolio_contract_error(
            "project completion contract hash does not match its context binding",
            code="invalid_blueprint_portfolio_context",
            details={"task_id": task_id, "field": "project_completion_contract_hash"},
        )
    if task_id not in completion_contract.covered_task_ids:
        raise _portfolio_contract_error(
            "project completion contract does not cover the current PM task",
            code="blueprint_portfolio_pm_authority_mismatch",
            details={
                "task_id": task_id,
                "covered_task_ids": list(completion_contract.covered_task_ids),
            },
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
        "project_completion_contract_ref": completion_ref,
        "project_completion_contract_hash": completion_hash,
        "project_completion_contract": completion_contract.to_dict(),
    }
