"""DirectorAdapter class implementation."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Mapping
from dataclasses import replace
from typing import Any, cast

from polaris.cells.chief_engineer.blueprint.public import (
    GetBlueprintStatusQueryV1,
    get_blueprint_status,
    validate_director_handoff_from_payload,
)
from polaris.cells.director.tasking.public.execution_guidance import (
    apply_task_execution_strategy_overrides,
    build_task_language_section,
    coerce_task_execution_profile,
    resolve_task_execution_profile,
    resolve_task_execution_strategy,
)
from polaris.cells.runtime.execution_broker.public import (
    RecordProjectArtifactCommandV1,
    record_project_artifact,
)

from ...base import BaseRoleAdapter
from ...director_execution_backend import (
    DirectorExecutionBackendRequest,
    resolve_director_execution_backend,
)
from ..adapter_sequential import (
    build_sequential_config,
    execute_hybrid,
    execute_sequential,
)
from ..dependency_artifact_evidence import (
    DIRECTOR_DEPENDENCY_ARTIFACT_SNAPSHOT_CONTEXT_KEY,
    DirectorDependencyArtifactEvidenceError,
    TrustedDirectorDependencyArtifactSnapshotV2,
    build_director_dependency_artifact_snapshot,
    project_director_dependency_artifact_snapshot,
    query_project_artifact_receipt_payload,
)
from ..dialogue import get_settings_safe
from ..execute_method import execute_director_task
from ..execution import DirectorPatchExecutor
from ..helpers import (
    is_empty_role_response,
    taskboard_snapshot_brief,
)
from ..state_tracking import DirectorStateTracker
from ..state_utils import (
    compose_projection_requirement,
    default_projection_slug,
)
from ._payload import (
    _copy_mapping_payload,
    _first_dict_list_payload,
    _first_mapping_payload,
    _project_director_execution_authority_evidence,
    _string_list_payload,
)
from ._role_response import (
    _extract_director_verification_commands,
    _normalize_director_role_response,
)
from ._task_contract import (
    _AUTHORITATIVE_TASK_BOUNDARY_LIST_KEYS,
    _ROLE_RUNTIME_METADATA_CONTEXT_EVIDENCE_KEYS,
    _TASK_CONTRACT_LIST_KEYS,
    _TASK_CONTRACT_MAPPING_KEYS,
    _TASK_CONTRACT_SCALAR_KEYS,
    _TASK_RUNTIME_GOVERNANCE_SCALAR_KEYS,
    _build_director_blueprint_handoff_lines,
    _director_actual_interface_injection_enabled,
    _first_contract_value,
    _has_contract_value,
    _load_ce_blueprint_contract_payload,
    _merge_ce_blueprint_contract_payload,
    _merge_contract_lists,
    _promoted_task_contract_payload,
    _set_structured_task_contract_slot,
    _task_contract_sources,
)
from ._timeout_budget import (
    _context_timeout_seconds_for_runtime_command,
    _prepare_role_dialogue_context,
    _role_dialogue_watchdog_timeout_seconds,
)

logger = logging.getLogger("polaris.cells.roles.adapters.internal.director.adapter")


class DirectorAdapter(BaseRoleAdapter):
    """Director 角色适配器

    职责：
    - 任务执行
    - 代码改写
    - 验证与测试
    - 工具调用
    """

    def __init__(self, workspace: str, task_runtime: Any = None) -> None:
        super().__init__(workspace)
        if task_runtime is not None:
            self._task_runtime = task_runtime
        self._state_tracker = DirectorStateTracker(workspace)
        self._execution = DirectorPatchExecutor(workspace)

    @property
    def role_id(self) -> str:
        return "director"

    def get_capabilities(self) -> list[str]:
        return [
            "execute_task",
            "write_code",
            "edit_file",
            "run_command",
            "verify_result",
            "sequential_execution",
            "adaptive_strategy_selection",
            "intelligent_self_correction",
            "multi_objective_optimization",
        ]

    # -------------------------------------------------------------------------
    # Main Execute Method
    # -------------------------------------------------------------------------

    async def execute(
        self,
        task_id: str,
        input_data: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """执行 Director 任务"""
        # Phase 2.4: Pre-execution strategy selection based on task characteristics
        directive = str(input_data.get("input") or input_data.get("directive") or "").strip()
        task_data = input_data.get("task") or input_data
        selected_strategy = self._select_execution_strategy(directive, task_data, context)
        if selected_strategy != "default":
            logger.info("Director strategy selected: %s for task %s", selected_strategy, task_id)
        self._reset_task_runtime_transition_failures()

        # Inject strategy into context for downstream use
        if context is not None:
            ctx_metadata = context.get("metadata") if isinstance(context, dict) else None
            if ctx_metadata is None:
                ctx_metadata = {}
                context["metadata"] = ctx_metadata
            if isinstance(ctx_metadata, dict):
                ctx_metadata["director_strategy"] = selected_strategy

        result = await execute_director_task(self, task_id, input_data, context)
        if not isinstance(result, dict):
            return result
        return self._with_task_runtime_transition_failure_evidence(result)

    def _select_execution_strategy(
        self,
        directive: str,
        task: dict[str, Any],
        context: dict[str, Any],
    ) -> str:
        """Phase 2.4: Select optimal execution strategy based on task characteristics.

        Args:
            directive: Task directive text
            task: Task data dictionary
            context: Execution context (may contain architect constraints)

        Returns:
            Strategy name: 'default', 'incremental', 'aggressive', 'conservative', 'focused'
        """
        strategy_factors: list[str] = []

        # Check architect constraints from context
        ctx_metadata = context.get("metadata") if isinstance(context, dict) else None
        architect_constraints = []
        if isinstance(ctx_metadata, dict):
            architect_constraints = ctx_metadata.get("architect_constraints", [])

        # Check for concerns from architect
        has_architect_concerns = any(c.get("type") == "concern" for c in architect_constraints if isinstance(c, dict))
        if has_architect_concerns:
            return "conservative"  # Be careful when architect raised concerns

        # Analyze task complexity
        if len(directive) > 300:
            strategy_factors.append("complex_directive")
        if "test" in directive.lower() or "verify" in directive.lower():
            strategy_factors.append("verification_focused")
        if "refactor" in directive.lower() or "重构" in directive:
            strategy_factors.append("refactoring")

        # Check for file targets
        target_files = task.get("target_files", []) if isinstance(task, dict) else []
        scope_files = task.get("scope_paths", []) if isinstance(task, dict) else []
        total_files = len(target_files) + len(scope_files)

        if total_files >= 10:
            strategy_factors.append("large_scope")
        elif total_files >= 5:
            strategy_factors.append("medium_scope")

        # Determine strategy
        if "large_scope" in strategy_factors and "complex_directive" in strategy_factors:
            return "incremental"
        if "refactoring" in strategy_factors:
            return "conservative"
        if "verification_focused" in strategy_factors:
            return "focused"
        if "medium_scope" in strategy_factors and "complex_directive" in strategy_factors:
            return "aggressive"
        return "default"

    def _apply_intelligent_correction(
        self,
        attempt_result: dict[str, Any],
        previous_attempts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Phase 2.4: Apply intelligent self-correction based on failure patterns.

        Args:
            attempt_result: Result of current execution attempt
            previous_attempts: List of previous attempt results

        Returns:
            Modified result with correction hints
        """
        if attempt_result.get("success", False):
            return attempt_result

        # Analyze failure patterns from previous attempts
        failure_types: dict[str, int] = {}
        for prev in previous_attempts:
            error = str(prev.get("error") or "")
            if "timeout" in error.lower():
                failure_types["timeout"] = failure_types.get("timeout", 0) + 1
            elif "syntax" in error.lower() or "语法" in error:
                failure_types["syntax_error"] = failure_types.get("syntax_error", 0) + 1
            elif "not found" in error.lower() or "找不到" in error:
                failure_types["missing_dependency"] = failure_types.get("missing_dependency", 0) + 1
            elif "permission" in error.lower() or "权限" in error:
                failure_types["permission"] = failure_types.get("permission", 0) + 1
            else:
                failure_types["unknown"] = failure_types.get("unknown", 0) + 1

        # Generate correction hints based on failure patterns
        correction_hints: list[str] = []
        for failure_type, count in failure_types.items():
            if count >= 2:
                if failure_type == "timeout":
                    correction_hints.append("Consider breaking down into smaller steps")
                elif failure_type == "syntax_error":
                    correction_hints.append("Check syntax before applying changes")
                elif failure_type == "missing_dependency":
                    correction_hints.append("Ensure all dependencies are available first")
                elif failure_type == "permission":
                    correction_hints.append("Verify file permissions before writing")

        if correction_hints:
            attempt_result["_correction_hints"] = correction_hints

        return attempt_result

    # -------------------------------------------------------------------------
    # Sequential Engine Configuration
    # -------------------------------------------------------------------------

    def _get_sequential_config(self, context: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Get Sequential configuration from settings and context."""
        settings = get_settings_safe()
        return build_sequential_config(settings, context)

    # -------------------------------------------------------------------------
    # Sequential Engine Execution
    # -------------------------------------------------------------------------

    async def _execute_sequential(
        self,
        task: dict[str, Any],
        task_id: str,
        run_id: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute task using Sequential Engine."""
        seq_config = self._get_sequential_config(context)
        if not seq_config:
            return {"success": False, "error": "Sequential not enabled"}
        timeout_seconds = float(seq_config["budget"].max_wall_time_seconds)

        async def _call_canonical_role_runtime(
            message: str,
            *,
            context: dict[str, Any] | None,
        ) -> dict[str, Any]:
            return await self._invoke_role_dialogue_with_timeout(
                message,
                context=context,
                timeout_seconds=timeout_seconds,
                stage_label="sequential",
            )

        return await execute_sequential(
            self.workspace,
            self.role_id,
            task,
            task_id,
            run_id,
            context,
            seq_config,
            _call_canonical_role_runtime,
            self._emit_task_trace_event,
            self._build_director_message,
        )

    async def _execute_hybrid(
        self,
        task: dict[str, Any],
        task_id: str,
        run_id: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute task using Hybrid Engine."""
        seq_config = self._get_sequential_config(context)
        if not seq_config:
            return {"success": False, "error": "Sequential not enabled"}
        timeout_seconds = float(seq_config["budget"].max_wall_time_seconds)

        async def _call_canonical_role_runtime(
            message: str,
            *,
            context: dict[str, Any] | None,
        ) -> dict[str, Any]:
            return await self._invoke_role_dialogue_with_timeout(
                message,
                context=context,
                timeout_seconds=timeout_seconds,
                stage_label="hybrid",
            )

        return await execute_hybrid(
            self.workspace,
            self.role_id,
            task,
            task_id,
            run_id,
            context,
            seq_config,
            self._emit_task_trace_event,
            _call_canonical_role_runtime,
            self._build_director_message,
        )

    # -------------------------------------------------------------------------
    # Role LLM Invocation
    # -------------------------------------------------------------------------

    async def _invoke_role_dialogue(
        self,
        message: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Invoke Director through the canonical role runtime first."""
        llm_max_retries = self._resolve_kernel_retry_budget(self.role_id)

        try:
            runtime_response = await self._invoke_role_runtime_session(
                message,
                context=context,
                max_retries=llm_max_retries,
            )
        except (ImportError, AttributeError) as exc:
            raise RuntimeError("director_role_runtime_boundary_unavailable") from exc
        else:
            primary = _normalize_director_role_response(runtime_response)
            if bool(primary.get("success")) and not is_empty_role_response(primary):
                return primary
            primary["error"] = str(primary.get("error") or "director_role_runtime_empty_response")
            primary["success"] = False
            return primary

    async def _invoke_role_runtime_session(
        self,
        message: str,
        *,
        context: dict[str, Any] | None,
        max_retries: int,
    ) -> dict[str, Any]:
        """Call roles.runtime so Context OS and Cognitive Runtime participate."""

        from polaris.cells.roles.adapters.public import (
            directed_effect_policy_service,
            directed_effect_service as directed_effect_mutation_service,
        )
        from polaris.cells.roles.kernel.public import (
            DirectedEffectRuntimeDependenciesV1,
            directed_effect_service as directed_effect_fence_service,
        )
        from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService
        from polaris.cells.runtime.task_runtime.public import (
            TaskRuntimeExecutionAttemptAuthoritySnapshotV1,
            TaskRuntimeExecutionAttemptAuthorityV1,
            TaskRuntimeExecutionAttemptIdentityV1,
        )

        context_payload = dict(context) if isinstance(context, dict) else {}
        # Quality-repair / no-write / empty-write retries often arrive without the
        # non-serializable TrustedDirectorDependencyArtifactSnapshotV2 token. Projecting
        # None then wipes actual_sibling_exports and final-request coverage fails closed
        # with missing_required_refs=actual_sibling_exports (L1-01 r122 TASK-2 follow-up).
        self._rebind_director_dependency_artifact_for_dialogue(context_payload)
        trusted_dependency_snapshot = context_payload.pop(
            DIRECTOR_DEPENDENCY_ARTIFACT_SNAPSHOT_CONTEXT_KEY,
            None,
        )
        project_director_dependency_artifact_snapshot(
            context_payload,
            (
                trusted_dependency_snapshot
                if type(trusted_dependency_snapshot) is TrustedDirectorDependencyArtifactSnapshotV2
                else None
            ),
        )
        self._ensure_director_verification_commands(
            message=message,
            context=context_payload,
        )
        metadata = self._build_role_runtime_metadata(context_payload, max_retries=max_retries)
        self._ensure_director_execution_profile(
            message=message,
            context=context_payload,
            metadata=metadata,
            workspace=str(self.workspace),
        )
        _project_director_execution_authority_evidence(context_payload, context)
        task_id = self._resolve_runtime_identity_field(
            context_payload,
            metadata,
            keys=("task_id", "pm_task_id", "target_task_id", "id"),
        )
        run_id = self._resolve_runtime_identity_field(
            context_payload,
            metadata,
            keys=("run_id", "workflow_run_id", "observer_run_id"),
        )
        session_id = self._resolve_role_runtime_session_id(
            context_payload,
            metadata=metadata,
            task_id=task_id,
            run_id=run_id,
            message=message,
        )
        authority = context_payload.get("task_runtime_execution_attempt_authority")
        if isinstance(authority, TaskRuntimeExecutionAttemptAuthorityV1):
            try:
                snapshot = authority.snapshot(lock_timeout_seconds=5.0)
            except (AttributeError, RuntimeError, TypeError, ValueError):
                snapshot = None
            if (
                type(snapshot) is TaskRuntimeExecutionAttemptAuthoritySnapshotV1
                and snapshot.success
                and not snapshot.closed
                and type(snapshot.identity) is TaskRuntimeExecutionAttemptIdentityV1
            ):
                attempt_identity = snapshot.identity
                # TaskRuntime rows use a private integer id while guarded role
                # sessions bind to the PM/CE external task identity. Preserve a
                # genuinely drifting caller id so RoleRuntime still rejects it;
                # normalize only the canonical internal-row projection.
                if task_id == str(attempt_identity.task_id):
                    context_payload["task_runtime_internal_task_id"] = task_id
                    metadata["task_runtime_internal_task_id"] = task_id
                    task_id = attempt_identity.external_task_id
        timeout_seconds = _context_timeout_seconds_for_runtime_command(context_payload)
        command = ExecuteRoleSessionCommandV1(
            role=self.role_id,
            session_id=session_id,
            workspace=str(self.workspace),
            user_message=message,
            run_id=run_id or None,
            task_id=task_id or None,
            domain=str(metadata.get("domain") or "code"),
            history=self._normalize_role_runtime_history(context_payload),
            context=context_payload,
            metadata=metadata,
            stream=False,
            host_kind="director_adapter",
            timeout_seconds=timeout_seconds,
        )
        policy_snapshot_port = directed_effect_policy_service.create_director_effect_policy_snapshot_port(
            str(self.workspace)
        )
        fence_ports = directed_effect_fence_service.create_directed_effect_fence_ports()
        mutation_port = directed_effect_mutation_service.create_director_directed_effect_mutation_port(
            workspace=str(self.workspace),
            policy_snapshot_port=policy_snapshot_port,
            fence_consume_port=fence_ports.consume,
        )
        directed_effect_runtime = DirectedEffectRuntimeDependenciesV1(
            policy_snapshot_port=policy_snapshot_port,
            fence_admin_port=fence_ports.admin,
            mutation_port=mutation_port,
        )
        runtime = RoleRuntimeService(
            directed_effect_runtime=directed_effect_runtime,
            directed_effect_required=True,
        )
        result = await runtime.execute_role_session(command)
        result_metadata = dict(getattr(result, "metadata", {}) or {})
        result_usage = dict(getattr(result, "usage", {}) or {})
        output = str(getattr(result, "output", "") or "")
        error = str(getattr(result, "error_message", "") or getattr(result, "error_code", "") or "").strip()
        batch_receipt = _first_mapping_payload(
            result_metadata.get("batch_receipt"),
            result_usage.get("batch_receipt"),
            getattr(result, "batch_receipt", None),
        )
        tool_results = _first_dict_list_payload(
            result_metadata.get("tool_results"),
            result_usage.get("tool_results"),
            getattr(result, "tool_results", None),
        )
        observed_tool_calls = [
            str(name).strip() for name in tuple(getattr(result, "tool_calls", ()) or ()) if str(name).strip()
        ]
        if observed_tool_calls:
            result_metadata.setdefault("observed_tool_calls", list(observed_tool_calls))
            result_metadata.setdefault("observed_tool_call_count", len(observed_tool_calls))
        return {
            "content": output,
            "response": output,
            "success": bool(getattr(result, "ok", False)) and not bool(error),
            "error": error,
            "role": str(getattr(result, "role", self.role_id) or self.role_id),
            "metadata": {
                **result_metadata,
                "role_runtime_entrypoint": "roles.runtime.execute_role_session",
                "role_runtime_session_id": session_id,
                "context_os_expected": True,
            },
            "execution_stats": {
                **result_usage,
                "role_runtime_entrypoint": "roles.runtime.execute_role_session",
            },
            "batch_receipt": batch_receipt,
            "tool_results": tool_results,
            "tool_calls": [],
            "observed_tool_calls": list(observed_tool_calls),
            "artifacts": list(getattr(result, "artifacts", ()) or ()),
            "raw_response": {
                "ok": bool(getattr(result, "ok", False)),
                "status": str(getattr(result, "status", "") or ""),
                "session_id": str(getattr(result, "session_id", "") or session_id),
                "run_id": str(getattr(result, "run_id", "") or run_id),
                "task_id": str(getattr(result, "task_id", "") or task_id),
                "metadata": result_metadata,
                "usage": result_usage,
                "batch_receipt": batch_receipt,
                "tool_results": tool_results,
                "observed_tool_calls": list(observed_tool_calls),
                "artifacts": list(getattr(result, "artifacts", ()) or ()),
                "error_code": str(getattr(result, "error_code", "") or ""),
                "error_message": str(getattr(result, "error_message", "") or ""),
            },
        }

    @staticmethod
    def _ensure_director_execution_profile(
        *,
        message: str,
        context: dict[str, Any],
        metadata: dict[str, Any],
        workspace: str,
    ) -> dict[str, Any]:
        existing = context.get("director_execution_profile")
        if not isinstance(existing, dict):
            existing = metadata.get("director_execution_profile")
        if isinstance(existing, dict) and existing:
            profile = coerce_task_execution_profile(existing)
        else:
            profile = resolve_task_execution_profile(
                subject=str(metadata.get("title") or metadata.get("subject") or message or ""),
                description=str(
                    metadata.get("description")
                    or metadata.get("objective")
                    or metadata.get("summary")
                    or context.get("description")
                    or ""
                ),
                metadata=metadata,
                target_files=DirectorAdapter._metadata_path_list(metadata, context, "target_files"),
                scope_paths=DirectorAdapter._metadata_path_list(metadata, context, "scope_paths"),
                workspace=str(workspace or ""),
            )
        strategy = resolve_task_execution_strategy(
            profile,
            metadata=metadata,
        )
        apply_task_execution_strategy_overrides(
            context=context,
            metadata=metadata,
            profile=profile,
            strategy=strategy,
        )
        metadata.setdefault(
            "task_execution_profile_source",
            "director.tasking.public.execution_guidance.resolve_task_execution_profile",
        )
        return profile.to_dict()

    @staticmethod
    def _metadata_path_list(
        metadata: dict[str, Any],
        context: dict[str, Any],
        key: str,
    ) -> list[str]:
        for source in (metadata, context):
            value = source.get(key)
            if isinstance(value, str):
                normalized = value.strip()
                return [normalized] if normalized else []
            if isinstance(value, (list, tuple, set)):
                return [str(item).strip() for item in value if str(item or "").strip()]
        return []

    @staticmethod
    def _build_role_runtime_metadata(context: dict[str, Any], *, max_retries: int) -> dict[str, Any]:
        raw_metadata = context.get("metadata")
        metadata: dict[str, Any] = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
        for key in ("task_id", "pm_task_id", "run_id", "session_id"):
            value = context.get(key)
            if value is not None and key not in metadata:
                metadata[key] = value
        for key in _ROLE_RUNTIME_METADATA_CONTEXT_EVIDENCE_KEYS:
            if key in metadata:
                continue
            value = context.get(key)
            if value is None:
                continue
            if isinstance(value, dict):
                if value:
                    metadata[key] = dict(value)
                continue
            if isinstance(value, list):
                if value:
                    metadata[key] = list(value)
                continue
            if isinstance(value, tuple):
                if value:
                    metadata[key] = list(value)
                continue
            if isinstance(value, str):
                normalized = value.strip()
                if normalized:
                    metadata[key] = normalized
                continue
            metadata[key] = value
        metadata.setdefault("source", "roles.adapters.director")
        metadata.setdefault("domain", "code")
        metadata.setdefault("validate_output", False)
        metadata.setdefault("max_retries", max(0, int(max_retries)))
        metadata.setdefault("use_repo_intelligence", True)
        metadata.setdefault("repo_intel_max_files", 20)
        metadata.setdefault("repo_intel_max_symbols", 40)
        metadata["role_runtime_required"] = True
        metadata["cognitive_runtime_required"] = True
        metadata["context_os_expected"] = True
        metadata.setdefault("cognitive_runtime_approval_mode", "auto_accept")
        metadata.setdefault(
            "cognitive_runtime_approval",
            {
                "mode": "auto_accept",
                "source": "roles.adapters.director",
                "scope": "director_execution_preflight",
                "approved_by": "director_adapter",
            },
        )
        return metadata

    @staticmethod
    def _promote_task_contract_to_runtime_context(
        *,
        task: dict[str, Any],
        context: dict[str, Any],
        workspace: str,
    ) -> None:
        """Promote claimed TaskBoard contract fields into RoleRuntime metadata."""

        if not isinstance(task, dict) or not isinstance(context, dict):
            return
        sources = _task_contract_sources(task)
        contract_payload = _promoted_task_contract_payload(sources)
        task_metadata_raw = task.get("metadata")
        task_metadata: dict[str, Any] = dict(task_metadata_raw) if isinstance(task_metadata_raw, dict) else {}
        governance_sources = [task_metadata]
        nested_task_metadata = task_metadata.get("metadata")
        if isinstance(nested_task_metadata, dict):
            governance_sources.append(nested_task_metadata)
        governance_payload = {
            key: value
            for key in _TASK_RUNTIME_GOVERNANCE_SCALAR_KEYS
            if (value := _first_contract_value(governance_sources, key)) is not None
        }
        blueprint_payload = _load_ce_blueprint_contract_payload(workspace, task)
        contract_payload = _merge_ce_blueprint_contract_payload(contract_payload, blueprint_payload)
        if not contract_payload and not governance_payload:
            return

        metadata_raw = context.get("metadata")
        metadata: dict[str, Any] = dict(metadata_raw) if isinstance(metadata_raw, dict) else {}

        # TaskRuntime owns these operational values after Factory admission. They
        # are not PM/CE contract fields, but downstream Director repair and
        # verification must see the same deadline authority. Preserve any value
        # already projected by the orchestration context; otherwise promote the
        # claimed TaskRuntime row's trusted metadata into both context views.
        for key, value in governance_payload.items():
            if not _has_contract_value(context, key):
                context[key] = value
            if not _has_contract_value(metadata, key):
                metadata[key] = value

        for key in _TASK_CONTRACT_LIST_KEYS:
            value = contract_payload.get(key)
            if not isinstance(value, list) or not value:
                continue
            if key in _AUTHORITATIVE_TASK_BOUNDARY_LIST_KEYS:
                task[key] = list(value)
                task_metadata[key] = list(value)
                context[key] = list(value)
                metadata[key] = list(value)
                continue
            merged_task = _merge_contract_lists(task.get(key), value)
            if merged_task:
                task[key] = merged_task
            merged_task_metadata = _merge_contract_lists(task_metadata.get(key), value)
            if merged_task_metadata:
                task_metadata[key] = merged_task_metadata
            merged_context = _merge_contract_lists(context.get(key), value)
            if merged_context:
                context[key] = merged_context
            merged_metadata = _merge_contract_lists(metadata.get(key), value)
            if merged_metadata:
                metadata[key] = merged_metadata

        for key in _TASK_CONTRACT_MAPPING_KEYS:
            value = contract_payload.get(key)
            if not isinstance(value, dict) or not value:
                continue
            _set_structured_task_contract_slot(context, key, value)
            _set_structured_task_contract_slot(metadata, key, value)
            _set_structured_task_contract_slot(task, key, value)
            _set_structured_task_contract_slot(task_metadata, key, value)
            if not _has_contract_value(context, key):
                context[key] = dict(value)
            if not _has_contract_value(metadata, key):
                metadata[key] = dict(value)
            if not _has_contract_value(task, key):
                task[key] = dict(value)
            if not _has_contract_value(task_metadata, key):
                task_metadata[key] = dict(value)

        for key in _TASK_CONTRACT_SCALAR_KEYS:
            value = contract_payload.get(key)
            if value is None or isinstance(value, (list, dict)):
                continue
            if not _has_contract_value(context, key):
                context[key] = value
            if not _has_contract_value(metadata, key):
                metadata[key] = value
            if not _has_contract_value(task, key):
                task[key] = value
            if not _has_contract_value(task_metadata, key):
                task_metadata[key] = value

        if workspace and not _has_contract_value(context, "workspace"):
            context["workspace"] = str(workspace)
        if workspace and not _has_contract_value(metadata, "workspace"):
            metadata["workspace"] = str(workspace)
        if workspace and not _has_contract_value(task_metadata, "workspace"):
            task_metadata["workspace"] = str(workspace)

        _set_structured_task_contract_slot(context, "pm_contract", contract_payload)
        _set_structured_task_contract_slot(metadata, "pm_contract", contract_payload)
        _set_structured_task_contract_slot(task, "pm_contract", contract_payload)
        _set_structured_task_contract_slot(task_metadata, "pm_contract", contract_payload)
        _set_structured_task_contract_slot(context, "task_contract", contract_payload)
        _set_structured_task_contract_slot(metadata, "task_contract", contract_payload)
        _set_structured_task_contract_slot(task_metadata, "task_contract", contract_payload)
        if blueprint_payload:
            for blueprint_key in ("ce_blueprint", "chief_engineer_blueprint", "blueprint", "task_blueprint"):
                _set_structured_task_contract_slot(context, blueprint_key, blueprint_payload)
                _set_structured_task_contract_slot(metadata, blueprint_key, blueprint_payload)
                _set_structured_task_contract_slot(task, blueprint_key, blueprint_payload)
                _set_structured_task_contract_slot(task_metadata, blueprint_key, blueprint_payload)
        if isinstance(contract_payload.get("module_interface_contract"), dict):
            module_contract = contract_payload["module_interface_contract"]
            _set_structured_task_contract_slot(context, "module_interface_contract", module_contract)
            _set_structured_task_contract_slot(metadata, "module_interface_contract", module_contract)
            _set_structured_task_contract_slot(task, "module_interface_contract", module_contract)
            _set_structured_task_contract_slot(task_metadata, "module_interface_contract", module_contract)

        if not isinstance(metadata.get("task"), dict):
            metadata["task"] = dict(contract_payload)
        if not isinstance(context.get("task_contract"), dict):
            context["task_contract"] = dict(contract_payload)
        if not isinstance(task_metadata.get("task_contract"), dict):
            task_metadata["task_contract"] = dict(contract_payload)
        task["metadata"] = task_metadata
        context["metadata"] = metadata

    @staticmethod
    def _ensure_director_verification_commands(*, message: str, context: dict[str, Any]) -> list[str]:
        metadata_raw = context.get("metadata")
        metadata: dict[str, Any] = dict(metadata_raw) if isinstance(metadata_raw, dict) else {}
        commands = _extract_director_verification_commands(
            context.get("verification_commands"),
            context.get("quality_commands"),
            context.get("workspace_quality_commands"),
            context.get("acceptance"),
            context.get("acceptance_criteria"),
            context.get("steps"),
            context.get("execution_checklist"),
            context.get("construction_step"),
            metadata,
        )
        if not commands:
            commands = _extract_director_verification_commands(message)
        if commands:
            context.setdefault("verification_commands", commands)
            metadata.setdefault("verification_commands", commands)
            context["metadata"] = metadata
        return commands

    @staticmethod
    def _resolve_runtime_identity_field(
        context: dict[str, Any],
        metadata: dict[str, Any],
        *,
        keys: tuple[str, ...],
    ) -> str:
        for source in (context, metadata):
            for key in keys:
                token = str(source.get(key) or "").strip()
                if token:
                    return token
        return ""

    @classmethod
    def _resolve_role_runtime_session_id(
        cls,
        context: dict[str, Any],
        *,
        metadata: dict[str, Any],
        task_id: str,
        run_id: str,
        message: str,
    ) -> str:
        explicit = cls._resolve_runtime_identity_field(
            context,
            metadata,
            keys=("session_id", "role_runtime_session_id", "runtime_session_id"),
        )
        if explicit:
            return explicit
        seed = "|".join(
            part
            for part in (
                "director",
                run_id,
                task_id,
                hashlib.sha256(message.encode("utf-8")).hexdigest()[:12],
            )
            if part
        )
        if not seed:
            seed = "director-adhoc"
        safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in seed)
        return safe.strip("-_")[:120] or "director-adhoc"

    @staticmethod
    def _normalize_role_runtime_history(context: dict[str, Any]) -> tuple[tuple[str, str], ...]:
        raw_history = context.get("history")
        if raw_history is None:
            raw_history = context.get("messages")
        if not isinstance(raw_history, (list, tuple)):
            return ()
        normalized: list[tuple[str, str]] = []
        for item in raw_history:
            role = ""
            content = ""
            if isinstance(item, dict):
                role = str(item.get("role") or item.get("speaker") or "").strip()
                content = str(item.get("content") or item.get("message") or "").strip()
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                role = str(item[0] or "").strip()
                content = str(item[1] or "").strip()
            if role and content:
                normalized.append((role, content))
        return tuple(normalized)

    async def _invoke_direct_runtime_provider(
        self,
        message: str,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Fail closed: direct provider bypass is no longer a Director fallback."""
        del message, timeout_seconds
        raise RuntimeError("director_runtime_provider_bypass_removed")

    async def _invoke_role_dialogue_with_timeout(
        self,
        message: str,
        *,
        context: dict[str, Any] | None,
        timeout_seconds: float,
        stage_label: str,
    ) -> dict[str, Any]:
        """Call role LLM with timeout."""
        context_payload, timeout = _prepare_role_dialogue_context(
            context,
            timeout_seconds=timeout_seconds,
            stage_label=stage_label,
        )
        watchdog_timeout = _role_dialogue_watchdog_timeout_seconds(
            context,
            provider_timeout_seconds=timeout,
        )
        timeout_budget = context_payload.get("director_role_call_timeout_budget")
        if isinstance(timeout_budget, dict):
            timeout_budget["transaction_watchdog_timeout_seconds"] = watchdog_timeout
            timeout_budget["provider_timeout_is_not_transaction_timeout"] = True
        try:
            response = await asyncio.wait_for(
                self._invoke_role_dialogue(message, context=context_payload),
                timeout=watchdog_timeout,
            )
            if isinstance(response, dict):
                return response
            return {
                "content": "",
                "success": False,
                "error": f"director_{stage_label}_invalid_llm_payload",
                "raw_response": response,
            }
        except asyncio.TimeoutError:
            return {
                "content": "",
                "success": False,
                "error": f"director_{stage_label}_llm_timeout",
                "raw_response": {"error": "timeout", "timeout": True},
            }
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return {
                "content": "",
                "success": False,
                "error": f"director_{stage_label}_llm_error:{exc}",
                "raw_response": {"error": str(exc), "exception_type": type(exc).__name__},
            }
        finally:
            _project_director_execution_authority_evidence(context_payload, context)

    # -------------------------------------------------------------------------
    # Task Retrieval
    # -------------------------------------------------------------------------

    def _get_task(self, task_id: str) -> dict | None:
        """获取任务信息"""
        return self.task_runtime.get_task(task_id)

    def _select_pending_board_task(self) -> dict[str, Any] | None:
        """当编排任务没有 TaskBoard 映射时，回退到可执行的真实待办任务。"""
        return self.task_runtime.select_next_task(prefer_resumable=True)

    def _materialize_runtime_task(
        self,
        requested_task_id: str,
        input_data: dict[str, Any],
    ) -> dict[str, Any]:
        """将迁移期编排任务物化为 runtime.task_runtime 的 canonical task。"""
        input_metadata_raw = input_data.get("metadata")
        input_metadata: dict[str, Any] = input_metadata_raw if isinstance(input_metadata_raw, dict) else {}
        subject = str(
            input_data.get("subject")
            or input_metadata.get("title")
            or input_metadata.get("subject")
            or input_data.get("input")
            or ""
        ).strip()
        if not subject:
            subject = f"Director task {requested_task_id}"
        description = str(
            input_data.get("description")
            or input_metadata.get("description")
            or input_metadata.get("goal")
            or input_data.get("input")
            or ""
        ).strip()
        metadata = self._build_materialized_metadata(requested_task_id, input_data)
        return self.task_runtime.ensure_task_row(
            external_task_id=requested_task_id,
            subject=subject,
            description=description,
            metadata=metadata,
        )

    def _build_ephemeral_task(self, requested_task_id: str, input_data: dict[str, Any]) -> dict[str, Any]:
        """Build a safe ephemeral task enriched with pending board contract hints."""
        task = self._materialize_runtime_task(requested_task_id, input_data)
        input_metadata_raw = input_data.get("metadata")
        input_metadata: dict[str, Any] = input_metadata_raw if isinstance(input_metadata_raw, dict) else {}

        pending_task_raw = self._select_pending_board_task()
        pending_task: dict[str, Any] = pending_task_raw if isinstance(pending_task_raw, dict) else {}
        pending_subject = str(
            pending_task.get("subject")
            or pending_task.get("title")
            or str(input_metadata.get("title") or "").strip()
            or str(input_metadata.get("subject") or "").strip()
            or ""
        ).strip()
        pending_description = str(
            pending_task.get("description") or pending_task.get("goal") or input_metadata.get("description") or ""
        ).strip()

        snapshot = self._state_tracker.build_taskboard_observation_snapshot(self.task_runtime)
        board_brief = self._taskboard_snapshot_brief(snapshot)

        current_desc = str(task.get("description") or "").strip()
        current_desc = board_brief if not current_desc else f"{current_desc}\n{board_brief}"

        task_contract_lines: list[str] = []
        if pending_subject:
            task_contract_lines.append(f"Pending TaskBoard contract: {pending_subject}")
        if pending_description:
            task_contract_lines.append(f"Pending TaskBoard description: {pending_description}")
        if task_contract_lines:
            current_desc = f"{current_desc}\n" + "\n".join(task_contract_lines)
        else:
            current_desc = f"{current_desc}\nNo pending TaskBoard contract found; use TaskBoard pending queue first."

        task["description"] = current_desc
        task["board_snapshot_brief"] = board_brief
        task["pending_task_contract"] = {
            "subject": pending_subject,
            "description": pending_description,
        }
        return task

    def _build_materialized_metadata(self, requested_task_id: str, input_data: dict[str, Any]) -> dict[str, Any]:
        """Build metadata dict for materialized runtime task."""
        if input_data is None:
            input_data = {}
        input_metadata_raw = input_data.get("metadata")
        input_metadata: dict[str, Any] = input_metadata_raw if isinstance(input_metadata_raw, dict) else {}

        def _list_or_empty(value: Any) -> list[Any]:
            return list(value) if isinstance(value, list) else []

        scope_paths = (
            input_data.get("scope_paths")
            if isinstance(input_data.get("scope_paths"), list)
            else input_metadata.get("scope_paths")
            if isinstance(input_metadata.get("scope_paths"), list)
            else []
        )
        target_files = (
            input_data.get("target_files")
            if isinstance(input_data.get("target_files"), list)
            else input_metadata.get("target_files")
            if isinstance(input_metadata.get("target_files"), list)
            else []
        )
        execution_checklist = (
            input_data.get("execution_checklist")
            if isinstance(input_data.get("execution_checklist"), list)
            else input_metadata.get("execution_checklist")
            if isinstance(input_metadata.get("execution_checklist"), list)
            else []
        )
        acceptance_criteria = (
            input_data.get("acceptance_criteria")
            if isinstance(input_data.get("acceptance_criteria"), list)
            else input_metadata.get("acceptance_criteria")
            if isinstance(input_metadata.get("acceptance_criteria"), list)
            else input_data.get("acceptance")
            if isinstance(input_data.get("acceptance"), list)
            else input_metadata.get("acceptance")
            if isinstance(input_metadata.get("acceptance"), list)
            else []
        )
        metadata: dict[str, Any] = {
            "goal": str(input_data.get("goal") or input_metadata.get("goal") or "").strip(),
            "scope": str(input_data.get("scope") or input_metadata.get("scope") or "").strip(),
            "steps": (
                input_data.get("steps")
                if isinstance(input_data.get("steps"), list)
                else input_metadata.get("steps")
                if isinstance(input_metadata.get("steps"), list)
                else execution_checklist
            ),
            "phase": str(input_data.get("phase") or input_metadata.get("phase") or "implementation").strip(),
            "pm_task_id": str(
                input_data.get("pm_task_id")
                or input_metadata.get("pm_task_id")
                or input_metadata.get("task_id")
                or input_metadata.get("id")
                or requested_task_id
            ).strip(),
            "source": "director_adapter.materialized_orchestration_task",
            "scope_paths": _list_or_empty(scope_paths),
            "target_files": _list_or_empty(target_files),
            "execution_checklist": _list_or_empty(execution_checklist),
            "acceptance_criteria": _list_or_empty(acceptance_criteria),
            "acceptance": _list_or_empty(acceptance_criteria),
        }
        input_metadata_no_proj = (
            {k: v for k, v in input_metadata.items() if k != "projection"} if input_metadata else {}
        )
        metadata.update(input_metadata_no_proj)
        for key in ("scope_paths", "target_files", "execution_checklist", "acceptance_criteria", "acceptance"):
            metadata[key] = _list_or_empty(metadata.get(key))
        if not isinstance(metadata.get("steps"), list):
            metadata["steps"] = list(metadata["execution_checklist"])
        return metadata

    # -------------------------------------------------------------------------
    # Execution Backend Resolution
    # -------------------------------------------------------------------------

    def _resolve_execution_backend_request(
        self,
        *,
        task_id: str,
        task: dict[str, Any],
        input_data: dict[str, Any],
        context: dict[str, Any],
    ) -> DirectorExecutionBackendRequest:
        """解析执行后端请求"""
        request = resolve_director_execution_backend(
            input_data=input_data,
            task=task,
            context=context,
            default_project_slug=default_projection_slug(task_id, task, input_data),
        )
        if not request.requirement and request.execution_backend != "projection_refresh_mapping":
            request = replace(
                request,
                requirement=compose_projection_requirement(task, input_data),
            )
        return request

    def _persist_execution_backend_metadata(
        self,
        task_id: str,
        request: DirectorExecutionBackendRequest,
    ) -> None:
        """持久化执行后端元数据"""
        if not task_id:
            return
        self._update_board_task(
            task_id,
            metadata=request.to_task_metadata(),
        )

    # -------------------------------------------------------------------------
    # Director Message Building
    # -------------------------------------------------------------------------

    def _resolve_child_task_for_dependency_artifact(
        self,
        context: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Resolve the child task mapping used to rebuild dependency artifact evidence."""

        for key in ("task", "task_payload", "pm_task", "director_task"):
            raw = context.get(key)
            if isinstance(raw, dict) and raw:
                return dict(raw)
        metadata = _copy_mapping_payload(context.get("metadata")) or {}
        for key in ("task", "task_payload"):
            raw = metadata.get(key)
            if isinstance(raw, dict) and raw:
                return dict(raw)
        task_id = str(
            context.get("task_id")
            or context.get("target_task_id")
            or metadata.get("task_id")
            or metadata.get("pm_task_id")
            or metadata.get("external_task_id")
            or metadata.get("source_task_id")
            or ""
        ).strip()
        if not task_id:
            return None
        row = self._get_task(task_id)
        if isinstance(row, dict) and row:
            return dict(row)
        # Numeric / TASK-N aliases
        for candidate in (task_id, task_id.removeprefix("TASK-").removeprefix("task-")):
            if not candidate:
                continue
            row = self._get_task(candidate)
            if isinstance(row, dict) and row:
                return dict(row)
        return None

    def _rebind_director_dependency_artifact_for_dialogue(
        self,
        context: dict[str, Any],
    ) -> TrustedDirectorDependencyArtifactSnapshotV2 | None:
        """Rebuild trusted sibling-export evidence before every Director dialogue turn.

        Follow-up stages (quality repair, no-write retry) may retain dependency
        requirements from execution strategy while losing the trusted snapshot token.
        Rebinding restores both the token and projected ``actual_sibling_exports``.
        """

        existing = context.get(DIRECTOR_DEPENDENCY_ARTIFACT_SNAPSHOT_CONTEXT_KEY)
        if type(existing) is TrustedDirectorDependencyArtifactSnapshotV2:
            return existing
        task = self._resolve_child_task_for_dependency_artifact(context)
        if task is None:
            return None
        return self._prepare_director_dependency_artifact_snapshot(task=task, context=context)

    @staticmethod
    def _dependency_artifact_factory_run_id(
        *,
        task: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> str:
        """Resolve the exact Factory epoch that owns dependency receipts."""

        task_metadata = _copy_mapping_payload(task.get("metadata")) or {}
        context_metadata = _copy_mapping_payload(context.get("metadata")) or {}
        for source in (task, task_metadata, context, context_metadata):
            for key in ("factory_run_id", "workflow_run_id", "run_id"):
                value = str(source.get(key) or "").strip()
                if value:
                    return value
        return ""

    def _resolve_dependency_parent_task(
        self,
        parent_task_id: str,
        *,
        child_task: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Resolve live parent state or reconstruct receipt authority after drain.

        Factory terminal drain intentionally removes TaskRuntime rows. Stage-local
        QA repair must still consume already-settled sibling interfaces without
        recreating a fake completed task. The fallback therefore uses only the
        strict CE handoff projection plus execution-broker project receipts; the
        dependency snapshot builder still verifies exact run/contract/task/path
        identity and current guarded file hashes before exposing any source body.
        """

        live_parent = self._get_task(parent_task_id)
        if isinstance(live_parent, dict):
            live_metadata = _copy_mapping_payload(live_parent.get("metadata")) or {}
            live_adapter_result = _copy_mapping_payload(live_metadata.get("adapter_result")) or {}
            has_live_receipt_evidence = bool(live_adapter_result.get("write_tool_evidence")) and any(
                live_adapter_result.get(key) not in (None, "", [], (), {})
                for key in ("tool_results", "batch_receipt", "primary_llm")
            )
            has_live_completion_authority = isinstance(
                live_metadata.get("task_completion_projection"),
                Mapping,
            ) or isinstance(live_parent.get("task_completion_projection"), Mapping)
            if has_live_receipt_evidence or has_live_completion_authority:
                return dict(live_parent)
            # QA-local reset may recreate a skeletal pending parent row after the
            # original TaskRuntime has drained.  That row has no delivery authority
            # and must not shadow the same-run strict CE completion projection.

        factory_run_id = self._dependency_artifact_factory_run_id(task=child_task, context=context)
        if not factory_run_id:
            return None
        try:
            status = get_blueprint_status(
                GetBlueprintStatusQueryV1(
                    task_id=parent_task_id,
                    workspace=str(self.workspace),
                    run_id=factory_run_id,
                )
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            return None
        blueprint_id = str(status.blueprint_id or "").strip()
        if not status.ok or not blueprint_id:
            return None
        handoff = validate_director_handoff_from_payload(
            str(self.workspace),
            {"task_id": parent_task_id, "blueprint_id": blueprint_id},
            require_strict=True,
        )
        projection = _copy_mapping_payload(handoff.get("task_completion_projection")) or {}
        if handoff.get("allowed") is not True or str(projection.get("run_id") or "").strip() != factory_run_id:
            return None
        return {
            "id": parent_task_id,
            "metadata": {
                "external_task_id": parent_task_id,
                "factory_run_id": factory_run_id,
                "task_completion_projection": projection,
                "dependency_artifact_authority_source": (
                    "chief_engineer.strict_handoff+runtime.execution_broker.project_artifact_receipt"
                ),
            },
        }

    def _prepare_director_dependency_artifact_snapshot(
        self,
        *,
        task: dict[str, Any],
        context: dict[str, Any],
    ) -> TrustedDirectorDependencyArtifactSnapshotV2 | None:
        """Project one trusted parent-artifact snapshot into a mutable turn context."""

        task_metadata = _copy_mapping_payload(task.get("metadata")) or {}
        context_metadata = _copy_mapping_payload(context.get("metadata")) or {}
        merged_task = dict(task)
        merged_task["metadata"] = {**task_metadata, **context_metadata}
        for key in (
            "resolved_depends_on_task_ids",
            "depends_on_task_ids",
            "depends_on_external",
            "dependency_task_ids",
            "depends_on",
        ):
            if key not in merged_task["metadata"] and context.get(key) not in (None, "", [], (), {}):
                merged_task["metadata"][key] = context[key]

        context.pop(DIRECTOR_DEPENDENCY_ARTIFACT_SNAPSHOT_CONTEXT_KEY, None)
        project_director_dependency_artifact_snapshot(context, None)
        if not _director_actual_interface_injection_enabled():
            return None
        try:
            snapshot = build_director_dependency_artifact_snapshot(
                workspace=str(self.workspace),
                child_task=merged_task,
                get_task=lambda parent_task_id: self._resolve_dependency_parent_task(
                    parent_task_id,
                    child_task=merged_task,
                    context=context,
                ),
                get_project_artifact_receipt=self._ensure_dependency_project_artifact_receipt,
            )
        except DirectorDependencyArtifactEvidenceError as exc:
            logger.warning(
                "Director dependency artifact snapshot rejected: run_id=%s task_id=%s code=%s details=%s",
                self._dependency_artifact_factory_run_id(task=task, context=context),
                task.get("id") or task.get("task_id") or context.get("task_id") or "",
                exc.code,
                dict(exc.details),
            )
            metadata = _copy_mapping_payload(context.get("metadata")) or {}
            metadata["actual_sibling_exports_projection_error"] = {
                "schema_version": "polaris.actual_sibling_exports.projection_error.v1",
                "code": exc.code,
                "details": dict(exc.details),
            }
            context["metadata"] = metadata
            return None

        metadata = _copy_mapping_payload(context.get("metadata")) or {}
        metadata.pop("actual_sibling_exports_projection_error", None)
        context["metadata"] = metadata
        if type(snapshot) is TrustedDirectorDependencyArtifactSnapshotV2:
            context[DIRECTOR_DEPENDENCY_ARTIFACT_SNAPSHOT_CONTEXT_KEY] = snapshot
        project_director_dependency_artifact_snapshot(context, snapshot)
        return snapshot

    @staticmethod
    def _ensure_dependency_project_artifact_receipt(
        query_payload: Mapping[str, str],
    ) -> Mapping[str, Any] | None:
        """Read or exact-authority rehydrate one parent artifact receipt.

        A task-local tool batch may contain both durable successful writes and a
        later rejected mutation.  Once TaskBoundary proves the owned files are
        complete, the failed batch is historical outcome evidence, but an older
        runtime may still lack its project-level receipt.  Re-recording here is
        safe: execution_broker independently validates the immutable CE
        obligation, task-local JobToken capability, current path, and bytes.
        """

        existing = query_project_artifact_receipt_payload(query_payload)
        if isinstance(existing, Mapping):
            return existing
        try:
            receipt = record_project_artifact(RecordProjectArtifactCommandV1(**dict(query_payload)))
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning(
                "Director dependency project receipt rehydration rejected: "
                "run_id=%s owner_task_id=%s obligation_id=%s path=%s error=%s",
                query_payload.get("run_id", ""),
                query_payload.get("owner_task_id", ""),
                query_payload.get("obligation_id", ""),
                query_payload.get("path", ""),
                exc,
            )
            return None
        return {
            "workspace": receipt.workspace,
            "project_id": receipt.project_id,
            "run_id": receipt.run_id,
            "completion_contract_hash": receipt.completion_contract_hash,
            "obligation_id": receipt.obligation_id,
            "owner_task_id": receipt.owner_task_id,
            "path": receipt.path,
            "artifact_hash": receipt.artifact_hash,
            "authority_revision": receipt.authority_revision,
            "receipt_hash": receipt.receipt_hash,
            "receipt_ref": receipt.receipt_ref,
        }

    def _build_director_message(
        self,
        task: dict[str, Any],
        *,
        text_patch_mode: bool = False,
        context: dict[str, Any] | None = None,
    ) -> str:
        """构建 Director 角色消息"""
        subject = task.get("subject", "")
        description = DirectorStateTracker.sanitize_task_description(str(task.get("description") or ""))
        raw_metadata = task.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        runtime_context = context if isinstance(context, dict) else {}
        dependency_artifact_snapshot = self._prepare_director_dependency_artifact_snapshot(
            task=task,
            context=runtime_context,
        )
        runtime_metadata_raw = runtime_context.get("metadata")
        runtime_metadata: dict[str, Any] = runtime_metadata_raw if isinstance(runtime_metadata_raw, dict) else {}
        goal = str(
            metadata.get("goal")
            or task.get("goal")
            or runtime_context.get("goal")
            or runtime_metadata.get("goal")
            or ""
        ).strip()

        def _first_listish(*values: Any, limit: int = 24) -> list[str]:
            for value in values:
                items = _string_list_payload(value, limit=limit)
                if items:
                    return items
            return []

        scope = _first_listish(
            metadata.get("scope"),
            task.get("scope"),
            runtime_context.get("scope"),
            runtime_metadata.get("scope"),
        )
        steps = _first_listish(
            metadata.get("steps"),
            task.get("steps"),
            runtime_context.get("steps"),
            runtime_metadata.get("steps"),
            metadata.get("execution_checklist"),
            task.get("execution_checklist"),
            runtime_context.get("execution_checklist"),
            runtime_metadata.get("execution_checklist"),
        )
        acceptance = _first_listish(
            metadata.get("acceptance"),
            task.get("acceptance"),
            runtime_context.get("acceptance"),
            runtime_metadata.get("acceptance"),
            metadata.get("acceptance_criteria"),
            task.get("acceptance_criteria"),
            runtime_context.get("acceptance_criteria"),
            runtime_metadata.get("acceptance_criteria"),
        )
        raw_adapter_result = metadata.get("adapter_result")
        adapter_result: dict[str, Any] = raw_adapter_result if isinstance(raw_adapter_result, dict) else {}
        qa_rework_reason = str(metadata.get("qa_rework_reason") or adapter_result.get("qa_rework_reason") or "").strip()
        qa_rework_evidence = metadata.get("qa_rework_evidence") or adapter_result.get("qa_rework_evidence")

        def _stringify_list(value: Any) -> list[str]:
            if isinstance(value, list):
                return [str(item or "").strip() for item in value if str(item or "").strip()]
            token = str(value or "").strip()
            if not token:
                return []
            return [part.strip() for part in token.split(",") if part.strip()] or [token]

        scope_items = _stringify_list(scope)
        target_file_items = _first_listish(
            metadata.get("target_files")
            or task.get("target_files")
            or runtime_context.get("target_files")
            or runtime_metadata.get("target_files"),
            limit=16,
        )
        scope_path_items = _first_listish(
            metadata.get("scope_paths")
            or task.get("scope_paths")
            or runtime_context.get("scope_paths")
            or runtime_metadata.get("scope_paths"),
            limit=16,
        )
        for item in [*scope_path_items, *target_file_items]:
            if item not in scope_items:
                scope_items.append(item)
        step_items = _stringify_list(steps)
        acceptance_items = _stringify_list(acceptance)
        qa_rework_items = _stringify_list(qa_rework_evidence)
        blueprint_id = str(
            metadata.get("blueprint_id")
            or task.get("blueprint_id")
            or runtime_context.get("blueprint_id")
            or runtime_metadata.get("blueprint_id")
            or ""
        ).strip()
        construction_step_raw = runtime_context.get("construction_step") or metadata.get("construction_step")
        construction_step: dict[str, Any] = construction_step_raw if isinstance(construction_step_raw, dict) else {}
        construction_target = str(construction_step.get("target_file") or "").strip()
        construction_signatures = _stringify_list(construction_step.get("signatures"))[:8]
        construction_verify = str(construction_step.get("verify") or "").strip()
        verification_commands = _extract_director_verification_commands(
            metadata.get("verification_commands"),
            task.get("verification_commands"),
            runtime_context.get("verification_commands"),
            runtime_metadata.get("verification_commands"),
            acceptance_items,
            step_items,
            construction_verify,
        )
        language_identity = ""
        language_section = ""
        try:
            guidance_metadata = {**metadata, **runtime_metadata}
            if construction_step:
                guidance_metadata["construction_step"] = construction_step
            language_targets = target_file_items or ([construction_target] if construction_target else [])
            language_identity, language_section = build_task_language_section(
                language_targets,
                str(self.workspace),
                metadata=guidance_metadata,
                subject=str(subject or ""),
                description=str(description or ""),
                scope_paths=scope_path_items,
            )
        except (RuntimeError, ValueError, ImportError) as exc:
            logger.debug("Failed to build Director language guidance: %s", exc)
        if language_identity:
            runtime_context["director_language_identity"] = language_identity
            runtime_metadata.setdefault("director_language_identity", language_identity)
            runtime_context["metadata"] = runtime_metadata
        factory_project = str(
            metadata.get("factory_bench_project_id")
            or runtime_metadata.get("factory_bench_project_id")
            or runtime_context.get("factory_bench_project_id")
            or ""
        ).strip()
        factory_title = str(
            metadata.get("factory_bench_title")
            or runtime_metadata.get("factory_bench_title")
            or runtime_context.get("factory_bench_title")
            or ""
        ).strip()
        blueprint_handoff_lines = _build_director_blueprint_handoff_lines(self.workspace, blueprint_id)

        lines = [
            "PM Task Contract / 任务合同:",
            f"任务: {subject}",
            "",
            f"描述: {description}" if description else "",
            "",
            f"目标: {goal}" if goal else "",
            f"范围: {', '.join(scope_items)}" if scope_items else "",
            f"目标文件: {', '.join(target_file_items)}" if target_file_items else "",
            (
                "目标文件覆盖硬门禁: 本任务列出的目标文件必须全部由本轮工具写入或编辑；"
                "多文件创建任务必须为每个目标文件分别发出 write/edit 工具调用，"
                "不得只写第一个 sibling 文件后结束。"
                if len(target_file_items) > 1
                else ""
            ),
            "",
            "执行步骤:",
            *[f"- {item}" for item in step_items],
            "",
            "Acceptance criteria / 验收标准:",
            *[f"- {item}" for item in acceptance_items],
            "",
            "Verification commands / 验证命令:" if verification_commands else "",
            *[f"- {item}" for item in verification_commands],
            "",
            "Director language/task identity / 语言专项身份:" if language_identity or language_section else "",
            language_identity,
            language_section.strip(),
            "",
            "Chief Engineer Blueprint / CE 蓝图交接:",
            *blueprint_handoff_lines,
            f"- construction target: {construction_target}" if construction_target else "",
            ("- construction signatures: " + "; ".join(construction_signatures) if construction_signatures else ""),
            f"- construction verify: {construction_verify}" if construction_verify else "",
            (
                f"- factory bench project: {factory_project}" + (f" - {factory_title}" if factory_title else "")
                if factory_project or factory_title
                else ""
            ),
            "",
            *(
                cast(TrustedDirectorDependencyArtifactSnapshotV2, dependency_artifact_snapshot).message_lines()
                if type(dependency_artifact_snapshot) is TrustedDirectorDependencyArtifactSnapshotV2
                else ()
            ),
            "",
            "QA 返工要求:" if qa_rework_reason else "",
            f"- 原因: {qa_rework_reason}" if qa_rework_reason else "",
            *[f"- 证据: {item}" for item in qa_rework_items],
            "必须修复 QA 证据中的真实文件并重新运行相关验证，不得仅确认既有 scope 存在。" if qa_rework_reason else "",
            "",
            "禁止输出 TODO/FIXME/NotImplemented 等占位实现。",
            "不得把示例路径当成目标文件；必须使用任务范围中的真实相对路径。",
            "生成 Python 测试时必须使用标准库 unittest，且 `python -m unittest discover -s tests -p 'test_*.py' -v` 必须至少发现并运行 1 个测试。",
            "测试只能覆盖目标、执行步骤、验收标准明确要求的能力；不得新增合同外功能断言或引入未声明第三方测试依赖。",
            "",
        ]
        if text_patch_mode:
            lines.extend(
                [
                    "当前运行时要求纯文本补丁。只输出可解析的文件块，不要解释。",
                    "创建或替换文件时使用如下格式，每个文件一个块:",
                    "relative/path.ext",
                    "```language",
                    "完整文件内容",
                    "```",
                    "修改已有文件时也可以使用 PATCH_FILE，但 PATCH_FILE 后必须是真实相对路径。",
                    "不要把 unified diff 或 ```diff 代码块当成文件内容输出；Markdown 文件块必须包含完整最终文件内容。",
                    "不要输出 `PATCH_FILE path` 后再跟 ```diff 代码块；若使用 PATCH_FILE 协议，必须使用运行时可解析的正式协议格式。",
                    "不要输出任何占位路径。",
                ]
            )
        else:
            lines.extend(
                [
                    "请通过运行时正式写入工具完成修改；若只能返回文本，输出可解析的文件块。",
                    "文本文件块格式:",
                    "relative/path.ext",
                    "```language",
                    "完整文件内容",
                    "```",
                ]
            )

        return "\n".join(line for line in lines if line != "")

    # -------------------------------------------------------------------------
    # Progress Update Methods (matching base class signatures)
    # -------------------------------------------------------------------------

    def _update_task_progress(
        self,
        task_id: str,
        phase: str,
        current_file: str | None = None,
        event_code: str | None = None,
        event_status: str | None = None,
        event_reason: str | None = None,
        event_detail: str | None = None,
        event_refs: dict[str, Any] | None = None,
    ) -> None:
        """Record Director progress as metadata-only task evidence.

        WS2 invariant:
            TaskRow status is owned by ``TaskRuntimeService`` execution
            transitions.  Director progress statuses such as ``running`` or
            ``failed`` are trace semantics, not row-state authority.  Delegating
            to ``BaseRoleAdapter._update_task_progress`` preserves these values
            under ``adapter_event_status`` without writing the TaskRow status
            column.
        """
        super()._update_task_progress(
            task_id,
            phase,
            current_file=current_file,
            event_code=event_code,
            event_status=event_status,
            event_reason=event_reason,
            event_detail=event_detail,
            event_refs=event_refs,
        )

    def _update_board_task(
        self,
        task_id: str,
        status: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """更新 TaskBoard 任务"""
        if not metadata and not status:
            return False
        return super()._update_board_task(task_id, status=status, metadata=metadata)

    async def _emit_task_trace_event(
        self,
        *,
        task_id: str,
        phase: str,
        step_kind: str,
        step_title: str,
        step_detail: str,
        status: str = "running",
        run_id: str = "",
        current_file: str | None = None,
        code: str | None = None,
        reason: str | None = None,
        refs: dict[str, Any] | None = None,
        attempt: int = 0,
        visibility: str = "debug",
    ) -> None:
        """发射任务追踪事件"""
        logger.debug(
            "Task trace: task_id=%s phase=%s step=%s",
            task_id,
            phase,
            step_kind,
        )

    def _append_runtime_stage_signals(
        self,
        *,
        stage: str,
        task_id: str,
        signals: list[dict[str, Any]],
        context: dict[str, Any] | None = None,
        source: str | None = None,
    ) -> str | None:
        """追加运行时阶段信号"""
        return None

    def _taskboard_snapshot_brief(self, snapshot: dict[str, Any]) -> str:
        """TaskBoard 快照简要描述"""
        return taskboard_snapshot_brief(snapshot)

    # -------------------------------------------------------------------------
    # State Tracker Proxy Methods (stable support delegates)
    # -------------------------------------------------------------------------

    def _collect_workspace_code_files(self) -> dict[str, str]:
        """Proxy to state tracker collect_workspace_code_files."""
        return self._state_tracker.collect_workspace_code_files()

    def _build_taskboard_observation_snapshot(self, sample_limit: int = 5) -> dict[str, Any]:
        """Proxy to state tracker build_taskboard_observation_snapshot."""
        return self._state_tracker.build_taskboard_observation_snapshot(self.task_runtime, sample_limit=sample_limit)
