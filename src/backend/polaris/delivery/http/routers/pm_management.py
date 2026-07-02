"""PM Management Router - PM管理API

提供文档管理、任务历史、需求追踪的REST API接口。
"""

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from polaris.cells.runtime.artifact_store.public.service import resolve_safe_path
from polaris.delivery.http.adapters.scripts_pm import ScriptsPMAdapter
from polaris.delivery.http.routers._shared import StructuredHTTPException, get_state, require_auth
from polaris.delivery.http.schemas.common import (
    DocumentDeleteResponse,
    DocumentDetailResponse,
    DocumentDiffResponse,
    DocumentListResponse,
    DocumentSearchResponse,
    DocumentVersionsResponse,
    DocumentWriteResponse,
    PMHealthResponse,
    PMInitResponse,
    PMStatusResponse,
    RequirementDetailResponse,
    RequirementListResponse,
    TaskAssignmentsResponse,
    TaskDetailResponse,
    TaskHistoryResponse,
    TaskListResponse,
    TaskSearchResponse,
)
from polaris.delivery.http.workspace import active_workspace_value, requested_or_active_workspace
from pydantic import BaseModel, Field

router = APIRouter(prefix="/pm", tags=["PM Management"])
v2_router = APIRouter(tags=["PM Management V2"])


def _get_pm_instance(workspace: str) -> ScriptsPMAdapter:
    """Get PM instance for workspace."""
    return ScriptsPMAdapter(workspace)


def _resolve_document_path(workspace: str, doc_path: str) -> str:
    """Resolve document path under workspace-bound safe path policy."""
    return resolve_safe_path(workspace, "", doc_path)


def _workspace_from_request(request: Request, requested: Any = "") -> str:
    """Resolve the active workspace for PM management requests."""
    settings = get_state(request).settings
    active_workspace = active_workspace_value(settings)
    workspace = requested_or_active_workspace(settings, requested)
    if not active_workspace and not str(requested or "").strip():
        raise StructuredHTTPException(
            status_code=400,
            code="WORKSPACE_NOT_CONFIGURED",
            message="workspace is not configured",
        )
    return workspace


def _enum_to_wire(value: Any) -> Any:
    """Convert str Enum values from PM internals to their JSON wire value."""
    return value.value if hasattr(value, "value") else value


def _task_to_response(task: Any) -> dict[str, Any]:
    """Normalize PM task dataclass/dict payloads for desktop API clients."""
    if isinstance(task, dict):
        task_dict = dict(task)
    else:
        task_dict = {
            "id": getattr(task, "id", ""),
            "title": getattr(task, "title", ""),
            "description": getattr(task, "description", ""),
            "status": _enum_to_wire(getattr(task, "status", "")),
            "priority": _enum_to_wire(getattr(task, "priority", "")),
            "assignee": getattr(task, "assignee", None),
            "assignee_type": _enum_to_wire(getattr(task, "assignee_type", None)),
            "requirements": getattr(task, "requirements", []),
            "dependencies": getattr(task, "dependencies", []),
            "estimated_effort": getattr(task, "estimated_effort", 0),
            "actual_effort": getattr(task, "actual_effort", 0),
            "created_at": getattr(task, "created_at", None),
            "updated_at": getattr(task, "updated_at", None),
            "assigned_at": getattr(task, "assigned_at", None),
            "started_at": getattr(task, "started_at", None),
            "completed_at": getattr(task, "completed_at", None),
            "result_summary": getattr(task, "result_summary", ""),
            "artifacts": getattr(task, "artifacts", []),
            "metadata": getattr(task, "metadata", {}),
        }

    task_dict["status"] = _enum_to_wire(task_dict.get("status"))
    task_dict["priority"] = _enum_to_wire(task_dict.get("priority"))
    task_dict["assignee_type"] = _enum_to_wire(task_dict.get("assignee_type"))

    title = str(task_dict.get("title") or task_dict.get("subject") or "").strip()
    subject = str(task_dict.get("subject") or title).strip()
    task_dict["title"] = title
    task_dict["subject"] = subject

    metadata = task_dict.get("metadata")
    if isinstance(metadata, dict):
        if "acceptance" in metadata:
            task_dict["acceptance"] = metadata["acceptance"]
        if "due_date" in metadata:
            task_dict["due_date"] = metadata["due_date"]
        if "tags" in metadata:
            task_dict["tags"] = metadata["tags"]
        if "parent_id" in metadata:
            task_dict["parent_id"] = metadata["parent_id"]

    return task_dict


def _document_to_response(document: Any) -> dict[str, Any]:
    """Normalize PM document rows to the public document list schema."""
    if isinstance(document, dict):
        doc_dict = dict(document)
    else:
        doc_dict = {
            "path": getattr(document, "path", ""),
            "current_version": getattr(document, "current_version", ""),
            "version_count": getattr(document, "version_count", 0),
            "last_modified": getattr(document, "last_modified", ""),
            "created_at": getattr(document, "created_at", ""),
        }

    version = str(doc_dict.get("current_version") or doc_dict.get("version") or "").strip()
    doc_dict["path"] = str(doc_dict.get("path") or "").strip()
    doc_dict["current_version"] = version
    doc_dict["version_count"] = int(doc_dict.get("version_count") or (1 if version else 0))
    doc_dict["last_modified"] = str(
        doc_dict.get("last_modified")
        or doc_dict.get("updated_at")
        or doc_dict.get("modified_at")
        or doc_dict.get("created_at")
        or ""
    )
    doc_dict["created_at"] = str(doc_dict.get("created_at") or doc_dict["last_modified"] or "")
    return doc_dict


def _collection_total(result: dict[str, Any], items: list[Any]) -> int:
    """Resolve a stable total for desktop list responses."""
    pagination = result.get("pagination")
    if isinstance(pagination, dict):
        raw_total = pagination.get("total")
        if isinstance(raw_total, int):
            return raw_total
        if isinstance(raw_total, str):
            try:
                return int(raw_total)
            except ValueError:
                pass
    return len(items)


def _with_desktop_collection_aliases(
    result: dict[str, Any],
    collection_key: str,
    *,
    normalize_item: bool = False,
) -> dict[str, Any]:
    """Add desktop `items`/`total` aliases while preserving legacy keys."""
    response = dict(result)
    raw_items = response.get(collection_key)
    items = list(raw_items) if isinstance(raw_items, list) else []
    if normalize_item:
        items = [_task_to_response(item) for item in items]

    response[collection_key] = items
    response["items"] = items
    response["total"] = _collection_total(response, items)
    response.setdefault("pagination", {"total": response["total"], "limit": len(items), "offset": 0})
    response.setdefault("ok", True)
    return response


def _with_document_collection_aliases(result: dict[str, Any]) -> dict[str, Any]:
    """Normalize document list responses while preserving legacy fields."""
    response = dict(result)
    raw_documents = response.get("documents")
    documents = [_document_to_response(item) for item in raw_documents] if isinstance(raw_documents, list) else []
    response["documents"] = documents
    response["items"] = documents
    response["total"] = _collection_total(response, documents)
    response.setdefault("pagination", {"total": response["total"], "limit": len(documents), "offset": 0})
    response.setdefault("ok", True)
    return response


def _empty_v2_collection_response(
    collection_key: str,
    *,
    limit: int,
    offset: int,
    reason: str,
    normalize_item: bool = False,
    error: str | None = None,
) -> dict[str, Any]:
    """Return an idle desktop collection projection when PM has not started."""
    payload: dict[str, Any] = {
        "ok": True,
        collection_key: [],
        "pagination": {"total": 0, "limit": limit, "offset": offset},
        "initialized": False,
        "state": "idle",
        "reason": reason,
    }
    if error:
        payload["error"] = error

    return _with_desktop_collection_aliases(
        payload,
        collection_key,
        normalize_item=normalize_item,
    )


def _empty_v2_task_list_response(
    *,
    limit: int,
    offset: int,
    reason: str,
    error: str | None = None,
) -> dict[str, Any]:
    """Return an idle desktop task-list projection when PM has not started."""
    return _empty_v2_collection_response(
        "tasks",
        limit=limit,
        offset=offset,
        reason=reason,
        normalize_item=True,
        error=error,
    )


def _empty_v2_document_list_response(
    *,
    limit: int,
    offset: int,
    reason: str,
    error: str | None = None,
) -> dict[str, Any]:
    """Return an idle desktop document-list projection when PM has not started."""
    return _empty_v2_collection_response("documents", limit=limit, offset=offset, reason=reason, error=error)


def _empty_v2_search_response(*, query: str, reason: str, error: str | None = None) -> dict[str, Any]:
    """Return an idle desktop search projection when PM has not started."""
    payload: dict[str, Any] = {
        "ok": True,
        "query": query,
        "results": [],
        "count": 0,
        "items": [],
        "total": 0,
        "initialized": False,
        "state": "idle",
        "reason": reason,
    }
    if error:
        payload["error"] = error
    return payload


def _empty_v2_document_search_response(
    *,
    query: str,
    reason: str,
    error: str | None = None,
) -> dict[str, Any]:
    """Return an idle desktop document-search projection when PM has not started."""
    return _empty_v2_search_response(query=query, reason=reason, error=error)


def _empty_v2_task_search_response(*, query: str, reason: str) -> dict[str, Any]:
    """Return an idle desktop task-search projection when PM has not started."""
    return _empty_v2_search_response(query=query, reason=reason)


def _empty_v2_task_detail_response(
    *,
    task_id: str,
    reason: str,
    error: str | None = None,
) -> dict[str, Any]:
    """Return an idle desktop task-detail projection for unavailable PM runtime."""
    payload: dict[str, Any] = {
        "ok": True,
        "id": task_id,
        "task_id": task_id,
        "initialized": False,
        "state": "idle",
        "reason": reason,
    }
    if error:
        payload["error"] = error
    return payload


def _empty_v2_document_detail_response(
    *,
    doc_path: str,
    reason: str,
    error: str | None = None,
) -> dict[str, Any]:
    """Return an idle desktop document-detail projection for unavailable PM runtime."""
    payload: dict[str, Any] = {
        "ok": True,
        "path": doc_path,
        "current_version": "",
        "version_count": 0,
        "last_modified": "",
        "created_at": "",
        "content": None,
        "versions": [],
        "analysis": {},
        "initialized": False,
        "state": "idle",
        "reason": reason,
    }
    if error:
        payload["error"] = error
    return payload


def _empty_v2_document_versions_response(
    *,
    doc_path: str,
    reason: str,
    error: str | None = None,
) -> dict[str, Any]:
    """Return an idle desktop document-versions projection for unavailable PM runtime."""
    payload: dict[str, Any] = {
        "ok": True,
        "path": doc_path,
        "versions": [],
        "initialized": False,
        "state": "idle",
        "reason": reason,
    }
    if error:
        payload["error"] = error
    return payload


def _empty_v2_document_diff_response(
    *,
    doc_path: str,
    old_version: str,
    new_version: str,
    reason: str,
    error: str | None = None,
) -> dict[str, Any]:
    """Return an idle desktop document-diff projection for unavailable PM runtime."""
    payload: dict[str, Any] = {
        "ok": True,
        "path": doc_path,
        "old_version": old_version,
        "new_version": new_version,
        "diff_text": "",
        "changed_sections": [],
        "added_requirements": [],
        "removed_requirements": [],
        "impact_score": 0.0,
        "initialized": False,
        "state": "idle",
        "reason": reason,
    }
    if error:
        payload["error"] = error
    return payload


def _empty_v2_task_assignments_response(
    *,
    task_id: str,
    reason: str,
    error: str | None = None,
) -> dict[str, Any]:
    """Return an idle desktop assignment projection for unavailable PM runtime."""
    payload: dict[str, Any] = {
        "ok": True,
        "task_id": task_id,
        "assignments": [],
        "count": 0,
        "initialized": False,
        "state": "idle",
        "reason": reason,
    }
    if error:
        payload["error"] = error
    return payload


def _empty_v2_requirement_detail_response(
    *,
    req_id: str,
    reason: str,
    error: str | None = None,
) -> dict[str, Any]:
    """Return an idle desktop requirement-detail projection for unavailable PM runtime."""
    payload: dict[str, Any] = {
        "ok": True,
        "id": req_id,
        "requirement_id": req_id,
        "initialized": False,
        "state": "idle",
        "reason": reason,
    }
    if error:
        payload["error"] = error
    return payload


def _empty_v2_pm_status_response(
    *,
    request: Request,
    workspace: str,
    reason: str,
    error: str | None = None,
) -> dict[str, Any]:
    """Return an idle desktop PM status projection for unavailable runtime."""
    resolved_workspace = _workspace_from_request(request, workspace)
    payload: dict[str, Any] = {
        "initialized": False,
        "workspace": resolved_workspace,
        "state": "idle",
        "reason": reason,
    }
    if error:
        payload["error"] = error
    return payload


def _empty_v2_pm_health_response(
    *,
    reason: str,
    error: str | None = None,
) -> dict[str, Any]:
    """Return a degraded health projection for unavailable PM runtime."""
    payload: dict[str, Any] = {
        "overall": "unavailable",
        "components": {"pm_runtime": "unavailable"},
        "metrics": {},
        "recommendations": ["Initialize or repair the PM runtime adapter before using PM desktop management."],
        "initialized": False,
        "state": "idle",
        "reason": reason,
    }
    if error:
        payload["error"] = error
    return payload


async def _get_pm_process_status() -> dict[str, Any]:
    """Return execution-broker backed PM process status when available."""

    try:
        from polaris.cells.orchestration.pm_planning.public.service import PMService
        from polaris.infrastructure.di.container import get_container

        container = await get_container()
        pm_service = await container.resolve_async(PMService)
        status = pm_service.get_status()
        return dict(status) if isinstance(status, dict) else {}
    except (AttributeError, KeyError, RuntimeError, TypeError, ValueError):
        # Keep PM document/status compatibility available in lightweight tests
        # where the process service is not bootstrapped.
        return {}


def _merge_pm_adapter_and_process_status(
    adapter_status: dict[str, Any],
    process_status: dict[str, Any],
    *,
    workspace: str,
) -> dict[str, Any]:
    result = dict(adapter_status)
    if process_status:
        initialized = result.get("initialized")
        result.update(process_status)
        if initialized is not None:
            result["initialized"] = initialized
    result.setdefault("workspace", workspace)
    return result


# ===== Request/Response Models =====


class DocumentCreateRequest(BaseModel):
    content: str
    change_summary: str = ""


class DocumentUpdateRequest(BaseModel):
    content: str
    change_summary: str = ""


class PMTaskCreateRequest(BaseModel):
    subject: str = Field(..., min_length=1)
    description: str = ""
    priority: str | None = None
    status: str | None = None
    acceptance: list[str] = Field(default_factory=list)
    assignee: str | None = None
    due_date: str | None = None
    tags: list[str] = Field(default_factory=list)
    parent_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentInfo(BaseModel):
    path: str
    current_version: str
    version_count: int
    last_modified: str
    created_at: str


class TaskInfo(BaseModel):
    id: str
    title: str
    description: str
    status: str
    priority: str
    assignee: str | None = None
    assignee_type: str | None = None
    created_at: str
    updated_at: str
    completed_at: str | None = None


class RequirementInfo(BaseModel):
    id: str
    title: str
    description: str
    status: str
    priority: str
    source_doc: str | None = None
    created_at: str
    updated_at: str
    tasks: list[str]


# ===== Document Management Endpoints =====


@router.get("/documents", dependencies=[Depends(require_auth)], response_model=DocumentListResponse)
def list_documents(
    request: Request,
    doc_type: str | None = Query(None, description="Filter by document type"),
    pattern: str | None = Query(None, description="Glob pattern to filter paths"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    workspace: str = Query("", description="Workspace override"),
) -> dict[str, Any]:
    """List all tracked documents in the workspace."""
    workspace = _workspace_from_request(request, workspace)

    pm = _get_pm_instance(workspace)

    if not pm.is_initialized():
        raise StructuredHTTPException(status_code=400, code="PM_NOT_INITIALIZED", message="PM system not initialized")

    result = pm.list_documents(doc_type=doc_type, pattern=pattern, limit=limit, offset=offset)
    return _with_document_collection_aliases(result)


@router.post("/documents/{doc_path:path}", dependencies=[Depends(require_auth)], response_model=DocumentWriteResponse)
def create_or_update_document(
    request: Request,
    doc_path: str,
    body: DocumentUpdateRequest,
    workspace: str = Query("", description="Workspace override"),
) -> dict[str, Any]:
    """Create or update a document."""
    workspace = _workspace_from_request(request, workspace)

    pm = _get_pm_instance(workspace)

    if not pm.is_initialized():
        raise StructuredHTTPException(status_code=400, code="PM_NOT_INITIALIZED", message="PM system not initialized")

    # Resolve full path
    full_path = _resolve_document_path(workspace, doc_path)

    version_info = pm.create_or_update_document(
        doc_path=full_path,
        content=body.content,
        updated_by="api",
        change_summary=body.change_summary or "Updated via API",
    )

    if version_info is None:
        raise StructuredHTTPException(
            status_code=500, code="PM_OPERATION_FAILED", message="Failed to create/update document"
        )

    return {
        "success": True,
        "path": full_path,
        "version": version_info.version if hasattr(version_info, "version") else str(version_info),
        "checksum": version_info.checksum if hasattr(version_info, "checksum") else None,
    }


@router.delete(
    "/documents/{doc_path:path}", dependencies=[Depends(require_auth)], response_model=DocumentDeleteResponse
)
def delete_document(
    request: Request,
    doc_path: str,
    delete_file: bool = Query(True, description="Whether to delete the actual file"),
    workspace: str = Query("", description="Workspace override"),
) -> dict[str, Any]:
    """Delete a document and its version history."""
    workspace = _workspace_from_request(request, workspace)

    pm = _get_pm_instance(workspace)

    if not pm.is_initialized():
        raise StructuredHTTPException(status_code=400, code="PM_NOT_INITIALIZED", message="PM system not initialized")

    # Resolve full path
    full_path = _resolve_document_path(workspace, doc_path)

    success = pm.delete_document(full_path, delete_file=delete_file)

    if not success:
        raise StructuredHTTPException(status_code=500, code="PM_OPERATION_FAILED", message="Failed to delete document")

    return {"success": True, "path": full_path, "deleted": True}


@router.get(
    "/documents/{doc_path:path}/versions", dependencies=[Depends(require_auth)], response_model=DocumentVersionsResponse
)
def get_document_versions(
    request: Request,
    doc_path: str,
    workspace: str = Query("", description="Workspace override"),
) -> dict[str, Any]:
    """Get all versions of a document."""
    workspace = _workspace_from_request(request, workspace)

    pm = _get_pm_instance(workspace)

    if not pm.is_initialized():
        raise StructuredHTTPException(status_code=400, code="PM_NOT_INITIALIZED", message="PM system not initialized")

    # Resolve full path
    full_path = _resolve_document_path(workspace, doc_path)

    versions = pm.get_document_versions(full_path)

    return {
        "path": full_path,
        "versions": [
            {
                "version": v.version if hasattr(v, "version") else v.get("version"),
                "created_at": v.created_at if hasattr(v, "created_at") else v.get("created_at"),
                "created_by": v.created_by if hasattr(v, "created_by") else v.get("created_by"),
                "change_summary": v.change_summary if hasattr(v, "change_summary") else v.get("change_summary"),
                "checksum": v.checksum if hasattr(v, "checksum") else v.get("checksum"),
            }
            for v in versions
        ],
    }


@router.get(
    "/documents/{doc_path:path}/compare", dependencies=[Depends(require_auth)], response_model=DocumentDiffResponse
)
def compare_document_versions(
    request: Request,
    doc_path: str,
    old_version: str = Query(..., description="Old version number"),
    new_version: str = Query(..., description="New version number"),
    workspace: str = Query("", description="Workspace override"),
) -> dict[str, Any]:
    """Compare two document versions."""
    workspace = _workspace_from_request(request, workspace)

    pm = _get_pm_instance(workspace)

    if not pm.is_initialized():
        raise StructuredHTTPException(status_code=400, code="PM_NOT_INITIALIZED", message="PM system not initialized")

    # Resolve full path
    full_path = _resolve_document_path(workspace, doc_path)

    diff = pm.compare_document_versions(full_path, old_version, new_version)

    return {
        "path": full_path,
        "old_version": diff.old_version if hasattr(diff, "old_version") else diff.get("old_version"),
        "new_version": diff.new_version if hasattr(diff, "new_version") else diff.get("new_version"),
        "diff_text": diff.diff_text if hasattr(diff, "diff_text") else diff.get("diff_text"),
        "changed_sections": diff.changed_sections
        if hasattr(diff, "changed_sections")
        else diff.get("changed_sections", []),
        "added_requirements": diff.added_requirements
        if hasattr(diff, "added_requirements")
        else diff.get("added_requirements", []),
        "removed_requirements": diff.removed_requirements
        if hasattr(diff, "removed_requirements")
        else diff.get("removed_requirements", []),
        "impact_score": diff.impact_score if hasattr(diff, "impact_score") else diff.get("impact_score", 0.0),
    }


@router.get("/documents/{doc_path:path}", dependencies=[Depends(require_auth)], response_model=DocumentDetailResponse)
def get_document(
    request: Request,
    doc_path: str,
    version: str | None = Query(None, description="Specific version (default: current)"),
    workspace: str = Query("", description="Workspace override"),
) -> dict[str, Any]:
    """Get document information including versions and analysis."""
    workspace = _workspace_from_request(request, workspace)

    pm = _get_pm_instance(workspace)

    if not pm.is_initialized():
        raise StructuredHTTPException(status_code=400, code="PM_NOT_INITIALIZED", message="PM system not initialized")

    # Resolve full path
    full_path = _resolve_document_path(workspace, doc_path)

    doc_info = pm.get_document(full_path)
    if doc_info is None:
        raise StructuredHTTPException(status_code=404, code="DOCUMENT_NOT_FOUND", message="Document not found")

    # Add content if requested
    content = pm.get_document_content(full_path, version)
    if content is not None:
        doc_info["content"] = content

    return doc_info


@router.get("/search/documents", dependencies=[Depends(require_auth)], response_model=DocumentSearchResponse)
def search_documents(
    request: Request,
    q: str = Query(..., description="Search query"),
    limit: int = Query(20, ge=1, le=100),
    workspace: str = Query("", description="Workspace override"),
) -> dict[str, Any]:
    """Search documents by content or path."""
    workspace = _workspace_from_request(request, workspace)

    pm = _get_pm_instance(workspace)

    if not pm.is_initialized():
        raise StructuredHTTPException(status_code=400, code="PM_NOT_INITIALIZED", message="PM system not initialized")

    results = pm.search_documents(query=q, limit=limit)

    return {"query": q, "results": results, "count": len(results)}


# ===== Task Management Endpoints =====


@router.get("/tasks", dependencies=[Depends(require_auth)], response_model=TaskListResponse)
def list_tasks(
    request: Request,
    status: str | None = Query(None, description="Filter by status"),
    assignee: str | None = Query(None, description="Filter by assignee"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    workspace: str = Query("", description="Workspace override"),
) -> dict[str, Any]:
    """List tasks with optional filtering."""
    workspace = _workspace_from_request(request, workspace)

    pm = _get_pm_instance(workspace)

    if not pm.is_initialized():
        raise StructuredHTTPException(status_code=400, code="PM_NOT_INITIALIZED", message="PM system not initialized")

    result = pm.list_tasks(status=status, assignee=assignee, limit=limit, offset=offset)
    return _with_desktop_collection_aliases(result, "tasks", normalize_item=True)


@router.get("/tasks/history", dependencies=[Depends(require_auth)], response_model=TaskHistoryResponse)
def get_task_history(
    request: Request,
    task_id: str | None = Query(None, description="Filter by task ID"),
    assignee: str | None = Query(None, description="Filter by assignee"),
    status: str | None = Query(None, description="Filter by status"),
    start_date: str | None = Query(None, description="Start date (ISO format)"),
    end_date: str | None = Query(None, description="End date (ISO format)"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    workspace: str = Query("", description="Workspace override"),
) -> dict[str, Any]:
    """Get task history with filtering and pagination."""
    workspace = _workspace_from_request(request, workspace)

    pm = _get_pm_instance(workspace)

    if not pm.is_initialized():
        raise StructuredHTTPException(status_code=400, code="PM_NOT_INITIALIZED", message="PM system not initialized")

    result = pm.get_task_history(
        task_id=task_id,
        assignee=assignee,
        status=status,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        offset=offset,
    )
    return result


@router.get("/tasks/director", dependencies=[Depends(require_auth)], response_model=TaskHistoryResponse)
def get_director_task_history(
    request: Request,
    iteration: int | None = Query(None, description="Filter by PM iteration number"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    workspace: str = Query("", description="Workspace override"),
) -> dict[str, Any]:
    """Get tasks that were dispatched to Director.

    This retrieves the task list sent to Director in each orchestration iteration.
    """
    workspace = _workspace_from_request(request, workspace)

    pm = _get_pm_instance(workspace)

    if not pm.is_initialized():
        raise StructuredHTTPException(status_code=400, code="PM_NOT_INITIALIZED", message="PM system not initialized")

    result = pm.get_director_task_history(iteration=iteration, limit=limit, offset=offset)
    return result


@router.get("/tasks/{task_id}", dependencies=[Depends(require_auth)], response_model=TaskDetailResponse)
def get_task(
    request: Request,
    task_id: str,
    workspace: str = Query("", description="Workspace override"),
) -> dict[str, Any]:
    """Get a specific task by ID."""
    workspace = _workspace_from_request(request, workspace)

    pm = _get_pm_instance(workspace)

    if not pm.is_initialized():
        raise StructuredHTTPException(status_code=400, code="PM_NOT_INITIALIZED", message="PM system not initialized")

    task = pm.get_task(task_id)
    if task is None:
        raise StructuredHTTPException(status_code=404, code="TASK_NOT_FOUND", message="Task not found")

    return _task_to_response(task)


def create_task(
    request: Request,
    body: PMTaskCreateRequest,
    workspace: str = Query("", description="Workspace override"),
) -> dict[str, Any]:
    """Create a PM task in the workspace-owned task registry."""
    workspace = _workspace_from_request(request, workspace)

    pm = _get_pm_instance(workspace)

    if not pm.is_initialized():
        raise StructuredHTTPException(status_code=400, code="PM_NOT_INITIALIZED", message="PM system not initialized")

    task = pm.create_task(
        subject=body.subject.strip(),
        description=body.description,
        priority=body.priority,
        status=body.status,
        acceptance=body.acceptance,
        assignee=body.assignee,
        due_date=body.due_date,
        tags=body.tags,
        parent_id=body.parent_id,
        metadata=body.metadata,
    )
    return _task_to_response(task)


@router.get(
    "/tasks/{task_id}/assignments", dependencies=[Depends(require_auth)], response_model=TaskAssignmentsResponse
)
def get_task_assignments(
    request: Request,
    task_id: str,
    limit: int = Query(100, ge=1, le=500),
    workspace: str = Query("", description="Workspace override"),
) -> dict[str, Any]:
    """Get assignment history for a task."""
    workspace = _workspace_from_request(request, workspace)

    pm = _get_pm_instance(workspace)

    if not pm.is_initialized():
        raise StructuredHTTPException(status_code=400, code="PM_NOT_INITIALIZED", message="PM system not initialized")

    assignments = pm.get_task_assignments(task_id=task_id, limit=limit)

    return {"task_id": task_id, "assignments": assignments, "count": len(assignments)}


@router.get("/search/tasks", dependencies=[Depends(require_auth)], response_model=TaskSearchResponse)
def search_tasks(
    request: Request,
    q: str = Query(..., description="Search query"),
    limit: int = Query(20, ge=1, le=100),
    workspace: str = Query("", description="Workspace override"),
) -> dict[str, Any]:
    """Search tasks by title or description."""
    workspace = _workspace_from_request(request, workspace)

    pm = _get_pm_instance(workspace)

    if not pm.is_initialized():
        raise StructuredHTTPException(status_code=400, code="PM_NOT_INITIALIZED", message="PM system not initialized")

    results = pm.search_tasks(query=q, limit=limit)

    return {"query": q, "results": results, "count": len(results)}


# ===== Requirements Endpoints =====


@router.get("/requirements", dependencies=[Depends(require_auth)], response_model=RequirementListResponse)
def list_requirements(
    request: Request,
    status: str | None = Query(None, description="Filter by status"),
    priority: str | None = Query(None, description="Filter by priority"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    workspace: str = Query("", description="Workspace override"),
) -> dict[str, Any]:
    """List requirements with optional filtering."""
    workspace = _workspace_from_request(request, workspace)

    pm = _get_pm_instance(workspace)

    if not pm.is_initialized():
        raise StructuredHTTPException(status_code=400, code="PM_NOT_INITIALIZED", message="PM system not initialized")

    result = pm.list_requirements(status=status, priority=priority, limit=limit, offset=offset)
    return _with_desktop_collection_aliases(result, "requirements")


@router.get("/requirements/{req_id}", dependencies=[Depends(require_auth)], response_model=RequirementDetailResponse)
def get_requirement(
    request: Request,
    req_id: str,
    workspace: str = Query("", description="Workspace override"),
) -> dict[str, Any]:
    """Get a specific requirement by ID."""
    workspace = _workspace_from_request(request, workspace)

    pm = _get_pm_instance(workspace)

    if not pm.is_initialized():
        raise StructuredHTTPException(status_code=400, code="PM_NOT_INITIALIZED", message="PM system not initialized")

    req = pm.get_requirement(req_id)
    if req is None:
        raise StructuredHTTPException(status_code=404, code="REQUIREMENT_NOT_FOUND", message="Requirement not found")

    return req


# ===== Project Status & Health =====


@router.get("/status", dependencies=[Depends(require_auth)], response_model=PMStatusResponse)
async def get_pm_status(
    request: Request,
    workspace: str = Query("", description="Workspace override"),
) -> dict[str, Any]:
    """Get PM system status."""
    workspace = _workspace_from_request(request, workspace)

    pm = _get_pm_instance(workspace)
    process_status = await _get_pm_process_status()

    if not pm.is_initialized():
        return _merge_pm_adapter_and_process_status(
            {"initialized": False, "workspace": workspace},
            process_status,
            workspace=workspace,
        )

    status = pm.get_status()
    adapter_status = dict(status) if isinstance(status, dict) else {"initialized": True}
    adapter_status.setdefault("initialized", True)
    return _merge_pm_adapter_and_process_status(
        adapter_status,
        process_status,
        workspace=workspace,
    )


@router.get("/health", dependencies=[Depends(require_auth)], response_model=PMHealthResponse)
def get_pm_health(
    request: Request,
    workspace: str = Query("", description="Workspace override"),
) -> dict[str, Any]:
    """Get project health analysis."""
    workspace = _workspace_from_request(request, workspace)

    pm = _get_pm_instance(workspace)

    if not pm.is_initialized():
        raise StructuredHTTPException(status_code=400, code="PM_NOT_INITIALIZED", message="PM system not initialized")

    return pm.analyze_project_health()


@router.post("/init", dependencies=[Depends(require_auth)], response_model=PMInitResponse)
def init_pm(
    request: Request,
    project_name: str = Query("", description="Project name"),
    description: str = Query("", description="Project description"),
    workspace: str = Query("", description="Workspace override"),
) -> dict[str, Any]:
    """Initialize PM system for the workspace."""
    workspace = _workspace_from_request(request, workspace)

    pm = _get_pm_instance(workspace)

    if pm.is_initialized():
        return {"initialized": True, "message": "PM system already initialized", "workspace": workspace}

    result = pm.initialize(project_name=project_name or "Unnamed Project", description=description)
    response = dict(result) if isinstance(result, dict) else {"initialized": bool(result)}
    response.setdefault("initialized", True)
    response.setdefault("workspace", workspace)
    response.setdefault("project_name", project_name or "Unnamed Project")
    return response


# --- V2 PM management endpoints ---


@v2_router.get("/v2/pm/documents", dependencies=[Depends(require_auth)], response_model=DocumentListResponse)
def v2_list_documents(
    request: Request,
    doc_type: str | None = Query(None, description="Filter by document type"),
    pattern: str | None = Query(None, description="Glob pattern to filter paths"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    workspace: str = Query("", description="Workspace override"),
) -> dict[str, Any]:
    """List tracked documents in the workspace."""
    try:
        result = list_documents(request, doc_type, pattern, limit, offset, workspace)
    except StructuredHTTPException as exc:
        if exc.code != "PM_NOT_INITIALIZED":
            raise
        return _empty_v2_document_list_response(limit=limit, offset=offset, reason=exc.code)
    except ImportError as exc:
        return _empty_v2_document_list_response(
            limit=limit,
            offset=offset,
            reason="PM_RUNTIME_UNAVAILABLE",
            error=str(exc),
        )
    return _with_document_collection_aliases(result)


@v2_router.get(
    "/v2/pm/documents/{doc_path:path}/versions",
    dependencies=[Depends(require_auth)],
    response_model=DocumentVersionsResponse,
)
def v2_get_document_versions(
    request: Request,
    doc_path: str,
    workspace: str = Query("", description="Workspace override"),
) -> dict[str, Any]:
    """Get all versions of a document."""
    try:
        return get_document_versions(request, doc_path, workspace)
    except StructuredHTTPException as exc:
        if exc.code != "PM_NOT_INITIALIZED":
            raise
        return _empty_v2_document_versions_response(doc_path=doc_path, reason=exc.code)
    except ImportError as exc:
        return _empty_v2_document_versions_response(
            doc_path=doc_path,
            reason="PM_RUNTIME_UNAVAILABLE",
            error=str(exc),
        )


@v2_router.get(
    "/v2/pm/documents/{doc_path:path}/compare",
    dependencies=[Depends(require_auth)],
    response_model=DocumentDiffResponse,
)
def v2_compare_document_versions(
    request: Request,
    doc_path: str,
    old_version: str = Query(..., description="Old version number"),
    new_version: str = Query(..., description="New version number"),
    workspace: str = Query("", description="Workspace override"),
) -> dict[str, Any]:
    """Compare two versions of a document."""
    try:
        return compare_document_versions(request, doc_path, old_version, new_version, workspace)
    except StructuredHTTPException as exc:
        if exc.code != "PM_NOT_INITIALIZED":
            raise
        return _empty_v2_document_diff_response(
            doc_path=doc_path,
            old_version=old_version,
            new_version=new_version,
            reason=exc.code,
        )
    except ImportError as exc:
        return _empty_v2_document_diff_response(
            doc_path=doc_path,
            old_version=old_version,
            new_version=new_version,
            reason="PM_RUNTIME_UNAVAILABLE",
            error=str(exc),
        )


@v2_router.get(
    "/v2/pm/documents/{doc_path:path}", dependencies=[Depends(require_auth)], response_model=DocumentDetailResponse
)
def v2_get_document(
    request: Request,
    doc_path: str,
    version: str | None = Query(None, description="Specific version (default: current)"),
    workspace: str = Query("", description="Workspace override"),
) -> dict[str, Any]:
    """Get a single document with optional version."""
    try:
        return get_document(request, doc_path, version, workspace)
    except StructuredHTTPException as exc:
        if exc.code != "PM_NOT_INITIALIZED":
            raise
        return _empty_v2_document_detail_response(doc_path=doc_path, reason=exc.code)
    except ImportError as exc:
        return _empty_v2_document_detail_response(
            doc_path=doc_path,
            reason="PM_RUNTIME_UNAVAILABLE",
            error=str(exc),
        )


@v2_router.post(
    "/v2/pm/documents/{doc_path:path}", dependencies=[Depends(require_auth)], response_model=DocumentWriteResponse
)
def v2_create_or_update_document(
    request: Request,
    doc_path: str,
    body: DocumentUpdateRequest,
    workspace: str = Query("", description="Workspace override"),
) -> dict[str, Any]:
    """Create or update a document."""
    return create_or_update_document(request, doc_path, body, workspace)


@v2_router.delete(
    "/v2/pm/documents/{doc_path:path}", dependencies=[Depends(require_auth)], response_model=DocumentDeleteResponse
)
def v2_delete_document(
    request: Request,
    doc_path: str,
    delete_file: bool = Query(True, description="Whether to delete the actual file"),
    workspace: str = Query("", description="Workspace override"),
) -> dict[str, Any]:
    """Delete a document and optionally its backing file."""
    return delete_document(request, doc_path, delete_file, workspace)


@v2_router.get("/v2/pm/search/documents", dependencies=[Depends(require_auth)], response_model=DocumentSearchResponse)
def v2_search_documents(
    request: Request,
    q: str = Query(..., description="Search query"),
    limit: int = Query(20, ge=1, le=100),
    workspace: str = Query("", description="Workspace override"),
) -> dict[str, Any]:
    """Search documents by content or path."""
    try:
        return search_documents(request, q, limit, workspace)
    except StructuredHTTPException as exc:
        if exc.code != "PM_NOT_INITIALIZED":
            raise
        return _empty_v2_document_search_response(query=q, reason=exc.code)
    except ImportError as exc:
        return _empty_v2_document_search_response(
            query=q,
            reason="PM_RUNTIME_UNAVAILABLE",
            error=str(exc),
        )


@v2_router.get("/v2/pm/tasks", dependencies=[Depends(require_auth)], response_model=TaskListResponse)
def v2_list_tasks(
    request: Request,
    status: str | None = Query(None, description="Filter by status"),
    assignee: str | None = Query(None, description="Filter by assignee"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    workspace: str = Query("", description="Workspace override"),
) -> dict[str, Any]:
    """List tasks with optional filtering."""
    try:
        result = list_tasks(request, status, assignee, limit, offset, workspace)
    except StructuredHTTPException as exc:
        if exc.code != "PM_NOT_INITIALIZED":
            raise
        return _empty_v2_task_list_response(limit=limit, offset=offset, reason=exc.code)
    except ImportError as exc:
        return _empty_v2_task_list_response(
            limit=limit,
            offset=offset,
            reason="PM_RUNTIME_UNAVAILABLE",
            error=str(exc),
        )
    return _with_desktop_collection_aliases(result, "tasks", normalize_item=True)


@v2_router.post("/v2/pm/tasks", dependencies=[Depends(require_auth)], response_model=TaskDetailResponse)
def v2_create_task(
    request: Request,
    body: PMTaskCreateRequest,
    workspace: str = Query("", description="Workspace override"),
) -> dict[str, Any]:
    """Create a PM task through the desktop v2 management API."""
    return create_task(request, body, workspace)


@v2_router.get("/v2/pm/tasks/history", dependencies=[Depends(require_auth)], response_model=TaskHistoryResponse)
def v2_get_task_history(
    request: Request,
    task_id: str | None = Query(None, description="Filter by task ID"),
    assignee: str | None = Query(None, description="Filter by assignee"),
    status: str | None = Query(None, description="Filter by status"),
    start_date: str | None = Query(None, description="Start date (ISO format)"),
    end_date: str | None = Query(None, description="End date (ISO format)"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    workspace: str = Query("", description="Workspace override"),
) -> dict[str, Any]:
    """Get task history with filtering and pagination."""
    try:
        return get_task_history(request, task_id, assignee, status, start_date, end_date, limit, offset, workspace)
    except StructuredHTTPException as exc:
        if exc.code != "PM_NOT_INITIALIZED":
            raise
        return _empty_v2_collection_response("history", limit=limit, offset=offset, reason=exc.code)
    except ImportError as exc:
        return _empty_v2_collection_response(
            "history",
            limit=limit,
            offset=offset,
            reason="PM_RUNTIME_UNAVAILABLE",
            error=str(exc),
        )


@v2_router.get("/v2/pm/tasks/director", dependencies=[Depends(require_auth)], response_model=TaskHistoryResponse)
def v2_get_director_task_history(
    request: Request,
    iteration: int | None = Query(None, description="Filter by PM iteration number"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    workspace: str = Query("", description="Workspace override"),
) -> dict[str, Any]:
    """Get tasks dispatched to Director by iteration."""
    try:
        return get_director_task_history(request, iteration, limit, offset, workspace)
    except StructuredHTTPException as exc:
        if exc.code != "PM_NOT_INITIALIZED":
            raise
        return _empty_v2_collection_response("iterations", limit=limit, offset=offset, reason=exc.code)
    except ImportError as exc:
        return _empty_v2_collection_response(
            "iterations",
            limit=limit,
            offset=offset,
            reason="PM_RUNTIME_UNAVAILABLE",
            error=str(exc),
        )


@v2_router.get(
    "/v2/pm/tasks/{task_id}/assignments", dependencies=[Depends(require_auth)], response_model=TaskAssignmentsResponse
)
def v2_get_task_assignments(
    request: Request,
    task_id: str,
    limit: int = Query(100, ge=1, le=500),
    workspace: str = Query("", description="Workspace override"),
) -> dict[str, Any]:
    """Get assignment history for a task."""
    try:
        return get_task_assignments(request, task_id, limit, workspace)
    except StructuredHTTPException as exc:
        if exc.code != "PM_NOT_INITIALIZED":
            raise
        return _empty_v2_task_assignments_response(task_id=task_id, reason=exc.code)
    except ImportError as exc:
        return _empty_v2_task_assignments_response(
            task_id=task_id,
            reason="PM_RUNTIME_UNAVAILABLE",
            error=str(exc),
        )


@v2_router.get("/v2/pm/tasks/{task_id}", dependencies=[Depends(require_auth)], response_model=TaskDetailResponse)
def v2_get_task(
    request: Request,
    task_id: str,
    workspace: str = Query("", description="Workspace override"),
) -> dict[str, Any]:
    """Get a specific task by ID."""
    try:
        return get_task(request, task_id, workspace)
    except StructuredHTTPException as exc:
        if exc.code != "PM_NOT_INITIALIZED":
            raise
        return _empty_v2_task_detail_response(task_id=task_id, reason=exc.code)
    except ImportError as exc:
        return _empty_v2_task_detail_response(
            task_id=task_id,
            reason="PM_RUNTIME_UNAVAILABLE",
            error=str(exc),
        )


@v2_router.get("/v2/pm/search/tasks", dependencies=[Depends(require_auth)], response_model=TaskSearchResponse)
def v2_search_tasks(
    request: Request,
    q: str = Query(..., description="Search query"),
    limit: int = Query(20, ge=1, le=100),
    workspace: str = Query("", description="Workspace override"),
) -> dict[str, Any]:
    """Search tasks by title or description."""
    try:
        return search_tasks(request, q, limit, workspace)
    except StructuredHTTPException as exc:
        if exc.code != "PM_NOT_INITIALIZED":
            raise
        return _empty_v2_task_search_response(query=q, reason=exc.code)
    except ImportError as exc:
        payload = _empty_v2_task_search_response(query=q, reason="PM_RUNTIME_UNAVAILABLE")
        payload["error"] = str(exc)
        return payload


@v2_router.get("/v2/pm/requirements", dependencies=[Depends(require_auth)], response_model=RequirementListResponse)
def v2_list_requirements(
    request: Request,
    status: str | None = Query(None, description="Filter by status"),
    priority: str | None = Query(None, description="Filter by priority"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    workspace: str = Query("", description="Workspace override"),
) -> dict[str, Any]:
    """List requirements with optional filtering."""
    try:
        result = list_requirements(request, status, priority, limit, offset, workspace)
    except StructuredHTTPException as exc:
        if exc.code != "PM_NOT_INITIALIZED":
            raise
        return _empty_v2_collection_response("requirements", limit=limit, offset=offset, reason=exc.code)
    except ImportError as exc:
        return _empty_v2_collection_response(
            "requirements",
            limit=limit,
            offset=offset,
            reason="PM_RUNTIME_UNAVAILABLE",
            error=str(exc),
        )
    return _with_desktop_collection_aliases(result, "requirements")


@v2_router.get(
    "/v2/pm/requirements/{req_id}", dependencies=[Depends(require_auth)], response_model=RequirementDetailResponse
)
def v2_get_requirement(
    request: Request,
    req_id: str,
    workspace: str = Query("", description="Workspace override"),
) -> dict[str, Any]:
    """Get a specific requirement by ID."""
    try:
        return get_requirement(request, req_id, workspace)
    except StructuredHTTPException as exc:
        if exc.code != "PM_NOT_INITIALIZED":
            raise
        return _empty_v2_requirement_detail_response(req_id=req_id, reason=exc.code)
    except ImportError as exc:
        return _empty_v2_requirement_detail_response(
            req_id=req_id,
            reason="PM_RUNTIME_UNAVAILABLE",
            error=str(exc),
        )


@v2_router.get("/v2/pm/management/status", dependencies=[Depends(require_auth)], response_model=PMStatusResponse)
async def v2_get_pm_status(
    request: Request,
    workspace: str = Query("", description="Workspace override"),
) -> dict[str, Any]:
    """Get PM system status for the workspace."""
    try:
        return await get_pm_status(request, workspace)
    except ImportError as exc:
        return _empty_v2_pm_status_response(
            request=request,
            workspace=workspace,
            reason="PM_RUNTIME_UNAVAILABLE",
            error=str(exc),
        )


@v2_router.get("/v2/pm/management/health", dependencies=[Depends(require_auth)], response_model=PMHealthResponse)
def v2_get_pm_health(
    request: Request,
    workspace: str = Query("", description="Workspace override"),
) -> dict[str, Any]:
    """Get project health analysis."""
    try:
        return get_pm_health(request, workspace)
    except StructuredHTTPException as exc:
        if exc.code != "PM_NOT_INITIALIZED":
            raise
        return _empty_v2_pm_health_response(reason=exc.code)
    except ImportError as exc:
        return _empty_v2_pm_health_response(
            reason="PM_RUNTIME_UNAVAILABLE",
            error=str(exc),
        )


@v2_router.post("/v2/pm/management/init", dependencies=[Depends(require_auth)], response_model=PMInitResponse)
def v2_init_pm(
    request: Request,
    project_name: str = Query("", description="Project name"),
    description: str = Query("", description="Project description"),
    workspace: str = Query("", description="Workspace override"),
) -> dict[str, Any]:
    """Initialize PM system for the workspace."""
    return init_pm(request, project_name, description, workspace)
