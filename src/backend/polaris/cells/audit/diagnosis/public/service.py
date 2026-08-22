"""Public service exports for `audit.diagnosis` cell."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from polaris.cells.audit.diagnosis.internal.connection_audit_service import (
    write_ws_connection_event,
    write_ws_connection_event_sync,
)
from polaris.cells.audit.diagnosis.internal.diagnosis_engine import AuditDiagnosisEngine
from polaris.cells.audit.diagnosis.internal.exact_run_causal_audit import build_exact_run_causal_report
from polaris.cells.audit.diagnosis.internal.toolkit import (
    build_failure_hops,
    build_triage_bundle,
    run_audit_command,
    to_script_projection,
)
from polaris.cells.audit.diagnosis.internal.toolkit.error_chain import (
    ChainBuilder,
    ErrorChain,
    ErrorChainLink,
    ErrorChainSearcher,
    ErrorMatcher,
    EventLoader,
    _parse_event_datetime,
)
from polaris.cells.audit.diagnosis.internal.toolkit.service import (
    _discover_journal_run_dirs,
    load_journal_events,
    resolve_runtime_root,
)
from polaris.cells.audit.diagnosis.internal.usecases import AuditUseCaseFacade
from polaris.cells.audit.diagnosis.public.contracts import (
    AuditDiagnosisResultV1,
    QueryAuditDiagnosisTrailV1,
    QueryExactRunCausalAuditV1,
)
from polaris.cells.chief_engineer.blueprint.public import (
    project_chief_engineer_delivery_depth_feasibility_from_pm_tasks,
)
from polaris.cells.context.engine.public import QueryFinalProviderRequestAuditV1, query_final_provider_request_audit
from polaris.cells.control_plane.run_ledger.public import ReadRunLedgerProjectionQueryV1, read_run_ledger_projection
from polaris.cells.director.runtime.public import (
    QueryDirectorRepairCoverageV1,
    QueryDirectorRepairPlanProbeV1,
    query_director_repair_coverage,
    query_director_repair_plan_probe,
)
from polaris.cells.factory.pipeline.public import (
    GetFactoryChainProjectionQueryV1,
    GetFactoryTerminalTaskRuntimeProjectionQueryV1,
    get_factory_chain_projection,
    get_factory_terminal_task_runtime_projection,
)
from polaris.kernelone.audit.registry import has_audit_store_factory
from polaris.kernelone.storage import resolve_storage_roots

# Public alias for discover_journal_run_dirs
discover_journal_run_dirs = _discover_journal_run_dirs


_RUN_ID_KEYS = frozenset({"run_id", "factory_run_id", "orchestration_run_id", "source_run_id"})
_FILE_DEFICIT_PATTERN = re.compile(
    r"\b(?P<metric>prod_files|production_source_files|test_files|test_source_files)\s*"
    r"(?:=|:)\s*(?P<actual>\d+)\s*<\s*(?P<required>\d+)\b",
    re.IGNORECASE,
)
_MACHINE_ERROR_PREFIX_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{2,127}$")


def _event_correlated_run_ids(value: object, *, max_depth: int = 12) -> set[str]:
    """Collect exact run identifiers from identifier fields only.

    Role journals often own a role-local top-level ``run_id`` while nested
    event metadata carries the parent ``factory_run_id``. Arbitrary prose is
    deliberately ignored so a message mentioning another run cannot correlate
    unrelated evidence.
    """

    found: set[str] = set()

    def visit(item: object, depth: int) -> None:
        if depth > max_depth:
            return
        if isinstance(item, Mapping):
            for key, nested in item.items():
                if str(key) in _RUN_ID_KEYS:
                    token = str(nested or "").strip()
                    if token:
                        found.add(token)
                elif isinstance(nested, (Mapping, list, tuple)):
                    visit(nested, depth + 1)
            return
        if isinstance(item, Sequence) and not isinstance(item, (str, bytes)):
            for nested in item:
                visit(nested, depth + 1)

    visit(value, 0)
    return found


def _event_matches_run_id(payload: Mapping[str, Any], run_id: str) -> bool:
    return str(run_id or "").strip() in _event_correlated_run_ids(payload)


def _offline_journal_events(runtime_root: Path, *, limit: int) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for run_dir in _discover_journal_run_dirs(runtime_root):
        events.extend(load_journal_events(run_dir, limit=limit))
    events.sort(key=lambda event: (float(event.get("ts_epoch") or 0.0), str(event.get("ts") or "")))
    return events[-limit:]


def _diagnosis_event_identity(event: Mapping[str, Any]) -> tuple[str, str]:
    """Return a stable identity without assuming every evidence source has an event id."""

    event_id = str(event.get("event_id") or "").strip()
    if event_id:
        return ("event_id", event_id)
    return (
        "payload",
        json.dumps(dict(event), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str),
    )


def _merge_diagnosis_events(
    *sources: Sequence[Mapping[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    """Merge AuditStore and physical journals without letting either hide evidence."""

    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for source in sources:
        for event in source:
            payload = dict(event)
            merged.setdefault(_diagnosis_event_identity(payload), payload)
    events = list(merged.values())
    events.sort(key=lambda event: (float(event.get("ts_epoch") or 0.0), str(event.get("ts") or "")))
    return events[-limit:]


def _has_audit_artifacts(runtime_root: Path) -> bool:
    audit_root = runtime_root / "audit"
    if not audit_root.exists():
        return False
    try:
        return any(audit_root.rglob("*"))
    except OSError:
        return True


def _has_journal_artifacts(runtime_root: Path) -> bool:
    return any((run_dir / "logs").is_dir() for run_dir in _discover_journal_run_dirs(runtime_root))


def _chief_engineer_authority_feasibility(
    *,
    workspace: str,
    factory_run_id: str,
) -> dict[str, Any]:
    """Revalidate persisted PM minimums against immutable CE artifacts."""

    workspace_path = Path(workspace)
    plan_path = workspace_path / ".polaris" / "plans" / f"{factory_run_id}.plan.json"
    portfolio_root = workspace_path / ".polaris" / "runtime" / "blueprints"
    if not plan_path.is_file() or not portfolio_root.is_dir():
        return {"available": False, "reason": "pm_plan_or_ce_portfolio_missing"}
    try:
        plan_payload = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return {"available": False, "reason": f"pm_plan_unreadable:{type(exc).__name__}"}
    raw_tasks = plan_payload.get("tasks") if isinstance(plan_payload, Mapping) else None
    if not isinstance(raw_tasks, list) or not all(isinstance(item, Mapping) for item in raw_tasks):
        return {"available": False, "reason": "pm_plan_tasks_invalid"}

    portfolio_path: Path | None = None
    portfolio_payload: Mapping[str, Any] | None = None
    for candidate in sorted(portfolio_root.glob("ce_portfolio_*.json"), reverse=True):
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if isinstance(payload, Mapping) and str(payload.get("run_id") or "") == factory_run_id:
            portfolio_path = candidate
            portfolio_payload = payload
            break
    if portfolio_path is None or portfolio_payload is None:
        return {"available": False, "reason": "exact_run_ce_portfolio_missing"}
    try:
        result = project_chief_engineer_delivery_depth_feasibility_from_pm_tasks(
            portfolio_payload,
            pm_tasks=[dict(item) for item in raw_tasks],
        )
    except (TypeError, ValueError) as exc:
        return {"available": False, "reason": f"ce_authority_projection_invalid:{exc}"}
    return {
        "available": True,
        **result,
        "plan_path": str(plan_path),
        "portfolio_path": str(portfolio_path),
    }


def _empty_diagnosis_trail(
    query: QueryAuditDiagnosisTrailV1,
    *,
    runtime_root: Path,
    reason: str,
) -> AuditDiagnosisResultV1:
    return AuditDiagnosisResultV1(
        ok=True,
        status="empty",
        workspace=query.workspace,
        payload={
            "runtime_root": str(runtime_root),
            "run_id": query.run_id or "",
            "task_id": query.task_id or "",
            "limit": query.limit,
            "total": 0,
            "events": [],
            "empty_reason": reason,
        },
    )


def query_audit_diagnosis_trail(query: QueryAuditDiagnosisTrailV1) -> AuditDiagnosisResultV1:
    """Read audit diagnosis trail evidence through the public Cell boundary."""

    runtime_root = resolve_runtime_root(workspace=query.workspace)
    if runtime_root is None:
        return AuditDiagnosisResultV1(
            ok=False,
            status="unavailable",
            workspace=query.workspace,
            payload={"events": [], "total": 0},
            error_code="runtime_root_unavailable",
            error_message="Unable to resolve runtime root for audit diagnosis trail.",
        )

    try:
        if not runtime_root.exists():
            return _empty_diagnosis_trail(query, runtime_root=runtime_root, reason="runtime_root_missing")
        has_artifacts = _has_audit_artifacts(runtime_root)
        if not has_audit_store_factory() and not has_artifacts and not _has_journal_artifacts(runtime_root):
            return _empty_diagnosis_trail(
                query,
                runtime_root=runtime_root,
                reason="audit_store_factory_unregistered_without_artifacts",
            )
        store_events: list[dict[str, Any]] = []
        if has_audit_store_factory():
            facade = AuditUseCaseFacade(runtime_root=runtime_root)
            events = facade.query_logs(
                task_id=query.task_id,
                limit=query.limit,
            )
            store_events = [event.to_dict() for event in events]
        journal_events = _offline_journal_events(runtime_root, limit=query.limit)
        payload_events = _merge_diagnosis_events(store_events, journal_events, limit=query.limit)
        if query.task_id:
            payload_events = [
                event
                for event in payload_events
                if str(_mapping_value(event, "task_id") or "").strip() == query.task_id
            ]
        if query.run_id:
            payload_events = [event for event in payload_events if _event_matches_run_id(event, query.run_id)]
        return AuditDiagnosisResultV1(
            ok=True,
            status="available" if payload_events else "empty",
            workspace=query.workspace,
            payload={
                "runtime_root": str(runtime_root),
                "run_id": query.run_id or "",
                "task_id": query.task_id or "",
                "limit": query.limit,
                "total": len(payload_events),
                "events": payload_events,
            },
        )
    except (RuntimeError, ValueError, OSError) as exc:
        return AuditDiagnosisResultV1(
            ok=False,
            status="unavailable",
            workspace=query.workspace,
            payload={"events": [], "total": 0, "runtime_root": str(runtime_root)},
            error_code="audit_diagnosis_query_failed",
            error_message=str(exc),
        )


def _context_snapshot_refs(value: object, *, limit: int = 16) -> list[str]:
    """Keep recent refs without starving earlier role stages.

    Long Director runs can emit dozens of snapshots after PM/CE. A plain tail
    would erase upstream evidence and make exact-run attribution guess. Keep
    the latest snapshot for every role first, then fill remaining capacity from
    the global recent tail.
    """

    refs: list[str] = []
    refs_by_role: dict[str, list[str]] = {}

    def collect(item: object, target: list[str]) -> None:
        if len(refs) >= 512:
            return
        if isinstance(item, dict):
            for key, nested in item.items():
                if key == "context_snapshot_ref":
                    ref = str(nested or "").strip().lower()
                    if len(ref) == 24 and all(char in "0123456789abcdef" for char in ref):
                        if ref not in target:
                            target.append(ref)
                        if ref not in refs:
                            refs.append(ref)
                else:
                    collect(nested, target)
            return
        if isinstance(item, list):
            for nested in item:
                collect(nested, target)

    events = list(value) if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else [value]
    for event in events:
        event_refs: list[str] = []
        collect(event, event_refs)
        role = (
            str(
                _mapping_value(event, "role")
                or _mapping_value(event, "agent_role")
                or _mapping_value(event, "stage")
                or ""
            )
            .strip()
            .casefold()
        )
        if "chief" in role or role in {"ce", "chief_engineer_review"}:
            role = "chief_engineer"
        elif "director" in role:
            role = "director"
        elif role in {"pm", "project_manager", "pm_planning"} or "project manager" in role:
            role = "pm"
        elif "qa" in role or "quality" in role:
            role = "qa"
        if role and event_refs:
            bucket = refs_by_role.setdefault(role, [])
            for ref in event_refs:
                if ref not in bucket:
                    bucket.append(ref)

    selected: list[str] = []
    for role in ("pm", "chief_engineer", "director", "qa"):
        bucket = refs_by_role.get(role) or []
        if bucket and bucket[-1] not in selected:
            selected.append(bucket[-1])
    for ref in reversed(refs):
        if ref not in selected:
            selected.append(ref)
        if len(selected) >= limit:
            break
    return selected[:limit]


def _mapping_value(value: object, key: str, *, max_depth: int = 10) -> object | None:
    """Return first nested value for ``key`` from structured evidence only."""

    def visit(item: object, depth: int) -> object | None:
        if depth > max_depth:
            return None
        if isinstance(item, Mapping):
            if key in item:
                return item.get(key)
            for nested in item.values():
                if isinstance(nested, (Mapping, list, tuple)):
                    found = visit(nested, depth + 1)
                    if found is not None:
                        return found
        elif isinstance(item, Sequence) and not isinstance(item, (str, bytes)):
            for nested in item:
                found = visit(nested, depth + 1)
                if found is not None:
                    return found
        return None

    return visit(value, 0)


def _structured_failure_signals(events: Sequence[Mapping[str, Any]], *, limit: int = 64) -> list[dict[str, str]]:
    """Project typed failure signals without parsing human prose."""

    latest_by_identity: dict[tuple[str, str, str, str], dict[str, str]] = {}
    for event in events:
        error_message = str(_mapping_value(event, "error_message") or "").strip()
        error_category = str(_mapping_value(event, "error_category") or "").strip()
        message_prefix = error_message.partition(":")[0].strip().casefold()
        machine_error_code = (
            message_prefix if error_message and _MACHINE_ERROR_PREFIX_PATTERN.fullmatch(message_prefix) else ""
        )
        error_code = str(_mapping_value(event, "error_code") or _mapping_value(event, "failure_class") or "").strip()
        event_kind = str(event.get("event") or event.get("type") or event.get("kind") or "").strip()
        if machine_error_code and error_code.casefold() in {
            "",
            "error",
            "failed",
            "failure",
            "none",
            "null",
            "ok",
            "output_validation_failed",
            "success",
        }:
            error_code = machine_error_code
        if error_code.casefold() in {"", "none", "null", "ok", "success"}:
            if machine_error_code:
                error_code = machine_error_code
            normalized_kind = event_kind.casefold()
            if not error_code and not any(
                token in normalized_kind for token in ("error", "fail", "timeout", "blocked")
            ):
                continue
            if not error_code:
                error_code = event_kind
        role = str(_mapping_value(event, "role") or _mapping_value(event, "agent_role") or "").strip()
        stage = str(_mapping_value(event, "stage") or _mapping_value(event, "failure_stage") or "").strip()
        task_id = str(_mapping_value(event, "task_id") or _mapping_value(event, "external_task_id") or "").strip()
        context_refs = _context_snapshot_refs([event], limit=2)
        context_ref = context_refs[-1] if context_refs else ""
        identity = (error_code, role, stage, task_id)
        signal = {
            "error_code": error_code,
            "event_kind": event_kind,
            "role": role,
            "stage": stage,
            "task_id": task_id,
            "context_snapshot_ref": context_ref,
            "timestamp": str(event.get("ts") or event.get("timestamp") or "").strip(),
        }
        if error_category:
            signal["error_category"] = error_category
        if error_message:
            signal["error_message"] = error_message
        latest_by_identity[identity] = signal
    signals = list(latest_by_identity.values())
    signals.sort(key=lambda item: item.get("timestamp", ""))
    return signals[-limit:]


def _message_texts(messages: object) -> list[str]:
    texts: list[str] = []

    def visit(value: object) -> None:
        if isinstance(value, str):
            texts.append(value)
        elif isinstance(value, Mapping):
            for key, nested in value.items():
                if key in {"content", "text"}:
                    visit(nested)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            for nested in value:
                visit(nested)

    visit(messages)
    return texts


def _file_deficits_from_final_request(messages: object) -> list[dict[str, object]]:
    """Extract fixed delivery-depth metric signatures from final request input.

    This backward-compatible extractor accepts only numeric gate signatures;
    it does not use model response prose or inspect generated project files.
    New requests should also project these values in structured audit metadata.
    """

    deficits: list[dict[str, object]] = []
    seen: set[tuple[str, int, int]] = set()
    for text in _message_texts(messages):
        for match in _FILE_DEFICIT_PATTERN.finditer(text):
            metric = match.group("metric").lower()
            actual = int(match.group("actual"))
            required = int(match.group("required"))
            key = (metric, actual, required)
            if key in seen:
                continue
            seen.add(key)
            deficits.append(
                {
                    "metric": metric,
                    "actual": actual,
                    "required": required,
                    "source": "final_provider_request.messages",
                }
            )
    return deficits


def _file_deficits_from_request_metadata(request_metadata: Mapping[str, Any]) -> list[dict[str, object]]:
    failed_gate = request_metadata.get("failed_gate_evidence_summary")
    failed_gate_map = failed_gate if isinstance(failed_gate, Mapping) else {}
    metrics_raw = failed_gate_map.get("quality_metrics")
    minimums_raw = failed_gate_map.get("quality_minimums")
    metrics = metrics_raw if isinstance(metrics_raw, Mapping) else {}
    minimums = dict(minimums_raw) if isinstance(minimums_raw, Mapping) else {}
    depth_summary = request_metadata.get("delivery_depth_contract_summary")
    depth_summary_map = depth_summary if isinstance(depth_summary, Mapping) else {}
    depth_minimums = depth_summary_map.get("minimums")
    if isinstance(depth_minimums, Mapping):
        for key, value in depth_minimums.items():
            minimums.setdefault(str(key), value)
    deficits: list[dict[str, object]] = []
    for metric in ("prod_files", "test_files"):
        actual = metrics.get(metric)
        required = minimums.get(f"min_{metric}")
        if (
            isinstance(actual, int)
            and not isinstance(actual, bool)
            and isinstance(required, int)
            and not isinstance(required, bool)
            and actual < required
        ):
            deficits.append(
                {
                    "metric": metric,
                    "actual": actual,
                    "required": required,
                    "source": "final_request_context_audit.request_metadata_summary",
                }
            )
    return deficits


def _provider_audit_projection(result: Any) -> dict[str, object]:
    payload = result.payload if isinstance(getattr(result, "payload", None), dict) else {}
    final_audit = payload.get("final_request_context_audit")
    final_audit_map = final_audit if isinstance(final_audit, dict) else {}
    evidence_coverage = final_audit_map.get("final_request_evidence_coverage")
    evidence_coverage_map = evidence_coverage if isinstance(evidence_coverage, dict) else {}
    request_metadata = final_audit_map.get("request_metadata_summary")
    request_metadata_map = request_metadata if isinstance(request_metadata, dict) else {}
    file_deficits = _file_deficits_from_request_metadata(request_metadata_map)
    if not file_deficits:
        file_deficits = _file_deficits_from_final_request(payload.get("messages"))
    raw_tools = payload.get("tools")
    tool_names: list[str] = []
    if isinstance(raw_tools, list):
        for raw_tool in raw_tools:
            if not isinstance(raw_tool, dict):
                continue
            function = raw_tool.get("function")
            function_map = function if isinstance(function, dict) else {}
            name = str(raw_tool.get("name") or function_map.get("name") or "").strip()
            if name and name not in tool_names:
                tool_names.append(name)
    return {
        "ok": result.ok,
        "status": result.status,
        "context_snapshot_ref": result.context_snapshot_ref,
        "error_code": result.error_code or "",
        "role": str(payload.get("role") or ""),
        "tool_names": tool_names,
        "tool_choice": payload.get("tool_choice"),
        "final_request_token_estimate": int(final_audit_map.get("final_request_token_estimate") or 0),
        "context_window_tokens": int(final_audit_map.get("context_window_tokens") or 0),
        "context_window_utilization": final_audit_map.get("context_window_utilization"),
        "evidence_coverage_pass": evidence_coverage_map.get("pass"),
        "role_identity_ok": evidence_coverage_map.get("role_identity_ok"),
        "required_refs": list(evidence_coverage_map.get("required_refs") or []),
        "included_refs": list(evidence_coverage_map.get("included_refs") or []),
        "missing_required_refs": list(evidence_coverage_map.get("missing_required_refs") or []),
        "required_tools": list(evidence_coverage_map.get("required_tools") or []),
        "missing_required_tools": list(evidence_coverage_map.get("missing_required_tools") or []),
        "file_deficits": file_deficits,
        "execution_profile_summary": request_metadata_map.get("execution_profile_summary") or {},
        "execution_contract_summary": request_metadata_map.get("execution_contract_summary") or {},
        "delivery_depth_contract_summary": request_metadata_map.get("delivery_depth_contract_summary") or {},
        "target_scope_summary": request_metadata_map.get("target_scope_summary") or {},
        "failed_gate_evidence_summary": request_metadata_map.get("failed_gate_evidence_summary") or {},
        "workspace_quality_evidence_summary": request_metadata_map.get("workspace_quality_evidence_summary") or {},
    }


def _repair_evidence_from_ledger(ledger_projection: Mapping[str, Any]) -> dict[str, Any]:
    """Return the latest bounded workspace-repair evidence from Run Ledger.

    Factory persists the full authority under ``runtime/qa`` and places a
    bounded, hash-addressed projection on the effective quality gate.  Reading
    the latest effective gate keeps historical failed attempts visible without
    allowing them to override a newer revision.
    """

    run_projection = ledger_projection.get("run_projection")
    run_projection_map = run_projection if isinstance(run_projection, Mapping) else {}
    effective_gates = run_projection_map.get("effective_gates")
    if not isinstance(effective_gates, (list, tuple)):
        return {}
    for raw_gate in reversed(effective_gates):
        if not isinstance(raw_gate, Mapping):
            continue
        repair = raw_gate.get("repair_result")
        if isinstance(repair, Mapping) and repair:
            return {**dict(repair), "evidence_source": "run_ledger.effective_gate.repair_result"}
    return {}


def _repair_evidence_from_workspace(workspace: str) -> dict[str, Any]:
    """Read the full workspace-quality artifact through canonical storage roots.

    This is read-only diagnosis.  It never edits the generated project and it
    refuses oversized or malformed artifacts instead of guessing from logs.
    """

    try:
        runtime_root = Path(resolve_storage_roots(workspace).runtime_root).expanduser().resolve()
        evidence_path = (runtime_root / "qa" / "workspace-validation.json").resolve()
        if not evidence_path.is_relative_to(runtime_root) or not evidence_path.is_file():
            return {}
        if evidence_path.stat().st_size > 8_000_000:
            return {}
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
        repair = payload.get("repair") if isinstance(payload, Mapping) else None
        if not isinstance(repair, Mapping) or not repair:
            return {}
        return {
            **dict(repair),
            "evidence_source": "runtime.qa.workspace_validation",
            "full_evidence_ref": str(evidence_path),
        }
    except (OSError, UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError):
        return {}


def _repair_base_files_from_coverage(
    *,
    workspace: str,
    coverage: Mapping[str, Any],
) -> dict[str, str]:
    """Read only diagnostic-owned files needed by the read-only plan probe."""

    workspace_root = Path(workspace).expanduser().resolve()
    base_files: dict[str, str] = {}
    raw_items = coverage.get("items")
    if not isinstance(raw_items, (list, tuple)):
        return base_files
    for raw_item in raw_items:
        if not isinstance(raw_item, Mapping):
            continue
        diagnostic = raw_item.get("diagnostic")
        diagnostic_map = diagnostic if isinstance(diagnostic, Mapping) else {}
        raw_path = str(diagnostic_map.get("path") or "").strip().replace("\\", "/")
        if not raw_path or raw_path in base_files:
            continue
        candidate = (workspace_root / raw_path).resolve()
        try:
            if not candidate.is_relative_to(workspace_root) or not candidate.is_file():
                continue
            if candidate.stat().st_size > 256_000:
                continue
            base_files[raw_path] = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        if len(base_files) >= 64:
            break
    return base_files


def _enrich_repair_evidence(*, workspace: str, evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Attach read-only coverage and concrete plan proof to verifier evidence."""

    enriched = dict(evidence)
    raw_errors = enriched.get("residual_errors") or enriched.get("artifact_quality_errors") or ()
    errors = tuple(str(item).strip() for item in raw_errors if str(item or "").strip())
    if not errors:
        return enriched
    coverage_raw = enriched.get("director_runtime_repair_coverage")
    coverage = dict(coverage_raw) if isinstance(coverage_raw, Mapping) else {}
    try:
        if not coverage:
            coverage = query_director_repair_coverage(
                QueryDirectorRepairCoverageV1(artifact_quality_errors=errors)
            ).to_dict()
            enriched["director_runtime_repair_coverage"] = coverage
        probe_raw = enriched.get("plan_probe_preaudit")
        if not isinstance(probe_raw, Mapping) or not probe_raw:
            base_files = _repair_base_files_from_coverage(workspace=workspace, coverage=coverage)
            enriched["plan_probe_preaudit"] = query_director_repair_plan_probe(
                QueryDirectorRepairPlanProbeV1(
                    artifact_quality_errors=errors,
                    base_files=base_files,
                    metadata={
                        "source": "audit.diagnosis.exact_run",
                        "read_only": True,
                    },
                )
            ).to_dict()
    except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
        enriched["audit_repair_probe_error"] = {
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    return enriched


async def query_exact_run_causal_audit(query: QueryExactRunCausalAuditV1) -> AuditDiagnosisResultV1:
    """Diagnose one exact Factory run from authoritative public projections."""

    try:
        factory_projection = await get_factory_chain_projection(
            GetFactoryChainProjectionQueryV1(workspace=query.workspace, run_id=query.factory_run_id)
        )
        if query.preloaded_run_ledger_projection is not None:
            ledger_projection = dict(query.preloaded_run_ledger_projection)
        else:
            ledger_projection = read_run_ledger_projection(
                ReadRunLedgerProjectionQueryV1(
                    workspace=query.workspace,
                    run_id=query.factory_run_id,
                    factory_run_id=query.factory_run_id,
                    project_id=query.project_id,
                )
            ).projection
        terminal_projection = get_factory_terminal_task_runtime_projection(
            GetFactoryTerminalTaskRuntimeProjectionQueryV1(
                workspace=query.workspace,
                factory_run_id=query.factory_run_id,
            )
        )
        trail = query_audit_diagnosis_trail(
            QueryAuditDiagnosisTrailV1(
                workspace=query.workspace,
                run_id=query.factory_run_id,
                limit=query.audit_event_limit,
            )
        )
        trail_events = list(trail.payload.get("events") or []) if trail.ok else []
        provider_audits = [
            query_final_provider_request_audit(
                QueryFinalProviderRequestAuditV1(
                    workspace=query.workspace,
                    context_snapshot_ref=context_ref,
                )
            )
            for context_ref in _context_snapshot_refs(trail_events)
        ]
        repair_evidence = (
            dict(query.preloaded_repair_evidence)
            if query.preloaded_repair_evidence is not None
            else _repair_evidence_from_workspace(query.workspace) or _repair_evidence_from_ledger(ledger_projection)
        )
        if repair_evidence:
            repair_evidence = _enrich_repair_evidence(
                workspace=query.workspace,
                evidence=repair_evidence,
            )
        report = build_exact_run_causal_report(
            workspace=query.workspace,
            factory_run_id=query.factory_run_id,
            project_id=query.project_id,
            factory_projection=factory_projection.to_dict(),
            ledger_projection=ledger_projection,
            terminal_task_runtime_projection=terminal_projection.to_dict() if terminal_projection else None,
            provider_request_audits=[_provider_audit_projection(result) for result in provider_audits],
            chief_engineer_authority_feasibility=_chief_engineer_authority_feasibility(
                workspace=query.workspace,
                factory_run_id=query.factory_run_id,
            ),
            structured_failure_signals=_structured_failure_signals(trail_events),
            audit_trail_total=int(trail.payload.get("total") or 0) if trail.ok else 0,
            repair_evidence=repair_evidence,
        )
        return AuditDiagnosisResultV1(
            ok=not bool(report["root_cause_code"]),
            status=str(report["current_status"]).lower(),
            workspace=query.workspace,
            payload=report,
            error_code=str(report["root_cause_code"]) or None,
            error_message=(
                f"Exact run blocked by {report['root_cause_code']} in {report['responsible_cell']}"
                if report["root_cause_code"]
                else None
            ),
        )
    except (RuntimeError, ValueError, OSError) as exc:
        return AuditDiagnosisResultV1(
            ok=False,
            status="unavailable",
            workspace=query.workspace,
            payload={"factory_run_id": query.factory_run_id},
            error_code="exact_run_causal_audit_failed",
            error_message=str(exc),
        )


__all__ = [
    "AuditDiagnosisEngine",
    "AuditUseCaseFacade",
    "ChainBuilder",
    "ErrorChain",
    "ErrorChainLink",
    "ErrorChainSearcher",
    "ErrorMatcher",
    "EventLoader",
    "_parse_event_datetime",
    "build_failure_hops",
    "build_triage_bundle",
    "discover_journal_run_dirs",
    "load_journal_events",
    "query_audit_diagnosis_trail",
    "query_exact_run_causal_audit",
    "resolve_runtime_root",
    "run_audit_command",
    "to_script_projection",
    "write_ws_connection_event",
    "write_ws_connection_event_sync",
]
