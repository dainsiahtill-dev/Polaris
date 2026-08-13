"""Chief Engineer v2 delivery routes.

Lossless decomposition (god-module -> package)
----------------------------------------------
This package is the lossless successor of the former single-file
``chief_engineer`` module (1934 lines). It re-exports every previously-public
(and previously-private-but-imported) symbol from the same import path so that
``import polaris.delivery.http.v2.chief_engineer`` and
``from polaris.delivery.http.v2.chief_engineer import X`` keep resolving
identically for all external importers (the v2 router mount, the desktop
client, the router test-suite, and the cell-yaml governance test).

The domain split lives in sibling implementation modules:

  * ``_router``             — the single :class:`fastapi.APIRouter` (mounted
                              unchanged by ``polaris.delivery.http.v2``) plus
                              the shared regex / coercion helpers that have no
                              test-patchable external dependency.
  * ``_schemas``            — the ~25 inline Pydantic request/response models.
  * ``blueprints``          — blueprint lifecycle route handlers
                              (generate / list / status / get / delete / bulk
                              + PM-task-plan reference sync).
  * ``diagnostics``         — workspace / LLM / blueprint diagnostics handlers.
  * ``governance``          — the six governance-domain CRUD handlers
                              (risk / tech-debt / ADR / tech-radar /
                              post-mortem / stack-policy) + the
                              Director-handoff decision gate.
  * ``release_readiness``   — the Tier-2 capstone release-readiness rollup.

Monkeypatch losslessness
------------------------
The router test-suite patches external symbols on this package
(``patch("polaris.delivery.http.v2.chief_engineer.BlueprintPersistence", ...)``
and the like). Those names are therefore re-exported here, and the handler
functions that consume them resolve them *through the live package* at call
time (``import polaris.delivery.http.v2.chief_engineer as _ce; _ce.<Name>``).
This mirrors the proven pattern in
``polaris/infrastructure/llm/providers/provider_helpers``.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

# Re-export stdlib / third-party / cell names that were module-level attributes
# of the former single-file module. The router test-suite monkeypatches several
# of them via dotted string paths (``patch("...chief_engineer.<Name>", ...)``),
# so they MUST remain attributes of this package; the handlers read them back
# off the package namespace at call time (``_ce.<Name>``).
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, Depends, Request
from polaris.cells.chief_engineer.blueprint.public import (
    ADRStatus,
    BlueprintPersistence,
    GenerateTaskBlueprintCommandV1,
    GetBlueprintStatusQueryV1,
    IncidentSeverity,
    ListADRsQueryV1,
    ListPostMortemsQueryV1,
    ListRisksQueryV1,
    ListTechDebtQueryV1,
    ListTechRadarQueryV1,
    PostMortemStatus,
    RegisterADRCommandV1,
    RegisterPostMortemCommandV1,
    RegisterRiskCommandV1,
    RegisterTechDebtCommandV1,
    RegisterTechRadarCommandV1,
    RiskSeverity,
    RiskStatus,
    TaskBlueprintResultV1,
    TechDebtSeverity,
    TechDebtStatus,
    TechRadarRing,
    UpdateADRStatusCommandV1,
    UpdatePostMortemStatusCommandV1,
    UpdateRiskStatusCommandV1,
    UpdateTechDebtStatusCommandV1,
    UpdateTechRadarRingCommandV1,
    assess_release_readiness,
    check_stack_policy,
    evaluate_handoff_decision_for_blueprint,
    generate_task_blueprint,
    get_blueprint_status,
    list_adrs,
    list_post_mortems,
    list_risks,
    list_tech_debt,
    list_tech_radar,
    register_adr,
    register_post_mortem,
    register_risk,
    register_tech_debt,
    register_tech_radar,
    summarize_adrs,
    summarize_post_mortems,
    summarize_risks,
    summarize_tech_debt,
    summarize_tech_radar,
    update_adr_status,
    update_post_mortem_status,
    update_risk_status,
    update_tech_debt_status,
    update_tech_radar_ring,
)
from polaris.cells.roles.kernel.public.service import (
    get_global_emitter,
    get_global_token_budget,
)
from polaris.cells.runtime.projection.public.role_contracts import (
    ChiefEngineerBlueprintDetailV1,
    ChiefEngineerBlueprintListV1,
    ChiefEngineerBlueprintSummaryV1,
)
from polaris.cells.runtime.projection.public.service import build_llm_status
from polaris.delivery.http.routers._shared import (
    StructuredHTTPException,
    ensure_required_roles_ready,
    get_state,
    require_auth,
)
from polaris.delivery.http.workspace import (
    active_workspace_value,
    settings_with_workspace_override,
)
from polaris.kernelone.fs.text_ops import write_json_atomic
from polaris.kernelone.storage import resolve_logical_path
from pydantic import BaseModel, Field

# Domain implementation submodules. Importing them registers their route
# decorators on the shared ``router`` object re-exported below.
from . import (
    blueprints as _blueprints,
    diagnostics as _diagnostics,
    governance as _governance,
    release_readiness as _release_readiness,
)
from ._router import (
    _BLUEPRINT_ID_RE,
    _blueprint_task_id,
    _dict_value,
    _governance_workspace,
    _pm_task_plan_rows,
    _read_json_file,
    _settings_for_request,
    _split_csv,
    _state_for_settings,
    _string_list,
    _task_id_from_plan_task,
    _utc_now,
    _validate_blueprint_id,
    _workspace_value,
    router,
)
from ._schemas import (
    ChiefEngineerBlueprintDeleteResponse,
    ChiefEngineerBlueprintDetailResponse,
    ChiefEngineerBlueprintListResponse,
    ChiefEngineerBlueprintSummary,
    ChiefEngineerBulkBlueprintError,
    ChiefEngineerBulkBlueprintTaskRequest,
    ChiefEngineerBulkGenerateBlueprintRequest,
    ChiefEngineerBulkGenerateBlueprintResponse,
    ChiefEngineerDiagnosticsBlueprintStatus,
    ChiefEngineerDiagnosticsLLMStatus,
    ChiefEngineerDiagnosticsResponse,
    ChiefEngineerDiagnosticsWorkspaceStatus,
    ChiefEngineerGenerateBlueprintRequest,
    ChiefEngineerPMTaskPlanProbe,
    ChiefEngineerRegisterADRRequest,
    ChiefEngineerRegisterPostMortemRequest,
    ChiefEngineerRegisterRiskRequest,
    ChiefEngineerRegisterTechDebtRequest,
    ChiefEngineerRegisterTechRadarRequest,
    ChiefEngineerStackPolicyCheckRequest,
    ChiefEngineerTaskBlueprintResultResponse,
    ChiefEngineerUpdateADRStatusRequest,
    ChiefEngineerUpdatePostMortemStatusRequest,
    ChiefEngineerUpdateRiskStatusRequest,
    ChiefEngineerUpdateTechDebtStatusRequest,
    ChiefEngineerUpdateTechRadarRingRequest,
)
from .blueprints import (
    _apply_blueprint_references_to_plan_payload,
    _blueprint_id_from_payload,
    _blueprint_payload_for_result,
    _blueprint_reference_update,
    _blueprint_result_response,
    _blueprint_summary,
    _generate_blueprint_for_task,
    _persistence_for_request,
    _run_contract_copy_path,
    _sync_blueprint_references_or_raise,
    _sync_blueprint_references_to_pm_task_plans,
    bulk_generate_chief_engineer_blueprints,
    delete_chief_engineer_blueprint,
    generate_chief_engineer_blueprint,
    get_chief_engineer_blueprint,
    get_chief_engineer_blueprint_status,
    list_chief_engineer_blueprints,
)
from .diagnostics import (
    _blueprint_contract_list,
    _blueprint_handoff_missing_fields,
    _blueprint_payload_is_handoff_ready,
    _blueprint_payload_is_traceability_only,
    _build_blueprint_diagnostics,
    _build_llm_diagnostics,
    _build_workspace_diagnostics,
    _diagnostic_issues,
    _generate_blockers,
    _handoff_blockers,
    _llm_event_stats,
    _load_pm_task_plan_probe,
    _pm_task_plan_candidate_paths,
    _role_payload,
    clear_chief_engineer_cache,
    get_chief_engineer_cache_stats,
    get_chief_engineer_diagnostics,
    get_chief_engineer_llm_events,
    get_chief_engineer_token_budget_stats,
)
from .governance import (
    check_chief_engineer_stack_policy,
    get_chief_engineer_handoff_decision,
    list_chief_engineer_adrs,
    list_chief_engineer_post_mortems,
    list_chief_engineer_risks,
    list_chief_engineer_tech_debt,
    list_chief_engineer_tech_radar,
    register_chief_engineer_adr,
    register_chief_engineer_post_mortem,
    register_chief_engineer_risk,
    register_chief_engineer_tech_debt,
    register_chief_engineer_tech_radar,
    update_chief_engineer_adr_status,
    update_chief_engineer_post_mortem_status,
    update_chief_engineer_risk_status,
    update_chief_engineer_tech_debt_status,
    update_chief_engineer_tech_radar_ring,
)
from .release_readiness import get_chief_engineer_release_readiness

# Keep submodule references alive on the package namespace; the handlers
# resolve test-patchable symbols back through this package at call time.
_ = (_blueprints, _diagnostics, _governance, _release_readiness)

# Public surface (names intended for external import). Private symbols are
# intentionally NOT hidden because the router test-suite monkeypatches several
# of them via dotted string paths; losslessness requires they remain accessible
# as package attributes (handled by the explicit re-exports above).
__all__ = [
    "ChiefEngineerBlueprintDeleteResponse",
    "ChiefEngineerBlueprintDetailResponse",
    "ChiefEngineerBlueprintListResponse",
    "ChiefEngineerBlueprintSummary",
    "ChiefEngineerBulkBlueprintError",
    "ChiefEngineerBulkBlueprintTaskRequest",
    "ChiefEngineerBulkGenerateBlueprintRequest",
    "ChiefEngineerBulkGenerateBlueprintResponse",
    "ChiefEngineerDiagnosticsBlueprintStatus",
    "ChiefEngineerDiagnosticsLLMStatus",
    "ChiefEngineerDiagnosticsResponse",
    "ChiefEngineerDiagnosticsWorkspaceStatus",
    "ChiefEngineerGenerateBlueprintRequest",
    "ChiefEngineerPMTaskPlanProbe",
    "ChiefEngineerRegisterADRRequest",
    "ChiefEngineerRegisterPostMortemRequest",
    "ChiefEngineerRegisterRiskRequest",
    "ChiefEngineerRegisterTechDebtRequest",
    "ChiefEngineerRegisterTechRadarRequest",
    "ChiefEngineerStackPolicyCheckRequest",
    "ChiefEngineerTaskBlueprintResultResponse",
    "ChiefEngineerUpdateADRStatusRequest",
    "ChiefEngineerUpdatePostMortemStatusRequest",
    "ChiefEngineerUpdateRiskStatusRequest",
    "ChiefEngineerUpdateTechDebtStatusRequest",
    "ChiefEngineerUpdateTechRadarRingRequest",
    "bulk_generate_chief_engineer_blueprints",
    "check_chief_engineer_stack_policy",
    "clear_chief_engineer_cache",
    "delete_chief_engineer_blueprint",
    "generate_chief_engineer_blueprint",
    "get_chief_engineer_blueprint",
    "get_chief_engineer_blueprint_status",
    "get_chief_engineer_cache_stats",
    "get_chief_engineer_diagnostics",
    "get_chief_engineer_handoff_decision",
    "get_chief_engineer_llm_events",
    "get_chief_engineer_release_readiness",
    "get_chief_engineer_token_budget_stats",
    "list_chief_engineer_adrs",
    "list_chief_engineer_blueprints",
    "list_chief_engineer_post_mortems",
    "list_chief_engineer_risks",
    "list_chief_engineer_tech_debt",
    "list_chief_engineer_tech_radar",
    "register_chief_engineer_adr",
    "register_chief_engineer_post_mortem",
    "register_chief_engineer_risk",
    "register_chief_engineer_tech_debt",
    "register_chief_engineer_tech_radar",
    "router",
    "update_chief_engineer_adr_status",
    "update_chief_engineer_post_mortem_status",
    "update_chief_engineer_risk_status",
    "update_chief_engineer_tech_debt_status",
    "update_chief_engineer_tech_radar_ring",
]
