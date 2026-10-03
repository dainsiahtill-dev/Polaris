"""Read-only authored source closure for disposable native verification.

Identity arguments are query keys, never grants. Baseline derivation must run
before a candidate claim changes receipt authority; pending effects are checked
separately against the frozen bytes. This module neither claims nor writes.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from polaris.cells.chief_engineer.blueprint.public import (
    ChiefEngineerBlueprintErrorV1,
    ProjectCompletionContractV1,
    QueryProjectCompletionContractV1,
    query_project_completion_contract,
)
from polaris.cells.control_plane.run_ledger.public import stable_hash
from polaris.cells.control_plane.run_ledger.public.directed_effect_receipt_validation import (
    directed_effect_receipt_v2_errors,
)
from polaris.cells.director.runtime.public.directed_effect_contracts import (
    DirectedEffectImmutableMapV1,
    DirectedEffectImmutableSequenceV1,
    DirectedEffectImmutableValueV1,
    hash_directed_effect_arguments,
)
from polaris.cells.events.fact_stream.public import QueryFactEventsV1, query_fact_events
from polaris.cells.runtime.execution_broker.public import (
    ProjectArtifactReceiptV1,
    ProjectArtifactSourceBaselineV1,
    QueryProjectArtifactReceiptV1,
    QueryProjectArtifactSourceBaselineV1,
    query_project_artifact_receipt,
    query_project_artifact_source_baseline,
)
from polaris.cells.runtime.task_runtime.public import (
    GetDirectedEffectInventoryQueryV1,
    GetDirectedEffectOperationQueryV1,
    GetDirectedEffectParentRegistryQueryV1,
    TaskRuntimeExecutionAttemptIdentityV1,
    get_directed_effect_inventory,
    get_directed_effect_operation,
    get_directed_effect_parent_registry,
)
from polaris.kernelone.errors import PathSecurityError
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter


class NativeValidationInputError(ValueError):
    """Unknown, stale or unproved source inputs must not reach staging."""


@dataclass(frozen=True, slots=True)
class NativeValidationSourceFile:
    path: str
    obligation_id: str
    owner_task_id: str
    content: bytes
    content_hash: str
    source_evidence: ProjectArtifactReceiptV1 | ProjectArtifactSourceBaselineV1


@dataclass(frozen=True, slots=True)
class NativeValidationSourceBaseline:
    workspace: str
    contract: ProjectCompletionContractV1
    files: tuple[NativeValidationSourceFile, ...]
    baseline_hash: str

    @property
    def input_hashes(self) -> Mapping[str, str]:
        return MappingProxyType({item.path: item.content_hash for item in self.files})

    @property
    def input_bytes(self) -> Mapping[str, bytes]:
        return MappingProxyType({item.path: item.content for item in self.files})


def _source_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or not path.parts
        or value != value.strip()
        or "\\" in value
        or "\x00" in value
        or path.is_absolute()
        or path.as_posix() != value
        or ".." in path.parts
        or path.parts[0] in {".polaris", ".git", "node_modules"}
    ):
        raise NativeValidationInputError("native_validation_source_path_invalid")
    return value


def _baseline_hash(
    workspace: str, contract: ProjectCompletionContractV1, files: tuple[NativeValidationSourceFile, ...]
) -> str:
    return stable_hash(
        {
            "schema_version": "factory.native_validation.source_baseline.v1",
            "workspace": workspace,
            "project_id": contract.project_id,
            "run_id": contract.run_id,
            "completion_contract_hash": contract.contract_hash,
            "files": [
                {
                    "path": item.path,
                    "obligation_id": item.obligation_id,
                    "owner_task_id": item.owner_task_id,
                    "content_hash": item.content_hash,
                    "source_receipt_hash": item.source_evidence.receipt_hash
                    if type(item.source_evidence) is ProjectArtifactReceiptV1
                    else item.source_evidence.source_receipt_hash,
                }
                for item in files
            ],
        }
    )


def _validate_frozen_baseline(baseline: NativeValidationSourceBaseline) -> None:
    if (
        type(baseline) is not NativeValidationSourceBaseline
        or type(baseline.contract) is not ProjectCompletionContractV1
        or type(baseline.files) is not tuple
        or baseline.workspace != str(Path(baseline.workspace).resolve())
    ):
        raise NativeValidationInputError("native_validation_baseline_invalid")
    contract = baseline.contract
    artifacts = sorted(
        (item for item in contract.obligations.artifacts if item.applicability != "not_applicable"),
        key=lambda item: item.path,
    )
    if not artifacts or len(artifacts) != len(baseline.files):
        raise NativeValidationInputError("native_validation_baseline_closure_incomplete")
    seen: set[str] = set()
    for artifact, item in zip(artifacts, baseline.files, strict=True):
        if (
            type(item) is not NativeValidationSourceFile
            or type(item.content) is not bytes
            or (item.path, item.obligation_id, item.owner_task_id)
            != (artifact.path, artifact.obligation_id, artifact.owner_task_id)
        ):
            raise NativeValidationInputError("native_validation_baseline_ce_binding_invalid")
        path = _source_path(item.path)
        if path in seen or item.owner_task_id not in contract.covered_task_ids:
            raise NativeValidationInputError("native_validation_baseline_owner_ambiguous")
        seen.add(path)
        evidence = item.source_evidence
        if type(evidence) not in {ProjectArtifactReceiptV1, ProjectArtifactSourceBaselineV1} or (
            evidence.workspace,
            evidence.project_id,
            evidence.run_id,
            evidence.completion_contract_hash,
            evidence.obligation_id,
            evidence.owner_task_id,
            evidence.path,
        ) != (
            baseline.workspace,
            contract.project_id,
            contract.run_id,
            contract.contract_hash,
            item.obligation_id,
            item.owner_task_id,
            item.path,
        ):
            raise NativeValidationInputError("native_validation_baseline_receipt_identity_invalid")
        if type(evidence) is ProjectArtifactSourceBaselineV1 and (
            evidence.writes_allowed is not False or evidence.completion_eligible is not False
        ):
            raise NativeValidationInputError("native_validation_baseline_has_authority")
        if hashlib.sha256(item.content).hexdigest() != item.content_hash or item.content_hash != evidence.artifact_hash:
            raise NativeValidationInputError("native_validation_baseline_bytes_unproved")
    if baseline.baseline_hash != _baseline_hash(baseline.workspace, contract, baseline.files):
        raise NativeValidationInputError("native_validation_baseline_hash_invalid")


def freeze_native_validation_baseline(
    *,
    workspace: str,
    project_id: str,
    run_id: str,
    completion_contract_hash: str,
) -> NativeValidationSourceBaseline:
    """Freeze the entire CE-owned authored closure before candidate admission."""
    root = str(Path(workspace).resolve())
    try:
        contract = query_project_completion_contract(
            QueryProjectCompletionContractV1(
                workspace=root,
                project_id=project_id,
                run_id=run_id,
                contract_hash=completion_contract_hash,
            )
        )
        if type(contract) is not ProjectCompletionContractV1 or (
            contract.project_id,
            contract.run_id,
            contract.contract_hash,
        ) != (project_id, run_id, completion_contract_hash):
            raise NativeValidationInputError("native_validation_ce_identity_mismatch")
        fs = KernelFileSystem(root, get_default_adapter())
        files: list[NativeValidationSourceFile] = []
        seen: set[str] = set()
        for artifact in contract.obligations.artifacts:
            if artifact.applicability == "not_applicable":
                continue
            path = _source_path(artifact.path)
            if path in seen or artifact.owner_task_id not in contract.covered_task_ids:
                raise NativeValidationInputError("native_validation_artifact_owner_ambiguous")
            seen.add(path)
            if artifact.owner_task_id is None:
                raise NativeValidationInputError("native_validation_artifact_owner_missing")
            identity = {
                "workspace": root,
                "project_id": project_id,
                "run_id": run_id,
                "completion_contract_hash": completion_contract_hash,
                "obligation_id": artifact.obligation_id,
                "owner_task_id": artifact.owner_task_id,
                "path": path,
            }
            evidence: ProjectArtifactReceiptV1 | ProjectArtifactSourceBaselineV1 | None = (
                query_project_artifact_receipt(QueryProjectArtifactReceiptV1(**identity))
            )
            if evidence is None:
                evidence = query_project_artifact_source_baseline(QueryProjectArtifactSourceBaselineV1(**identity))
            if type(evidence) not in {ProjectArtifactReceiptV1, ProjectArtifactSourceBaselineV1}:
                raise NativeValidationInputError(f"native_validation_source_receipt_missing:{path}")
            assert evidence is not None
            if any(getattr(evidence, key) != value for key, value in identity.items()):
                raise NativeValidationInputError("native_validation_source_receipt_identity_mismatch")
            if type(evidence) is ProjectArtifactSourceBaselineV1 and (
                evidence.writes_allowed is not False or evidence.completion_eligible is not False
            ):
                raise NativeValidationInputError("native_validation_source_baseline_has_authority")
            content = fs.workspace_read_bytes(path)
            content_hash = hashlib.sha256(content).hexdigest()
            if content_hash != evidence.artifact_hash:
                raise NativeValidationInputError(f"native_validation_source_bytes_drift:{path}")
            files.append(
                NativeValidationSourceFile(
                    path, artifact.obligation_id, artifact.owner_task_id, content, content_hash, evidence
                )
            )
        frozen = tuple(sorted(files, key=lambda item: item.path))
        if not frozen:
            raise NativeValidationInputError("native_validation_authored_closure_empty")
        # A source change during capture is not a coherent baseline.
        if any(hashlib.sha256(fs.workspace_read_bytes(item.path)).hexdigest() != item.content_hash for item in frozen):
            raise NativeValidationInputError("native_validation_source_changed_during_capture")
        baseline = NativeValidationSourceBaseline(root, contract, frozen, _baseline_hash(root, contract, frozen))
        _validate_frozen_baseline(baseline)
        return baseline
    except NativeValidationInputError:
        raise
    except (ChiefEngineerBlueprintErrorV1, PathSecurityError, OSError, RuntimeError, TypeError, ValueError) as exc:
        raise NativeValidationInputError(f"native_validation_owner_query_failed:{type(exc).__name__}:{exc}") from exc


@dataclass(frozen=True, slots=True)
class NativeValidationCandidateInputs:
    baseline: NativeValidationSourceBaseline
    files: tuple[tuple[str, bytes], ...]
    effect_receipt_refs: tuple[str, ...]

    @property
    def input_bytes(self) -> Mapping[str, bytes]:
        return MappingProxyType(dict(self.files))

    @property
    def input_hashes(self) -> Mapping[str, str]:
        return MappingProxyType({path: hashlib.sha256(content).hexdigest() for path, content in self.files})


def _validate_projection(
    contract: ProjectCompletionContractV1, projection: Mapping[str, Any], task_id: str
) -> set[str]:
    seed = {key: value for key, value in projection.items() if key != "projection_hash"}
    if projection.get("projection_hash") != stable_hash(seed):
        raise NativeValidationInputError("native_validation_projection_hash_invalid")
    if task_id not in contract.covered_task_ids:
        raise NativeValidationInputError("native_validation_projection_task_unknown")
    verification = [item for item in contract.obligations.verification if item.owner_task_id == task_id]
    owned = [item for item in contract.obligations.artifacts if item.owner_task_id == task_id]
    covered_ids = {value for item in verification for value in item.covers_obligation_ids}
    own_ids = {item.obligation_id for item in owned}
    authorities = [item for item in contract.verification_command_authority if item.task_id == task_id]
    by_hash = {item.authority_hash: item for item in authorities}
    reference = projection.get("project_contract_ref")
    if not isinstance(reference, str) or not reference.endswith("#project_completion_contract"):
        raise NativeValidationInputError("native_validation_projection_reference_invalid")
    expected = {
        "schema_version": "polaris.task_completion_projection.v1",
        "project_contract_id": contract.contract_id,
        "project_contract_ref": reference,
        "project_contract_hash": contract.contract_hash,
        "project_id": contract.project_id,
        "run_id": contract.run_id,
        "task_id": task_id,
        "project_kind": contract.project_kind,
        "completion_predicate_version": contract.completion_predicate_version,
        "verifier_policy_hash": contract.verifier_policy_hash,
        "verifier_policy_snapshot_hash": contract.verifier_policy_snapshot_hash,
        "owned_artifacts": [item.to_dict() for item in owned],
        "dependency_artifacts": [
            item.to_dict() for item in contract.obligations.artifacts if item.obligation_id in covered_ids - own_ids
        ],
        "owned_entrypoints": [
            item.to_dict() for item in contract.obligations.entrypoints if item.owner_task_id == task_id
        ],
        "owned_verification": [item.to_dict() for item in verification],
        "verification_command_authority": [item.to_dict() for item in authorities],
        "verification_execution_authority": [
            {
                "obligation_id": item.obligation_id,
                "owner_task_id": item.owner_task_id,
                "modality": item.modality,
                "command": item.command,
                "argv": list(by_hash[str(item.command_authority_hash)].argv),
                "cwd": by_hash[str(item.command_authority_hash)].cwd,
                "command_authority_hash": item.command_authority_hash,
                "covers_obligation_ids": list(item.covers_obligation_ids),
            }
            for item in verification
            if item.applicability != "not_applicable"
        ],
    }
    if seed != expected:
        raise NativeValidationInputError("native_validation_projection_ce_slice_mismatch")
    return {item.path for item in owned if item.applicability != "not_applicable"}


def _immutable(value: Any) -> DirectedEffectImmutableValueV1:
    if value is None or type(value) in {bool, int, float, str}:
        return value
    if isinstance(value, Mapping):
        if any(type(key) is not str or not key or key != key.strip() for key in value):
            raise NativeValidationInputError("native_validation_physical_body_keys_invalid")
        return DirectedEffectImmutableMapV1(tuple(sorted((key, _immutable(item)) for key, item in value.items())))
    if isinstance(value, (list, tuple)):
        return DirectedEffectImmutableSequenceV1(tuple(_immutable(item) for item in value))
    raise NativeValidationInputError("native_validation_physical_body_invalid")


def _verify_persisted_commit(
    attempt: TaskRuntimeExecutionAttemptIdentityV1, receipt: Mapping[str, Any], commit: Mapping[str, Any]
) -> None:
    registry_result = get_directed_effect_parent_registry(
        GetDirectedEffectParentRegistryQueryV1(
            workspace=attempt.workspace,
            task_id=attempt.task_id,
            execution_attempt=attempt,
        )
    )
    registry = registry_result.registry
    if not registry_result.ok or registry is None:
        raise NativeValidationInputError(f"native_validation_parent_registry_unavailable:{registry_result.code}")
    bindings = [
        item for item in registry.bindings_by_id.values() if item.correlation.batch_id == receipt.get("batch_id")
    ]
    if len(bindings) != 1:
        raise NativeValidationInputError("native_validation_parent_binding_ambiguous")
    binding = bindings[0]
    inventory = get_directed_effect_inventory(
        GetDirectedEffectInventoryQueryV1(
            workspace=attempt.workspace,
            task_id=attempt.task_id,
            execution_attempt=attempt,
            parent_binding=binding,
        )
    )
    if not inventory.ok or inventory.projection is None:
        raise NativeValidationInputError("native_validation_inventory_unavailable")
    members = [item for item in inventory.projection.members if item.operation_id == receipt.get("operation_id")]
    if len(members) != 1:
        raise NativeValidationInputError("native_validation_effect_member_unknown")
    member = members[0]
    if (member.tool_call_id, member.normalized_tool_name, member.expected_receipt_binding_hash, member.effect_type) != (
        receipt.get("tool_call_id"),
        receipt.get("normalized_tool_name"),
        receipt.get("receipt_binding_hash"),
        "write",
    ):
        raise NativeValidationInputError("native_validation_effect_member_mismatch")
    operation = get_directed_effect_operation(
        GetDirectedEffectOperationQueryV1(
            workspace=attempt.workspace,
            task_id=attempt.task_id,
            execution_attempt=attempt,
            parent_binding=binding,
            tool_call_id=member.tool_call_id,
            effect_id=member.effect_id,
        )
    )
    snapshot = operation.snapshot
    if (
        not operation.ok
        or operation.state != "RECEIPT_COMMITTED"
        or snapshot is None
        or (
            operation.version != commit.get("version")
            or snapshot.last_event_id != commit.get("event_id")
            or operation.operation is None
            or operation.operation.operation_id != receipt.get("operation_id")
        )
    ):
        raise NativeValidationInputError("native_validation_effect_not_current_committed")
    if snapshot.source_head_seq > 4096:
        raise NativeValidationInputError("native_validation_effect_proof_stream_excessive")
    facts = query_fact_events(
        QueryFactEventsV1(
            workspace=attempt.workspace,
            stream=binding.operation_stream_token,
            limit=max(1, snapshot.source_head_seq),
            strict_integrity=True,
        )
    )
    events = [event for event in facts.events if event.get("event_id") == snapshot.last_event_id]
    if len(events) != 1:
        raise NativeValidationInputError("native_validation_commit_event_missing")
    payload = events[0].get("payload")
    descriptor = payload.get("replay_descriptor") if isinstance(payload, Mapping) else None
    if (
        not isinstance(descriptor, Mapping)
        or descriptor.get("command") != "commit_receipt"
        or any(
            descriptor.get(key) != commit.get(key)
            for key in ("receipt_ref", "receipt_hash", "receipt_binding_hash", "receipt_outcome")
        )
    ):
        raise NativeValidationInputError("native_validation_persisted_receipt_binding_mismatch")


def overlay_native_validation_candidate(
    baseline: NativeValidationSourceBaseline,
    *,
    pending: Mapping[str, Any],
    task_completion_projection: Mapping[str, Any],
    round_repair_results: Sequence[Mapping[str, Any]],
) -> NativeValidationCandidateInputs:
    """Overlay only current, truly committed effects on the pre-claim bytes."""
    try:
        _validate_frozen_baseline(baseline)
        contract = query_project_completion_contract(
            QueryProjectCompletionContractV1(
                workspace=baseline.workspace,
                project_id=baseline.contract.project_id,
                run_id=baseline.contract.run_id,
                contract_hash=baseline.contract.contract_hash,
            )
        )
        if contract != baseline.contract:
            raise NativeValidationInputError("native_validation_ce_contract_changed")
        task_id = pending.get("task_id")
        attempt = pending.get("execution_attempt")
        if (
            not isinstance(task_id, str)
            or not isinstance(attempt, TaskRuntimeExecutionAttemptIdentityV1)
            or type(attempt) is not TaskRuntimeExecutionAttemptIdentityV1
            or (
                Path(attempt.workspace).resolve() != Path(baseline.workspace)
                or attempt.run_id != contract.run_id
                or attempt.external_task_id != task_id
                or attempt.role_id != "director"
                or pending.get("task_row_id") != attempt.task_id
            )
        ):
            raise NativeValidationInputError("native_validation_pending_attempt_identity_invalid")
        owned_paths = _validate_projection(contract, task_completion_projection, task_id)
        files = {item.path: item for item in baseline.files}
        fs = KernelFileSystem(baseline.workspace, get_default_adapter())
        overlays: dict[str, bytes] = {}
        receipt_refs: list[str] = []
        for row in round_repair_results:
            receipt, commit, physical = row.get("effect_receipt"), row.get("effect_receipt_commit"), row.get("result")
            if not isinstance(receipt, Mapping) or not isinstance(commit, Mapping) or not isinstance(physical, Mapping):
                raise NativeValidationInputError("native_validation_effect_row_incomplete")
            if directed_effect_receipt_v2_errors(receipt, commit, prefix="candidate") != ():
                raise NativeValidationInputError("native_validation_effect_receipt_not_valid_v2")
            if receipt.get("receipt_outcome") != "succeeded" or receipt.get("normalized_tool_name") not in {
                "edit_file",
                "write_file",
            }:
                raise NativeValidationInputError("native_validation_effect_not_source_write")
            if physical.get("effect_receipt") != receipt or physical.get("effect_receipt_commit") != commit:
                raise NativeValidationInputError("native_validation_nested_receipt_mismatch")
            body = {
                key: value for key, value in physical.items() if key not in {"effect_receipt", "effect_receipt_commit"}
            }
            hashed_body = hash_directed_effect_arguments(
                tuple(sorted((key, _immutable(value)) for key, value in body.items()))
            )
            if hashed_body != receipt.get("physical_result_hash"):
                raise NativeValidationInputError("native_validation_physical_result_hash_mismatch")
            path = _source_path(str(body.get("file") or ""))
            if path not in files or path not in owned_paths or path in overlays:
                raise NativeValidationInputError("native_validation_candidate_path_not_owned_or_ambiguous")
            if body.get("before_sha256") != files[path].content_hash or body.get("after_sha256") == body.get(
                "before_sha256"
            ):
                raise NativeValidationInputError("native_validation_candidate_before_or_noop_invalid")
            content = fs.workspace_read_bytes(path)
            if hashlib.sha256(content).hexdigest() != body.get("after_sha256"):
                raise NativeValidationInputError("native_validation_candidate_after_drift")
            _verify_persisted_commit(attempt, receipt, commit)
            overlays[path] = content
            receipt_refs.append(str(receipt["receipt_id"]))
        if not overlays:
            raise NativeValidationInputError("native_validation_candidate_effects_missing")
        staged: list[tuple[str, bytes]] = []
        for path, item in sorted(files.items()):
            expected = overlays.get(path, item.content)
            if fs.workspace_read_bytes(path) != expected:
                raise NativeValidationInputError(f"native_validation_source_or_candidate_drift:{path}")
            staged.append((path, expected))
        return NativeValidationCandidateInputs(baseline, tuple(staged), tuple(receipt_refs))
    except NativeValidationInputError:
        raise
    except (
        ChiefEngineerBlueprintErrorV1,
        PathSecurityError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
        KeyError,
        AttributeError,
    ) as exc:
        raise NativeValidationInputError(
            f"native_validation_candidate_proof_failed:{type(exc).__name__}:{exc}"
        ) from exc
