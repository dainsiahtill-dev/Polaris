"""Receipt-bound parent artifact evidence for dependent Director tasks.

This module owns a read-only projection:

TaskRuntime observable parent row -> committed physical effect receipt ->
KernelOne guarded file snapshot -> final-request evidence payload.

It never scans a workspace and never mutates TaskRuntime or project files.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from polaris.kernelone.fs.guarded_regular_file_snapshot import (
    GuardedRegularFileSnapshotError,
    read_guarded_regular_file_snapshot,
)

DIRECTOR_DEPENDENCY_ARTIFACT_SNAPSHOT_CONTEXT_KEY: Final[str] = "_trusted_director_dependency_artifact_snapshot_v2"
_SCHEMA_VERSION: Final[str] = "polaris.actual_sibling_exports.evidence.v2"
_SOURCE: Final[str] = "roles.adapters.director.task_runtime_dependency_artifact_snapshot"
_MAX_PARENT_TASKS: Final[int] = 16
_MAX_MODULES: Final[int] = 32
_MAX_FILE_BYTES: Final[int] = 64 * 1024
_MAX_TOTAL_BYTES: Final[int] = 256 * 1024

ProjectArtifactReceiptLookup = Callable[[Mapping[str, str]], Mapping[str, Any] | None]


class DirectorDependencyArtifactEvidenceError(RuntimeError):
    """Typed fail-closed dependency evidence projection error."""

    def __init__(self, message: str, *, code: str, details: Mapping[str, object] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


def _fail(code: str, message: str, **details: object) -> DirectorDependencyArtifactEvidenceError:
    return DirectorDependencyArtifactEvidenceError(message, code=code, details=details)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list_of_mappings(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _text_list(value: Any) -> list[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, (list, tuple, set, frozenset)):
        values = list(value)
    else:
        return []
    result: list[str] = []
    for item in values:
        token = str(item or "").strip()
        if token and token not in result:
            result.append(token)
    return result


def _hash64(value: Any) -> str:
    token = str(value or "").strip()
    if len(token) != 64 or any(character not in "0123456789abcdef" for character in token):
        return ""
    return token


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _normalized_relative_path(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw or "\\" in raw or raw.startswith("/") or "\x00" in raw:
        return ""
    parts = raw.split("/")
    if any(not part or part in {".", ".."} for part in parts):
        return ""
    if ":" in parts[0]:
        return ""
    return "/".join(parts)


def _dependency_task_ids(child_task: Mapping[str, Any]) -> list[str]:
    metadata = _mapping(child_task.get("metadata"))
    containers = (metadata, _mapping(metadata.get("task_context")), dict(child_task))
    for container in containers:
        for key in (
            "resolved_depends_on_task_ids",
            "depends_on_task_ids",
            "depends_on_external",
            "dependency_task_ids",
            "depends_on",
        ):
            values = _text_list(container.get(key))
            if values:
                if len(values) > _MAX_PARENT_TASKS:
                    raise _fail(
                        "dependency_artifact_parent_budget_exceeded",
                        "dependency task count exceeds the hard parent budget",
                        count=len(values),
                        maximum=_MAX_PARENT_TASKS,
                    )
                return values
    return []


def _parent_identity(parent: Mapping[str, Any]) -> tuple[str, str]:
    metadata = _mapping(parent.get("metadata"))
    runtime_id = str(parent.get("id") or "").strip()
    external_id = str(
        metadata.get("external_task_id")
        or metadata.get("pm_task_id")
        or metadata.get("source_task_id")
        or metadata.get("task_id")
        or ""
    ).strip()
    return runtime_id, external_id


def _receipt_result_rows(adapter_result: Mapping[str, Any]) -> list[dict[str, Any]]:
    primary_llm = _mapping(adapter_result.get("primary_llm"))
    primary_metadata = _mapping(primary_llm.get("metadata"))
    no_write_retry = _mapping(adapter_result.get("no_write_materialization_retry"))
    no_write_metadata = _mapping(no_write_retry.get("metadata"))
    empty_write_retry = _mapping(adapter_result.get("empty_write_content_retry"))
    empty_write_metadata = _mapping(empty_write_retry.get("metadata"))
    candidate_batches = (
        _mapping(adapter_result.get("batch_receipt")),
        _mapping(primary_llm.get("batch_receipt")),
        _mapping(primary_metadata.get("batch_receipt")),
        _mapping(no_write_retry.get("batch_receipt")),
        _mapping(no_write_metadata.get("batch_receipt")),
        _mapping(empty_write_retry.get("batch_receipt")),
        _mapping(empty_write_metadata.get("batch_receipt")),
    )
    rows: list[dict[str, Any]] = []
    for batch in candidate_batches:
        rows.extend(_list_of_mappings(batch.get("raw_results")))
        rows.extend(_list_of_mappings(batch.get("results")))
    rows.extend(_list_of_mappings(adapter_result.get("tool_results")))
    rows.extend(_list_of_mappings(primary_metadata.get("tool_results")))
    rows.extend(_list_of_mappings(no_write_retry.get("tool_results")))
    rows.extend(_list_of_mappings(no_write_metadata.get("tool_results")))
    rows.extend(_list_of_mappings(empty_write_retry.get("tool_results")))
    rows.extend(_list_of_mappings(empty_write_metadata.get("tool_results")))
    return rows


def _validated_receipt_by_path(
    *,
    parent_task_id: str,
    adapter_result: Mapping[str, Any],
) -> tuple[dict[str, dict[str, str]], tuple[str, ...]]:
    if adapter_result.get("write_tool_evidence") is not True:
        raise _fail(
            "dependency_artifact_write_evidence_missing",
            "parent adapter result lacks affirmative write-tool evidence",
            parent_task_id=parent_task_id,
        )
    changed_paths = _text_list(adapter_result.get("new_files"))
    for path in _text_list(adapter_result.get("modified_files")):
        if path not in changed_paths:
            changed_paths.append(path)
    normalized_paths: list[str] = []
    for raw_path in changed_paths:
        path = _normalized_relative_path(raw_path)
        if not path:
            raise _fail(
                "dependency_artifact_path_invalid",
                "parent adapter result contains an unsafe artifact path",
                parent_task_id=parent_task_id,
                path=raw_path,
            )
        if path not in normalized_paths:
            normalized_paths.append(path)
    if not normalized_paths:
        raise _fail(
            "dependency_artifact_parent_files_missing",
            "parent task has no materialized files",
            parent_task_id=parent_task_id,
        )
    if len(normalized_paths) > _MAX_MODULES:
        raise _fail(
            "dependency_artifact_module_budget_exceeded",
            "parent artifact count exceeds the hard module budget",
            count=len(normalized_paths),
            maximum=_MAX_MODULES,
        )

    receipts: dict[str, dict[str, str]] = {}
    for result_row in _receipt_result_rows(adapter_result):
        if str(result_row.get("status") or "").strip().lower() != "success":
            continue
        result = _mapping(result_row.get("result"))
        path = _normalized_relative_path(result.get("file"))
        if not path or path not in normalized_paths:
            continue
        effect_receipt = _mapping(result_row.get("effect_receipt"))
        if not effect_receipt:
            effect_receipt = _mapping(result.get("effect_receipt"))
        commit = _mapping(result_row.get("effect_receipt_commit"))
        if not commit:
            commit = _mapping(result.get("effect_receipt_commit"))
        receipt_id = str(effect_receipt.get("receipt_id") or "").strip()
        receipt_hash = _hash64(effect_receipt.get("receipt_hash"))
        binding_hash = _hash64(effect_receipt.get("receipt_binding_hash"))
        physical_result_hash = _hash64(effect_receipt.get("physical_result_hash"))
        target_state_hash = _hash64(effect_receipt.get("target_state_hash"))
        valid = (
            effect_receipt.get("schema_version") == "roles.adapters.director_physical_effect_receipt.v2"
            and effect_receipt.get("authoritative") is True
            and effect_receipt.get("durable") is True
            and effect_receipt.get("receipt_outcome") == "succeeded"
            and bool(receipt_id)
            and bool(receipt_hash)
            and bool(binding_hash)
            and bool(physical_result_hash)
            and bool(target_state_hash)
            and commit.get("state") == "RECEIPT_COMMITTED"
            and str(commit.get("receipt_ref") or "").strip() == receipt_id
            and _hash64(commit.get("receipt_hash")) == receipt_hash
        )
        if not valid:
            continue
        receipt = {
            "receipt_id": receipt_id,
            "receipt_hash": receipt_hash,
            "receipt_binding_hash": binding_hash,
            "physical_result_hash": physical_result_hash,
            "target_state_hash": target_state_hash,
        }
        # Last successful write wins. Materialization + quality-repair commonly
        # rewrites the same path (e.g. package.json) with distinct receipts.
        # Fail-closed conflict here blocked child-task actual_sibling_exports
        # rebind even when the parent completed with durable files (r131 L1-01).
        receipts[path] = receipt

    missing_paths = tuple(path for path in normalized_paths if path not in receipts)
    if missing_paths and not receipts:
        raise _fail(
            "dependency_artifact_receipt_missing",
            "parent task has no artifact bound to a committed physical effect receipt",
            parent_task_id=parent_task_id,
            missing_paths=missing_paths,
        )
    return receipts, missing_paths


def _task_completion_projection(parent: Mapping[str, Any]) -> dict[str, Any]:
    """Read the sealed CE completion projection from a parent row or reconstruct."""

    metadata = _mapping(parent.get("metadata"))
    projection = _mapping(metadata.get("task_completion_projection"))
    if projection:
        return projection
    return _mapping(parent.get("task_completion_projection"))


def _completion_artifact_queries(
    *,
    parent: Mapping[str, Any],
    parent_task_id: str,
    workspace: str,
) -> list[dict[str, str]]:
    """Project exact owned-artifact queries from the sealed CE completion projection."""

    projection = _task_completion_projection(parent)
    if not projection:
        return []
    external_task_id = _parent_identity(parent)[1]
    projection_task_id = str(projection.get("task_id") or "").strip()
    if not external_task_id or projection_task_id not in {parent_task_id, external_task_id}:
        raise _fail(
            "dependency_artifact_completion_identity_mismatch",
            "parent completion projection does not match the declared dependency",
            parent_task_id=parent_task_id,
            external_task_id=external_task_id,
            projection_task_id=projection_task_id,
        )
    project_id = str(projection.get("project_id") or "").strip()
    run_id = str(projection.get("run_id") or "").strip()
    contract_hash = _hash64(projection.get("project_contract_hash"))
    if not project_id or not run_id or not contract_hash:
        raise _fail(
            "dependency_artifact_completion_authority_invalid",
            "parent completion projection lacks exact project receipt authority",
            parent_task_id=parent_task_id,
        )
    queries: list[dict[str, str]] = []
    for artifact in _list_of_mappings(projection.get("owned_artifacts")):
        if str(artifact.get("applicability") or "required").strip().lower() != "required":
            continue
        owner_task_id = str(artifact.get("owner_task_id") or "").strip()
        obligation_id = str(artifact.get("obligation_id") or "").strip()
        path = _normalized_relative_path(artifact.get("path"))
        if owner_task_id != external_task_id or not obligation_id or not path:
            raise _fail(
                "dependency_artifact_completion_obligation_invalid",
                "parent completion projection contains an invalid owned artifact",
                parent_task_id=parent_task_id,
                owner_task_id=owner_task_id,
                obligation_id=obligation_id,
                path=str(artifact.get("path") or ""),
            )
        queries.append(
            {
                "workspace": workspace,
                "project_id": project_id,
                "run_id": run_id,
                "completion_contract_hash": contract_hash,
                "obligation_id": obligation_id,
                "owner_task_id": owner_task_id,
                "path": path,
            }
        )
    return queries


def _project_receipts_by_path(
    *,
    parent: Mapping[str, Any],
    parent_task_id: str,
    workspace: str,
    lookup: ProjectArtifactReceiptLookup | None,
) -> tuple[dict[str, dict[str, str]], tuple[str, ...]]:
    """Read current, execution-broker-sealed project artifact receipts."""

    queries = _completion_artifact_queries(
        parent=parent,
        parent_task_id=parent_task_id,
        workspace=workspace,
    )
    if not queries:
        return {}, ()
    receipts: dict[str, dict[str, str]] = {}
    if lookup is not None:
        for query in queries:
            receipt = lookup(query)
            if not isinstance(receipt, Mapping):
                continue
            artifact_hash = _hash64(receipt.get("artifact_hash"))
            receipt_hash = _hash64(receipt.get("receipt_hash"))
            authority_revision = _hash64(receipt.get("authority_revision"))
            receipt_ref = str(receipt.get("receipt_ref") or "").strip()
            identity_matches = all(str(receipt.get(key) or "").strip() == value for key, value in query.items())
            if not (identity_matches and artifact_hash and receipt_hash and authority_revision and receipt_ref):
                continue
            receipts[query["path"]] = {
                "receipt_id": receipt_ref,
                "receipt_hash": receipt_hash,
                "receipt_binding_hash": authority_revision,
                "physical_result_hash": artifact_hash,
                "target_state_hash": artifact_hash,
                "artifact_hash": artifact_hash,
                "authority_source": "runtime.execution_broker.project_artifact_receipt.v1",
            }
    missing_paths = tuple(query["path"] for query in queries if query["path"] not in receipts)
    return receipts, missing_paths


def query_project_artifact_receipt_payload(query_payload: Mapping[str, str]) -> Mapping[str, Any] | None:
    """Read one exact execution-broker receipt without weakening fail-closed callers."""

    from polaris.cells.runtime.execution_broker.public import (
        ProjectArtifactReceiptV1,
        QueryProjectArtifactReceiptV1,
        query_project_artifact_receipt,
    )

    try:
        receipt = query_project_artifact_receipt(QueryProjectArtifactReceiptV1(**dict(query_payload)))
    except (OSError, RuntimeError, TypeError, ValueError):
        return None
    if type(receipt) is not ProjectArtifactReceiptV1:
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


def build_current_task_project_artifact_receipt_evidence(
    *,
    task: Mapping[str, Any],
    task_id: str,
    workspace: str,
    lookup: ProjectArtifactReceiptLookup | None = query_project_artifact_receipt_payload,
) -> dict[str, Any]:
    """Prove a retry's owned artifacts are already committed and byte-current.

    This is stronger than workspace existence.  It requires the sealed CE
    completion projection, exact project/run/contract/task/path authority, and
    execution-broker receipts whose artifact hashes still match guarded files.
    """

    queries = _completion_artifact_queries(
        parent=task,
        parent_task_id=task_id,
        workspace=workspace,
    )
    receipts, missing_paths = _project_receipts_by_path(
        parent=task,
        parent_task_id=task_id,
        workspace=workspace,
        lookup=lookup,
    )
    stale_paths: list[str] = []
    for path, receipt in receipts.items():
        try:
            snapshot = read_guarded_regular_file_snapshot(workspace, path, _MAX_FILE_BYTES)
        except (GuardedRegularFileSnapshotError, OSError, ValueError):
            stale_paths.append(path)
            continue
        if hashlib.sha256(snapshot.content).hexdigest() != receipt.get("artifact_hash"):
            stale_paths.append(path)
    receipt_paths = sorted(path for path in receipts if path not in stale_paths)
    receipt_refs = sorted(
        str(receipts[path].get("receipt_id") or "").strip()
        for path in receipt_paths
        if str(receipts[path].get("receipt_id") or "").strip()
    )
    missing = sorted(set(missing_paths).union(stale_paths))
    return {
        "schema_version": "polaris.current_task_project_artifact_receipt_evidence.v1",
        "authority": "runtime.execution_broker.project_artifact_receipt.v1",
        "task_id": task_id,
        "required_artifact_count": len(queries),
        "receipt_count": len(receipt_paths),
        "receipt_paths": receipt_paths,
        "receipt_refs": receipt_refs,
        "missing_or_stale_paths": missing,
        "ok": bool(queries) and not missing and len(receipt_paths) == len(queries),
    }


@dataclass(frozen=True, slots=True)
class TrustedDirectorDependencyArtifactSnapshotV2:
    """Internal exact-type token coupling one payload to one rendered message."""

    _payload_json: str
    _message_lines: tuple[str, ...]

    def payload(self) -> dict[str, Any]:
        value = json.loads(self._payload_json)
        if not isinstance(value, dict):
            raise RuntimeError("trusted_dependency_artifact_payload_invalid")
        return value

    def message_lines(self) -> tuple[str, ...]:
        return self._message_lines


def build_director_dependency_artifact_snapshot(
    *,
    workspace: str,
    child_task: Mapping[str, Any],
    get_task: Callable[[str], dict[str, Any] | None],
    get_project_artifact_receipt: ProjectArtifactReceiptLookup | None = None,
) -> TrustedDirectorDependencyArtifactSnapshotV2 | None:
    """Build exactly one bounded snapshot from declared parent execution facts."""

    if not workspace or not os.path.isabs(workspace):
        raise _fail(
            "dependency_artifact_workspace_invalid",
            "dependency artifact workspace must be a non-empty absolute path",
            workspace=workspace,
        )
    dependency_ids = _dependency_task_ids(child_task)
    if not dependency_ids:
        return None

    modules: list[dict[str, Any]] = []
    uncovered_artifacts: list[dict[str, str]] = []
    covered_parent_ids: list[str] = []
    zero_artifact_parent_ids: list[str] = []
    total_bytes = 0
    for dependency_id in dependency_ids:
        parent = get_task(dependency_id)
        if not isinstance(parent, dict):
            raise _fail(
                "dependency_artifact_parent_missing",
                "declared parent task is absent from the TaskRuntime observable read model",
                parent_task_id=dependency_id,
            )
        runtime_id, external_id = _parent_identity(parent)
        if dependency_id not in {runtime_id, external_id}:
            raise _fail(
                "dependency_artifact_parent_identity_mismatch",
                "TaskRuntime parent row does not match the declared dependency",
                requested_parent_task_id=dependency_id,
                runtime_task_id=runtime_id,
                external_task_id=external_id,
            )
        metadata = _mapping(parent.get("metadata"))
        adapter_result = _mapping(metadata.get("adapter_result"))
        receipts: dict[str, dict[str, str]] = {}
        effect_missing_paths: tuple[str, ...] = ()
        if adapter_result.get("write_tool_evidence") is True and (
            _text_list(adapter_result.get("new_files")) or _text_list(adapter_result.get("modified_files"))
        ):
            receipts, effect_missing_paths = _validated_receipt_by_path(
                parent_task_id=dependency_id,
                adapter_result=adapter_result,
            )
        project_receipts, project_missing_paths = _project_receipts_by_path(
            parent=parent,
            parent_task_id=dependency_id,
            workspace=workspace,
            lookup=get_project_artifact_receipt,
        )
        for path, receipt in project_receipts.items():
            receipts.setdefault(path, receipt)
        candidate_missing_paths = tuple(dict.fromkeys((*effect_missing_paths, *project_missing_paths)))
        missing_paths = tuple(path for path in candidate_missing_paths if path not in receipts)
        if not receipts:
            # CE split parents (L2-12 TASK-3-foundation) can have a sealed
            # completion projection with zero required owned artifacts.  That
            # parent contributes no sibling bodies.  Failing the whole snapshot
            # here wiped TASK-2 sealed receipts from the child final request
            # (missing_required_refs=actual_sibling_exports).  A skeletal row
            # without a completion projection still fail-closes.
            if not candidate_missing_paths and _task_completion_projection(parent):
                covered_parent_ids.append(dependency_id)
                zero_artifact_parent_ids.append(dependency_id)
                continue
            raise _fail(
                "dependency_artifact_receipt_missing",
                "parent task has no artifact bound to a committed receipt",
                parent_task_id=dependency_id,
                missing_paths=missing_paths,
            )
        uncovered_artifacts.extend(
            {
                "parent_task_id": dependency_id,
                "path": path,
                "reason": "committed_effect_receipt_missing",
            }
            for path in missing_paths
        )
        for path in sorted(receipts):
            if len(modules) >= _MAX_MODULES:
                raise _fail(
                    "dependency_artifact_module_budget_exceeded",
                    "dependency artifact count exceeds the hard module budget",
                    count=len(modules) + 1,
                    maximum=_MAX_MODULES,
                )
            try:
                snapshot = read_guarded_regular_file_snapshot(
                    workspace,
                    path,
                    _MAX_FILE_BYTES,
                )
            except (GuardedRegularFileSnapshotError, OSError, ValueError) as exc:
                raise _fail(
                    "dependency_artifact_guarded_read_failed",
                    "receipt-bound parent artifact could not be read with guarded identity proof",
                    parent_task_id=dependency_id,
                    path=path,
                    source_error_code=getattr(exc, "code", type(exc).__name__),
                ) from exc
            try:
                body = snapshot.content.decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise _fail(
                    "dependency_artifact_utf8_invalid",
                    "receipt-bound parent artifact is not valid UTF-8 text",
                    parent_task_id=dependency_id,
                    path=path,
                ) from exc
            total_bytes += snapshot.size
            if total_bytes > _MAX_TOTAL_BYTES:
                raise _fail(
                    "dependency_artifact_total_budget_exceeded",
                    "dependency artifacts exceed the hard total byte budget",
                    total_bytes=total_bytes,
                    maximum=_MAX_TOTAL_BYTES,
                )
            receipt = receipts[path]
            content_hash = hashlib.sha256(snapshot.content).hexdigest()
            expected_artifact_hash = receipt.get("artifact_hash", "")
            if expected_artifact_hash and expected_artifact_hash != content_hash:
                raise _fail(
                    "dependency_artifact_project_receipt_hash_mismatch",
                    "guarded parent artifact bytes do not match the project artifact receipt",
                    parent_task_id=dependency_id,
                    path=path,
                )
            source_fact = {
                "parent_task_id": dependency_id,
                "parent_runtime_task_id": runtime_id,
                "parent_external_task_id": external_id,
                "path": path,
                "receipt_authority_source": receipt.get(
                    "authority_source",
                    "roles.adapters.director_physical_effect_receipt.v2",
                ),
                **receipt,
            }
            modules.append(
                {
                    "parent_task_id": dependency_id,
                    "parent_runtime_task_id": runtime_id,
                    "parent_external_task_id": external_id,
                    "source_fact_ref": f"task_runtime.observable_task:{runtime_id}",
                    "source_fact_hash": _canonical_hash(source_fact),
                    "effect_receipt_id": receipt["receipt_id"],
                    "effect_receipt_hash": receipt["receipt_hash"],
                    "effect_receipt_binding_hash": receipt["receipt_binding_hash"],
                    "physical_result_hash": receipt["physical_result_hash"],
                    "target_state_hash": receipt["target_state_hash"],
                    "receipt_authority_source": receipt.get(
                        "authority_source",
                        "roles.adapters.director_physical_effect_receipt.v2",
                    ),
                    "path": path,
                    "sha256": content_hash,
                    "byte_count": snapshot.size,
                    "body": body,
                    "guarded_snapshot": {
                        "device": snapshot.device,
                        "inode": snapshot.inode,
                        "mtime_ns": snapshot.mtime_ns,
                        "ctime_ns": snapshot.ctime_ns,
                        "root_device": snapshot.root_device,
                        "root_inode": snapshot.root_inode,
                    },
                }
            )
        covered_parent_ids.append(dependency_id)

    payload: dict[str, Any] = {
        "schema_version": _SCHEMA_VERSION,
        "source": _SOURCE,
        "dependency_task_ids": dependency_ids,
        "covered_parent_task_ids": covered_parent_ids,
        "zero_artifact_parent_task_ids": zero_artifact_parent_ids,
        "modules": modules,
        "module_count": len(modules),
        "total_byte_count": total_bytes,
        "receipt_coverage_complete": not uncovered_artifacts,
        "uncovered_artifacts": uncovered_artifacts,
    }
    payload["snapshot_sha256"] = _canonical_hash(payload)
    message_lines = [
        "已提交父任务的真实依赖产物 / Committed parent-task dependency artifacts:",
        (
            f"{_SCHEMA_VERSION} snapshot_sha256={payload['snapshot_sha256']} "
            "(effect-receipt bound; exact bodies below are authoritative)"
        ),
        ("消费这些文件时必须使用下列真实定义；不得用 planned_exports、tentative_exports 或猜测符号覆盖物理事实。"),
    ]
    for module in modules:
        message_lines.extend(
            [
                (
                    f"--- parent_task_id={module['parent_task_id']} "
                    f"receipt_id={module['effect_receipt_id']} "
                    f"path={module['path']} sha256={module['sha256']} ---"
                ),
                str(module["body"]),
            ]
        )
    if uncovered_artifacts:
        message_lines.extend(
            [
                "Receipt coverage is partial: only the exact bodies above are authoritative.",
                (
                    "Do not guess or import uncovered parent artifacts: "
                    + ", ".join(f"{item['parent_task_id']}:{item['path']}" for item in uncovered_artifacts)
                ),
            ]
        )
    return TrustedDirectorDependencyArtifactSnapshotV2(
        _payload_json=_canonical_json_bytes(payload).decode("utf-8"),
        _message_lines=tuple(message_lines),
    )


def project_director_dependency_artifact_snapshot(
    context: dict[str, Any],
    snapshot: TrustedDirectorDependencyArtifactSnapshotV2 | None,
) -> None:
    """Reject caller presets and project only an exact internal trusted token."""

    context.pop("actual_sibling_exports", None)
    metadata = _mapping(context.get("metadata"))
    metadata.pop("actual_sibling_exports", None)
    if type(snapshot) is TrustedDirectorDependencyArtifactSnapshotV2:
        payload = snapshot.payload()
        context["actual_sibling_exports"] = payload
        metadata["actual_sibling_exports"] = snapshot.payload()
    context["metadata"] = metadata
