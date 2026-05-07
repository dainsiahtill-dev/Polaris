from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for candidate in (BACKEND_ROOT,):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from polaris.delivery.cli.pm import backend as pm_backend  # noqa: E402


def test_resolve_pm_backend_kind_honors_explicit_ollama(monkeypatch) -> None:
    monkeypatch.setattr(
        pm_backend,
        "_resolve_role_runtime_llm_config",
        lambda _state, _role: SimpleNamespace(provider_kind="generic"),
    )
    kind, _cfg = pm_backend.resolve_pm_backend_kind("ollama", SimpleNamespace())
    assert kind == "ollama"


def test_resolve_pm_backend_kind_honors_explicit_codex(monkeypatch) -> None:
    monkeypatch.setattr(
        pm_backend,
        "_resolve_role_runtime_llm_config",
        lambda _state, _role: SimpleNamespace(provider_kind="generic"),
    )
    kind, _cfg = pm_backend.resolve_pm_backend_kind("codex", SimpleNamespace())
    assert kind == "codex"


def test_resolve_pm_backend_kind_auto_uses_runtime_provider_for_ollama_role_mapping(monkeypatch) -> None:
    monkeypatch.setattr(
        pm_backend,
        "_resolve_role_runtime_llm_config",
        lambda _state, _role: SimpleNamespace(provider_kind="ollama"),
    )
    kind, _cfg = pm_backend.resolve_pm_backend_kind("auto", SimpleNamespace())
    assert kind == "generic"


def test_invoke_pm_backend_generic_prefers_runtime_provider(monkeypatch) -> None:
    state = SimpleNamespace(
        workspace_full=".",
        show_output=False,
        timeout=0,
        events_full="",
        ollama_full="",
        model="unused",
    )
    args = SimpleNamespace(
        codex_full_auto=True,
        codex_dangerous=False,
        codex_profile="",
    )
    monkeypatch.setattr(
        pm_backend,
        "_invoke_generic_runtime_provider",
        lambda **_: '{"tasks":[]}',
    )

    def _should_not_call_ollama(*_args, **_kwargs):
        raise AssertionError("invoke_ollama should not be called when runtime provider succeeds")

    monkeypatch.setattr(pm_backend, "invoke_ollama", _should_not_call_ollama)
    output = pm_backend.invoke_pm_backend(state, "prompt", "generic", args, usage_ctx=None)
    assert output == '{"tasks":[]}'


def test_generic_runtime_provider_allows_ollama_http_adapter(monkeypatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(pm_backend, "load_pm_model_config", lambda: ("ollama", "test-model"))

    def _fake_invoke_role_runtime_provider(**kwargs):
        captured["blocked_provider_types"] = tuple(kwargs.get("blocked_provider_types") or ())
        return SimpleNamespace(
            attempted=True,
            ok=True,
            output='{"tasks":[]}',
            provider_type="ollama",
            model="test-model",
            latency_ms=1,
            error="",
            usage=None,
        )

    monkeypatch.setattr(
        "polaris.kernelone.llm.runtime.invoke_role_runtime_provider",
        _fake_invoke_role_runtime_provider,
    )

    state = SimpleNamespace(
        workspace_full=".",
        timeout=0,
        events_full="",
    )

    output = pm_backend._invoke_generic_runtime_provider(state, "prompt", usage_ctx=None)

    assert output == '{"tasks":[]}'
    blocked_provider_types = captured["blocked_provider_types"]
    assert isinstance(blocked_provider_types, tuple)
    assert "ollama" not in blocked_provider_types


def test_invoke_pm_backend_generic_raises_on_empty_runtime_output(monkeypatch) -> None:
    state = SimpleNamespace(
        workspace_full=".",
        show_output=False,
        timeout=0,
        events_full="",
        ollama_full="",
        model="unused",
    )
    args = SimpleNamespace(
        codex_full_auto=True,
        codex_dangerous=False,
        codex_profile="",
    )
    monkeypatch.setattr(
        pm_backend,
        "_invoke_generic_runtime_provider",
        lambda **_: "",
    )
    monkeypatch.setattr(
        pm_backend,
        "invoke_ollama",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("invoke_ollama must not be used as an implicit fallback")
        ),
    )

    with pytest.raises(RuntimeError, match="empty response"):
        pm_backend.invoke_pm_backend(state, "prompt", "generic", args, usage_ctx=None)
