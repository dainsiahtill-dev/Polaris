"""Architecture guards for retired omniscient audit compatibility aliases."""

from __future__ import annotations

from pathlib import Path

from polaris.kernelone.audit.omniscient import context_manager
from polaris.kernelone.audit.omniscient.interceptors import llm_interceptor

_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_CONTEXT_MANAGER = _BACKEND_ROOT / "polaris" / "kernelone" / "audit" / "omniscient" / "context_manager.py"
_LLM_INTERCEPTOR = (
    _BACKEND_ROOT / "polaris" / "kernelone" / "audit" / "omniscient" / "interceptors" / "llm_interceptor.py"
)
_RUNTIME_INVOKE = (
    _BACKEND_ROOT / "polaris" / "cells" / "llm" / "provider_runtime" / "internal" / "runtime_invoke.py"
)


def test_omniscient_context_manager_does_not_reexport_retired_aliases() -> None:
    """Unified audit context exports must use explicit current names."""
    source = _CONTEXT_MANAGER.read_text(encoding="utf-8")

    assert hasattr(context_manager, "UnifiedAuditContext")
    assert hasattr(context_manager, "audit_context_scope")
    assert not hasattr(context_manager, "AuditContext")
    assert not hasattr(context_manager, "AuditContextManager")
    assert not hasattr(context_manager, "AuditContextScope")
    assert "AuditContext = UnifiedAuditContext" not in source
    assert "AuditContextManager = _AuditContextScope" not in source
    assert "AuditContextScope = _AuditContextScope" not in source


def test_llm_interceptor_does_not_reexport_retired_audit_aliases() -> None:
    """LLM audit imports must use the concrete call interceptor/tracker names."""
    source = _LLM_INTERCEPTOR.read_text(encoding="utf-8")

    assert hasattr(llm_interceptor, "LLMCallInterceptor")
    assert hasattr(llm_interceptor, "LLMCallTracker")
    assert not hasattr(llm_interceptor, "LLMAuditTracker")
    assert "LLMAuditInterceptor = LLMCallInterceptor" not in source
    assert "LLMAuditTracker = LLMCallTracker" not in source
    assert '"LLMAuditTracker"' not in source


def test_provider_runtime_imports_llm_strategy_from_schema_source() -> None:
    """Provider runtime should not rely on incidental schema reexports."""
    source = _RUNTIME_INVOKE.read_text(encoding="utf-8")

    assert "from polaris.kernelone.audit.omniscient.schemas.llm_event import LLMStrategy" in source
    assert "LLMStrategy,\n)" not in source
