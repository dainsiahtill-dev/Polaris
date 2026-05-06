"""Permissions API routes - 权限管理 API

提供权限检查、角色管理和策略管理的 REST API 端点。

API 设计:
- GET  /v2/permissions/check   - 检查权限
- GET  /v2/permissions/effective - 获取有效权限
- GET  /v2/permissions/roles   - 列出角色
- POST /v2/permissions/assign - 分配角色
- GET  /v2/permissions/policies - 列出策略
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from polaris.cells.policy.permission.public import (
    DecisionContext,
    PermissionService,
)
from polaris.cells.roles.profile.public import (
    Action,
    Resource,
    ResourceType,
    Subject,
    SubjectType,
)
from polaris.delivery.http.dependencies import require_auth
from polaris.delivery.http.routers._shared import StructuredHTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v2/permissions", tags=["permissions"])


# ═══════════════════════════════════════════════════════════════════════════
# Request/Response Models
# ═══════════════════════════════════════════════════════════════════════════


class SubjectInput(BaseModel):
    """主体输入"""

    type: str  # "role", "user", "service"
    id: str


class ResourceInput(BaseModel):
    """资源输入"""

    type: str  # "file", "directory", "tool", "api", "workspace", "task"
    pattern: str = "*"
    path: str | None = None


class PermissionCheckRequest(BaseModel):
    """权限检查请求"""

    subject: SubjectInput
    resource: ResourceInput
    action: str  # "read", "write", "execute", "delete", "admin", "list"
    context: dict[str, Any] = {}


class PermissionCheckResponse(BaseModel):
    """权限检查响应"""

    allowed: bool
    decision: str
    matched_policies: list[str]
    reason: str


class RoleInfo(BaseModel):
    """角色信息"""

    id: str
    display_name: str
    description: str
    permission_count: int
    inherits_from: list[str]
    priority: int


class RoleListResponse(BaseModel):
    """角色列表响应"""

    roles: list[RoleInfo]


class RoleAssignRequest(BaseModel):
    """角色分配请求"""

    subject_type: str  # "role", "user", "service"
    subject_id: str
    role_id: str


class RoleAssignResponse(BaseModel):
    """角色分配响应"""

    assigned: bool
    subject: dict[str, str]
    role_id: str


class EffectivePermissionsResponse(BaseModel):
    """有效权限响应"""

    subject: dict[str, str]
    permissions: list[str]


class PolicyInfo(BaseModel):
    """策略信息"""

    id: str
    name: str
    effect: str
    subjects: list[dict[str, str]]
    resources: list[dict[str, Any]]
    actions: list[str]
    priority: int
    enabled: bool


class PolicyListResponse(BaseModel):
    """策略列表响应"""

    policies: list[PolicyInfo]


# ═══════════════════════════════════════════════════════════════════════════
# API Endpoints
# ═══════════════════════════════════════════════════════════════════════════


async def _get_permission_service(response_model=PermissionCheckResponse, dependencies=None) -> None:
    if dependencies is None:
        dependencies = [Depends(require_auth)]
    pass


@router.post("/check", response_model=PermissionCheckResponse, dependencies=[Depends(require_auth)])  # DEPRECATED
async def check_permission(
    req: PermissionCheckRequest,
    permission_service: PermissionService = Depends(_get_permission_service),
) -> PermissionCheckResponse:
    """检查权限

    评估主体对资源的操作权限。

    示例请求:
    ```json
    {
      "subject": {"type": "role", "id": "director"},
      "resource": {"type": "file", "pattern": "**/*.py"},
      "action": "write",
      "context": {}
    }
    ```
    """
    try:
        # 转换输入
        subject = Subject(
            type=SubjectType(req.subject.type),
            id=req.subject.id,
        )
        resource = Resource(
            type=ResourceType(req.resource.type),
            pattern=req.resource.pattern,
            path=req.resource.path,
        )
        action = Action(req.action)

        # 创建决策上下文
        context = DecisionContext(
            task_id=req.context.get("task_id"),
            session_id=req.context.get("session_id"),
        )

        # 执行权限检查
        result = await permission_service.check_permission(
            subject=subject,
            resource=resource,
            action=action,
            context=context,
        )

        return PermissionCheckResponse(
            allowed=result.allowed,
            decision=result.decision,
            matched_policies=result.matched_policies,
            reason=result.reason,
        )

    except ValueError as e:
        logger.error("Permission check invalid request: %s", e)
        raise StructuredHTTPException(status_code=400, code="INVALID_REQUEST", message="invalid request") from e
    except RuntimeError as e:
        logger.error("Permission check failed: %s", e)
        raise StructuredHTTPException(status_code=500, code="INTERNAL_ERROR", message="internal error") from e


@router.get(
    "/effective", response_model=EffectivePermissionsResponse, dependencies=[Depends(require_auth)]
)  # DEPRECATED
async def get_effective_permissions(
    subject_type: str = "role",
    subject_id: str = "pm",
    permission_service: PermissionService = Depends(_get_permission_service),
) -> EffectivePermissionsResponse:
    """获取主体的有效权限列表

    包括直接权限和继承的权限。
    """
    try:
        subject = Subject(
            type=SubjectType(subject_type),
            id=subject_id,
        )

        permissions = await permission_service.get_effective_permissions(subject)

        return EffectivePermissionsResponse(
            subject={"type": subject_type, "id": subject_id},
            permissions=permissions,
        )

    except ValueError as e:
        logger.error("Get effective permissions invalid request: %s", e)
        raise StructuredHTTPException(status_code=400, code="INVALID_REQUEST", message="invalid request") from e
    except RuntimeError as e:
        logger.error("Get effective permissions failed: %s", e)
        raise StructuredHTTPException(status_code=500, code="INTERNAL_ERROR", message="internal error") from e


@router.get("/roles", response_model=RoleListResponse, dependencies=[Depends(require_auth)])  # DEPRECATED
async def list_roles(
    permission_service: PermissionService = Depends(_get_permission_service),
) -> RoleListResponse:
    """列出所有角色及其权限统计"""
    try:
        roles_data = await permission_service.list_roles()

        roles = [
            RoleInfo(
                id=r["id"],
                display_name=r["display_name"],
                description=r["description"],
                permission_count=r["permission_count"],
                inherits_from=r["inherits_from"],
                priority=r["priority"],
            )
            for r in roles_data
        ]

        return RoleListResponse(roles=roles)

    except (RuntimeError, ValueError) as e:
        logger.error("List roles failed: %s", e)
        raise StructuredHTTPException(status_code=500, code="INTERNAL_ERROR", message="internal error") from e


@router.post("/assign", response_model=RoleAssignResponse, dependencies=[Depends(require_auth)])  # DEPRECATED
async def assign_role(
    req: RoleAssignRequest,
    permission_service: PermissionService = Depends(_get_permission_service),
) -> RoleAssignResponse:
    """分配角色给主体

    示例请求:
    ```json
    {
      "subject_type": "user",
      "subject_id": "user-123",
      "role_id": "pm"
    }
    ```
    """
    try:
        result = await permission_service.assign_role(
            subject_type=SubjectType(req.subject_type),
            subject_id=req.subject_id,
            role_id=req.role_id,
        )

        return RoleAssignResponse(
            assigned=result["assigned"],
            subject=result["subject"],
            role_id=result["role_id"],
        )

    except ValueError as e:
        logger.error("Assign role invalid request: %s", e)
        raise StructuredHTTPException(status_code=400, code="INVALID_REQUEST", message="invalid request") from e
    except RuntimeError as e:
        logger.error("Assign role failed: %s", e)
        raise StructuredHTTPException(status_code=500, code="INTERNAL_ERROR", message="internal error") from e


@router.get("/policies", response_model=PolicyListResponse, dependencies=[Depends(require_auth)])  # DEPRECATED
async def list_policies(
    permission_service: PermissionService = Depends(_get_permission_service),
) -> PolicyListResponse:
    """列出所有权限策略"""
    try:
        policies_data = permission_service.list_policies()

        policies = [
            PolicyInfo(
                id=p["id"],
                name=p["name"],
                effect=p["effect"],
                subjects=p["subjects"],
                resources=p["resources"],
                actions=p["actions"],
                priority=p["priority"],
                enabled=p["enabled"],
            )
            for p in policies_data
        ]

        return PolicyListResponse(policies=policies)

    except (RuntimeError, ValueError) as e:
        logger.error("List policies failed: %s", e)
        raise StructuredHTTPException(status_code=500, code="INTERNAL_ERROR", message="internal error") from e


# ═══════════════════════════════════════════════════════════════════════════
# v2 API Endpoints (canonical)
# ═══════════════════════════════════════════════════════════════════════════


@router.post("/v2/check", response_model=PermissionCheckResponse, dependencies=[Depends(require_auth)])
async def v2_check_permission(
    req: PermissionCheckRequest,
    permission_service: PermissionService = Depends(_get_permission_service),
) -> PermissionCheckResponse:
    """Check whether a subject is allowed to perform an action on a resource."""
    return await check_permission(req, permission_service)


@router.get("/v2/effective", response_model=EffectivePermissionsResponse, dependencies=[Depends(require_auth)])
async def v2_get_effective_permissions(
    subject_type: str = "role",
    subject_id: str = "pm",
    permission_service: PermissionService = Depends(_get_permission_service),
) -> EffectivePermissionsResponse:
    """Get effective permissions for a subject, including inherited roles."""
    return await get_effective_permissions(subject_type, subject_id, permission_service)


@router.get("/v2/roles", response_model=RoleListResponse, dependencies=[Depends(require_auth)])
async def v2_list_roles(
    permission_service: PermissionService = Depends(_get_permission_service),
) -> RoleListResponse:
    """List all roles with permission statistics."""
    return await list_roles(permission_service)


@router.post("/v2/assign", response_model=RoleAssignResponse, dependencies=[Depends(require_auth)])
async def v2_assign_role(
    req: RoleAssignRequest,
    permission_service: PermissionService = Depends(_get_permission_service),
) -> RoleAssignResponse:
    """Assign a role to a subject."""
    return await assign_role(req, permission_service)


@router.get("/v2/policies", response_model=PolicyListResponse, dependencies=[Depends(require_auth)])
async def v2_list_policies(
    permission_service: PermissionService = Depends(_get_permission_service),
) -> PolicyListResponse:
    """List all permission policies."""
    return await list_policies(permission_service)
