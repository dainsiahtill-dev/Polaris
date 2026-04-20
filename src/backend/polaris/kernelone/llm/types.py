from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .shared_contracts import Usage


@dataclass
class InvokeResult:
    ok: bool
    output: str
    latency_ms: int
    usage: Usage
    error: str | None = None
    raw: dict[str, Any] | None = None
    streaming: bool = False
    thinking: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "ok": self.ok,
            "output": self.output,
            "latency_ms": self.latency_ms,
            "usage": self.usage.to_dict(),
            "streaming": self.streaming,
        }
        if self.error:
            payload["error"] = self.error
        if self.raw is not None:
            payload["raw"] = self.raw
        if self.thinking is not None:
            payload["thinking"] = self.thinking
        return payload


@dataclass
class HealthResult:
    ok: bool
    latency_ms: int
    error: str | None = None
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"ok": self.ok, "latency_ms": self.latency_ms}
        if self.error:
            payload["error"] = self.error
        if self.details:
            payload["details"] = self.details
        return payload


@dataclass
class ModelInfo:
    id: str
    label: str | None = None
    raw: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"id": self.id}
        if self.label:
            payload["label"] = self.label
        if self.raw is not None:
            payload["raw"] = self.raw
        return payload


@dataclass
class ModelListResult:
    ok: bool
    models: list[ModelInfo]
    supported: bool = True
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "ok": self.ok,
            "supported": self.supported,
            "models": [m.to_dict() for m in self.models],
        }
        if self.error:
            payload["error"] = self.error
        return payload


# Re-export Usage.estimate as module-level function for backward compatibility.
# The canonical implementation lives in shared_contracts.Usage.estimate().
def estimate_usage(prompt: str, output: str) -> Usage:
    """Estimate token usage for a prompt/output pair.

    Note: This function is deprecated. Use Usage.estimate() instead.
    """
    return Usage.estimate(prompt, output)
