"""Owner-scoped composition and persistence for CE semantic repair."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from typing import Any, cast

from polaris.cells.chief_engineer.blueprint.internal.semantic_repair_store import (
    ChiefEngineerSemanticRepairCandidateStore,
)
from polaris.cells.chief_engineer.blueprint.public.contracts import (
    ArtifactObligationV1,
    ChiefEngineerBehaviorExampleV1,
    ChiefEngineerBehaviorInvariantV1,
    ChiefEngineerPortfolioStructuralRecoveryV1,
    ChiefEngineerPortfolioTaskV1,
    ChiefEngineerSemanticRepairCandidateV1,
    ChiefEngineerSemanticRepairDiagnosisV1,
    ChiefEngineerSemanticRepairPatchV1,
    ChiefEngineerSemanticRepairReceiptV1,
    EntrypointObligationV1,
)

from ._helpers import (
    _ce_artifact_role_matches_path,
    _ce_delegated_artifact_roles,
    _ce_topology_authorizes_artifact,
    _is_ce_production_source_path,
    _is_ce_test_topology_path,
)
from ._portfolio import _task_authorizes_completion_path, _task_expandable_scope_paths


def _delegated_artifact_roles(task: ChiefEngineerPortfolioTaskV1) -> frozenset[str]:
    if task.topology_authority != "chief_engineer":
        return frozenset()
    return _ce_delegated_artifact_roles(task.required_source_kinds)


def _task_delegates_artifact(*, task: ChiefEngineerPortfolioTaskV1, semantic_role: str, path: str) -> bool:
    return _ce_topology_authorizes_artifact(
        topology_authority=task.topology_authority,
        required_source_kinds=task.required_source_kinds,
        allowed_source_suffixes=task.allowed_source_suffixes,
        semantic_role=semantic_role,
        path=path,
    )


def _hash(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _mapping(name: str, value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    return cast(dict[str, Any], deepcopy(dict(value)))


def _rows(name: str, value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise TypeError(f"{name} must be a list")
    return [_mapping(f"{name}[{index}]", item) for index, item in enumerate(value)]


class _UnsafePortfolioStructuralRecoveryError(ValueError):
    """Raised internally when a malformed payload cannot be relocated safely."""


def _classify_interface_declaration(row: Mapping[str, Any]) -> str:
    keys = set(row)
    provider = {"symbol", "owner_task_id", "path"}.issubset(keys)
    consumer = {"consumer_task_id", "provider_symbol"}.issubset(keys)
    if provider == consumer:
        raise _UnsafePortfolioStructuralRecoveryError("ambiguous interface declaration")
    return "provider_declarations" if provider else "consumer_declarations"


def normalize_chief_engineer_portfolio_tool_arguments(
    payload: Mapping[str, Any],
) -> ChiefEngineerPortfolioStructuralRecoveryV1:
    """Relocate known CE portfolio members without inventing semantic content.

    Some provider tool streams preserve every value but lift nested object
    members to the root or emit an array ``item`` beside its owning array.
    This normalizer is intentionally narrow and transactional: ambiguity or a
    destination conflict returns the original payload unchanged.
    """

    source = _mapping("payload", payload)
    working = deepcopy(source)
    codes: list[str] = []
    try:
        construction = _mapping("construction_plan", working.get("construction_plan"))
        working["construction_plan"] = construction

        for member in ("task_plans", "project_interface_contract", "shared_behavior_contract"):
            if member not in working:
                continue
            incoming = working[member]
            if member in construction and construction[member] != incoming:
                raise _UnsafePortfolioStructuralRecoveryError(f"conflicting {member}")
            construction[member] = deepcopy(incoming)
            del working[member]
            codes.append(f"move_root_{member}")

        interface = _mapping("project_interface_contract", construction.get("project_interface_contract"))
        construction["project_interface_contract"] = interface
        for collection in ("provider_declarations", "consumer_declarations"):
            destination = _rows(collection, interface.get(collection, []))
            if collection in working:
                destination.extend(_rows(f"root.{collection}", working.pop(collection)))
                codes.append(f"move_root_{collection}")
            interface[collection] = destination

        for owner, container, code_prefix in (
            ("project_interface_contract", interface, "project_interface"),
            ("root", working, "root"),
        ):
            if "item" not in container:
                continue
            item = _mapping(f"{owner}.item", container["item"])
            collection = _classify_interface_declaration(item)
            destination = _rows(collection, interface.get(collection))
            destination.append(item)
            interface[collection] = destination
            del container["item"]
            kind = "provider" if collection == "provider_declarations" else "consumer"
            codes.append(f"classify_{code_prefix}_item_as_{kind}")
    except (TypeError, ValueError):
        return ChiefEngineerPortfolioStructuralRecoveryV1(
            source_payload=source,
            payload=source,
            repair_codes=(),
        )

    if not codes:
        return ChiefEngineerPortfolioStructuralRecoveryV1(
            source_payload=source,
            payload=source,
            repair_codes=(),
        )
    return ChiefEngineerPortfolioStructuralRecoveryV1(
        source_payload=source,
        payload=working,
        repair_codes=tuple(codes),
    )


def _artifact_from_row(row: Mapping[str, Any]) -> ArtifactObligationV1:
    return ArtifactObligationV1(
        obligation_id=row.get("obligation_id"),
        path=row.get("path"),
        semantic_role=row.get("semantic_role"),
        applicability=row.get("applicability"),
        owner_task_id=row.get("owner_task_id"),
    )


def _entrypoint_from_row(row: Mapping[str, Any]) -> EntrypointObligationV1:
    return EntrypointObligationV1(
        obligation_id=row.get("obligation_id"),
        kind=row.get("kind"),
        applicability=row.get("applicability"),
        owner_task_id=row.get("owner_task_id"),
        source_path=row.get("source_path"),
        runtime_path=row.get("runtime_path"),
        command=row.get("command"),
    )


def _behavior_from_row(row: Mapping[str, Any]) -> ChiefEngineerBehaviorInvariantV1:
    examples = _rows("verification_examples", row.get("verification_examples"))
    return ChiefEngineerBehaviorInvariantV1(
        invariant_id=row.get("invariant_id"),
        statement=row.get("statement"),
        owner_task_id=row.get("owner_task_id"),
        consumer_task_ids=tuple(row.get("consumer_task_ids") or ()),
        covered_obligation_ids=tuple(row.get("covered_obligation_ids") or ()),
        verification_examples=tuple(
            ChiefEngineerBehaviorExampleV1(
                given=item.get("given"),
                when=item.get("when"),
                then=item.get("then"),
            )
            for item in examples
        ),
    )


def _replace_by_id(
    current: list[dict[str, Any]],
    upserts: tuple[Any, ...],
    *,
    id_field: str,
) -> list[dict[str, Any]]:
    current_ids = [str(row.get(id_field) or "") for row in current]
    if any(not value for value in current_ids) or len(current_ids) != len(set(current_ids)):
        raise ValueError(f"existing semantic rows contain invalid or duplicate {id_field}")
    upsert_ids = [str(getattr(value, id_field)) for value in upserts]
    if len(upsert_ids) != len(set(upsert_ids)):
        raise ValueError(f"semantic patch contains duplicate {id_field}")
    indexed = {str(row[id_field]): deepcopy(row) for row in current}
    for value in upserts:
        indexed[str(getattr(value, id_field))] = value.to_dict()
    return [indexed[key] for key in sorted(indexed)]


def _assert_upsert_identity_immutable(
    current: list[dict[str, Any]],
    upserts: tuple[Any, ...],
    *,
    id_field: str,
    identity_fields: tuple[str, ...],
) -> None:
    """Reject semantic patches that repurpose an existing obligation id."""

    indexed = {str(row.get(id_field) or ""): row for row in current}
    for value in upserts:
        row_id = str(getattr(value, id_field))
        baseline = indexed.get(row_id)
        if baseline is None:
            continue
        projected = value.to_dict()
        changed = [
            field
            for field in identity_fields
            if baseline.get(field) != projected.get(field)
        ]
        if changed:
            raise ValueError(
                "semantic repair cannot mutate immutable semantic identity: "
                f"{id_field}={row_id!r}:fields={changed!r}"
            )


def _semantic_sections(candidate: Mapping[str, Any]) -> dict[str, Any]:
    completion = _mapping("project_completion_contract", candidate.get("project_completion_contract"))
    obligations = _mapping("project_completion_contract.obligations", completion.get("obligations"))
    construction = _mapping("construction_plan", candidate.get("construction_plan"))
    behavior = _mapping(
        "construction_plan.shared_behavior_contract",
        construction.get("shared_behavior_contract") or {"invariants": []},
    )
    task_plans = _mapping("construction_plan.task_plans", construction.get("task_plans"))
    return {
        "artifacts": obligations.get("artifacts"),
        "entrypoints": obligations.get("entrypoints"),
        "verification": obligations.get("verification"),
        "behavior_invariants": behavior.get("invariants"),
        "task_behavior_refs": {
            task_id: _mapping(f"task_plans[{task_id!r}]", plan).get("behavior_invariant_refs")
            for task_id, plan in sorted(task_plans.items())
        },
        "project_interface_contract": construction.get("project_interface_contract"),
    }


def project_chief_engineer_semantic_repair_provider_context(
    candidate: ChiefEngineerSemanticRepairCandidateV1,
    diagnosis: ChiefEngineerSemanticRepairDiagnosisV1,
    *,
    tasks: tuple[ChiefEngineerPortfolioTaskV1, ...],
) -> dict[str, Any]:
    """Project the exact, minimal base state required to author a safe patch.

    A candidate hash alone is not executable repair context: the provider must
    see the current semantic rows so it can avoid duplicate ids/paths and must
    see each task's immutable apply scope so every upsert retains an owner.
    Only diagnosis-authorized sections are exposed; unrelated portfolio prose
    remains outside the retry envelope.
    """

    if diagnosis.candidate_hash != candidate.candidate_hash:
        raise ValueError("diagnosis candidate_hash does not match candidate")
    sections = _semantic_sections(candidate.candidate)
    task_ids = tuple(task.task_id for task in tasks)
    if task_ids != candidate.task_ids:
        raise ValueError("semantic repair PM task ids do not match candidate task ids")
    current_artifacts = _rows("artifacts", sections["artifacts"])
    current_entrypoints = _rows("entrypoints", sections["entrypoints"])
    occupied_paths = {
        str(row.get(key) or "").strip()
        for row in (*current_artifacts, *current_entrypoints)
        for key in ("path", "source_path", "runtime_path")
        if str(row.get(key) or "").strip()
    }
    task_authority = {
        task.task_id: {
            "target_files": list(task.target_files),
            "scope_paths": list(task.scope_paths),
            "unused_exact_target_paths": [path for path in task.target_files if path not in occupied_paths],
            "expandable_scope_paths": list(_task_expandable_scope_paths(task)),
            "topology_authority": task.topology_authority,
            "required_source_kinds": list(task.required_source_kinds),
            "delegated_artifact_roles": sorted(_delegated_artifact_roles(task)),
        }
        for task in tasks
    }
    available_exact_target_paths = sorted(
        {
            path
            for row in task_authority.values()
            for path in cast(list[str], row["unused_exact_target_paths"])
        }
    )
    available_prod_target_paths = sorted(
        {
            path
            for task in tasks
            for path in task.target_files
            if path not in occupied_paths
            and _ce_artifact_role_matches_path(
                semantic_role="source",
                path=path,
                allowed_source_suffixes=task.allowed_source_suffixes,
            )
        }
    )
    available_test_target_paths = sorted(
        {
            path
            for task in tasks
            for path in task.target_files
            if path not in occupied_paths
            and _ce_artifact_role_matches_path(
                semantic_role="test",
                path=path,
                allowed_source_suffixes=task.allowed_source_suffixes,
            )
        }
    )
    expandable_scope_paths = sorted(
        {
            path
            for row in task_authority.values()
            for path in cast(list[str], row["expandable_scope_paths"])
        }
    )
    delegated_topology_task_ids = sorted(
        task.task_id
        for task in tasks
        if task.topology_authority == "chief_engineer" and task.required_source_kinds
    )
    delegated_artifact_roles = sorted(
        {role for task in tasks for role in _delegated_artifact_roles(task)}
    )
    expandable_test_scope_paths = [
        path for path in expandable_scope_paths if _is_ce_test_topology_path(f"{path}/test.py")
    ]
    expandable_prod_scope_paths = [
        path
        for path in expandable_scope_paths
        if not _is_ce_test_topology_path(f"{path}/test.py")
        and not set(str(path).lower().replace("\\", "/").split("/")).intersection({"docs", "doc"})
    ]
    depth_artifact_repair = "artifact_upsert" in diagnosis.allowed_operations and any(
        code.startswith("chief_engineer.delivery_depth.") for code in diagnosis.diagnostic_codes
    )
    required_depth_metrics: list[str] = []
    if any("prod_files" in code for code in diagnosis.diagnostic_codes):
        required_depth_metrics.append("prod_files")
    if any("test_files" in code for code in diagnosis.diagnostic_codes):
        required_depth_metrics.append("test_files")
    metric_feasibility = {
        "prod_files": bool(
            available_prod_target_paths
            or expandable_prod_scope_paths
            or set(delegated_artifact_roles).intersection({"source", "entrypoint"})
        ),
        "test_files": bool(
            available_test_target_paths or expandable_test_scope_paths or "test" in delegated_artifact_roles
        ),
    }
    repair_feasible = not depth_artifact_repair or (
        all(metric_feasibility[metric] for metric in required_depth_metrics)
        if required_depth_metrics
        else any(metric_feasibility.values())
    )
    projection: dict[str, Any] = {
        "schema_version": "chief_engineer.semantic_repair_provider_context.v1",
        "base_candidate_hash": candidate.candidate_hash,
        "diagnosis_hash": diagnosis.diagnosis_hash,
        "diagnostic_codes": list(diagnosis.diagnostic_codes),
        "allowed_operations": list(diagnosis.allowed_operations),
        "task_authority": task_authority,
        "authority_basis_hash": _hash(task_authority),
        "available_exact_target_paths": available_exact_target_paths,
        "available_prod_target_paths": available_prod_target_paths,
        "available_test_target_paths": available_test_target_paths,
        "expandable_scope_paths": expandable_scope_paths,
        "expandable_prod_scope_paths": expandable_prod_scope_paths,
        "expandable_test_scope_paths": expandable_test_scope_paths,
        "delegated_topology_task_ids": delegated_topology_task_ids,
        "delegated_artifact_roles": delegated_artifact_roles,
        "required_depth_metrics": required_depth_metrics,
        "metric_feasibility": metric_feasibility,
        "repair_feasible": repair_feasible,
        "blocker_code": "" if repair_feasible else "chief_engineer.semantic_repair_authority_infeasible",
        "current": {},
    }
    current = cast(dict[str, Any], projection["current"])
    allowed = set(diagnosis.allowed_operations)
    if "artifact_upsert" in allowed:
        current["artifacts"] = deepcopy(sections["artifacts"])
    if "entrypoint_upsert" in allowed:
        current["artifacts"] = deepcopy(sections["artifacts"])
        current["entrypoints"] = deepcopy(sections["entrypoints"])
    if "behavior_invariant_upsert" in allowed:
        current["artifacts"] = deepcopy(sections["artifacts"])
        current["entrypoints"] = deepcopy(sections["entrypoints"])
        current["behavior_invariants"] = deepcopy(sections["behavior_invariants"])
    if "task_behavior_ref_replace" in allowed:
        current["behavior_invariants"] = deepcopy(sections["behavior_invariants"])
        current["task_behavior_refs"] = deepcopy(sections["task_behavior_refs"])
    return projection


def compose_chief_engineer_semantic_repair(
    candidate: ChiefEngineerSemanticRepairCandidateV1,
    diagnosis: ChiefEngineerSemanticRepairDiagnosisV1,
    patch: ChiefEngineerSemanticRepairPatchV1,
    *,
    tasks: tuple[ChiefEngineerPortfolioTaskV1, ...],
) -> tuple[ChiefEngineerSemanticRepairCandidateV1, ChiefEngineerSemanticRepairReceiptV1]:
    """Compose one typed patch, preserving unrelated schema-valid sections."""

    if diagnosis.candidate_hash != candidate.candidate_hash:
        raise ValueError("diagnosis candidate_hash does not match candidate")
    if patch.base_candidate_hash != candidate.candidate_hash:
        raise ValueError("patch base_candidate_hash does not match candidate")
    if patch.diagnosis_hash != diagnosis.diagnosis_hash:
        raise ValueError("patch diagnosis_hash does not match diagnosis")
    unauthorized = set(patch.operations) - set(diagnosis.allowed_operations)
    if unauthorized:
        raise ValueError(f"patch operations are not diagnosis-authorized: {sorted(unauthorized)}")

    payload = deepcopy(dict(candidate.candidate))
    completion = _mapping("project_completion_contract", payload["project_completion_contract"])
    obligations = _mapping("project_completion_contract.obligations", completion.get("obligations"))
    # The frozen candidate is the schema-valid repair base. Preserve its
    # untouched rows verbatim instead of rehydrating every row through today's
    # stricter DTO constructors: an incremental artifact patch must not fail on
    # an unrelated legacy entrypoint representation accepted by the original
    # portfolio turn (exact r07 used runtime_path="."). Only typed upserts are
    # freshly constructed and validated.
    artifact_baseline_rows = _rows("artifacts", obligations.get("artifacts"))
    entrypoint_baseline_rows = _rows("entrypoints", obligations.get("entrypoints"))
    _assert_upsert_identity_immutable(
        artifact_baseline_rows,
        patch.artifact_upserts,
        id_field="obligation_id",
        identity_fields=("path", "semantic_role", "owner_task_id"),
    )
    _assert_upsert_identity_immutable(
        entrypoint_baseline_rows,
        patch.entrypoint_upserts,
        id_field="obligation_id",
        identity_fields=("kind", "source_path", "runtime_path", "owner_task_id"),
    )
    task_ids = set(candidate.task_ids)
    tasks_by_id = {task.task_id: task for task in tasks}
    if tuple(task.task_id for task in tasks) != candidate.task_ids:
        raise ValueError("semantic repair PM task ids do not match candidate task ids")

    for row in artifact_baseline_rows:
        owner_task_id = row.get("owner_task_id")
        if owner_task_id is not None and owner_task_id not in task_ids:
            raise ValueError(f"artifact owner_task_id is outside candidate task set: {owner_task_id}")
    for artifact in patch.artifact_upserts:
        if artifact.owner_task_id is not None and artifact.owner_task_id not in task_ids:
            raise ValueError(f"artifact owner_task_id is outside candidate task set: {artifact.owner_task_id}")
    for artifact in patch.artifact_upserts:
        owner = tasks_by_id.get(str(artifact.owner_task_id or ""))
        if not _ce_artifact_role_matches_path(
            semantic_role=artifact.semantic_role,
            path=artifact.path,
            allowed_source_suffixes=owner.allowed_source_suffixes if owner is not None else (),
        ):
            raise ValueError(
                "semantic repair artifact semantic role does not match path kind: "
                f"semantic_role={artifact.semantic_role!r}:path={artifact.path!r}"
            )
        if owner is None or not (
            _task_authorizes_completion_path(task=owner, path=artifact.path)
            or _task_delegates_artifact(task=owner, semantic_role=artifact.semantic_role, path=artifact.path)
        ):
            raise ValueError(
                "semantic repair artifact path is outside immutable PM authority: "
                f"owner_task_id={artifact.owner_task_id!r}:path={artifact.path!r}"
            )
    for row in entrypoint_baseline_rows:
        owner_task_id = row.get("owner_task_id")
        if owner_task_id is not None and owner_task_id not in task_ids:
            raise ValueError(f"entrypoint owner_task_id is outside candidate task set: {owner_task_id}")
    for entrypoint in patch.entrypoint_upserts:
        if entrypoint.owner_task_id is not None and entrypoint.owner_task_id not in task_ids:
            raise ValueError(f"entrypoint owner_task_id is outside candidate task set: {entrypoint.owner_task_id}")
    for entrypoint in patch.entrypoint_upserts:
        owner = tasks_by_id.get(str(entrypoint.owner_task_id or ""))
        source_authorized = bool(
            owner is not None
            and entrypoint.source_path is not None
            and (
                _task_authorizes_completion_path(task=owner, path=entrypoint.source_path)
                or (
                    "entrypoint" in _delegated_artifact_roles(owner)
                    and _is_ce_production_source_path(
                        entrypoint.source_path,
                        allowed_source_suffixes=owner.allowed_source_suffixes,
                    )
                )
            )
        )
        runtime_authorized = bool(
            owner is not None
            and entrypoint.runtime_path is not None
            and _task_authorizes_completion_path(task=owner, path=entrypoint.runtime_path)
        )
        if entrypoint.source_path is not None and not source_authorized:
            raise ValueError(
                "semantic repair entrypoint source path is outside immutable PM authority: "
                f"owner_task_id={entrypoint.owner_task_id!r}:path={entrypoint.source_path!r}"
            )
        if entrypoint.source_path is None and entrypoint.runtime_path is not None and not runtime_authorized:
            raise ValueError(
                "semantic repair entrypoint runtime path is outside immutable PM authority: "
                f"owner_task_id={entrypoint.owner_task_id!r}:path={entrypoint.runtime_path!r}"
            )

    artifact_rows = _replace_by_id(
        artifact_baseline_rows, patch.artifact_upserts, id_field="obligation_id"
    )
    artifact_paths = [row["path"] for row in artifact_rows]
    if len(artifact_paths) != len(set(artifact_paths)):
        raise ValueError("semantic repair would create duplicate artifact paths")
    entrypoint_rows = _replace_by_id(
        entrypoint_baseline_rows, patch.entrypoint_upserts, id_field="obligation_id"
    )
    all_obligation_ids = [row["obligation_id"] for row in artifact_rows] + [
        row["obligation_id"] for row in entrypoint_rows
    ]
    if len(all_obligation_ids) != len(set(all_obligation_ids)):
        raise ValueError("semantic repair would create duplicate obligation ids")
    obligations["artifacts"] = artifact_rows
    obligations["entrypoints"] = entrypoint_rows
    completion["obligations"] = obligations
    payload["project_completion_contract"] = completion

    construction = _mapping("construction_plan", payload["construction_plan"])
    behavior = _mapping(
        "construction_plan.shared_behavior_contract",
        construction.get("shared_behavior_contract") or {"invariants": []},
    )
    existing_invariants = [
        _behavior_from_row(row) for row in _rows("shared_behavior_contract.invariants", behavior.get("invariants"))
    ]
    invariants = _replace_by_id(
        [item.to_dict() for item in existing_invariants],
        patch.behavior_invariant_upserts,
        id_field="invariant_id",
    )
    known_invariant_ids = {row["invariant_id"] for row in invariants}
    for row in invariants:
        if row["owner_task_id"] not in task_ids or not set(row["consumer_task_ids"]).issubset(task_ids):
            raise ValueError("behavior invariant owner/consumer is outside candidate task set")
        if not set(row["covered_obligation_ids"]).issubset(set(all_obligation_ids)):
            raise ValueError("behavior invariant references unknown completion obligations")
    behavior["invariants"] = invariants
    construction["shared_behavior_contract"] = behavior

    task_plans = _mapping("construction_plan.task_plans", construction["task_plans"])
    unknown_tasks = set(patch.task_behavior_ref_replacements) - task_ids
    if unknown_tasks:
        raise ValueError(f"task behavior replacements reference unknown tasks: {sorted(unknown_tasks)}")
    for task_id, refs in patch.task_behavior_ref_replacements.items():
        unknown_refs = set(refs) - known_invariant_ids
        if unknown_refs:
            raise ValueError(f"task behavior replacement references unknown invariants: {sorted(unknown_refs)}")
        if task_id not in task_plans:
            raise ValueError(f"task behavior replacement has no candidate task plan: {task_id}")
        task_plan = _mapping(f"construction_plan.task_plans[{task_id!r}]", task_plans[task_id])
        task_plan["behavior_invariant_refs"] = list(refs)
        task_plans[task_id] = task_plan
    construction["task_plans"] = task_plans
    payload["construction_plan"] = construction

    after = ChiefEngineerSemanticRepairCandidateV1(
        workspace=candidate.workspace,
        project_id=candidate.project_id,
        run_id=candidate.run_id,
        pm_contract_hash=candidate.pm_contract_hash,
        task_ids=candidate.task_ids,
        task_set_hash=candidate.task_set_hash,
        candidate=payload,
    )
    before_sections = _semantic_sections(candidate.candidate)
    after_sections = _semantic_sections(after.candidate)
    unchanged = {
        name: _hash(before_value)
        for name, before_value in before_sections.items()
        if _hash(before_value) == _hash(after_sections[name])
    }
    changed_ids = tuple(
        sorted(
            {
                *(item.obligation_id for item in patch.artifact_upserts),
                *(item.obligation_id for item in patch.entrypoint_upserts),
                *(item.invariant_id for item in patch.behavior_invariant_upserts),
                *patch.task_behavior_ref_replacements.keys(),
            }
        )
    )
    receipt = ChiefEngineerSemanticRepairReceiptV1(
        before_candidate_hash=candidate.candidate_hash,
        patch_hash=patch.patch_hash,
        after_candidate_hash=after.candidate_hash,
        diagnosis_hash=diagnosis.diagnosis_hash,
        changed_semantic_ids=changed_ids,
        unchanged_section_hashes=unchanged,
    )
    return after, receipt


def persist_chief_engineer_semantic_repair_candidate(
    candidate: ChiefEngineerSemanticRepairCandidateV1,
) -> str:
    return ChiefEngineerSemanticRepairCandidateStore(candidate.workspace).persist(candidate)


def load_chief_engineer_semantic_repair_candidate(
    *,
    workspace: str,
    project_id: str,
    run_id: str,
    candidate_hash: str,
) -> ChiefEngineerSemanticRepairCandidateV1 | None:
    return ChiefEngineerSemanticRepairCandidateStore(workspace).load(
        project_id=project_id,
        run_id=run_id,
        candidate_hash=candidate_hash,
    )


def build_chief_engineer_semantic_repair_patch_schema() -> dict[str, Any]:
    """Return strict provider JSON schema for typed, upsert-only semantic repair."""

    nullable_string = {"anyOf": [{"type": "string", "minLength": 1}, {"type": "null"}]}
    entrypoint_schema = {
        "type": "object",
        "properties": {
            "obligation_id": {"type": "string", "minLength": 1},
            "kind": {"type": "string", "enum": ["cli", "web", "api", "library"]},
            "applicability": {
                "type": "string",
                "enum": ["required", "optional", "not_applicable"],
            },
            "owner_task_id": nullable_string,
            "source_path": nullable_string,
            "runtime_path": nullable_string,
            "command": nullable_string,
        },
        "required": [
            "obligation_id",
            "kind",
            "applicability",
            "owner_task_id",
            "source_path",
            "runtime_path",
            "command",
        ],
        "additionalProperties": False,
    }
    behavior_schema = {
        "type": "object",
        "properties": {
            "invariant_id": {"type": "string", "minLength": 1},
            "statement": {"type": "string", "minLength": 1},
            "owner_task_id": {"type": "string", "minLength": 1},
            "consumer_task_ids": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "string", "minLength": 1},
            },
            "covered_obligation_ids": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "string", "minLength": 1},
            },
            "verification_examples": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "given": {"type": "string", "minLength": 1},
                        "when": {"type": "string", "minLength": 1},
                        "then": {"type": "string", "minLength": 1},
                    },
                    "required": ["given", "when", "then"],
                    "additionalProperties": False,
                },
            },
        },
        "required": [
            "invariant_id",
            "statement",
            "owner_task_id",
            "consumer_task_ids",
            "covered_obligation_ids",
            "verification_examples",
        ],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "base_candidate_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "diagnosis_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "artifact_upserts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "obligation_id": {"type": "string", "minLength": 1},
                        "path": {"type": "string", "minLength": 1},
                        "semantic_role": {
                            "type": "string",
                            "enum": ["source", "manifest", "test", "entrypoint", "config", "docs", "assets"],
                        },
                        "applicability": {
                            "type": "string",
                            "enum": ["required", "optional", "not_applicable"],
                        },
                        "owner_task_id": nullable_string,
                    },
                    "required": ["obligation_id", "path", "semantic_role", "applicability", "owner_task_id"],
                    "additionalProperties": False,
                },
            },
            "entrypoint_upserts": {"type": "array", "items": entrypoint_schema},
            "behavior_invariant_upserts": {"type": "array", "items": behavior_schema},
            "task_behavior_ref_replacements": {
                "type": "object",
                "additionalProperties": {"type": "array", "items": {"type": "string", "minLength": 1}},
            },
        },
        "required": [
            "base_candidate_hash",
            "diagnosis_hash",
            "artifact_upserts",
            "entrypoint_upserts",
            "behavior_invariant_upserts",
            "task_behavior_ref_replacements",
        ],
        "additionalProperties": False,
    }


__all__ = [
    "build_chief_engineer_semantic_repair_patch_schema",
    "compose_chief_engineer_semantic_repair",
    "load_chief_engineer_semantic_repair_candidate",
    "normalize_chief_engineer_portfolio_tool_arguments",
    "persist_chief_engineer_semantic_repair_candidate",
    "project_chief_engineer_semantic_repair_provider_context",
]
