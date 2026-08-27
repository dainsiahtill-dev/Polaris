"""Owner-scoped composition and persistence for CE semantic repair."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from typing import Any, cast, get_args

from polaris.cells.chief_engineer.blueprint.internal.semantic_repair_store import (
    ChiefEngineerSemanticRepairCandidateStore,
)
from polaris.cells.chief_engineer.blueprint.public.contracts import (
    ArtifactObligationV1,
    ArtifactSemanticRoleV1,
    ChiefEngineerBehaviorExampleV1,
    ChiefEngineerBehaviorInvariantV1,
    ChiefEngineerPortfolioStructuralRecoveryV1,
    ChiefEngineerPortfolioTaskV1,
    ChiefEngineerSemanticRepairCandidateV1,
    ChiefEngineerSemanticRepairDiagnosisV1,
    ChiefEngineerSemanticRepairOperationV1,
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
from ._portfolio import (
    _task_authorizes_completion_path,
    _task_expandable_scope_paths,
    project_chief_engineer_portfolio_delivery_depth_feasibility,
)


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


def _task_authorizes_artifact(
    *,
    task: ChiefEngineerPortfolioTaskV1,
    artifact: ArtifactObligationV1,
) -> bool:
    return bool(
        _ce_artifact_role_matches_path(
            semantic_role=artifact.semantic_role,
            path=artifact.path,
            allowed_source_suffixes=task.allowed_source_suffixes,
        )
        and (
            _task_authorizes_completion_path(task=task, path=artifact.path)
            or _task_delegates_artifact(
                task=task,
                semantic_role=artifact.semantic_role,
                path=artifact.path,
            )
        )
    )


def _normalize_unique_artifact_owner(
    artifact: ArtifactObligationV1,
    *,
    tasks: tuple[ChiefEngineerPortfolioTaskV1, ...],
) -> ArtifactObligationV1:
    """Repair only a wrong known owner when immutable PM authority is unique."""

    tasks_by_id = {task.task_id: task for task in tasks}
    owner = tasks_by_id.get(str(artifact.owner_task_id or ""))
    if owner is None or _task_authorizes_artifact(task=owner, artifact=artifact):
        return artifact
    authorized = tuple(task for task in tasks if _task_authorizes_artifact(task=task, artifact=artifact))
    if len(authorized) != 1:
        return artifact
    return ArtifactObligationV1(
        obligation_id=artifact.obligation_id,
        path=artifact.path,
        semantic_role=artifact.semantic_role,
        applicability=artifact.applicability,
        owner_task_id=authorized[0].task_id,
    )


def _hash(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalize_depth_artifact_split_upserts(
    artifacts: tuple[ArtifactObligationV1, ...],
    *,
    baseline_rows: list[dict[str, Any]],
    occupied_ids: set[str],
    tasks: tuple[ChiefEngineerPortfolioTaskV1, ...],
) -> tuple[tuple[ArtifactObligationV1, ...], dict[str, tuple[str, ...]], tuple[str, ...]]:
    """Normalize an unambiguous provider group-id reuse into an artifact split.

    During delivery-depth repair providers commonly reuse the existing
    obligation id while proposing a second same-owner/same-role physical file.
    Moving the old obligation would violate immutable semantic identity, but
    preserving it and minting a deterministic id for the new path is safe when
    the entire semantic profile is unchanged and the new path is independently
    authorized.  Ambiguous shapes remain untouched for the immutable guard to
    reject.
    """

    baseline_by_id = {
        str(row.get("obligation_id") or ""): row for row in baseline_rows if str(row.get("obligation_id") or "")
    }
    baseline_paths = {str(row.get("path") or "") for row in baseline_rows}
    tasks_by_id = {task.task_id: task for task in tasks}
    normalized: list[ArtifactObligationV1] = []
    remap: dict[str, list[str]] = {}
    minted_ids: list[str] = []
    for artifact in artifacts:
        baseline = baseline_by_id.get(artifact.obligation_id)
        if baseline is None or str(baseline.get("path") or "") == artifact.path:
            normalized.append(artifact)
            continue
        # ``applicability`` is intentionally mutable and is not part of the
        # immutable artifact identity enforced below.  A depth repair may turn
        # an optional baseline test into a *new* required physical test while
        # preserving the optional row.  Requiring equal applicability here
        # skipped the safe split and sent the exact L3-24 r51 payload to the
        # immutable-path guard instead.
        same_immutable_profile = (
            baseline.get("semantic_role") == artifact.semantic_role
            and baseline.get("owner_task_id") == artifact.owner_task_id
        )
        owner = tasks_by_id.get(str(artifact.owner_task_id or ""))
        if (
            not same_immutable_profile
            or artifact.path in baseline_paths
            or owner is None
            or not _task_authorizes_artifact(task=owner, artifact=artifact)
        ):
            normalized.append(artifact)
            continue
        identity = json.dumps(
            {
                "group_id": artifact.obligation_id,
                "path": artifact.path,
                "semantic_role": artifact.semantic_role,
                "applicability": artifact.applicability,
                "owner_task_id": artifact.owner_task_id,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        digest = hashlib.sha256(identity).hexdigest()
        minted_id = ""
        for width in range(16, len(digest) + 1, 4):
            candidate_id = f"artifact-normalized-{digest[:width]}"
            if candidate_id not in occupied_ids:
                minted_id = candidate_id
                break
        if not minted_id:  # pragma: no cover - cryptographic collision guard.
            raise ValueError(f"cannot mint a unique artifact obligation id for {artifact.obligation_id}")
        occupied_ids.add(minted_id)
        minted_ids.append(minted_id)
        remap.setdefault(artifact.obligation_id, [artifact.obligation_id]).append(minted_id)
        normalized.append(
            ArtifactObligationV1(
                obligation_id=minted_id,
                path=artifact.path,
                semantic_role=artifact.semantic_role,
                applicability=artifact.applicability,
                owner_task_id=artifact.owner_task_id,
            )
        )
    return (
        tuple(normalized),
        {key: tuple(values) for key, values in remap.items()},
        tuple(minted_ids),
    )


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


_MANIFEST_ARTIFACT_BASENAMES = frozenset(
    {
        "cargo.toml",
        "cmakelists.txt",
        "go.mod",
        "package.json",
        "pom.xml",
        "pyproject.toml",
        "requirements.txt",
    }
)
_DOCUMENTATION_ARTIFACT_SUFFIXES = frozenset({".adoc", ".md", ".mdx", ".rst"})
_ARTIFACT_SEMANTIC_ROLES = frozenset(get_args(ArtifactSemanticRoleV1))


def _infer_artifact_semantic_role(*, path: str, entrypoint_paths: frozenset[str]) -> str | None:
    normalized = str(path or "").strip().replace("\\", "/")
    if not normalized:
        return None
    if normalized in entrypoint_paths:
        return "entrypoint"
    basename = normalized.rsplit("/", 1)[-1].lower()
    if basename in _MANIFEST_ARTIFACT_BASENAMES:
        return "manifest"
    if _ce_artifact_role_matches_path(semantic_role="test", path=normalized):
        return "test"
    if any(basename.endswith(suffix) for suffix in _DOCUMENTATION_ARTIFACT_SUFFIXES):
        return "docs"
    if _is_ce_production_source_path(normalized):
        return "source"
    return None


def _classify_interface_declaration(row: Mapping[str, Any]) -> str:
    keys = set(row)
    provider = {"symbol", "owner_task_id", "path"}.issubset(keys)
    consumer = {"consumer_task_id", "provider_symbol"}.issubset(keys)
    if provider == consumer:
        raise _UnsafePortfolioStructuralRecoveryError("ambiguous interface declaration")
    return "provider_declarations" if provider else "consumer_declarations"


_BEHAVIOR_INVARIANT_REQUIRED_KEYS = frozenset(
    {
        "invariant_id",
        "owner_task_id",
        "consumer_task_ids",
        "covered_obligation_ids",
        "statement",
        "verification_examples",
    }
)
_BEHAVIOR_INVARIANT_DISCRIMINATOR_KEYS = _BEHAVIOR_INVARIANT_REQUIRED_KEYS - {"owner_task_id"}
_TASK_PLAN_ARRAY_FIELDS = frozenset({"behavior_invariant_refs", "scope_for_apply", "risk_flags"})


def _unwrap_provider_item_array(name: str, value: object) -> list[Any]:
    """Unwrap a provider's ``{"item": ...}`` encoding for known array fields.

    Provider tool transports may collapse a one-element string array to a
    scalar ``item``.  Accept that exact lossless shape; other JSON scalars
    remain ambiguous and fail closed.
    """

    if isinstance(value, list):
        return deepcopy(value)
    if not isinstance(value, Mapping) or set(value) != {"item"}:
        raise _UnsafePortfolioStructuralRecoveryError(f"{name} is not an item-wrapped array")
    item = value["item"]
    if isinstance(item, list):
        return deepcopy(item)
    if isinstance(item, Mapping):
        return [deepcopy(dict(item))]
    if isinstance(item, str):
        return [item]
    raise _UnsafePortfolioStructuralRecoveryError(f"{name}.item is not an array member")


def _normalize_behavior_invariant_row(
    name: str,
    value: object,
    *,
    require_shape: bool = True,
) -> tuple[dict[str, Any], bool]:
    row = _mapping(name, value)
    if require_shape and not _BEHAVIOR_INVARIANT_REQUIRED_KEYS.issubset(row):
        raise _UnsafePortfolioStructuralRecoveryError(f"{name} is not a behavior invariant")
    changed = False
    for field in ("consumer_task_ids", "covered_obligation_ids", "verification_examples"):
        if field not in row:
            continue
        current = row.get(field)
        normalized = _unwrap_provider_item_array(f"{name}.{field}", current)
        if normalized != current:
            row[field] = normalized
            changed = True
    return row, changed


def _append_unique_behavior_invariant(
    invariants: list[dict[str, Any]],
    incoming: dict[str, Any],
) -> bool:
    invariant_id = incoming.get("invariant_id")
    if not isinstance(invariant_id, str) or not invariant_id.strip():
        raise _UnsafePortfolioStructuralRecoveryError("behavior invariant has no stable id")
    matches = [row for row in invariants if row.get("invariant_id") == invariant_id]
    if not matches:
        invariants.append(incoming)
        return True
    if len(matches) != 1 or matches[0] != incoming:
        raise _UnsafePortfolioStructuralRecoveryError(f"conflicting behavior invariant {invariant_id}")
    return False


def _expand_obligation_refs(
    values: object,
    *,
    remap: Mapping[str, tuple[str, ...]],
) -> object:
    """Expand one provider group-id reference into canonical row ids."""

    if not isinstance(values, list):
        return values
    expanded: list[object] = []
    seen: set[str] = set()
    for value in values:
        replacements: tuple[object, ...] = remap.get(value, (value,)) if isinstance(value, str) else (value,)
        for replacement in replacements:
            if isinstance(replacement, str):
                if replacement in seen:
                    continue
                seen.add(replacement)
            expanded.append(replacement)
    return expanded


def _normalize_shared_artifact_obligation_groups(
    payload: dict[str, Any],
) -> tuple[dict[str, tuple[str, ...]], tuple[str, ...]]:
    """Split safe provider artifact group labels into unique obligation ids.

    Some providers use one ``obligation_id`` as a semantic group label for
    several physical files.  The authoritative completion contract instead
    requires one globally unique row id.  The conversion is safe only when
    every grouped row has a distinct path and identical owner, role, and
    applicability.  Every verifier and shared-behavior reference is expanded
    in the same transaction; ambiguous groups remain fail-closed.
    """

    completion_raw = payload.get("project_completion_contract")
    if not isinstance(completion_raw, Mapping):
        return {}, ()
    completion = _mapping("project_completion_contract", completion_raw)
    obligations_raw = completion.get("obligations")
    if not isinstance(obligations_raw, Mapping):
        return {}, ()
    obligations = _mapping("project_completion_contract.obligations", obligations_raw)
    artifacts = _rows(
        "project_completion_contract.obligations.artifacts",
        obligations.get("artifacts", []),
    )
    entrypoints = _rows(
        "project_completion_contract.obligations.entrypoints",
        obligations.get("entrypoints", []),
    )
    verification = _rows(
        "project_completion_contract.obligations.verification",
        obligations.get("verification", []),
    )
    artifact_groups: dict[str, list[dict[str, Any]]] = {}
    for row in artifacts:
        obligation_id = row.get("obligation_id")
        if not isinstance(obligation_id, str) or not obligation_id:
            continue
        artifact_groups.setdefault(obligation_id, []).append(row)
    duplicate_groups = {obligation_id: rows for obligation_id, rows in artifact_groups.items() if len(rows) > 1}
    if not duplicate_groups:
        return {}, ()

    entrypoint_ids = {str(row.get("obligation_id") or "") for row in entrypoints if str(row.get("obligation_id") or "")}
    verification_by_id: dict[str, list[dict[str, Any]]] = {}
    for row in verification:
        obligation_id = str(row.get("obligation_id") or "")
        if obligation_id:
            verification_by_id.setdefault(obligation_id, []).append(row)
    occupied_ids = {
        str(row.get("obligation_id") or "")
        for row in (*artifacts, *entrypoints, *verification)
        if str(row.get("obligation_id") or "")
    }
    normalized_rows_by_identity: dict[int, dict[str, Any]] = {}
    remap: dict[str, tuple[str, ...]] = {}
    minted_ids: list[str] = []
    for obligation_id, group in sorted(duplicate_groups.items()):
        if obligation_id in entrypoint_ids:
            raise _UnsafePortfolioStructuralRecoveryError(
                f"artifact obligation group collides with another obligation category: {obligation_id}"
            )
        paths = [str(row.get("path") or "") for row in group]
        if any(not path for path in paths) or len(paths) != len(set(paths)):
            raise _UnsafePortfolioStructuralRecoveryError(
                "existing semantic rows contain invalid or duplicate obligation_id: "
                f"artifact group path is ambiguous for {obligation_id}"
            )
        semantic_profiles = {
            (
                row.get("semantic_role"),
                row.get("applicability"),
                row.get("owner_task_id"),
            )
            for row in group
        }
        if len(semantic_profiles) != 1:
            raise _UnsafePortfolioStructuralRecoveryError(
                f"artifact obligation group has ambiguous semantic authority: {obligation_id}"
            )
        colliding_verifiers = verification_by_id.get(obligation_id, [])
        if colliding_verifiers:
            _semantic_role, applicability, owner_task_id = next(iter(semantic_profiles))
            safe_covering_verifier_collision = all(
                row.get("owner_task_id") == owner_task_id
                and row.get("applicability") == applicability
                and obligation_id in (row.get("covers_obligation_ids") or [])
                for row in colliding_verifiers
            )
            if not safe_covering_verifier_collision:
                raise _UnsafePortfolioStructuralRecoveryError(
                    f"artifact obligation group collides with another obligation category: {obligation_id}"
                )

        normalized_ids: list[str] = []
        for index, row in enumerate(sorted(group, key=lambda item: str(item.get("path") or ""))):
            normalized_row = dict(row)
            normalized_id = obligation_id
            if index or colliding_verifiers:
                identity = json.dumps(
                    {
                        "group_id": obligation_id,
                        "path": row.get("path"),
                        "semantic_role": row.get("semantic_role"),
                        "applicability": row.get("applicability"),
                        "owner_task_id": row.get("owner_task_id"),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
                digest = hashlib.sha256(identity).hexdigest()
                for width in range(16, len(digest) + 1, 4):
                    candidate_id = f"artifact-normalized-{digest[:width]}"
                    if candidate_id not in occupied_ids:
                        normalized_id = candidate_id
                        break
                else:  # pragma: no cover - cryptographic collision guard.
                    raise _UnsafePortfolioStructuralRecoveryError(
                        f"cannot mint a unique artifact obligation id for {obligation_id}"
                    )
                occupied_ids.add(normalized_id)
                minted_ids.append(normalized_id)
                normalized_row["obligation_id"] = normalized_id
            normalized_ids.append(normalized_id)
            normalized_rows_by_identity[id(row)] = normalized_row
        remap[obligation_id] = tuple(normalized_ids)

    obligations["artifacts"] = [normalized_rows_by_identity.get(id(row), row) for row in artifacts]
    for row in verification:
        row["covers_obligation_ids"] = _expand_obligation_refs(
            row.get("covers_obligation_ids"),
            remap=remap,
        )
    obligations["verification"] = verification
    completion["obligations"] = obligations
    payload["project_completion_contract"] = completion

    construction_raw = payload.get("construction_plan")
    if isinstance(construction_raw, Mapping):
        construction = _mapping("construction_plan", construction_raw)
        behavior_raw = construction.get("shared_behavior_contract")
        if isinstance(behavior_raw, Mapping):
            behavior = _mapping("shared_behavior_contract", behavior_raw)
            invariants = _rows(
                "shared_behavior_contract.invariants",
                behavior.get("invariants", []),
            )
            for invariant in invariants:
                invariant["covered_obligation_ids"] = _expand_obligation_refs(
                    invariant.get("covered_obligation_ids"),
                    remap=remap,
                )
            behavior["invariants"] = invariants
            construction["shared_behavior_contract"] = behavior
            payload["construction_plan"] = construction
    return remap, tuple(minted_ids)


def _normalize_lifted_task_plan(name: str, value: object) -> dict[str, Any]:
    row = _mapping(name, value)
    if "behavior_invariant_refs" not in row:
        raise _UnsafePortfolioStructuralRecoveryError(f"{name} is not a known lifted task plan")
    for field in _TASK_PLAN_ARRAY_FIELDS:
        if field in row:
            row[field] = _unwrap_provider_item_array(f"{name}.{field}", row[field])
    return row


def _lifted_task_plan_is_redundant(*, lifted: Mapping[str, Any], canonical: Mapping[str, Any]) -> bool:
    for field, lifted_value in lifted.items():
        canonical_value = canonical.get(field)
        if not isinstance(lifted_value, list) or not isinstance(canonical_value, list):
            return False
        if any(item not in canonical_value for item in lifted_value):
            return False
    return True


def normalize_chief_engineer_portfolio_tool_arguments(
    payload: Mapping[str, Any],
    *,
    authoritative_task_ids: tuple[str, ...] = (),
) -> ChiefEngineerPortfolioStructuralRecoveryV1:
    """Relocate known CE portfolio members without inventing semantic content.

    Some provider tool streams preserve every value but lift nested object
    members to the root or emit an array ``item`` beside its owning array.
    This normalizer is intentionally narrow and transactional: ambiguity or a
    destination conflict returns the original payload unchanged.
    """

    if not isinstance(authoritative_task_ids, tuple) or any(
        not isinstance(task_id, str) or not task_id.strip() for task_id in authoritative_task_ids
    ):
        raise TypeError("authoritative_task_ids must contain non-empty strings")
    if len(set(authoritative_task_ids)) != len(authoritative_task_ids):
        raise ValueError("authoritative_task_ids must not contain duplicates")
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

        task_plans = _mapping("task_plans", construction.get("task_plans", {}))
        construction["task_plans"] = task_plans
        moved_lifted_task_plan = False
        removed_lifted_task_plan = False
        for task_id, lifted_raw in tuple(construction.items()):
            if (
                task_id
                in {
                    "item",
                    "project_interface_contract",
                    "shared_behavior_contract",
                    "task_plans",
                }
                or not isinstance(lifted_raw, Mapping)
                or "behavior_invariant_refs" not in lifted_raw
            ):
                continue
            lifted = _normalize_lifted_task_plan(f"construction_plan.{task_id}", lifted_raw)
            canonical_raw = task_plans.get(task_id)
            if canonical_raw is None:
                task_plans[task_id] = lifted
                moved_lifted_task_plan = True
            else:
                canonical = _mapping(f"task_plans.{task_id}", canonical_raw)
                if not _lifted_task_plan_is_redundant(lifted=lifted, canonical=canonical):
                    raise _UnsafePortfolioStructuralRecoveryError(f"conflicting lifted task plan {task_id}")
                removed_lifted_task_plan = True
            del construction[task_id]
        if moved_lifted_task_plan:
            codes.append("move_lifted_task_plans")
        if removed_lifted_task_plan:
            codes.append("remove_redundant_lifted_task_plan")

        behavior_raw = construction.get("shared_behavior_contract")
        if isinstance(behavior_raw, Mapping):
            behavior = _mapping("shared_behavior_contract", behavior_raw)
            source_invariants = _rows("shared_behavior_contract.invariants", behavior.get("invariants", []))
            invariants: list[dict[str, Any]] = []
            unwrapped_invariant_arrays = False
            for index, source_invariant in enumerate(source_invariants):
                invariant, changed = _normalize_behavior_invariant_row(
                    f"shared_behavior_contract.invariants[{index}]",
                    source_invariant,
                    require_shape=False,
                )
                _append_unique_behavior_invariant(invariants, invariant)
                unwrapped_invariant_arrays = unwrapped_invariant_arrays or changed

            if "item" in behavior:
                raw_items = behavior["item"] if isinstance(behavior["item"], list) else [behavior["item"]]
                for index, raw_item in enumerate(raw_items):
                    invariant, changed = _normalize_behavior_invariant_row(
                        f"shared_behavior_contract.item[{index}]",
                        raw_item,
                    )
                    _append_unique_behavior_invariant(invariants, invariant)
                    unwrapped_invariant_arrays = unwrapped_invariant_arrays or changed
                del behavior["item"]
                codes.append("move_shared_behavior_item_to_invariants")

            if "item" in construction and isinstance(construction["item"], list):
                remaining_items: list[Any] = []
                moved_invariant = False
                for index, raw_item in enumerate(construction["item"]):
                    item_keys = set(raw_item) if isinstance(raw_item, Mapping) else set()
                    invariant_key_overlap = item_keys.intersection(_BEHAVIOR_INVARIANT_DISCRIMINATOR_KEYS)
                    if not invariant_key_overlap:
                        # Provider streams can lift values from several sibling
                        # arrays into one ``construction_plan.item`` list.  Keep
                        # non-invariant rows byte-for-byte instead of guessing
                        # whether they are phases, files, or deliverables.
                        remaining_items.append(raw_item)
                        continue
                    invariant, changed = _normalize_behavior_invariant_row(
                        f"construction_plan.item[{index}]",
                        raw_item,
                    )
                    _append_unique_behavior_invariant(invariants, invariant)
                    moved_invariant = True
                    unwrapped_invariant_arrays = unwrapped_invariant_arrays or changed
                if moved_invariant:
                    if remaining_items:
                        construction["item"] = remaining_items
                    else:
                        del construction["item"]
                    codes.append("move_construction_items_to_behavior_invariants")

            behavior["invariants"] = invariants
            construction["shared_behavior_contract"] = behavior
            if unwrapped_invariant_arrays:
                codes.append("unwrap_behavior_invariant_array_items")

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

        behavior_raw = construction.get("shared_behavior_contract")
        if isinstance(behavior_raw, Mapping):
            behavior = _mapping("shared_behavior_contract", behavior_raw)
            invariants = _rows("shared_behavior_contract.invariants", behavior.get("invariants", []))
            task_refs_by_invariant: dict[str, set[str]] = {}
            for task_id, task_plan_raw in task_plans.items():
                if not isinstance(task_id, str) or not isinstance(task_plan_raw, Mapping):
                    continue
                refs = task_plan_raw.get("behavior_invariant_refs")
                if not isinstance(refs, list):
                    continue
                for invariant_id in refs:
                    if isinstance(invariant_id, str) and invariant_id:
                        task_refs_by_invariant.setdefault(invariant_id, set()).add(task_id)
            removed_owner_ref = False
            rebound_owner_only_ref = False
            removed_task_local_invariant_ids: set[str] = set()
            retained_invariants: list[dict[str, Any]] = []
            for invariant in invariants:
                invariant_id = invariant.get("invariant_id")
                owner_task_id = invariant.get("owner_task_id")
                consumer_task_ids = invariant.get("consumer_task_ids")
                if not isinstance(owner_task_id, str) or not isinstance(consumer_task_ids, list):
                    retained_invariants.append(invariant)
                    continue
                if owner_task_id not in consumer_task_ids:
                    retained_invariants.append(invariant)
                    continue
                remaining_consumers = [task_id for task_id in consumer_task_ids if task_id != owner_task_id]
                if not remaining_consumers:
                    reverse_task_refs = set(task_refs_by_invariant.get(str(invariant_id or ""), set()))
                    remaining_consumers = sorted(task_id for task_id in reverse_task_refs if task_id != owner_task_id)
                    if not remaining_consumers:
                        if (
                            isinstance(invariant_id, str)
                            and invariant_id
                            and (
                                reverse_task_refs == {owner_task_id}
                                or (not reverse_task_refs and authoritative_task_ids == (owner_task_id,))
                            )
                        ):
                            # The row is proven task-local: its declared owner is
                            # its sole consumer and either the same owner is its
                            # sole reverse task-plan reference or immutable PM
                            # authority proves this is a one-task portfolio. It
                            # has no place in a cross-task shared contract, so
                            # remove the advisory row and its matching shared ref
                            # without inventing a sibling consumer or changing PM
                            # authority.
                            removed_task_local_invariant_ids.add(invariant_id)
                            continue
                        raise _UnsafePortfolioStructuralRecoveryError(
                            "behavior invariant has no consumer after removing its owner"
                        )
                    rebound_owner_only_ref = True
                else:
                    removed_owner_ref = True
                invariant["consumer_task_ids"] = remaining_consumers
                retained_invariants.append(invariant)
            if removed_task_local_invariant_ids:
                for task_plan in task_plans.values():
                    if not isinstance(task_plan, dict):
                        continue
                    refs = task_plan.get("behavior_invariant_refs")
                    if not isinstance(refs, list):
                        continue
                    task_plan["behavior_invariant_refs"] = [
                        ref for ref in refs if ref not in removed_task_local_invariant_ids
                    ]
            if removed_owner_ref or rebound_owner_only_ref or removed_task_local_invariant_ids:
                behavior["invariants"] = retained_invariants
                construction["shared_behavior_contract"] = behavior
            if removed_owner_ref:
                codes.append("remove_behavior_owner_from_consumers")
            if rebound_owner_only_ref:
                codes.append("rebind_behavior_consumers_from_task_refs")
            if removed_task_local_invariant_ids:
                codes.append("remove_task_local_invariant_from_shared_contract")

        completion_raw = working.get("project_completion_contract")
        if isinstance(completion_raw, Mapping):
            completion = _mapping("project_completion_contract", completion_raw)
            obligations_raw = completion.get("obligations")
            if isinstance(obligations_raw, Mapping):
                obligations = _mapping("project_completion_contract.obligations", obligations_raw)
                artifacts = _rows(
                    "project_completion_contract.obligations.artifacts",
                    obligations.get("artifacts", []),
                )
                entrypoints = _rows(
                    "project_completion_contract.obligations.entrypoints",
                    obligations.get("entrypoints", []),
                )
                verification = _rows(
                    "project_completion_contract.obligations.verification",
                    obligations.get("verification", []),
                )
                artifact_group_remap, _minted_artifact_ids = _normalize_shared_artifact_obligation_groups(working)
                if artifact_group_remap:
                    codes.append("split_shared_artifact_obligation_ids")
                    completion = _mapping(
                        "project_completion_contract",
                        working["project_completion_contract"],
                    )
                    obligations = _mapping(
                        "project_completion_contract.obligations",
                        completion["obligations"],
                    )
                    artifacts = _rows(
                        "project_completion_contract.obligations.artifacts",
                        obligations.get("artifacts", []),
                    )
                    entrypoints = _rows(
                        "project_completion_contract.obligations.entrypoints",
                        obligations.get("entrypoints", []),
                    )
                    verification = _rows(
                        "project_completion_contract.obligations.verification",
                        obligations.get("verification", []),
                    )
                entrypoint_paths = frozenset(
                    str(value).strip().replace("\\", "/")
                    for row in entrypoints
                    for value in (row.get("source_path"), row.get("runtime_path"))
                    if isinstance(value, str) and value.strip()
                )
                inferred_missing_role = False
                inferred_invalid_role = False
                for artifact in artifacts:
                    semantic_role = artifact.get("semantic_role")
                    if semantic_role in _ARTIFACT_SEMANTIC_ROLES:
                        continue
                    role = _infer_artifact_semantic_role(
                        path=str(artifact.get("path") or ""),
                        entrypoint_paths=entrypoint_paths,
                    )
                    if role is None:
                        raise _UnsafePortfolioStructuralRecoveryError("artifact semantic role is ambiguous")
                    artifact["semantic_role"] = role
                    if "semantic_role" in artifact and semantic_role is not None:
                        inferred_invalid_role = True
                    else:
                        inferred_missing_role = True
                if inferred_missing_role or inferred_invalid_role:
                    obligations["artifacts"] = artifacts
                    completion["obligations"] = obligations
                    working["project_completion_contract"] = completion
                if inferred_missing_role:
                    codes.append("infer_missing_artifact_semantic_roles")
                if inferred_invalid_role:
                    codes.append("infer_invalid_artifact_semantic_roles")

                completion_obligation_ids = {
                    obligation_id
                    for row in (*artifacts, *entrypoints, *verification)
                    if isinstance((obligation_id := row.get("obligation_id")), str)
                    and obligation_id
                    and obligation_id == obligation_id.strip()
                }
                verification_ids_by_covered_alias: dict[str, set[str]] = {}
                for row in verification:
                    verification_id = row.get("obligation_id")
                    covered_aliases = row.get("covers_obligation_ids")
                    if not isinstance(verification_id, str) or not isinstance(covered_aliases, list):
                        continue
                    for covered_alias in covered_aliases:
                        if isinstance(covered_alias, str) and covered_alias:
                            verification_ids_by_covered_alias.setdefault(covered_alias, set()).add(verification_id)
                behavior_raw = construction.get("shared_behavior_contract")
                if completion_obligation_ids and isinstance(behavior_raw, Mapping):
                    behavior = _mapping("shared_behavior_contract", behavior_raw)
                    invariants = _rows("shared_behavior_contract.invariants", behavior.get("invariants", []))
                    removed_unknown_ref = False
                    mapped_verifier_alias = False
                    consumed_behavior_aliases: set[str] = set()
                    for invariant in invariants:
                        covered_ids = invariant.get("covered_obligation_ids")
                        if not isinstance(covered_ids, list) or not covered_ids:
                            continue
                        if not all(
                            isinstance(obligation_id, str) and obligation_id and obligation_id == obligation_id.strip()
                            for obligation_id in covered_ids
                        ):
                            continue
                        retained_ids: list[str] = []
                        mapped_this_invariant = False
                        removed_this_invariant = False
                        for obligation_id in covered_ids:
                            replacements = (
                                (obligation_id,)
                                if obligation_id in completion_obligation_ids
                                else tuple(sorted(verification_ids_by_covered_alias.get(obligation_id, set())))
                            )
                            if replacements:
                                mapped_this_invariant = (
                                    mapped_this_invariant or obligation_id not in completion_obligation_ids
                                )
                                if obligation_id not in completion_obligation_ids:
                                    consumed_behavior_aliases.add(obligation_id)
                                for replacement in replacements:
                                    if replacement not in retained_ids:
                                        retained_ids.append(replacement)
                            else:
                                removed_this_invariant = True
                        if len(retained_ids) == len(covered_ids) and retained_ids == covered_ids:
                            continue
                        if not retained_ids:
                            # Leave an all-unknown set intact for the semantic
                            # gate; do not discard independent safe recovery.
                            continue
                        invariant["covered_obligation_ids"] = retained_ids
                        mapped_verifier_alias = mapped_verifier_alias or mapped_this_invariant
                        removed_unknown_ref = removed_unknown_ref or removed_this_invariant
                    if removed_unknown_ref or mapped_verifier_alias:
                        behavior["invariants"] = invariants
                        construction["shared_behavior_contract"] = behavior
                    if removed_unknown_ref:
                        codes.append("remove_unknown_behavior_obligation_refs")
                    if mapped_verifier_alias:
                        codes.append("map_behavior_obligation_aliases_to_verification")
                    removed_verifier_alias = False
                    if consumed_behavior_aliases:
                        for verifier in verification:
                            covered_ids = verifier.get("covers_obligation_ids")
                            if not isinstance(covered_ids, list) or not covered_ids:
                                continue
                            retained_ids = [
                                obligation_id
                                for obligation_id in covered_ids
                                if obligation_id not in consumed_behavior_aliases
                            ]
                            # Do not turn an alias-only verifier into a hollow
                            # verifier. It remains invalid so the strict contract
                            # gate can reject it. The paired recovery is safe only
                            # when canonical artifact/entrypoint coverage remains.
                            if retained_ids and retained_ids != covered_ids:
                                verifier["covers_obligation_ids"] = retained_ids
                                removed_verifier_alias = True
                    if removed_verifier_alias:
                        obligations["verification"] = verification
                        completion["obligations"] = obligations
                        working["project_completion_contract"] = completion
                        codes.append("remove_consumed_verifier_coverage_aliases")
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


def _introduces_duplicate_value(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    *,
    field: str,
) -> bool:
    """Whether an incremental patch increases an already-duplicate value count."""

    before_counts: dict[str, int] = {}
    after_counts: dict[str, int] = {}
    for row in before:
        value = str(row.get(field) or "")
        before_counts[value] = before_counts.get(value, 0) + 1
    for row in after:
        value = str(row.get(field) or "")
        after_counts[value] = after_counts.get(value, 0) + 1
    return any(count > 1 and count > before_counts.get(value, 0) for value, count in after_counts.items())


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
        changed = [field for field in identity_fields if baseline.get(field) != projected.get(field)]
        if changed:
            raise ValueError(
                f"semantic repair cannot mutate immutable semantic identity: {id_field}={row_id!r}:fields={changed!r}"
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
    current_verification = _rows("verification", sections["verification"])
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
            "primary_language": task.primary_language,
            "allowed_source_suffixes": list(task.allowed_source_suffixes),
            "entrypoint_kind_authority": task.entrypoint_kind_authority,
            "delegated_artifact_roles": sorted(_delegated_artifact_roles(task)),
        }
        for task in tasks
    }
    available_exact_target_paths = sorted(
        {path for row in task_authority.values() for path in cast(list[str], row["unused_exact_target_paths"])}
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
        {path for row in task_authority.values() for path in cast(list[str], row["expandable_scope_paths"])}
    )
    delegated_topology_task_ids = sorted(
        task.task_id for task in tasks if task.topology_authority == "chief_engineer" and task.required_source_kinds
    )
    delegated_artifact_roles = sorted({role for task in tasks for role in _delegated_artifact_roles(task)})
    expandable_test_scope_paths = sorted(
        {
            path
            for task in tasks
            if task.allowed_source_suffixes
            for path in _task_expandable_scope_paths(task)
            if "test" in _delegated_artifact_roles(task)
            or "tests" in _delegated_artifact_roles(task)
            or _is_ce_test_topology_path(f"{path}/test_file{task.allowed_source_suffixes[0]}")
        }
    )
    expandable_prod_scope_paths = sorted(
        {
            path
            for task in tasks
            if task.allowed_source_suffixes
            for path in _task_expandable_scope_paths(task)
            if not set(str(path).lower().replace("\\", "/").split("/")).intersection({"docs", "doc"})
        }
    )
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
    delivery_depth_feasibility = (
        project_chief_engineer_portfolio_delivery_depth_feasibility(
            candidate.candidate,
            tasks=tasks,
        )
        if depth_artifact_repair
        else None
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
        "upsert_identity_policy": {},
        "current": {},
    }
    if delivery_depth_feasibility is not None:
        projection["delivery_depth_feasibility"] = delivery_depth_feasibility
    current = cast(dict[str, Any], projection["current"])
    identity_policy = cast(dict[str, Any], projection["upsert_identity_policy"])
    allowed = set(diagnosis.allowed_operations)
    if "artifact_upsert" in allowed:
        current["artifacts"] = deepcopy(sections["artifacts"])
        artifact_identity_fields = ("path", "semantic_role", "owner_task_id")
        identity_policy["artifact_upsert"] = {
            "id_field": "obligation_id",
            "immutable_fields": list(artifact_identity_fields),
            "existing_identities": {
                str(row["obligation_id"]): {field: row.get(field) for field in artifact_identity_fields}
                for row in current_artifacts
                if isinstance(row.get("obligation_id"), str) and str(row["obligation_id"]).strip()
            },
            "new_identity_rule": (
                "If the desired path, semantic_role, or owner_task_id differs from an existing obligation, "
                "mint a new unique obligation_id and leave the existing row unchanged."
            ),
        }
    if "entrypoint_upsert" in allowed:
        current["artifacts"] = deepcopy(sections["artifacts"])
        current["entrypoints"] = deepcopy(sections["entrypoints"])
        entrypoint_identity_fields = ("kind", "source_path", "runtime_path", "owner_task_id")
        identity_policy["entrypoint_upsert"] = {
            "id_field": "obligation_id",
            "immutable_fields": list(entrypoint_identity_fields),
            "existing_identities": {
                str(row["obligation_id"]): {field: row.get(field) for field in entrypoint_identity_fields}
                for row in current_entrypoints
                if isinstance(row.get("obligation_id"), str) and str(row["obligation_id"]).strip()
            },
            "new_identity_rule": (
                "If kind, source_path, runtime_path, or owner_task_id differs from an existing obligation, "
                "mint a new unique obligation_id and use the authorized replacement protocol."
            ),
        }
        projection["removable_entrypoint_obligation_ids"] = sorted(
            {
                str(row["obligation_id"])
                for row in current_entrypoints
                if isinstance(row.get("obligation_id"), str) and str(row["obligation_id"]).strip()
            }
        )
        projection["entrypoint_replacement_rule"] = (
            "To replace a diagnosed invalid entrypoint under a new obligation_id, list the exact obsolete "
            "obligation_id in entrypoint_remove_obligation_ids and include one same-owner same-kind "
            "entrypoint_upsert. Unknown ids and removals without a replacement fail closed."
        )
    if "behavior_invariant_upsert" in allowed:
        current["artifacts"] = deepcopy(sections["artifacts"])
        current["entrypoints"] = deepcopy(sections["entrypoints"])
        current["verification"] = deepcopy(sections["verification"])
        current["behavior_invariants"] = deepcopy(sections["behavior_invariants"])
        projection["allowed_completion_obligation_ids"] = sorted(
            {
                str(row["obligation_id"])
                for row in (*current_artifacts, *current_entrypoints, *current_verification)
                if isinstance(row.get("obligation_id"), str) and str(row["obligation_id"]).strip()
            }
        )
    if "task_behavior_ref_replace" in allowed:
        current["behavior_invariants"] = deepcopy(sections["behavior_invariants"])
        current["task_behavior_refs"] = deepcopy(sections["task_behavior_refs"])
    return projection


def bind_chief_engineer_semantic_repair_provider_patch(
    payload: Mapping[str, Any],
    *,
    candidate: ChiefEngineerSemanticRepairCandidateV1,
    diagnosis: ChiefEngineerSemanticRepairDiagnosisV1,
) -> tuple[ChiefEngineerSemanticRepairPatchV1, dict[str, Any]]:
    """Bind provider-authored semantic content to active server CAS authority.

    ``base_candidate_hash`` and ``diagnosis_hash`` identify the already active
    repair transaction; they are not semantic choices the provider is allowed
    to make.  Parse the exact provider envelope first so malformed hashes and
    unauthorized operation groups still fail closed, then replace only those
    two provenance echoes with the authoritative in-process identities.  The
    echoes remain first-class audit evidence instead of becoming execution
    authority or causing a useful typed patch to be discarded.
    """

    if diagnosis.candidate_hash != candidate.candidate_hash:
        raise ValueError("diagnosis candidate_hash does not match candidate")
    provider_patch = ChiefEngineerSemanticRepairPatchV1.from_provider_dict(
        payload,
        allowed_operations=diagnosis.allowed_operations,
    )
    patch = ChiefEngineerSemanticRepairPatchV1(
        base_candidate_hash=candidate.candidate_hash,
        diagnosis_hash=diagnosis.diagnosis_hash,
        artifact_upserts=provider_patch.artifact_upserts,
        entrypoint_upserts=provider_patch.entrypoint_upserts,
        entrypoint_remove_obligation_ids=provider_patch.entrypoint_remove_obligation_ids,
        behavior_invariant_upserts=provider_patch.behavior_invariant_upserts,
        task_behavior_ref_replacements=provider_patch.task_behavior_ref_replacements,
    )
    binding = {
        "schema_version": "chief_engineer.semantic_repair_provider_binding.v1",
        "authority_source": "active_semantic_repair_transaction",
        "provider_base_candidate_hash": provider_patch.base_candidate_hash,
        "provider_diagnosis_hash": provider_patch.diagnosis_hash,
        "bound_base_candidate_hash": candidate.candidate_hash,
        "bound_diagnosis_hash": diagnosis.diagnosis_hash,
        "base_candidate_hash_match": provider_patch.base_candidate_hash == candidate.candidate_hash,
        "diagnosis_hash_match": provider_patch.diagnosis_hash == diagnosis.diagnosis_hash,
    }
    return patch, binding


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
    allowed_operations = set(diagnosis.allowed_operations)
    unauthorized = set(patch.operations) - allowed_operations
    authorized = set(patch.operations).intersection(allowed_operations)
    if unauthorized and not authorized:
        raise ValueError(f"patch operations are not diagnosis-authorized: {sorted(unauthorized)}")

    # Provider output is evidence, not execution authority. A useful typed
    # repair may contain an extra operation group unrelated to the frozen
    # diagnosis (exact L3-22 r14: valid artifact depth repair plus redundant
    # entrypoint upserts). Execute only the diagnosis-authorized subset. If no
    # authorized work remains, the guard above still fails closed. Rebuilding
    # the patch also makes the receipt bind the effective operation set and any
    # deterministic owner correction rather than the untrusted provider payload.
    patch = ChiefEngineerSemanticRepairPatchV1(
        base_candidate_hash=patch.base_candidate_hash,
        diagnosis_hash=patch.diagnosis_hash,
        artifact_upserts=(
            tuple(_normalize_unique_artifact_owner(artifact, tasks=tasks) for artifact in patch.artifact_upserts)
            if "artifact_upsert" in allowed_operations
            else ()
        ),
        entrypoint_upserts=(patch.entrypoint_upserts if "entrypoint_upsert" in allowed_operations else ()),
        entrypoint_remove_obligation_ids=(
            patch.entrypoint_remove_obligation_ids if "entrypoint_upsert" in allowed_operations else ()
        ),
        behavior_invariant_upserts=(
            patch.behavior_invariant_upserts if "behavior_invariant_upsert" in allowed_operations else ()
        ),
        task_behavior_ref_replacements=(
            patch.task_behavior_ref_replacements if "task_behavior_ref_replace" in allowed_operations else {}
        ),
    )

    payload = deepcopy(dict(candidate.candidate))
    artifact_group_remap, normalized_artifact_ids = _normalize_shared_artifact_obligation_groups(payload)
    ambiguous_artifact_patch_ids = {
        artifact.obligation_id for artifact in patch.artifact_upserts if artifact.obligation_id in artifact_group_remap
    }
    if ambiguous_artifact_patch_ids:
        raise ValueError(
            "semantic repair artifact upsert targets a shared provider group id: "
            f"{sorted(ambiguous_artifact_patch_ids)!r}"
        )
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
    verification_rows = _rows("verification", obligations.get("verification"))
    baseline_artifact_ids = {str(row.get("obligation_id") or "") for row in artifact_baseline_rows}
    baseline_artifact_paths = {str(row.get("path") or "") for row in artifact_baseline_rows}
    depth_diagnostics = {
        "chief_engineer.delivery_depth.prod_files_below_minimum",
        "chief_engineer.delivery_depth.test_files_below_minimum",
    }
    depth_repair_active = bool(set(diagnosis.diagnostic_codes).intersection(depth_diagnostics))
    baseline_owner_rebound_ids: tuple[str, ...] = ()
    if depth_repair_active:
        # Normalize every frozen baseline artifact whose declared owner is
        # uniquely impossible under immutable PM/CE topology authority.  The
        # old implementation corrected only rows repeated by the provider,
        # forcing the model to waste tokens echoing already-present artifacts
        # and leaving omitted rows invisible to delivery-depth feasibility.
        normalized_baseline_rows: list[dict[str, Any]] = []
        rebound_ids: list[str] = []
        for row in artifact_baseline_rows:
            artifact = _artifact_from_row(row)
            normalized = _normalize_unique_artifact_owner(artifact, tasks=tasks)
            if normalized.owner_task_id != artifact.owner_task_id:
                rebound_ids.append(artifact.obligation_id)
                normalized_baseline_rows.append(normalized.to_dict())
            else:
                normalized_baseline_rows.append(row)
        artifact_baseline_rows = normalized_baseline_rows
        baseline_owner_rebound_ids = tuple(rebound_ids)
    if depth_repair_active:
        # One redundant physical-path obligation must not poison otherwise
        # useful depth repair work. Exact L3-22 r40: the provider proposed six
        # new production paths plus one new test path, but also emitted a fresh
        # obligation id for the already-owned ``main.go`` entrypoint. The old
        # all-or-nothing check discarded every useful path and pushed the next
        # repair turn into duplicate-path churn. Keep existing-id replacements
        # and genuinely new physical paths. If no useful upsert remains, retain
        # the original patch so the duplicate guard below still fails closed.
        useful_artifact_upserts = tuple(
            artifact
            for artifact in patch.artifact_upserts
            if artifact.obligation_id in baseline_artifact_ids or artifact.path not in baseline_artifact_paths
        )
        if useful_artifact_upserts and useful_artifact_upserts != patch.artifact_upserts:
            patch = ChiefEngineerSemanticRepairPatchV1(
                base_candidate_hash=patch.base_candidate_hash,
                diagnosis_hash=patch.diagnosis_hash,
                artifact_upserts=useful_artifact_upserts,
                entrypoint_upserts=patch.entrypoint_upserts,
                entrypoint_remove_obligation_ids=patch.entrypoint_remove_obligation_ids,
                behavior_invariant_upserts=patch.behavior_invariant_upserts,
                task_behavior_ref_replacements=patch.task_behavior_ref_replacements,
            )
    depth_split_remap: dict[str, tuple[str, ...]] = {}
    depth_split_minted_ids: tuple[str, ...] = ()
    if depth_repair_active and patch.artifact_upserts:
        normalized_upserts, depth_split_remap, depth_split_minted_ids = _normalize_depth_artifact_split_upserts(
            patch.artifact_upserts,
            baseline_rows=artifact_baseline_rows,
            occupied_ids={
                str(row.get("obligation_id") or "")
                for row in (*artifact_baseline_rows, *entrypoint_baseline_rows, *verification_rows)
                if str(row.get("obligation_id") or "")
            },
            tasks=tasks,
        )
        if normalized_upserts != patch.artifact_upserts:
            patch = ChiefEngineerSemanticRepairPatchV1(
                base_candidate_hash=patch.base_candidate_hash,
                diagnosis_hash=patch.diagnosis_hash,
                artifact_upserts=normalized_upserts,
                entrypoint_upserts=patch.entrypoint_upserts,
                entrypoint_remove_obligation_ids=patch.entrypoint_remove_obligation_ids,
                behavior_invariant_upserts=patch.behavior_invariant_upserts,
                task_behavior_ref_replacements=patch.task_behavior_ref_replacements,
            )
    artifact_upsert_ids = {artifact.obligation_id for artifact in patch.artifact_upserts}
    artifact_identity_baseline_rows = [
        (
            _normalize_unique_artifact_owner(
                _artifact_from_row(row),
                tasks=tasks,
            ).to_dict()
            if str(row.get("obligation_id") or "") in artifact_upsert_ids
            else row
        )
        for row in artifact_baseline_rows
    ]
    _assert_upsert_identity_immutable(
        artifact_identity_baseline_rows,
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
    artifact_upserts = patch.artifact_upserts

    for row in artifact_baseline_rows:
        owner_task_id = row.get("owner_task_id")
        if owner_task_id is not None and owner_task_id not in task_ids:
            raise ValueError(f"artifact owner_task_id is outside candidate task set: {owner_task_id}")
    for artifact in artifact_upserts:
        if artifact.owner_task_id is not None and artifact.owner_task_id not in task_ids:
            raise ValueError(f"artifact owner_task_id is outside candidate task set: {artifact.owner_task_id}")
    for artifact in artifact_upserts:
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
    entrypoint_baseline_by_id = {
        str(row.get("obligation_id") or ""): row
        for row in entrypoint_baseline_rows
        if str(row.get("obligation_id") or "")
    }
    entrypoint_remove_ids = set(patch.entrypoint_remove_obligation_ids)
    unknown_entrypoint_remove_ids = entrypoint_remove_ids - set(entrypoint_baseline_by_id)
    if unknown_entrypoint_remove_ids:
        raise ValueError(
            f"semantic repair references unknown entrypoint obligation ids: {sorted(unknown_entrypoint_remove_ids)!r}"
        )
    replacement_owner_kinds = {(entrypoint.owner_task_id, entrypoint.kind) for entrypoint in patch.entrypoint_upserts}
    for obligation_id in patch.entrypoint_remove_obligation_ids:
        row = entrypoint_baseline_by_id[obligation_id]
        owner_kind = (row.get("owner_task_id"), row.get("kind"))
        if owner_kind not in replacement_owner_kinds:
            raise ValueError(
                "semantic repair entrypoint removal requires a same-owner same-kind replacement: "
                f"obligation_id={obligation_id!r}"
            )
    for entrypoint in patch.entrypoint_upserts:
        if entrypoint.owner_task_id is not None and entrypoint.owner_task_id not in task_ids:
            raise ValueError(f"entrypoint owner_task_id is outside candidate task set: {entrypoint.owner_task_id}")
    for entrypoint in patch.entrypoint_upserts:
        owner = tasks_by_id.get(str(entrypoint.owner_task_id or ""))
        if owner is None or not owner.entrypoint_kind_authority:
            raise ValueError(
                "semantic repair entrypoint lacks immutable PM kind authority: "
                f"owner_task_id={entrypoint.owner_task_id!r}"
            )
        if entrypoint.kind != owner.entrypoint_kind_authority:
            raise ValueError(
                "semantic repair entrypoint kind conflicts with immutable PM authority: "
                f"expected={owner.entrypoint_kind_authority!r}:actual={entrypoint.kind!r}"
            )
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

    artifact_rows = _replace_by_id(artifact_baseline_rows, artifact_upserts, id_field="obligation_id")
    if _introduces_duplicate_value(
        artifact_baseline_rows,
        artifact_rows,
        field="path",
    ):
        raise ValueError("semantic repair would create duplicate artifact paths")
    retained_entrypoint_rows = [
        row for row in entrypoint_baseline_rows if str(row.get("obligation_id") or "") not in entrypoint_remove_ids
    ]
    entrypoint_rows = _replace_by_id(retained_entrypoint_rows, patch.entrypoint_upserts, id_field="obligation_id")
    if depth_split_remap:
        for row in verification_rows:
            row["covers_obligation_ids"] = _expand_obligation_refs(
                row.get("covers_obligation_ids"),
                remap=depth_split_remap,
            )
    # An artifact and its entrypoint may intentionally share one obligation id
    # to express that they are two views of the same delivery fact.  Frozen CE
    # candidates already use this link (exact L3-23 r05: ``OBL-MAIN-RS``).
    # Reject only duplicate counts introduced or increased by the patch; do not
    # make an unrelated incremental repair fail on a pre-existing legal link.
    all_obligation_rows = [*artifact_rows, *entrypoint_rows, *verification_rows]
    if _introduces_duplicate_value(
        [*artifact_baseline_rows, *entrypoint_baseline_rows, *verification_rows],
        all_obligation_rows,
        field="obligation_id",
    ):
        raise ValueError("semantic repair would create duplicate obligation ids")
    all_obligation_ids = [str(row["obligation_id"]) for row in all_obligation_rows]
    obligations["artifacts"] = artifact_rows
    obligations["entrypoints"] = entrypoint_rows
    obligations["verification"] = verification_rows
    completion["obligations"] = obligations
    payload["project_completion_contract"] = completion

    construction = _mapping("construction_plan", payload["construction_plan"])
    behavior = _mapping(
        "construction_plan.shared_behavior_contract",
        construction.get("shared_behavior_contract") or {"invariants": []},
    )
    # The frozen candidate is structurally valid but intentionally may contain
    # the diagnosed semantic defect. Preserve its raw rows until authorized
    # typed upserts replace matching ids; strict rehydration here would reject
    # the baseline before the repair could fix it (exact L3-22 r25).
    existing_invariants = _rows(
        "shared_behavior_contract.invariants",
        behavior.get("invariants"),
    )
    invariants = _replace_by_id(
        existing_invariants,
        patch.behavior_invariant_upserts,
        id_field="invariant_id",
    )
    effective_artifact_remap = dict(artifact_group_remap)
    effective_artifact_remap.update(depth_split_remap)
    if effective_artifact_remap:
        for invariant in invariants:
            invariant["covered_obligation_ids"] = _expand_obligation_refs(
                invariant.get("covered_obligation_ids"),
                remap=effective_artifact_remap,
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
                *(item.obligation_id for item in artifact_upserts),
                *(item.obligation_id for item in patch.entrypoint_upserts),
                *patch.entrypoint_remove_obligation_ids,
                *(item.invariant_id for item in patch.behavior_invariant_upserts),
                *patch.task_behavior_ref_replacements.keys(),
                *normalized_artifact_ids,
                *depth_split_minted_ids,
                *baseline_owner_rebound_ids,
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


def build_chief_engineer_semantic_repair_patch_schema(
    *,
    allowed_operations: tuple[ChiefEngineerSemanticRepairOperationV1, ...] | None = None,
) -> dict[str, Any]:
    """Return diagnosis-scoped provider schema for typed semantic repair.

    Authorized operation groups keep their strict item schema. Disabled groups
    retain only their required container shape because the authoritative patch
    parser drops them before composition. This prevents irrelevant provider
    noise from pre-empting the operation subset authorized by the diagnosis.
    """

    enabled_operations = frozenset(
        allowed_operations
        if allowed_operations is not None
        else (
            "artifact_upsert",
            "entrypoint_upsert",
            "behavior_invariant_upsert",
            "task_behavior_ref_replace",
        )
    )

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
                **(
                    {
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
                        }
                    }
                    if "artifact_upsert" in enabled_operations
                    else {}
                ),
            },
            "entrypoint_upserts": {
                "type": "array",
                **({"items": entrypoint_schema} if "entrypoint_upsert" in enabled_operations else {}),
            },
            "entrypoint_remove_obligation_ids": {
                "type": "array",
                **(
                    {"items": {"type": "string", "minLength": 1}, "uniqueItems": True}
                    if "entrypoint_upsert" in enabled_operations
                    else {}
                ),
            },
            "behavior_invariant_upserts": {
                "type": "array",
                **({"items": behavior_schema} if "behavior_invariant_upsert" in enabled_operations else {}),
            },
            "task_behavior_ref_replacements": {
                "type": "object",
                **(
                    {"additionalProperties": {"type": "array", "items": {"type": "string", "minLength": 1}}}
                    if "task_behavior_ref_replace" in enabled_operations
                    else {}
                ),
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
