from __future__ import annotations

import builtins
from typing import Any

import pytest
from polaris.kernelone.cognitive.telemetry import CognitiveTelemetry, NoOpSpan


def test_cognitive_telemetry_disables_when_opentelemetry_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_import = builtins.__import__

    def blocked_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.startswith("opentelemetry"):
            raise ImportError(name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)

    telemetry = CognitiveTelemetry(enabled=True)
    span = telemetry.start_span("test.span")

    assert telemetry.enabled is False
    assert isinstance(span, NoOpSpan)
