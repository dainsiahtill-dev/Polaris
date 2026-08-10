"""Stateless aggregate-chat planning subsystem for the ``roles.runtime`` cell.

This package is the lossless successor of the former ``aggregate_chat`` module.
It owns the lobe / integration / entrypoint spec tables, the plan and pack
builders, lobe selection / chaining helpers and the content renderers.

Everything here is stateless: the :class:`RoleRuntimeService` singleton, its
``_kernel_lock`` and the aggregate-chat *methods* remain in ``service.py``;
only the stateless helper *functions* those methods call live here. The import
direction is one-way: ``service.py`` imports from this package, never the
reverse.

All previously importable attributes (including private helpers and the
stdlib / typing names that were module-level attributes of the former module)
are re-exported so ``import ...public.aggregate_chat`` and
``from ...public.aggregate_chat import X`` keep resolving identically.
"""

from __future__ import annotations

import hashlib
import importlib
import json
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from polaris.cells.control_plane.run_ledger.public import (
    merge_failure_evidence_payload,
    suspected_files_from_failure_evidence_payload,
)
from polaris.cells.roles.runtime.public.aggregate_chat._entrypoint_checks import (
    _attribute_check,
    _check_aggregate_entrypoint,
    _dedupe_tokens,
    _factory_cognitive_runtime_check,
    _file_check,
    _generated_pack_check,
    _graph_cell_check,
    _load_session_public_symbol,
    _module_check,
    _module_exists,
    _public_context_adapter_check,
    _roles_kernel_public_check,
    _roles_runtime_check,
    _route_check,
    _workspace_runtime_path_check,
)
from polaris.cells.roles.runtime.public.aggregate_chat._execution import (
    _aggregate_execution_context,
    _aggregate_execution_metadata,
    _aggregate_handoff_from_result,
    _aggregate_history_from_messages,
    _build_aggregate_lobe_directive,
    _build_aggregate_lobe_turn_envelope,
    _render_aggregate_chain_content,
    _render_aggregate_execution_content,
    _render_aggregate_plan_content,
    _select_aggregate_execution_role,
    _select_aggregate_lobe_chain,
    _selected_message_index,
    _stable_completion_id,
    _summarize_aggregate_memory_pack,
)
from polaris.cells.roles.runtime.public.aggregate_chat._helpers import (
    _aggregate_objective_from_messages,
)
from polaris.cells.roles.runtime.public.aggregate_chat._memory import (
    _aggregate_max_lobe_turns,
    _aggregate_memory_current_facts,
    _aggregate_memory_recall_limit,
    _aggregate_memory_recall_query,
    _aggregate_memory_recall_triggers,
    _aggregate_phase_for_contextos,
    _build_aggregate_attention_candidates,
    _build_aggregate_contextos_attention_budget_pack,
    _build_aggregate_memory_recall_pack,
    _estimate_aggregate_text_tokens,
    _lobe_by_id,
    _lobe_has_current_role,
    _select_aggregate_execution_lobe,
)
from polaris.cells.roles.runtime.public.aggregate_chat._packs import (
    _build_aggregate_context_governance_pack,
    _build_aggregate_distilled_knowledge_pack,
    _build_aggregate_task_market_projection_pack,
    _distill_aggregate_lobe_result,
    _read_aggregate_generated_pack_summary,
    _serialize_context_budget,
    _serialize_distilled_knowledge_unit,
)
from polaris.cells.roles.runtime.public.aggregate_chat._plan import (
    _aggregate_plan_failure_evidence_payload,
    _build_aggregate_lobe,
    _build_cognitive_ledger,
    _build_compute_policy,
    _build_runtime_audit_result,
    _build_runtime_integrations,
    _build_takeover_directive,
    _build_takeover_evidence_status,
    _extract_failure_evidence,
    _extract_failure_signals,
    _normalize_failure_signal,
    _select_aggregate_role_ids,
)
from polaris.cells.roles.runtime.public.aggregate_chat._specs import (
    _AGGREGATE_FAILURE_EVIDENCE_KEYS,
    _AGGREGATE_FAILURE_SIGNAL_ALIASES,
    _AGGREGATE_LOBE_SPECS,
    _AGGREGATE_MODEL_ID,
    _AGGREGATE_RUNTIME_INTEGRATION_SPECS,
    _BACKEND_ROOT,
    _DEFAULT_AGGREGATE_ROLE_IDS,
    _ENTRYPOINT_MODULE_ALIASES,
    _SESSION_PUBLIC_EXPORTS,
)
from polaris.cells.roles.runtime.public.contracts import (
    AggregateChatCompletionsCommandV1,
    AggregateChatMessageV1,
    AggregateCognitiveLedgerEntryV1,
    AggregateRoleLobeV1,
    AggregateRolePlanResultV1,
    AggregateRuntimeAuditResultV1,
    AggregateRuntimeEntrypointCheckV1,
    AggregateRuntimeIntegrationV1,
    AggregateTakeoverDirectiveV1,
    BuildAggregateRolePlanQueryV1,
    RoleExecutionResultV1,
)
