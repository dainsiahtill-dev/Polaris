"""Domain adapters for State-First Context OS."""

from __future__ import annotations

from .code import CodeContextDomainAdapter
from .contracts import (
    ContextDomainAdapter,
    ContextOSObservable,
    ContextOSObserver,
    DomainRoutingDecision,
    DomainStatePatchHints,
)
from .generic import GenericContextDomainAdapter

_BUILTIN_ADAPTERS: dict[str, ContextDomainAdapter] = {
    "generic": GenericContextDomainAdapter(),
    "code": CodeContextDomainAdapter(),
}


def get_context_domain_adapter(adapter_id: str | None = None) -> ContextDomainAdapter:
    token = str(adapter_id or "generic").strip().lower() or "generic"
    return _BUILTIN_ADAPTERS.get(token, _BUILTIN_ADAPTERS["generic"])


__all__ = [
    "CodeContextDomainAdapter",
    "ContextDomainAdapter",
    "ContextOSObservable",
    "ContextOSObserver",
    "DomainRoutingDecision",
    "DomainStatePatchHints",
    "GenericContextDomainAdapter",
    "get_context_domain_adapter",
]
