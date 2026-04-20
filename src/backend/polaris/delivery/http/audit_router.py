"""Audit API router for Phase 1.2.

Provides endpoints for:
- GET /v2/audit/logs - Query audit events
- GET /v2/audit/export - Export audit data (JSON/CSV)
- GET /v2/audit/verify - Verify chain integrity
- GET /v2/audit/stats - Get audit statistics
- POST /v2/audit/cleanup - Clean up old logs
- POST /v2/audit/triage - Generate triage bundle
- GET /v2/audit/failures/{run_id}/hops - Get failure hops
- GET /v2/audit/corruption - Get corruption log
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from polaris.bootstrap.config import Settings, get_settings
from fastapi import APIRouter, Depends, HTTPException, Path as FastApiPath, Query
from fastapi.responses import Response
from polaris.cells.audit.diagnosis.public.service import (
    AuditDiagnosisEngine,
    AuditUseCaseFacade,
    build_failure_hops,
    build_triage_bundle,
)
from polaris.delivery.http.dependencies import require_auth
from polaris.delivery.http.schemas import (
    AuditCleanupParams,
    AuditCleanupResponse,
    AuditLogsResponse,
    AuditStatsResponse,
    AuditTraceResponse,
    AuditTriageRequest,
    AuditTriageResponse,
    AuditVerifyResponse,
    CodeRegionRequest,
    CodeRegionResponse,
    FailureAnalysisRequest,
    FailureAnalysisResponse,
    FailureHopsResponse,
    ProjectScanRequest,
    ProjectScanResponse,
)
from polaris.kernelone.audit import KernelAuditEventType, validate_run_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/audit", tags=["Audit v2"])


def get_runtime_root(settings: Settings = Depends(get_settings)) -> Path:
    """Get runtime root path from settings.

    Note: In future, this should resolve from workspace parameter.
    """
    return Path(settings.runtime_base)


def get_audit_facade(runtime_root: Path = Depends(get_runtime_root)) -> AuditUseCaseFacade:
    """Get audit use case facade for the resolved runtime root."""
    return AuditUseCaseFacade(runtime_root=runtime_root)


def get_diagnosis_engine(
    runtime_root: Path = Depends(get_runtime_root),
    settings: Settings = Depends(get_settings),
) -> AuditDiagnosisEngine:
    """Build diagnosis/scanning engine with runtime+workspace context."""
    workspace = str(getattr(settings, "workspace", "") or ".")
    return AuditDiagnosisEngine(runtime_root=runtime_root, workspace=workspace)


def parse_datetime(dt_str: str | None) -> datetime | None:
    """Parse ISO8601 datetime string."""
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid datetime format: {dt_str}. Use ISO8601 format.")


def parse_line_range(value: str | None) -> tuple[int, int] | None:
    token = str(value or "").strip()
    if not token:
        return None
    matched = re.fullmatch(r"(\d+)\s*-\s*(\d+)", token)
    if not matched:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid line range: {value}. Use format like 10-50.",
        )
    start = int(matched.group(1))
    end = int(matched.group(2))
    if start <= 0 or end <= 0 or end < start:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid line range bounds: {value}.",
        )
    return (start, end)


def _resolve_failure_hops_events_path(runtime_root: Path) -> Path | None:
    candidates = (
        runtime_root / "events" / "runtime.events.jsonl",
        runtime_root / "events" / "events.jsonl",
        runtime_root / "events.jsonl",
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


@router.get("/logs", response_model=AuditLogsResponse, dependencies=[Depends(require_auth)])
async def get_audit_logs(
    start_time: str | None = Query(None, description="Start time (ISO8601)"),
    end_time: str | None = Query(None, description="End time (ISO8601)"),
    event_type: str | None = Query(None, description="Event type filter"),
    role: str | None = Query(None, description="Role filter"),
    task_id: str | None = Query(None, description="Task ID filter"),
    limit: int = Query(100, ge=1, le=1000, description="Max events to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    facade: AuditUseCaseFacade = Depends(get_audit_facade),
) -> AuditLogsResponse:
    """Query audit events with filters.

    Returns paginated audit events matching the specified filters.
    """
    start_dt = parse_datetime(start_time)
    end_dt = parse_datetime(end_time)

    event_type_enum: KernelAuditEventType | None = None
    if event_type:
        try:
            event_type_enum = KernelAuditEventType(event_type)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=(f"Invalid event_type: {event_type}. Valid values: {[e.value for e in KernelAuditEventType]}"),
            )

    # Get total count for pagination
    all_events = facade.query_logs(
        start_time=start_dt,
        end_time=end_dt,
        event_type=event_type_enum,
        role=role,
        task_id=task_id,
        limit=10000,  # Get enough to count
    )
    total_count = len(all_events)

    events = facade.query_logs(
        start_time=start_dt,
        end_time=end_dt,
        event_type=event_type_enum,
        role=role,
        task_id=task_id,
        limit=limit,
        offset=offset,
    )

    return AuditLogsResponse(
        events=[e.to_dict() for e in events],
        pagination={
            "total": total_count,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(events) < total_count,
        },
    )


@router.get("/export", dependencies=[Depends(require_auth)])
async def export_audit(
    format: str = Query("json", description="Export format: json, csv"),
    start_time: str | None = Query(None, description="Start time (ISO8601)"),
    end_time: str | None = Query(None, description="End time (ISO8601)"),
    event_types: str | None = Query(None, description="Comma-separated event types"),
    include_data: bool = Query(True, description="Include full data payload"),
    facade: AuditUseCaseFacade = Depends(get_audit_facade),
) -> Response:
    """Export audit logs in specified format.

    Supports JSON and CSV export with time range filtering.
    """
    start_dt = parse_datetime(start_time)
    end_dt = parse_datetime(end_time)

    event_type_list: list[KernelAuditEventType] | None = None
    if event_types:
        try:
            event_type_list = [KernelAuditEventType(et.strip()) for et in event_types.split(",")]
        except ValueError as e:
            logger.error("export_audit: invalid event_type: %s", e)
            raise HTTPException(
                status_code=400,
                detail="internal error",
            )

    if format == "csv":
        csv_content = facade.export_csv(
            start_time=start_dt,
            end_time=end_dt,
        )
        return Response(
            content=csv_content,
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=audit-export.csv"},
        )

    # JSON export (default)
    export_data = facade.export_json(
        start_time=start_dt,
        end_time=end_dt,
        event_types=event_type_list,  # type: ignore[arg-type]
        include_data=include_data,
    )

    json_content = json.dumps(export_data, ensure_ascii=False, indent=2)
    return Response(
        content=json_content,
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=audit-export.json"},
    )


@router.get("/verify", response_model=AuditVerifyResponse, dependencies=[Depends(require_auth)])
async def verify_audit_chain(
    facade: AuditUseCaseFacade = Depends(get_audit_facade),
) -> AuditVerifyResponse:
    """Verify integrity of audit log chain.

    Validates HMAC-SHA256 signatures and hash chain continuity.
    """
    result = facade.verify_chain()

    return AuditVerifyResponse(
        chain_valid=bool(result.get("chain_valid")),
        first_event_hash=str(result.get("first_event_hash") or ""),
        last_event_hash=str(result.get("last_event_hash") or ""),
        total_events=int(result.get("total_events") or 0),
        gap_count=int(result.get("gap_count") or 0),
        verified_at=str(result.get("verified_at") or datetime.now(timezone.utc).isoformat()),
        invalid_events=list(result.get("invalid_events") or []),
    )


@router.get("/stats", response_model=AuditStatsResponse, dependencies=[Depends(require_auth)])
async def get_audit_stats(
    start_time: str | None = Query(None, description="Start time (ISO8601)"),
    end_time: str | None = Query(None, description="End time (ISO8601)"),
    facade: AuditUseCaseFacade = Depends(get_audit_facade),
) -> AuditStatsResponse:
    """Get audit statistics for specified time range.

    Returns aggregated statistics including event counts by type, role, and result.
    """
    start_dt = parse_datetime(start_time)
    end_dt = parse_datetime(end_time)

    stats = facade.get_stats(start_time=start_dt, end_time=end_dt)

    return AuditStatsResponse(
        stats=stats,
        time_range={
            "start": start_time,
            "end": end_time,
        },
    )


@router.post("/cleanup", response_model=AuditCleanupResponse, dependencies=[Depends(require_auth)])
async def cleanup_audit(
    params: AuditCleanupParams,
    facade: AuditUseCaseFacade = Depends(get_audit_facade),
) -> AuditCleanupResponse:
    """Clean up audit logs older than retention period.

    By default performs a dry run. Set dry_run=false to actually delete files.
    """
    result = facade.cleanup_old_logs(dry_run=params.dry_run)

    # Convert bytes to MB (handle potential missing key)
    would_free_bytes = result.get("would_free_bytes", 0)
    free_mb = would_free_bytes / (1024 * 1024)

    return AuditCleanupResponse(
        would_delete=result.get("would_delete", 0),
        would_free_mb=round(free_mb, 2),
        affected_files=result.get("affected_files", []),
        dry_run=result.get("dry_run", True),
        cutoff_date=result.get("cutoff_date", ""),
    )


@router.post("/triage", response_model=AuditTriageResponse, dependencies=[Depends(require_auth)])
async def audit_triage(
    request: AuditTriageRequest,
    runtime_root: Path = Depends(get_runtime_root),
    settings: Settings = Depends(get_settings),
) -> AuditTriageResponse:
    """Generate complete triage bundle.

    Input: run_id/task_id/trace_id
    Output: Complete triage package including:
    - PM quality history
    - Leakage findings
    - Director tool audit
    - Fixed issues
    - Acceptance results
    - Evidence paths
    - Next risks
    - 3-hops failure localization
    """
    if not (request.run_id or request.task_id or request.trace_id):
        raise HTTPException(status_code=400, detail="Must provide run_id, task_id, or trace_id")

    if request.run_id and not validate_run_id(request.run_id):
        raise HTTPException(status_code=400, detail=f"Invalid run_id format: {request.run_id}")

    workspace = str(getattr(settings, "workspace", "") or ".")
    bundle = build_triage_bundle(
        workspace=workspace,
        run_id=request.run_id,
        task_id=request.task_id,
        trace_id=request.trace_id,
        runtime_root=runtime_root,
    )
    return AuditTriageResponse(
        status=str(bundle.get("status", "not_found")),
        run_id=bundle.get("run_id"),
        task_id=bundle.get("task_id"),
        trace_id=bundle.get("trace_id"),
        pm_quality_history=bundle.get("pm_quality_history", []),
        leakage_findings=bundle.get("leakage_findings", []),
        director_tool_audit=bundle.get("director_tool_audit", {}),
        issues_fixed=bundle.get("issues_fixed", []),
        acceptance_results=bundle.get("acceptance_results", {}),
        evidence_paths=bundle.get("evidence_paths", {}),
        next_risks=bundle.get("next_risks", []),
        failure_hops=bundle.get("failure_hops"),
        generated_at=str(bundle.get("generated_at") or datetime.now(timezone.utc).isoformat()),
    )


@router.post("/analyze-failure", response_model=FailureAnalysisResponse, dependencies=[Depends(require_auth)])
async def analyze_failure(
    request: FailureAnalysisRequest,
    engine: AuditDiagnosisEngine = Depends(get_diagnosis_engine),
) -> FailureAnalysisResponse:
    """Analyze failure chain with 3-hop diagnosis."""
    if not (request.run_id or request.task_id or request.error_message):
        raise HTTPException(
            status_code=400,
            detail="Must provide run_id, task_id, or error_message.",
        )

    payload = engine.analyze_failure(
        run_id=request.run_id,
        task_id=request.task_id,
        error_hint=request.error_message,
        time_range=request.time_range,
        depth=request.depth,
    )
    return FailureAnalysisResponse(**payload)


@router.post("/scan-project", response_model=ProjectScanResponse, dependencies=[Depends(require_auth)])
async def scan_project(
    request: ProjectScanRequest,
    engine: AuditDiagnosisEngine = Depends(get_diagnosis_engine),
) -> ProjectScanResponse:
    """Run project-level QA/audit scan."""
    try:
        payload = engine.scan_project(
            scope=request.scope,
            focus=request.focus,
            max_files=request.max_files,
            max_findings=request.max_findings,
        )
    except FileNotFoundError as exc:
        logger.error("Project scan failed (FileNotFoundError): %s", exc)
        raise HTTPException(status_code=404, detail="internal error") from exc
    except ValueError as exc:
        logger.error("Project scan failed (ValueError): %s", exc)
        raise HTTPException(status_code=400, detail="internal error") from exc

    return ProjectScanResponse(**payload)


@router.post("/check-region", response_model=CodeRegionResponse, dependencies=[Depends(require_auth)])
async def check_code_region(
    request: CodeRegionRequest,
    engine: AuditDiagnosisEngine = Depends(get_diagnosis_engine),
) -> CodeRegionResponse:
    """Run focused QA/audit checks on a file region/function."""
    if not (request.file_path or request.function_name):
        raise HTTPException(
            status_code=400,
            detail="Either file_path or function_name is required.",
        )

    line_range = parse_line_range(request.lines)

    try:
        payload = engine.check_region(
            file_path=request.file_path,
            function_name=request.function_name,
            line_range=line_range,
        )
    except FileNotFoundError as exc:
        logger.error("Code region check failed (FileNotFoundError): %s", exc)
        raise HTTPException(status_code=404, detail="internal error") from exc
    except ValueError as exc:
        logger.error("Code region check failed (ValueError): %s", exc)
        raise HTTPException(status_code=400, detail="internal error") from exc

    return CodeRegionResponse(**payload)


@router.get("/trace/{trace_id}", response_model=AuditTraceResponse, dependencies=[Depends(require_auth)])
async def get_audit_trace(
    trace_id: str = FastApiPath(..., description="Trace ID"),
    limit: int = Query(300, ge=1, le=2000, description="Maximum trace events"),
    engine: AuditDiagnosisEngine = Depends(get_diagnosis_engine),
) -> AuditTraceResponse:
    """Get full audit timeline for a trace_id."""
    payload = engine.get_trace(trace_id=trace_id, limit=limit)
    if int(payload.get("event_count") or 0) == 0:
        raise HTTPException(status_code=404, detail=f"No events found for trace {trace_id}")
    return AuditTraceResponse(**payload)


@router.get("/failures/{run_id}/hops", response_model=FailureHopsResponse, dependencies=[Depends(require_auth)])
async def get_failure_hops(
    run_id: str = FastApiPath(..., description="Run ID"),
    runtime_root: Path = Depends(get_runtime_root),
) -> FailureHopsResponse:
    """Get 3-hops failure localization result.

    Hop 1: Phase - The phase where failure occurred
    Hop 2: Evidence - Related evidence
    Hop 3: Tool Output - Tool raw output
    """
    # Validate run_id format
    if not validate_run_id(run_id):
        raise HTTPException(status_code=400, detail=f"Invalid run_id format: {run_id}")

    # Try to read pre-computed failure_hops
    hops_path = runtime_root / "artifacts" / "runs" / run_id / "failure_hops.json"

    if hops_path.exists():
        try:
            with open(hops_path, encoding="utf-8") as f:
                data = json.load(f)

                # Upgrade to V2 schema
                return FailureHopsResponse(
                    schema_version=2,
                    run_id=data.get("run_id", run_id),
                    generated_at=data.get("generated_at", datetime.now(timezone.utc).isoformat()),
                    ready=data.get("ready", False),
                    has_failure=data.get("has_failure", False),
                    failure_code=data.get("failure_code", ""),
                    failure_event_seq=data.get("failure_event_seq"),
                    hop1_phase=data.get("hop1_phase"),
                    hop2_evidence=data.get("hop2_evidence"),
                    hop3_tool_output=data.get("hop3_tool_output"),
                )
        except (RuntimeError, ValueError) as e:
            logger.error("Failed to read failure hops for run_id=%s: %s", run_id, e)
            raise HTTPException(status_code=500, detail="internal error")

    # Not found - try to generate real-time
    events_path = _resolve_failure_hops_events_path(runtime_root)

    if events_path is None:
        raise HTTPException(status_code=404, detail=f"No events found for run {run_id}")

    # Real-time build
    try:
        hops_data = build_failure_hops(
            str(events_path),
            run_id=run_id,
            event_seq_start=0,
            event_seq_end=0,
        )

        return FailureHopsResponse(
            schema_version=2,
            run_id=run_id,
            generated_at=hops_data.get("generated_at", datetime.now(timezone.utc).isoformat()),
            ready=hops_data.get("ready", False),
            has_failure=hops_data.get("has_failure", False),
            failure_code=hops_data.get("failure_code", ""),
            failure_event_seq=hops_data.get("failure_event_seq"),
            hop1_phase=hops_data.get("hop1_phase"),
            hop2_evidence=hops_data.get("hop2_evidence"),
            hop3_tool_output=hops_data.get("hop3_tool_output"),
        )
    except (RuntimeError, ValueError) as e:
        logger.error("get_failure_hops failed: run_id=%s: %s", run_id, e)
        raise HTTPException(status_code=500, detail="internal error")


@router.get("/corruption", dependencies=[Depends(require_auth)])
async def get_corruption_log(
    limit: int = Query(100, ge=1, le=1000, description="Max records to return"),
    runtime_root: Path = Depends(get_runtime_root),
    settings: Settings = Depends(get_settings),
) -> list[dict[str, Any]]:
    """Get corruption log."""
    try:
        workspace = str(getattr(settings, "workspace", "") or ".")
        facade = AuditUseCaseFacade(runtime_root=runtime_root)
        return facade.get_corruption_log(workspace=workspace, limit=limit)
    except (RuntimeError, ValueError) as e:
        logger.error("get_corruption_log failed: %s", e)
        raise HTTPException(status_code=500, detail="internal error")
