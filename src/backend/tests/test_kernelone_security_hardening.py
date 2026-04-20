from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from polaris.cells.director.execution.public.tools import is_command_blocked
from polaris.kernelone.single_agent.role_framework.base import RoleBase, RoleInfo, RoleState
from polaris.kernelone.single_agent.role_framework.fastapi import FASTAPI_AVAILABLE, RoleFastAPI
from polaris.kernelone.llm.toolkit.native_function_calling import (
    NativeFunctionCallingHandler,
)
from polaris.kernelone.process.command_executor import (
    CommandExecutionService,
    CommandRequest,
)


class _DummyRole(RoleBase):
    def __init__(self, workspace: str) -> None:
        super().__init__(workspace=workspace, role_name="dummy")
        self._initialized = True
        self._state = RoleState.READY

    def get_info(self) -> RoleInfo:
        return RoleInfo(name="dummy", version="1.0.0", description="dummy role")

    def get_status(self) -> dict[str, Any]:
        return {
            "name": "dummy",
            "version": "1.0.0",
            "state": self.state.name,
        }

    def is_initialized(self) -> bool:
        return self._initialized

    def initialize(self, **kwargs: Any) -> dict[str, Any]:
        self._initialized = True
        return {"success": True, "kwargs": dict(kwargs)}


def test_is_command_blocked_rejects_shell_metacharacters() -> None:
    assert is_command_blocked("python -m pytest --version || whoami") is True
    assert is_command_blocked("pytest -q") is False


def test_command_executor_rejects_python_inline_execution(tmp_path: Path) -> None:
    service = CommandExecutionService(tmp_path)

    with pytest.raises(ValueError, match="inline execution flag"):
        service.build_subprocess_spec(
            CommandRequest(
                executable="python",
                args=["-c", "print('hello')"],
                cwd=".",
            )
        )


def test_command_executor_rejects_node_eval_execution(tmp_path: Path) -> None:
    service = CommandExecutionService(tmp_path)

    with pytest.raises(ValueError, match="inline execution flag"):
        service.build_subprocess_spec(
            CommandRequest(
                executable="node",
                args=["-e", "console.log('hello')"],
                cwd=".",
            )
        )


def test_command_executor_allows_repo_scoped_python_module(tmp_path: Path) -> None:
    service = CommandExecutionService(tmp_path)

    spec = service.build_subprocess_spec(
        CommandRequest(
            executable="python",
            args=["-m", "pytest", "--version"],
            cwd=".",
        )
    )

    assert spec["argv"][:3] == ["python", "-m", "pytest"]


def test_command_executor_allows_workspace_package_module(tmp_path: Path) -> None:
    package_root = tmp_path / "demo_app"
    delivery_root = package_root / "delivery"
    delivery_root.mkdir(parents=True, exist_ok=True)
    (package_root / "__init__.py").write_text("", encoding="utf-8")
    (delivery_root / "__init__.py").write_text("", encoding="utf-8")
    (delivery_root / "cli.py").write_text("def main() -> int:\n    return 0\n", encoding="utf-8")

    service = CommandExecutionService(tmp_path)

    spec = service.build_subprocess_spec(
        CommandRequest(
            executable="python",
            args=["-m", "demo_app.delivery.cli", "list"],
            cwd=".",
        )
    )

    assert spec["argv"][:3] == ["python", "-m", "demo_app.delivery.cli"]


def test_native_function_calling_rejects_malformed_tool_arguments(
    tmp_path: Path,
) -> None:
    handler = NativeFunctionCallingHandler(str(tmp_path))

    class _ExplodingExecutor:
        def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            raise AssertionError(f"tool should not execute: {name} {arguments}")

    handler.executor = _ExplodingExecutor()
    tool_calls = handler.parse_response(
        {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "function": {
                                    "name": "write_file",
                                    "arguments": '{"path":',
                                },
                            }
                        ]
                    }
                }
            ]
        }
    )

    results = handler.execute_tool_calls(tool_calls)

    assert len(results) == 1
    assert results[0].is_error is True
    assert "invalid JSON arguments" in str(results[0].output.get("error") or "")


@pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="FastAPI is not installed")
def test_role_fastapi_uses_spec_compliant_cors(tmp_path: Path) -> None:
    api = RoleFastAPI(_DummyRole, workspace=str(tmp_path))
    app = api.app

    cors_middleware = next(
        middleware for middleware in app.user_middleware
        if middleware.cls.__name__ == "CORSMiddleware"
    )

    assert cors_middleware.kwargs["allow_origins"] == ["*"]
    assert cors_middleware.kwargs["allow_credentials"] is False
