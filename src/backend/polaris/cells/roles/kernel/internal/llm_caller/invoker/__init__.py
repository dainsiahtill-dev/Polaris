"""LLM Invoker Service package.

Unified service for LLM invocation that consolidates call, call_structured, and
call_stream functionality from the previous standalone modules.

This package is the lossless successor of the former ``invoker`` module. It
re-exports every previously-public symbol from the same import path so that
``import ...llm_caller.invoker`` and ``from ...llm_caller.invoker import X``
keep resolving identically for all external importers.

This is the service layer implementation that replaces:
- call_sync.py (call method)
- call_structured.py (call_structured method)
- call_stream.py (call_stream method)

Migration: 2026-03-31
"""

from __future__ import annotations

# Backward-compatible re-export of stdlib / typing / third-party names that were
# module-level attributes of the former single-file ``invoker`` module
# (preserves the full dir() surface oracle).
import asyncio
import contextlib
import copy
import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, replace
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

from polaris.cells.control_plane.run_ledger.public import (
    project_native_tool_call_envelopes_to_metadata,
)
from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import (
    FactoryRoleSemanticRequestIdentityV1,
    get_factory_role_evidence_authority_binding,
)
from polaris.kernelone.llm.engine import AIExecutor
from polaris.kernelone.llm.engine._executor_base import coerce_required_flag
from polaris.kernelone.llm.runtime_config import (
    RoleBindingSlot,
    clear_role_provider_override,
    get_role_binding_candidates,
    get_role_binding_override,
    get_role_provider_override,
    is_role_binding_healthy,
    mark_role_binding_unhealthy,
    set_role_binding_override,
    set_role_provider_override,
)
from polaris.kernelone.telemetry.debug_stream import emit_debug_event
from polaris.kernelone.tool_execution.contracts import canonicalize_tool_name

from ...llm_cache import get_global_llm_cache
from ..context_audit import (
    build_final_provider_request_snapshot,
    build_final_request_context_audit_for_request,
)
from ..error_handling import (
    ERROR_CATEGORY_CANCELLED,
    build_native_tool_unavailable_error,
    classify_error,
    is_response_format_unsupported,
    is_retryable_error,
)
from ..event_emitter import LLMEventEmitter
from ..factory_dispatch_propagation import (
    FactorySemanticDispatchPropagationPort,
    enforce_factory_aware_final_request_evidence_coverage,
)
from ..final_provider_attempt_qualification import (
    context_snapshot_matches_frozen_attempt,
    final_request_snapshot_evidence,
)
from ..final_request_metrics import validated_final_context_evidence
from ..helpers import (
    build_native_tool_call_envelope_payloads,
    extract_json_from_text,
    extract_native_tool_calls,
    native_tool_call_name,
    resolve_tool_call_provider,
)
from ..invoker_phases import FallbackLadderResult, read_response_status
from ..request_preparer import LLMRequestPreparer
from ..response_types import LLMResponse, PreparedLLMRequest, StructuredLLMResponse
from ..stream_engine import StreamEngine
from ..stream_handler import (
    normalize_stream_chunk,
    resolve_stream_runtime_config,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from polaris.cells.roles.kernel.internal.context_gateway import ContextRequest
    from polaris.cells.roles.profile.public.service import RoleProfile

from ._helpers import (
    INSTRUCTOR_AVAILABLE,
    _clear_context_snapshot_context,
    _invoke_executor_with_factory_dispatch,
    _physical_dispatch_port_for_request,
    _profile_lacks_forced_tool_choice,
    _recover_text_tool_calls_from_response_text,
    _required_tool_not_called_error,
    _store_active_request_context_snapshot,
)
from ._llm_invoker import LLMInvoker

# Preserve optional instructor binding exactly as the former module did.
# (helpers already probes availability; re-bind name onto this package.)
with contextlib.suppress(ImportError):
    from polaris.infrastructure.llm.instructor_client import create_structured_client

logger = logging.getLogger(__name__)

# Import-time side effect preserved from the former single-file module
# (exact message; package __name__ matches former module path).
logger.debug("LLMInvoker module loaded: __name__=%s", __name__)

__all__ = [
    "LLMInvoker",
    "_clear_context_snapshot_context",
    "_invoke_executor_with_factory_dispatch",
    "_physical_dispatch_port_for_request",
    "_profile_lacks_forced_tool_choice",
    "_recover_text_tool_calls_from_response_text",
    "_required_tool_not_called_error",
    "_store_active_request_context_snapshot",
]
