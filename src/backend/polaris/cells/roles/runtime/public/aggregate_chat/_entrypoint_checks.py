"""Entrypoint existence/wiring checks for aggregate runtime audit."""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
from typing import Any

from polaris.cells.roles.runtime.public.aggregate_chat._specs import (
    _BACKEND_ROOT,
    _ENTRYPOINT_MODULE_ALIASES,
)
from polaris.cells.roles.runtime.public.contracts import (
    AggregateRuntimeEntrypointCheckV1,
)


def _load_session_public_symbol(name: str) -> object:
    from polaris.cells.roles.session import public as session_public

    value = getattr(session_public, name)
    globals()[name] = value
    return value


def _dedupe_tokens(values: Any) -> tuple[str, ...]:
    tokens: list[str] = []
    seen: set[str] = set()
    try:
        iterator = iter(values)
    except TypeError:
        return ()
    for item in iterator:
        token = str(item or "").strip()
        if token and token not in seen:
            tokens.append(token)
            seen.add(token)
    return tuple(tokens)


def _module_exists(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, AttributeError, ValueError):
        return False


def _file_check(entrypoint: str, path: Path) -> AggregateRuntimeEntrypointCheckV1:
    ok = path.exists()
    return AggregateRuntimeEntrypointCheckV1(
        entrypoint=entrypoint,
        check_type="file",
        ok=ok,
        evidence=str(path),
        reason="exists" if ok else "missing",
    )


def _module_check(entrypoint: str, module_name: str) -> AggregateRuntimeEntrypointCheckV1:
    ok = _module_exists(module_name)
    return AggregateRuntimeEntrypointCheckV1(
        entrypoint=entrypoint,
        check_type="module",
        ok=ok,
        evidence=module_name,
        reason="import_spec_found" if ok else "import_spec_missing",
    )


def _attribute_check(entrypoint: str, *, module_name: str, attribute: str) -> AggregateRuntimeEntrypointCheckV1:
    reason = "attribute_found"
    ok = False
    try:
        module = importlib.import_module(module_name)
        target: Any = module
        for part in attribute.split("."):
            target = getattr(target, part)
        ok = True
    except (AttributeError, ImportError, ValueError) as exc:
        reason = f"attribute_missing:{exc.__class__.__name__}"
    return AggregateRuntimeEntrypointCheckV1(
        entrypoint=entrypoint,
        check_type="attribute",
        ok=ok,
        evidence=f"{module_name}:{attribute}",
        reason=reason,
    )


def _route_check(entrypoint: str) -> AggregateRuntimeEntrypointCheckV1:
    route_path = _BACKEND_ROOT / "polaris" / "delivery" / "http" / "routers" / "aggregate_chat.py"
    ok = False
    reason = "missing"
    if route_path.exists():
        with open(route_path, encoding="utf-8") as handle:
            source = handle.read()
        ok = '@router.post("/v1/chat/completions"' in source
        reason = "route_declared" if ok else "route_missing"
    return AggregateRuntimeEntrypointCheckV1(
        entrypoint=entrypoint,
        check_type="http_route",
        ok=ok,
        evidence=str(route_path),
        reason=reason,
    )


def _graph_cell_check(entrypoint: str) -> AggregateRuntimeEntrypointCheckV1:
    graph_path = _BACKEND_ROOT / "docs" / "graph" / "catalog" / "cells.yaml"
    cell_id = entrypoint.split(":", 1)[1].strip() if ":" in entrypoint else ""
    ok = False
    reason = "missing"
    if graph_path.exists() and cell_id:
        with open(graph_path, encoding="utf-8") as handle:
            source = handle.read()
        ok = cell_id in source
        reason = "cell_id_declared" if ok else "cell_id_missing"
    return AggregateRuntimeEntrypointCheckV1(
        entrypoint=entrypoint,
        check_type="graph_cell",
        ok=ok,
        evidence=str(graph_path),
        reason=reason,
    )


def _generated_pack_check(entrypoint: str) -> AggregateRuntimeEntrypointCheckV1:
    matches = tuple((_BACKEND_ROOT / "polaris" / "cells").glob(f"**/{entrypoint}"))
    ok = bool(matches)
    return AggregateRuntimeEntrypointCheckV1(
        entrypoint=entrypoint,
        check_type="generated_pack",
        ok=ok,
        evidence=str(matches[0]) if matches else str(_BACKEND_ROOT / "polaris" / "cells" / "**" / entrypoint),
        reason="pack_found" if ok else "pack_missing",
    )


def _workspace_runtime_path_check(workspace: str, entrypoint: str) -> AggregateRuntimeEntrypointCheckV1:
    workspace_path = Path(workspace).expanduser()
    ok = workspace_path.exists()
    return AggregateRuntimeEntrypointCheckV1(
        entrypoint=entrypoint,
        check_type="workspace_runtime_path",
        ok=ok,
        evidence=str(workspace_path / entrypoint),
        reason="workspace_root_exists_path_created_on_demand" if ok else "workspace_root_missing",
    )


def _roles_runtime_check(entrypoint: str) -> AggregateRuntimeEntrypointCheckV1:
    attribute = entrypoint.removeprefix("roles.runtime.")
    class_check = _attribute_check(
        entrypoint,
        module_name="polaris.cells.roles.runtime.public.service",
        attribute=f"RoleRuntimeService.{attribute}",
    )
    if class_check.ok:
        return class_check
    module_check = _attribute_check(
        entrypoint,
        module_name="polaris.cells.roles.runtime.public.service",
        attribute=attribute,
    )
    if module_check.ok:
        return module_check
    return class_check


def _roles_kernel_public_check(entrypoint: str) -> AggregateRuntimeEntrypointCheckV1:
    attribute = entrypoint.removeprefix("roles.kernel.")
    return _attribute_check(
        entrypoint,
        module_name="polaris.cells.roles.kernel.public.service",
        attribute=attribute,
    )


def _factory_cognitive_runtime_check(entrypoint: str) -> AggregateRuntimeEntrypointCheckV1:
    attribute = entrypoint.removeprefix("factory.cognitive_runtime.")
    return _attribute_check(
        entrypoint,
        module_name="polaris.cells.factory.cognitive_runtime.public.service",
        attribute=f"CognitiveRuntimePublicService.{attribute}",
    )


def _public_context_adapter_check(entrypoint: str) -> AggregateRuntimeEntrypointCheckV1:
    attribute = entrypoint.removeprefix("roles.runtime.public.context_adapter.")
    return _attribute_check(
        entrypoint,
        module_name="polaris.cells.roles.runtime.public.context_adapter",
        attribute=attribute,
    )


def _check_aggregate_entrypoint(workspace: str, entrypoint: str) -> AggregateRuntimeEntrypointCheckV1:
    token = str(entrypoint or "").strip()
    if token.startswith("docs.graph.catalog.cells:"):
        return _graph_cell_check(token)
    if token.startswith("docs/"):
        return _file_check(token, _BACKEND_ROOT / token)
    if token == "delivery.http./v1/chat/completions":
        return _route_check(token)
    if token.startswith("runtime/"):
        return _workspace_runtime_path_check(workspace, token)
    if token.startswith("generated/"):
        return _generated_pack_check(token)
    if token.startswith("roles.runtime.public.context_adapter."):
        return _public_context_adapter_check(token)
    if token.startswith("roles.runtime."):
        return _roles_runtime_check(token)
    if token.startswith("roles.kernel.RoleExecutionKernel"):
        return _roles_kernel_public_check(token)
    if token.startswith("factory.cognitive_runtime.") and token != "factory.cognitive_runtime.receipts":
        return _factory_cognitive_runtime_check(token)
    module_name = _ENTRYPOINT_MODULE_ALIASES.get(token)
    if module_name is None and token.startswith("polaris."):
        module_name = token
    if module_name is not None:
        return _module_check(token, module_name)
    return AggregateRuntimeEntrypointCheckV1(
        entrypoint=token,
        check_type="unresolved",
        ok=False,
        evidence=token,
        reason="no_entrypoint_resolver",
    )
