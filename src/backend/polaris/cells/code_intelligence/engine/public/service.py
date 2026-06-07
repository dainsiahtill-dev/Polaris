"""Public service exports for `code_intelligence.engine` cell."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from polaris.cells.code_intelligence.engine.public.contracts import (
    AstDependencyVerificationResultV1,
    VerifyAstDependencyQueryV1,
)
from polaris.kernelone.fs.registry import get_default_adapter
from polaris.kernelone.fs.runtime import KernelFileSystem
from polaris.kernelone.llm.toolkit.executor.handlers.treesitter import TreeSitterSymbolHandler


def _mapping_tuple(value: Any) -> tuple[Mapping[str, Any], ...]:
    if value is None or isinstance(value, str | bytes):
        return ()
    if isinstance(value, Iterable):
        rows: list[Mapping[str, Any]] = []
        for item in value:
            if isinstance(item, Mapping):
                rows.append(dict(item))
        return tuple(rows)
    return ()


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        token = value.strip()
        return (token,) if token else ()
    if isinstance(value, Iterable):
        rows: list[str] = []
        seen: set[str] = set()
        for item in value:
            token = str(item or "").strip()
            if token and token not in seen:
                rows.append(token)
                seen.add(token)
        return tuple(rows)
    token = str(value).strip()
    return (token,) if token else ()


def verify_ast_dependency(query: VerifyAstDependencyQueryV1) -> AstDependencyVerificationResultV1:
    """Verify a symbol/dependency target through the code intelligence public contract."""
    if not isinstance(query, VerifyAstDependencyQueryV1):
        raise TypeError("query must be VerifyAstDependencyQueryV1")

    kernel_fs = KernelFileSystem(query.workspace, get_default_adapter())
    raw = TreeSitterSymbolHandler(kernel_fs).find_symbol(
        language=query.language,
        file=query.path,
        symbol=query.symbol,
        kind=query.kind,
        max_results=query.max_results,
        fuzzy=query.fuzzy,
        context_radius=query.context_radius,
    )
    results = _mapping_tuple(raw.get("results"))
    return AstDependencyVerificationResultV1(
        ok=bool(raw.get("ok", False)),
        workspace=query.workspace,
        path=str(raw.get("file") or query.path).strip(),
        language=str(raw.get("language") or query.language).strip(),
        symbol=str(raw.get("symbol") or query.symbol).strip(),
        results=results,
        engine=str(raw.get("engine") or "").strip(),
        warnings=_string_tuple(raw.get("warnings")),
        error=str(raw.get("error") or "").strip(),
        metadata={
            "suggestion": str(raw.get("suggestion") or "").strip(),
            "total_found": int(raw.get("total_found") or len(results)),
        },
    )


__all__ = [
    "AstDependencyVerificationResultV1",
    "VerifyAstDependencyQueryV1",
    "verify_ast_dependency",
]
