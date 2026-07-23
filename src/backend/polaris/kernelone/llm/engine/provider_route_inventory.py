"""Closed Factory provider-route inventory.

Factory-bound role inference may use only provider routes whose concrete
physical attempt is known to cross the injected physical-dispatch port.  The
inventory is deliberately static: runtime registration, provider capability
self-declaration, aliases, or plugin discovery cannot promote an opaque route.
Administrative health/list-model calls are outside this policy because they do
not carry a Factory physical-dispatch port.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .contracts import PhysicalProviderDispatchRuntimePort
from .provider_native_request import FactoryProviderDispatchMode

FactoryProviderRouteClass = Literal["governed_supported", "factory_disabled_opaque"]

_GOVERNED_SUPPORTED: FactoryProviderRouteClass = "governed_supported"
_FACTORY_DISABLED_OPAQUE: FactoryProviderRouteClass = "factory_disabled_opaque"


def _normalize_provider_type(provider_type: str) -> str:
    token = str(provider_type or "").strip().lower()
    return "codex_cli" if token == "cli" else token


@dataclass(frozen=True, slots=True)
class FactoryProviderRouteInventoryEntryV1:
    provider_type: str
    invoke: FactoryProviderRouteClass
    stream: FactoryProviderRouteClass

    def __post_init__(self) -> None:
        normalized = _normalize_provider_type(self.provider_type)
        if normalized != self.provider_type:
            raise ValueError("provider route inventory keys must be normalized")
        for value in (self.invoke, self.stream):
            if value not in {_GOVERNED_SUPPORTED, _FACTORY_DISABLED_OPAQUE}:
                raise ValueError("provider route inventory class is invalid")


# Every stable default ProviderManager type is listed exactly once.  A mode is
# promoted only after its concrete transport and every retry/reconnect path are
# proven to cross the physical-dispatch port.  In particular, an outer SDK or
# subprocess call is not sufficient evidence for hidden internal HTTP retries.
_FACTORY_PROVIDER_ROUTE_INVENTORY: tuple[FactoryProviderRouteInventoryEntryV1, ...] = (
    FactoryProviderRouteInventoryEntryV1(
        provider_type="anthropic_compat",
        invoke=_GOVERNED_SUPPORTED,
        stream=_GOVERNED_SUPPORTED,
    ),
    FactoryProviderRouteInventoryEntryV1(
        provider_type="codex_cli",
        invoke=_FACTORY_DISABLED_OPAQUE,
        stream=_FACTORY_DISABLED_OPAQUE,
    ),
    FactoryProviderRouteInventoryEntryV1(
        provider_type="codex_sdk",
        # The SDK owns its hidden HTTP/retry wire and no exact native-request
        # projection exists yet.  An injected outer callback is insufficient.
        invoke=_FACTORY_DISABLED_OPAQUE,
        stream=_FACTORY_DISABLED_OPAQUE,
    ),
    FactoryProviderRouteInventoryEntryV1(
        provider_type="gemini_api",
        # Gemini's contents/generationConfig body has no frozen closed-set
        # projection yet, so neither sync nor stream may enter Factory.
        invoke=_FACTORY_DISABLED_OPAQUE,
        stream=_FACTORY_DISABLED_OPAQUE,
    ),
    FactoryProviderRouteInventoryEntryV1(
        provider_type="gemini_cli",
        invoke=_FACTORY_DISABLED_OPAQUE,
        stream=_FACTORY_DISABLED_OPAQUE,
    ),
    FactoryProviderRouteInventoryEntryV1(
        provider_type="kimi",
        # Provider-specific retry/body behavior has no closed native projection.
        invoke=_FACTORY_DISABLED_OPAQUE,
        stream=_FACTORY_DISABLED_OPAQUE,
    ),
    FactoryProviderRouteInventoryEntryV1(
        provider_type="minimax",
        # Promote only after its exact invoke and stream wire shapes are projected.
        invoke=_FACTORY_DISABLED_OPAQUE,
        stream=_FACTORY_DISABLED_OPAQUE,
    ),
    FactoryProviderRouteInventoryEntryV1(
        provider_type="ollama",
        # Ollama can switch among /api/chat, /api/generate, and OpenAI-compat
        # bodies.  Until all variants have exact projections, fail closed.
        invoke=_FACTORY_DISABLED_OPAQUE,
        stream=_FACTORY_DISABLED_OPAQUE,
    ),
    FactoryProviderRouteInventoryEntryV1(
        provider_type="openai_compat",
        invoke=_GOVERNED_SUPPORTED,
        stream=_GOVERNED_SUPPORTED,
    ),
)

_INVENTORY_BY_PROVIDER_TYPE = {entry.provider_type: entry for entry in _FACTORY_PROVIDER_ROUTE_INVENTORY}
if len(_INVENTORY_BY_PROVIDER_TYPE) != len(_FACTORY_PROVIDER_ROUTE_INVENTORY):
    raise RuntimeError("factory provider route inventory contains duplicate provider types")


def factory_provider_route_inventory() -> tuple[FactoryProviderRouteInventoryEntryV1, ...]:
    """Return the immutable static inventory in deterministic order."""

    return _FACTORY_PROVIDER_ROUTE_INVENTORY


def classify_factory_provider_route(
    provider_type: str,
    *,
    mode: FactoryProviderDispatchMode,
) -> FactoryProviderRouteClass | None:
    """Return one static classification; unknown/dynamic routes stay unclassified."""

    if mode not in {"invoke", "stream"}:
        raise ValueError("factory provider dispatch mode is invalid")
    entry = _INVENTORY_BY_PROVIDER_TYPE.get(_normalize_provider_type(provider_type))
    if entry is None:
        return None
    return entry.invoke if mode == "invoke" else entry.stream


def factory_provider_route_policy_error(
    provider_type: str,
    *,
    mode: FactoryProviderDispatchMode,
    physical_dispatch_port: PhysicalProviderDispatchRuntimePort | None,
) -> str:
    """Return the stable fail-closed error for a Factory-bound provider route."""

    if physical_dispatch_port is None:
        return ""
    normalized = _normalize_provider_type(provider_type)
    classification = classify_factory_provider_route(normalized, mode=mode)
    if classification is None:
        return f"factory_provider_route_uninventoried:{normalized or 'missing'}:{mode}"
    if classification != _GOVERNED_SUPPORTED:
        return f"factory_provider_route_disabled_opaque:{normalized}:{mode}"
    return ""


def factory_provider_implementation_policy_error(
    provider_type: str,
    *,
    mode: FactoryProviderDispatchMode,
    physical_dispatch_port: PhysicalProviderDispatchRuntimePort | None,
    implementation_trusted: bool,
) -> str:
    """Reject same-name runtime replacements before their first outbound call."""

    if physical_dispatch_port is None:
        return ""
    if mode not in {"invoke", "stream"}:
        raise ValueError("factory provider dispatch mode is invalid")
    normalized = _normalize_provider_type(provider_type)
    if implementation_trusted is not True:
        return f"factory_provider_route_implementation_untrusted:{normalized or 'missing'}:{mode}"
    return ""


__all__ = [
    "FactoryProviderDispatchMode",
    "FactoryProviderRouteClass",
    "FactoryProviderRouteInventoryEntryV1",
    "classify_factory_provider_route",
    "factory_provider_implementation_policy_error",
    "factory_provider_route_inventory",
    "factory_provider_route_policy_error",
]
