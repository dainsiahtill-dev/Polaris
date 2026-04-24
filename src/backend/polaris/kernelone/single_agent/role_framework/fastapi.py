"""Role Framework FastAPI - FastAPI接口适配器

自动生成角色的FastAPI接口。
"""

import logging
import os
from contextlib import asynccontextmanager

from pydantic import BaseModel

# FastAPI imports with fallback
try:
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.middleware.cors import CORSMiddleware

    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False
    # type: ignore[misc,assignment]
    FastAPI = object  # type: ignore[misc,assignment]
    HTTPException = Exception  # type: ignore[misc,assignment]

    def Query(*a, **k) -> None:  # type: ignore[misc,no-redef]  # noqa: N802
        return None


from .base import RoleBase

logger = logging.getLogger(__name__)


class InitRequest(BaseModel):
    """初始化请求"""

    name: str = ""
    description: str = ""


class RunRequest(BaseModel):
    """运行请求"""

    iterations: int = 1
    timeout: int | None = None


class RoleFastAPI:
    """角色FastAPI适配器

    为RoleBase子类自动生成FastAPI接口。

    用法:
        api = RoleFastAPI(MyRole, port=50000)
        api.run()
    """

    def __init__(
        self,
        role_class: type[RoleBase],
        host: str = "127.0.0.1",
        port: int = 50000,
        workspace: str = ".",
        title: str | None = None,
        description: str | None = None,
    ) -> None:
        if not FASTAPI_AVAILABLE:
            raise ImportError("FastAPI is required. Install with: pip install fastapi uvicorn")

        self.role_class = role_class
        self.host = host
        self.port = port
        self.workspace = os.path.abspath(workspace)
        self.title = title or f"{role_class.__name__} API"
        self.description = description or f"API for {role_class.__name__}"
        self._role_instances: dict[str, RoleBase] = {}
        self._app: FastAPI | None = None

    def _get_role(self, workspace: str | None = None) -> RoleBase:
        """获取角色实例"""
        ws = workspace or self.workspace
        ws_abs = os.path.abspath(ws)

        if ws_abs not in self._role_instances:
            # 使用类名作为默认 role_name
            default_role_name = self.role_class.__name__.lower()
            self._role_instances[ws_abs] = self.role_class(ws_abs, default_role_name)

        return self._role_instances[ws_abs]

    def _create_app(self) -> FastAPI:
        """创建FastAPI应用"""

        @asynccontextmanager
        async def lifespan(app: FastAPI):
            """应用生命周期"""
            logger.info(
                "%s API starting on %s:%s (workspace=%s)",
                self.role_class.__name__,
                self.host,
                self.port,
                self.workspace,
            )
            yield
            logger.info("%s API shutting down", self.role_class.__name__)

        app = FastAPI(
            title=self.title,
            description=self.description,
            version="1.0.0",
            lifespan=lifespan,
        )

        # CORS
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # Root endpoint
        @app.get("/")
        def root():
            role = self._get_role()
            info = role.get_info()
            return {
                "name": info.name,
                "version": info.version,
                "description": info.description,
                "workspace": self.workspace,
            }

        # Status endpoint
        @app.get("/status")
        def get_status():
            role = self._get_role()
            return role.get_status()

        # Init endpoint
        @app.post("/init")
        def init(req: InitRequest):
            role = self._get_role()

            if role.is_initialized():
                return {
                    "success": True,
                    "message": f"{role.role_name} already initialized",
                    "workspace": role.workspace,
                }

            result = role.initialize(name=req.name, description=req.description)
            return result

        # Health endpoint
        @app.get("/health")
        def get_health():
            role = self._get_role()

            if not role.is_initialized():
                raise HTTPException(status_code=400, detail="Not initialized")

            if hasattr(role, "get_health"):
                return role.get_health()
            else:
                return {
                    "state": role.state.name,
                    "initialized": role.is_initialized(),
                }

        # Capabilities endpoint
        @app.get("/capabilities")
        def get_capabilities():
            role = self._get_role()
            info = role.get_info()
            return {
                "capabilities": [c.name for c in info.capabilities],
            }

        # Run endpoint (if supported)
        @app.post("/run")
        def run(req: RunRequest):
            role = self._get_role()

            if not role.is_initialized():
                raise HTTPException(status_code=400, detail="Not initialized")

            if not hasattr(role, "run"):
                raise HTTPException(status_code=501, detail="Run not supported")

            try:
                result = role.run(iterations=req.iterations, timeout=req.timeout)
                return result
            except (RuntimeError, ValueError) as e:
                logger.error("Role run failed: %s", e)
                raise HTTPException(status_code=500, detail="internal error") from e

        self._app = app
        return app

    @property
    def app(self) -> FastAPI:
        """获取FastAPI应用实例"""
        if self._app is None:
            self._app = self._create_app()
        return self._app

    def run(self, reload: bool = False) -> None:
        """运行服务器"""
        import uvicorn

        logger.info(
            "Starting %s on %s:%s for workspace %s",
            self.title,
            self.host,
            self.port,
            self.workspace,
        )

        uvicorn.run(
            self.app,
            host=self.host,
            port=self.port,
            reload=reload,
        )


# Example usage
if __name__ == "__main__":
    from .base import RoleBase, RoleInfo

    class ExampleRole(RoleBase):
        def get_info(self) -> RoleInfo:
            return RoleInfo(
                name="example",
                version="1.0.0",
                description="Example role",
            )

        def get_status(self) -> dict:
            return {"name": self.role_name, "state": self.state.name}

        def is_initialized(self) -> bool:
            return True

        def initialize(self, **kwargs) -> dict:
            return {"success": True}

    api = RoleFastAPI(ExampleRole, port=50001)
    api.run()
