"""API v2 for new architecture."""

from fastapi import APIRouter
from polaris.delivery.http.routers.agent import router as agent_router
from polaris.delivery.ws.runtime_endpoint import router as runtime_ws_router

from .audit import router as audit_router  # 新增 - 审计路由
from .director import router as director_router
from .observability import router as observability_router
from .orchestration import router as orchestration_router
from .pm import router as pm_router
from .resident import router as resident_router
from .services import router as services_router

# Main v2 router
router = APIRouter(prefix="/v2")

# Include sub-routers
router.include_router(director_router)
router.include_router(pm_router)
router.include_router(resident_router)
router.include_router(runtime_ws_router)
router.include_router(services_router)
router.include_router(orchestration_router)
router.include_router(audit_router)  # 新增 - 挂载审计路由
router.include_router(observability_router)

# Include Agent router. Factory keeps its own /v2/factory prefix and is mounted
# directly by app_factory to avoid /v2/v2/factory route duplication.
router.include_router(agent_router)

__all__ = ["router"]
