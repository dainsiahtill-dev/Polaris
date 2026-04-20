"""PM Management Router - PM管理API

提供文档管理、任务历史、需求追踪的REST API接口。
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from polaris.cells.runtime.artifact_store.public.service import resolve_safe_path
from polaris.delivery.http.adapters.scripts_pm import ScriptsPMAdapter
from polaris.delivery.http.routers._shared import get_state, require_auth
from pydantic import BaseModel

router = APIRouter(prefix="/pm", tags=["PM Management"])


def _get_pm_instance(workspace: str) -> ScriptsPMAdapter:
    """Get PM instance for workspace."""
    return ScriptsPMAdapter(workspace)


def _resolve_document_path(workspace: str, doc_path: str) -> str:
    """Resolve document path under workspace-bound safe path policy."""
    return resolve_safe_path(workspace, "", doc_path)


# ===== Request/Response Models =====


class DocumentCreateRequest(BaseModel):
    content: str
    change_summary: str = ""


class DocumentUpdateRequest(BaseModel):
    content: str
    change_summary: str = ""


class DocumentInfo(BaseModel):
    path: str
    current_version: str
    version_count: int
    last_modified: str
    created_at: str


class DocumentListResponse(BaseModel):
    documents: list[DocumentInfo]
    pagination: dict[str, Any]


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


class TaskListResponse(BaseModel):
    tasks: list[dict[str, Any]]
    pagination: dict[str, Any]


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


class RequirementListResponse(BaseModel):
    requirements: list[dict[str, Any]]
    pagination: dict[str, Any]


# ===== Document Management Endpoints =====


@router.get("/documents", dependencies=[Depends(require_auth)])
def list_documents(
    request: Request,
    doc_type: str | None = Query(None, description="Filter by document type"),
    pattern: str | None = Query(None, description="Glob pattern to filter paths"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """List all tracked documents in the workspace."""
    state = get_state(request)
    workspace_raw = state.settings.workspace
    workspace = str(workspace_raw) if not isinstance(workspace_raw, str) else workspace_raw

    pm = _get_pm_instance(workspace)

    if not pm.is_initialized():
        raise HTTPException(status_code=400, detail="PM system not initialized")

    result = pm.list_documents(doc_type=doc_type, pattern=pattern, limit=limit, offset=offset)
    return result


@router.get("/documents/{doc_path:path}", dependencies=[Depends(require_auth)])
def get_document(
    request: Request,
    doc_path: str,
    version: str | None = Query(None, description="Specific version (default: current)"),
) -> dict[str, Any]:
    """Get document information including versions and analysis."""
    state = get_state(request)
    workspace_raw = state.settings.workspace
    workspace = str(workspace_raw) if not isinstance(workspace_raw, str) else workspace_raw

    pm = _get_pm_instance(workspace)

    if not pm.is_initialized():
        raise HTTPException(status_code=400, detail="PM system not initialized")

    # Resolve full path
    full_path = _resolve_document_path(workspace, doc_path)

    doc_info = pm.get_document(full_path)
    if doc_info is None:
        raise HTTPException(status_code=404, detail=f"Document not found: {doc_path}")

    # Add content if requested
    content = pm.get_document_content(full_path, version)
    if content is not None:
        doc_info["content"] = content

    return doc_info


@router.post("/documents/{doc_path:path}", dependencies=[Depends(require_auth)])
def create_or_update_document(
    request: Request,
    doc_path: str,
    body: DocumentUpdateRequest,
) -> dict[str, Any]:
    """Create or update a document."""
    state = get_state(request)
    workspace_raw = state.settings.workspace
    workspace = str(workspace_raw) if not isinstance(workspace_raw, str) else workspace_raw

    pm = _get_pm_instance(workspace)

    if not pm.is_initialized():
        raise HTTPException(status_code=400, detail="PM system not initialized")

    # Resolve full path
    full_path = _resolve_document_path(workspace, doc_path)

    version_info = pm.create_or_update_document(
        doc_path=full_path,
        content=body.content,
        updated_by="api",
        change_summary=body.change_summary or "Updated via API",
    )

    if version_info is None:
        raise HTTPException(status_code=500, detail="Failed to create/update document")

    return {
        "success": True,
        "path": full_path,
        "version": version_info.version if hasattr(version_info, "version") else str(version_info),
        "checksum": version_info.checksum if hasattr(version_info, "checksum") else None,
    }


@router.delete("/documents/{doc_path:path}", dependencies=[Depends(require_auth)])
def delete_document(
    request: Request,
    doc_path: str,
    delete_file: bool = Query(True, description="Whether to delete the actual file"),
) -> dict[str, Any]:
    """Delete a document and its version history."""
    state = get_state(request)
    workspace_raw = state.settings.workspace
    workspace = str(workspace_raw) if not isinstance(workspace_raw, str) else workspace_raw

    pm = _get_pm_instance(workspace)

    if not pm.is_initialized():
        raise HTTPException(status_code=400, detail="PM system not initialized")

    # Resolve full path
    full_path = _resolve_document_path(workspace, doc_path)

    success = pm.delete_document(full_path, delete_file=delete_file)

    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete document")

    return {"success": True, "path": full_path, "deleted": True}


@router.get("/documents/{doc_path:path}/versions", dependencies=[Depends(require_auth)])
def get_document_versions(
    request: Request,
    doc_path: str,
) -> dict[str, Any]:
    """Get all versions of a document."""
    state = get_state(request)
    workspace_raw = state.settings.workspace
    workspace = str(workspace_raw) if not isinstance(workspace_raw, str) else workspace_raw

    pm = _get_pm_instance(workspace)

    if not pm.is_initialized():
        raise HTTPException(status_code=400, detail="PM system not initialized")

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


@router.get("/documents/{doc_path:path}/compare", dependencies=[Depends(require_auth)])
def compare_document_versions(
    request: Request,
    doc_path: str,
    old_version: str = Query(..., description="Old version number"),
    new_version: str = Query(..., description="New version number"),
) -> dict[str, Any]:
    """Compare two document versions."""
    state = get_state(request)
    workspace_raw = state.settings.workspace
    workspace = str(workspace_raw) if not isinstance(workspace_raw, str) else workspace_raw

    pm = _get_pm_instance(workspace)

    if not pm.is_initialized():
        raise HTTPException(status_code=400, detail="PM system not initialized")

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


@router.get("/search/documents", dependencies=[Depends(require_auth)])
def search_documents(
    request: Request,
    q: str = Query(..., description="Search query"),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    """Search documents by content or path."""
    state = get_state(request)
    workspace_raw = state.settings.workspace
    workspace = str(workspace_raw) if not isinstance(workspace_raw, str) else workspace_raw

    pm = _get_pm_instance(workspace)

    if not pm.is_initialized():
        raise HTTPException(status_code=400, detail="PM system not initialized")

    results = pm.search_documents(query=q, limit=limit)

    return {"query": q, "results": results, "count": len(results)}


# ===== Task Management Endpoints =====


@router.get("/tasks", dependencies=[Depends(require_auth)])
def list_tasks(
    request: Request,
    status: str | None = Query(None, description="Filter by status"),
    assignee: str | None = Query(None, description="Filter by assignee"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """List tasks with optional filtering."""
    state = get_state(request)
    workspace_raw = state.settings.workspace
    workspace = str(workspace_raw) if not isinstance(workspace_raw, str) else workspace_raw

    pm = _get_pm_instance(workspace)

    if not pm.is_initialized():
        raise HTTPException(status_code=400, detail="PM system not initialized")

    result = pm.list_tasks(status=status, assignee=assignee, limit=limit, offset=offset)
    return result


@router.get("/tasks/history", dependencies=[Depends(require_auth)])
def get_task_history(
    request: Request,
    task_id: str | None = Query(None, description="Filter by task ID"),
    assignee: str | None = Query(None, description="Filter by assignee"),
    status: str | None = Query(None, description="Filter by status"),
    start_date: str | None = Query(None, description="Start date (ISO format)"),
    end_date: str | None = Query(None, description="End date (ISO format)"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Get task history with filtering and pagination."""
    state = get_state(request)
    workspace_raw = state.settings.workspace
    workspace = str(workspace_raw) if not isinstance(workspace_raw, str) else workspace_raw

    pm = _get_pm_instance(workspace)

    if not pm.is_initialized():
        raise HTTPException(status_code=400, detail="PM system not initialized")

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


@router.get("/tasks/director", dependencies=[Depends(require_auth)])
def get_director_task_history(
    request: Request,
    iteration: int | None = Query(None, description="Filter by PM iteration number"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Get tasks that were dispatched to Director.

    This retrieves the task list sent to Director in each orchestration iteration.
    """
    state = get_state(request)
    workspace_raw = state.settings.workspace
    workspace = str(workspace_raw) if not isinstance(workspace_raw, str) else workspace_raw

    pm = _get_pm_instance(workspace)

    if not pm.is_initialized():
        raise HTTPException(status_code=400, detail="PM system not initialized")

    result = pm.get_director_task_history(iteration=iteration, limit=limit, offset=offset)
    return result


@router.get("/tasks/{task_id}", dependencies=[Depends(require_auth)])
def get_task(
    request: Request,
    task_id: str,
) -> dict[str, Any]:
    """Get a specific task by ID."""
    state = get_state(request)
    workspace_raw = state.settings.workspace
    workspace = str(workspace_raw) if not isinstance(workspace_raw, str) else workspace_raw

    pm = _get_pm_instance(workspace)

    if not pm.is_initialized():
        raise HTTPException(status_code=400, detail="PM system not initialized")

    task = pm.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    # Convert task to dict
    if hasattr(task, "__dict__"):
        task_dict = {
            "id": task.id,
            "title": task.title,
            "description": task.description,
            "status": task.status.value if hasattr(task.status, "value") else str(task.status),
            "priority": task.priority.value if hasattr(task.priority, "value") else str(task.priority),
            "assignee": task.assignee,
            "assignee_type": task.assignee_type.value
            if hasattr(task.assignee_type, "value")
            else str(task.assignee_type)
            if task.assignee_type
            else None,
            "requirements": task.requirements,
            "dependencies": task.dependencies,
            "estimated_effort": task.estimated_effort,
            "actual_effort": task.actual_effort,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
            "assigned_at": task.assigned_at,
            "started_at": task.started_at,
            "completed_at": task.completed_at,
            "result_summary": task.result_summary,
            "artifacts": task.artifacts,
            "metadata": task.metadata,
        }
    else:
        task_dict = dict(task)

    return task_dict


@router.get("/tasks/{task_id}/assignments", dependencies=[Depends(require_auth)])
def get_task_assignments(
    request: Request,
    task_id: str,
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    """Get assignment history for a task."""
    state = get_state(request)
    workspace_raw = state.settings.workspace
    workspace = str(workspace_raw) if not isinstance(workspace_raw, str) else workspace_raw

    pm = _get_pm_instance(workspace)

    if not pm.is_initialized():
        raise HTTPException(status_code=400, detail="PM system not initialized")

    assignments = pm.get_task_assignments(task_id=task_id, limit=limit)

    return {"task_id": task_id, "assignments": assignments, "count": len(assignments)}


@router.get("/search/tasks", dependencies=[Depends(require_auth)])
def search_tasks(
    request: Request,
    q: str = Query(..., description="Search query"),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    """Search tasks by title or description."""
    state = get_state(request)
    workspace_raw = state.settings.workspace
    workspace = str(workspace_raw) if not isinstance(workspace_raw, str) else workspace_raw

    pm = _get_pm_instance(workspace)

    if not pm.is_initialized():
        raise HTTPException(status_code=400, detail="PM system not initialized")

    results = pm.search_tasks(query=q, limit=limit)

    return {"query": q, "results": results, "count": len(results)}


# ===== Requirements Endpoints =====


@router.get("/requirements", dependencies=[Depends(require_auth)])
def list_requirements(
    request: Request,
    status: str | None = Query(None, description="Filter by status"),
    priority: str | None = Query(None, description="Filter by priority"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """List requirements with optional filtering."""
    state = get_state(request)
    workspace_raw = state.settings.workspace
    workspace = str(workspace_raw) if not isinstance(workspace_raw, str) else workspace_raw

    pm = _get_pm_instance(workspace)

    if not pm.is_initialized():
        raise HTTPException(status_code=400, detail="PM system not initialized")

    result = pm.list_requirements(status=status, priority=priority, limit=limit, offset=offset)
    return result


@router.get("/requirements/{req_id}", dependencies=[Depends(require_auth)])
def get_requirement(
    request: Request,
    req_id: str,
) -> dict[str, Any]:
    """Get a specific requirement by ID."""
    state = get_state(request)
    workspace_raw = state.settings.workspace
    workspace = str(workspace_raw) if not isinstance(workspace_raw, str) else workspace_raw

    pm = _get_pm_instance(workspace)

    if not pm.is_initialized():
        raise HTTPException(status_code=400, detail="PM system not initialized")

    req = pm.get_requirement(req_id)
    if req is None:
        raise HTTPException(status_code=404, detail=f"Requirement not found: {req_id}")

    return req


# ===== Project Status & Health =====


@router.get("/status", dependencies=[Depends(require_auth)])
def get_pm_status(request: Request) -> dict[str, Any]:
    """Get PM system status."""
    state = get_state(request)
    workspace_raw = state.settings.workspace
    workspace = str(workspace_raw) if not isinstance(workspace_raw, str) else workspace_raw

    pm = _get_pm_instance(workspace)

    if not pm.is_initialized():
        return {"initialized": False, "workspace": workspace}

    return pm.get_status()


@router.get("/health", dependencies=[Depends(require_auth)])
def get_pm_health(request: Request) -> dict[str, Any]:
    """Get project health analysis."""
    state = get_state(request)
    workspace_raw = state.settings.workspace
    workspace = str(workspace_raw) if not isinstance(workspace_raw, str) else workspace_raw

    pm = _get_pm_instance(workspace)

    if not pm.is_initialized():
        raise HTTPException(status_code=400, detail="PM system not initialized")

    return pm.analyze_project_health()


@router.post("/init", dependencies=[Depends(require_auth)])
def init_pm(
    request: Request,
    project_name: str = Query("", description="Project name"),
    description: str = Query("", description="Project description"),
) -> dict[str, Any]:
    """Initialize PM system for the workspace."""
    state = get_state(request)
    workspace_raw = state.settings.workspace
    workspace = str(workspace_raw) if not isinstance(workspace_raw, str) else workspace_raw

    pm = _get_pm_instance(workspace)

    if pm.is_initialized():
        return {"initialized": True, "message": "PM system already initialized", "workspace": workspace}

    result = pm.initialize(project_name=project_name or "Unnamed Project", description=description)
    return result
