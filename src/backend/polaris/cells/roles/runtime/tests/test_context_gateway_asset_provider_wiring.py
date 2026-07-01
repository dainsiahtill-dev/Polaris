"""Runtime wiring for role-owned ContextGateway asset providers."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock


def test_context_gateway_config_is_kernel_public_contract() -> None:
    """Runtime must consume ContextGatewayConfig through the kernel public boundary."""
    from polaris.cells.roles.kernel.public.service import ContextGatewayConfig

    assert ContextGatewayConfig.__name__ == "ContextGatewayConfig"


def test_runtime_kernel_injects_ce_and_qa_context_asset_readers(monkeypatch: Any, tmp_path: Any) -> None:
    """Runtime-composed kernels mount CE/QA asset readers without kernel business imports."""
    from polaris.cells.roles.kernel.internal.kernel.context_gateway_config_builder import build_context_gateway_config
    from polaris.cells.roles.kernel.public.service import ContextGatewayConfig
    from polaris.cells.roles.runtime.public import service as runtime_service

    blueprint_result = object()
    verdict_result = object()
    calls: list[tuple[str, str, str]] = []

    def fake_blueprint_status(task_id: str, workspace: str) -> object:
        calls.append(("blueprint", task_id, workspace))
        return blueprint_result

    def fake_qa_verdict(task_id: str, workspace: str) -> object:
        calls.append(("qa", task_id, workspace))
        return verdict_result

    monkeypatch.setattr(runtime_service, "_read_blueprint_status_for_context", fake_blueprint_status)
    monkeypatch.setattr(runtime_service, "_read_qa_verdict_for_context", fake_qa_verdict)

    service = runtime_service.RoleRuntimeService()
    kernel = service._get_kernel(str(tmp_path))
    factory = getattr(kernel, "context_gateway_config_factory", None)

    assert factory is not None
    config = build_context_gateway_config(
        factory,
        "chief_engineer",
        MagicMock(),
        MagicMock(),
    )
    assert isinstance(config, ContextGatewayConfig)
    assert config.blueprint_overview_provider is not None
    assert config.verdict_history_provider is not None

    assert config.blueprint_overview_provider("task-1", str(tmp_path)) is blueprint_result
    assert config.verdict_history_provider("task-2", str(tmp_path)) is verdict_result
    assert calls == [
        ("blueprint", "task-1", str(tmp_path)),
        ("qa", "task-2", str(tmp_path)),
    ]
