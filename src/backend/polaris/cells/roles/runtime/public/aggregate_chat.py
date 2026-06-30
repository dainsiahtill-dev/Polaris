"""Stateless aggregate-chat planning subsystem for the ``roles.runtime`` cell.

This module is a lossless extraction of the aggregate-chat planning subsystem
from :mod:`polaris.cells.roles.runtime.public.service`. It owns the lobe /
integration / entrypoint spec tables, the plan and pack builders, lobe
selection / chaining helpers and the content renderers.

Everything here is stateless: the :class:`RoleRuntimeService` singleton, its
``_kernel_lock`` and the aggregate-chat *methods* remain in ``service.py``;
only the stateless helper *functions* those methods call live here. The import
direction is one-way: ``service.py`` imports from this module, never the
reverse.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

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

_AGGREGATE_MODEL_ID = "polaris.aggregate_llm.v1"
_DEFAULT_AGGREGATE_ROLE_IDS: tuple[str, ...] = (
    "pm",
    "architect",
    "chief_engineer",
    "director",
    "qa",
)
_AGGREGATE_FAILURE_SIGNAL_ALIASES: dict[str, str] = {
    "compile": "compile_failure",
    "compiler_failure": "compile_failure",
    "compile_error": "compile_failure",
    "tsc_failure": "typecheck_failure",
    "type_error": "typecheck_failure",
    "typecheck": "typecheck_failure",
    "apply_failure": "failed_apply",
    "patch_apply_failure": "failed_apply",
    "repo_map_empty": "empty_repo_map",
    "localization": "localization_uncertain",
}
_AGGREGATE_FAILURE_EVIDENCE_KEYS: dict[str, tuple[str, ...]] = {
    "compile_failure": ("compiler_output", "changed_files", "test_command"),
    "typecheck_failure": ("typecheck_output", "tsconfig_path", "changed_files"),
    "failed_apply": ("patch_payload", "apply_error", "target_paths"),
    "localization_uncertain": ("repo_map", "mentioned_symbols", "candidate_files"),
    "degraded_signal": ("degraded_reason", "cognitive_runtime_preflight", "session_id"),
    "empty_repo_map": ("repo_intelligence_result", "workspace", "language_filter"),
    "graph_boundary_violation": ("cell_id", "target_paths", "graph_edge"),
}


_AGGREGATE_LOBE_SPECS: tuple[dict[str, Any], ...] = (
    {
        "lobe_id": "constraint_boundary_generator",
        "title": "Architect + QA constraint boundary generator",
        "phase": "preflight",
        "role_ids": ("architect", "qa"),
        "virtual_role_ids": (),
        "capability_refs": (
            "docs.graph.catalog.cells",
            "docs.graph.subgraphs.context_plane",
            "polaris.kernelone.context.context_os.StateFirstContextOS",
            "polaris.cells.factory.cognitive_runtime",
        ),
        "attention_masks": (
            "graph_boundary",
            "forbid_prompt_leakage",
            "verification_before_write",
        ),
        "memory_triggers": (),
        "compute_tier": "cloud_control",
        "handoff_keys": (
            "constraint_topology",
            "negative_prompt_masks",
            "graph_boundary_verdict",
        ),
        "takeover_triggers": (
            "graph_boundary_violation",
            "prompt_leakage_risk",
            "unverified_write_plan",
        ),
        "output_contract": "constraint_topology_v1",
    },
    {
        "lobe_id": "dialectic_self_heal_loop",
        "title": "Chief Engineer + adversarial critic reasoning loop",
        "phase": "blueprint_refinement",
        "role_ids": ("chief_engineer", "qa"),
        "virtual_role_ids": ("adversarial_critic",),
        "capability_refs": (
            "polaris.cells.chief_engineer.blueprint",
            "polaris.kernelone.cognitive.governance.verification",
            "polaris.kernelone.tool_execution.code_validator",
            "polaris.kernelone.cognitive.execution.rollback_manager",
        ),
        "attention_masks": (
            "edge_case_attack",
            "rollback_required",
            "test_gate_required",
        ),
        "memory_triggers": ("verification_failure", "compiler_failure"),
        "compute_tier": "cloud_critic_local_retry",
        "handoff_keys": (
            "self_healed_blueprint",
            "edge_case_matrix",
            "rollback_plan",
        ),
        "takeover_triggers": (
            "blueprint_incomplete",
            "compile_failure",
            "typecheck_failure",
        ),
        "output_contract": "self_healed_blueprint_v1",
    },
    {
        "lobe_id": "hippocampus_controller",
        "title": "Director + ContextOS/Akashic memory router",
        "phase": "execution_context_projection",
        "role_ids": ("director",),
        "virtual_role_ids": (),
        "capability_refs": (
            "polaris.kernelone.context.context_os.StateFirstContextOS",
            "polaris.kernelone.akashic.semantic_memory.AkashicSemanticMemory",
            "polaris.kernelone.context.repo_intelligence.RepoIntelligenceFacade",
            "polaris.cells.roles.session.public.contracts",
        ),
        "attention_masks": (
            "active_window",
            "stable_facts",
            "open_loops",
        ),
        "memory_triggers": ("degraded_signal", "localization_uncertain", "long_session"),
        "compute_tier": "local_memory_router",
        "handoff_keys": (
            "context_projection",
            "akashic_recall_pack",
            "repo_localization_candidates",
        ),
        "takeover_triggers": (
            "degraded_signal",
            "empty_repo_map",
            "localization_uncertain",
        ),
        "output_contract": "context_projection_v1",
    },
    {
        "lobe_id": "tool_commit_guard",
        "title": "Director + QA governed commit guard",
        "phase": "apply_and_verify",
        "role_ids": ("director", "qa"),
        "virtual_role_ids": (),
        "capability_refs": (
            "polaris.cells.roles.kernel.transaction.turn_ledger",
            "polaris.kernelone.llm.toolkit.tool_normalization",
            "polaris.kernelone.tool_execution.code_validator",
            "polaris.cells.factory.cognitive_runtime.handoff",
        ),
        "attention_masks": (
            "single_commit",
            "tool_policy",
            "change_set_validation",
        ),
        "memory_triggers": ("rollback", "failed_apply", "integration_qa_failure"),
        "compute_tier": "local_execution_guard",
        "handoff_keys": (
            "validated_change_set",
            "tool_receipt",
            "qa_gate_result",
        ),
        "takeover_triggers": (
            "unsafe_tool_call",
            "change_set_out_of_scope",
            "integration_qa_failure",
        ),
        "output_contract": "verified_patch_v1",
    },
    {
        "lobe_id": "task_market_allocator",
        "title": "PM + Director + QA runtime task allocator",
        "phase": "stage_handoff",
        "role_ids": ("pm", "director", "qa"),
        "virtual_role_ids": (),
        "capability_refs": (
            "polaris.cells.runtime.task_market",
            "polaris.cells.runtime.projection",
            "polaris.cells.orchestration.pm_dispatch",
            "polaris.cells.factory.cognitive_runtime.receipt",
        ),
        "attention_masks": (
            "task_contract_quality",
            "stage_owner",
            "receipt_required",
        ),
        "memory_triggers": ("task_requeue", "dead_letter", "handoff_gap"),
        "compute_tier": "control_plane",
        "handoff_keys": (
            "task_contract",
            "stage_owner_matrix",
            "runtime_receipt_ids",
        ),
        "takeover_triggers": (
            "task_quality_below_gate",
            "handoff_gap",
            "dead_letter",
        ),
        "output_contract": "runtime_handoff_matrix_v1",
    },
)
_AGGREGATE_RUNTIME_INTEGRATION_SPECS: tuple[dict[str, Any], ...] = (
    {
        "tech_id": "acga_graph_cell_governance",
        "title": "ACGA 2.0 Graph/Cell governance",
        "status": "wired",
        "priority": "p1",
        "production_entrypoints": (
            "docs/graph/catalog/cells.yaml",
            "roles.runtime.build_aggregate_role_plan",
            "delivery.http./v1/chat/completions",
        ),
        "trigger_keys": ("workspace", "domain", "role_ids"),
        "evidence_keys": ("graph_catalog_cell", "required_capability_refs", "graph_boundary_verdict"),
        "runtime_effects": ("constrain role/capability selection before aggregate execution",),
        "benefit": "Keeps aggregate-model planning inside graph-owned architecture boundaries.",
        "capability_refs": ("docs.graph.catalog.cells",),
    },
    {
        "tech_id": "kernelone_agent_os",
        "title": "KernelOne Agent OS foundation",
        "status": "wired",
        "priority": "p1",
        "production_entrypoints": (
            "roles.runtime.stream_chat_turn",
            "roles.kernel.RoleExecutionKernel",
            "kernelone.context",
        ),
        "trigger_keys": ("execution_mode=single_turn",),
        "evidence_keys": ("strategy_fingerprint", "context_override", "runtime_receipts"),
        "runtime_effects": ("execute selected concrete role through KernelOne-backed role runtime",),
        "benefit": "Turns role lobes into a shared runtime substrate instead of prompt-only agents.",
        "capability_refs": ("polaris.kernelone", "polaris.cells.roles.kernel"),
    },
    {
        "tech_id": "turn_transaction_kernel_ledger",
        "title": "Turn Transaction Kernel / Turn Ledger",
        "status": "wired",
        "priority": "p0",
        "production_entrypoints": (
            "roles.runtime.stream_chat_turn",
            "roles.kernel.internal.transaction_kernel",
            "roles.kernel.internal.transaction.ledger",
        ),
        "trigger_keys": ("execution_mode=single_turn", "tool_call"),
        "evidence_keys": ("tool_calls", "turn_events_metadata", "receipt_ids"),
        "runtime_effects": ("commit or reject tool effects through the normal turn transaction flow",),
        "benefit": "Prevents aggregate execution from becoming an unaudited multi-agent chat transcript.",
        "capability_refs": ("polaris.cells.roles.kernel.transaction.turn_ledger",),
    },
    {
        "tech_id": "context_plane_isolation",
        "title": "Context Plane isolation",
        "status": "wired",
        "priority": "p0",
        "production_entrypoints": (
            "roles.runtime._prepare_session_request",
            "roles.runtime._enforce_required_context_os",
            "kernelone.context.context_os",
        ),
        "trigger_keys": ("context_os_expected", "state_first_context_os_enabled"),
        "evidence_keys": ("context_os_preflight", "context_os_audit", "context_os_snapshot"),
        "runtime_effects": ("separate control metadata from model-visible context projection",),
        "benefit": "Reduces prompt pollution while preserving auditable control-plane decisions.",
        "capability_refs": ("polaris.kernelone.context.context_os.StateFirstContextOS",),
    },
    {
        "tech_id": "descriptor_context_verify_packs",
        "title": "Descriptor / Context Pack / Verify Pack split",
        "status": "wired",
        "priority": "p2",
        "production_entrypoints": (
            "roles.runtime._build_aggregate_context_governance_pack",
            "cells.context.catalog",
            "cells.context.engine",
            "generated/context.pack.json",
        ),
        "trigger_keys": ("context_request", "verification_scope"),
        "evidence_keys": ("descriptor_pack", "context_pack", "verify_pack"),
        "runtime_effects": ("inject descriptor, working context, and verification summaries into aggregate turns",),
        "benefit": "Keeps retrieval, work, and verification attention layers distinct.",
        "capability_refs": ("polaris.cells.context.catalog", "polaris.cells.context.engine"),
    },
    {
        "tech_id": "strategy_profile_overlay_fingerprint",
        "title": "Strategy Profile + Role Overlay + Fingerprint",
        "status": "wired",
        "priority": "p0",
        "production_entrypoints": (
            "roles.runtime.stream_chat_turn",
            "roles.runtime.create_strategy_run",
            "runtime/strategy_runs",
        ),
        "trigger_keys": ("execution_mode=single_turn", "domain", "role"),
        "evidence_keys": ("profile_id", "profile_hash", "bundle_id", "strategy_receipt_path"),
        "runtime_effects": ("emit per-turn strategy identity before role execution",),
        "benefit": "Makes each brain-lobe activation traceable to a concrete context/tool policy.",
        "capability_refs": ("polaris.kernelone.context.strategy_profiles",),
    },
    {
        "tech_id": "cognitive_runtime_receipt_handoff",
        "title": "Cognitive Runtime Receipt / Handoff Pack",
        "status": "wired",
        "priority": "p0",
        "production_entrypoints": (
            "roles.runtime._emit_cognitive_runtime_shadow_artifacts",
            "factory.cognitive_runtime.record_runtime_receipt",
            "factory.cognitive_runtime.export_handoff_pack",
        ),
        "trigger_keys": ("cognitive_runtime_required", "session_id"),
        "evidence_keys": ("cognitive_runtime_evidence", "receipt_id", "handoff_id"),
        "runtime_effects": ("record completed role turns and export cross-lobe handoff packs",),
        "benefit": "Provides hard evidence for memory transfer instead of relying on chat history.",
        "capability_refs": ("polaris.cells.factory.cognitive_runtime",),
    },
    {
        "tech_id": "session_continuity_engine",
        "title": "Session Continuity Engine",
        "status": "wired",
        "priority": "p1",
        "production_entrypoints": (
            "roles.runtime._project_host_history",
            "roles.runtime._persist_session_turn_state",
            "context.session_continuity",
        ),
        "trigger_keys": ("session_id", "history", "include_session_snapshot"),
        "evidence_keys": ("stable_facts", "open_loops", "recent_window", "session_turn_events"),
        "runtime_effects": ("persist and reload structured session state across aggregate calls",),
        "benefit": "Turns long-running aggregate chats into stateful memory projection, not raw history piles.",
        "capability_refs": ("polaris.kernelone.context.session_continuity",),
    },
    {
        "tech_id": "context_catalog_graph_semantic_retrieval",
        "title": "Context Catalog / Graph-constrained semantic retrieval",
        "status": "wired",
        "priority": "p1",
        "production_entrypoints": (
            "roles.runtime._build_aggregate_context_governance_pack",
            "cells.context.catalog",
            "cells.context.engine.internal.search_gateway",
        ),
        "trigger_keys": ("context_query", "cell_scope", "graph_boundary"),
        "evidence_keys": ("catalog_descriptor", "graph_scope", "retrieval_candidates"),
        "runtime_effects": ("run catalog search gateway before aggregate role execution",),
        "benefit": "Prevents semantic memory recall from crossing Cell ownership boundaries.",
        "capability_refs": ("polaris.cells.context.catalog",),
    },
    {
        "tech_id": "repo_intelligence_localizer",
        "title": "Repo Intelligence / Repo Map Localizer",
        "status": "wired",
        "priority": "p1",
        "production_entrypoints": (
            "roles.runtime._build_session_request",
            "roles.runtime.public.context_adapter.augment_context_with_repo_intelligence",
            "kernelone.context.repo_intelligence",
        ),
        "trigger_keys": ("domain=code", "localization_uncertain", "empty_repo_map"),
        "evidence_keys": ("repo_intelligence", "candidate_files", "symbol_tags"),
        "runtime_effects": ("project code-localization candidates into role turn context",),
        "benefit": "Improves local-model coding accuracy by aiming attention at likely files first.",
        "capability_refs": ("polaris.kernelone.context.repo_intelligence.RepoIntelligenceFacade",),
    },
    {
        "tech_id": "akashic_knowledge_pipeline",
        "title": "Akashic Knowledge Pipeline",
        "status": "wired",
        "priority": "p1",
        "production_entrypoints": (
            "roles.runtime._build_aggregate_memory_recall_pack",
            "kernelone.memory",
            "kernelone.context.context_os.memory",
            "kernelone.context.context_os.memory_search",
        ),
        "trigger_keys": ("long_session", "memory_search", "akashic_recall_pack"),
        "evidence_keys": ("memory_candidates", "semantic_chunks", "vector_store_refs"),
        "runtime_effects": ("call ContextOS MemoryManager during aggregate memory fallback turns",),
        "benefit": "Allows repeated execution experience and documents to become reusable recall material.",
        "capability_refs": ("polaris.kernelone.akashic.semantic_memory.AkashicSemanticMemory",),
    },
    {
        "tech_id": "tool_normalization_edit_blocks",
        "title": "Tool Normalization / Edit Blocks Normalizer",
        "status": "wired",
        "priority": "p1",
        "production_entrypoints": (
            "roles.kernel.internal.tool_call_protocol",
            "roles.kernel.internal.output.action_parser",
            "roles.kernel.internal.transaction.modification_contract",
        ),
        "trigger_keys": ("tool_call", "edit_block", "failed_apply"),
        "evidence_keys": ("normalized_tool_call", "modification_contract", "apply_error"),
        "runtime_effects": ("normalize unstable model tool output before execution or repair",),
        "benefit": "Reduces format drift when aggregate execution delegates coding to weaker local models.",
        "capability_refs": ("polaris.kernelone.llm.toolkit.tool_normalization",),
    },
    {
        "tech_id": "change_set_validation_rollback",
        "title": "Change-set Validation + Rollback Manager",
        "status": "wired",
        "priority": "p1",
        "production_entrypoints": (
            "factory.cognitive_runtime.validate_change_set",
            "roles.kernel.internal.transaction.write_authority",
            "kernelone.cognitive.execution.rollback_manager",
        ),
        "trigger_keys": ("write_tool", "change_set", "rollback"),
        "evidence_keys": ("validated_change_set", "rollback_snapshot", "scope_lease"),
        "runtime_effects": ("guard physical writes with validation and rollback receipts",),
        "benefit": "Adds a write fuse between aggregate reasoning and repository mutation.",
        "capability_refs": ("polaris.kernelone.cognitive.execution.rollback_manager",),
    },
    {
        "tech_id": "task_market_runtime_projection",
        "title": "Task Market / Runtime Projection",
        "status": "wired",
        "priority": "p2",
        "production_entrypoints": (
            "roles.runtime._build_aggregate_task_market_projection_pack",
            "cells.runtime.task_market",
            "cells.runtime.projection",
            "cells.orchestration.pm_dispatch",
        ),
        "trigger_keys": ("pm_task_id", "stage_owner", "runtime_projection"),
        "evidence_keys": ("task_contract", "stage_owner_matrix", "projection_snapshot"),
        "runtime_effects": ("snapshot task-market projection during aggregate role execution",),
        "benefit": "Makes multi-role delivery a governed workflow rather than ad hoc cross-calls.",
        "capability_refs": ("polaris.cells.runtime.task_market", "polaris.cells.runtime.projection"),
    },
    {
        "tech_id": "cognitive_knowledge_distiller",
        "title": "Cognitive Knowledge Distiller",
        "status": "wired",
        "priority": "p2",
        "production_entrypoints": (
            "roles.runtime._build_aggregate_distilled_knowledge_pack",
            "roles.runtime._distill_aggregate_lobe_result",
            "docs.graph.catalog.cells:cognitive.knowledge_distiller",
            "polaris.cells.cognitive.knowledge_distiller.public.service",
        ),
        "trigger_keys": ("receipt_id", "post_run_distillation"),
        "evidence_keys": ("distilled_lesson", "source_receipts", "replay_case"),
        "runtime_effects": ("retrieve distilled knowledge before execution and distill lobe results after execution",),
        "benefit": "Converts one-off aggregate runs into reusable operational knowledge.",
        "capability_refs": ("cognitive.knowledge_distiller",),
    },
    {
        "tech_id": "contextos_attention_phase_budgeting",
        "title": "ContextOS Attention / Phase-aware Budgeting / Predictive Compression",
        "status": "wired",
        "priority": "p1",
        "production_entrypoints": (
            "roles.runtime._build_aggregate_contextos_attention_budget_pack",
            "kernelone.context.context_os.attention",
            "kernelone.context.context_os.phase_budget_planner",
            "kernelone.context.context_os.predictive",
        ),
        "trigger_keys": ("phase", "budget_plan", "attention_masks"),
        "evidence_keys": ("attention_scores", "phase_budget", "compression_decisions"),
        "runtime_effects": ("inject phase budget, attention scores, and predictive compression signals per lobe",),
        "benefit": "Supports different context budgets for planning, localization, execution, and QA lobes.",
        "capability_refs": ("polaris.kernelone.context.context_os.phase_budget_planner",),
    },
)

_SESSION_PUBLIC_EXPORTS = frozenset(
    {
        "PathSecurityError",
        "RoleDataStore",
        "RoleDataStoreError",
    }
)
_BACKEND_ROOT = Path(__file__).resolve().parents[5]
_ENTRYPOINT_MODULE_ALIASES: dict[str, str] = {
    "cells.context.catalog": "polaris.cells.context.catalog",
    "cells.context.engine": "polaris.cells.context.engine",
    "cells.context.engine.internal.search_gateway": "polaris.cells.context.engine.internal.search_gateway",
    "cells.orchestration.pm_dispatch": "polaris.cells.orchestration.pm_dispatch",
    "cells.runtime.projection": "polaris.cells.runtime.projection",
    "cells.runtime.task_market": "polaris.cells.runtime.task_market",
    "factory.cognitive_runtime.receipts": "polaris.cells.factory.cognitive_runtime.public.service",
    "kernelone.context": "polaris.kernelone.context",
    "kernelone.context.context_os": "polaris.kernelone.context.context_os",
    "kernelone.context.context_os.attention": "polaris.kernelone.context.context_os.attention",
    "kernelone.context.context_os.memory": "polaris.kernelone.context.context_os.memory",
    "kernelone.context.context_os.memory_search": "polaris.kernelone.context.context_os.memory_search",
    "kernelone.context.context_os.phase_budget_planner": "polaris.kernelone.context.context_os.phase_budget_planner",
    "kernelone.context.context_os.predictive": "polaris.kernelone.context.context_os.predictive",
    "kernelone.context.repo_intelligence": "polaris.kernelone.context.repo_intelligence",
    "kernelone.cognitive.execution.rollback_manager": "polaris.kernelone.cognitive.execution.rollback_manager",
    "kernelone.memory": "polaris.kernelone.memory",
    "context.session_continuity": "polaris.kernelone.context.session_continuity",
    "roles.kernel.internal.output.action_parser": "polaris.cells.roles.kernel.internal.output.action_parser",
    "roles.kernel.internal.tool_call_protocol": "polaris.cells.roles.kernel.internal.tool_call_protocol",
    "roles.kernel.internal.transaction.ledger": "polaris.cells.roles.kernel.internal.transaction.ledger",
    "roles.kernel.internal.transaction.modification_contract": (
        "polaris.cells.roles.kernel.internal.transaction.modification_contract"
    ),
    "roles.kernel.internal.transaction.write_authority": (
        "polaris.cells.roles.kernel.internal.transaction.write_authority"
    ),
    "roles.kernel.internal.transaction_kernel": "polaris.cells.roles.kernel.internal.transaction_kernel",
}


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


def _select_aggregate_role_ids(
    requested_role_ids: tuple[str, ...],
    available_role_ids: set[str],
) -> tuple[str, ...]:
    if requested_role_ids:
        return _dedupe_tokens(role_id for role_id in requested_role_ids if role_id in available_role_ids)
    selected = tuple(role_id for role_id in _DEFAULT_AGGREGATE_ROLE_IDS if role_id in available_role_ids)
    if selected:
        return selected
    return tuple(sorted(available_role_ids))


def _build_aggregate_lobe(
    spec: Mapping[str, Any],
    *,
    selected_role_ids: set[str],
    available_role_ids: set[str],
    include_virtual_lobes: bool,
) -> AggregateRoleLobeV1:
    role_ids = _dedupe_tokens(spec.get("role_ids") or ())
    virtual_role_ids = _dedupe_tokens(spec.get("virtual_role_ids") or ()) if include_virtual_lobes else ()
    missing_role_ids = tuple(
        role_id for role_id in role_ids if role_id not in selected_role_ids or role_id not in available_role_ids
    )
    status = "active" if not missing_role_ids else "partial"
    return AggregateRoleLobeV1(
        lobe_id=str(spec.get("lobe_id") or ""),
        title=str(spec.get("title") or ""),
        phase=str(spec.get("phase") or ""),
        role_ids=role_ids,
        virtual_role_ids=virtual_role_ids,
        capability_refs=_dedupe_tokens(spec.get("capability_refs") or ()),
        attention_masks=_dedupe_tokens(spec.get("attention_masks") or ()),
        memory_triggers=_dedupe_tokens(spec.get("memory_triggers") or ()),
        compute_tier=str(spec.get("compute_tier") or "unspecified"),
        handoff_keys=_dedupe_tokens(spec.get("handoff_keys") or ()),
        takeover_triggers=_dedupe_tokens(spec.get("takeover_triggers") or ()),
        output_contract=str(spec.get("output_contract") or ""),
        status=status,
        missing_role_ids=missing_role_ids,
        metadata={
            "truthful_migration": (
                "virtual_role_ids are aggregate lobes or critics, not current roles.profile entries"
                if virtual_role_ids
                else "all role_ids are current roles.profile entries"
            ),
            "stateful": False,
        },
    )


def _build_cognitive_ledger(lobes: tuple[AggregateRoleLobeV1, ...]) -> tuple[AggregateCognitiveLedgerEntryV1, ...]:
    entries: list[AggregateCognitiveLedgerEntryV1] = []
    for index, lobe in enumerate(lobes):
        next_lobe = lobes[index + 1].lobe_id if index + 1 < len(lobes) else ""
        entries.append(
            AggregateCognitiveLedgerEntryV1(
                sequence=index,
                lobe_id=lobe.lobe_id,
                phase=lobe.phase,
                compute_tier=lobe.compute_tier,
                reads=(*lobe.capability_refs, *lobe.memory_triggers),
                writes=(*lobe.handoff_keys, lobe.output_contract),
                handoff_to=(next_lobe,) if next_lobe else (),
                takeover_triggers=lobe.takeover_triggers,
            )
        )
    return tuple(entries)


def _build_compute_policy(lobes: tuple[AggregateRoleLobeV1, ...]) -> dict[str, Any]:
    tier_order = _dedupe_tokens(lobe.compute_tier for lobe in lobes)
    return {
        "policy_id": "aggregate_compute_swap.v1",
        "tier_order": tier_order,
        "default_priority": "local_self_heal_first_after_compiler_feedback",
        "cloud_priority_conditions": (
            "architectural_ambiguity",
            "graph_boundary_violation",
            "high_blast_radius",
        ),
        "local_priority_conditions": (
            "compile_failure",
            "typecheck_failure",
            "failed_apply",
            "localization_uncertain",
        ),
        "rationale": (
            "Use cloud critique for high-ambiguity boundary decisions, but route "
            "compiler/test failures to local self-heal loops first because they are "
            "measurable and produce reusable Cognitive Runtime receipts."
        ),
    }


def _build_runtime_integrations(workspace: str) -> tuple[AggregateRuntimeIntegrationV1, ...]:
    integrations: list[AggregateRuntimeIntegrationV1] = []
    for spec in _AGGREGATE_RUNTIME_INTEGRATION_SPECS:
        production_entrypoints = _dedupe_tokens(spec.get("production_entrypoints") or ())
        entrypoint_checks = tuple(
            _check_aggregate_entrypoint(workspace, entrypoint) for entrypoint in production_entrypoints
        )
        missing_entrypoints = tuple(check.entrypoint for check in entrypoint_checks if not check.ok)
        integrations.append(
            AggregateRuntimeIntegrationV1(
                tech_id=str(spec.get("tech_id") or ""),
                title=str(spec.get("title") or ""),
                status=str(spec.get("status") or ""),
                priority=str(spec.get("priority") or ""),
                production_entrypoints=production_entrypoints,
                trigger_keys=_dedupe_tokens(spec.get("trigger_keys") or ()),
                evidence_keys=_dedupe_tokens(spec.get("evidence_keys") or ()),
                runtime_effects=_dedupe_tokens(spec.get("runtime_effects") or ()),
                benefit=str(spec.get("benefit") or ""),
                capability_refs=_dedupe_tokens(spec.get("capability_refs") or ()),
                entrypoint_checks=entrypoint_checks,
                entrypoints_verified=bool(entrypoint_checks) and not missing_entrypoints,
                missing_entrypoints=missing_entrypoints,
            )
        )
    return tuple(integrations)


def _build_runtime_audit_result(
    *,
    workspace: str,
    integrations: tuple[AggregateRuntimeIntegrationV1, ...],
    metadata: Mapping[str, Any] | None = None,
) -> AggregateRuntimeAuditResultV1:
    wired = tuple(item for item in integrations if item.status == "wired" and item.entrypoints_verified)
    available = tuple(item for item in integrations if item.status == "available" and item.entrypoints_verified)
    planned_bridge = tuple(item for item in integrations if item.status == "planned_bridge")
    missing_checks = tuple(
        check for integration in integrations for check in integration.entrypoint_checks if not check.ok
    )
    verified_checks = tuple(
        check for integration in integrations for check in integration.entrypoint_checks if check.ok
    )
    priority_wired = tuple(item.tech_id for item in wired if item.priority == "p0")
    warnings: list[str] = []
    if planned_bridge:
        warnings.append(f"planned_bridge:{','.join(item.tech_id for item in planned_bridge)}")
    if missing_checks:
        warnings.append(f"missing_entrypoints:{','.join(check.entrypoint for check in missing_checks)}")
    return AggregateRuntimeAuditResultV1(
        ok=bool(integrations) and len(priority_wired) >= 4 and not missing_checks,
        workspace=workspace,
        aggregate_model_id=_AGGREGATE_MODEL_ID,
        integrations=integrations,
        wired_count=len(wired),
        available_count=len(available),
        planned_bridge_count=len(planned_bridge),
        verified_entrypoint_count=len(verified_checks),
        missing_entrypoint_count=len(missing_checks),
        priority_wired=priority_wired,
        warnings=tuple(warnings),
        metadata={
            "audit_scope": "aggregate_llm_unique_technology_runtime_integrations",
            "status_semantics": {
                "wired": "current aggregate runtime path has a production entrypoint",
                "available": "implemented capability exists but aggregate path does not force it yet",
                "planned_bridge": "known architecture asset still needs an aggregate bridge",
            },
            **dict(metadata or {}),
        },
    )


def _normalize_failure_signal(value: str) -> str:
    token = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return _AGGREGATE_FAILURE_SIGNAL_ALIASES.get(token, token)


def _extract_failure_signals(query: BuildAggregateRolePlanQueryV1) -> tuple[str, ...]:
    signals: list[str] = []
    signals.extend(query.failure_signals)
    for source in (query.context, query.metadata):
        for key in ("failure_signal", "degraded_signal", "error_signal"):
            raw_value = source.get(key) if isinstance(source, Mapping) else None
            if isinstance(raw_value, str) and raw_value.strip():
                signals.append(raw_value)
        raw_values = source.get("failure_signals") if isinstance(source, Mapping) else None
        if isinstance(raw_values, list | tuple):
            signals.extend(str(item or "") for item in raw_values)
    return _dedupe_tokens(_normalize_failure_signal(signal) for signal in signals if str(signal or "").strip())


def _extract_failure_evidence(query: BuildAggregateRolePlanQueryV1) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    for source in (query.context, query.metadata):
        raw_evidence = source.get("failure_evidence") if isinstance(source, Mapping) else None
        if isinstance(raw_evidence, Mapping):
            evidence.update(dict(raw_evidence))
    evidence.update(dict(query.failure_evidence))
    return evidence


def _build_takeover_evidence_status(
    *,
    takeover_directive: AggregateTakeoverDirectiveV1 | None,
    failure_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    if takeover_directive is None:
        return {
            "required_keys": (),
            "present_keys": tuple(sorted(str(key) for key in failure_evidence)),
            "missing_keys": (),
            "complete": True,
        }
    required_keys = takeover_directive.evidence_keys
    present_keys = tuple(
        key for key in required_keys if key in failure_evidence and failure_evidence.get(key) is not None
    )
    missing_keys = tuple(key for key in required_keys if key not in present_keys)
    return {
        "required_keys": required_keys,
        "present_keys": present_keys,
        "missing_keys": missing_keys,
        "complete": not missing_keys,
    }


def _build_takeover_directive(
    *,
    lobes: tuple[AggregateRoleLobeV1, ...],
    cognitive_ledger: tuple[AggregateCognitiveLedgerEntryV1, ...],
    failure_signals: tuple[str, ...],
) -> AggregateTakeoverDirectiveV1 | None:
    if not failure_signals:
        return None
    ledger_by_lobe = {entry.lobe_id: entry for entry in cognitive_ledger}
    for signal in failure_signals:
        for lobe in lobes:
            if signal not in lobe.takeover_triggers and signal not in lobe.memory_triggers:
                continue
            entry = ledger_by_lobe.get(lobe.lobe_id)
            next_lobes = entry.handoff_to if entry is not None else ()
            return AggregateTakeoverDirectiveV1(
                trigger=signal,
                lobe_id=lobe.lobe_id,
                compute_tier=lobe.compute_tier,
                reason=f"{signal} activates {lobe.lobe_id} through aggregate takeover triggers",
                evidence_keys=_AGGREGATE_FAILURE_EVIDENCE_KEYS.get(signal, ("failure_signal",)),
                action_contract=lobe.output_contract,
                next_lobes=next_lobes,
            )
    return None


def _lobe_by_id(plan: AggregateRolePlanResultV1, lobe_id: str | None) -> AggregateRoleLobeV1 | None:
    token = str(lobe_id or "").strip()
    if not token:
        return None
    for lobe in plan.lobes:
        if lobe.lobe_id == token:
            return lobe
    return None


def _select_aggregate_execution_lobe(plan: AggregateRolePlanResultV1) -> AggregateRoleLobeV1:
    takeover_lobe = _lobe_by_id(
        plan,
        plan.takeover_directive.lobe_id if plan.takeover_directive is not None else None,
    )
    if takeover_lobe is not None:
        return takeover_lobe
    for lobe in plan.lobes:
        if lobe.status == "active":
            return lobe
    return plan.lobes[0]


def _lobe_has_current_role(lobe: AggregateRoleLobeV1, plan: AggregateRolePlanResultV1) -> bool:
    current_roles = set(plan.current_role_ids)
    return any(role_id in current_roles for role_id in lobe.role_ids)


def _aggregate_max_lobe_turns(command: AggregateChatCompletionsCommandV1) -> int:
    for source in (command.metadata, command.context):
        raw_value = source.get("max_lobe_turns") if isinstance(source, Mapping) else None
        if raw_value is None and isinstance(source, Mapping):
            raw_value = source.get("aggregate_max_lobe_turns")
        if raw_value is None:
            continue
        try:
            parsed = int(raw_value)
        except (TypeError, ValueError):
            continue
        return min(max(parsed, 1), 5)
    return 3


def _aggregate_memory_recall_limit(command: AggregateChatCompletionsCommandV1) -> int:
    for source in (command.metadata, command.context):
        raw_value = source.get("memory_recall_limit") if isinstance(source, Mapping) else None
        if raw_value is None and isinstance(source, Mapping):
            raw_value = source.get("akashic_recall_limit")
        if raw_value is None:
            continue
        try:
            parsed = int(raw_value)
        except (TypeError, ValueError):
            continue
        return min(max(parsed, 0), 10)
    return 5


def _aggregate_memory_recall_triggers(
    *,
    plan: AggregateRolePlanResultV1,
    selected_lobe: AggregateRoleLobeV1,
) -> tuple[str, ...]:
    failure_signals = tuple(str(item) for item in plan.metadata.get("failure_signals") or ())
    triggers = [
        signal
        for signal in failure_signals
        if signal in selected_lobe.memory_triggers
        or signal in {"localization_uncertain", "degraded_signal", "empty_repo_map", "long_session"}
    ]
    if selected_lobe.lobe_id == "hippocampus_controller":
        triggers.append("hippocampus_controller")
    return _dedupe_tokens(triggers)


def _aggregate_memory_recall_query(
    *,
    command: AggregateChatCompletionsCommandV1,
    plan: AggregateRolePlanResultV1,
    selected_lobe: AggregateRoleLobeV1,
) -> str:
    evidence = plan.metadata.get("failure_evidence")
    evidence_text = " ".join(str(value) for value in dict(evidence).values()) if isinstance(evidence, Mapping) else ""
    return " ".join(
        item
        for item in (
            _aggregate_objective_from_messages(command.messages),
            selected_lobe.lobe_id,
            " ".join(str(item) for item in plan.metadata.get("failure_signals") or ()),
            evidence_text,
        )
        if item
    ).strip()


def _aggregate_memory_current_facts(
    *,
    plan: AggregateRolePlanResultV1,
    selected_lobe: AggregateRoleLobeV1,
    prior_handoffs: tuple[Mapping[str, Any], ...],
) -> list[str]:
    facts = [
        f"aggregate_model_id={plan.aggregate_model_id}",
        f"selected_lobe_id={selected_lobe.lobe_id}",
        f"selected_lobe_phase={selected_lobe.phase}",
    ]
    for signal in plan.metadata.get("failure_signals") or ():
        facts.append(f"failure_signal={signal}")
    evidence = plan.metadata.get("failure_evidence")
    if isinstance(evidence, Mapping):
        facts.extend(f"failure_evidence.{key}={value}" for key, value in evidence.items())
    for handoff in prior_handoffs:
        facts.append(
            "prior_handoff="
            + json.dumps(
                {
                    "lobe_id": handoff.get("lobe_id"),
                    "role_id": handoff.get("role_id"),
                    "status": handoff.get("status"),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    return facts


def _build_aggregate_memory_recall_pack(
    *,
    command: AggregateChatCompletionsCommandV1,
    plan: AggregateRolePlanResultV1,
    selected_lobe: AggregateRoleLobeV1,
    prior_handoffs: tuple[Mapping[str, Any], ...],
) -> dict[str, Any]:
    triggers = _aggregate_memory_recall_triggers(plan=plan, selected_lobe=selected_lobe)
    limit = _aggregate_memory_recall_limit(command)
    if not triggers or limit <= 0:
        return {
            "enabled": False,
            "provider": "ContextOS.MemoryManager",
            "triggers": triggers,
            "reason": "no_memory_trigger" if not triggers else "limit_zero",
        }
    query = _aggregate_memory_recall_query(command=command, plan=plan, selected_lobe=selected_lobe)
    current_facts = _aggregate_memory_current_facts(
        plan=plan,
        selected_lobe=selected_lobe,
        prior_handoffs=prior_handoffs,
    )
    try:
        from polaris.kernelone.context.context_os.memory import MemoryManager

        manager = MemoryManager(workspace=command.workspace)
        projections = manager.process(query=query, current_facts=current_facts, limit=limit)
        projection_payloads = [projection.to_dict() for projection in projections[:limit]]
        return {
            "enabled": True,
            "provider": "ContextOS.MemoryManager",
            "status": "ok",
            "triggers": triggers,
            "query": query,
            "current_facts": current_facts,
            "projection_count": len(projection_payloads),
            "injection_allowed_count": sum(1 for item in projection_payloads if bool(item.get("injection_allowed"))),
            "projections": projection_payloads,
            "truthful_migration": "Memory is supplementary and never overrides current failure evidence.",
        }
    except (RuntimeError, ValueError, TypeError, OSError) as exc:
        return {
            "enabled": True,
            "provider": "ContextOS.MemoryManager",
            "status": "degraded",
            "triggers": triggers,
            "query": query,
            "current_facts": current_facts,
            "projection_count": 0,
            "injection_allowed_count": 0,
            "projections": [],
            "error_message": str(exc),
        }


def _aggregate_phase_for_contextos(
    *,
    selected_lobe: AggregateRoleLobeV1,
    plan: AggregateRolePlanResultV1,
) -> str:
    if plan.takeover_directive is not None and plan.takeover_directive.trigger in {
        "compile_failure",
        "typecheck_failure",
        "failed_apply",
    }:
        return "debugging"
    return {
        "preflight": "planning",
        "blueprint_refinement": "planning",
        "execution_context_projection": "exploration",
        "apply_and_verify": "verification",
        "stage_handoff": "review",
    }.get(selected_lobe.phase, "planning")


def _estimate_aggregate_text_tokens(value: Any) -> int:
    try:
        payload = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        payload = str(value)
    return max(1, len(payload) // 4) if payload else 0


def _build_aggregate_attention_candidates(
    *,
    command: AggregateChatCompletionsCommandV1,
    plan: AggregateRolePlanResultV1,
    selected_lobe: AggregateRoleLobeV1,
    prior_handoffs: tuple[Mapping[str, Any], ...],
) -> tuple[Any, ...]:
    objective = _aggregate_objective_from_messages(command.messages)
    candidates: list[Any] = [
        SimpleNamespace(
            event_id="aggregate.objective",
            content=objective,
            role="user",
            kind="user_turn",
            sequence=0,
            metadata={"is_pinned": True, "candidate_kind": "objective"},
            created_at="",
        )
    ]
    failure_evidence = dict(plan.metadata.get("failure_evidence") or {})
    if failure_evidence:
        candidates.append(
            SimpleNamespace(
                event_id="aggregate.failure_evidence",
                content=json.dumps(failure_evidence, ensure_ascii=False, sort_keys=True),
                role="system",
                kind="error",
                sequence=1,
                metadata={"contains_error": True, "candidate_kind": "failure_evidence"},
                created_at="",
            )
        )
    if prior_handoffs:
        candidates.append(
            SimpleNamespace(
                event_id="aggregate.prior_handoffs",
                content=json.dumps([dict(item) for item in prior_handoffs], ensure_ascii=False, sort_keys=True),
                role="assistant",
                kind="tool_result",
                sequence=2,
                metadata={"contains_tool_result": True, "candidate_kind": "prior_handoffs"},
                created_at="",
            )
        )
    candidates.append(
        SimpleNamespace(
            event_id=f"aggregate.lobe.{selected_lobe.lobe_id}",
            content=" ".join((selected_lobe.title, selected_lobe.phase, " ".join(selected_lobe.attention_masks))),
            role="system",
            kind="system",
            sequence=3,
            metadata={"candidate_kind": "lobe_directive"},
            created_at="",
        )
    )
    return tuple(candidates)


def _build_aggregate_contextos_attention_budget_pack(
    *,
    command: AggregateChatCompletionsCommandV1,
    plan: AggregateRolePlanResultV1,
    selected_lobe: AggregateRoleLobeV1,
    prior_handoffs: tuple[Mapping[str, Any], ...],
) -> dict[str, Any]:
    phase_name = _aggregate_phase_for_contextos(selected_lobe=selected_lobe, plan=plan)
    transcript_tokens = sum(_estimate_aggregate_text_tokens(message.content) for message in command.messages)
    artifact_tokens = _estimate_aggregate_text_tokens(plan.metadata.get("failure_evidence") or {})
    artifact_tokens += _estimate_aggregate_text_tokens([dict(item) for item in prior_handoffs]) if prior_handoffs else 0
    try:
        from polaris.kernelone.context.context_os.attention import AttentionScorer
        from polaris.kernelone.context.context_os.attention.scorer import ScoringContext
        from polaris.kernelone.context.context_os.phase_budget_planner import PhaseAwareBudgetPlanner
        from polaris.kernelone.context.context_os.phase_detection import TaskPhase
        from polaris.kernelone.context.context_os.predictive import PredictiveCompressor

        task_phase = TaskPhase(phase_name)
        budget_plan = PhaseAwareBudgetPlanner().plan_budget(
            phase=task_phase,
            transcript_tokens=transcript_tokens,
            artifact_tokens=artifact_tokens,
        )
        prediction = PredictiveCompressor().predict(current_phase=task_phase.value, recent_events=())
        scorer = AttentionScorer(use_embeddings=False)
        scoring_context = ScoringContext(
            current_intent=_aggregate_objective_from_messages(command.messages),
            current_goal=selected_lobe.output_contract,
            hard_constraints=tuple(selected_lobe.attention_masks),
            current_task_id=command.run_id or command.session_id or "",
            current_phase=task_phase,
        )
        attention_scores = [
            {
                "event_id": str(getattr(candidate, "event_id", "")),
                "kind": str(getattr(candidate, "kind", "")),
                "score": scorer.score_candidate(candidate, scoring_context).to_dict(),
            }
            for candidate in _build_aggregate_attention_candidates(
                command=command,
                plan=plan,
                selected_lobe=selected_lobe,
                prior_handoffs=prior_handoffs,
            )
        ]
        return {
            "enabled": True,
            "provider": "ContextOS.Attention+PhaseBudget+PredictiveCompression",
            "status": "ok",
            "phase": task_phase.value,
            "attention_masks": list(selected_lobe.attention_masks),
            "phase_budget": budget_plan.to_dict(),
            "attention_scores": attention_scores,
            "predictive_compression": prediction.to_dict(),
        }
    except (ImportError, RuntimeError, ValueError, TypeError, OSError) as exc:
        return {
            "enabled": True,
            "provider": "ContextOS.Attention+PhaseBudget+PredictiveCompression",
            "status": "degraded",
            "phase": phase_name,
            "attention_masks": list(selected_lobe.attention_masks),
            "phase_budget": {},
            "attention_scores": [],
            "predictive_compression": {},
            "error_message": str(exc),
        }


def _build_aggregate_task_market_projection_pack(
    *,
    command: AggregateChatCompletionsCommandV1,
) -> dict[str, Any]:
    try:
        from polaris.cells.runtime.projection.task_market_projection import TaskMarketProjection

        summary = TaskMarketProjection(workspace=command.workspace).get_dashboard_summary()
        active_items = summary.get("active_items")
        if isinstance(active_items, list):
            summary = dict(summary)
            summary["active_items"] = active_items[:5]
            summary["active_items_truncated"] = len(active_items) > 5
        return {
            "enabled": True,
            "provider": "TaskMarketProjection",
            "status": "ok",
            "summary": summary,
        }
    except (ImportError, RuntimeError, ValueError, TypeError, OSError) as exc:
        return {
            "enabled": True,
            "provider": "TaskMarketProjection",
            "status": "degraded",
            "summary": {},
            "error_message": str(exc),
        }


def _read_aggregate_generated_pack_summary(pack_name: str, path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "pack": pack_name,
            "status": "missing",
            "path": path.relative_to(_BACKEND_ROOT).as_posix() if path.is_relative_to(_BACKEND_ROOT) else str(path),
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return {
            "pack": pack_name,
            "status": "degraded",
            "path": path.relative_to(_BACKEND_ROOT).as_posix() if path.is_relative_to(_BACKEND_ROOT) else str(path),
            "error_message": str(exc),
        }
    descriptors = payload.get("descriptors")
    capabilities = payload.get("capabilities")
    items = payload.get("items")
    return {
        "pack": pack_name,
        "status": "ok",
        "path": path.relative_to(_BACKEND_ROOT).as_posix() if path.is_relative_to(_BACKEND_ROOT) else str(path),
        "version": payload.get("version"),
        "cell_id": payload.get("cell_id"),
        "descriptor_count": len(descriptors) if isinstance(descriptors, list) else None,
        "capability_count": len(capabilities) if isinstance(capabilities, list) else None,
        "item_count": len(items) if isinstance(items, list) else None,
        "source_hash": payload.get("source_hash"),
        "snapshot_hash": payload.get("snapshot_hash"),
    }


def _serialize_context_budget(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        dumped = value.model_dump()
        return dict(dumped) if isinstance(dumped, Mapping) else {}
    if hasattr(value, "dict"):
        dumped = value.dict()
        return dict(dumped) if isinstance(dumped, Mapping) else {}
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _build_aggregate_context_governance_pack(
    *,
    command: AggregateChatCompletionsCommandV1,
    selected_lobe: AggregateRoleLobeV1,
    selected_role: str,
    chain_turn_index: int,
) -> dict[str, Any]:
    query = _aggregate_objective_from_messages(command.messages)
    descriptor_summaries = [
        _read_aggregate_generated_pack_summary(
            "context.catalog.descriptor",
            _BACKEND_ROOT / "polaris/cells/context/catalog/generated/descriptor.pack.json",
        ),
        _read_aggregate_generated_pack_summary(
            "context.engine.descriptor",
            _BACKEND_ROOT / "polaris/cells/context/engine/generated/descriptor.pack.json",
        ),
    ]
    generated_context_summaries = [
        _read_aggregate_generated_pack_summary(
            "context.catalog.context",
            _BACKEND_ROOT / "polaris/cells/context/catalog/generated/context.pack.json",
        ),
        _read_aggregate_generated_pack_summary(
            "context.engine.context",
            _BACKEND_ROOT / "polaris/cells/context/engine/generated/context.pack.json",
        ),
    ]
    try:
        from polaris.cells.context.engine.public.service import build_context_window, get_search_service

        retrieval_candidates = get_search_service().search(query, limit=5)
        pack, policy, budget, sources_enabled = build_context_window(
            project_root=command.workspace,
            role=selected_role,
            query=query,
            step=chain_turn_index + 1,
            run_id=command.run_id or command.session_id or _AGGREGATE_MODEL_ID,
            mode=selected_lobe.phase,
            sources_enabled=[],
            policy={"max_tokens": 4096, "max_chars": 16384, "cost_class": "LOCAL"},
            context_override={"state_first_context_os_enabled": True},
            session_id=command.session_id or "",
        )
        context_pack = {
            "provider": "context.engine.build_context_window",
            "request_hash": pack.request_hash,
            "item_count": len(pack.items),
            "total_tokens": pack.total_tokens,
            "total_chars": pack.total_chars,
            "snapshot_path": pack.snapshot_path,
            "snapshot_hash": pack.snapshot_hash,
            "sources_enabled": list(sources_enabled),
        }
        verify_pack = {
            "budget": _serialize_context_budget(budget),
            "policy_cost_class": policy.get("cost_class"),
            "descriptor_pack_count": len(descriptor_summaries),
            "generated_context_pack_count": len(generated_context_summaries),
            "retrieval_candidate_count": len(retrieval_candidates),
        }
        return {
            "enabled": True,
            "provider": "ContextCatalog+ContextEngine",
            "status": "ok",
            "query": query,
            "descriptor_pack": descriptor_summaries,
            "generated_context_pack": generated_context_summaries,
            "context_pack": context_pack,
            "verify_pack": verify_pack,
            "graph_constrained_retrieval": {
                "provider": "context.engine.search_gateway",
                "candidate_count": len(retrieval_candidates),
                "candidates": retrieval_candidates,
            },
        }
    except (ImportError, RuntimeError, ValueError, TypeError, OSError) as exc:
        return {
            "enabled": True,
            "provider": "ContextCatalog+ContextEngine",
            "status": "degraded",
            "query": query,
            "descriptor_pack": descriptor_summaries,
            "generated_context_pack": generated_context_summaries,
            "context_pack": {},
            "verify_pack": {},
            "graph_constrained_retrieval": {"candidate_count": 0, "candidates": []},
            "error_message": str(exc),
        }


def _serialize_distilled_knowledge_unit(unit: Any) -> dict[str, Any]:
    return {
        "knowledge_id": str(getattr(unit, "knowledge_id", "") or ""),
        "knowledge_type": str(getattr(unit, "knowledge_type", "") or ""),
        "pattern_summary": str(getattr(unit, "pattern_summary", "") or ""),
        "confidence": float(getattr(unit, "confidence", 0.0) or 0.0),
        "occurrence_count": int(getattr(unit, "occurrence_count", 0) or 0),
        "prevention_hint": getattr(unit, "prevention_hint", None),
        "metadata": dict(getattr(unit, "metadata", {}) or {}),
    }


def _build_aggregate_distilled_knowledge_pack(
    *,
    command: AggregateChatCompletionsCommandV1,
    plan: AggregateRolePlanResultV1,
    selected_lobe: AggregateRoleLobeV1,
    selected_role: str,
) -> dict[str, Any]:
    query = " ".join(
        token
        for token in (
            _aggregate_objective_from_messages(command.messages),
            selected_lobe.output_contract,
            " ".join(str(signal) for signal in plan.metadata.get("failure_signals") or ()),
        )
        if token
    )
    try:
        from polaris.cells.cognitive.knowledge_distiller.public.contracts import RetrieveKnowledgeQueryV1
        from polaris.cells.cognitive.knowledge_distiller.public.service import KnowledgeDistillerService

        result = KnowledgeDistillerService(workspace=command.workspace).retrieve_knowledge(
            RetrieveKnowledgeQueryV1(
                workspace=command.workspace,
                query=query,
                top_k=5,
                role_filter=selected_role,
                min_confidence=0.3,
            )
        )
        return {
            "enabled": True,
            "provider": "KnowledgeDistillerService.retrieve_knowledge",
            "status": "ok",
            "query": result.query,
            "total_available": result.total_available,
            "knowledge_units": [_serialize_distilled_knowledge_unit(unit) for unit in result.knowledge_units],
        }
    except (ImportError, RuntimeError, ValueError, TypeError, OSError) as exc:
        return {
            "enabled": True,
            "provider": "KnowledgeDistillerService.retrieve_knowledge",
            "status": "degraded",
            "query": query,
            "total_available": 0,
            "knowledge_units": [],
            "error_message": str(exc),
        }


def _aggregate_suspected_files_from_failure_evidence(failure_evidence: Mapping[str, Any]) -> list[str]:
    files: list[str] = []
    for key in ("changed_files", "target_paths", "candidate_files"):
        value = failure_evidence.get(key)
        if isinstance(value, str) and value.strip():
            files.append(value.strip())
        elif isinstance(value, (list, tuple)):
            files.extend(str(item).strip() for item in value if str(item or "").strip())
    return files[:20]


def _distill_aggregate_lobe_result(
    *,
    command: AggregateChatCompletionsCommandV1,
    plan: AggregateRolePlanResultV1,
    selected_lobe: AggregateRoleLobeV1,
    selected_role: str,
    chain_turn_index: int,
    result: RoleExecutionResultV1,
) -> dict[str, Any]:
    failure_evidence = dict(plan.metadata.get("failure_evidence") or {})
    error_summary = (
        result.error_message
        or failure_evidence.get("compiler_output")
        or failure_evidence.get("typecheck_output")
        or failure_evidence.get("apply_error")
        or ""
    )
    structured_findings: dict[str, Any] = {
        "task_progress": selected_lobe.phase,
        "action_taken": selected_lobe.output_contract,
        "verified_results": [result.status] if result.ok else [],
        "patched_files": [],
        "suspected_files": _aggregate_suspected_files_from_failure_evidence(failure_evidence),
        "_findings_trajectory": [
            {
                "lobe_id": selected_lobe.lobe_id,
                "role_id": selected_role,
                "status": result.status,
                "chain_turn_index": chain_turn_index,
            }
        ],
    }
    if error_summary:
        structured_findings["error_summary"] = str(error_summary)
    try:
        from polaris.cells.cognitive.knowledge_distiller.public.contracts import DistillSessionCommandV1
        from polaris.cells.cognitive.knowledge_distiller.public.service import KnowledgeDistillerService

        distill_session_id = (
            result.session_id
            or command.session_id
            or result.task_id
            or result.run_id
            or command.run_id
            or f"aggregate:{selected_lobe.lobe_id}:{chain_turn_index}"
        )
        distillation = KnowledgeDistillerService(workspace=command.workspace).distill_session(
            DistillSessionCommandV1(
                workspace=command.workspace,
                session_id=distill_session_id,
                run_id=result.run_id or command.run_id,
                structured_findings=structured_findings,
                task_progress=selected_lobe.phase,
                outcome="completed" if result.ok else "failed",
                metadata={
                    "role": selected_role,
                    "lobe_id": selected_lobe.lobe_id,
                    "aggregate_model_id": plan.aggregate_model_id,
                    "chain_turn_index": chain_turn_index,
                },
            )
        )
        return {
            "enabled": True,
            "provider": "KnowledgeDistillerService.distill_session",
            "status": "ok",
            "knowledge_units_created": distillation.knowledge_units_created,
            "patterns_extracted": list(distillation.patterns_extracted),
            "knowledge_ids": list(distillation.knowledge_ids),
        }
    except (ImportError, RuntimeError, ValueError, TypeError, OSError) as exc:
        return {
            "enabled": True,
            "provider": "KnowledgeDistillerService.distill_session",
            "status": "degraded",
            "knowledge_units_created": 0,
            "patterns_extracted": [],
            "knowledge_ids": [],
            "error_message": str(exc),
        }


def _select_aggregate_lobe_chain(
    *,
    plan: AggregateRolePlanResultV1,
    command: AggregateChatCompletionsCommandV1,
) -> tuple[AggregateRoleLobeV1, ...]:
    max_lobes = _aggregate_max_lobe_turns(command)
    start_lobe = _select_aggregate_execution_lobe(plan)
    ordered_lobes = list(plan.lobes)
    try:
        start_index = ordered_lobes.index(start_lobe)
    except ValueError:
        start_index = 0
    candidates = ordered_lobes[start_index:]
    selected = [lobe for lobe in candidates if _lobe_has_current_role(lobe, plan)]
    if not selected and _lobe_has_current_role(start_lobe, plan):
        selected = [start_lobe]
    return tuple(selected[:max_lobes])


def _select_aggregate_execution_role(
    *,
    plan: AggregateRolePlanResultV1,
    command: AggregateChatCompletionsCommandV1,
    selected_lobe: AggregateRoleLobeV1,
) -> str:
    current_roles = set(plan.current_role_ids)
    for source in (command.metadata, command.context):
        raw_role = source.get("aggregate_execution_role") if isinstance(source, Mapping) else None
        if raw_role is None and isinstance(source, Mapping):
            raw_role = source.get("execution_role")
        role = str(raw_role or "").strip()
        if role and role in current_roles:
            return role
    preferred_by_lobe: dict[str, tuple[str, ...]] = {
        "constraint_boundary_generator": ("architect", "qa"),
        "dialectic_self_heal_loop": ("chief_engineer", "qa"),
        "hippocampus_controller": ("director",),
        "tool_commit_guard": ("director", "qa"),
        "task_market_allocator": ("pm", "director", "qa"),
    }
    for role in preferred_by_lobe.get(selected_lobe.lobe_id, selected_lobe.role_ids):
        if role in current_roles and role in selected_lobe.role_ids:
            return role
    for role in selected_lobe.role_ids:
        if role in current_roles:
            return role
    for role in ("director", "chief_engineer", "architect", "pm", "qa"):
        if role in current_roles:
            return role
    if plan.current_role_ids:
        return plan.current_role_ids[0]
    raise ValueError("aggregate single_turn requires at least one concrete role")


def _selected_message_index(messages: tuple[AggregateChatMessageV1, ...]) -> int:
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].role.lower() == "user":
            return index
    return len(messages) - 1


def _aggregate_history_from_messages(messages: tuple[AggregateChatMessageV1, ...]) -> tuple[tuple[str, str], ...]:
    selected_index = _selected_message_index(messages)
    return tuple((message.role, message.content) for index, message in enumerate(messages) if index != selected_index)


def _build_aggregate_lobe_directive(
    *,
    plan: AggregateRolePlanResultV1,
    selected_lobe: AggregateRoleLobeV1,
    selected_role: str,
    chain_turn_index: int,
) -> dict[str, Any]:
    return {
        "schema": "aggregate_lobe_directive.v1",
        "aggregate_model_id": plan.aggregate_model_id,
        "chain_turn_index": chain_turn_index,
        "lobe_id": selected_lobe.lobe_id,
        "phase": selected_lobe.phase,
        "role_id": selected_role,
        "role_ids": list(selected_lobe.role_ids),
        "virtual_role_ids": list(selected_lobe.virtual_role_ids),
        "compute_tier": selected_lobe.compute_tier,
        "capability_refs": list(selected_lobe.capability_refs),
        "attention_masks": list(selected_lobe.attention_masks),
        "memory_triggers": list(selected_lobe.memory_triggers),
        "handoff_keys": list(selected_lobe.handoff_keys),
        "takeover_triggers": list(selected_lobe.takeover_triggers),
        "output_contract": selected_lobe.output_contract,
        "truthful_migration": "Only role_id is executed; virtual_role_ids are planning constructs.",
    }


def _summarize_aggregate_memory_pack(memory_recall_pack: Mapping[str, Any]) -> dict[str, Any]:
    projections = memory_recall_pack.get("projections")
    projection_summaries: list[dict[str, Any]] = []
    if isinstance(projections, list):
        for item in projections[:5]:
            if not isinstance(item, Mapping):
                continue
            memory = item.get("memory")
            memory_payload = dict(memory) if isinstance(memory, Mapping) else {}
            projection_summaries.append(
                {
                    "memory_id": memory_payload.get("memory_id"),
                    "content": memory_payload.get("content"),
                    "injection_allowed": bool(item.get("injection_allowed")),
                    "injection_reason": item.get("injection_reason"),
                }
            )
    return {
        "enabled": bool(memory_recall_pack.get("enabled")),
        "provider": memory_recall_pack.get("provider"),
        "status": memory_recall_pack.get("status") or "skipped",
        "triggers": list(memory_recall_pack.get("triggers") or ()),
        "query": memory_recall_pack.get("query"),
        "projection_count": memory_recall_pack.get("projection_count", 0),
        "injection_allowed_count": memory_recall_pack.get("injection_allowed_count", 0),
        "projections": projection_summaries,
    }


def _build_aggregate_lobe_turn_envelope(
    *,
    command: AggregateChatCompletionsCommandV1,
    plan: AggregateRolePlanResultV1,
    selected_lobe: AggregateRoleLobeV1,
    selected_role: str,
    chain_turn_index: int,
    prior_handoffs: tuple[Mapping[str, Any], ...],
    memory_recall_pack: Mapping[str, Any],
    contextos_attention_budget_pack: Mapping[str, Any],
    task_market_projection_pack: Mapping[str, Any],
    context_governance_pack: Mapping[str, Any],
    distilled_knowledge_pack: Mapping[str, Any],
) -> str:
    payload: dict[str, Any] = {
        "schema": "polaris.aggregate_lobe_turn.v1",
        "original_objective": _aggregate_objective_from_messages(command.messages),
        "execution_mode": command.execution_mode,
        "lobe_directive": _build_aggregate_lobe_directive(
            plan=plan,
            selected_lobe=selected_lobe,
            selected_role=selected_role,
            chain_turn_index=chain_turn_index,
        ),
        "failure": {
            "signals": list(plan.metadata.get("failure_signals") or ()),
            "evidence": dict(plan.metadata.get("failure_evidence") or {}),
            "takeover_evidence_status": dict(plan.metadata.get("takeover_evidence_status") or {}),
            "takeover_directive": (
                {
                    "trigger": plan.takeover_directive.trigger,
                    "lobe_id": plan.takeover_directive.lobe_id,
                    "action_contract": plan.takeover_directive.action_contract,
                }
                if plan.takeover_directive is not None
                else None
            ),
        },
        "prior_handoffs": [dict(item) for item in prior_handoffs],
        "memory_recall": _summarize_aggregate_memory_pack(memory_recall_pack),
        "contextos_attention_budget": dict(contextos_attention_budget_pack),
        "context_governance": dict(context_governance_pack),
        "distilled_knowledge": dict(distilled_knowledge_pack),
        "runtime_projection": {
            "provider": task_market_projection_pack.get("provider"),
            "status": task_market_projection_pack.get("status"),
            "summary": task_market_projection_pack.get("summary") or {},
        },
        "response_contract": selected_lobe.output_contract,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _aggregate_execution_context(
    *,
    command: AggregateChatCompletionsCommandV1,
    plan: AggregateRolePlanResultV1,
    selected_lobe: AggregateRoleLobeV1,
    selected_role: str,
    chain_turn_index: int = 0,
    prior_handoffs: tuple[Mapping[str, Any], ...] = (),
    memory_recall_pack: Mapping[str, Any] | None = None,
    contextos_attention_budget_pack: Mapping[str, Any] | None = None,
    task_market_projection_pack: Mapping[str, Any] | None = None,
    context_governance_pack: Mapping[str, Any] | None = None,
    distilled_knowledge_pack: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    context = dict(command.context)
    recall_pack = dict(memory_recall_pack or {})
    attention_budget_pack = dict(contextos_attention_budget_pack or {})
    task_projection_pack = dict(task_market_projection_pack or {})
    governance_pack = dict(context_governance_pack or {})
    knowledge_pack = dict(distilled_knowledge_pack or {})
    lobe_directive = _build_aggregate_lobe_directive(
        plan=plan,
        selected_lobe=selected_lobe,
        selected_role=selected_role,
        chain_turn_index=chain_turn_index,
    )
    context.setdefault("state_first_context_os_enabled", True)
    context["aggregate_runtime_context"] = {
        "aggregate_model_id": plan.aggregate_model_id,
        "execution_mode": command.execution_mode,
        "selected_lobe_id": selected_lobe.lobe_id,
        "selected_role_id": selected_role,
        "chain_turn_index": chain_turn_index,
        "execution_order": list(plan.execution_order),
        "prior_handoffs": [dict(item) for item in prior_handoffs],
        "lobe_directive": lobe_directive,
        "failure_signals": list(plan.metadata.get("failure_signals") or ()),
        "failure_evidence": dict(plan.metadata.get("failure_evidence") or {}),
        "takeover_evidence_status": dict(plan.metadata.get("takeover_evidence_status") or {}),
        "akashic_recall_pack": recall_pack,
        "contextos_attention_budget_pack": attention_budget_pack,
        "task_market_projection_pack": task_projection_pack,
        "context_governance_pack": governance_pack,
        "distilled_knowledge_pack": knowledge_pack,
        "takeover_directive": (
            {
                "trigger": plan.takeover_directive.trigger,
                "lobe_id": plan.takeover_directive.lobe_id,
                "action_contract": plan.takeover_directive.action_contract,
            }
            if plan.takeover_directive is not None
            else None
        ),
    }
    return context


def _aggregate_execution_metadata(
    *,
    command: AggregateChatCompletionsCommandV1,
    plan: AggregateRolePlanResultV1,
    selected_lobe: AggregateRoleLobeV1,
    selected_role: str,
    chain_turn_index: int = 0,
    prior_handoffs: tuple[Mapping[str, Any], ...] = (),
    memory_recall_pack: Mapping[str, Any] | None = None,
    contextos_attention_budget_pack: Mapping[str, Any] | None = None,
    task_market_projection_pack: Mapping[str, Any] | None = None,
    context_governance_pack: Mapping[str, Any] | None = None,
    distilled_knowledge_pack: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = dict(command.metadata)
    recall_pack = dict(memory_recall_pack or {})
    attention_budget_pack = dict(contextos_attention_budget_pack or {})
    task_projection_pack = dict(task_market_projection_pack or {})
    governance_pack = dict(context_governance_pack or {})
    knowledge_pack = dict(distilled_knowledge_pack or {})
    lobe_directive = _build_aggregate_lobe_directive(
        plan=plan,
        selected_lobe=selected_lobe,
        selected_role=selected_role,
        chain_turn_index=chain_turn_index,
    )
    metadata.setdefault("context_os_expected", True)
    metadata.setdefault("cognitive_runtime_required", True)
    metadata.setdefault("cognitive_runtime_mode", "mainline")
    metadata["aggregate_execution"] = {
        "planner": "roles.runtime.aggregate_chat_completions.v1",
        "aggregate_model_id": plan.aggregate_model_id,
        "execution_mode": command.execution_mode,
        "selected_lobe_id": selected_lobe.lobe_id,
        "selected_role_id": selected_role,
        "chain_turn_index": chain_turn_index,
        "selected_lobe_phase": selected_lobe.phase,
        "selected_lobe_compute_tier": selected_lobe.compute_tier,
        "lobe_directive": lobe_directive,
        "prior_handoff_count": len(prior_handoffs),
        "takeover_evidence_status": dict(plan.metadata.get("takeover_evidence_status") or {}),
        "akashic_recall_status": {
            "enabled": bool(recall_pack.get("enabled")),
            "status": recall_pack.get("status") or "skipped",
            "projection_count": recall_pack.get("projection_count", 0),
            "injection_allowed_count": recall_pack.get("injection_allowed_count", 0),
        },
        "contextos_attention_budget_status": {
            "enabled": bool(attention_budget_pack.get("enabled")),
            "status": attention_budget_pack.get("status") or "skipped",
            "phase": attention_budget_pack.get("phase"),
            "attention_score_count": len(attention_budget_pack.get("attention_scores") or ()),
        },
        "task_market_projection_status": {
            "enabled": bool(task_projection_pack.get("enabled")),
            "status": task_projection_pack.get("status") or "skipped",
            "total_active": (task_projection_pack.get("summary") or {}).get("total_active", 0),
            "dead_letter_count": (task_projection_pack.get("summary") or {}).get("dead_letter_count", 0),
        },
        "context_governance_status": {
            "enabled": bool(governance_pack.get("enabled")),
            "status": governance_pack.get("status") or "skipped",
            "retrieval_candidate_count": (governance_pack.get("graph_constrained_retrieval") or {}).get(
                "candidate_count", 0
            ),
        },
        "distilled_knowledge_status": {
            "enabled": bool(knowledge_pack.get("enabled")),
            "status": knowledge_pack.get("status") or "skipped",
            "total_available": knowledge_pack.get("total_available", 0),
            "knowledge_unit_count": len(knowledge_pack.get("knowledge_units") or ()),
        },
        "p0_runtime_integrations": [
            item.tech_id for item in plan.runtime_integrations if item.priority == "p0" and item.status == "wired"
        ],
    }
    return metadata


def _aggregate_handoff_from_result(
    *,
    sequence: int,
    selected_lobe: AggregateRoleLobeV1,
    selected_role: str,
    result: RoleExecutionResultV1,
) -> dict[str, Any]:
    runtime_evidence = result.metadata.get("cognitive_runtime_evidence")
    receipt_id = runtime_evidence.get("receipt_id") if isinstance(runtime_evidence, Mapping) else None
    handoff_id = runtime_evidence.get("handoff_id") if isinstance(runtime_evidence, Mapping) else None
    return {
        "sequence": sequence,
        "lobe_id": selected_lobe.lobe_id,
        "role_id": selected_role,
        "status": result.status,
        "ok": result.ok,
        "output_contract": selected_lobe.output_contract,
        "output": result.output,
        "tool_calls": list(result.tool_calls),
        "receipt_id": receipt_id,
        "handoff_id": handoff_id,
    }


def _render_aggregate_execution_content(
    *,
    plan: AggregateRolePlanResultV1,
    selected_lobe: AggregateRoleLobeV1,
    selected_role: str,
    execution_result: RoleExecutionResultV1,
) -> str:
    payload: dict[str, Any] = {
        "aggregate_model_id": plan.aggregate_model_id,
        "execution_mode": "single_turn",
        "selected_lobe_id": selected_lobe.lobe_id,
        "selected_role_id": selected_role,
        "status": execution_result.status,
        "ok": execution_result.ok,
        "output": execution_result.output,
        "tool_calls": list(execution_result.tool_calls),
        "runtime_evidence": {
            "strategy_fingerprint": execution_result.metadata.get("strategy_fingerprint"),
            "context_os_preflight": execution_result.metadata.get("context_os_preflight"),
            "cognitive_runtime_preflight": execution_result.metadata.get("cognitive_runtime_preflight"),
            "cognitive_runtime_evidence": execution_result.metadata.get("cognitive_runtime_evidence"),
        },
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _render_aggregate_chain_content(
    *,
    plan: AggregateRolePlanResultV1,
    chain_lobes: tuple[AggregateRoleLobeV1, ...],
    chain_roles: tuple[str, ...],
    execution_results: tuple[RoleExecutionResultV1, ...],
    handoffs: tuple[Mapping[str, Any], ...],
) -> str:
    payload: dict[str, Any] = {
        "aggregate_model_id": plan.aggregate_model_id,
        "execution_mode": "lobe_chain",
        "executed_lobes": [lobe.lobe_id for lobe in chain_lobes],
        "executed_roles": list(chain_roles),
        "status": "ok" if all(result.ok for result in execution_results) else "failed",
        "ok": all(result.ok for result in execution_results),
        "handoffs": [dict(item) for item in handoffs],
        "results": [
            {
                "sequence": index,
                "lobe_id": chain_lobes[index].lobe_id if index < len(chain_lobes) else "",
                "role_id": result.role,
                "status": result.status,
                "ok": result.ok,
                "output": result.output,
                "tool_calls": list(result.tool_calls),
                "runtime_evidence": {
                    "strategy_fingerprint": result.metadata.get("strategy_fingerprint"),
                    "context_os_preflight": result.metadata.get("context_os_preflight"),
                    "cognitive_runtime_preflight": result.metadata.get("cognitive_runtime_preflight"),
                    "cognitive_runtime_evidence": result.metadata.get("cognitive_runtime_evidence"),
                },
            }
            for index, result in enumerate(execution_results)
        ],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _aggregate_objective_from_messages(messages: tuple[AggregateChatMessageV1, ...]) -> str:
    user_messages = [message.content for message in messages if message.role.lower() == "user"]
    if user_messages:
        return user_messages[-1]
    return messages[-1].content


def _stable_completion_id(command: AggregateChatCompletionsCommandV1, objective: str) -> str:
    seed = {
        "workspace": command.workspace,
        "model": command.model,
        "objective": objective,
        "messages": [
            {
                "role": message.role,
                "content": message.content,
                "name": message.name,
            }
            for message in command.messages
        ],
        "role_ids": list(command.role_ids),
        "failure_signals": list(command.failure_signals),
        "failure_evidence": dict(command.failure_evidence),
        "domain": command.domain,
        "execution_mode": command.execution_mode,
        "session_id": command.session_id,
        "run_id": command.run_id,
    }
    digest = hashlib.sha256(json.dumps(seed, ensure_ascii=True, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return f"aggcmpl-{digest}"


def _render_aggregate_plan_content(plan: AggregateRolePlanResultV1) -> str:
    payload: dict[str, Any] = {
        "aggregate_model_id": plan.aggregate_model_id,
        "execution_mode": "plan_only",
        "current_role_ids": list(plan.current_role_ids),
        "execution_order": list(plan.execution_order),
        "required_capability_refs": list(plan.required_capability_refs),
        "failure_signals": list(plan.metadata.get("failure_signals") or ()),
        "failure_evidence": dict(plan.metadata.get("failure_evidence") or {}),
        "takeover_evidence_status": dict(plan.metadata.get("takeover_evidence_status") or {}),
        "runtime_integrations": [
            {
                "tech_id": integration.tech_id,
                "title": integration.title,
                "status": integration.status,
                "priority": integration.priority,
                "production_entrypoints": list(integration.production_entrypoints),
                "entrypoints_verified": integration.entrypoints_verified,
                "missing_entrypoints": list(integration.missing_entrypoints),
                "entrypoint_checks": [
                    {
                        "entrypoint": check.entrypoint,
                        "check_type": check.check_type,
                        "ok": check.ok,
                        "evidence": check.evidence,
                        "reason": check.reason,
                    }
                    for check in integration.entrypoint_checks
                ],
                "evidence_keys": list(integration.evidence_keys),
                "runtime_effects": list(integration.runtime_effects),
                "benefit": integration.benefit,
            }
            for integration in plan.runtime_integrations
        ],
        "compute_policy": dict(plan.compute_policy),
        "takeover_directive": (
            {
                "trigger": plan.takeover_directive.trigger,
                "lobe_id": plan.takeover_directive.lobe_id,
                "compute_tier": plan.takeover_directive.compute_tier,
                "reason": plan.takeover_directive.reason,
                "evidence_keys": list(plan.takeover_directive.evidence_keys),
                "action_contract": plan.takeover_directive.action_contract,
                "next_lobes": list(plan.takeover_directive.next_lobes),
                "status": plan.takeover_directive.status,
            }
            if plan.takeover_directive is not None
            else None
        ),
        "cognitive_ledger": [
            {
                "sequence": item.sequence,
                "lobe_id": item.lobe_id,
                "phase": item.phase,
                "compute_tier": item.compute_tier,
                "reads": list(item.reads),
                "writes": list(item.writes),
                "handoff_to": list(item.handoff_to),
                "takeover_triggers": list(item.takeover_triggers),
            }
            for item in plan.cognitive_ledger
        ],
        "warnings": list(plan.warnings),
        "truthful_migration": str(plan.metadata.get("truthful_migration") or ""),
        "lobes": [
            {
                "lobe_id": lobe.lobe_id,
                "phase": lobe.phase,
                "role_ids": list(lobe.role_ids),
                "virtual_role_ids": list(lobe.virtual_role_ids),
                "capability_refs": list(lobe.capability_refs),
                "attention_masks": list(lobe.attention_masks),
                "memory_triggers": list(lobe.memory_triggers),
                "compute_tier": lobe.compute_tier,
                "handoff_keys": list(lobe.handoff_keys),
                "takeover_triggers": list(lobe.takeover_triggers),
                "output_contract": lobe.output_contract,
                "status": lobe.status,
                "missing_role_ids": list(lobe.missing_role_ids),
            }
            for lobe in plan.lobes
        ],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)
