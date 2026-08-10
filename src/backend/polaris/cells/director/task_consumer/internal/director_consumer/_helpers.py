"""Director consumer helpers (handoff, evidence, QA authority, adapter input)."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import sys
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Coroutine

from polaris.cells.director.task_consumer.public.project_verification import (
    ProjectVerificationReceiptV1,
    QueryProjectVerificationReceiptV1,
    ResolveProjectVerificationAuthorityQueryV1,
)


def _package() -> Any:
    """Return the package module for monkeypatch-visible late binding."""
    return sys.modules[__package__]


logger = logging.getLogger(__name__)

_ROUTE_DIRECT_TO_DIRECTOR = "direct_to_director"
_ROUTE_CHIEF_BLUEPRINT_REQUIRED = "chief_blueprint_required"
_OWNER_HANDOFF_TASK_RECORD_LIMIT = 10_000
_OWNER_HANDOFF_REQUEST_KEYS = (
    "ownership_handoff_requests",
    "owner_task_retry_handoff_requests",
    "unresolved_owner_handoff_requests",
)
_STRUCTURED_FAILURE_MAPPING_KEYS = (
    "task_boundary_scope_filter",
    "scope_authority",
    "adapter_result",
    "metadata",
    "failure_payload",
    "typed_failure",
    "task_boundary",
    "task_boundary_failure",
    "scope_authority_evidence",
    "evidence",
)
_STRUCTURED_FAILURE_SEQUENCE_KEYS = (
    "failure_evidence",
    "evidence_rows",
    "evidence",
)
_MAX_STRUCTURED_FAILURE_MAPPINGS = 32


def _normalize_task_market_route(payload: dict[str, Any]) -> str:
    metadata_raw = payload.get("metadata")
    metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
    for container in (payload, metadata):
        for key in ("task_market_route", "route", "routing", "dispatch_route", "execution_route"):
            token = str(container.get(key) or "").strip().lower().replace("-", "_").replace(" ", "_")
            if token in {
                _ROUTE_DIRECT_TO_DIRECTOR,
                "direct",
                "director",
                "director_direct",
                "direct_director",
                "pending_exec",
                "exec",
                "execution",
            }:
                return _ROUTE_CHIEF_BLUEPRINT_REQUIRED
            if token in {
                _ROUTE_CHIEF_BLUEPRINT_REQUIRED,
                "chief",
                "chief_engineer",
                "chiefengineer",
                "blueprint",
                "blueprint_required",
                "requires_blueprint",
                "pending_design",
                "design",
            }:
                return _ROUTE_CHIEF_BLUEPRINT_REQUIRED
        for key in ("blueprint_required", "requires_blueprint", "chief_engineer_required"):
            value = container.get(key)
            if isinstance(value, bool):
                return _ROUTE_CHIEF_BLUEPRINT_REQUIRED
            if isinstance(value, str):
                bool_token = value.strip().lower()
                if bool_token in {"1", "true", "yes", "y", "on"}:
                    return _ROUTE_CHIEF_BLUEPRINT_REQUIRED
                if bool_token in {"0", "false", "no", "n", "off"}:
                    return _ROUTE_CHIEF_BLUEPRINT_REQUIRED
    return _ROUTE_CHIEF_BLUEPRINT_REQUIRED


def _validated_blueprint_handoff(
    workspace: str, task_id: str, payload: dict[str, Any]
) -> tuple[bool, str, str, dict[str, Any]]:
    payload_with_task_id = dict(payload)
    payload_with_task_id.setdefault("task_id", task_id)
    validation = _package().validate_director_handoff_from_payload(workspace, payload_with_task_id, require_strict=True)
    return (
        bool(validation.get("allowed")),
        str(validation.get("blueprint_id") or ""),
        str(validation.get("reason") or ""),
        validation,
    )


def _normalize_handoff_validation_result(result: Any) -> tuple[bool, str, str, dict[str, Any]]:
    """Normalize legacy test shims and the strict production validation result."""

    if isinstance(result, tuple):
        if len(result) >= 4:
            return bool(result[0]), str(result[1] or ""), str(result[2] or ""), dict(result[3] or {})
        if len(result) >= 3:
            return bool(result[0]), str(result[1] or ""), str(result[2] or ""), {}
    return False, "", "invalid Chief Engineer handoff validation result", {}


def _attach_handoff_validation_payload(payload: dict[str, Any], validation: dict[str, Any]) -> None:
    """Project strict handoff evidence into the Director execution payload."""

    if not validation:
        return
    strict_decision = validation.get("strict_decision_payload")
    if isinstance(strict_decision, dict) and strict_decision:
        payload["ce_handoff_decision"] = dict(strict_decision)
        payload["ce_handoff_decision_hash"] = str(strict_decision.get("decision_hash") or "")
        payload.setdefault("handoff_decision_hash", str(strict_decision.get("decision_hash") or ""))
    legacy_decision = validation.get("decision_payload")
    if isinstance(legacy_decision, dict) and legacy_decision:
        payload.setdefault("handoff_decision", dict(legacy_decision))
    task_completion_projection = validation.get("task_completion_projection")
    if isinstance(task_completion_projection, dict) and task_completion_projection:
        payload["task_completion_projection"] = dict(task_completion_projection)
        payload["completion_contract_hash"] = str(task_completion_projection.get("project_contract_hash") or "")
        payload["completion_contract_ref"] = str(task_completion_projection.get("project_contract_ref") or "")
    validation_audit = dict(validation)
    validation_audit.pop("task_completion_projection", None)
    payload["director_handoff_validation"] = validation_audit


def _job_token_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    metadata_raw = payload.get("metadata")
    metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
    for container in (payload, metadata):
        for key in ("job_token", "control_plane_job_token", "capability_token"):
            value = container.get(key)
            if isinstance(value, dict) and str(value.get("token_id") or "").strip():
                return dict(value)
    return {}


def _mapping_copy(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


_NO_CHANGE_FLAGS = frozenset(
    {
        "allow_no_changes",
        "no_changes_expected",
        "allow_empty_changed_files",
        "director_noop_allowed",
    }
)
_NO_CHANGE_MODES = frozenset(
    {
        "noop",
        "no_op",
        "no-op",
        "read_only",
        "read-only",
        "inspection",
        "inspection_only",
        "analysis_only",
    }
)
_VERIFIED_EXISTING_SCOPE_MODES = frozenset({"verified_existing_workspace_scope"})


class UnrecoverableExecutionError(RuntimeError):
    """Execution failure that should be dead-lettered and compensated."""


class InterfaceContractAmendmentRequiredError(RuntimeError):
    """Execution evidence proved CE must revise the interface contract."""

    def __init__(self, message: str, *, amendment_request: dict[str, Any]) -> None:
        super().__init__(message)
        self.amendment_request = dict(amendment_request)


class InterfaceContractRepairRequiredError(RuntimeError):
    """Execution evidence proved Director should repair within the interface contract."""

    def __init__(self, message: str, *, repair_evidence: dict[str, Any]) -> None:
        super().__init__(message)
        self.repair_evidence = dict(repair_evidence)


def _contract_authority_blocker(
    *,
    task_id: str,
    error_code: str,
    evidence: Mapping[str, Any],
    payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project one terminal blocker without requesting upstream replanning."""

    source_payload = payload if isinstance(payload, Mapping) else {}
    normalized_task_id = str(task_id or "").strip()
    normalized_error_code = str(error_code or "").strip()
    if not normalized_task_id or not normalized_error_code:
        raise ValueError("contract authority blocker requires task_id and error_code")
    raw_job_token = source_payload.get("job_token")
    job_token = raw_job_token if isinstance(raw_job_token, Mapping) else {}
    completion_contract_hash = str(
        source_payload.get("completion_contract_hash")
        or source_payload.get("contract_hash")
        or job_token.get("contract_hash")
        or "missing"
    ).strip()
    blueprint_id = str(source_payload.get("blueprint_id") or "missing").strip()
    run_id = str(
        source_payload.get("run_id") or source_payload.get("factory_run_id") or job_token.get("run_id") or "missing"
    ).strip()
    trace_id = str(source_payload.get("trace_id") or job_token.get("trace_id") or "missing").strip()
    missing_identity_fields = tuple(
        name
        for name, value in (
            ("completion_contract_hash", completion_contract_hash),
            ("blueprint_id", blueprint_id),
            ("run_id", run_id),
            ("trace_id", trace_id),
        )
        if value == "missing"
    )
    return {
        "schema_version": "director.contract_authority_blocker.v1",
        "blocker_kind": "contract_or_authority_contradiction",
        "task_id": normalized_task_id,
        "error_code": normalized_error_code,
        "completion_contract_hash": completion_contract_hash,
        "blueprint_id": blueprint_id,
        "run_id": run_id,
        "trace_id": trace_id,
        "identity_complete": not missing_identity_fields,
        "missing_identity_fields": list(missing_identity_fields),
        "automatic_upstream_replan": False,
        "automatic_escalation": False,
        "retry_same_contract": False,
        "evidence": dict(evidence),
    }


@dataclass(frozen=True, slots=True)
class _OwnerHandoffFailure:
    """Typed adapter-failure evidence needed for owner-task routing."""

    scope_payload: dict[str, Any]
    failure_class: str
    responsible_layer: str
    failure_evidence: tuple[dict[str, Any], ...]


class _OwnerHandoffRoutingRequiredError(RuntimeError):
    """Control-flow signal for a structured ScopeAuthority owner handoff."""

    def __init__(self, message: str, *, failure: _OwnerHandoffFailure) -> None:
        super().__init__(message)
        self.failure = failure


DirectorTaskExecutor = Callable[[str, dict[str, Any], str], dict[str, Any]]


def _director_execution_timeout_seconds(visibility_timeout_seconds: int) -> float:
    default_timeout = min(600.0, float(max(1, int(visibility_timeout_seconds))))
    raw = os.environ.get("KERNELONE_TASK_MARKET_DIRECTOR_EXECUTION_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return default_timeout
    try:
        configured = float(raw)
    except ValueError:
        return default_timeout
    if configured <= 0:
        return default_timeout
    return min(configured, float(max(1, int(visibility_timeout_seconds))))


async def _await_with_optional_timeout(
    coro: Coroutine[Any, Any, dict[str, Any]],
    timeout_seconds: float | None,
) -> dict[str, Any]:
    if timeout_seconds is not None and timeout_seconds > 0:
        return await asyncio.wait_for(coro, timeout=timeout_seconds)
    return await coro


def _run_coroutine_sync(
    coro: Coroutine[Any, Any, dict[str, Any]],
    *,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Run an async Director adapter call from the synchronous consumer loop."""

    bounded_timeout = timeout_seconds if timeout_seconds is not None and timeout_seconds > 0 else None
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        if bounded_timeout is None:
            return asyncio.run(coro)

    result_box: dict[str, Any] = {}

    def _runner() -> None:
        try:
            result_box["result"] = asyncio.run(_await_with_optional_timeout(coro, bounded_timeout))
        except BaseException as exc:  # noqa: BLE001
            result_box["error"] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join(bounded_timeout)
    if thread.is_alive():
        raise TimeoutError(f"Director adapter execution timed out after {bounded_timeout:.1f}s")
    error = result_box.get("error")
    if isinstance(error, BaseException):
        raise error
    result = result_box.get("result")
    if isinstance(result, dict):
        return result
    return {}


def _normalize_string_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, (str, os.PathLike)):
        raw_values: list[Any] = [raw]
    elif isinstance(raw, (list, tuple, set)):
        raw_values = list(raw)
    else:
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_values:
        if not isinstance(item, (str, os.PathLike)):
            continue
        token = str(item).strip()
        if not token:
            continue
        key = token.replace("\\", "/").lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(token)
    return normalized


def _truthy_payload_flag(payload: dict[str, Any], key: str) -> bool:
    value = payload.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return False


def _allows_no_execution_evidence(payload: dict[str, Any]) -> bool:
    for key in _NO_CHANGE_FLAGS:
        if _truthy_payload_flag(payload, key):
            return True

    for key in ("execution_mode", "task_mode", "mode", "change_mode"):
        mode = str(payload.get(key) or "").strip().lower()
        if mode in _NO_CHANGE_MODES:
            return True
    return False


def _has_verified_existing_scope_evidence(exec_result: dict[str, Any]) -> bool:
    adapter_summary = exec_result.get("director_adapter_result")
    if not isinstance(adapter_summary, dict):
        return False
    if adapter_summary.get("success") is not True:
        return False
    materialization_mode = str(adapter_summary.get("materialization_mode") or "").strip()
    if materialization_mode not in _VERIFIED_EXISTING_SCOPE_MODES:
        return False
    existing_contract_evidence = adapter_summary.get("existing_contract_evidence")
    return isinstance(existing_contract_evidence, dict) and existing_contract_evidence.get("ok") is True


def _director_evidence_status(changed_files: list[str], exec_result: dict[str, Any]) -> str:
    if changed_files:
        return "changed_files_reported"
    if _has_verified_existing_scope_evidence(exec_result):
        return "verified_existing_workspace_scope"
    return "explicit_no_changes"


def _verified_existing_scope_covers_target(exec_result: dict[str, Any], target_file: str) -> bool:
    target = target_file.strip().replace("\\", "/").lstrip("./")
    if not target or not _has_verified_existing_scope_evidence(exec_result):
        return False
    adapter_summary = exec_result.get("director_adapter_result")
    if not isinstance(adapter_summary, dict):
        return False
    existing_contract_evidence = adapter_summary.get("existing_contract_evidence")
    if not isinstance(existing_contract_evidence, dict):
        return False
    for raw_path in _normalize_string_list(existing_contract_evidence.get("existing_paths")):
        candidate = raw_path.strip().replace("\\", "/").lstrip("./")
        if candidate == target or candidate.endswith(f"/{target}"):
            return True
    return False


def _step_target_file(payload: dict[str, Any]) -> str:
    """Declared single target for fission or direct single-file work."""
    step = payload.get("construction_step")
    if isinstance(step, dict):
        return str(step.get("target_file") or "").strip().replace("\\", "/").lstrip("./")
    target_files = _normalize_string_list(payload.get("target_files"))
    if len(target_files) == 1:
        return target_files[0].strip().replace("\\", "/").lstrip("./")
    return ""


def _changed_files_cover_target(target_file: str, changed_files: list[str]) -> bool:
    target = target_file.strip().replace("\\", "/").lstrip("./")
    if not target:
        return True
    for raw in changed_files:
        candidate = str(raw).strip().replace("\\", "/").lstrip("./")
        if candidate == target or candidate.endswith(f"/{target}"):
            return True
    return False


# R7-C (I3-r28): deterministic anti-shrink backstop for repair turns. The weak
# model, asked to fix one named error in an existing file, tends to rewrite the
# whole file SMALLER (live r28: main.js 5762B/22 constructs -> 3095B/12). The
# tool-restriction (R7-A) and preserve-instruction nudge it; this gate is the
# fail-closed guarantee that does not trust the model — a repair that drops the
# file below a fraction of its prior size is rejected and re-taught.
_REPAIR_SHRINK_GUARD_RATIO_ENV = "KERNELONE_REPAIR_SHRINK_GUARD_RATIO"
_DEFAULT_REPAIR_SHRINK_GUARD_RATIO = 0.6
# Files smaller than this pre-repair are not guarded — a tiny stub legitimately
# changes size by large fractions, and the floor is meaningless there.
_REPAIR_SHRINK_MIN_PRIOR_BYTES = 400


def _repair_shrink_guard_ratio() -> float:
    """Resolve the shrink floor ratio (env override, else 0.6). Clamped to (0,1]."""
    raw = os.environ.get(_REPAIR_SHRINK_GUARD_RATIO_ENV, "").strip()
    if raw:
        try:
            value = float(raw)
        except ValueError:
            return _DEFAULT_REPAIR_SHRINK_GUARD_RATIO
        if 0.0 < value <= 1.0:
            return value
    return _DEFAULT_REPAIR_SHRINK_GUARD_RATIO


def _repair_prior_target_size(workspace: str, payload: dict[str, Any]) -> int | None:
    """Byte size of the step target before a repair exec, else None (fail-open).

    Fires only on a repair turn: the payload carries a non-empty ``last_failure``
    AND the declared step ``target_file`` already exists on disk. Returns None for
    first-write / no-failure turns so they are never gated.
    """
    last_failure = payload.get("last_failure")
    if not isinstance(last_failure, dict) or not str(last_failure.get("error_message") or "").strip():
        return None
    target = _step_target_file(payload)
    if not target:
        return None
    try:
        path = os.path.join(workspace, target)
        if os.path.isfile(path):
            return os.path.getsize(path)
    except OSError:
        return None
    return None


def _repair_shrink_error(workspace: str, target: str, prior_size: int) -> str | None:
    """Teaching error if a repair shrank the target below the guard floor, else None."""
    if not target or prior_size < _REPAIR_SHRINK_MIN_PRIOR_BYTES:
        return None
    try:
        path = os.path.join(workspace, target)
        if not os.path.isfile(path):
            return None
        new_size = os.path.getsize(path)
    except OSError:
        return None
    floor = int(prior_size * _repair_shrink_guard_ratio())
    if new_size >= floor:
        return None
    return (
        f"REPAIR SHRANK '{target}': it was {prior_size} bytes of working code and is now "
        f"{new_size} bytes (below the {floor}-byte preservation floor). The file already "
        f"worked except for the named failure — fix ONLY that error in place using edit_blocks "
        f"(a SEARCH/REPLACE block or a line-range edit). Do NOT rewrite the file from scratch "
        f"or drop existing functions."
    )


def _fill_assembly_owned_anchors(payload: dict[str, Any]) -> list[str]:
    """Anchors a ``fill_scope_only`` step is permitted to implement ([] for non-fills)."""
    step = payload.get("construction_step")
    if not isinstance(step, dict) or not step.get("fill_scope_only"):
        return []
    return [str(a).strip() for a in (step.get("anchor_ids") or []) if str(a).strip()]


def _read_target_file_content(workspace: str, target: str) -> str | None:
    if not target:
        return None
    try:
        path = os.path.join(workspace, target)
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                return fh.read()
    except OSError:
        return None
    return None


def _fill_assembly_baseline(workspace: str, payload: dict[str, Any]) -> str | None:
    """The target file content BEFORE an anchored-fill exec (the skeleton / prior-fill
    baseline the deterministic merger validates against), or None when the step is not an
    anchored fill (so non-fill steps are never gated — fail-open)."""
    if not _fill_assembly_owned_anchors(payload):
        return None
    return _read_target_file_content(workspace, _step_target_file(payload))


def _fill_assembly_drift_error(workspace: str, payload: dict[str, Any], baseline: str | None) -> tuple[str, str] | None:
    """P3 deterministic merger gate (codex 2026-06-15): validate a fill's RESULTING file
    against the skeleton baseline. Returns ``(error_code, message)`` on a contract
    violation (interface drift / out-of-region / missing-or-dup anchor) so the caller can
    REQUEUE + re-ask, else None. Fail-open: skips non-fill steps, a missing baseline, or
    an unreadable target — never invents a failure."""
    owned = _fill_assembly_owned_anchors(payload)
    if not owned or baseline is None:
        return None
    proposed = _read_target_file_content(workspace, _step_target_file(payload))
    if proposed is None:
        return None
    from polaris.kernelone.quality.assembly_merger import validate_fill_assembly

    verdict = validate_fill_assembly(baseline, proposed, owned_anchors=owned)
    if verdict.ok:
        return None
    return verdict.error_code, verdict.message


def _read_consumed_interfaces(
    workspace: str, payload: dict[str, Any], step: dict[str, Any]
) -> dict[str, dict[str, Any]] | None:
    """Frozen identifiers of OTHER files this step must reuse, from the interface ledger.

    I3-r28: surfacing the cross-file contract turns it from an unverified CE-prompt
    nudge into a precondition the Director actually sees, so it reuses frozen names
    (e.g. ``gameCanvas``) instead of inventing mismatched ones (e.g. ``game``).
    Fail-open (None): the contract is advisory context, never a turn-stranding hard dep.
    """
    own_target = str(step.get("target_file") or "").strip()
    try:
        from polaris.kernelone.quality.interface_ledger import read_all_declared_interfaces

        declared = read_all_declared_interfaces(
            workspace, str(payload.get("cache_root", "")), exclude_target=own_target
        )
    except (OSError, RuntimeError, ValueError):
        return None
    return declared or None


def _append_normalized_paths(paths: list[str], raw: Any) -> None:
    for value in _normalize_string_list(raw):
        normalized = value.replace("\\", "/")
        if normalized not in paths:
            paths.append(normalized)


def _extract_changed_files_from_mapping(paths: list[str], mapping: dict[str, Any]) -> None:
    for key in (
        "changed_files",
        "affected_files",
        "all_affected_files",
        "new_files",
        "modified_files",
        "files",
    ):
        _append_normalized_paths(paths, mapping.get(key))

    for key in ("file", "path", "target", "relative_path", "target_path"):
        value = mapping.get(key)
        if isinstance(value, (str, os.PathLike)):
            _append_normalized_paths(paths, value)

    effect_raw = mapping.get("effect_receipt")
    if isinstance(effect_raw, dict):
        _extract_changed_files_from_mapping(paths, effect_raw)


def _extract_director_changed_files(adapter_result: dict[str, Any]) -> list[str]:
    changed_files: list[str] = []
    _extract_changed_files_from_mapping(changed_files, adapter_result)

    for key in ("tool_results", "results", "actions"):
        raw_rows = adapter_result.get(key)
        if not isinstance(raw_rows, list):
            continue
        for row in raw_rows:
            if isinstance(row, dict):
                _extract_changed_files_from_mapping(changed_files, row)

    adapter_nested = adapter_result.get("adapter_result")
    if isinstance(adapter_nested, dict):
        _extract_changed_files_from_mapping(changed_files, adapter_nested)

    return changed_files


def _extract_director_side_effects(adapter_result: dict[str, Any]) -> list[dict[str, Any]]:
    raw_side_effects = adapter_result.get("side_effects")
    if not isinstance(raw_side_effects, list):
        return []
    return [dict(row) for row in raw_side_effects if isinstance(row, dict)]


def _compact_director_adapter_summary(adapter_result: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "success": bool(adapter_result.get("success")),
        "task_id": str(adapter_result.get("task_id") or "").strip(),
        "tools_executed": adapter_result.get("tools_executed", 0),
        "materialization_mode": str(adapter_result.get("materialization_mode") or "").strip(),
    }
    existing_contract_evidence = adapter_result.get("existing_contract_evidence")
    if isinstance(existing_contract_evidence, dict):
        summary["existing_contract_evidence"] = {
            "ok": existing_contract_evidence.get("ok") is True,
            "reason": str(existing_contract_evidence.get("reason") or "").strip(),
            "candidate_paths": _normalize_string_list(existing_contract_evidence.get("candidate_paths")),
            "existing_paths": _normalize_string_list(existing_contract_evidence.get("existing_paths")),
            "missing_paths": _normalize_string_list(existing_contract_evidence.get("missing_paths")),
        }
    for key in ("error", "error_code", "failure_stage", "root_cause_hint"):
        value = adapter_result.get(key)
        if value:
            summary[key] = str(value)
    return summary


def _adapter_failure_message(adapter_result: dict[str, Any]) -> str:
    base = ""
    for key in ("error", "error_code", "root_cause_hint", "failure_stage"):
        value = str(adapter_result.get(key) or "").strip()
        if value:
            base = value
            break
    if not base:
        base = "director_adapter_execution_failed"
    # Generic markers like director_materialization_quality_failed teach the
    # next claimant nothing (live I3-r12) — carry the first concrete quality
    # error so the bounce teaching names the actual failing check.
    quality_errors = adapter_result.get("artifact_quality_errors")
    if isinstance(quality_errors, (list, tuple)):
        for entry in quality_errors:
            detail = str(entry or "").strip()
            if detail:
                return f"{base}: {detail[:400]}"
    return base


def _structured_adapter_failure_mappings(
    adapter_result: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    """Return bounded typed mappings reachable through known failure fields.

    The adapter payload is untrusted. Traversal follows only schema-bearing
    mapping/list fields and never inspects display strings or exception prose.
    """

    pending: list[Mapping[str, Any]] = [adapter_result]
    collected: list[Mapping[str, Any]] = []
    seen: set[int] = set()
    while pending and len(collected) < _MAX_STRUCTURED_FAILURE_MAPPINGS:
        current = pending.pop(0)
        identity = id(current)
        if identity in seen:
            continue
        seen.add(identity)
        collected.append(current)

        for key in _STRUCTURED_FAILURE_MAPPING_KEYS:
            nested = current.get(key)
            if isinstance(nested, Mapping):
                pending.append(nested)
        for key in _STRUCTURED_FAILURE_SEQUENCE_KEYS:
            rows = current.get(key)
            if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
                continue
            pending.extend(row for row in rows if isinstance(row, Mapping))
    return tuple(collected)


def _contains_owner_handoff_requests(payload: Mapping[str, Any]) -> bool:
    """Return whether a typed scope payload contains concrete handoff rows."""

    for key in _OWNER_HANDOFF_REQUEST_KEYS:
        rows = payload.get(key)
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
            continue
        if any(isinstance(row, Mapping) for row in rows):
            return True
    return False


def _first_structured_failure_token(
    mappings: Sequence[Mapping[str, Any]],
    key: str,
) -> str:
    for payload in mappings:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _first_failure_evidence_rows(
    mappings: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    for payload in mappings:
        raw_evidence = payload.get("failure_evidence")
        if isinstance(raw_evidence, Mapping):
            return (dict(raw_evidence),)
        if not isinstance(raw_evidence, Sequence) or isinstance(raw_evidence, (str, bytes, bytearray)):
            continue
        rows = tuple(dict(row) for row in raw_evidence if isinstance(row, Mapping))
        if rows:
            return rows
    return ()


def _owner_handoff_failure_from_adapter_failure(
    adapter_result: Mapping[str, Any],
) -> _OwnerHandoffFailure | None:
    """Extract owner-handoff facts from a typed adapter failure payload."""

    if adapter_result.get("success") is True:
        return None
    mappings = _structured_adapter_failure_mappings(adapter_result)
    scope_payload = next(
        (dict(payload) for payload in mappings if _contains_owner_handoff_requests(payload)),
        None,
    )
    if scope_payload is None:
        return None
    return _OwnerHandoffFailure(
        scope_payload=scope_payload,
        failure_class=_first_structured_failure_token(mappings, "failure_class"),
        responsible_layer=_first_structured_failure_token(mappings, "responsible_layer"),
        failure_evidence=_first_failure_evidence_rows(mappings),
    )


def _owner_handoff_failure_metadata(
    failure: _OwnerHandoffFailure,
    *,
    adapter_failure_message: str,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "reason": "scope_authority_owner_handoff",
        "adapter_failure_message": adapter_failure_message,
    }
    if failure.failure_class:
        metadata["failure_class"] = failure.failure_class
    if failure.responsible_layer:
        metadata["responsible_layer"] = failure.responsible_layer
    return metadata


def _owner_handoff_evidence_metadata(
    failure: _OwnerHandoffFailure,
    *,
    handoff_request: Mapping[str, Any] | None,
    routing_summary: Mapping[str, Any],
) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "scope_authority": dict(failure.scope_payload),
        "owner_handoff_routing": dict(routing_summary),
        "failure_evidence": [dict(row) for row in failure.failure_evidence],
    }
    if handoff_request is not None:
        evidence["ownership_handoff_request"] = dict(handoff_request)
    return evidence


def _owner_handoff_failure_projection(
    failure: _OwnerHandoffFailure,
    *,
    adapter_failure_message: str,
    handoff_request: Mapping[str, Any] | None,
    routing_summary: Mapping[str, Any],
    routing_error: BaseException | None = None,
) -> dict[str, Any]:
    """Build auditable failure metadata from typed handoff facts only."""

    metadata = _owner_handoff_failure_metadata(
        failure,
        adapter_failure_message=adapter_failure_message,
    )
    evidence = _owner_handoff_evidence_metadata(
        failure,
        handoff_request=handoff_request,
        routing_summary=routing_summary,
    )
    if routing_error is not None:
        evidence["owner_rework_route_error"] = {
            "type": type(routing_error).__name__,
            "message": str(routing_error),
        }
    metadata["owner_handoff_evidence"] = evidence
    return metadata


def _scan_director_artifact_quality_evidence(
    *,
    workspace_path: Path,
    task_id: str,
    scope: list[str],
) -> tuple[Any | None, str]:
    try:
        from polaris.kernelone.quality import scan_workspace_artifact_quality_evidence

        return (
            scan_workspace_artifact_quality_evidence(
                workspace_path.as_posix(),
                relative_paths=scope or None,
                task_id=task_id,
            ),
            "",
        )
    except (OSError, RuntimeError, ValueError) as exc:
        return None, str(exc)


def _interface_contract_amendment_from_adapter_failure(
    *,
    workspace_path: Path,
    task_id: str,
    payload: dict[str, Any],
    adapter_result: dict[str, Any],
) -> dict[str, Any] | None:
    """Return CE amendment evidence when artifact quality proves design drift."""

    if adapter_result.get("success") is True:
        return None
    scope = _contract_amendment_scan_scope(payload=payload, adapter_result=adapter_result)
    evidence, _scan_error = _scan_director_artifact_quality_evidence(
        workspace_path=workspace_path,
        task_id=task_id,
        scope=scope,
    )
    if evidence is None:
        return None
    if evidence.contract_amendment_request is None:
        return None
    return {
        "amendment_request": evidence.contract_amendment_request.to_dict(),
        "cross_artifact_issues": [issue.to_dict() for issue in evidence.cross_artifact_issues],
        "cross_artifact_repair_plans": [plan.to_dict() for plan in evidence.cross_artifact_repair_plans],
    }


def _interface_contract_repair_from_adapter_failure(
    *,
    workspace_path: Path,
    task_id: str,
    payload: dict[str, Any],
    adapter_result: dict[str, Any],
) -> dict[str, Any] | None:
    """Return Director repair evidence when the contract is valid but implementation is not."""

    if adapter_result.get("success") is True:
        return None
    scope = _contract_amendment_scan_scope(payload=payload, adapter_result=adapter_result)
    evidence, _scan_error = _scan_director_artifact_quality_evidence(
        workspace_path=workspace_path,
        task_id=task_id,
        scope=scope,
    )
    if evidence is None:
        return None
    if evidence.contract_amendment_request is not None:
        return None
    repair_plans = [
        plan.to_dict()
        for plan in evidence.cross_artifact_repair_plans
        if plan.authority == "director_repair_within_contract"
    ]
    if not repair_plans:
        return None
    return {
        "cross_artifact_issues": [issue.to_dict() for issue in evidence.cross_artifact_issues],
        "cross_artifact_repair_plans": repair_plans,
    }


def _final_convergence_scan_scope(
    *,
    workspace_path: Path,
    payload: dict[str, Any],
    changed_files: list[str],
    exec_result: dict[str, Any],
) -> list[str]:
    """Return existing files that represent the final Director output surface."""

    scope: list[str] = []
    scope.extend(_normalize_string_list(changed_files))
    scope.extend(_normalize_string_list(payload.get("target_files")))
    scope.extend(_normalize_string_list(payload.get("scope_paths")))
    step_target = _step_target_file(payload)
    if step_target:
        scope.append(step_target)
    adapter_result = exec_result.get("director_adapter_result")
    if isinstance(adapter_result, dict):
        existing_evidence = adapter_result.get("existing_contract_evidence")
        if isinstance(existing_evidence, dict):
            scope.extend(_normalize_string_list(existing_evidence.get("existing_paths")))

    rows: list[str] = []
    seen: set[str] = set()
    for raw_path in _dedupe_normalized_paths(scope):
        path = raw_path.strip().replace("\\", "/").lstrip("./")
        if not path or path in {".", "/"} or any(part in {"", ".", ".."} for part in Path(path).parts):
            continue
        if path in seen:
            continue
        if (workspace_path / path).is_file():
            seen.add(path)
            rows.append(path)
    return rows


def _final_convergence_failure(
    *,
    workspace_path: Path,
    task_id: str,
    payload: dict[str, Any],
    changed_files: list[str],
    exec_result: dict[str, Any],
) -> tuple[str, str, str | None, dict[str, Any]] | None:
    """Validate final files after all Director writes/fallbacks have settled."""

    scope = _final_convergence_scan_scope(
        workspace_path=workspace_path,
        payload=payload,
        changed_files=changed_files,
        exec_result=exec_result,
    )
    if not scope:
        return None
    evidence, scan_error = _scan_director_artifact_quality_evidence(
        workspace_path=workspace_path,
        task_id=task_id,
        scope=scope,
    )
    if evidence is None:
        return (
            "FINAL_CONVERGENCE_SCAN_FAILED",
            f"Final convergence scan failed before QA handoff: {scan_error}",
            "pending_exec",
            {"scan_scope": scope, "scan_error": scan_error},
        )
    errors = [str(item).strip() for item in evidence.errors if str(item).strip()]
    if not errors and evidence.contract_amendment_request is None:
        return None
    evidence_payload = {
        "schema_version": "director.final_convergence_evidence.v1",
        "scan_scope": scope,
        "changed_files": list(changed_files),
        "errors": errors,
        "cross_artifact_issues": [issue.to_dict() for issue in evidence.cross_artifact_issues],
        "cross_artifact_repair_plans": [plan.to_dict() for plan in evidence.cross_artifact_repair_plans],
        "contract_amendment_request": (
            evidence.contract_amendment_request.to_dict() if evidence.contract_amendment_request is not None else None
        ),
    }
    if evidence.contract_amendment_request is not None:
        evidence_payload["structured_blocker"] = _contract_authority_blocker(
            task_id=task_id,
            error_code="FINAL_CONVERGENCE_CONTRACT_AMENDMENT_REQUIRED",
            evidence=evidence_payload,
            payload=payload,
        )
        return (
            "FINAL_CONVERGENCE_CONTRACT_AMENDMENT_REQUIRED",
            errors[0] if errors else "Final convergence found a completion-contract contradiction",
            None,
            evidence_payload,
        )
    return (
        "FINAL_CONVERGENCE_ARTIFACT_QUALITY_FAILED",
        errors[0] if errors else "Final convergence artifact quality failed before QA handoff",
        "pending_exec",
        evidence_payload,
    )


def _contract_amendment_scan_scope(*, payload: dict[str, Any], adapter_result: dict[str, Any]) -> list[str]:
    scope: list[str] = []
    for key in ("target_files", "scope_paths", "changed_files"):
        scope.extend(_normalize_string_list(payload.get(key)))
    step_target = _step_target_file(payload)
    if step_target:
        scope.append(step_target)
    scope.extend(_normalize_string_list(adapter_result.get("changed_files")))
    return _dedupe_normalized_paths(scope)


@dataclass(frozen=True, slots=True)
class _QaLocalRepairAuthority:
    kind: str
    projection_hash: str
    obligation_id: str = ""
    prior_receipt: ProjectVerificationReceiptV1 | None = None


def _verification_receipt_query_from_command(command: Any) -> QueryProjectVerificationReceiptV1:
    return _package().QueryProjectVerificationReceiptV1(
        workspace=command.workspace,
        project_id=command.project_id,
        run_id=command.run_id,
        completion_contract_hash=command.completion_contract_hash,
        obligation_id=command.obligation_id,
        owner_task_id=command.owner_task_id,
        modality=command.modality,
        argv=command.argv,
        cwd=command.cwd,
        command_authority_hash=command.command_authority_hash,
        input_artifacts=command.input_artifacts,
        timeout_seconds=command.timeout_seconds,
        job_token_id=command.job_token_id,
        job_token_set_hash=command.job_token_set_hash,
        execution_policy_hash=command.execution_policy_hash,
        authority_revision=command.authority_revision,
        policy_profile_id=command.policy_profile_id,
        policy_decision_hash=command.policy_decision_hash,
        executable_path=command.executable_path,
        executable_realpath=command.executable_realpath,
        executable_hash=command.executable_hash,
    )


def _resolve_qa_local_repair_authority(
    *, workspace: str, task_id: str, payload: dict[str, Any]
) -> _QaLocalRepairAuthority | None:
    """Resolve payload locators back to current owner authority before editing."""

    repair_context = payload.get("qa_local_repair_context")
    if not isinstance(repair_context, Mapping):
        return None
    if str(repair_context.get("task_id") or "").strip() != task_id:
        raise ValueError("QA local repair context is owned by a different task")
    projection = payload.get("task_completion_projection")
    if not isinstance(projection, Mapping):
        raise ValueError("QA local repair requires the current task completion projection")
    projection_hash = str(projection.get("projection_hash") or "").strip()
    if (
        not projection_hash
        or projection_hash != str(repair_context.get("task_completion_projection_hash") or "").strip()
    ):
        raise ValueError("QA local repair projection identity changed")
    if (
        str(projection.get("task_id") or "").strip() != task_id
        or str(projection.get("project_contract_hash") or "").strip()
        != str(repair_context.get("project_completion_contract_hash") or "").strip()
    ):
        raise ValueError("QA local repair contract/task identity changed")

    authority_kind = str(repair_context.get("repair_authority_kind") or "").strip()
    repair_policy = repair_context.get("repair_policy")
    if not isinstance(repair_policy, Mapping) or repair_policy.get("same_task_only") is not True:
        raise ValueError("QA local repair policy is missing same-task authority")
    if authority_kind == "diagnostic_effect":
        diagnostic = repair_context.get("diagnostic_effect_authority")
        if (
            not isinstance(diagnostic, Mapping)
            or str(diagnostic.get("diagnostic_kind") or "").strip() != "non_executable_qa_diagnostic"
            or diagnostic.get("executable_verifier_observed") is not False
            or str(diagnostic.get("task_id") or "").strip() != task_id
            or str(diagnostic.get("task_completion_projection_hash") or "").strip() != projection_hash
            or diagnostic.get("requires_material_effect") is not True
        ):
            raise ValueError("QA diagnostic repair lacks current effect authority")
        return _QaLocalRepairAuthority(kind=authority_kind, projection_hash=projection_hash)
    if authority_kind != "exact_verifier_receipt" or repair_policy.get("rerun_exact_failed_verifier") is not True:
        raise ValueError("QA local repair authority kind is unsupported")
    failed = repair_context.get("failed_verifier")
    if not isinstance(failed, Mapping):
        raise ValueError("QA local repair requires an exact failed_verifier receipt")
    prior_receipt_hash = str(failed.get("receipt_hash") or "").strip()
    prior_receipt_ref = str(failed.get("receipt_ref") or "").strip()
    if not prior_receipt_hash or not prior_receipt_ref:
        raise ValueError("QA local repair failed_verifier lacks receipt hash/ref")
    if str(failed.get("owner_task_id") or "").strip() != task_id:
        raise ValueError("QA failed verifier is owned by a different task")
    project_id = str(projection.get("project_id") or "").strip()
    run_id = str(projection.get("run_id") or "").strip()
    completion_contract_hash = str(projection.get("project_contract_hash") or "").strip()
    obligation_id = str(failed.get("obligation_id") or "").strip()
    if (
        str(failed.get("project_id") or "").strip() != project_id
        or str(failed.get("run_id") or "").strip() != run_id
        or str(failed.get("completion_contract_hash") or "").strip() != completion_contract_hash
    ):
        raise ValueError("QA failed verifier locator belongs to another project/run/contract")
    authorities = projection.get("verification_execution_authority")
    rows = [row for row in authorities if isinstance(row, Mapping)] if isinstance(authorities, list) else []
    matches = [row for row in rows if str(row.get("obligation_id") or "").strip() == obligation_id]
    if len(matches) != 1:
        raise ValueError("QA failed verifier obligation is absent or ambiguous in current task projection")
    projected = matches[0]
    command = _package().authorize_project_verification_command(
        ResolveProjectVerificationAuthorityQueryV1(
            workspace=workspace,
            project_id=project_id,
            run_id=run_id,
            completion_contract_hash=completion_contract_hash,
            obligation_id=obligation_id,
        )
    )
    expected_authority = (
        str(failed.get("owner_task_id") or "").strip(),
        str(failed.get("modality") or "").strip(),
        tuple(str(item) for item in list(failed.get("argv") or [])),
        str(failed.get("cwd") or "").strip(),
        str(failed.get("command_authority_hash") or "").strip(),
    )
    actual_authority = (
        command.owner_task_id,
        command.modality,
        command.argv,
        command.cwd,
        command.command_authority_hash,
    )
    if actual_authority != expected_authority:
        raise ValueError("exact verifier authority changed since the QA failure receipt")
    projected_authority = (
        str(projected.get("owner_task_id") or "").strip(),
        str(projected.get("modality") or "").strip(),
        tuple(str(item) for item in list(projected.get("argv") or [])),
        str(projected.get("cwd") or "").strip(),
        str(projected.get("command_authority_hash") or "").strip(),
    )
    if actual_authority != projected_authority:
        raise ValueError("execution broker authority differs from current task projection")
    prior_receipt = _package().query_project_verification_receipt(_verification_receipt_query_from_command(command))
    if not isinstance(prior_receipt, _package().ProjectVerificationReceiptV1):
        raise ValueError("QA failed verifier receipt is not current in execution_broker")
    if prior_receipt.receipt_hash != prior_receipt_hash or prior_receipt.receipt_ref != prior_receipt_ref:
        raise ValueError("QA failed verifier receipt locator does not match owner receipt")
    if str(failed.get("input_artifact_hash") or "").strip() != prior_receipt.input_artifact_hash:
        raise ValueError("QA failed verifier input closure does not match owner receipt")
    if prior_receipt.succeeded:
        raise ValueError("QA local repair cannot use a successful verifier receipt as a failure locator")
    return _QaLocalRepairAuthority(
        kind=authority_kind,
        projection_hash=projection_hash,
        obligation_id=obligation_id,
        prior_receipt=prior_receipt,
    )


def _task_projection_artifact_state(*, workspace: str, payload: dict[str, Any]) -> dict[str, str]:
    projection = payload.get("task_completion_projection")
    if not isinstance(projection, Mapping):
        return {}
    paths: list[str] = []
    for key in ("owned_artifacts", "owned_entrypoints"):
        rows = projection.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, Mapping):
                path = str(row.get("path") or "").strip().replace("\\", "/").lstrip("./")
                if path:
                    paths.append(path)
    root = Path(workspace).expanduser().resolve()
    state: dict[str, str] = {}
    for relative in sorted(set(paths)):
        candidate = (root / relative).resolve()
        if candidate != root and root not in candidate.parents:
            raise ValueError("task projection artifact escapes workspace")
        state[relative] = hashlib.sha256(candidate.read_bytes()).hexdigest() if candidate.is_file() else "missing"
    return state


def _revalidate_qa_exact_verifier(
    *,
    workspace: str,
    task_id: str,
    payload: dict[str, Any],
    authority: _QaLocalRepairAuthority | None = None,
) -> dict[str, Any] | None:
    """Re-run the exact QA-failed verifier after one same-task repair effect."""

    resolved = authority or _resolve_qa_local_repair_authority(workspace=workspace, task_id=task_id, payload=payload)
    if resolved is None or resolved.kind == "diagnostic_effect":
        return None
    prior_receipt = resolved.prior_receipt
    if not isinstance(prior_receipt, _package().ProjectVerificationReceiptV1):
        raise ValueError("QA exact verifier authority lacks owner receipt")
    projection = payload.get("task_completion_projection")
    if not isinstance(projection, Mapping) or str(projection.get("projection_hash") or "") != resolved.projection_hash:
        raise ValueError("QA exact verifier projection changed during repair")
    command = _package().authorize_project_verification_command(
        ResolveProjectVerificationAuthorityQueryV1(
            workspace=workspace,
            project_id=str(projection.get("project_id") or "").strip(),
            run_id=str(projection.get("run_id") or "").strip(),
            completion_contract_hash=str(projection.get("project_contract_hash") or "").strip(),
            obligation_id=resolved.obligation_id,
        )
    )
    if (
        command.project_id,
        command.run_id,
        command.completion_contract_hash,
        command.obligation_id,
        command.owner_task_id,
        command.modality,
        command.argv,
        command.cwd,
        command.command_authority_hash,
    ) != (
        prior_receipt.project_id,
        prior_receipt.run_id,
        prior_receipt.completion_contract_hash,
        prior_receipt.obligation_id,
        prior_receipt.owner_task_id,
        prior_receipt.modality,
        prior_receipt.argv,
        prior_receipt.cwd,
        prior_receipt.command_authority_hash,
    ):
        raise ValueError("exact verifier authority changed during same-task repair")
    result = _package().run_project_verification(command)
    receipt = result.receipt
    if not isinstance(receipt, _package().ProjectVerificationReceiptV1):
        raise RuntimeError(f"exact verifier revalidation produced no receipt: {result.code}")
    return {
        "schema_version": "director.qa-exact-verifier-revalidation.v1",
        "task_id": task_id,
        "obligation_id": receipt.obligation_id,
        "prior_failed_receipt_hash": prior_receipt.receipt_hash,
        "prior_failed_receipt_ref": prior_receipt.receipt_ref,
        "revalidation_receipt_hash": receipt.receipt_hash,
        "revalidation_receipt_ref": receipt.receipt_ref,
        "command_authority_hash": receipt.command_authority_hash,
        "input_artifact_hash": receipt.input_artifact_hash,
        "proof_evidence_hash": receipt.proof_evidence_hash,
        "exit_code": receipt.exit_code,
        "timed_out": receipt.timed_out,
        "succeeded": receipt.succeeded and receipt.input_artifact_hash != prior_receipt.input_artifact_hash,
        "material_effect_observed": receipt.input_artifact_hash != prior_receipt.input_artifact_hash,
    }


def _dedupe_normalized_paths(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for path in paths:
        normalized = str(path or "").strip().replace("\\", "/").lstrip("./")
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique


def _pre_state_punch_list(step: dict[str, Any], *, cwd: str) -> dict[str, Any] | None:
    """施工现状勘察（缺陷清单 / punch list, Fix-13）。

    改建式步骤（目标文件已被前置步骤写出）的施工单若只说"确保有 X"，
    弱执行者读到看似完整的文件会判定"已完成"拒绝动笔——live I3-r13:
    编辑模式 0/5，三次重试全零 diff。领取时跑一次本步 verify，把失败
    子句（含 T2 实测残差）列成清单随施工单下发："缺这几样，补齐"。
    核心逻辑委托 KernelOne 工具链（三个 verify 触点的单一事实源）。
    """
    from polaris.kernelone.quality.step_verify import collect_failing_clauses, normalize_step_verify

    verify = normalize_step_verify(step.get("verify"))
    if not verify:
        return None
    return collect_failing_clauses(verify, cwd=cwd)


def _build_director_adapter_input(task_id: str, payload: dict[str, Any], lease_token: str) -> dict[str, Any]:
    metadata_raw = payload.get("metadata")
    metadata: dict[str, Any] = dict(metadata_raw) if isinstance(metadata_raw, dict) else {}
    source_pm_task_id = str(payload.get("source_pm_task_id") or metadata.get("source_pm_task_id") or "").strip()
    pm_task_id = str(payload.get("pm_task_id") or metadata.get("pm_task_id") or source_pm_task_id or task_id).strip()
    title = str(payload.get("title") or payload.get("subject") or task_id).strip()
    goal = str(payload.get("goal") or metadata.get("goal") or title).strip()

    metadata.update(
        {
            "task_market_task_id": task_id,
            "task_market_lease_token": lease_token,
            "source_pm_task_id": source_pm_task_id or pm_task_id,
            "pm_task_id": pm_task_id,
            "source": "runtime.task_market.pending_exec",
        }
    )

    # Project-wide completion authority is intentionally not prompt material.
    # Director receives one owner-derived task slice and the immutable root
    # hash/ref; physical owners independently re-query the full contract.
    project_wide_fields = {
        "project_completion_contract",
        "project_completion_authority",
        "pm_task_contracts",
    }
    adapter_input = {key: value for key, value in payload.items() if key not in project_wide_fields}
    adapter_input.update(
        {
            "task_id": task_id,
            "pm_task_id": pm_task_id,
            "subject": title,
            "description": str(payload.get("description") or goal).strip(),
            "input": goal,
            "directive": goal,
            "metadata": metadata,
        }
    )
    return adapter_input
