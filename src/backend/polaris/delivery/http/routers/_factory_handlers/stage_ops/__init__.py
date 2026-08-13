# ruff: noqa: E402, F403
"""Factory HTTP router helpers — stage ops package.

This package is the lossless successor of the former single-file ``stage_ops``
module. It re-exports every previously-public (and
previously-private-but-imported) symbol from the same import path so that
``import ...stage_ops`` and ``from ...stage_ops import X`` keep resolving
identically for all external importers, and so that ``factory.py``'s
``_rebind_helper_module`` continues to rebind these callables into the host
router namespace (where unit tests monkeypatch them via
``monkeypatch.setattr(factory_router_module, <name>, ...)``).

Losslessness constraints honored here:

1. The former monolith did ``from .mapping import *``. Consumers
   (``runtime.py``, ``factory.py``, tests) reach mapping names through
   ``stage_ops``'s namespace, so this package must re-export the mapping surface
   too. The explicit import of mapping names below preserves the module-level
   attribute surface that ``dir()`` / introspection fences rely on.

2. ``factory.py``'s ``_rebind_helper_module`` copies functions whose
   ``__module__ == "_factory_handlers.stage_ops"`` into the host router module
   globals (so free-name lookups inside them observe monkeypatches on the host).
   After the split, functions are defined in sibling submodules and would carry
   a submodule ``__module__`` that the rebind treats as "imported, skip". We
   therefore rewrite ``__module__`` of every re-exported callable to this
   package's ``__name__`` BEFORE the host rebind runs. This restores the exact
   monolith behavior: the rebind creates new ``FunctionType`` objects bound to
   host globals, so monkeypatches on ``factory.<name>`` are observed by every
   re-exported helper.

3. Cross-module free-name resolution between sibling submodules is handled by
   ``_wire_cross_module_namespace``, mirroring the proven pattern in
   ``polaris/cells/roles/adapters/internal/director/quality_gate/__init__`` and
   ``polaris/infrastructure/llm/providers/provider_helpers/__init__``.
"""

from __future__ import annotations

# Re-export stdlib / third-party / kernelone names that were module-level
# attributes of the former single-file module. Consumers and tests reference
# them as ``stage_ops.<name>`` (e.g. ``stage_ops.logger``) so they MUST remain
# attributes of this package. Submodule imports follow after this block; E402
# is expected and lossless.
import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from polaris.cells.control_plane.run_ledger.public.contracts import (
    ReadRunLedgerProjectionQueryV1,
)
from polaris.cells.control_plane.run_ledger.public.service import (
    read_run_ledger_projection,
)
from polaris.cells.factory.pipeline.public import (
    FACTORY_TERMINAL_TASK_RUNTIME_PROJECTION_METADATA_KEY,
    FactoryRun,
    FactoryRunService,
    FactoryTerminalTaskRuntimeProjectionV1,
)
from polaris.cells.factory.pipeline.public.types import (
    FactoryStartRequest,
)
from polaris.cells.runtime.task_runtime.public.evidence import (
    task_row_execution_event_failure,
)
from polaris.cells.runtime.task_runtime.public.service import (
    TaskRuntimeService,
)
from polaris.delivery.http.routers._shared import (
    StructuredHTTPException,
    ensure_required_roles_ready,
)
from polaris.kernelone.constants import DEFAULT_DIRECTOR_MAX_PARALLELISM
from polaris.kernelone.fs.text_ops import write_text_atomic
from polaris.kernelone.llm.budget_policy import (
    resolve_director_dispatch_timeout_seconds,
)
from polaris.kernelone.quality import (
    ScopeAuthorityOwnerHandoffIndex,
    ScopeAuthorityOwnerHandoffRouting,
    owner_handoff_index_summary,
    ownership_handoff_requests_from_scope_payload,
    resolve_owner_handoff_routing,
    task_identifier_token_aliases,
    task_record_routing_key,
)
from polaris.kernelone.storage import (
    resolve_logical_path,
    resolve_runtime_path,
    resolve_storage_roots,
)

if TYPE_CHECKING:
    from polaris.cells.runtime.state_owner.public.service import AppState

logger = logging.getLogger("polaris.delivery.http.routers.factory")

# The former monolith did ``from .mapping import *`` and depended on several
# mapping constants/helpers at module scope. Preserve that surface so all
# consumers (runtime.py, factory.py, tests) that reach mapping names through
# stage_ops keep resolving them. ``__all__`` in ``mapping`` already exports
# every name below, so the star import is sufficient and lossless.
from ..mapping import *

# Implementation submodules (domain split of former single-file module).
from . import (
    _common as _common,
    artifacts as artifacts,
    director_resume as director_resume,
    failure_classification as failure_classification,
    quality_gate_rework as quality_gate_rework,
    run_summary as run_summary,
    stage_context as stage_context,
)
from ._common import (
    _check_docs_ready,
    _guard_automatic_router_mutation,
    _json_payload,
    _load_json_object,
    _pm_plan_task_count,
    _pm_plan_task_ids,
    _read_json_artifact,
    _resolve_loop_max_cycles,
    _resolve_loop_stall_threshold,
    _resolve_quality_rework_max_cycles,
    _resolve_runtime_path,
    _resolve_task_identifier,
    _safe_events_tail_limit,
    _write_json_text_atomic,
)
from .artifacts import (
    _artifact_item_from_path,
    _artifact_item_from_stage_ref,
    _artifact_response_path,
    _build_artifacts_response,
    _extract_task_id_from_payload,
    _list_run_artifacts,
    _list_stage_artifacts,
    _merge_artifact_items,
    _task_id_from_artifact_file,
    _task_id_from_artifact_name,
)
from .director_resume import (
    _bind_director_resume_chief_engineer_review,
    _chief_engineer_blueprint_count,
    _chief_engineer_review_evidence,
    _director_resume_source_task_dirs,
    _director_resume_task_files,
    _director_resume_task_payloads,
    _director_resume_task_rows_mtime,
    _director_resume_taskboard_score,
    _director_resume_workspace_slug,
    _ensure_director_resume_evidence_ready,
    _pre_director_snapshot_ready,
    _raise_director_resume_task_runtime_failure,
    _rehydrate_director_resume_taskboard,
    _reset_current_director_resume_taskboard,
    _taskboard_record_count,
)
from .failure_classification import (
    _classify_factory_failure_code,
    _factory_failure_suggestion,
)
from .quality_gate_rework import (
    _apply_quality_gate_task_boundary_rework_requests,
    _decide_delivery_loop_action,
    _ownership_handoff_requests_from_repair_payload,
    _quality_gate_handoff_summary_from_payload,
    _quality_gate_owner_handoff_index,
    _quality_gate_owner_handoff_routing,
    _read_docs_pipeline_state,
    _read_pm_plan_signature,
    _read_quality_gate_rework_summary,
    _read_task_boundary_workspace_validation,
    _record_factory_task_runtime_transition_failure,
    _safe_rework_int,
    _task_boundary_rework_evidence,
    _task_record_needs_task_boundary_rework,
    _workspace_validation_requests_task_boundary_rework,
)
from .run_summary import (
    _attach_control_plane_projection,
    _build_director_convergence,
    _build_factory_audit_bundle,
    _build_summary_json,
    _build_summary_markdown,
    _count_events_by_type,
    _extract_missing_delivery_targets,
    _extract_per_binding_task_status,
    _extract_taskboard_snapshots,
    _factory_run_identity,
    _model_dump_json_dict,
    _persist_run_summary,
)
from .stage_context import (
    _build_stage_context,
    _build_stage_list,
    _ensure_factory_runtime_ready,
    _normalize_start_from,
    _required_ready_roles_for_stages,
    _settings_qa_enabled,
)


def _wire_cross_module_namespace() -> None:
    """Inject sibling symbols into each submodule globals for free-name lookup.

    Functions defined in submodules resolve free names via their module
    ``__dict__``. After the package re-exports every symbol, copy non-owned
    names into each submodule so cross-module calls remain lossless without
    rewriting call sites. Ownership is each submodule's ``__all__``.

    This mirrors the proven pattern in
    ``polaris/cells/roles/adapters/internal/director/quality_gate/__init__`` and
    ``polaris/infrastructure/llm/providers/provider_helpers/__init__``.
    """
    import sys

    pkg = sys.modules[__name__]
    shared = {key: value for key, value in pkg.__dict__.items() if not key.startswith("__")}
    for mod in (
        _common,
        director_resume,
        stage_context,
        quality_gate_rework,
        artifacts,
        run_summary,
        failure_classification,
    ):
        owned = set(getattr(mod, "__all__", ()) or ())
        for key, value in shared.items():
            if key not in owned:
                mod.__dict__[key] = value


_wire_cross_module_namespace()


def _rebrand_callable_modules() -> None:
    """Rewrite ``__module__`` of re-exported callables to this package's name.

    ``factory.py``'s ``_rebind_helper_module`` copies functions whose
    ``__module__`` equals the source helper module's ``__name__`` into the host
    router module globals as fresh ``FunctionType`` objects bound to host
    globals. That is how unit-test monkeypatches on
    ``factory.<name>`` are observed by helper bodies. After the split, each
    callable's ``__module__`` points at its defining submodule and the rebind
    would skip it as an "imported sibling". Rewriting ``__module__`` to this
    package's ``__name__`` restores the exact monolith behavior.

    Only callables DEFINED in this package (a submodule whose dotted name starts
    with this package's name) are rebranded. Imported kernelone / stdlib helpers
    (e.g. ``resolve_owner_handoff_routing``, ``task_record_routing_key``) keep
    their original ``__module__`` so the host rebind does NOT rebind them with
    host globals (which would sever their internal free-name resolution).
    """
    import sys
    import types

    pkg_name = __name__
    pkg = sys.modules[pkg_name]
    for key, value in list(pkg.__dict__.items()):
        if key.startswith("__"):
            continue
        if not isinstance(value, (types.FunctionType, types.BuiltinFunctionType)):
            continue
        original_module = getattr(value, "__module__", None)
        # Only rebrand callables whose home is one of this package's submodules.
        # These are the helpers that ``factory.py``'s rebind must copy with host
        # globals so monkeypatches on the host are observed.
        if original_module is None or not original_module.startswith(pkg_name + "."):
            continue
        with contextlib.suppress(AttributeError, TypeError):
            value.__module__ = pkg_name


_rebrand_callable_modules()


__all__ = [
    "_apply_quality_gate_task_boundary_rework_requests",
    "_artifact_item_from_path",
    "_artifact_item_from_stage_ref",
    "_artifact_response_path",
    "_attach_control_plane_projection",
    "_bind_director_resume_chief_engineer_review",
    "_build_artifacts_response",
    "_build_director_convergence",
    "_build_factory_audit_bundle",
    "_build_stage_context",
    "_build_stage_list",
    "_build_summary_json",
    "_build_summary_markdown",
    "_check_docs_ready",
    "_chief_engineer_blueprint_count",
    "_chief_engineer_review_evidence",
    "_classify_factory_failure_code",
    "_count_events_by_type",
    "_decide_delivery_loop_action",
    "_director_resume_source_task_dirs",
    "_director_resume_task_files",
    "_director_resume_task_payloads",
    "_director_resume_task_rows_mtime",
    "_director_resume_taskboard_score",
    "_director_resume_workspace_slug",
    "_ensure_director_resume_evidence_ready",
    "_ensure_factory_runtime_ready",
    "_extract_missing_delivery_targets",
    "_extract_per_binding_task_status",
    "_extract_task_id_from_payload",
    "_extract_taskboard_snapshots",
    "_factory_failure_suggestion",
    "_factory_run_identity",
    "_guard_automatic_router_mutation",
    "_json_payload",
    "_list_run_artifacts",
    "_list_stage_artifacts",
    "_load_json_object",
    "_merge_artifact_items",
    "_model_dump_json_dict",
    "_normalize_start_from",
    "_ownership_handoff_requests_from_repair_payload",
    "_persist_run_summary",
    "_pm_plan_task_count",
    "_pm_plan_task_ids",
    "_pre_director_snapshot_ready",
    "_quality_gate_handoff_summary_from_payload",
    "_quality_gate_owner_handoff_index",
    "_quality_gate_owner_handoff_routing",
    "_raise_director_resume_task_runtime_failure",
    "_read_docs_pipeline_state",
    "_read_json_artifact",
    "_read_pm_plan_signature",
    "_read_quality_gate_rework_summary",
    "_read_task_boundary_workspace_validation",
    "_record_factory_task_runtime_transition_failure",
    "_rehydrate_director_resume_taskboard",
    "_required_ready_roles_for_stages",
    "_reset_current_director_resume_taskboard",
    "_resolve_loop_max_cycles",
    "_resolve_loop_stall_threshold",
    "_resolve_quality_rework_max_cycles",
    "_resolve_runtime_path",
    "_resolve_task_identifier",
    "_safe_events_tail_limit",
    "_safe_rework_int",
    "_settings_qa_enabled",
    "_task_boundary_rework_evidence",
    "_task_id_from_artifact_file",
    "_task_id_from_artifact_name",
    "_task_record_needs_task_boundary_rework",
    "_taskboard_record_count",
    "_workspace_validation_requests_task_boundary_rework",
    "_write_json_text_atomic",
]
