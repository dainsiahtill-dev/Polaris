"""ToolLoopController context-window resolution tests."""

from __future__ import annotations

from types import SimpleNamespace
from typing import NoReturn

from polaris.cells.roles.kernel.internal import tool_loop_controller
from polaris.cells.roles.kernel.internal.tool_loop_controller import ToolLoopController
from polaris.cells.roles.profile.public.service import RoleTurnRequest


def _build_controller(profile: object) -> ToolLoopController:
    request = RoleTurnRequest(
        workspace=".",
        message="ping",
        history=[],
    )
    return ToolLoopController.from_request(request=request, profile=profile)  # type: ignore[arg-type]


def test_effective_context_window_prefers_model_catalog(monkeypatch) -> None:
    profile = SimpleNamespace(
        role_id="pm",
        provider_id="",
        model="gpt-4.1",
        context_policy=SimpleNamespace(max_context_tokens=8000),
    )

    class _Catalog:
        def __init__(self, workspace: str) -> None:
            self.workspace = workspace

        def resolve(self, provider_id: str, model: str):
            assert provider_id == ""
            assert model == "gpt-4.1"
            return SimpleNamespace(max_context_tokens=128000)

    def _unexpected_binding(_role_id: str) -> tuple[str, str]:
        raise AssertionError("runtime binding should not be used when profile.model is set")

    monkeypatch.setattr(tool_loop_controller, "ModelCatalog", _Catalog)
    monkeypatch.setattr(tool_loop_controller, "get_role_model", _unexpected_binding)

    controller = _build_controller(profile)
    assert controller._effective_context_window_tokens() == 128000


def test_effective_context_window_uses_runtime_role_binding(monkeypatch) -> None:
    profile = SimpleNamespace(
        role_id="pm",
        provider_id="",
        model="",
        context_policy=SimpleNamespace(max_context_tokens=8000),
    )
    catalog_calls: list[tuple[str, str]] = []

    class _Catalog:
        def __init__(self, workspace: str) -> None:
            self.workspace = workspace

        def resolve(self, provider_id: str, model: str):
            catalog_calls.append((provider_id, model))
            return SimpleNamespace(max_context_tokens=200000)

    monkeypatch.setattr(tool_loop_controller, "ModelCatalog", _Catalog)
    monkeypatch.setattr(
        tool_loop_controller,
        "get_role_model",
        lambda _role_id: ("openai_compat", "gpt-4o"),
    )

    controller = _build_controller(profile)
    assert controller._effective_context_window_tokens() == 200000
    assert catalog_calls == [("openai_compat", "gpt-4o")]


def test_effective_context_window_falls_back_to_context_policy(monkeypatch) -> None:
    profile = SimpleNamespace(
        role_id="pm",
        provider_id="",
        model="",
        context_policy=SimpleNamespace(max_context_tokens=9000),
    )

    class _Catalog:
        def __init__(self, workspace: str) -> None:
            self.workspace = workspace

        def resolve(self, provider_id: str, model: str) -> NoReturn:
            raise AssertionError("model catalog should not run when model is unresolved")

    monkeypatch.setattr(tool_loop_controller, "ModelCatalog", _Catalog)
    monkeypatch.setattr(tool_loop_controller, "get_role_model", lambda _role_id: ("", ""))

    controller = _build_controller(profile)
    assert controller._effective_context_window_tokens() == 9000
