from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


def _require_non_empty(name: str, value: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} must be a non-empty string")
    return normalized


def _to_dict_copy(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(payload or {})


def _normalize_optional_string(value: str | None) -> str | None:
    if value is None:
        return None
    token = str(value).strip()
    return token or None


@dataclass(frozen=True)
class VerifyAstDependencyQueryV1:
    """Typed query for CE AST/symbol dependency verification."""

    workspace: str
    path: str
    language: str
    symbol: str
    kind: str | None = None
    max_results: int = 10
    context_radius: int = 5
    fuzzy: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "path", _require_non_empty("path", self.path))
        object.__setattr__(self, "language", _require_non_empty("language", self.language).lower())
        object.__setattr__(self, "symbol", _require_non_empty("symbol", self.symbol))
        object.__setattr__(self, "kind", _normalize_optional_string(self.kind))
        object.__setattr__(self, "max_results", max(1, min(int(self.max_results), 50)))
        object.__setattr__(self, "context_radius", max(0, min(int(self.context_radius), 20)))
        object.__setattr__(self, "fuzzy", bool(self.fuzzy))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class AstDependencyVerificationResultV1:
    """Typed result for AST/symbol dependency verification."""

    ok: bool
    workspace: str
    path: str
    language: str
    symbol: str
    results: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    engine: str = ""
    warnings: tuple[str, ...] = field(default_factory=tuple)
    error: str = ""
    result_count: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "ok", bool(self.ok))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "path", _require_non_empty("path", self.path))
        object.__setattr__(self, "language", _require_non_empty("language", self.language).lower())
        object.__setattr__(self, "symbol", _require_non_empty("symbol", self.symbol))
        object.__setattr__(self, "results", tuple(dict(item) for item in self.results))
        object.__setattr__(self, "engine", str(self.engine or "").strip())
        object.__setattr__(self, "warnings", tuple(str(item).strip() for item in self.warnings if str(item).strip()))
        object.__setattr__(self, "error", str(self.error or "").strip())
        object.__setattr__(self, "result_count", len(self.results))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


class CodeIntelligenceEngineErrorV1(RuntimeError):  # noqa: N818
    """Raised when `code_intelligence.engine` public contract processing fails."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "code_intelligence_engine_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(_require_non_empty("message", message))
        self.code = _require_non_empty("code", code)
        self.details = _to_dict_copy(details)


__all__ = [
    "AstDependencyVerificationResultV1",
    "CodeIntelligenceEngineErrorV1",
    "VerifyAstDependencyQueryV1",
]
