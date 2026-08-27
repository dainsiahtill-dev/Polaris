"""Private mixin _Mixin02 for OrchestrationStageExecutor."""

# mypy: disable-error-code="attr-defined"

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

from jsonschema import Draft202012Validator

if TYPE_CHECKING:
    pass

from polaris.cells.chief_engineer.blueprint.public import (
    BuildChiefEngineerBlueprintPortfolioCommandV1,
    ChiefEngineerBlueprintPortfolioV1,
    ChiefEngineerPortfolioTaskV1,
    ChiefEngineerSemanticRepairCandidateV1,
    ChiefEngineerSemanticRepairDiagnosisV1,
    ChiefEngineerSemanticRepairOperationV1,
    GenerateTaskBlueprintCommandV1,
    bind_chief_engineer_semantic_repair_provider_patch,
    build_chief_engineer_blueprint_portfolio,
    build_chief_engineer_semantic_repair_patch_schema,
    chief_engineer_semantic_repair_task_set_hash,
    compose_chief_engineer_semantic_repair,
    generate_task_blueprint,
    normalize_chief_engineer_portfolio_tool_arguments,
    persist_chief_engineer_review_document,
    persist_chief_engineer_semantic_repair_candidate,
    project_chief_engineer_delivery_depth_feasibility_from_pm_tasks,
    project_chief_engineer_semantic_repair_provider_context,
    project_chief_engineer_task_blueprint,
    validate_director_handoff_from_payload,
)
from polaris.cells.chief_engineer.blueprint.public.contracts import (
    _issue_chief_engineer_portfolio_authority_carrier,
)
from polaris.cells.control_plane.run_ledger.public import FailureClassV1
from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import (
    FactoryRoleEvidenceAuthorityBindingV1,
)
from polaris.cells.roles.kernel.public.service import QualityChecker
from polaris.cells.roles.kernel.public.structured_output_contracts import (
    RoleStructuredOutputContractV1,
)
from polaris.cells.roles.runtime.public.contracts import (
    ExecuteRoleTaskCommandV1,
    RoleExecutionResultV1,
)
from polaris.cells.runtime.task_runtime.public import (
    TaskRuntimeExecutionAttemptIdentityV1,
    TaskRuntimeExecutionAttemptSettlementOutcomeV1,
)
from polaris.kernelone.constants import (
    MAX_LLM_PROVIDER_TIMEOUT_SECONDS,  # noqa: F401 — re-exported for characterization-test surface
)
from polaris.kernelone.llm.budget_policy import (
    chief_engineer_portfolio_output_tokens,
)

from .. import (
    factory_director_dispatch_impl as director_dispatch_impl,
    factory_materialization_impl as materialization_impl,
    factory_stage_helpers as helpers,
    factory_workspace_quality_impl as workspace_quality_impl,
)
from ..factory_deadline_calculations import (  # noqa: F401 — re-exported for characterization-test surface
    _CHIEF_ENGINEER_EXECUTION_ATTEMPT_SETTLEMENT_GRACE_SECONDS,
    _CHIEF_ENGINEER_LLM_TIMEOUT_ENV_KEYS,
    _DEFAULT_CHIEF_ENGINEER_LLM_TIMEOUT_SECONDS,
    ChiefEngineerExecutionAttemptLeaseBudget as _ChiefEngineerExecutionAttemptLeaseBudget,
)
from ..factory_deadline_policy import (
    FactoryDeadlineAdmissionV1,
    FactoryDeadlineDispositionV1,
    build_task_dependency_schedule,
)
from ..factory_role_evidence_authority import (
    FactoryRoleEvidenceAuthorityPort,
)
from ..factory_run_models import (
    _WORKSPACE_VALIDATION_OUTPUT_MAX_CHARS,
    FactoryRun,
    StageResult,
)
from ..factory_stage_artifact_bindings import (
    PM_STAGE_ARTIFACT_BINDING_CONTEXT_KEY,
    RevalidatedPMStageArtifactBindingV1,
)
from ..run_ledger import load_run_ledger_projection
from ._helpers import (
    _CHIEF_ENGINEER_PORTFOLIO_REASONING_BUDGET_TOKENS,
    _CHIEF_ENGINEER_SCHEMA_REPAIR_ERROR_MAX_CHARS,
    _CHIEF_ENGINEER_SCHEMA_REPAIR_MAX_TOKENS,
    _CHIEF_ENGINEER_SCHEMA_REPAIR_REASONING_BUDGET_TOKENS,
    _ChiefEngineerExecutionAttemptLeaseKeeper,
    _ChiefEngineerExecutionAttemptLeaseScope,
    _ChiefEngineerPortfolioAuthorityError,
    _ChiefEngineerPortfolioAuthorityV1,
    _is_workspace_quality_repair_path,
    resolve_workspace_quality_existing_file,
    workspace_quality_rust_plan_probe_companion_paths,
)
from ._pkg_proxy import pkg

logger = logging.getLogger("polaris.cells.factory.pipeline.internal.factory_stage_executor")

# Every non-streaming CE repair may retry its identical physical request once
# for transient provider transport failures (for example HTTP 529 overload).
# This is not another schema/semantic repair round: PM authority, request body,
# TaskRuntime claim, and structured-output contract remain frozen.
_CHIEF_ENGINEER_REPAIR_TRANSPORT_MAX_RETRIES = 1

# Semantic patch repairs expose the same budget under the legacy audit key.
_CHIEF_ENGINEER_SEMANTIC_PATCH_TRANSPORT_MAX_RETRIES = 1

# Structured compiler error codes used by the workspace-quality repair-effect
# classifier: rustc ``error[E0432]`` and TypeScript ``TS2551``.  One scan
# yields a bare, casefoldable code token per diagnostic.
_COMPILER_ERROR_CODE_RES = re.compile(
    r"error\s*\[\s*(?P<rust>E\d{3,5})\s*\]|(?<![\w])TS(?P<ts>\d{4})(?![\w])",
    re.IGNORECASE,
)
_CPP_MISSING_MEMBER_RE = re.compile(
    r"has no member named\s+[`'‘\"](?P<name>[A-Za-z_]\w*)[`'’\"]",
    re.IGNORECASE,
)
_CPP_DIAGNOSTIC_KIND_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("cpp_undeclared", ("has not been declared", "was not declared in this scope", "does not name a type")),
    ("cpp_redefinition", ("multiple definition", "different underlying type", "redefinition of")),
    ("cpp_missing_member", ("has no member named", "is not a member of")),
    ("cpp_ambiguous", ("call of overloaded", "ambiguous")),
    ("cpp_no_matching", ("no matching function",)),
    ("cpp_invalid_this", ("invalid use of 'this'", "invalid use of this", "invalid use of ‘this’")),
)


def _compiler_error_code_from_match(match: re.Match[str]) -> str:
    rust_code = match.group("rust")
    if rust_code:
        return rust_code.lower()
    ts_code = match.group("ts")
    return f"ts{ts_code.lower()}" if ts_code else ""


class _Mixin02:
    """Method group extracted from OrchestrationStageExecutor (lossless)."""

    @staticmethod
    def _chief_engineer_semantic_repair_diagnosis(
        *,
        candidate: ChiefEngineerSemanticRepairCandidateV1,
        output_errors: Sequence[str],
    ) -> ChiefEngineerSemanticRepairDiagnosisV1:
        """Map semantic failures to stable codes and typed operation authority."""

        codes: list[str] = []
        operations: list[ChiefEngineerSemanticRepairOperationV1] = []
        for raw_error in output_errors:
            error = str(raw_error).strip().casefold()
            if "delivery depth infeasible" in error:
                metric = "test_files" if "test_files=" in error else "prod_files"
                codes.append(f"chief_engineer.delivery_depth.{metric}_below_minimum")
                operations.append("artifact_upsert")
                if metric == "test_files" and len(candidate.task_ids) > 1:
                    # Adding a test artifact can create a new PM-authorized test
                    # owner.  The same atomic patch must be able to bind that
                    # owner to production behavior; otherwise depth repair
                    # merely unmasks cross-task coverage after the provider
                    # budget is exhausted (exact L3-23 r06). A one-task
                    # portfolio has no legal cross-task owner/consumer pair;
                    # requiring one there creates an impossible repair contract.
                    codes.append("chief_engineer.shared_behavior_contract.cross_task_production_test_coverage_missing")
                    operations.extend(("behavior_invariant_upsert", "task_behavior_ref_replace"))
            elif "cross-task production-and-test obligation coverage" in error:
                codes.append("chief_engineer.shared_behavior_contract.cross_task_production_test_coverage_missing")
                operations.extend(("behavior_invariant_upsert", "task_behavior_ref_replace"))
            elif "shared_behavior_contract" in error or "behavior invariant" in error:
                codes.append("chief_engineer.shared_behavior_contract.invalid")
                operations.extend(("behavior_invariant_upsert", "task_behavior_ref_replace"))
            elif "entrypoint" in error:
                codes.append("chief_engineer.entrypoint_contract.invalid")
                operations.append("entrypoint_upsert")
            else:
                raise ValueError(
                    "chief_engineer_semantic_repair_diagnosis_unsupported:"
                    + hashlib.sha256(error.encode("utf-8")).hexdigest()[:16]
                )
        return ChiefEngineerSemanticRepairDiagnosisV1(
            candidate_hash=candidate.candidate_hash,
            diagnostic_codes=tuple(dict.fromkeys(codes)),
            allowed_operations=tuple(dict.fromkeys(operations)),
        )

    def _compose_chief_engineer_semantic_repair_result(
        self,
        *,
        result: RoleExecutionResultV1,
        candidate: ChiefEngineerSemanticRepairCandidateV1,
        diagnosis: ChiefEngineerSemanticRepairDiagnosisV1,
        tasks: tuple[ChiefEngineerPortfolioTaskV1, ...],
    ) -> RoleExecutionResultV1:
        """Parse, CAS-compose, then fully validate one provider patch."""

        if not result.ok:
            return result
        raw_patch = dict(result.metadata or {}).get("structured_output")
        try:
            if not isinstance(raw_patch, Mapping):
                raise TypeError("semantic repair structured_output must be an object")
            patch, provider_binding = bind_chief_engineer_semantic_repair_provider_patch(
                raw_patch,
                candidate=candidate,
                diagnosis=diagnosis,
            )
            after, receipt = compose_chief_engineer_semantic_repair(
                candidate,
                diagnosis,
                patch,
                tasks=tasks,
            )
            output_errors = self._chief_engineer_portfolio_output_errors(after.candidate, tasks=tasks)
        except (TypeError, ValueError) as exc:
            return self._chief_engineer_post_validation_repair_result(
                prior_result=result,
                output_errors=[
                    (f"chief_engineer.semantic_patch_invalid:{type(exc).__name__}:{str(exc).strip()}")[
                        :_CHIEF_ENGINEER_SCHEMA_REPAIR_ERROR_MAX_CHARS
                    ]
                ],
            )
        metadata = dict(result.metadata or {})
        metadata.update(
            {
                "structured_output": dict(after.candidate),
                "chief_engineer_semantic_repair_receipt": receipt.to_dict(),
                "chief_engineer_semantic_repair_patch_hash": patch.patch_hash,
                "chief_engineer_semantic_repair_candidate_hash": after.candidate_hash,
                "chief_engineer_semantic_repair_provider_binding": provider_binding,
            }
        )
        composed_result = RoleExecutionResultV1(
            ok=True,
            status=str(getattr(result, "status", "") or "completed"),
            role=str(getattr(result, "role", "") or "chief_engineer"),
            workspace=str(getattr(result, "workspace", "") or self.workspace),
            task_id=getattr(result, "task_id", None),
            session_id=getattr(result, "session_id", None),
            run_id=getattr(result, "run_id", None),
            output=str(getattr(result, "output", "") or ""),
            thinking=getattr(result, "thinking", None),
            tool_calls=tuple(getattr(result, "tool_calls", ()) or ()),
            artifacts=tuple(getattr(result, "artifacts", ()) or ()),
            usage=dict(getattr(result, "usage", {}) or {}),
            metadata=metadata,
            turn_history=list(getattr(result, "turn_history", []) or []),
        )
        if output_errors:
            # A useful patch may expose a new residual.  The next repair must
            # consume the composed candidate and its receipt, not the provider's
            # now-stale patch envelope retained in ``result.metadata``.
            return self._chief_engineer_post_validation_repair_result(
                prior_result=composed_result,
                output_errors=output_errors,
            )
        return composed_result

    def _recover_chief_engineer_portfolio_structural_result(
        self,
        *,
        result: RoleExecutionResultV1,
        portfolio_task_ids: tuple[str, ...],
    ) -> RoleExecutionResultV1:
        """Recover content-preserving CE tool-argument nesting drift."""

        metadata = dict(result.metadata or {})
        tool_call = metadata.get("tool_call")
        structured_output = metadata.get("structured_output")
        arguments = structured_output if result.ok and isinstance(structured_output, Mapping) else None
        if arguments is None and isinstance(tool_call, Mapping):
            arguments = tool_call.get("arguments")
        if not isinstance(arguments, Mapping):
            return result
        recovery = normalize_chief_engineer_portfolio_tool_arguments(
            arguments,
            authoritative_task_ids=portfolio_task_ids,
        )
        if not recovery.recovered:
            return result
        schema = self._chief_engineer_structured_output_contract(portfolio_task_ids).json_schema
        schema_errors = sorted(
            Draft202012Validator(schema).iter_errors(dict(recovery.payload)),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
        if schema_errors:
            return result
        recovered_payload = dict(recovery.payload)
        metadata.update(
            {
                "structured_output": recovered_payload,
                "chief_engineer_portfolio_structural_recovery": recovery.to_dict(),
            }
        )
        if isinstance(tool_call, Mapping):
            recovered_tool_call = dict(cast(Mapping[str, Any], tool_call))
            recovered_tool_call["arguments"] = deepcopy(recovered_payload)
            metadata["tool_call"] = recovered_tool_call
        return RoleExecutionResultV1(
            ok=True,
            status="completed",
            role=str(getattr(result, "role", "") or "chief_engineer"),
            workspace=str(getattr(result, "workspace", "") or self.workspace),
            task_id=getattr(result, "task_id", None),
            session_id=getattr(result, "session_id", None),
            run_id=getattr(result, "run_id", None),
            output=json.dumps(recovered_payload, ensure_ascii=False, sort_keys=True),
            thinking=getattr(result, "thinking", None),
            tool_calls=tuple(getattr(result, "tool_calls", ()) or ()),
            artifacts=tuple(getattr(result, "artifacts", ()) or ()),
            usage=dict(getattr(result, "usage", {}) or {}),
            metadata=metadata,
            turn_history=list(getattr(result, "turn_history", []) or []),
        )

    @staticmethod
    def _chief_engineer_schema_repair_base_candidate(
        result: RoleExecutionResultV1,
        *,
        portfolio_task_ids: tuple[str, ...],
    ) -> dict[str, Any] | None:
        """Return the immutable structured candidate carried by a failed CE turn.

        Native forced-tool responses normally have empty visible assistant output.
        Their actual candidate lives in ``metadata.tool_call.arguments``.  A later
        bounded repair also carries the original candidate explicitly so a failed
        patch cannot replace valid subtrees from the previous round.
        """

        metadata = dict(result.metadata or {})
        carried = metadata.get("chief_engineer_schema_repair_base_candidate")
        candidate: dict[str, Any] | None = None
        if isinstance(carried, Mapping):
            candidate = deepcopy(dict(carried))
        structured_output = metadata.get("structured_output")
        if candidate is None and isinstance(structured_output, Mapping):
            candidate = deepcopy(dict(structured_output))
        tool_call = metadata.get("tool_call")
        if candidate is None and isinstance(tool_call, Mapping):
            arguments = tool_call.get("arguments")
            if isinstance(arguments, Mapping):
                candidate = deepcopy(dict(arguments))
        if candidate is None:
            return None
        recovery = normalize_chief_engineer_portfolio_tool_arguments(
            candidate,
            authoritative_task_ids=portfolio_task_ids,
        )
        if recovery.recovered and "unwrap_task_plan_array_items" in recovery.repair_codes:
            recovered_construction = recovery.payload.get("construction_plan")
            candidate_construction = candidate.get("construction_plan")
            if isinstance(recovered_construction, Mapping) and isinstance(candidate_construction, Mapping):
                recovered_task_plans = recovered_construction.get("task_plans")
                if isinstance(recovered_task_plans, Mapping):
                    normalized_construction = deepcopy(dict(candidate_construction))
                    normalized_construction["task_plans"] = deepcopy(dict(recovered_task_plans))
                    candidate["construction_plan"] = normalized_construction
        return candidate

    @staticmethod
    def _chief_engineer_required_property_repair_paths(
        *,
        candidate: Mapping[str, Any],
        schema: Mapping[str, Any],
    ) -> tuple[tuple[str, ...], ...]:
        """Return exact object-only paths when every schema error is ``required``.

        Array-index, type, enum, and additional-property failures stay on the
        existing fail-closed reconstruction path.  Only unambiguous missing
        object members are safe to patch without replacing valid sibling data.
        """

        errors = sorted(
            Draft202012Validator(dict(schema)).iter_errors(dict(candidate)),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
        if not errors or any(error.validator != "required" for error in errors):
            return ()
        paths: set[tuple[str, ...]] = set()
        for error in errors:
            parent_path = tuple(error.absolute_path)
            if any(not isinstance(part, str) or not part for part in parent_path):
                return ()
            instance = error.instance
            required = error.validator_value
            if not isinstance(instance, Mapping) or not isinstance(required, list):
                return ()
            missing = [item for item in required if isinstance(item, str) and item and item not in instance]
            if not missing:
                return ()
            paths.update((*cast(tuple[str, ...], parent_path), item) for item in missing)
        return tuple(sorted(paths))

    @classmethod
    def _chief_engineer_required_property_patch_schema(
        cls,
        *,
        schema: Mapping[str, Any],
        paths: tuple[tuple[str, ...], ...],
    ) -> dict[str, Any]:
        """Build a strict merge-patch schema for exact missing object members."""

        if not paths:
            raise ValueError("chief_engineer_schema_repair_paths_required")

        def _path_schema(node: Mapping[str, Any], path: tuple[str, ...]) -> dict[str, Any]:
            key = path[0]
            properties = node.get("properties")
            if not isinstance(properties, Mapping) or not isinstance(properties.get(key), Mapping):
                raise ValueError(f"chief_engineer_schema_repair_path_not_in_schema:{'.'.join(path)}")
            child_schema = cast(Mapping[str, Any], properties[key])
            child = deepcopy(dict(child_schema)) if len(path) == 1 else _path_schema(child_schema, path[1:])
            return {
                "type": "object",
                "additionalProperties": False,
                "properties": {key: child},
                "required": [key],
            }

        def _merge_schema(left: dict[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
            merged = deepcopy(left)
            merged_properties = cast(dict[str, Any], merged.setdefault("properties", {}))
            for key, right_value in cast(Mapping[str, Any], right.get("properties") or {}).items():
                left_value = merged_properties.get(key)
                if isinstance(left_value, dict) and isinstance(right_value, Mapping):
                    merged_properties[key] = _merge_schema(left_value, right_value)
                else:
                    merged_properties[key] = deepcopy(right_value)
            merged["required"] = sorted(
                {str(item) for item in merged.get("required", [])} | {str(item) for item in right.get("required", [])}
            )
            return merged

        patch_schema: dict[str, Any] = {
            "type": "object",
            "additionalProperties": False,
            "properties": {},
            "required": [],
        }
        for path in paths:
            if not path:
                raise ValueError("chief_engineer_schema_repair_path_must_not_be_empty")
            patch_schema = _merge_schema(patch_schema, _path_schema(schema, path))
        return patch_schema

    @staticmethod
    def _merge_chief_engineer_required_property_patch(
        base: Mapping[str, Any],
        patch: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Recursively merge a schema-limited patch without deleting siblings."""

        merged = deepcopy(dict(base))
        for key, value in patch.items():
            current = merged.get(key)
            if isinstance(current, Mapping) and isinstance(value, Mapping):
                merged[key] = _Mixin02._merge_chief_engineer_required_property_patch(current, value)
            else:
                merged[key] = deepcopy(value)
        return merged

    def _compose_chief_engineer_required_property_repair_result(
        self,
        *,
        result: RoleExecutionResultV1,
        base_candidate: Mapping[str, Any],
        repair_paths: tuple[tuple[str, ...], ...],
        portfolio_task_ids: tuple[str, ...],
    ) -> RoleExecutionResultV1:
        """Merge one typed missing-member patch, then revalidate the full schema."""

        metadata = dict(result.metadata or {})
        metadata["chief_engineer_schema_repair_base_candidate"] = deepcopy(dict(base_candidate))
        metadata["chief_engineer_schema_repair_paths"] = [list(path) for path in repair_paths]
        metadata["chief_engineer_schema_repair_base_candidate_hash"] = hashlib.sha256(
            json.dumps(base_candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if not result.ok:
            result.metadata.clear()
            result.metadata.update(metadata)
            return result

        patch = metadata.get("structured_output")
        if not isinstance(patch, Mapping):
            tool_call = metadata.get("tool_call")
            patch = tool_call.get("arguments") if isinstance(tool_call, Mapping) else None
        if not isinstance(patch, Mapping):
            return RoleExecutionResultV1(
                ok=False,
                status="failed",
                role=str(getattr(result, "role", "") or "chief_engineer"),
                workspace=str(getattr(result, "workspace", "") or self.workspace),
                task_id=getattr(result, "task_id", None),
                session_id=getattr(result, "session_id", None),
                run_id=getattr(result, "run_id", None),
                output=str(getattr(result, "output", "") or ""),
                thinking=getattr(result, "thinking", None),
                tool_calls=tuple(getattr(result, "tool_calls", ()) or ()),
                artifacts=tuple(getattr(result, "artifacts", ()) or ()),
                usage=dict(getattr(result, "usage", {}) or {}),
                metadata=metadata,
                error_code="chief_engineer.schema_repair_patch_missing",
                error_message="Schema repair completed without a structured patch payload.",
                turn_history=list(getattr(result, "turn_history", []) or []),
            )

        merged_candidate = self._merge_chief_engineer_required_property_patch(base_candidate, patch)
        full_schema = self._chief_engineer_structured_output_contract(portfolio_task_ids).json_schema
        errors = sorted(
            Draft202012Validator(full_schema).iter_errors(merged_candidate),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
        metadata["chief_engineer_schema_repair_base_candidate"] = deepcopy(merged_candidate)
        metadata["chief_engineer_schema_repair_patch"] = deepcopy(dict(patch))
        if errors:
            first = errors[0]
            path = ".".join(str(part) for part in first.absolute_path) or "$"
            return RoleExecutionResultV1(
                ok=False,
                status="failed",
                role=str(getattr(result, "role", "") or "chief_engineer"),
                workspace=str(getattr(result, "workspace", "") or self.workspace),
                task_id=getattr(result, "task_id", None),
                session_id=getattr(result, "session_id", None),
                run_id=getattr(result, "run_id", None),
                output="",
                thinking=getattr(result, "thinking", None),
                tool_calls=tuple(getattr(result, "tool_calls", ()) or ()),
                artifacts=tuple(getattr(result, "artifacts", ()) or ()),
                usage=dict(getattr(result, "usage", {}) or {}),
                metadata=metadata,
                error_code="structured_output_payload_schema_mismatch",
                error_message=f"structured_output_payload_schema_mismatch:{path}:{first.message}",
                turn_history=list(getattr(result, "turn_history", []) or []),
            )

        metadata["structured_output"] = deepcopy(merged_candidate)
        metadata["chief_engineer_schema_repair_merged"] = True
        tool_call = metadata.get("tool_call")
        if isinstance(tool_call, Mapping):
            merged_tool_call = dict(tool_call)
            merged_tool_call["arguments"] = deepcopy(merged_candidate)
            metadata["tool_call"] = merged_tool_call
        return RoleExecutionResultV1(
            ok=True,
            status="completed",
            role=str(getattr(result, "role", "") or "chief_engineer"),
            workspace=str(getattr(result, "workspace", "") or self.workspace),
            task_id=getattr(result, "task_id", None),
            session_id=getattr(result, "session_id", None),
            run_id=getattr(result, "run_id", None),
            output=json.dumps(merged_candidate, ensure_ascii=False, sort_keys=True),
            thinking=getattr(result, "thinking", None),
            tool_calls=tuple(getattr(result, "tool_calls", ()) or ()),
            artifacts=tuple(getattr(result, "artifacts", ()) or ()),
            usage=dict(getattr(result, "usage", {}) or {}),
            metadata=metadata,
            turn_history=list(getattr(result, "turn_history", []) or []),
        )

    @staticmethod
    def _append_chief_engineer_structural_recovery_signal(
        *,
        result: RoleExecutionResultV1,
        stage_signals: list[dict[str, Any]],
        task_id: str,
    ) -> None:
        recovery = dict(result.metadata or {}).get("chief_engineer_portfolio_structural_recovery")
        if not isinstance(recovery, Mapping) or not bool(recovery.get("recovered")):
            return
        stage_signals.append(
            {
                "code": "chief_engineer.portfolio_structural_recovered",
                "severity": "warning",
                "detail": "Relocated CE tool arguments and revalidated the exact portfolio schema.",
                "task_id": task_id,
                "source_hash": str(recovery.get("source_hash") or ""),
                "recovered_hash": str(recovery.get("recovered_hash") or ""),
                "repair_codes": list(recovery.get("repair_codes") or []),
                "provider_call_consumed": False,
            }
        )

    def _build_chief_engineer_portfolio_from_candidate(
        self,
        *,
        run_id: str,
        tasks: tuple[ChiefEngineerPortfolioTaskV1, ...],
        authority: _ChiefEngineerPortfolioAuthorityV1,
        structured_output: Mapping[str, Any],
        revalidate_existing: bool = False,
    ) -> ChiefEngineerBlueprintPortfolioV1:
        return build_chief_engineer_blueprint_portfolio(
            BuildChiefEngineerBlueprintPortfolioCommandV1(
                workspace=str(self.workspace),
                run_id=run_id,
                tasks=tasks,
                authority_carrier=_issue_chief_engineer_portfolio_authority_carrier(
                    workspace=str(self.workspace),
                    run_id=run_id,
                    project_id=authority.project_id,
                    pm_stage_event_id=authority.pm_stage_event_id,
                    pm_contract_hash=authority.pm_contract_hash,
                    tasks=tasks,
                    catalog_snapshot=authority.catalog_snapshot,
                    catalog_snapshot_hash=authority.catalog_snapshot_hash,
                    verifier_policy_hash=authority.verifier_policy_hash,
                    verifier_policy_snapshot=authority.verifier_policy,
                    verifier_policy_snapshot_hash=authority.verifier_policy_snapshot_hash,
                    verification_command_authority=authority.verification_command_authority,
                ),
                llm_blueprint=dict(structured_output),
            ),
            revalidate_existing=revalidate_existing,
        )

    @staticmethod
    def _chief_engineer_schema_repair_output_tokens(
        *,
        task_count: int,
        repair_round: int,
        semantic_patch: bool,
    ) -> int:
        """Return the smallest safe budget for one CE repair request.

        Typed patches and the first bounded schema repair stay at the 8K
        ceiling.  A later structural repair has to reconstruct the same full
        multi-task portfolio as the primary request, so it must reuse the
        task-scaled portfolio budget instead of deterministically truncating.
        """

        if task_count < 1:
            raise ValueError("chief_engineer_repair_task_count_must_be_positive")
        if repair_round < 1:
            raise ValueError("chief_engineer_repair_round_must_be_positive")
        if semantic_patch or repair_round == 1:
            return _CHIEF_ENGINEER_SCHEMA_REPAIR_MAX_TOKENS
        return chief_engineer_portfolio_output_tokens(task_count)

    async def _run_chief_engineer_schema_repair(
        self,
        *,
        run: FactoryRun,
        authority_port: FactoryRoleEvidenceAuthorityPort,
        authority_binding: FactoryRoleEvidenceAuthorityBindingV1,
        prior_result: RoleExecutionResultV1,
        portfolio_context: Mapping[str, Any],
        portfolio_task_ids: tuple[str, ...],
        portfolio_tasks: tuple[ChiefEngineerPortfolioTaskV1, ...],
        deadline_decision: FactoryDeadlineAdmissionV1,
        repair_round: int = 1,
        semantic_candidate: ChiefEngineerSemanticRepairCandidateV1 | None = None,
        semantic_diagnosis: ChiefEngineerSemanticRepairDiagnosisV1 | None = None,
        prompt_profile_identity: Mapping[str, str] | None = None,
    ) -> RoleExecutionResultV1:
        """Run one separately claimed schema reconstruction or typed semantic patch."""

        if repair_round < 1:
            raise ValueError("chief_engineer_repair_round_must_be_positive")
        repair_scope = _ChiefEngineerExecutionAttemptLeaseScope()
        semantic_patch = semantic_candidate is not None or semantic_diagnosis is not None
        if semantic_patch and (semantic_candidate is None or semantic_diagnosis is None):
            raise ValueError("chief_engineer_semantic_repair_candidate_and_diagnosis_required")
        repair_output_tokens = self._chief_engineer_schema_repair_output_tokens(
            task_count=len(portfolio_task_ids),
            repair_round=repair_round,
            semantic_patch=semantic_patch,
        )
        if semantic_patch:
            repair_suffix = "SEMANTIC-PATCH" if repair_round == 1 else f"SEMANTIC-PATCH-REPAIR-{repair_round}"
        else:
            repair_suffix = "SCHEMA-REPAIR" if repair_round == 1 else f"CONTRACT-REPAIR-{repair_round}"
        repair_task_id = f"CE-PORTFOLIO-{run.id}-{repair_suffix}"
        repair_timeout_seconds = int(deadline_decision.timeout_seconds)
        repair_lease_budget = self._chief_engineer_execution_attempt_lease_budget(repair_timeout_seconds)
        repair_objective = self._chief_engineer_schema_repair_objective(
            prior_result=prior_result,
            portfolio_task_ids=portfolio_task_ids,
        )
        schema_repair_base_candidate: dict[str, Any] | None = None
        schema_repair_paths: tuple[tuple[str, ...], ...] = ()
        schema_repair_patch_schema: dict[str, Any] | None = None
        if not semantic_patch:
            schema_repair_base_candidate = self._chief_engineer_schema_repair_base_candidate(
                prior_result,
                portfolio_task_ids=portfolio_task_ids,
            )
            if schema_repair_base_candidate is not None:
                full_schema = self._chief_engineer_structured_output_contract(portfolio_task_ids).json_schema
                schema_repair_paths = self._chief_engineer_required_property_repair_paths(
                    candidate=schema_repair_base_candidate,
                    schema=full_schema,
                )
                if schema_repair_paths:
                    schema_repair_patch_schema = self._chief_engineer_required_property_patch_schema(
                        schema=full_schema,
                        paths=schema_repair_paths,
                    )
                    base_candidate_hash = hashlib.sha256(
                        json.dumps(
                            schema_repair_base_candidate,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest()
                    repair_objective = (
                        "Repair the existing Chief Engineer portfolio by returning exactly one typed missing-member "
                        "patch through submit_structured_role_output. The platform has retained the immutable prior "
                        "candidate and will merge this patch server-side before revalidating the complete strict "
                        "portfolio schema. Emit ONLY the schema-required paths listed below; do not reconstruct, "
                        "repeat, delete, or overwrite valid sibling fields. Preserve PM task authority and derive "
                        "the missing content from the validated PM contracts, target_files, and scope_paths already "
                        "attached to this request. Emit no assistant prose or raw JSON outside the single required "
                        "tool call.\n"
                        f"Immutable base candidate SHA-256: {base_candidate_hash}\n"
                        "Required missing paths: "
                        + json.dumps([list(path) for path in schema_repair_paths], ensure_ascii=False)
                    )
        provider_patch_context: dict[str, Any] | None = None
        if semantic_candidate is not None and semantic_diagnosis is not None:
            provider_patch_context = project_chief_engineer_semantic_repair_provider_context(
                semantic_candidate,
                semantic_diagnosis,
                tasks=portfolio_tasks,
            )
            if not bool(provider_patch_context.get("repair_feasible")):
                metadata = dict(prior_result.metadata or {})
                metadata.update(
                    {
                        "error_code": "chief_engineer.semantic_repair_authority_infeasible",
                        "root_cause_hint": (
                            "The schema-valid CE candidate cannot close its delivery-depth deficit within "
                            "immutable PM target_files/scope_paths. PM authority must be corrected before "
                            "another CE provider attempt."
                        ),
                        "chief_engineer_semantic_repair_provider_context": provider_patch_context,
                    }
                )
                return RoleExecutionResultV1(
                    ok=False,
                    status="failed",
                    role=str(getattr(prior_result, "role", "") or "chief_engineer"),
                    workspace=str(getattr(prior_result, "workspace", "") or self.workspace),
                    task_id=getattr(prior_result, "task_id", None),
                    session_id=getattr(prior_result, "session_id", None),
                    run_id=getattr(prior_result, "run_id", None),
                    output=str(getattr(prior_result, "output", "") or ""),
                    thinking=getattr(prior_result, "thinking", None),
                    tool_calls=tuple(getattr(prior_result, "tool_calls", ()) or ()),
                    artifacts=tuple(getattr(prior_result, "artifacts", ()) or ()),
                    usage=dict(getattr(prior_result, "usage", {}) or {}),
                    metadata=metadata,
                    error_code="chief_engineer.semantic_repair_authority_infeasible",
                    error_message=str(metadata["root_cause_hint"]),
                    turn_history=list(getattr(prior_result, "turn_history", ()) or ()),
                )
            repair_objective = (
                "Repair the schema-valid Chief Engineer candidate by returning exactly one typed semantic patch "
                "envelope. Do not reconstruct the portfolio, emit JSON Pointer/free-form paths, or delete raw "
                "portfolio fields. The only authorized removal surface is entrypoint_remove_obligation_ids: "
                "when replacing a diagnosed invalid current entrypoint under a new obligation_id, include the "
                "obsolete exact id there and add one same-owner same-kind entrypoint_upsert in the same envelope. "
                "Never leave the invalid row in place beside its replacement. Do not change PM task authority "
                "or alter unrelated sections. Use only the operation arrays authorized "
                "by the diagnosis. Echo base_candidate_hash and diagnosis_hash exactly. Call the required "
                "result-submission tool exactly once; do not emit prose or a second envelope. "
                "Treat every existing semantic ID as an immutable identity. For each upsert, consult "
                "upsert_identity_policy in the authoritative patch context: an existing ID MUST preserve every "
                "listed immutable field exactly. If the desired path or any other immutable field differs, mint "
                "a new unique obligation_id and leave the existing row unchanged; never repurpose an old ID. "
                "When adding a test artifact under expandable_test_scope_paths, its file suffix MUST appear in "
                "the owner task's allowed_source_suffixes; an existing harness path in another language does not "
                "authorize inventing more files with that suffix. "
                "When authoritative patch context contains delivery_depth_feasibility.deficits, satisfy every "
                "row in this single atomic envelope: add at least that row's exact deficit count of distinct, "
                "authorized artifacts for its metric. Do not treat invalid-language current rows as satisfying "
                "the projected deficit. "
                "For behavior_invariant_upserts, every covered_obligation_ids value MUST either come from "
                "allowed_completion_obligation_ids in the authoritative patch context OR be the obligation_id "
                "of an artifact_upsert in this same atomic envelope; never copy diagnostic prose, gate labels, "
                "commands, or acceptance text into that ID field. When the diagnostic is "
                "chief_engineer.shared_behavior_contract.cross_task_production_test_coverage_missing, replace "
                "or add an invariant whose owner is the production task, whose consumers include the test task, "
                "and whose covered_obligation_ids include BOTH one required production source/entrypoint "
                "artifact obligation and one required test artifact obligation (or its required test-verifier "
                "obligation). Bind that invariant from both tasks.\n"
                f"Base candidate hash: {semantic_candidate.candidate_hash}\n"
                f"Diagnosis hash: {semantic_diagnosis.diagnosis_hash}\n"
                "Stable diagnostic codes: "
                + json.dumps(list(semantic_diagnosis.diagnostic_codes), ensure_ascii=False)
                + "\nAllowed operations: "
                + json.dumps(list(semantic_diagnosis.allowed_operations), ensure_ascii=False)
                + "\nExact base candidate patch context (authoritative JSON; do not omit or duplicate current rows): "
                + json.dumps(provider_patch_context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            )
        prior_error = str(prior_result.error_message or prior_result.error_code or "output validation failed").strip()[
            :_CHIEF_ENGINEER_SCHEMA_REPAIR_ERROR_MAX_CHARS
        ]
        root_member_match = re.search(
            r"Additional properties are not allowed \((?P<members>.+?)\s+(?:was|were) unexpected\)",
            prior_error,
        )
        observed_invalid_root_members = (
            sorted(set(re.findall(r"'([^']+)'", root_member_match.group("members"))))
            if root_member_match is not None
            else []
        )
        raw_pm_tasks = portfolio_context.get("pm_task_contracts")
        pm_task_payloads: list[Mapping[str, Any]] = (
            [dict(item) for item in raw_pm_tasks if isinstance(item, Mapping)] if isinstance(raw_pm_tasks, list) else []
        )
        depth_projection = project_chief_engineer_delivery_depth_feasibility_from_pm_tasks(
            {},
            pm_tasks=pm_task_payloads,
        )
        delivery_depth_minimums = {
            str(key): int(value)
            for key, value in dict(depth_projection.get("minimums") or {}).items()
            if isinstance(value, int) and not isinstance(value, bool) and value > 0
        }
        if delivery_depth_minimums:
            repair_objective += "\nAuthoritative delivery-depth minimums (exact values): " + json.dumps(
                delivery_depth_minimums, ensure_ascii=False, sort_keys=True
            )
        if observed_invalid_root_members:
            repair_objective += (
                "\nObserved invalid root members from the prior result envelope (names only; relocate them under "
                "their schema-declared parents): " + json.dumps(observed_invalid_root_members, ensure_ascii=False)
            )
        prior_output = (
            json.dumps(
                schema_repair_base_candidate,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if schema_repair_base_candidate is not None
            else str(prior_result.output or "")
        )
        repair_failure_feedback = {
            "schema_version": "factory.chief_engineer_schema_repair.failure_evidence.v1",
            "failure_class": self._ce_schema_repair_failure_class(prior_result),
            "failure_stage": "chief_engineer_review",
            "detail": prior_error,
            "prior_output_sha256": hashlib.sha256(prior_output.encode("utf-8")).hexdigest(),
            "prior_output_chars": len(prior_output),
            "evidence_refs": [],
            "delivery_depth_minimums": delivery_depth_minimums,
            "observed_invalid_root_members": observed_invalid_root_members,
            "expected_root_members": [
                "construction_plan",
                "project_completion_contract",
                "risk_flags",
                "scope_for_apply",
            ],
        }
        prior_metadata = dict(prior_result.metadata or {})
        post_validation_errors = prior_metadata.get("chief_engineer_post_validation_errors")
        if isinstance(post_validation_errors, list):
            repair_failure_feedback["contract_validation_errors"] = [
                str(item).strip() for item in post_validation_errors if str(item).strip()
            ]
        repair_profile_identity = {
            str(key): str(value)
            for key, value in dict(prompt_profile_identity or {}).items()
            if str(key).strip() and str(value).strip()
        }
        if not repair_profile_identity:
            repair_profile_identity = self._ce_prompt_profile_identity(prior_result)
        required_profile_fields = {"language", "task_type", "prompt_stage", "artifact"}
        missing_profile_fields = sorted(required_profile_fields.difference(repair_profile_identity))
        if missing_profile_fields:
            raise RuntimeError(
                "chief_engineer_schema_repair_prompt_profile_identity_missing:" + ",".join(missing_profile_fields)
            )
        try:
            runtime_task_id, execution_attempt = self._claim_chief_engineer_execution_attempt(
                run_id=run.id,
                portfolio_task_id=repair_task_id,
                objective=repair_objective,
                lease_budget=repair_lease_budget,
            )
            repair_scope.bind_claim(task_id=runtime_task_id, execution_attempt=execution_attempt)
            repair_scope.start_keeper(
                _ChiefEngineerExecutionAttemptLeaseKeeper(
                    workspace=str(self.workspace),
                    task_id=runtime_task_id,
                    execution_attempt=execution_attempt,
                    budget=repair_lease_budget,
                )
            )
            repair_context = deepcopy(dict(portfolio_context))
            repair_context.update(
                {
                    **repair_profile_identity,
                    "chief_engineer_schema_repair_prompt_profile_source": ("primary_final_request_context_audit"),
                    "chief_engineer_schema_repair": True,
                    "chief_engineer_repair_round": repair_round,
                    "chief_engineer_schema_repair_of_task_id": f"CE-PORTFOLIO-{run.id}",
                    "chief_engineer_prior_error_code": str(prior_result.error_code or ""),
                    "chief_engineer_prior_error_message": prior_error,
                    "failure_feedback": repair_failure_feedback,
                    "chief_engineer_deadline_decision": deadline_decision.to_dict(),
                    "chief_engineer_llm_timeout_seconds": repair_timeout_seconds,
                    "llm_call_timeout_seconds": repair_timeout_seconds,
                    "request_timeout_seconds": repair_timeout_seconds,
                    "temperature": 0.0,
                    "llm_max_tokens": repair_output_tokens,
                    "reasoning_budget_tokens": _CHIEF_ENGINEER_SCHEMA_REPAIR_REASONING_BUDGET_TOKENS,
                    "response_format_mode": "json",
                    "chief_engineer_json_contract_required": True,
                    "chief_engineer_portfolio_required": not semantic_patch,
                    "chief_engineer_schema_repair_base_candidate_hash": (
                        hashlib.sha256(prior_output.encode("utf-8")).hexdigest()
                        if schema_repair_base_candidate is not None
                        else ""
                    ),
                    "chief_engineer_schema_repair_paths": [list(path) for path in schema_repair_paths],
                    "chief_engineer_semantic_patch_transport_retry_budget": (
                        _CHIEF_ENGINEER_SEMANTIC_PATCH_TRANSPORT_MAX_RETRIES if semantic_patch else 0
                    ),
                    "chief_engineer_repair_transport_retry_budget": (
                        _CHIEF_ENGINEER_REPAIR_TRANSPORT_MAX_RETRIES
                    ),
                }
            )
            if semantic_candidate is not None and semantic_diagnosis is not None:
                repair_context.update(
                    {
                        "chief_engineer_semantic_repair": True,
                        "chief_engineer_semantic_repair_candidate": semantic_candidate.to_dict(),
                        "chief_engineer_semantic_repair_diagnosis": semantic_diagnosis.to_dict(),
                        "chief_engineer_semantic_repair_provider_context": provider_patch_context,
                        "chief_engineer_semantic_repair_base_candidate_hash": semantic_candidate.candidate_hash,
                        "chief_engineer_semantic_repair_diagnosis_hash": semantic_diagnosis.diagnosis_hash,
                        "chief_engineer_portfolio_required": False,
                    }
                )
            structured_output_contract = self._chief_engineer_structured_output_contract(portfolio_task_ids)
            if schema_repair_patch_schema is not None:
                structured_output_contract = RoleStructuredOutputContractV1(
                    schema_name="chief_engineer_blueprint_portfolio_required_patch",
                    description=(
                        "Strict merge patch containing only schema-proven missing required object members. "
                        "The platform retains and revalidates the immutable full CE candidate."
                    ),
                    json_schema=schema_repair_patch_schema,
                )
            if semantic_candidate is not None and semantic_diagnosis is not None:
                structured_output_contract = RoleStructuredOutputContractV1(
                    schema_name="chief_engineer_semantic_repair_patch",
                    description=(
                        "Typed, diagnosis-scoped patch for one exact CE semantic candidate and diagnosis; "
                        "base_candidate_hash and diagnosis_hash are immutable CAS values."
                    ),
                    json_schema=build_chief_engineer_semantic_repair_patch_schema(
                        allowed_operations=semantic_diagnosis.allowed_operations,
                    ),
                )
            command = ExecuteRoleTaskCommandV1(
                role="chief_engineer",
                task_id=repair_task_id,
                workspace=str(self.workspace),
                objective=repair_objective,
                run_id=run.id,
                # Diversify the bounded repair transport after an incomplete
                # streamed forced-tool payload. Schema and CE authority stay strict.
                stream=False,
                context=repair_context,
                timeout_seconds=repair_timeout_seconds,
                execution_attempt=execution_attempt,
                structured_output_contract=structured_output_contract,
                metadata={
                    "pm_task_contract": dict(repair_context["pm_task_contract"]),
                    "pm_task_contracts": list(repair_context["pm_task_contracts"]),
                    "target_files": list(repair_context["target_files"]),
                    "scope_paths": list(repair_context["scope_paths"]),
                    "source": "factory_stage_executor.chief_engineer_schema_repair",
                    "schema_repair_of_task_id": f"CE-PORTFOLIO-{run.id}",
                    "chief_engineer_repair_round": repair_round,
                    "chief_engineer_semantic_repair": semantic_patch,
                    "chief_engineer_schema_repair_base_candidate_hash": (
                        hashlib.sha256(prior_output.encode("utf-8")).hexdigest()
                        if schema_repair_base_candidate is not None
                        else ""
                    ),
                    "chief_engineer_schema_repair_paths": [list(path) for path in schema_repair_paths],
                    "chief_engineer_semantic_repair_base_candidate_hash": (
                        semantic_candidate.candidate_hash if semantic_candidate is not None else ""
                    ),
                    "chief_engineer_semantic_repair_diagnosis_hash": (
                        semantic_diagnosis.diagnosis_hash if semantic_diagnosis is not None else ""
                    ),
                    "inherited_prompt_profile_identity": dict(repair_profile_identity),
                    "cognitive_runtime_mode": "off",
                    "cognitive_runtime_enabled": False,
                    "cognitive_runtime_required": False,
                    "llm_call_timeout_seconds": repair_timeout_seconds,
                    "validate_output": True,
                    "max_retries": _CHIEF_ENGINEER_REPAIR_TRANSPORT_MAX_RETRIES,
                    "chief_engineer_repair_transport_retry_budget": (
                        _CHIEF_ENGINEER_REPAIR_TRANSPORT_MAX_RETRIES
                    ),
                    "chief_engineer_semantic_patch_transport_retry_budget": (
                        _CHIEF_ENGINEER_SEMANTIC_PATCH_TRANSPORT_MAX_RETRIES if semantic_patch else 0
                    ),
                    "temperature": 0.0,
                    "llm_max_tokens": repair_output_tokens,
                    "reasoning_budget_tokens": _CHIEF_ENGINEER_SCHEMA_REPAIR_REASONING_BUDGET_TOKENS,
                    "response_format_mode": "json",
                    "chief_engineer_json_contract_required": True,
                    "chief_engineer_portfolio_required": not semantic_patch,
                },
            )
            result = cast(
                RoleExecutionResultV1,
                await self._call_with_factory_role_evidence_authority(
                    authority_port,
                    "chief_engineer",
                    lambda: pkg().RoleRuntimeService().execute_role_task(command),
                    authority_binding=authority_binding,
                ),
            )
            if (
                not semantic_patch
                and schema_repair_base_candidate is not None
                and schema_repair_paths
                and schema_repair_patch_schema is not None
            ):
                result = self._compose_chief_engineer_required_property_repair_result(
                    result=result,
                    base_candidate=schema_repair_base_candidate,
                    repair_paths=schema_repair_paths,
                    portfolio_task_ids=portfolio_task_ids,
                )
            if not semantic_patch:
                # Required-property repair first merges a strict partial payload
                # into the frozen full portfolio.  That composed portfolio must
                # pass through the same content-preserving structural recovery
                # as every other full CE result.  Exact L3-24 r42 otherwise
                # froze one-task owner-only invariants, then asked a semantic
                # patch to invent an impossible sibling consumer.
                result = self._recover_chief_engineer_portfolio_structural_result(
                    result=result,
                    portfolio_task_ids=portfolio_task_ids,
                )
            should_settle, heartbeat_failure = repair_scope.begin_settlement()
            if not should_settle or repair_scope.execution_attempt is None:
                reason = heartbeat_failure.error_message if heartbeat_failure is not None else "settlement_not_started"
                raise RuntimeError(f"chief_engineer_schema_repair_settlement_blocked:{reason}")
            self._settle_chief_engineer_execution_attempt(
                task_id=runtime_task_id,
                execution_attempt=repair_scope.execution_attempt,
                stage_status="success" if result.ok else "failed",
                summary=(
                    "chief_engineer_schema_repair_completed"
                    if result.ok
                    else str(result.error_code or "chief_engineer_schema_repair_failed")
                ),
            )
            if result.ok and repair_round == 1:
                self._complete_chief_engineer_attempt_after_schema_repair(
                    run_id=run.id,
                    objective=repair_objective,
                    lease_budget=repair_lease_budget,
                )
            return result
        except asyncio.CancelledError as exc:
            self._settle_chief_engineer_execution_attempt_after_exception(
                lease_scope=repair_scope,
                stage_status="cancelled",
                summary="chief_engineer_schema_repair_cancelled",
                preserved_error=exc,
            )
            raise
        except BaseException as exc:
            self._settle_chief_engineer_execution_attempt_after_exception(
                lease_scope=repair_scope,
                stage_status="failed",
                summary=f"chief_engineer_schema_repair_exception:{type(exc).__name__}",
                preserved_error=exc,
            )
            raise
        finally:
            repair_scope.stop_keeper()

    async def _execute_chief_engineer_review(self, run: FactoryRun, context: dict[str, Any]) -> StageResult:
        """Run CE review under one claim-bound heartbeat and settlement scope."""

        lease_scope = _ChiefEngineerExecutionAttemptLeaseScope()
        try:
            return await self._execute_chief_engineer_review_with_attempt_lease(
                run,
                context,
                lease_scope,
            )
        except asyncio.CancelledError as exc:
            self._settle_chief_engineer_execution_attempt_after_exception(
                lease_scope=lease_scope,
                stage_status="cancelled",
                summary="chief_engineer_portfolio_review_cancelled",
                preserved_error=exc,
            )
            raise
        except BaseException as exc:
            self._settle_chief_engineer_execution_attempt_after_exception(
                lease_scope=lease_scope,
                stage_status="failed",
                summary=f"chief_engineer_portfolio_review_exception:{type(exc).__name__}",
                preserved_error=exc,
            )
            raise
        finally:
            lease_scope.stop_keeper()

    async def _execute_chief_engineer_review_with_attempt_lease(
        self,
        run: FactoryRun,
        context: dict[str, Any],
        lease_scope: _ChiefEngineerExecutionAttemptLeaseScope,
    ) -> StageResult:
        """Create one CE project portfolio and project task-level handoffs."""

        logger.info("Executing Chief Engineer project review for run %s", run.id)
        authority_port = self._factory_role_evidence_cutoff_port(context)
        synced_plan_source = self._ensure_pm_plan_contract_available()
        self._enrich_pm_plan_contract_artifact("tasks/plan.json")
        stage_signals: list[dict[str, Any]] = []
        blueprint_rows: list[dict[str, Any]] = []
        portfolio: ChiefEngineerBlueprintPortfolioV1 | None = None
        ce_evidence: dict[str, Any] = {}
        llm_call_count = 0
        cancelled_by_factory = False
        ce_runtime_task_id: int | None = None
        ce_execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None

        if synced_plan_source:
            stage_signals.append(
                {
                    "code": "chief_engineer.plan_contract_synced_from_workspace_mirror",
                    "severity": "info",
                    "detail": "Copied PM workspace plan mirror into runtime tasks/plan.json before blueprint review.",
                    "source_path": synced_plan_source,
                }
            )

        pm_tasks = self._load_pm_plan_tasks("tasks/plan.json")
        if not pm_tasks:
            stage_signals.append(
                {
                    "code": "chief_engineer.plan_missing",
                    "severity": "error",
                    "detail": "tasks/plan.json missing or empty tasks array",
                }
            )

        cancel_event = self._resolve_cancel_event(context)
        abort_checker = self._resolve_abort_checker(context)
        if pm_tasks and cancel_event is not None and cancel_event.is_set():
            cancelled_by_factory = True
            stage_signals.append(
                {
                    "code": "chief_engineer.cancelled_before_portfolio",
                    "severity": "warning",
                    "detail": "Factory cancel event was set before the CE portfolio request.",
                }
            )
        if pm_tasks and not cancelled_by_factory and abort_checker is not None:
            abort_reason = ""
            with contextlib.suppress(AttributeError, OSError, RuntimeError, TypeError, ValueError):
                abort_reason = str(await abort_checker() or "").strip()
            if abort_reason:
                cancelled_by_factory = True
                stage_signals.append(
                    {
                        "code": "chief_engineer.cancelled_before_portfolio",
                        "severity": "warning",
                        "detail": f"Factory abort was requested before CE portfolio review: {abort_reason}",
                        "abort_reason": abort_reason,
                    }
                )

        portfolio_tasks: tuple[ChiefEngineerPortfolioTaskV1, ...] = ()
        portfolio_context: dict[str, Any] = {}
        portfolio_authority: _ChiefEngineerPortfolioAuthorityV1 | None = None
        deadline_decision: FactoryDeadlineAdmissionV1 | None = None
        if pm_tasks and not cancelled_by_factory:
            try:
                start_metadata_raw = context.get("metadata")
                start_metadata = start_metadata_raw if isinstance(start_metadata_raw, Mapping) else {}
                local_rework_raw = start_metadata.get("chief_engineer_local_rework_evidence")
                local_rework_evidence = local_rework_raw if isinstance(local_rework_raw, Mapping) else None
                portfolio_tasks = self._chief_engineer_portfolio_tasks(pm_tasks)
                portfolio_context = self._chief_engineer_portfolio_context(
                    pm_tasks,
                    run_id=run.id,
                    failure_feedback=local_rework_evidence,
                )
            except (TypeError, ValueError) as exc:
                stage_signals.append(
                    {
                        "code": "chief_engineer.portfolio_contract_invalid",
                        "severity": "error",
                        "detail": f"{type(exc).__name__}: {exc}",
                    }
                )

        if portfolio_tasks:
            try:
                portfolio_authority = await self._load_chief_engineer_portfolio_authority(
                    run=run,
                    pm_tasks=pm_tasks,
                    portfolio_tasks=portfolio_tasks,
                )
                portfolio_context["project_completion_authority"] = {
                    "project_id": portfolio_authority.project_id,
                    "run_id": run.id,
                    "pm_contract_hash": portfolio_authority.pm_contract_hash,
                    "covered_task_ids": list(portfolio_authority.pm_task_ids),
                    "project_kind_authority": portfolio_authority.project_kind_authority.to_dict(),
                    "completion_predicate_version": "polaris.project_completion_predicate.v1",
                    "verifier_policy_hash": portfolio_authority.verifier_policy_hash,
                    "verifier_policy": dict(portfolio_authority.verifier_policy),
                    "verifier_policy_snapshot_hash": portfolio_authority.verifier_policy_snapshot_hash,
                    "verification_command_authority": [
                        item.to_dict() for item in portfolio_authority.verification_command_authority
                    ],
                    "authority": "factory_committed_pm_and_verifier_policy",
                    "llm_may_override": False,
                }
            except _ChiefEngineerPortfolioAuthorityError as exc:
                stage_signals.append(
                    {
                        "code": exc.code,
                        "severity": "error",
                        "detail": str(exc),
                    }
                )
                portfolio_tasks = ()
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                stage_signals.append(
                    {
                        "code": "chief_engineer.project_completion_authority_invalid",
                        "severity": "error",
                        "detail": f"{type(exc).__name__}: {exc}",
                    }
                )
                portfolio_tasks = ()

        ce_result: RoleExecutionResultV1 | None = None
        if portfolio_tasks and portfolio is None:
            dependency_schedule = build_task_dependency_schedule(pm_tasks)
            requested_timeout_seconds = self._chief_engineer_llm_timeout_seconds(context)
            deadline_decision = self._chief_engineer_deadline_projection_decision(
                context,
                requested_timeout_seconds=requested_timeout_seconds,
                dependency_schedule=dependency_schedule,
            )
            deadline_payload = deadline_decision.to_dict()
            if deadline_decision.disposition is FactoryDeadlineDispositionV1.BLOCK:
                stage_signals.append(
                    {
                        "code": "chief_engineer.deadline_admission_blocked",
                        "severity": "error",
                        "detail": (
                            "The CE portfolio request was not admitted because the remaining Factory lease "
                            "cannot preserve all mandatory Director, QA, finalization, and safety budgets."
                        ),
                        "deadline_decision": deadline_payload,
                        "reason": deadline_decision.reason,
                    }
                )
            else:
                ce_timeout_seconds = int(deadline_decision.timeout_seconds)
                ce_lease_budget = self._chief_engineer_execution_attempt_lease_budget(ce_timeout_seconds)
                portfolio_context.update(
                    {
                        "cognitive_runtime_mode": "off",
                        "cognitive_runtime_enabled": False,
                        "cognitive_runtime_required": False,
                        "suppress_working_memory_contract": True,
                        "suppress_tool_policy_prompt": True,
                        "disable_internal_tool_rounds": True,
                        "delivery_mode": "analyze_only",
                        "temperature": 0.2,
                        "response_format_mode": "json",
                        "chief_engineer_json_contract_required": True,
                        "chief_engineer_portfolio_required": True,
                        "llm_max_tokens": chief_engineer_portfolio_output_tokens(len(portfolio_tasks)),
                        "reasoning_budget_tokens": _CHIEF_ENGINEER_PORTFOLIO_REASONING_BUDGET_TOKENS,
                        "chief_engineer_llm_timeout_seconds": ce_timeout_seconds,
                        "llm_call_timeout_seconds": ce_timeout_seconds,
                        "request_timeout_seconds": ce_timeout_seconds,
                        "chief_engineer_deadline_decision": deadline_payload,
                    }
                )
                portfolio_task_id = f"CE-PORTFOLIO-{run.id}"
                previous_repair_diagnostic = ""
                primary_prompt_profile_identity: dict[str, str] = {}
                semantic_repair_candidate: ChiefEngineerSemanticRepairCandidateV1 | None = None
                semantic_repair_diagnosis: ChiefEngineerSemanticRepairDiagnosisV1 | None = None
                try:
                    objective = self._chief_engineer_portfolio_objective(pm_tasks)
                    ce_runtime_task_id, ce_execution_attempt = self._claim_chief_engineer_execution_attempt(
                        run_id=run.id,
                        portfolio_task_id=portfolio_task_id,
                        objective=objective,
                        lease_budget=ce_lease_budget,
                    )
                    lease_scope.bind_claim(
                        task_id=ce_runtime_task_id,
                        execution_attempt=ce_execution_attempt,
                    )
                    lease_scope.start_keeper(
                        _ChiefEngineerExecutionAttemptLeaseKeeper(
                            workspace=str(self.workspace),
                            task_id=ce_runtime_task_id,
                            execution_attempt=ce_execution_attempt,
                            budget=ce_lease_budget,
                        )
                    )
                    command = ExecuteRoleTaskCommandV1(
                        role="chief_engineer",
                        task_id=portfolio_task_id,
                        workspace=str(self.workspace),
                        objective=objective,
                        run_id=run.id,
                        stream=True,
                        context=portfolio_context,
                        timeout_seconds=ce_timeout_seconds,
                        execution_attempt=ce_execution_attempt,
                        structured_output_contract=self._chief_engineer_structured_output_contract(
                            tuple(task.task_id for task in portfolio_tasks)
                        ),
                        metadata={
                            "pm_task_contract": dict(portfolio_context["pm_task_contract"]),
                            "pm_task_contracts": list(portfolio_context["pm_task_contracts"]),
                            "target_files": list(portfolio_context["target_files"]),
                            "scope_paths": list(portfolio_context["scope_paths"]),
                            "source": "factory_stage_executor.chief_engineer_portfolio_review",
                            "cognitive_runtime_mode": "off",
                            "cognitive_runtime_enabled": False,
                            "cognitive_runtime_required": False,
                            "llm_call_timeout_seconds": ce_timeout_seconds,
                            "validate_output": True,
                            "max_retries": 0,
                            "temperature": 0.2,
                            "reasoning_budget_tokens": _CHIEF_ENGINEER_PORTFOLIO_REASONING_BUDGET_TOKENS,
                            "response_format_mode": "json",
                            "chief_engineer_json_contract_required": True,
                            "chief_engineer_portfolio_required": True,
                            "project_completion_authority": dict(portfolio_context["project_completion_authority"]),
                        },
                    )
                    llm_call_count = 1
                    authority_binding = authority_port.mint_authority_binding("chief_engineer")
                    ce_result = cast(
                        RoleExecutionResultV1,
                        await self._call_with_factory_role_evidence_authority(
                            authority_port,
                            "chief_engineer",
                            lambda: pkg().RoleRuntimeService().execute_role_task(command),
                            authority_binding=authority_binding,
                        ),
                    )
                    ce_result = self._recover_chief_engineer_portfolio_structural_result(
                        result=ce_result,
                        portfolio_task_ids=tuple(task.task_id for task in portfolio_tasks),
                    )
                    primary_prompt_profile_identity = self._ce_prompt_profile_identity(ce_result)
                    self._append_chief_engineer_structural_recovery_signal(
                        result=ce_result,
                        stage_signals=stage_signals,
                        task_id=portfolio_task_id,
                    )
                    if self._ce_portfolio_result_allows_schema_repair(ce_result):
                        previous_repair_diagnostic = str(
                            ce_result.error_message or ce_result.error_code or "output validation failed"
                        ).strip()
                        initial_evidence = self._ce_extract_llm_evidence(
                            ce_result,
                            task_id=portfolio_task_id,
                            run_id=run.id,
                        )
                        repair_signal: dict[str, Any] = {
                            "code": "chief_engineer.output_schema_repair_started",
                            "severity": "warning",
                            "detail": str(
                                ce_result.error_message
                                or "CE stream output failed validation; one bounded schema repair was requested."
                            ),
                            "task_id": portfolio_task_id,
                            "repair_task_id": f"{portfolio_task_id}-SCHEMA-REPAIR",
                            "prior_error_code": ce_result.error_code,
                            "prior_failure_class": self._ce_schema_repair_failure_class(ce_result),
                        }
                        self._attach_ce_llm_evidence(repair_signal, initial_evidence)
                        stage_signals.append(repair_signal)
                        try:
                            self._settle_chief_engineer_attempt_before_schema_repair(lease_scope=lease_scope)
                        except (OSError, RuntimeError, TypeError, ValueError) as exc:
                            stage_signals.append(
                                {
                                    "code": "chief_engineer.output_schema_repair_settlement_failed",
                                    "severity": "error",
                                    "detail": f"{type(exc).__name__}: {exc}",
                                    "task_id": portfolio_task_id,
                                }
                            )
                            ce_result = None
                        else:
                            deadline_decision = self._chief_engineer_deadline_projection_decision(
                                context,
                                requested_timeout_seconds=requested_timeout_seconds,
                                dependency_schedule=dependency_schedule,
                                output_tokens=_CHIEF_ENGINEER_SCHEMA_REPAIR_MAX_TOKENS,
                            )
                            if deadline_decision.disposition is FactoryDeadlineDispositionV1.BLOCK:
                                stage_signals.append(
                                    {
                                        "code": "chief_engineer.output_schema_repair_deadline_blocked",
                                        "severity": "error",
                                        "detail": (
                                            "The CE schema repair was not admitted because the remaining Factory "
                                            "lease cannot preserve mandatory downstream budgets."
                                        ),
                                        "task_id": portfolio_task_id,
                                        "deadline_decision": deadline_decision.to_dict(),
                                        "reason": deadline_decision.reason,
                                    }
                                )
                                ce_result = None
                            else:
                                ce_result = await self._run_chief_engineer_schema_repair(
                                    run=run,
                                    authority_port=authority_port,
                                    authority_binding=authority_binding,
                                    prior_result=ce_result,
                                    portfolio_context=portfolio_context,
                                    portfolio_task_ids=tuple(task.task_id for task in portfolio_tasks),
                                    portfolio_tasks=portfolio_tasks,
                                    deadline_decision=deadline_decision,
                                    prompt_profile_identity=primary_prompt_profile_identity,
                                )
                                self._append_chief_engineer_structural_recovery_signal(
                                    result=ce_result,
                                    stage_signals=stage_signals,
                                    task_id=f"{portfolio_task_id}-SCHEMA-REPAIR",
                                )
                                llm_call_count = 2
                    if ce_result is not None and ce_result.ok and llm_call_count == 1:
                        primary_structured_output = dict(ce_result.metadata or {}).get("structured_output")
                        if isinstance(primary_structured_output, Mapping):
                            primary_output_errors = self._chief_engineer_portfolio_output_errors(
                                primary_structured_output,
                                tasks=portfolio_tasks,
                            )
                            if primary_output_errors:
                                assert portfolio_authority is not None
                                task_ids = tuple(task.task_id for task in portfolio_tasks)
                                semantic_repair_candidate = ChiefEngineerSemanticRepairCandidateV1(
                                    workspace=str(self.workspace),
                                    project_id=portfolio_authority.project_id,
                                    run_id=run.id,
                                    pm_contract_hash=portfolio_authority.pm_contract_hash,
                                    task_ids=task_ids,
                                    task_set_hash=chief_engineer_semantic_repair_task_set_hash(task_ids),
                                    candidate=dict(primary_structured_output),
                                )
                                candidate_ref = persist_chief_engineer_semantic_repair_candidate(
                                    semantic_repair_candidate
                                )
                                semantic_repair_diagnosis = self._chief_engineer_semantic_repair_diagnosis(
                                    candidate=semantic_repair_candidate,
                                    output_errors=primary_output_errors,
                                )
                                previous_repair_diagnostic = "|".join(semantic_repair_diagnosis.diagnostic_codes)
                                primary_evidence = self._ce_extract_llm_evidence(
                                    ce_result,
                                    task_id=portfolio_task_id,
                                    run_id=run.id,
                                )
                                repair_signal = {
                                    "code": "chief_engineer.output_contract_repair_started",
                                    "severity": "warning",
                                    "detail": "; ".join(primary_output_errors),
                                    "task_id": portfolio_task_id,
                                    "repair_task_id": f"{portfolio_task_id}-SCHEMA-REPAIR",
                                    "prior_error_code": "output_validation_failed",
                                    "prior_failure_class": "output_validation_failed",
                                    "validation_errors": list(primary_output_errors),
                                    "diagnostic_codes": list(semantic_repair_diagnosis.diagnostic_codes),
                                    "allowed_operations": list(semantic_repair_diagnosis.allowed_operations),
                                    "candidate_ref": candidate_ref,
                                    "candidate_hash": semantic_repair_candidate.candidate_hash,
                                    "diagnosis_hash": semantic_repair_diagnosis.diagnosis_hash,
                                    "pm_authority_preserved": True,
                                    "provider_calls_capped": 2,
                                }
                                self._attach_ce_llm_evidence(repair_signal, primary_evidence)
                                stage_signals.append(repair_signal)
                                semantic_failure = self._chief_engineer_post_validation_repair_result(
                                    prior_result=ce_result,
                                    output_errors=primary_output_errors,
                                )
                                self._settle_chief_engineer_attempt_before_schema_repair(lease_scope=lease_scope)
                                semantic_deadline_decision = self._chief_engineer_deadline_projection_decision(
                                    context,
                                    requested_timeout_seconds=requested_timeout_seconds,
                                    dependency_schedule=dependency_schedule,
                                    output_tokens=_CHIEF_ENGINEER_SCHEMA_REPAIR_MAX_TOKENS,
                                )
                                if semantic_deadline_decision.disposition is FactoryDeadlineDispositionV1.BLOCK:
                                    stage_signals.append(
                                        {
                                            "code": "chief_engineer.output_contract_repair_deadline_blocked",
                                            "severity": "error",
                                            "detail": (
                                                "The CE output-contract repair was not admitted because the "
                                                "remaining Factory lease cannot preserve mandatory downstream budgets."
                                            ),
                                            "task_id": portfolio_task_id,
                                            "deadline_decision": semantic_deadline_decision.to_dict(),
                                            "reason": semantic_deadline_decision.reason,
                                        }
                                    )
                                    ce_result = semantic_failure
                                else:
                                    repair_result = await self._run_chief_engineer_schema_repair(
                                        run=run,
                                        authority_port=authority_port,
                                        authority_binding=authority_binding,
                                        prior_result=semantic_failure,
                                        portfolio_context=portfolio_context,
                                        portfolio_task_ids=tuple(task.task_id for task in portfolio_tasks),
                                        portfolio_tasks=portfolio_tasks,
                                        deadline_decision=semantic_deadline_decision,
                                        semantic_candidate=semantic_repair_candidate,
                                        semantic_diagnosis=semantic_repair_diagnosis,
                                        prompt_profile_identity=primary_prompt_profile_identity,
                                    )
                                    ce_result = repair_result
                                    if (
                                        repair_result.error_code
                                        == "chief_engineer.semantic_repair_authority_infeasible"
                                    ):
                                        if stage_signals and stage_signals[-1] is repair_signal:
                                            stage_signals.pop()
                                    else:
                                        ce_result = self._compose_chief_engineer_semantic_repair_result(
                                            result=repair_result,
                                            candidate=semantic_repair_candidate,
                                            diagnosis=semantic_repair_diagnosis,
                                            tasks=portfolio_tasks,
                                        )
                                        llm_call_count = 2
                    # One repair can still leave either a protocol/schema error
                    # (for example, omit project_completion_contract) or a
                    # schema-valid immutable owner-contract deficit. Give both
                    # cases one final same-role, same-run repair carrying the
                    # exact new diagnostic. This remains bounded to three total
                    # physical calls, uses a distinct TaskRuntime claim, and
                    # preserves downstream Director/QA deadline reserves.
                    if ce_result is not None and llm_call_count == 2:
                        repaired_output_errors: list[str] = []
                        ce_result_metadata = dict(ce_result.metadata or {})
                        composed_semantic_candidate = bool(
                            ce_result_metadata.get("chief_engineer_semantic_repair_candidate_hash")
                        )
                        if ce_result.ok or composed_semantic_candidate:
                            repaired_structured_output = ce_result_metadata.get("structured_output")
                            if isinstance(repaired_structured_output, Mapping):
                                if ce_result.ok:
                                    repaired_output_errors = self._chief_engineer_portfolio_output_errors(
                                        repaired_structured_output,
                                        tasks=portfolio_tasks,
                                    )
                                else:
                                    repaired_output_errors = [
                                        str(item).strip()
                                        for item in ce_result_metadata.get(
                                            "chief_engineer_post_validation_errors",
                                            (),
                                        )
                                        if str(item).strip()
                                    ]
                                # Every schema-valid repair result becomes the exact base for the
                                # next typed patch.  Do not retain the first transaction after a
                                # patch has changed the candidate: its CAS hash, diagnosis and
                                # allowed operations describe the *previous* residual.  Exact
                                # L3-24 r31 fixed an invalid help entrypoint in repair 1, then
                                # revalidation exposed only a production-depth deficit; retaining
                                # the entrypoint diagnosis made repair 2 regress the fixed command
                                # and prevented the required artifact upsert.
                                if repaired_output_errors:
                                    assert portfolio_authority is not None
                                    task_ids = tuple(task.task_id for task in portfolio_tasks)
                                    prior_candidate_hash = (
                                        semantic_repair_candidate.candidate_hash
                                        if semantic_repair_candidate is not None
                                        else ""
                                    )
                                    semantic_repair_candidate = ChiefEngineerSemanticRepairCandidateV1(
                                        workspace=str(self.workspace),
                                        project_id=portfolio_authority.project_id,
                                        run_id=run.id,
                                        pm_contract_hash=portfolio_authority.pm_contract_hash,
                                        task_ids=task_ids,
                                        task_set_hash=chief_engineer_semantic_repair_task_set_hash(task_ids),
                                        candidate=dict(repaired_structured_output),
                                    )
                                    candidate_ref = persist_chief_engineer_semantic_repair_candidate(
                                        semantic_repair_candidate
                                    )
                                    semantic_repair_diagnosis = self._chief_engineer_semantic_repair_diagnosis(
                                        candidate=semantic_repair_candidate,
                                        output_errors=repaired_output_errors,
                                    )
                                    stage_signals.append(
                                        {
                                            "code": (
                                                "chief_engineer.semantic_repair_transaction_refreshed"
                                                if prior_candidate_hash
                                                else "chief_engineer.schema_repair_candidate_frozen"
                                            ),
                                            "severity": "info",
                                            "detail": (
                                                "The current schema-valid CE repair result remains semantically "
                                                "incomplete; its exact candidate and residual diagnosis now form "
                                                "the next typed patch transaction."
                                            ),
                                            "task_id": portfolio_task_id,
                                            "candidate_ref": candidate_ref,
                                            "prior_candidate_hash": prior_candidate_hash,
                                            "candidate_hash": semantic_repair_candidate.candidate_hash,
                                            "diagnosis_hash": semantic_repair_diagnosis.diagnosis_hash,
                                            "diagnostic_codes": list(semantic_repair_diagnosis.diagnostic_codes),
                                            "allowed_operations": list(semantic_repair_diagnosis.allowed_operations),
                                        }
                                    )
                        elif self._ce_portfolio_result_allows_schema_repair(ce_result):
                            repaired_error = str(
                                ce_result.error_message or ce_result.error_code or "output validation failed"
                            ).strip()
                            if repaired_error:
                                repaired_output_errors = [repaired_error]
                        final_repair_diagnostic = "; ".join(repaired_output_errors).strip()
                        if semantic_repair_diagnosis is not None:
                            final_repair_diagnostic = "|".join(semantic_repair_diagnosis.diagnostic_codes)
                        final_repair_has_progress = ce_result.ok or (
                            semantic_repair_candidate is not None
                            or final_repair_diagnostic != previous_repair_diagnostic
                        )
                        if repaired_output_errors and final_repair_has_progress:
                            repaired_evidence = self._ce_extract_llm_evidence(
                                ce_result,
                                task_id=portfolio_task_id,
                                run_id=run.id,
                            )
                            final_repair_signal: dict[str, Any] = {
                                "code": "chief_engineer.output_contract_final_repair_started",
                                "severity": "warning",
                                "detail": "; ".join(repaired_output_errors),
                                "task_id": portfolio_task_id,
                                "repair_task_id": f"{portfolio_task_id}-CONTRACT-REPAIR-2",
                                "prior_error_code": str(ce_result.error_code or "output_validation_failed"),
                                "prior_failure_class": self._ce_schema_repair_failure_class(ce_result),
                                "validation_errors": list(repaired_output_errors),
                                "diagnostic_codes": (
                                    list(semantic_repair_diagnosis.diagnostic_codes)
                                    if semantic_repair_diagnosis is not None
                                    else []
                                ),
                                "candidate_hash": (
                                    semantic_repair_candidate.candidate_hash
                                    if semantic_repair_candidate is not None
                                    else ""
                                ),
                                "diagnosis_hash": (
                                    semantic_repair_diagnosis.diagnosis_hash
                                    if semantic_repair_diagnosis is not None
                                    else ""
                                ),
                                "pm_authority_preserved": True,
                                "provider_calls_capped": 3,
                            }
                            self._attach_ce_llm_evidence(final_repair_signal, repaired_evidence)
                            stage_signals.append(final_repair_signal)
                            final_semantic_failure = (
                                self._chief_engineer_post_validation_repair_result(
                                    prior_result=ce_result,
                                    output_errors=repaired_output_errors,
                                )
                                if ce_result.ok
                                else ce_result
                            )
                            final_repair_output_tokens = self._chief_engineer_schema_repair_output_tokens(
                                task_count=len(portfolio_tasks),
                                repair_round=2,
                                semantic_patch=semantic_repair_candidate is not None,
                            )
                            final_semantic_deadline = self._chief_engineer_deadline_projection_decision(
                                context,
                                requested_timeout_seconds=requested_timeout_seconds,
                                dependency_schedule=dependency_schedule,
                                output_tokens=final_repair_output_tokens,
                            )
                            if final_semantic_deadline.disposition is FactoryDeadlineDispositionV1.BLOCK:
                                stage_signals.append(
                                    {
                                        "code": "chief_engineer.output_contract_final_repair_deadline_blocked",
                                        "severity": "error",
                                        "detail": (
                                            "The final CE owner-contract repair was not admitted because the "
                                            "remaining Factory lease cannot preserve mandatory downstream budgets."
                                        ),
                                        "task_id": portfolio_task_id,
                                        "deadline_decision": final_semantic_deadline.to_dict(),
                                        "reason": final_semantic_deadline.reason,
                                    }
                                )
                                ce_result = final_semantic_failure
                            else:
                                repair_result = await self._run_chief_engineer_schema_repair(
                                    run=run,
                                    authority_port=authority_port,
                                    authority_binding=authority_binding,
                                    prior_result=final_semantic_failure,
                                    portfolio_context=portfolio_context,
                                    portfolio_task_ids=tuple(task.task_id for task in portfolio_tasks),
                                    portfolio_tasks=portfolio_tasks,
                                    deadline_decision=final_semantic_deadline,
                                    repair_round=2,
                                    semantic_candidate=semantic_repair_candidate,
                                    semantic_diagnosis=semantic_repair_diagnosis,
                                    prompt_profile_identity=primary_prompt_profile_identity,
                                )
                                ce_result = repair_result
                                if repair_result.error_code == "chief_engineer.semantic_repair_authority_infeasible":
                                    if stage_signals and stage_signals[-1] is final_repair_signal:
                                        stage_signals.pop()
                                else:
                                    if semantic_repair_candidate is not None and semantic_repair_diagnosis is not None:
                                        ce_result = self._compose_chief_engineer_semantic_repair_result(
                                            result=repair_result,
                                            candidate=semantic_repair_candidate,
                                            diagnosis=semantic_repair_diagnosis,
                                            tasks=portfolio_tasks,
                                        )
                                    llm_call_count = 3

                    # If both earlier calls were consumed by transport/schema
                    # reconstruction, the final full-contract result can be the
                    # first schema-valid candidate and therefore the first point
                    # where semantic validation is possible. Do not strand that
                    # candidate or restart PM/CE: freeze it and allow exactly one
                    # typed, same-role semantic patch under a fresh deadline/lease.
                    if ce_result is not None and ce_result.ok and llm_call_count == 3:
                        final_structured_output = dict(ce_result.metadata or {}).get("structured_output")
                        if isinstance(final_structured_output, Mapping):
                            final_output_errors = self._chief_engineer_portfolio_output_errors(
                                final_structured_output,
                                tasks=portfolio_tasks,
                            )
                            if final_output_errors:
                                assert portfolio_authority is not None
                                task_ids = tuple(task.task_id for task in portfolio_tasks)
                                final_candidate = ChiefEngineerSemanticRepairCandidateV1(
                                    workspace=str(self.workspace),
                                    project_id=portfolio_authority.project_id,
                                    run_id=run.id,
                                    pm_contract_hash=portfolio_authority.pm_contract_hash,
                                    task_ids=task_ids,
                                    task_set_hash=chief_engineer_semantic_repair_task_set_hash(task_ids),
                                    candidate=dict(final_structured_output),
                                )
                                final_candidate_ref = persist_chief_engineer_semantic_repair_candidate(final_candidate)
                                final_diagnosis = self._chief_engineer_semantic_repair_diagnosis(
                                    candidate=final_candidate,
                                    output_errors=final_output_errors,
                                )
                                final_signal: dict[str, Any] = {
                                    "code": "chief_engineer.final_schema_candidate_semantic_repair_started",
                                    "severity": "warning",
                                    "detail": "; ".join(final_output_errors),
                                    "task_id": portfolio_task_id,
                                    "repair_task_id": f"{portfolio_task_id}-SEMANTIC-PATCH-REPAIR-3",
                                    "validation_errors": list(final_output_errors),
                                    "diagnostic_codes": list(final_diagnosis.diagnostic_codes),
                                    "allowed_operations": list(final_diagnosis.allowed_operations),
                                    "candidate_ref": final_candidate_ref,
                                    "candidate_hash": final_candidate.candidate_hash,
                                    "diagnosis_hash": final_diagnosis.diagnosis_hash,
                                    "pm_authority_preserved": True,
                                    "provider_calls_capped": 4,
                                }
                                self._attach_ce_llm_evidence(
                                    final_signal,
                                    self._ce_extract_llm_evidence(
                                        ce_result,
                                        task_id=portfolio_task_id,
                                        run_id=run.id,
                                    ),
                                )
                                stage_signals.append(final_signal)
                                final_semantic_failure = self._chief_engineer_post_validation_repair_result(
                                    prior_result=ce_result,
                                    output_errors=final_output_errors,
                                )
                                final_semantic_deadline = self._chief_engineer_deadline_projection_decision(
                                    context,
                                    requested_timeout_seconds=requested_timeout_seconds,
                                    dependency_schedule=dependency_schedule,
                                    output_tokens=_CHIEF_ENGINEER_SCHEMA_REPAIR_MAX_TOKENS,
                                )
                                if final_semantic_deadline.disposition is FactoryDeadlineDispositionV1.BLOCK:
                                    stage_signals.append(
                                        {
                                            "code": "chief_engineer.final_schema_candidate_semantic_repair_deadline_blocked",
                                            "severity": "error",
                                            "detail": (
                                                "The typed semantic patch for the final schema-valid CE candidate "
                                                "was not admitted because downstream budgets could not be preserved."
                                            ),
                                            "task_id": portfolio_task_id,
                                            "deadline_decision": final_semantic_deadline.to_dict(),
                                            "reason": final_semantic_deadline.reason,
                                        }
                                    )
                                    ce_result = final_semantic_failure
                                else:
                                    final_patch_result = await self._run_chief_engineer_schema_repair(
                                        run=run,
                                        authority_port=authority_port,
                                        authority_binding=authority_binding,
                                        prior_result=final_semantic_failure,
                                        portfolio_context=portfolio_context,
                                        portfolio_task_ids=task_ids,
                                        portfolio_tasks=portfolio_tasks,
                                        deadline_decision=final_semantic_deadline,
                                        repair_round=3,
                                        semantic_candidate=final_candidate,
                                        semantic_diagnosis=final_diagnosis,
                                        prompt_profile_identity=primary_prompt_profile_identity,
                                    )
                                    ce_result = final_patch_result
                                    if (
                                        final_patch_result.error_code
                                        != "chief_engineer.semantic_repair_authority_infeasible"
                                    ):
                                        ce_result = self._compose_chief_engineer_semantic_repair_result(
                                            result=final_patch_result,
                                            candidate=final_candidate,
                                            diagnosis=final_diagnosis,
                                            tasks=portfolio_tasks,
                                        )
                                    llm_call_count = 4
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 — contain provider/http failures as stage signals
                    # Provider/network failures (e.g. aiohttp.ClientResponseError on
                    # HTTP 403 quota) must become stage signals, not uncaught escapes
                    # that strand the Factory run before execute_stage can finish.
                    stage_signals.append(
                        {
                            "code": "chief_engineer.llm_review_failed",
                            "severity": "error",
                            "detail": f"{type(exc).__name__}: {exc}",
                            "task_id": portfolio_task_id,
                            "exception_type": type(exc).__name__,
                        }
                    )
                    ce_result = None

        ce_llm_blueprint: dict[str, Any] = {}
        if ce_result is not None:
            portfolio_task_id = f"CE-PORTFOLIO-{run.id}"
            ce_evidence = self._ce_extract_llm_evidence(
                ce_result,
                task_id=portfolio_task_id,
                run_id=run.id,
            )
            ce_provider = str(ce_evidence.get("provider") or "unknown")
            ce_model = str(ce_evidence.get("model") or "unknown")
            raw_output = str(ce_result.output or "")
            ce_result_metadata = dict(ce_result.metadata or {})

            if not ce_result.ok:
                missing_final_request_evidence = self._ce_missing_final_request_evidence(ce_evidence)
                advisory_projection_allowed = (
                    self._ce_portfolio_result_allows_schema_repair(ce_result)
                    and not ce_evidence.get("provider_model_unknown")
                    and not missing_final_request_evidence
                    and portfolio_authority is not None
                    and semantic_repair_candidate is None
                )
                if advisory_projection_allowed:
                    assert portfolio_authority is not None
                    fallback_candidate = self._chief_engineer_authoritative_pm_projection_candidate()
                    try:
                        portfolio = self._build_chief_engineer_portfolio_from_candidate(
                            run_id=run.id,
                            tasks=portfolio_tasks,
                            authority=portfolio_authority,
                            structured_output=fallback_candidate,
                        )
                    except (OSError, RuntimeError, TypeError, ValueError) as fallback_exc:
                        fallback_code = str(getattr(fallback_exc, "code", "") or "").strip()
                        fallback_details = getattr(fallback_exc, "details", None)
                        rejected_signal: dict[str, Any] = {
                            "code": "chief_engineer.advisory_projection_fallback_infeasible",
                            "severity": "error",
                            "detail": (
                                "The minimal PM-authority advisory projection cannot satisfy the immutable "
                                "project completion contract; preserve the physical Provider failure instead "
                                "of projecting misleading empty delivery authority."
                            ),
                            "task_id": portfolio_task_id,
                            "provider": ce_provider,
                            "model": ce_model,
                            "failure_class": self._ce_schema_repair_failure_class(ce_result),
                            "provider_error": str(ce_result.error_message or ce_result.error_code or "")[:512],
                            "fallback_error_code": fallback_code,
                            "fallback_validation_errors": [f"{type(fallback_exc).__name__}: {fallback_exc}"],
                            "pm_authority_preserved": True,
                            "scope_expansion_allowed": False,
                            "provider_calls_capped": llm_call_count,
                        }
                        if isinstance(fallback_details, Mapping) and fallback_details:
                            rejected_signal["fallback_contract_error_details"] = dict(fallback_details)
                        self._attach_ce_llm_evidence(rejected_signal, ce_evidence)
                        stage_signals.append(rejected_signal)
                    else:
                        fallback_signal: dict[str, Any] = {
                            "code": "chief_engineer.advisory_projection_fallback",
                            "severity": "warning",
                            "detail": (
                                "Primary plus bounded repair did not produce a valid CE result-protocol payload; "
                                "continued with the owner-finalized PM projection after its immutable delivery "
                                "contract passed validation."
                            ),
                            "task_id": portfolio_task_id,
                            "provider": ce_provider,
                            "model": ce_model,
                            "failure_class": self._ce_schema_repair_failure_class(ce_result),
                            "provider_error": str(ce_result.error_message or ce_result.error_code or "")[:512],
                            "pm_authority_preserved": True,
                            "scope_expansion_allowed": False,
                            "provider_calls_capped": llm_call_count,
                        }
                        self._attach_ce_llm_evidence(fallback_signal, ce_evidence)
                        stage_signals.append(fallback_signal)
                else:
                    authority_infeasible = ce_result.error_code == "chief_engineer.semantic_repair_authority_infeasible"
                    semantic_repair_exhausted = semantic_repair_candidate is not None and not authority_infeasible
                    error_signal: dict[str, Any] = {
                        "code": (
                            "chief_engineer.semantic_repair_authority_infeasible"
                            if authority_infeasible
                            else (
                                "chief_engineer.semantic_repair_exhausted"
                                if semantic_repair_exhausted
                                else "chief_engineer.llm_review_failed"
                            )
                        ),
                        "severity": "error",
                        "detail": ce_result.error_message or ce_result.error_code or "CE portfolio LLM call failed",
                        "task_id": portfolio_task_id,
                        "provider": ce_provider,
                        "model": ce_model,
                        "recoverable": False,
                    }
                    if semantic_repair_candidate is not None and semantic_repair_diagnosis is not None:
                        error_signal.update(
                            {
                                "candidate_hash": semantic_repair_candidate.candidate_hash,
                                "diagnosis_hash": semantic_repair_diagnosis.diagnosis_hash,
                                "diagnostic_codes": list(semantic_repair_diagnosis.diagnostic_codes),
                                "provider_calls_capped": llm_call_count,
                            }
                        )
                    self._attach_ce_llm_evidence(error_signal, ce_evidence)
                    stage_signals.append(error_signal)
            elif ce_evidence.get("provider_model_unknown"):
                stage_signals.append(
                    {
                        "code": "chief_engineer.llm_evidence_missing",
                        "severity": "error",
                        "detail": str(ce_evidence.get("provider_model_unknown_reason") or ""),
                        "task_id": portfolio_task_id,
                        "provider": ce_provider,
                        "model": ce_model,
                        "provider_model_unknown": True,
                    }
                )
            else:
                audit_payload: dict[str, Any] = {
                    "provider": ce_provider,
                    "model": ce_model,
                    "cache_hit": bool(ce_evidence.get("cache_hit")),
                    "task_id": portfolio_task_id,
                    "run_id": run.id,
                    "portfolio_task_ids": [task.task_id for task in portfolio_tasks],
                }
                self._attach_ce_llm_evidence(audit_payload, ce_evidence)
                self._emit_audit_event("chief_engineer.llm_call", **audit_payload)
                missing_final_request_evidence = self._ce_missing_final_request_evidence(ce_evidence)
                if missing_final_request_evidence:
                    missing_signal: dict[str, Any] = {
                        "code": "chief_engineer.final_request_audit_missing",
                        "severity": "error",
                        "detail": (
                            "CE LLM result did not expose required final provider-request evidence: "
                            + ", ".join(missing_final_request_evidence)
                        ),
                        "task_id": portfolio_task_id,
                        "provider": ce_provider,
                        "model": ce_model,
                        "missing": missing_final_request_evidence,
                    }
                    self._attach_ce_llm_evidence(missing_signal, ce_evidence)
                    stage_signals.append(missing_signal)

            call_error_count = sum(
                1 for signal in stage_signals if str(signal.get("severity") or "").strip().lower() == "error"
            )
            if not ce_llm_blueprint and portfolio is None:
                structured_output = ce_result_metadata.get("structured_output")
                if isinstance(structured_output, Mapping):
                    ce_llm_blueprint = dict(structured_output)
                elif "<SESSION_PATCH" in raw_output or "</SESSION_PATCH>" in raw_output:
                    stage_signals.append(
                        {
                            "code": "chief_engineer.session_patch_output_rejected",
                            "severity": "error",
                            "detail": "CE returned SESSION_PATCH content instead of the required portfolio JSON object",
                            "task_id": portfolio_task_id,
                            "provider": ce_provider,
                            "model": ce_model,
                        }
                    )
                elif call_error_count == 0:
                    quality_result = QualityChecker(str(self.workspace)).validate_output(
                        raw_output,
                        cast(Any, SimpleNamespace(role_id="chief_engineer")),
                    )
                    if not quality_result.success:
                        stage_signals.append(
                            {
                                "code": "chief_engineer.output_schema_invalid",
                                "severity": "error",
                                "detail": "; ".join(str(item) for item in quality_result.errors)
                                or "CE portfolio output failed schema validation",
                                "task_id": portfolio_task_id,
                                "provider": ce_provider,
                                "model": ce_model,
                                "quality_score": float(quality_result.quality_score),
                                "suggestions": list(quality_result.suggestions),
                            }
                        )
                    elif isinstance(quality_result.data, Mapping):
                        ce_llm_blueprint = dict(quality_result.data)

            if ce_llm_blueprint and call_error_count == 0:
                construction_plan = ce_llm_blueprint.get("construction_plan")
                task_plans = construction_plan.get("task_plans") if isinstance(construction_plan, Mapping) else None
                declared_task_ids = (
                    {str(task_id).strip() for task_id in task_plans} if isinstance(task_plans, Mapping) else set()
                )
                missing_task_ids = sorted({task.task_id for task in portfolio_tasks} - declared_task_ids)
                if missing_task_ids:
                    stage_signals.append(
                        {
                            "code": "chief_engineer.task_plan_overlay_defaulted",
                            "severity": "warning",
                            "detail": (
                                "CE omitted advisory task-local plans; the Chief Engineer owner "
                                "projected exact PM task boundaries instead of spending another "
                                "Provider repair call."
                            ),
                            "task_id": portfolio_task_id,
                            "missing_task_ids": missing_task_ids,
                            "provider": ce_provider,
                            "model": ce_model,
                            "pm_authority_preserved": True,
                            "provider_calls_saved": 1,
                        }
                    )
                if "scope_for_apply" not in ce_llm_blueprint:
                    omission_signal: dict[str, Any] = {
                        "code": "chief_engineer.scope_advisory_omitted",
                        "severity": "warning",
                        "detail": (
                            "CE omitted non-authoritative scope_for_apply advice; "
                            "PM target_files and scope_paths remain the apply authority."
                        ),
                        "task_id": portfolio_task_id,
                        "provider": ce_provider,
                        "model": ce_model,
                        "pm_authority_preserved": True,
                        "scope_expansion_allowed": False,
                    }
                    self._attach_ce_llm_evidence(omission_signal, ce_evidence)
                    stage_signals.append(omission_signal)
                output_errors = self._chief_engineer_portfolio_output_errors(
                    ce_llm_blueprint,
                    tasks=portfolio_tasks,
                )
                if output_errors:
                    stage_signals.append(
                        {
                            "code": "chief_engineer.portfolio_output_invalid",
                            "severity": "error",
                            "detail": "; ".join(output_errors),
                            "task_id": portfolio_task_id,
                            "provider": ce_provider,
                            "model": ce_model,
                            "errors": output_errors,
                        }
                    )
                else:
                    assert portfolio_authority is not None
                    task_ids = tuple(task.task_id for task in portfolio_tasks)
                    valid_candidate = ChiefEngineerSemanticRepairCandidateV1(
                        workspace=str(self.workspace),
                        project_id=portfolio_authority.project_id,
                        run_id=run.id,
                        pm_contract_hash=portfolio_authority.pm_contract_hash,
                        task_ids=task_ids,
                        task_set_hash=chief_engineer_semantic_repair_task_set_hash(task_ids),
                        candidate=ce_llm_blueprint,
                    )
                    candidate_ref = persist_chief_engineer_semantic_repair_candidate(valid_candidate)
                    stage_signals.append(
                        {
                            "code": "chief_engineer.structured_candidate_persisted",
                            "severity": "info",
                            "detail": (
                                "Persisted schema-valid CE output before semantic owner projection for "
                                "same-run stage-local recovery."
                            ),
                            "candidate_ref": candidate_ref,
                            "candidate_hash": valid_candidate.candidate_hash,
                            "pm_contract_hash": portfolio_authority.pm_contract_hash,
                        }
                    )
            elif not ce_llm_blueprint and portfolio is None and call_error_count == 0:
                stage_signals.append(
                    {
                        "code": "chief_engineer.output_schema_invalid",
                        "severity": "error",
                        "detail": "CE portfolio output did not contain a JSON object",
                        "task_id": portfolio_task_id,
                        "provider": ce_provider,
                        "model": ce_model,
                    }
                )

        has_pre_projection_errors = any(
            str(signal.get("severity") or "").strip().lower() == "error" for signal in stage_signals
        )
        if (
            portfolio is None
            and portfolio_tasks
            and portfolio_authority is not None
            and ce_llm_blueprint
            and not has_pre_projection_errors
        ):
            try:
                portfolio = self._build_chief_engineer_portfolio_from_candidate(
                    run_id=run.id,
                    tasks=portfolio_tasks,
                    authority=portfolio_authority,
                    structured_output=ce_llm_blueprint,
                )
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                contract_error_code = str(getattr(exc, "code", "") or "").strip()
                signal: dict[str, Any] = {
                    "code": contract_error_code or "chief_engineer.portfolio_generation_failed",
                    "severity": "error",
                    "detail": f"{type(exc).__name__}: {exc}",
                }
                contract_error_details = getattr(exc, "details", None)
                if isinstance(contract_error_details, Mapping) and contract_error_details:
                    signal["contract_error_details"] = dict(contract_error_details)
                stage_signals.append(signal)

        if portfolio is not None:
            portfolio_reference = portfolio.to_reference()
            portfolio_context_evidence = portfolio.to_task_blueprint_context()
            for index, task in enumerate(pm_tasks, start=1):
                task_id = self._task_id(task, index)
                objective = self._task_objective(task)
                task_constraints = self._task_blueprint_constraints(task)
                task_context = self._task_blueprint_context(task, run_id=run.id, index=index)
                task_context.update(portfolio_context_evidence)
                task_context["chief_engineer_blueprint_portfolio"] = dict(portfolio_reference)
                if deadline_decision is not None:
                    task_context["chief_engineer_deadline_decision"] = deadline_decision.to_dict()
                try:
                    task_llm_blueprint = project_chief_engineer_task_blueprint(portfolio, task_id)
                    result = generate_task_blueprint(
                        GenerateTaskBlueprintCommandV1(
                            task_id=task_id,
                            workspace=str(self.workspace),
                            objective=objective,
                            run_id=run.id,
                            constraints=task_constraints,
                            context=task_context,
                            llm_blueprint=task_llm_blueprint,
                        )
                    )
                except (OSError, RuntimeError, TypeError, ValueError) as exc:
                    stage_signals.append(
                        {
                            "code": "chief_engineer.blueprint_generation_failed",
                            "severity": "error",
                            "detail": f"{type(exc).__name__}: {exc}",
                            "task_id": task_id,
                        }
                    )
                    continue

                if not result.ok or not result.blueprint_id or not result.blueprint_path:
                    stage_signals.append(
                        {
                            "code": "chief_engineer.blueprint_result_invalid",
                            "severity": "error",
                            "detail": result.summary or result.status,
                            "task_id": task_id,
                        }
                    )
                    continue

                repaired_missing_artifact = self._ensure_chief_engineer_blueprint_artifact_present(
                    result=result,
                    task=task,
                    task_context=task_context,
                    constraints=task_constraints,
                    run_id=run.id,
                )
                if repaired_missing_artifact:
                    stage_signals.append(
                        {
                            "code": "chief_engineer.blueprint_artifact_rewritten_from_result",
                            "severity": "warning",
                            "detail": (
                                "CE returned a valid blueprint result but the physical blueprint artifact was "
                                "missing; rewrote the handoff artifact from structured result fields."
                            ),
                            "task_id": task_id,
                            "blueprint_id": result.blueprint_id,
                            "blueprint_path": result.blueprint_path,
                        }
                    )

                handoff_validation = validate_director_handoff_from_payload(
                    str(self.workspace),
                    {"task_id": task_id, "blueprint_id": result.blueprint_id},
                    require_strict=True,
                )
                handoff_payload_raw = handoff_validation.get("decision_payload")
                handoff_payload: dict[str, Any] = handoff_payload_raw if isinstance(handoff_payload_raw, dict) else {}
                if not handoff_validation.get("allowed") and not handoff_payload:
                    stage_signals.append(
                        {
                            "code": "chief_engineer.handoff_decision_unreadable",
                            "severity": "error",
                            "detail": str(
                                handoff_validation.get("reason")
                                or "Generated CE blueprint could not be loaded for handoff validation."
                            ),
                            "task_id": task_id,
                            "blueprint_id": result.blueprint_id,
                            "handoff_validation": handoff_validation,
                        }
                    )
                elif not handoff_validation.get("allowed"):
                    stage_signals.append(
                        {
                            "code": "chief_engineer.handoff_blocked",
                            "severity": "error",
                            "detail": str(handoff_validation.get("reason") or "Chief Engineer handoff blocked."),
                            "task_id": task_id,
                            "blueprint_id": result.blueprint_id,
                            "blockers": list(handoff_payload.get("blockers") or []),
                            "handoff_decision": handoff_payload,
                            "handoff_validation": handoff_validation,
                        }
                    )

                row_evidence = {
                    **ce_evidence,
                    "portfolio_id": portfolio.portfolio_id,
                    "portfolio_path": portfolio.portfolio_path,
                    "portfolio_hash": portfolio.portfolio_hash,
                    "project_interface_contract_ref": portfolio.project_interface_contract_ref,
                    "project_interface_contract_hash": portfolio.project_interface_contract_hash,
                }
                blueprint_rows.append(
                    {
                        "task_id": result.task_id,
                        "status": result.status,
                        "blueprint_id": result.blueprint_id,
                        "blueprint_path": result.blueprint_path,
                        "summary": result.summary,
                        "recommendations": list(result.recommendations),
                        "risks": list(result.risks),
                        "handoff_ready": bool(handoff_validation.get("allowed")),
                        "handoff_decision": handoff_payload,
                        "llm_evidence": row_evidence,
                        "llm_blueprint_consumed": True,
                        "llm_blueprint_keys": sorted(task_llm_blueprint),
                        "portfolio_reference": dict(portfolio_reference),
                    }
                )

        review_artifact = ""
        if blueprint_rows or stage_signals or portfolio is not None:
            review_artifact = persist_chief_engineer_review_document(
                workspace=str(self.workspace),
                run_id=run.id,
                payload={
                    "schema_version": "factory.chief_engineer_review.v2",
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "source": "factory_stage_executor.chief_engineer_portfolio_review",
                    "factory_run_id": run.id,
                    "task_plan": "tasks/plan.json",
                    "total_tasks": len(pm_tasks),
                    "generated_blueprints": len(blueprint_rows),
                    "llm_call_count": llm_call_count,
                    "portfolio": portfolio.to_reference() if portfolio is not None else {},
                    "project_interface_contract": (
                        portfolio.project_interface_contract.to_reference() if portfolio is not None else {}
                    ),
                    "blueprints": blueprint_rows,
                    "signals": stage_signals,
                },
            )

        keeper_stop = lease_scope.stop_keeper()
        heartbeat_failure = keeper_stop.failure
        if not keeper_stop.thread_exited:
            stage_signals.append(
                {
                    "code": "chief_engineer.execution_attempt_keeper_stop_failed",
                    "severity": "error",
                    "detail": (
                        f"{heartbeat_failure.error_type}: {heartbeat_failure.error_message}"
                        if heartbeat_failure is not None
                        else "lease keeper did not confirm thread exit"
                    ),
                    "reason": heartbeat_failure.reason if heartbeat_failure is not None else "unknown",
                }
            )
        elif heartbeat_failure is not None:
            stage_signals.append(
                {
                    "code": "chief_engineer.execution_attempt_heartbeat_failed",
                    "severity": "error",
                    "detail": (f"{heartbeat_failure.error_type}: {heartbeat_failure.error_message}"),
                    "reason": heartbeat_failure.reason,
                    "task_id": (
                        lease_scope.execution_attempt.external_task_id
                        if lease_scope.execution_attempt is not None
                        else ""
                    ),
                    "session_id": (
                        lease_scope.execution_attempt.session_id if lease_scope.execution_attempt is not None else ""
                    ),
                }
            )

        has_errors = any(
            str(item.get("severity") or "").strip().lower() == "error"
            for item in stage_signals
            if isinstance(item, dict)
        )
        stage_status = "cancelled" if cancelled_by_factory else "failed" if has_errors else "success"
        error_code = ""
        root_cause_hint = ""
        failure_recoverable = True
        if has_errors:
            for signal in stage_signals:
                if str(signal.get("severity") or "").strip().lower() != "error":
                    continue
                error_code = str(signal.get("code") or "").strip()
                root_cause_hint = str(signal.get("detail") or "").strip()
                if isinstance(signal.get("recoverable"), bool):
                    failure_recoverable = bool(signal["recoverable"])
                if error_code:
                    break

        if ce_runtime_task_id is not None and ce_execution_attempt is not None:
            should_settle, heartbeat_failure = lease_scope.begin_settlement()
            if should_settle:
                settlement_attempt = lease_scope.execution_attempt
                if settlement_attempt is None:
                    raise RuntimeError("chief_engineer_execution_attempt_settlement_identity_missing")
                try:
                    self._settle_chief_engineer_execution_attempt(
                        task_id=ce_runtime_task_id,
                        execution_attempt=settlement_attempt,
                        stage_status=stage_status,
                        summary=error_code or "chief_engineer_portfolio_review_completed",
                    )
                except (OSError, RuntimeError, TypeError, ValueError) as exc:
                    stage_signals.append(
                        {
                            "code": "chief_engineer.execution_attempt_settlement_failed",
                            "severity": "error",
                            "detail": f"{type(exc).__name__}: {exc}",
                        }
                    )
                    stage_status = "failed"
                    error_code = "chief_engineer.execution_attempt_settlement_failed"
                    root_cause_hint = str(exc)
            elif heartbeat_failure is not None and not any(
                str(signal.get("code") or "") == "chief_engineer.execution_attempt_keeper_stop_failed"
                for signal in stage_signals
                if isinstance(signal, dict)
            ):
                stage_signals.append(
                    {
                        "code": "chief_engineer.execution_attempt_settlement_blocked",
                        "severity": "error",
                        "detail": f"{heartbeat_failure.error_type}: {heartbeat_failure.error_message}",
                        "reason": heartbeat_failure.reason,
                    }
                )
                stage_status = "failed"
                error_code = "chief_engineer.execution_attempt_settlement_blocked"
                root_cause_hint = heartbeat_failure.error_message

        stage_signal_path = ""
        if stage_signals:
            stage_signal_path = self._write_stage_signal_artifact(
                stage="chief_engineer_review",
                run_id=run.id,
                signals=stage_signals,
            )

        artifacts = [row["blueprint_path"] for row in blueprint_rows if row.get("blueprint_path")]
        if portfolio is not None:
            artifacts.append(portfolio.portfolio_path)
        if review_artifact:
            artifacts.append(review_artifact)
        self._mirror_chief_engineer_artifacts(run.id, blueprint_rows, review_artifact, artifacts)
        if stage_signal_path:
            artifacts.append(stage_signal_path)

        return StageResult(
            stage="chief_engineer_review",
            status=stage_status,
            output=(
                f"Chief Engineer portfolio review generated {len(blueprint_rows)}/{len(pm_tasks)} blueprints; "
                f"llm_calls={llm_call_count}; signals={len(stage_signals)}; "
                f"error_code={error_code or 'none'}; root_cause_hint={root_cause_hint or 'none'}"
            ),
            artifacts=artifacts,
            metadata={
                "error_code": error_code,
                "failure_class": (
                    "ROLE_LLM_REVIEW_FAILED"
                    if error_code == "chief_engineer.llm_review_failed"
                    else "CHIEF_ENGINEER_REVIEW_FAILED"
                    if stage_status == "failed"
                    else ""
                ),
                "responsible_layer": "chief_engineer" if stage_status == "failed" else "",
                "root_cause_hint": root_cause_hint,
                "recoverable": failure_recoverable if stage_status == "failed" else False,
            },
        )

    async def _execute_director_dispatch(self, run: FactoryRun, context: dict[str, Any]) -> StageResult:
        return await director_dispatch_impl._execute_director_dispatch(self, run, context)

    @staticmethod
    def _bool_from_context_or_env(
        context: dict[str, Any],
        *keys: str,
        env_var: str = "",
        default: bool = True,
    ) -> bool:
        return helpers.bool_from_context_or_env(context, *keys, env_var=env_var, default=default)

    def _load_package_scripts(self) -> dict[str, str]:
        return self._workspace_quality.load_package_scripts()

    def _workspace_quality_commands(self, context: dict[str, Any]) -> list[list[str]]:
        return self._workspace_quality.workspace_quality_commands(context)

    @staticmethod
    def _canonical_project_id(context: dict[str, Any]) -> str:
        return str(
            context.get("project_id")
            or context.get("requested_project_id")
            or context.get("factory_bench_project_id")
            or ""
        ).strip()

    def _canonical_factory_projection(
        self,
        run: FactoryRun,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Load the canonical Factory run-tree projection.

        The same-cell adapter owns workspace/factory/project scoping. Missing
        or malformed facts return an empty projection so all callers fail
        closed through the pure authority evaluator.
        """

        try:
            projection = load_run_ledger_projection(
                self.workspace,
                run_id=run.id,
                factory_run_id=run.id,
                project_id=self._canonical_project_id(context),
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning("Canonical Factory projection unavailable for run %s: %s", run.id, exc)
            return {}
        # Factory portfolios also create internal settlement / verification
        # TaskRuntime rows under the same factory_run_id. Director completion
        # authority owns only the immutable, committed PM contract tasks.
        # Mutable workspace mirrors are never completion authority.
        proof = context.get(PM_STAGE_ARTIFACT_BINDING_CONTEXT_KEY)
        contract_task_ids: list[str] = []
        if (
            isinstance(proof, RevalidatedPMStageArtifactBindingV1)
            and proof.binding.factory_run_id == run.id
            and proof.binding.stage == "pm_planning"
        ):
            contract_task_ids = [
                helpers._canonical_task_id_token(task_id)
                for task_id in proof.task_ids
                if helpers._canonical_task_id_token(task_id)
            ]
        expected_task_ids = set(contract_task_ids)

        def _latest_contract_rows(authority: Mapping[str, Any]) -> list[dict[str, Any]]:
            authority_rows = authority.get("rows")
            scoped_candidates = (
                [
                    dict(row)
                    for row in authority_rows
                    if isinstance(row, Mapping) and helpers._runtime_row_contract_task_id(row) in expected_task_ids
                ]
                if isinstance(authority_rows, list)
                else []
            )
            # Stage-local retries create fresh numeric TaskRuntime rows for the
            # same immutable PM task. Keep only the newest fact for each external
            # contract identity; old failed/removed attempts remain audit facts but
            # cannot collide with or override current completion authority.
            latest_scoped_rows: dict[str, dict[str, Any]] = {}
            for row in scoped_candidates:
                contract_task_id = helpers._runtime_row_contract_task_id(row)
                previous = latest_scoped_rows.get(contract_task_id)
                current_seq = row.get("fact_event_seq")
                previous_seq = previous.get("fact_event_seq") if previous is not None else None
                if previous is None or (
                    isinstance(current_seq, int) and (not isinstance(previous_seq, int) or current_seq > previous_seq)
                ):
                    latest_scoped_rows[contract_task_id] = row
            return [latest_scoped_rows[task_id] for task_id in contract_task_ids if task_id in latest_scoped_rows]

        try:
            task_runtime_projection = (
                pkg().TaskRuntimeService(str(self.workspace)).query_observable_task_rows_projection()
            )
            task_runtime_authority = task_runtime_projection.to_authority_dict(
                factory_run_id=run.id,
            )
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning(
                "Canonical live TaskRuntime projection unavailable for run %s: %s",
                run.id,
                exc,
            )
            task_runtime_authority = {}
        scoped_rows = _latest_contract_rows(task_runtime_authority)

        # Terminal Factory settlement deliberately drains TaskRuntime files and
        # leaves ``removed`` tombstones. QA retries still need the exact frozen
        # authority captured immediately before that destructive reset. This is
        # a read model of TaskRuntime's facts, not a second state owner. A
        # Director-stage retry invalidates the old epoch snapshot in
        # ``FactoryRunService.retry_run_from_stage`` before new execution.
        live_scope_is_current = (
            bool(expected_task_ids)
            and {helpers._runtime_row_contract_task_id(row) for row in scoped_rows} == expected_task_ids
            and all(
                str(row.get("execution_state") or row.get("status") or "").strip().lower() != "removed"
                for row in scoped_rows
            )
        )
        if not live_scope_is_current:
            from polaris.cells.factory.pipeline.public.contracts import (
                FACTORY_TERMINAL_TASK_RUNTIME_PROJECTION_METADATA_KEY,
                FactoryTerminalTaskRuntimeProjectionV1,
            )

            frozen_payload = run.metadata.get(
                FACTORY_TERMINAL_TASK_RUNTIME_PROJECTION_METADATA_KEY,
            )
            if isinstance(frozen_payload, Mapping):
                try:
                    frozen = FactoryTerminalTaskRuntimeProjectionV1.from_dict(
                        frozen_payload,
                    )
                except (OSError, TypeError, ValueError) as exc:
                    logger.warning(
                        "Canonical frozen TaskRuntime projection invalid for run %s: %s",
                        run.id,
                        exc,
                    )
                else:
                    if frozen.factory_run_id == run.id:
                        frozen_authority = deepcopy(dict(frozen.projection))
                        frozen_scoped_rows = _latest_contract_rows(frozen_authority)
                        frozen_scope_exact = (
                            bool(expected_task_ids)
                            and {helpers._runtime_row_contract_task_id(row) for row in frozen_scoped_rows}
                            == expected_task_ids
                            and all(
                                str(row.get("execution_state") or row.get("status") or "").strip().lower() != "removed"
                                for row in frozen_scoped_rows
                            )
                        )
                        if frozen_scope_exact:
                            task_runtime_authority = frozen_authority
                            scoped_rows = frozen_scoped_rows
                            task_runtime_authority["authority_epoch_source"] = (
                                "factory_terminal_task_runtime_projection"
                            )
        task_runtime_authority["rows"] = scoped_rows
        task_runtime_authority["row_count"] = len(scoped_rows)
        task_runtime_authority["owner_scope"] = (
            "pm_contract_tasks" if contract_task_ids else "pm_contract_binding_invalid"
        )
        # Keep duplicates so the pure evaluator can reject PM aliases such as
        # ``1`` plus ``TASK-1`` instead of silently collapsing obligations.
        task_runtime_authority["owned_task_ids"] = sorted(contract_task_ids)
        projection["task_runtime_projection"] = task_runtime_authority
        return projection

    def _workspace_quality_task_boundary_blocker(
        self,
        run: FactoryRun,
        context: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Block workspace validation until canonical task boundaries settle."""

        projection = self._canonical_factory_projection(run, context)
        authority = helpers.evaluate_canonical_factory_authority(projection)
        if authority.director_stage_authorized:
            return None
        # A same-task completion action deliberately reopens its exact Director
        # owner before QA runs the failed verifier and local repair.  If the
        # process dies between that durable reopen and the verifier call, the
        # next same-run QA retry observes a canonical ``pending`` row.  Treating
        # that prepared owner like an ordinary incomplete materialization makes
        # the recovery self-deadlock: workspace checks refuse to run, so the
        # owner can never be claimed, edited, revalidated, and settled.
        #
        # This is admission to the verifier/repair transaction only, not delivery
        # success.  Require an authoritative TaskRuntime row plus the full
        # same-run action receipt.  Unprepared pending/blocked/active tasks remain
        # fail-closed, and final quality authority still requires verifier PASS
        # followed by TaskRuntime reconciliation.
        if self._workspace_quality_prepared_local_rework_authorized(
            projection=projection,
            authority=authority,
            factory_run_id=run.id,
            director_dispatch_settled=self._workspace_quality_director_dispatch_settled(run),
        ):
            return None
        failure_class = authority.failure_class or (
            FailureClassV1.DEPENDENCY_NOT_UNLOCKED.value
            if not authority.task_boundary_present
            else FailureClassV1.INCOMPLETE_MATERIALIZATION.value
        )
        return {
            "schema_version": "factory.workspace_quality.task_boundary_blocker.v2",
            "reason_code": authority.reason_code,
            "failure_class": failure_class,
            "responsible_layer": authority.responsible_layer or "task_boundary",
            "task_count": authority.task_count,
            "incomplete_task_ids": list(authority.incomplete_task_ids),
            "detail": authority.detail,
            "authority_source": "run_ledger_projection",
        }

    @staticmethod
    def _workspace_quality_director_dispatch_settled(run: FactoryRun) -> bool:
        """True after Director first-wave materialization has a durable success mark.

        Same-run qa_gate retries keep ``last_successful_stage=director_dispatch``
        even when later quality residuals fail or a restart fence blocks one
        owner. That mark is admission to measure/repair, not delivery success.
        """

        last_successful = str(
            (run.metadata or {}).get("last_successful_stage") or getattr(run, "recovery_point", "") or ""
        ).strip()
        if last_successful in {"director_dispatch", "quality_gate"}:
            return True
        return "director_dispatch" in {
            str(stage or "").strip() for stage in (getattr(run, "stages_completed", None) or ())
        }

    @staticmethod
    def _workspace_quality_prepared_local_rework_authorized(
        *,
        projection: Mapping[str, Any],
        authority: helpers.CanonicalFactoryAuthority,
        factory_run_id: str,
        director_dispatch_settled: bool = False,
    ) -> bool:
        """Allow QA to resume a durably prepared same-task repair transaction."""

        if authority.reason_code != "task_runtime_not_converged":
            return False
        incomplete_ids = set(authority.incomplete_runtime_task_ids)
        if not incomplete_ids:
            return False

        task_runtime_raw = projection.get("task_runtime_projection")
        task_runtime = task_runtime_raw if isinstance(task_runtime_raw, Mapping) else {}
        rows_raw = task_runtime.get("rows")
        rows = [row for row in rows_raw if isinstance(row, Mapping)] if isinstance(rows_raw, list) else []
        rows_by_task: dict[str, Mapping[str, Any]] = {}
        for row in rows:
            task_id = helpers._runtime_row_contract_task_id(row)
            if not task_id or task_id in rows_by_task:
                return False
            rows_by_task[task_id] = row
        if not incomplete_ids.issubset(rows_by_task):
            return False

        lower_hex = frozenset("0123456789abcdef")

        def is_sha256(value: Any) -> bool:
            token = str(value or "").strip()
            return len(token) == 64 and all(char in lower_hex for char in token)

        for task_id in incomplete_ids:
            row = rows_by_task[task_id]
            state = str(row.get("execution_state") or row.get("status") or "").strip().lower()
            # Compact authority rows omit last_execution_error. A same-run
            # qa_gate retry therefore cannot classify ``workspace_quality_*``
            # residuals by error text. TaskBoundary completed_verified is the
            # independent delivery axis: failed/pending runtime history must
            # not skip the verifier once Director already sealed the owner.
            # ``claimed_by=director`` on pending/failed is a leftover last
            # claim, not an active in-progress lock.
            #
            # Live L2-15: after director_dispatch, quality settled owners as
            # failed ``workspace_quality_repair_*`` and a restart fence left
            # the remaining TU ``blocked``. Those residuals are the repair
            # surface, not incomplete first materialization. Skipping the
            # verifier here deadlocks ncmd=0. Pending/never-started rows stay
            # fail-closed unless they carry a prepared same-task receipt.
            if state in {"in_progress", "claimed"}:
                return False
            if state in {"pending", "ready"}:
                if _Mixin02._row_has_prepared_same_task_local_rework(
                    row,
                    factory_run_id=factory_run_id,
                    task_id=task_id,
                    is_sha256=is_sha256,
                ) or _Mixin02._task_boundary_completed_verified(projection, task_id):
                    continue
                return False
            if state == "failed" and (
                director_dispatch_settled or _Mixin02._task_boundary_completed_verified(projection, task_id)
            ):
                continue
            if state == "blocked" and director_dispatch_settled:
                continue
            return False
        return True

    @staticmethod
    def _task_boundary_completed_verified(projection: Mapping[str, Any], task_id: str) -> bool:
        """Return True when the owner TaskBoundary is completed_verified."""

        boundary_raw = projection.get("task_boundary")
        boundary = boundary_raw if isinstance(boundary_raw, Mapping) else {}
        latest_raw = boundary.get("latest_by_task")
        latest = latest_raw if isinstance(latest_raw, Mapping) else {}
        canonical = helpers._canonical_task_id_token(task_id)
        for key in (task_id, canonical, f"TASK-{canonical}" if canonical else ""):
            if not key:
                continue
            verdict = latest.get(key)
            if not isinstance(verdict, Mapping):
                continue
            status = str(verdict.get("status") or "").strip().lower()
            if bool(verdict.get("ok")) and status == "completed_verified":
                return True
        return False

    @staticmethod
    def _row_has_prepared_same_task_local_rework(
        row: Mapping[str, Any],
        *,
        factory_run_id: str,
        task_id: str,
        is_sha256: Callable[[Any], bool],
    ) -> bool:
        """Return True when the row carries a complete same-task rework receipt."""

        metadata_raw = row.get("metadata")
        metadata = metadata_raw if isinstance(metadata_raw, Mapping) else {}
        action_raw = metadata.get("factory_local_rework")
        action = action_raw if isinstance(action_raw, Mapping) else {}
        diagnostic_raw = action.get("diagnostic")
        diagnostic = diagnostic_raw if isinstance(diagnostic_raw, Mapping) else {}
        action_id = str(action.get("action_id") or "").strip()
        action_kind = str(action.get("action_kind") or "").strip()
        executable_actions = frozenset(
            {
                "publish_owner_rework",
                "refresh_owner_evidence",
                "run_deterministic_repair",
                "run_required_verifier",
            }
        )
        if (
            action.get("schema_version") != "task-runtime.same-task-local-rework-record/1"
            or str(action.get("factory_run_id") or "").strip() != factory_run_id
            or str(action.get("external_task_id") or "").strip() != task_id
            or action_kind not in executable_actions
            or str(diagnostic.get("owner_task_id") or "").strip() != task_id
            or str(diagnostic.get("allowed_next_action") or "").strip() != action_kind
            or not is_sha256(action_id)
            or not is_sha256(action.get("dispatch_claim_id"))
            or not is_sha256(action.get("effect_hash"))
        ):
            return False
        authorizations_raw = metadata.get("same_task_local_rework_authorizations")
        authorizations = authorizations_raw if isinstance(authorizations_raw, list) else []
        matching_authorizations = [
            record
            for record in authorizations
            if isinstance(record, Mapping)
            and str(record.get("action_id") or "").strip() == action_id
            and str(record.get("effect_hash") or "").strip() == str(action.get("effect_hash") or "").strip()
        ]
        return len(matching_authorizations) == 1

    @staticmethod
    def _trim_command_output(text: str, limit: int = _WORKSPACE_VALIDATION_OUTPUT_MAX_CHARS) -> str:
        return helpers.trim_command_output(text, limit)

    def _run_workspace_quality_command(self, command: list[str], timeout_seconds: float) -> dict[str, Any]:
        return self._workspace_quality.run_command(command, timeout_seconds)

    @staticmethod
    def _resolve_workspace_quality_command(command: list[str]) -> list[str]:
        return helpers.resolve_workspace_quality_command(command)

    def _workspace_quality_repair_errors(self, results: list[dict[str, Any]]) -> list[str]:
        return workspace_quality_impl._workspace_quality_repair_errors(self, results)

    @staticmethod
    def _workspace_quality_diagnostic_signature(errors: Iterable[str]) -> tuple[str, ...]:
        """Return a stable verifier-diagnostic signature for convergence checks.

        Repair success is owned by the post-repair verifier, not by a write-tool
        receipt.  Normalize whitespace/case so formatting jitter does not buy
        another Provider attempt, while preserving paths/codes/symbols needed to
        distinguish a real diagnostic change.
        """

        normalized = {" ".join(str(error or "").split()).casefold() for error in errors if str(error or "").strip()}
        return tuple(sorted(normalized))

    @staticmethod
    def _workspace_quality_diagnostic_error_codes(signature: Iterable[str]) -> set[str]:
        """Extract structured compiler error codes from a diagnostic signature.

        Only explicit code anchors count (rustc ``error[E0432]``, TypeScript
        ``TS2551``); plain prose diagnostics return an empty set so callers
        stay conservative. Codes are casefolded for comparison.
        """

        codes: set[str] = set()
        for item in signature:
            text = str(item or "")
            structured = {
                code
                for match in _COMPILER_ERROR_CODE_RES.finditer(text)
                if (code := _compiler_error_code_from_match(match))
            }
            if structured:
                codes.update(structured)
                continue
            # g++/clang do not emit rustc/tsc codes. Live L1-06 advanced from
            # "'MoonError' has not been declared" to "has no member named
            # last_error" with a real edit, but the one-blob signature kept
            # equal_count_swap and tripped the two-round breaker. Phrase
            # classes plus missing-member names are the C++ phase tokens.
            lowered = text.lower()
            for kind, hints in _CPP_DIAGNOSTIC_KIND_HINTS:
                if any(hint in lowered for hint in hints):
                    codes.add(kind)
            codes.update(f"cpp_member_{match.group('name').lower()}" for match in _CPP_MISSING_MEMBER_RE.finditer(text))
        return codes

    @staticmethod
    def _go_crash_unmasked_runnable_tests(
        before_signature: Iterable[str],
        after_signature: Iterable[str],
    ) -> bool:
        """True when a Go runtime crash became runnable test assertions.

        Live L2-13: editing engine/service.go cleared ``fatal error: stack
        overflow``. The next verifier surface was ``--- FAIL:`` test
        assertions (plus the same delivery-depth residual). That count
        increase is unmasking, not regression.
        """

        before_text = "\n".join(str(item or "") for item in before_signature).casefold()
        after_text = "\n".join(str(item or "") for item in after_signature)
        after_folded = after_text.casefold()
        crash_markers = ("fatal error: stack overflow", "goroutine stack exceeds", "fatal error:")
        if not any(marker in before_text for marker in crash_markers):
            return False
        if any(marker in after_folded for marker in crash_markers):
            return False
        return "--- fail:" in after_folded

    @staticmethod
    def _go_compile_barrier_unmasked_runnable_tests(
        before_signature: Iterable[str],
        after_signature: Iterable[str],
    ) -> bool:
        """True when a Go compile barrier clears and real tests become runnable.

        ``go test ./...`` may report package assertion failures together with
        one root-package compile error.  Fixing that compiler diagnostic can
        reveal additional root-package assertions while leaving the total
        diagnostic count unchanged.  That is a later verifier phase, not an
        equal-count swap.  Require the compiler marker to disappear and real
        ``--- FAIL:`` evidence to remain so ordinary assertion churn stays
        conservative.
        """

        before_text = "\n".join(str(item or "") for item in before_signature).casefold()
        after_text = "\n".join(str(item or "") for item in after_signature).casefold()
        go_compile_markers = (
            "cannot convert",
            "undefined:",
            "declared and not used",
            "imported and not used",
            "has no field or method",
            "invalid operation:",
            "not enough arguments in call",
            "too many arguments in call",
            "assignment mismatch:",
            "multiple-value ",
            "not enough return values",
            "too many return values",
        )
        before_has_compile_barrier = any(marker in before_text for marker in go_compile_markers)
        after_has_compile_barrier = any(marker in after_text for marker in go_compile_markers)
        return before_has_compile_barrier and not after_has_compile_barrier and "--- fail:" in after_text

    @staticmethod
    def _python_import_barrier_unmasked_runnable_tests(
        before_signature: Iterable[str],
        after_signature: Iterable[str],
    ) -> bool:
        """True when Python test collection advances into runnable tests.

        A missing import/symbol can collapse an entire unittest module into
        ``unittest.loader._FailedTest``.  Fixing that single barrier commonly
        reveals many independent assertion failures, so the diagnostic count
        increases even though execution advanced from zero collected tests to
        a real test run.  Treat only that evidenced phase transition as
        forward-unmasking; ordinary Python exception churn remains regression.
        """

        before_text = "\n".join(str(item or "") for item in before_signature).casefold()
        after_text = "\n".join(str(item or "") for item in after_signature).casefold()
        collection_barriers = (
            "unittest.loader._failedtest",
            "failed to import test module",
        )
        if not any(marker in before_text for marker in collection_barriers):
            return False
        if any(marker in after_text for marker in collection_barriers):
            return False
        return bool(re.search(r"\bran\s+[1-9][0-9]*\s+tests?\b", after_text))

    @staticmethod
    def _python_setup_barrier_unmasked_runtime_tests(
        before_signature: Iterable[str],
        after_signature: Iterable[str],
    ) -> bool:
        """True when fewer broken unittest setup barriers expose test methods.

        A generated ``setUpClass`` can fail before any test method runs.  A
        repair that clears one such barrier may reveal several independent
        runtime failures, increasing the raw diagnostic count while still
        advancing execution.  Require both a strict reduction of the exact
        setup-barrier family and newly visible named test-method errors; other
        Python exception churn remains fail-closed.
        """

        before_items = tuple(str(item or "").casefold() for item in before_signature)
        after_items = tuple(str(item or "").casefold() for item in after_signature)

        def _setup_barrier_count(items: tuple[str, ...]) -> int:
            return sum(
                1
                for item in items
                if "error: setupclass" in item and "typeerror: testcase.assertequal() missing" in item
            )

        before_barriers = _setup_barrier_count(before_items)
        after_barriers = _setup_barrier_count(after_items)
        if before_barriers <= 0 or after_barriers >= before_barriers:
            return False
        return any(
            re.search(r"(?m)^error:\s+test_[a-z0-9_]", item) is not None
            or re.search(r"(?m)^fail:\s+test_[a-z0-9_]", item) is not None
            for item in after_items
        )

    @classmethod
    def _workspace_quality_repair_effect(
        cls,
        *,
        before_signature: tuple[str, ...],
        after_signature: tuple[str, ...],
        verifier_passed: bool,
        write_tool_evidence: bool,
        before_results: Iterable[Mapping[str, Any]] = (),
        after_results: Iterable[Mapping[str, Any]] = (),
        workspace: os.PathLike[str] | str | None = None,
    ) -> str:
        """Classify one local repair by verifier effect, never by model claim."""

        if verifier_passed:
            return "resolved"
        if not write_tool_evidence:
            return "no_op"
        if workspace_quality_impl.workspace_quality_compile_barrier_reduced(
            tuple(before_results),
            tuple(after_results),
        ):
            # A downstream test runner may invoke the same still-red build and
            # fan one remaining compiler blocker into more setup errors.  An
            # explicit strict reduction of the upstream compiler frontier is
            # nevertheless real monotonic progress and must survive into the
            # next same-task repair round.  Use ``progress`` rather than
            # ``forward_unmask``: the latter is intentionally accepted only
            # for a newly observed diagnostic phase, while a strict frontier
            # subset necessarily retains already-seen residual diagnostics.
            return "progress"
        if workspace_quality_impl.workspace_quality_compiler_diagnostic_frontier_reduced(
            tuple(before_results),
            tuple(after_results),
        ):
            # A shared downstream blocker can keep every translation unit red
            # even after the candidate removes one causal compiler error.  The
            # explicit post-edit error-identity subset is verifier-owned proof
            # of monotonic progress; unlike cardinality, it rejects swaps.
            return "progress"
        if workspace_quality_impl.workspace_quality_cpp_include_barrier_repaired(
            tuple(before_results),
            tuple(after_results),
            workspace=workspace,
        ):
            # Correcting an early malformed include can expose more later C++
            # diagnostics beyond the per-TU excerpt limit. Exact disk evidence
            # distinguishes a valid include repair from deletion that merely
            # hides the preprocessor diagnostic.
            return "progress"
        if workspace_quality_impl.workspace_quality_test_harness_barrier_reduced(
            tuple(before_results),
            tuple(after_results),
        ):
            # A Python test wrapper may fail in its own error-reporting path
            # before surfacing an already-proven product compiler blocker.
            # Clearing that wrapper-only barrier is causal progress even when
            # top-level diagnostic cardinality stays unchanged.
            return "progress"
        if workspace_quality_impl.workspace_quality_verifier_regressed(
            tuple(before_results),
            tuple(after_results),
        ):
            return "regression"
        before = set(before_signature)
        after = set(after_signature)
        if after == before:
            return "stagnant"
        before_test_identities = workspace_quality_impl._workspace_quality_failing_test_identities(before_signature)
        after_test_identities = workspace_quality_impl._workspace_quality_failing_test_identities(after_signature)
        if (
            before_test_identities
            and after_test_identities
            and before_test_identities == after_test_identities
            and workspace_quality_impl._workspace_quality_is_pure_named_test_surface(before_signature)
            and workspace_quality_impl._workspace_quality_is_pure_named_test_surface(after_signature)
        ):
            # Live L3-22: the same two Go tests emitted different package
            # durations/cache footers, so whole-text signatures differed and
            # the loop invented an equal-count swap plus duplicate regression
            # guards. Named failing tests are the stable verifier identity;
            # changed assertion values remain useful context but not progress.
            return "stagnant"
        if cls._go_crash_unmasked_runnable_tests(before_signature, after_signature):
            return "forward_unmask"
        if cls._go_compile_barrier_unmasked_runnable_tests(before_signature, after_signature):
            return "forward_unmask"
        if cls._python_import_barrier_unmasked_runnable_tests(before_signature, after_signature):
            return "forward_unmask"
        if cls._python_setup_barrier_unmasked_runtime_tests(before_signature, after_signature):
            return "forward_unmask"
        # A smaller authoritative diagnostic set is measurable progress even
        # when the remaining diagnostic text is not a strict subset.  Real
        # verifier output often changes framing after an earlier barrier is
        # cleared (for example, a Go compile failure becomes two runnable test
        # failures, then one test failure whose package footer/duration also
        # changed).  Requiring set inclusion mislabeled that 2 -> 1 reduction
        # as a regression and prematurely tripped the two-round circuit
        # breaker.  This classification never grants success: the verifier
        # still owns success, and the bounded repair budget still applies.
        if len(after) < len(before):
            return "progress"
        if len(after) == len(before):
            # rustc compiles in phases (resolution -> trait/borrow-check ->
            # codegen) and stops at the first hard error, so an edit that
            # resolves the reported code unmasks the next phase's diagnostic:
            # E0432 -> E0277 -> E0507, often at the same line.  Live L1-05
            # rounds 1-2 each carried fingerprint-changing edits yet both were
            # miscounted as equivalent swaps and the loop stopped with
            # compilation strictly advanced.  Disjoint rustc code sets on both
            # sides mean every previously reported code was resolved; the
            # caller's oscillation guard keeps A -> B -> A ping-pong
            # stagnating.  tsc reports all type errors in a single pass, so
            # disjoint TS codes stay an equal-count swap (churn), preserving
            # the TS7015 -> TS2551 stagnation characterization.
            before_codes = cls._workspace_quality_diagnostic_error_codes(before_signature)
            after_codes = cls._workspace_quality_diagnostic_error_codes(after_signature)
            before_rust = {code for code in before_codes if code.startswith("e")}
            after_rust = {code for code in after_codes if code.startswith("e")}
            if (
                before_rust
                and after_rust
                and len(before_rust) == len(before_codes)
                and len(after_rust) == len(after_codes)
                and not (before_rust & after_rust)
            ):
                return "forward_unmask"
            before_cpp = {code for code in before_codes if code.startswith("cpp_")}
            after_cpp = {code for code in after_codes if code.startswith("cpp_")}
            # Phase change (undeclared -> missing member) or a strict
            # reduction of missing-member names. Adding new kinds without
            # dropping old ones stays a swap.
            if (
                before_cpp
                and after_cpp
                and before_cpp != after_cpp
                and (not (before_cpp & after_cpp) or after_cpp < before_cpp)
            ):
                return "forward_unmask"
            return "equal_count_swap"
        return "regression"

    def _workspace_quality_repair_issue_payloads(
        self,
        artifact_quality_errors: list[str],
    ) -> tuple[dict[str, Any], ...]:
        if not artifact_quality_errors:
            return ()
        try:
            from polaris.kernelone.quality import (
                artifact_quality_issues_for_errors,
                scan_workspace_artifact_quality_evidence,
            )

            evidence = scan_workspace_artifact_quality_evidence(str(self.workspace))
            return artifact_quality_issues_for_errors(artifact_quality_errors, evidence.issues)
        except (ImportError, OSError, RuntimeError, TypeError, ValueError):
            pass
        try:
            from polaris.kernelone.quality import artifact_quality_issues_from_errors

            return artifact_quality_issues_from_errors(str(item) for item in artifact_quality_errors or [])
        except (ImportError, RuntimeError, TypeError, ValueError):
            return ()

    def _workspace_quality_repair_coverage_report(self, artifact_quality_errors: list[str]) -> dict[str, Any]:
        if not artifact_quality_errors:
            return {}
        try:
            from polaris.cells.director.runtime.public import (
                QueryDirectorRepairCoverageV1,
                query_director_repair_coverage,
            )

            return query_director_repair_coverage(
                QueryDirectorRepairCoverageV1(
                    artifact_quality_errors=tuple(str(item) for item in artifact_quality_errors),
                    artifact_quality_issues=self._workspace_quality_repair_issue_payloads(artifact_quality_errors),
                )
            ).to_dict()
        except (ImportError, RuntimeError, TypeError, ValueError) as exc:
            return {
                "schema_version": "factory.workspace_quality_repair_coverage_query_error.v1",
                "source": "factory_stage_executor",
                "access": "read_only",
                "coverage_query_failed": True,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "total_diagnostics": len(artifact_quality_errors),
                "coverage_gap_count": 0,
                "coverage_gaps": [],
            }

    def _workspace_quality_repair_plan_probe_report(self, artifact_quality_errors: list[str]) -> dict[str, Any]:
        if not artifact_quality_errors:
            return {}
        try:
            from polaris.cells.director.runtime.public import (
                QueryDirectorRepairPlanProbeV1,
                query_director_repair_plan_probe,
            )

            return query_director_repair_plan_probe(
                QueryDirectorRepairPlanProbeV1(
                    artifact_quality_errors=tuple(str(item) for item in artifact_quality_errors),
                    artifact_quality_issues=self._workspace_quality_repair_issue_payloads(artifact_quality_errors),
                    base_files=self._workspace_quality_repair_plan_probe_base_files(artifact_quality_errors),
                    metadata={
                        "source": "factory_stage_executor.workspace_quality",
                        "coverage_is_not_planning": True,
                    },
                )
            ).to_dict()
        except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            return {
                "schema_version": "factory.workspace_quality_repair_plan_probe_query_error.v1",
                "source": "factory_stage_executor",
                "access": "read_only",
                "plan_probe_query_failed": True,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "total_diagnostics": len(artifact_quality_errors),
                "status": "plan_probe_unavailable",
                "coverage_is_not_planning": True,
            }

    def _workspace_quality_repair_plan_probe_base_files(self, artifact_quality_errors: list[str]) -> dict[str, str]:
        workspace_root = self.workspace.resolve()
        candidates: list[str] = []
        candidates.extend(self._workspace_quality_repair_diagnostic_target_files(artifact_quality_errors))
        candidates.extend(self._workspace_quality_repair_target_files())
        candidates.extend(
            workspace_quality_rust_plan_probe_companion_paths(
                workspace_root,
                artifact_quality_errors=artifact_quality_errors,
            )
        )
        base_files: dict[str, str] = {}
        for raw_candidate in candidates:
            normalized = os.path.normpath(str(raw_candidate or "").strip().replace("\\", "/")).replace("\\", "/")
            if not normalized or normalized in base_files or not _is_workspace_quality_repair_path(normalized):
                continue
            path = resolve_workspace_quality_existing_file(workspace_root, normalized)
            try:
                if path is None or not path.is_relative_to(workspace_root) or not path.is_file():
                    continue
                if path.stat().st_size > 256_000:
                    continue
                stored = "Cargo.toml" if path.name.lower() == "cargo.toml" else normalized
                if stored in base_files:
                    continue
                base_files[stored] = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError, ValueError):
                continue
            if len(base_files) >= 64:
                break
        return base_files

    def _director_stage_should_run_materialization_quality_settle(
        self,
        *,
        stage_status: str,
        error_code: str,
    ) -> bool:
        return materialization_impl._director_stage_should_run_materialization_quality_settle(
            self, stage_status=stage_status, error_code=error_code
        )

    def _workspace_has_delivery_surface(self) -> bool:
        return materialization_impl._workspace_has_delivery_surface(self)

    def _recover_director_stage_authority_after_delivery_settle(
        self,
        *,
        run: FactoryRun,
        context: dict[str, Any],
        prior_authority: helpers.CanonicalFactoryAuthority,
    ) -> helpers.CanonicalFactoryAuthority | None:
        return materialization_impl._recover_director_stage_authority_after_delivery_settle(
            self, run=run, context=context, prior_authority=prior_authority
        )

    def _seal_director_stage_missing_tool_lifecycles(
        self,
        *,
        run: FactoryRun,
        incomplete_task_ids: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        return materialization_impl._seal_director_stage_missing_tool_lifecycles(
            self, run=run, incomplete_task_ids=incomplete_task_ids
        )

    def _collect_director_stage_materialization_diagnostics(self) -> list[str]:
        return materialization_impl._collect_director_stage_materialization_diagnostics(self)

    def _ensure_director_stage_materialization_typescript_toolchain(self) -> None:
        """Best-effort npm install so settle can collect tsc diagnostics (R167)."""

        package_json = self.workspace / "package.json"
        if not package_json.is_file():
            return
        try:
            payload = json.loads(package_json.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            return
        if not isinstance(payload, Mapping):
            return
        deps: dict[str, Any] = {}
        for key in ("dependencies", "devDependencies"):
            raw = payload.get(key)
            if isinstance(raw, Mapping):
                deps.update(raw)
        has_typescript = any(str(name).lower() == "typescript" for name in deps)
        if not has_typescript:
            return
        try:
            pkg().subprocess.run(
                ["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"],
                cwd=str(self.workspace),
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
        except (OSError, TimeoutError, ValueError) as exc:
            logger.warning(
                "Director stage materialization settle npm install skipped: %s",
                exc,
            )

    def _claim_director_stage_materialization_settle_attempt(
        self,
        *,
        run_id: str,
    ) -> tuple[str, int, TaskRuntimeExecutionAttemptIdentityV1]:
        return materialization_impl._claim_director_stage_materialization_settle_attempt(self, run_id=run_id)

    @staticmethod
    def _workspace_quality_repair_owner_score(
        candidate: Mapping[str, Any],
        *,
        run_id: str,
        normalized_targets: set[str],
    ) -> tuple[int, int]:
        """Score only task-owned paths when selecting a verifier-repair owner.

        ``project_declared_target_files`` is a project-wide inventory copied to
        every Director task.  Treating it as ownership makes unrelated tasks tie
        for every project file. Ownership comes from task-local PM paths plus the
        run-bound CE/JobToken write scope used by physical tools.
        """

        metadata = candidate.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        candidate_factory_run_id = str(metadata.get("factory_run_id") or "").strip()
        external_id = str(metadata.get("external_task_id") or candidate.get("external_task_id") or "").strip()
        if candidate_factory_run_id != run_id or not external_id or external_id.startswith("factory-"):
            return (-1, -1)
        authoritative_paths = workspace_quality_impl._workspace_quality_authoritative_owner_paths(
            metadata,
            run_id=run_id,
        )
        completion = metadata.get("task_completion_projection")
        owned_artifacts = completion.get("owned_artifacts") if isinstance(completion, Mapping) else None
        completion_run_id = str(completion.get("run_id") or "").strip() if isinstance(completion, Mapping) else ""
        has_completion_ownership = bool(owned_artifacts) and completion_run_id in {
            "",
            run_id,
        }
        raw_paths: list[Any] = list(authoritative_paths)
        if not has_completion_ownership:
            for key in ("target_files", "scope_paths"):
                value = metadata.get(key)
                if isinstance(value, str):
                    raw_paths.append(value)
                elif isinstance(value, list | tuple | set):
                    raw_paths.extend(value)
        candidate_paths = {str(path or "").strip().replace("\\", "/") for path in raw_paths if str(path or "").strip()}
        overlaps = workspace_quality_impl._workspace_quality_repair_path_overlaps(normalized_targets, candidate_paths)
        source_overlap = sum(
            1 for path in overlaps if not workspace_quality_impl._is_workspace_quality_test_target(path)
        )
        # A verifier diagnostic often yields both the failing test path and
        # its imported implementation source. Prefer the implementation owner
        # when each task overlaps one target; otherwise TaskRuntime row order
        # can select the test task and make the real source edit out of scope.
        overlap = len(overlaps) + source_overlap
        status = str(candidate.get("status") or candidate.get("raw_status") or "").strip().lower()
        rework_priority = 1 if status in {"pending", "ready", "blocked", "failed"} else 0
        return (overlap, rework_priority)

    def _claim_workspace_quality_repair_attempt(
        self,
        *,
        run: FactoryRun,
        repair_attempt: int,
        target_files: list[str],
    ) -> tuple[str, int, TaskRuntimeExecutionAttemptIdentityV1, dict[str, Any]]:
        return workspace_quality_impl._claim_workspace_quality_repair_attempt(
            self, run=run, repair_attempt=repair_attempt, target_files=target_files
        )

    @staticmethod
    def _materialization_settle_attempt_outcome(stage_status: str) -> TaskRuntimeExecutionAttemptSettlementOutcomeV1:
        """Map settle procedure stage_status to a terminal TaskRuntime outcome.

        R184/M06: never return suspended for factory-owned settle helper claims.
        """

        normalized = str(stage_status or "").strip().lower()
        if normalized in {"success", "completed", "ok", "passed"}:
            return "completed"
        return "failed"

    def _settle_director_stage_materialization_attempt(
        self,
        *,
        task_row_id: int,
        execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
        stage_status: str,
        summary: str,
    ) -> dict[str, Any]:
        return materialization_impl._settle_director_stage_materialization_attempt(
            self,
            task_row_id=task_row_id,
            execution_attempt=execution_attempt,
            stage_status=stage_status,
            summary=summary,
        )
