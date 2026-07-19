"""Strict PM/CE artifact provenance bound into Factory stage facts."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Literal, TypeAlias

from polaris.cells.chief_engineer.blueprint.public import (
    ChiefEngineerBlueprintErrorV1,
    QueryBlueprintProvenanceV1,
    query_blueprint_provenance,
)
from polaris.kernelone.events.final_request_evidence import canonical_role_final_request_hash
from polaris.kernelone.fs.guarded_regular_file_snapshot import (
    GuardedRegularFileSnapshotError,
    read_guarded_regular_file_snapshot,
)

from .factory_store import (
    FactoryArtifactSnapshotError,
    FactoryArtifactSnapshotV1,
    FactoryStore,
)

_BINDING_SCHEMA = "factory.stage_artifact_bindings.v1"
_PM_SCHEMA = "pm.plan_artifact.v1"
_CE_REVIEW_SCHEMA = "factory.chief_engineer_review.v2"
_CE_BLUEPRINT_SCHEMA = "chief_engineer.blueprint.v1"
_MAX_ARTIFACT_BYTES = 4 * 1024 * 1024
_MAX_BLUEPRINT_AGGREGATE_BYTES = 64 * 1024 * 1024
_MAX_TASKS = 512
_MAX_TARGETS_PER_TASK = 512
_MAX_TARGETS_TOTAL = 8192
_MAX_IDENTITY_UTF8_BYTES = 256
_MAX_LOGICAL_PATH_UTF8_BYTES = 1024
_LOWER_HEX = frozenset("0123456789abcdef")


class FactoryStageArtifactBindingError(RuntimeError):
    """Typed strict-validation failure for Factory stage artifact bindings."""

    def __init__(self, code: str, message: str, *, details: dict[str, object] | None = None) -> None:
        self.code = code
        self.details = details or {}
        super().__init__(f"{code}: {message}")


def _fail(code: str, message: str, **details: object) -> FactoryStageArtifactBindingError:
    return FactoryStageArtifactBindingError(code, message, details=details)


def _exact_object(value: object, fields: frozenset[str], *, code: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise _fail(code, "Artifact JSON value must be an exact object")
    row = dict(value)
    if set(row) != fields:
        raise _fail(
            code,
            "Artifact JSON object fields do not match the frozen contract",
            missing=sorted(fields.difference(row)),
            extra=sorted(set(row).difference(fields)),
        )
    return row


def _exact_identity(value: object, *, field: str) -> str:
    if type(value) is not str:
        raise _fail("factory_stage_artifact_identity_invalid", f"{field} must be an exact string")
    normalized = unicodedata.normalize("NFC", value)
    if (
        not normalized
        or normalized != value
        or normalized != normalized.strip()
        or len(normalized.encode("utf-8")) > _MAX_IDENTITY_UTF8_BYTES
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in normalized)
    ):
        raise _fail("factory_stage_artifact_identity_invalid", f"{field} is not an exact bounded identity")
    return normalized


def _exact_filename_identity(value: object, *, field: str) -> str:
    identity = _exact_identity(value, field=field)
    if identity in {".", ".."} or any(token in identity for token in ("/", "\\", "\x00")):
        raise _fail("factory_stage_artifact_identity_invalid", f"{field} is not a safe filename identity")
    return identity


def _exact_hash(value: object, *, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(character not in _LOWER_HEX for character in value):
        raise _fail("factory_stage_artifact_hash_invalid", f"{field} must be lower-case 64-hex")
    return value


def _exact_int(value: object, *, field: str, minimum: int = 0, maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise _fail("factory_stage_artifact_count_invalid", f"{field} is outside its exact integer bound")
    return value


def _logical_path(value: object, *, field: str = "logical_path") -> str:
    if type(value) is not str:
        raise _fail("factory_stage_artifact_logical_path_invalid", f"{field} must be an exact string")
    normalized = unicodedata.normalize("NFC", value)
    if (
        not normalized
        or normalized != value
        or normalized != normalized.strip()
        or normalized.startswith("/")
        or "\\" in normalized
        or any(unicodedata.category(character).startswith("C") for character in normalized)
        or len(normalized.encode("utf-8")) > _MAX_LOGICAL_PATH_UTF8_BYTES
    ):
        raise _fail("factory_stage_artifact_logical_path_invalid", f"{field} is not a bounded POSIX path")
    parts = normalized.split("/")
    if any(not part or part in {".", ".."} for part in parts):
        raise _fail("factory_stage_artifact_logical_path_invalid", f"{field} contains an unsafe component")
    if PurePosixPath(normalized).as_posix() != normalized:
        raise _fail("factory_stage_artifact_logical_path_invalid", f"{field} is not canonical")
    return normalized


def _strict_json(raw: bytes) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise _fail("factory_stage_artifact_invalid_utf8", "Artifact is not strict UTF-8") from exc

    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise _fail(
                    "factory_stage_artifact_duplicate_key",
                    "Artifact JSON contains a duplicate object key",
                    key=key,
                )
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise _fail(
            "factory_stage_artifact_invalid_json_constant",
            "Artifact JSON contains NaN or Infinity",
            constant=value,
        )

    try:
        payload = json.loads(text, object_pairs_hook=pairs_hook, parse_constant=reject_constant)
    except FactoryStageArtifactBindingError:
        raise
    except json.JSONDecodeError as exc:
        raise _fail("factory_stage_artifact_invalid_json", "Artifact is not valid strict JSON") from exc
    if type(payload) is not dict:
        raise _fail("factory_stage_artifact_root_not_object", "Artifact JSON root must be an object")
    return payload


def parse_factory_stage_artifact_json(raw: bytes) -> dict[str, Any]:
    """Parse one strict UTF-8 Factory artifact object without mutation.

    This is the Factory Cell's shared parser for persisted stage artifacts.  It
    rejects duplicate keys, non-finite JSON constants, invalid UTF-8, malformed
    JSON, and non-object roots before any caller can derive or persist facts.
    """

    if type(raw) is not bytes:
        raise _fail(
            "factory_stage_artifact_bytes_invalid",
            "Artifact input must be exact immutable bytes",
        )
    return _strict_json(raw)


def _source_relative_path(logical_path: str) -> str:
    return logical_path.removeprefix("runtime/")


def _expected_snapshot_ref(factory_run_id: str, raw_sha256: str) -> str:
    return f"runtime/{factory_run_id}/artifacts/stage-bindings/sha256/{raw_sha256[:2]}/{raw_sha256}.json"


def _read_source(source_root: object, logical_path: str, *, max_bytes: int = _MAX_ARTIFACT_BYTES) -> bytes:
    path = _logical_path(logical_path, field="logical_source_path")
    try:
        snapshot = read_guarded_regular_file_snapshot(
            str(source_root),
            _source_relative_path(path),
            max_bytes,
        )
    except GuardedRegularFileSnapshotError as exc:
        if exc.code == "guarded_snapshot_max_bytes_exceeded":
            raise _fail(
                "factory_stage_artifact_byte_limit_exceeded",
                "Artifact exceeds its exact byte bound",
                logical_path=path,
                max_bytes=max_bytes,
            ) from exc
        raise _fail(
            "factory_stage_artifact_source_unsafe",
            "Artifact source is not one stable no-follow regular file",
            logical_path=path,
            source_error_code=exc.code,
        ) from exc
    except (OSError, TypeError, ValueError) as exc:
        raise _fail(
            "factory_stage_artifact_source_unsafe",
            "Artifact source root or path is unsafe",
            logical_path=path,
        ) from exc
    if snapshot.size <= 0:
        raise _fail("factory_stage_artifact_empty", "Artifact must contain positive UTF-8 bytes")
    return snapshot.content


def _persist_snapshot(
    factory_store: FactoryStore,
    factory_run_id: str,
    raw: bytes,
) -> FactoryArtifactSnapshotV1:
    raw_sha256 = hashlib.sha256(raw).hexdigest()
    try:
        return factory_store.persist_stage_artifact_snapshot(factory_run_id, raw_sha256, raw)
    except FactoryArtifactSnapshotError as exc:
        raise _fail(
            "factory_stage_artifact_snapshot_failed",
            "FactoryStore immutable snapshot persistence failed closed",
            snapshot_error_code=exc.code,
        ) from exc


def _artifact_canonical_hash(domain: str, schema: str, document: Mapping[str, Any]) -> str:
    return canonical_role_final_request_hash(
        {
            "domain": domain,
            "document_schema_version": schema,
            "document": dict(document),
        }
    )


def _task_projection(task: Mapping[str, Any], *, factory_run_id: str) -> dict[str, Any]:
    if "id" not in task:
        raise _fail("factory_stage_artifact_pm_task_id_invalid", "PM task requires exact id; aliases are forbidden")
    if "task_id" in task or "uid" in task:
        raise _fail(
            "factory_stage_artifact_pm_task_id_invalid",
            "PM task identity aliases are forbidden even when exact id is present",
        )
    task_id = _exact_identity(task["id"], field="task.id")
    raw_targets = task.get("target_files")
    if type(raw_targets) is not list:
        raise _fail("factory_stage_artifact_target_files_invalid", "PM task target_files must be an exact list")
    if len(raw_targets) > _MAX_TARGETS_PER_TASK:
        raise _fail("factory_stage_artifact_target_limit_exceeded", "PM task has too many target paths")
    targets = [_logical_path(item, field="task.target_files") for item in raw_targets]
    if len(set(targets)) != len(targets):
        raise _fail("factory_stage_artifact_target_files_duplicate", "PM task target paths must be unique")
    return {"task_id": task_id, "target_files": sorted(targets)}


def _validate_pm_document(document: dict[str, Any], *, factory_run_id: str) -> tuple[dict[str, Any], ...]:
    root = _exact_object(
        document,
        frozenset({"schema_version", "generated_at", "source", "directive", "quality_gate", "tasks"}),
        code="factory_stage_artifact_pm_document_fields_invalid",
    )
    if root["schema_version"] != _PM_SCHEMA:
        raise _fail("factory_stage_artifact_pm_schema_invalid", "PM document schema is not pm.plan_artifact.v1")
    _exact_identity(root["generated_at"], field="pm.generated_at")
    if root["source"] != "pm_adapter_v2":
        raise _fail("factory_stage_artifact_pm_source_invalid", "PM document source is not pm_adapter_v2")
    if type(root["directive"]) is not str:
        raise _fail("factory_stage_artifact_pm_directive_invalid", "PM directive must be an exact string")
    gate = _exact_object(
        root["quality_gate"],
        frozenset({"score", "critical_issue_count", "summary", "signals"}),
        code="factory_stage_artifact_pm_quality_gate_fields_invalid",
    )
    _exact_int(gate["score"], field="quality_gate.score", minimum=0)
    _exact_int(gate["critical_issue_count"], field="quality_gate.critical_issue_count", minimum=0)
    if type(gate["summary"]) is not str or type(gate["signals"]) is not list:
        raise _fail("factory_stage_artifact_pm_quality_gate_invalid", "PM quality gate field types are invalid")

    raw_tasks = root["tasks"]
    if type(raw_tasks) is not list or not raw_tasks or len(raw_tasks) > _MAX_TASKS:
        raise _fail("factory_stage_artifact_pm_task_count_invalid", "PM tasks must contain 1..512 objects")
    projections: list[dict[str, Any]] = []
    task_ids: list[str] = []
    total_targets = 0
    for raw_task in raw_tasks:
        if type(raw_task) is not dict:
            raise _fail("factory_stage_artifact_pm_task_invalid", "Each PM task must be an exact object")
        projection = _task_projection(raw_task, factory_run_id=factory_run_id)
        task_id = projection["task_id"]
        if task_id in task_ids:
            raise _fail("factory_stage_artifact_pm_task_id_duplicate", "PM task ids must be unique", task_id=task_id)
        task_ids.append(task_id)
        total_targets += len(projection["target_files"])
        if total_targets > _MAX_TARGETS_TOTAL:
            raise _fail("factory_stage_artifact_target_limit_exceeded", "PM document has too many target paths")
        projections.append(projection)
    return tuple(projections)


def _pm_item_hashes(
    document: dict[str, Any],
    projections: tuple[dict[str, Any], ...],
    *,
    factory_run_id: str,
) -> tuple[str, str, str]:
    task_ids = sorted(str(row["task_id"]) for row in projections)
    task_id_vector_sha256 = canonical_role_final_request_hash(
        {
            "domain": "polaris.factory.pm_task_id_vector.v1",
            "factory_run_id": factory_run_id,
            "task_ids": task_ids,
        }
    )
    target_files_projection_sha256 = canonical_role_final_request_hash(
        {
            "domain": "polaris.factory.pm_target_files_projection.v1",
            "factory_run_id": factory_run_id,
            "tasks": sorted(projections, key=lambda row: str(row["task_id"])),
        }
    )
    canonical_json_sha256 = _artifact_canonical_hash(
        "polaris.factory.stage_artifact.pm_contract.v1",
        _PM_SCHEMA,
        document,
    )
    return canonical_json_sha256, task_id_vector_sha256, target_files_projection_sha256


@dataclass(frozen=True, slots=True)
class PMContractArtifactBindingV1:
    kind: Literal["pm_contract"]
    logical_source_path: str
    immutable_snapshot_ref: str
    document_schema_version: str
    utf8_byte_count: int
    task_count: int
    raw_sha256: str
    canonical_json_sha256: str
    task_id_vector_sha256: str
    target_files_projection_sha256: str

    def to_record(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "logical_source_path": self.logical_source_path,
            "immutable_snapshot_ref": self.immutable_snapshot_ref,
            "document_schema_version": self.document_schema_version,
            "utf8_byte_count": self.utf8_byte_count,
            "task_count": self.task_count,
            "raw_sha256": self.raw_sha256,
            "canonical_json_sha256": self.canonical_json_sha256,
            "task_id_vector_sha256": self.task_id_vector_sha256,
            "target_files_projection_sha256": self.target_files_projection_sha256,
        }

    @classmethod
    def from_record(cls, value: object) -> PMContractArtifactBindingV1:
        row = _exact_object(
            value, frozenset(cls.__dataclass_fields__), code="factory_stage_artifact_pm_item_fields_invalid"
        )
        if row["kind"] != "pm_contract":
            raise _fail("factory_stage_artifact_item_kind_invalid", "PM item kind must be pm_contract")
        if row["logical_source_path"] != "tasks/plan.json" or row["document_schema_version"] != _PM_SCHEMA:
            raise _fail("factory_stage_artifact_pm_item_identity_invalid", "PM item source/schema is not canonical")
        return cls(
            kind="pm_contract",
            logical_source_path="tasks/plan.json",
            immutable_snapshot_ref=_logical_path(row["immutable_snapshot_ref"], field="immutable_snapshot_ref"),
            document_schema_version=_PM_SCHEMA,
            utf8_byte_count=_exact_int(
                row["utf8_byte_count"], field="utf8_byte_count", minimum=1, maximum=_MAX_ARTIFACT_BYTES
            ),
            task_count=_exact_int(row["task_count"], field="task_count", minimum=1, maximum=_MAX_TASKS),
            raw_sha256=_exact_hash(row["raw_sha256"], field="raw_sha256"),
            canonical_json_sha256=_exact_hash(row["canonical_json_sha256"], field="canonical_json_sha256"),
            task_id_vector_sha256=_exact_hash(row["task_id_vector_sha256"], field="task_id_vector_sha256"),
            target_files_projection_sha256=_exact_hash(
                row["target_files_projection_sha256"], field="target_files_projection_sha256"
            ),
        )


@dataclass(frozen=True, slots=True)
class PMStageEventArtifactBindingV1:
    kind: Literal["pm_stage_event"]
    event_id: str
    chain_sequence: int
    chain_event_hash: str
    pm_immutable_snapshot_ref: str
    pm_raw_sha256: str
    pm_canonical_json_sha256: str
    pm_task_id_vector_sha256: str
    pm_target_files_projection_sha256: str

    def to_record(self) -> dict[str, object]:
        return {field: getattr(self, field) for field in self.__dataclass_fields__}

    @classmethod
    def from_record(cls, value: object) -> PMStageEventArtifactBindingV1:
        row = _exact_object(
            value, frozenset(cls.__dataclass_fields__), code="factory_stage_artifact_pm_event_item_fields_invalid"
        )
        if row["kind"] != "pm_stage_event":
            raise _fail("factory_stage_artifact_item_kind_invalid", "CE first item must be pm_stage_event")
        return cls(
            kind="pm_stage_event",
            event_id=_exact_identity(row["event_id"], field="event_id"),
            chain_sequence=_exact_int(row["chain_sequence"], field="chain_sequence", minimum=1),
            chain_event_hash=_exact_hash(row["chain_event_hash"], field="chain_event_hash"),
            pm_immutable_snapshot_ref=_logical_path(
                row["pm_immutable_snapshot_ref"], field="pm_immutable_snapshot_ref"
            ),
            pm_raw_sha256=_exact_hash(row["pm_raw_sha256"], field="pm_raw_sha256"),
            pm_canonical_json_sha256=_exact_hash(row["pm_canonical_json_sha256"], field="pm_canonical_json_sha256"),
            pm_task_id_vector_sha256=_exact_hash(row["pm_task_id_vector_sha256"], field="pm_task_id_vector_sha256"),
            pm_target_files_projection_sha256=_exact_hash(
                row["pm_target_files_projection_sha256"], field="pm_target_files_projection_sha256"
            ),
        )


@dataclass(frozen=True, slots=True)
class CEReviewManifestArtifactBindingV1:
    kind: Literal["ce_review_manifest"]
    logical_source_path: str
    immutable_snapshot_ref: str
    document_schema_version: str
    utf8_byte_count: int
    total_tasks: int
    generated_blueprints: int
    raw_sha256: str
    canonical_json_sha256: str

    def to_record(self) -> dict[str, object]:
        return {field: getattr(self, field) for field in self.__dataclass_fields__}

    @classmethod
    def from_record(cls, value: object) -> CEReviewManifestArtifactBindingV1:
        row = _exact_object(
            value, frozenset(cls.__dataclass_fields__), code="factory_stage_artifact_ce_review_item_fields_invalid"
        )
        if row["kind"] != "ce_review_manifest":
            raise _fail("factory_stage_artifact_item_kind_invalid", "CE second item must be ce_review_manifest")
        if row["document_schema_version"] != _CE_REVIEW_SCHEMA:
            raise _fail("factory_stage_artifact_ce_review_schema_invalid", "CE review item schema is invalid")
        return cls(
            kind="ce_review_manifest",
            logical_source_path=_logical_path(row["logical_source_path"], field="logical_source_path"),
            immutable_snapshot_ref=_logical_path(row["immutable_snapshot_ref"], field="immutable_snapshot_ref"),
            document_schema_version=_CE_REVIEW_SCHEMA,
            utf8_byte_count=_exact_int(
                row["utf8_byte_count"], field="utf8_byte_count", minimum=1, maximum=_MAX_ARTIFACT_BYTES
            ),
            total_tasks=_exact_int(row["total_tasks"], field="total_tasks", minimum=1, maximum=_MAX_TASKS),
            generated_blueprints=_exact_int(
                row["generated_blueprints"], field="generated_blueprints", minimum=1, maximum=_MAX_TASKS
            ),
            raw_sha256=_exact_hash(row["raw_sha256"], field="raw_sha256"),
            canonical_json_sha256=_exact_hash(row["canonical_json_sha256"], field="canonical_json_sha256"),
        )


@dataclass(frozen=True, slots=True)
class CEBlueprintArtifactBindingV1:
    kind: Literal["ce_blueprint"]
    ordinal: int
    logical_source_path: str
    immutable_snapshot_ref: str
    document_schema_version: str
    utf8_byte_count: int
    raw_sha256: str
    canonical_json_sha256: str
    blueprint_id: str
    task_id: str
    factory_run_id: str
    hash_scheme: str
    embedded_blueprint_hash: str
    recomputed_blueprint_hash: str
    embedded_pm_contract_hash: str
    recomputed_pm_contract_hash: str
    embedded_pm_task_canonical_sha256: str
    expected_pm_task_canonical_sha256: str
    embedded_pm_task_projection_sha256: str
    target_files_projection_sha256: str

    def to_record(self) -> dict[str, object]:
        return {field: getattr(self, field) for field in self.__dataclass_fields__}

    @classmethod
    def from_record(cls, value: object) -> CEBlueprintArtifactBindingV1:
        row = _exact_object(
            value, frozenset(cls.__dataclass_fields__), code="factory_stage_artifact_ce_blueprint_item_fields_invalid"
        )
        if row["kind"] != "ce_blueprint":
            raise _fail("factory_stage_artifact_item_kind_invalid", "CE blueprint item kind is invalid")
        if row["document_schema_version"] != _CE_BLUEPRINT_SCHEMA:
            raise _fail("factory_stage_artifact_ce_blueprint_schema_invalid", "CE blueprint item schema is invalid")
        if row["hash_scheme"] != "chief_engineer.blueprint_hash.v1":
            raise _fail("factory_stage_artifact_ce_blueprint_hash_scheme_invalid", "CE hash scheme is invalid")
        hash_fields = {
            field: _exact_hash(row[field], field=field)
            for field in (
                "raw_sha256",
                "canonical_json_sha256",
                "embedded_blueprint_hash",
                "recomputed_blueprint_hash",
                "embedded_pm_contract_hash",
                "recomputed_pm_contract_hash",
                "embedded_pm_task_canonical_sha256",
                "expected_pm_task_canonical_sha256",
                "embedded_pm_task_projection_sha256",
                "target_files_projection_sha256",
            )
        }
        if hash_fields["embedded_blueprint_hash"] != hash_fields["recomputed_blueprint_hash"]:
            raise _fail(
                "factory_stage_artifact_ce_blueprint_hash_mismatch",
                "Embedded and recomputed CE blueprint hashes differ",
            )
        if hash_fields["embedded_pm_contract_hash"] != hash_fields["recomputed_pm_contract_hash"]:
            raise _fail(
                "factory_stage_artifact_ce_pm_contract_hash_mismatch",
                "Embedded and recomputed PM contract hashes differ",
            )
        if hash_fields["embedded_pm_task_canonical_sha256"] != hash_fields["expected_pm_task_canonical_sha256"]:
            raise _fail(
                "factory_stage_artifact_ce_pm_task_hash_mismatch",
                "Embedded and expected PM task canonical hashes differ",
            )
        if hash_fields["embedded_pm_task_projection_sha256"] != hash_fields["target_files_projection_sha256"]:
            raise _fail(
                "factory_stage_artifact_ce_target_projection_mismatch",
                "Embedded PM-task and producer target projections differ",
            )
        return cls(
            kind="ce_blueprint",
            ordinal=_exact_int(row["ordinal"], field="ordinal", minimum=0, maximum=_MAX_TASKS - 1),
            logical_source_path=_logical_path(row["logical_source_path"], field="logical_source_path"),
            immutable_snapshot_ref=_logical_path(row["immutable_snapshot_ref"], field="immutable_snapshot_ref"),
            document_schema_version=_CE_BLUEPRINT_SCHEMA,
            utf8_byte_count=_exact_int(
                row["utf8_byte_count"], field="utf8_byte_count", minimum=1, maximum=_MAX_ARTIFACT_BYTES
            ),
            blueprint_id=_exact_filename_identity(row["blueprint_id"], field="blueprint_id"),
            task_id=_exact_identity(row["task_id"], field="task_id"),
            factory_run_id=_exact_filename_identity(row["factory_run_id"], field="factory_run_id"),
            hash_scheme="chief_engineer.blueprint_hash.v1",
            **hash_fields,
        )


FactoryStageArtifactItemV1: TypeAlias = (
    PMContractArtifactBindingV1
    | PMStageEventArtifactBindingV1
    | CEReviewManifestArtifactBindingV1
    | CEBlueprintArtifactBindingV1
)


def _binding_vector_hash(
    *,
    factory_run_id: str,
    stage: Literal["pm_planning", "chief_engineer_review"],
    item_records: list[dict[str, object]],
) -> str:
    return canonical_role_final_request_hash(
        {
            "domain": "polaris.factory.stage_artifact_binding_vector.v1",
            "schema_version": _BINDING_SCHEMA,
            "factory_run_id": factory_run_id,
            "stage": stage,
            "items": item_records,
        }
    )


@dataclass(frozen=True, slots=True)
class FactoryStageArtifactBindingsV1:
    schema_version: str
    factory_run_id: str
    stage: Literal["pm_planning", "chief_engineer_review"]
    items: tuple[FactoryStageArtifactItemV1, ...]
    binding_vector_sha256: str

    def to_record(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "factory_run_id": self.factory_run_id,
            "stage": self.stage,
            "items": [item.to_record() for item in self.items],
            "binding_vector_sha256": self.binding_vector_sha256,
        }

    @classmethod
    def _construct_unchecked(
        cls,
        *,
        factory_run_id: str,
        stage: Literal["pm_planning", "chief_engineer_review"],
        items: tuple[FactoryStageArtifactItemV1, ...],
        binding_vector_sha256: str,
    ) -> FactoryStageArtifactBindingsV1:
        """Construct only after the shared parser has proven all invariants."""

        return cls(
            schema_version=_BINDING_SCHEMA,
            factory_run_id=factory_run_id,
            stage=stage,
            items=items,
            binding_vector_sha256=binding_vector_sha256,
        )

    @classmethod
    def create(
        cls,
        *,
        factory_run_id: str,
        stage: Literal["pm_planning", "chief_engineer_review"],
        items: tuple[FactoryStageArtifactItemV1, ...],
    ) -> FactoryStageArtifactBindingsV1:
        run_id = _exact_filename_identity(factory_run_id, field="factory_run_id")
        allowed_item_types = {
            PMContractArtifactBindingV1,
            PMStageEventArtifactBindingV1,
            CEReviewManifestArtifactBindingV1,
            CEBlueprintArtifactBindingV1,
        }
        if type(items) is not tuple or not all(type(item) in allowed_item_types for item in items):
            raise _fail(
                "factory_stage_artifact_binding_items_invalid",
                "Binding create requires an exact tuple of frozen item DTOs",
            )
        item_records = [item.to_record() for item in items]
        vector_hash = _binding_vector_hash(
            factory_run_id=run_id,
            stage=stage,
            item_records=item_records,
        )
        # ``from_record`` owns the shared invariant parser and constructs via
        # ``_construct_unchecked``; it never calls ``create``, so this strict
        # roundtrip cannot recurse.
        return cls.from_record(
            {
                "schema_version": _BINDING_SCHEMA,
                "factory_run_id": run_id,
                "stage": stage,
                "items": item_records,
                "binding_vector_sha256": vector_hash,
            }
        )

    @classmethod
    def from_record(cls, value: object) -> FactoryStageArtifactBindingsV1:
        row = _exact_object(
            value,
            frozenset({"schema_version", "factory_run_id", "stage", "items", "binding_vector_sha256"}),
            code="factory_stage_artifact_binding_fields_invalid",
        )
        if row["schema_version"] != _BINDING_SCHEMA:
            raise _fail("factory_stage_artifact_binding_schema_invalid", "Binding schema is invalid")
        raw_stage = row["stage"]
        if raw_stage not in {"pm_planning", "chief_engineer_review"}:
            raise _fail("factory_stage_artifact_binding_stage_invalid", "Binding stage is invalid")
        stage: Literal["pm_planning", "chief_engineer_review"] = (
            "pm_planning" if raw_stage == "pm_planning" else "chief_engineer_review"
        )
        run_id = _exact_filename_identity(row["factory_run_id"], field="factory_run_id")
        raw_items = row["items"]
        if type(raw_items) is not list:
            raise _fail("factory_stage_artifact_binding_items_invalid", "Binding items must be an exact list")
        if stage == "pm_planning":
            if len(raw_items) != 1:
                raise _fail("factory_stage_artifact_binding_items_invalid", "PM binding requires exactly one item")
            pm_item = PMContractArtifactBindingV1.from_record(raw_items[0])
            if pm_item.immutable_snapshot_ref != _expected_snapshot_ref(run_id, pm_item.raw_sha256):
                raise _fail(
                    "factory_stage_artifact_snapshot_ref_invalid",
                    "PM immutable snapshot ref does not match the run/raw content address",
                )
            items: tuple[FactoryStageArtifactItemV1, ...] = (pm_item,)
        else:
            if len(raw_items) < 3:
                raise _fail(
                    "factory_stage_artifact_binding_items_invalid",
                    "CE binding requires PM, review, and blueprint items",
                )
            pm_event_item = PMStageEventArtifactBindingV1.from_record(raw_items[0])
            if pm_event_item.pm_immutable_snapshot_ref != _expected_snapshot_ref(run_id, pm_event_item.pm_raw_sha256):
                raise _fail(
                    "factory_stage_artifact_snapshot_ref_invalid",
                    "CE PM-event snapshot ref does not match the run/raw content address",
                )
            review_item = CEReviewManifestArtifactBindingV1.from_record(raw_items[1])
            if review_item.logical_source_path != f"runtime/state/blueprints/{run_id}.review.json":
                raise _fail(
                    "factory_stage_artifact_ce_review_path_invalid",
                    "CE review item logical source path is not canonical for the run",
                )
            if review_item.immutable_snapshot_ref != _expected_snapshot_ref(run_id, review_item.raw_sha256):
                raise _fail(
                    "factory_stage_artifact_snapshot_ref_invalid",
                    "CE review snapshot ref does not match the run/raw content address",
                )
            if review_item.generated_blueprints != len(raw_items) - 2 or review_item.total_tasks != len(raw_items) - 2:
                raise _fail(
                    "factory_stage_artifact_ce_review_count_mismatch",
                    "CE review item counts do not match blueprint binding rows",
                )
            parsed: list[FactoryStageArtifactItemV1] = [pm_event_item, review_item]
            seen_task_ids: set[str] = set()
            seen_blueprint_ids: set[str] = set()
            for ordinal, raw_item in enumerate(raw_items[2:]):
                blueprint_item = CEBlueprintArtifactBindingV1.from_record(raw_item)
                if blueprint_item.ordinal != ordinal:
                    raise _fail(
                        "factory_stage_artifact_blueprint_order_invalid", "CE blueprint ordinals are not contiguous"
                    )
                if blueprint_item.factory_run_id != run_id:
                    raise _fail(
                        "factory_stage_artifact_ce_blueprint_run_id_mismatch",
                        "CE blueprint binding belongs to another Factory run",
                    )
                if blueprint_item.logical_source_path != f"runtime/blueprints/{blueprint_item.blueprint_id}.json":
                    raise _fail(
                        "factory_stage_artifact_ce_blueprint_path_invalid",
                        "CE blueprint item logical source path is not canonical",
                    )
                if blueprint_item.immutable_snapshot_ref != _expected_snapshot_ref(run_id, blueprint_item.raw_sha256):
                    raise _fail(
                        "factory_stage_artifact_snapshot_ref_invalid",
                        "CE blueprint snapshot ref does not match the run/raw content address",
                    )
                if blueprint_item.task_id in seen_task_ids or blueprint_item.blueprint_id in seen_blueprint_ids:
                    raise _fail(
                        "factory_stage_artifact_ce_blueprint_identity_duplicate",
                        "CE blueprint binding identities must be unique",
                    )
                seen_task_ids.add(blueprint_item.task_id)
                seen_blueprint_ids.add(blueprint_item.blueprint_id)
                parsed.append(blueprint_item)
            items = tuple(parsed)
        observed_hash = _exact_hash(row["binding_vector_sha256"], field="binding_vector_sha256")
        expected_hash = _binding_vector_hash(
            factory_run_id=run_id,
            stage=stage,
            item_records=[item.to_record() for item in items],
        )
        if expected_hash != observed_hash:
            raise _fail("factory_stage_artifact_binding_vector_mismatch", "Binding vector hash does not match items")
        return cls._construct_unchecked(
            factory_run_id=run_id,
            stage=stage,
            items=items,
            binding_vector_sha256=observed_hash,
        )


def _pm_item_from_bytes(
    raw: bytes,
    snapshot: FactoryArtifactSnapshotV1,
    *,
    factory_run_id: str,
) -> tuple[PMContractArtifactBindingV1, dict[str, Any]]:
    document = _strict_json(raw)
    projections = _validate_pm_document(document, factory_run_id=factory_run_id)
    canonical_hash, task_vector_hash, target_projection_hash = _pm_item_hashes(
        document,
        projections,
        factory_run_id=factory_run_id,
    )
    item = PMContractArtifactBindingV1(
        kind="pm_contract",
        logical_source_path="tasks/plan.json",
        immutable_snapshot_ref=snapshot.logical_ref,
        document_schema_version=_PM_SCHEMA,
        utf8_byte_count=len(raw),
        task_count=len(projections),
        raw_sha256=snapshot.raw_sha256,
        canonical_json_sha256=canonical_hash,
        task_id_vector_sha256=task_vector_hash,
        target_files_projection_sha256=target_projection_hash,
    )
    return item, document


def build_pm_stage_artifact_bindings(
    *,
    factory_store: FactoryStore,
    source_root: object,
    factory_run_id: str,
) -> FactoryStageArtifactBindingsV1:
    """Freeze the exact persisted PM plan before its successful stage fact."""

    run_id = _exact_filename_identity(factory_run_id, field="factory_run_id")
    raw = _read_source(source_root, "tasks/plan.json")
    document = _strict_json(raw)
    _validate_pm_document(document, factory_run_id=run_id)
    snapshot = _persist_snapshot(factory_store, run_id, raw)
    item, _ = _pm_item_from_bytes(raw, snapshot, factory_run_id=run_id)
    return FactoryStageArtifactBindingsV1.create(factory_run_id=run_id, stage="pm_planning", items=(item,))


_CE_REVIEW_ROOT_FIELDS = frozenset(
    {
        "schema_version",
        "generated_at",
        "source",
        "factory_run_id",
        "task_plan",
        "total_tasks",
        "generated_blueprints",
        "llm_call_count",
        "portfolio",
        "project_interface_contract",
        "blueprints",
        "signals",
    }
)
_CE_REVIEW_ROW_FIELDS = frozenset(
    {
        "task_id",
        "status",
        "blueprint_id",
        "blueprint_path",
        "summary",
        "recommendations",
        "risks",
        "handoff_ready",
        "handoff_decision",
        "llm_evidence",
        "llm_blueprint_consumed",
        "llm_blueprint_keys",
        "portfolio_reference",
    }
)


def _validate_review_document(document: dict[str, Any], *, factory_run_id: str) -> tuple[dict[str, Any], ...]:
    root = _exact_object(document, _CE_REVIEW_ROOT_FIELDS, code="factory_stage_artifact_ce_review_fields_invalid")
    if root["schema_version"] != _CE_REVIEW_SCHEMA:
        raise _fail("factory_stage_artifact_ce_review_schema_invalid", "CE review schema is invalid")
    if root["source"] != "factory_stage_executor.chief_engineer_portfolio_review":
        raise _fail("factory_stage_artifact_ce_review_source_invalid", "CE review source is invalid")
    if root["factory_run_id"] != factory_run_id or root["task_plan"] != "tasks/plan.json":
        raise _fail("factory_stage_artifact_ce_review_identity_invalid", "CE review run/task-plan identity is invalid")
    _exact_identity(root["generated_at"], field="review.generated_at")
    total_tasks = _exact_int(root["total_tasks"], field="total_tasks", minimum=1, maximum=_MAX_TASKS)
    generated = _exact_int(root["generated_blueprints"], field="generated_blueprints", minimum=1, maximum=_MAX_TASKS)
    _exact_int(root["llm_call_count"], field="llm_call_count", minimum=0)
    if type(root["portfolio"]) is not dict or type(root["project_interface_contract"]) is not dict:
        raise _fail("factory_stage_artifact_ce_review_contract_invalid", "CE review portfolio fields must be objects")
    if type(root["signals"]) is not list or type(root["blueprints"]) is not list:
        raise _fail("factory_stage_artifact_ce_review_contract_invalid", "CE review list fields are invalid")
    if len(root["blueprints"]) != generated or total_tasks != generated:
        raise _fail("factory_stage_artifact_ce_review_count_mismatch", "CE review counts do not match manifest rows")
    rows: list[dict[str, Any]] = []
    seen_task_ids: set[str] = set()
    seen_blueprint_ids: set[str] = set()
    for raw_row in root["blueprints"]:
        row = _exact_object(
            raw_row,
            _CE_REVIEW_ROW_FIELDS,
            code="factory_stage_artifact_ce_review_row_fields_invalid",
        )
        task_id = _exact_identity(row["task_id"], field="review.task_id")
        blueprint_id = _exact_filename_identity(row["blueprint_id"], field="review.blueprint_id")
        expected_path = f"runtime/blueprints/{blueprint_id}.json"
        if row["blueprint_path"] != expected_path:
            raise _fail("factory_stage_artifact_ce_review_path_invalid", "CE review blueprint path is not canonical")
        if task_id in seen_task_ids or blueprint_id in seen_blueprint_ids:
            raise _fail("factory_stage_artifact_ce_review_duplicate", "CE review identities must be unique")
        seen_task_ids.add(task_id)
        seen_blueprint_ids.add(blueprint_id)
        if row["status"] != "generated":
            raise _fail(
                "factory_stage_artifact_ce_review_row_invalid",
                "Successful CE review rows must have status='generated'",
            )
        for field in ("recommendations", "risks", "llm_blueprint_keys"):
            if type(row[field]) is not list:
                raise _fail("factory_stage_artifact_ce_review_row_invalid", f"CE review {field} must be a list")
        for field in ("handoff_decision", "llm_evidence", "portfolio_reference"):
            if type(row[field]) is not dict:
                raise _fail("factory_stage_artifact_ce_review_row_invalid", f"CE review {field} must be an object")
        if row["handoff_ready"] is not True:
            raise _fail(
                "factory_stage_artifact_ce_review_row_invalid",
                "Successful CE review rows must have handoff_ready=true",
            )
        if type(row["llm_blueprint_consumed"]) is not bool:
            raise _fail("factory_stage_artifact_ce_review_row_invalid", "CE review boolean fields are invalid")
        rows.append(row)
    return tuple(rows)


def _pm_binding_from_committed_event(
    factory_store: FactoryStore,
    event: Mapping[str, Any],
    *,
    factory_run_id: str,
) -> tuple[PMStageEventArtifactBindingV1, dict[str, Any]]:
    if event.get("type") != "stage_completed" or event.get("stage") != "pm_planning":
        raise _fail("factory_stage_artifact_pm_event_invalid", "Referenced event is not a PM stage completion")
    if event.get("run_id") != factory_run_id or event.get("chain_schema_version") != "factory.event_chain.v1":
        raise _fail("factory_stage_artifact_pm_event_identity_invalid", "Referenced PM event identity is invalid")
    result = event.get("result")
    if (
        type(result) is not dict
        or result.get("stage") != "pm_planning"
        or result.get("status")
        not in {
            "success",
            "completed",
        }
    ):
        raise _fail("factory_stage_artifact_pm_event_not_success", "Referenced PM event is not successful")
    pm_binding = FactoryStageArtifactBindingsV1.from_record(event.get("stage_artifact_bindings"))
    if pm_binding.factory_run_id != factory_run_id or pm_binding.stage != "pm_planning":
        raise _fail("factory_stage_artifact_pm_event_binding_invalid", "PM event binding identity is invalid")
    pm_item = pm_binding.items[0]
    if not isinstance(pm_item, PMContractArtifactBindingV1):
        raise _fail("factory_stage_artifact_pm_event_binding_invalid", "PM event binding item is invalid")
    try:
        snapshot = factory_store.read_stage_artifact_snapshot(
            factory_run_id,
            pm_item.immutable_snapshot_ref,
            pm_item.raw_sha256,
            pm_item.utf8_byte_count,
        )
    except FactoryArtifactSnapshotError as exc:
        raise _fail(
            "factory_stage_artifact_pm_snapshot_invalid",
            "Referenced PM immutable snapshot failed strict re-read",
            snapshot_error_code=exc.code,
        ) from exc
    expected_pm_item, document = _pm_item_from_bytes(snapshot.content, snapshot, factory_run_id=factory_run_id)
    if expected_pm_item != pm_item:
        raise _fail("factory_stage_artifact_pm_event_binding_mismatch", "PM event binding does not match its snapshot")
    event_item = PMStageEventArtifactBindingV1(
        kind="pm_stage_event",
        event_id=_exact_identity(event.get("event_id"), field="pm_event.event_id"),
        chain_sequence=_exact_int(event.get("chain_sequence"), field="pm_event.chain_sequence", minimum=1),
        chain_event_hash=_exact_hash(event.get("chain_event_hash"), field="pm_event.chain_event_hash"),
        pm_immutable_snapshot_ref=pm_item.immutable_snapshot_ref,
        pm_raw_sha256=pm_item.raw_sha256,
        pm_canonical_json_sha256=pm_item.canonical_json_sha256,
        pm_task_id_vector_sha256=pm_item.task_id_vector_sha256,
        pm_target_files_projection_sha256=pm_item.target_files_projection_sha256,
    )
    return event_item, document


def _pm_tasks_by_id(document: Mapping[str, Any], *, factory_run_id: str) -> dict[str, dict[str, Any]]:
    projections = _validate_pm_document(dict(document), factory_run_id=factory_run_id)
    raw_tasks = document["tasks"]
    return {str(projection["task_id"]): dict(raw_tasks[index]) for index, projection in enumerate(projections)}


def _pm_task_canonical_hash(task: Mapping[str, Any], *, factory_run_id: str) -> str:
    return canonical_role_final_request_hash(
        {
            "domain": "polaris.factory.ce_pm_task.v1",
            "factory_run_id": factory_run_id,
            "task": dict(task),
        }
    )


def _pm_task_projection_hash(task: Mapping[str, Any], *, factory_run_id: str) -> str:
    projection = _task_projection(task, factory_run_id=factory_run_id)
    return canonical_role_final_request_hash(
        {
            "domain": "polaris.factory.pm_task_projection.v1",
            "factory_run_id": factory_run_id,
            "task": projection,
        }
    )


def build_chief_engineer_stage_artifact_bindings(
    *,
    factory_store: FactoryStore,
    source_root: object,
    factory_run_id: str,
    pm_stage_event: Mapping[str, Any],
) -> FactoryStageArtifactBindingsV1:
    """Freeze the exact PM link, CE review, and ordered CE blueprints."""

    run_id = _exact_filename_identity(factory_run_id, field="factory_run_id")
    if not isinstance(pm_stage_event, Mapping):
        raise _fail("factory_stage_artifact_pm_event_invalid", "PM stage event must be a mapping")
    pm_event_item, pm_document = _pm_binding_from_committed_event(
        factory_store,
        pm_stage_event,
        factory_run_id=run_id,
    )
    pm_tasks = _pm_tasks_by_id(pm_document, factory_run_id=run_id)

    review_path = f"runtime/state/blueprints/{run_id}.review.json"
    review_raw = _read_source(source_root, review_path)
    review_document = _strict_json(review_raw)
    rows = _validate_review_document(review_document, factory_run_id=run_id)
    review_task_ids = {str(row["task_id"]) for row in rows}
    committed_pm_task_ids = set(pm_tasks)
    if len(rows) != len(pm_tasks) or review_task_ids != committed_pm_task_ids:
        raise _fail(
            "factory_stage_artifact_ce_review_task_set_mismatch",
            "CE review rows must exactly cover the committed PM task set",
            missing=sorted(committed_pm_task_ids.difference(review_task_ids)),
            extra=sorted(review_task_ids.difference(committed_pm_task_ids)),
            expected_count=len(pm_tasks),
            observed_count=len(rows),
        )
    review_snapshot = _persist_snapshot(factory_store, run_id, review_raw)
    review_item = CEReviewManifestArtifactBindingV1(
        kind="ce_review_manifest",
        logical_source_path=review_path,
        immutable_snapshot_ref=review_snapshot.logical_ref,
        document_schema_version=_CE_REVIEW_SCHEMA,
        utf8_byte_count=len(review_raw),
        total_tasks=len(rows),
        generated_blueprints=len(rows),
        raw_sha256=review_snapshot.raw_sha256,
        canonical_json_sha256=_artifact_canonical_hash(
            "polaris.factory.stage_artifact.ce_review_manifest.v1",
            _CE_REVIEW_SCHEMA,
            review_document,
        ),
    )

    blueprint_items: list[CEBlueprintArtifactBindingV1] = []
    aggregate_bytes = 0
    for ordinal, row in enumerate(rows):
        task_id = str(row["task_id"])
        blueprint_id = str(row["blueprint_id"])
        expected_pm_task = pm_tasks.get(task_id)
        if expected_pm_task is None:
            raise _fail(
                "factory_stage_artifact_ce_task_identity_mismatch",
                "CE review task id does not exactly match a committed PM task",
                task_id=task_id,
            )
        logical_path = str(row["blueprint_path"])
        raw = _read_source(source_root, logical_path)
        aggregate_bytes += len(raw)
        if aggregate_bytes > _MAX_BLUEPRINT_AGGREGATE_BYTES:
            raise _fail(
                "factory_stage_artifact_blueprint_aggregate_limit_exceeded",
                "CE blueprint aggregate exceeds 64 MiB",
            )
        document = _strict_json(raw)
        try:
            provenance = query_blueprint_provenance(
                QueryBlueprintProvenanceV1(
                    blueprint=document,
                    expected_pm_task=expected_pm_task,
                    expected_factory_run_id=run_id,
                    expected_task_id=task_id,
                    expected_blueprint_id=blueprint_id,
                    expected_logical_path=logical_path,
                )
            )
        except (ChiefEngineerBlueprintErrorV1, TypeError, ValueError) as exc:
            raise _fail(
                "factory_stage_artifact_ce_blueprint_provenance_invalid",
                "CE public provenance query rejected the exact blueprint",
                blueprint_id=blueprint_id,
                producer_error_code=getattr(exc, "code", type(exc).__name__),
            ) from exc
        if not provenance.matches:
            raise _fail(
                "factory_stage_artifact_ce_blueprint_hash_mismatch",
                "CE public provenance query reported a producer hash mismatch",
                blueprint_id=blueprint_id,
            )
        snapshot = _persist_snapshot(factory_store, run_id, raw)
        embedded_pm_task = document.get("pm_task")
        if type(embedded_pm_task) is not dict:
            raise _fail("factory_stage_artifact_ce_pm_task_invalid", "CE blueprint pm_task must be an exact object")
        item = CEBlueprintArtifactBindingV1(
            kind="ce_blueprint",
            ordinal=ordinal,
            logical_source_path=logical_path,
            immutable_snapshot_ref=snapshot.logical_ref,
            document_schema_version=_CE_BLUEPRINT_SCHEMA,
            utf8_byte_count=len(raw),
            raw_sha256=snapshot.raw_sha256,
            canonical_json_sha256=_artifact_canonical_hash(
                "polaris.factory.stage_artifact.ce_blueprint.v1",
                _CE_BLUEPRINT_SCHEMA,
                document,
            ),
            blueprint_id=provenance.blueprint_id,
            task_id=provenance.task_id,
            factory_run_id=provenance.factory_run_id,
            hash_scheme=provenance.hash_scheme,
            embedded_blueprint_hash=provenance.embedded_blueprint_hash,
            recomputed_blueprint_hash=provenance.recomputed_blueprint_hash,
            embedded_pm_contract_hash=provenance.pm_contract_hash,
            recomputed_pm_contract_hash=provenance.recomputed_pm_contract_hash,
            embedded_pm_task_canonical_sha256=_pm_task_canonical_hash(
                embedded_pm_task,
                factory_run_id=run_id,
            ),
            expected_pm_task_canonical_sha256=_pm_task_canonical_hash(
                expected_pm_task,
                factory_run_id=run_id,
            ),
            embedded_pm_task_projection_sha256=_pm_task_projection_hash(
                embedded_pm_task,
                factory_run_id=run_id,
            ),
            target_files_projection_sha256=_pm_task_projection_hash(
                {"id": task_id, "target_files": list(provenance.target_files)},
                factory_run_id=run_id,
            ),
        )
        blueprint_items.append(item)

    items: tuple[FactoryStageArtifactItemV1, ...] = (pm_event_item, review_item, *blueprint_items)
    return FactoryStageArtifactBindingsV1.create(
        factory_run_id=run_id,
        stage="chief_engineer_review",
        items=items,
    )


@dataclass(frozen=True, slots=True)
class RevalidatedPMStageArtifactBindingV1:
    """Pure read-only proof of one successful PM stage binding."""

    binding: FactoryStageArtifactBindingsV1
    item: PMContractArtifactBindingV1
    document: dict[str, Any]
    task_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RevalidatedCEStageArtifactBindingV1:
    """Pure read-only proof of one ordered CE manifest/blueprint binding."""

    binding: FactoryStageArtifactBindingsV1
    review_document: dict[str, Any]
    blueprint_items: tuple[CEBlueprintArtifactBindingV1, ...]
    blueprint_documents: tuple[dict[str, Any], ...]


def revalidate_pm_stage_artifact_binding(
    *,
    factory_store: FactoryStore,
    factory_run_id: str,
    stage_event: Mapping[str, Any],
) -> RevalidatedPMStageArtifactBindingV1:
    """Strictly reread a PM immutable snapshot without persisting anything."""

    run_id = _exact_filename_identity(factory_run_id, field="factory_run_id")
    if not isinstance(stage_event, Mapping):
        raise _fail("factory_stage_artifact_pm_event_invalid", "PM stage event must be a mapping")
    _, document = _pm_binding_from_committed_event(factory_store, stage_event, factory_run_id=run_id)
    binding = FactoryStageArtifactBindingsV1.from_record(stage_event.get("stage_artifact_bindings"))
    item = binding.items[0]
    if not isinstance(item, PMContractArtifactBindingV1):
        raise _fail("factory_stage_artifact_pm_event_binding_invalid", "PM stage binding item is invalid")
    projections = _validate_pm_document(document, factory_run_id=run_id)
    return RevalidatedPMStageArtifactBindingV1(
        binding=binding,
        item=item,
        document=document,
        task_ids=tuple(sorted(str(row["task_id"]) for row in projections)),
    )


def revalidate_chief_engineer_stage_artifact_binding(
    *,
    factory_store: FactoryStore,
    factory_run_id: str,
    stage_event: Mapping[str, Any],
    pm_stage_event: Mapping[str, Any],
) -> RevalidatedCEStageArtifactBindingV1:
    """Strictly reread one CE review and ordered blueprint vector, read-only."""

    run_id = _exact_filename_identity(factory_run_id, field="factory_run_id")
    if not isinstance(stage_event, Mapping):
        raise _fail("factory_stage_artifact_ce_event_invalid", "CE stage event must be a mapping")
    if (
        stage_event.get("type") != "stage_completed"
        or stage_event.get("stage") != "chief_engineer_review"
        or stage_event.get("run_id") != run_id
        or stage_event.get("chain_schema_version") != "factory.event_chain.v1"
    ):
        raise _fail("factory_stage_artifact_ce_event_invalid", "Referenced event is not a current-run CE completion")
    result = stage_event.get("result")
    if (
        type(result) is not dict
        or result.get("stage") != "chief_engineer_review"
        or result.get("status") not in {"success", "completed"}
    ):
        raise _fail("factory_stage_artifact_ce_event_not_success", "Referenced CE event is not successful")

    pm_revalidated = revalidate_pm_stage_artifact_binding(
        factory_store=factory_store,
        factory_run_id=run_id,
        stage_event=pm_stage_event,
    )
    pm_tasks = _pm_tasks_by_id(pm_revalidated.document, factory_run_id=run_id)
    binding = FactoryStageArtifactBindingsV1.from_record(stage_event.get("stage_artifact_bindings"))
    if binding.factory_run_id != run_id or binding.stage != "chief_engineer_review":
        raise _fail("factory_stage_artifact_ce_event_binding_invalid", "CE event binding identity is invalid")
    pm_event_item = binding.items[0]
    review_item = binding.items[1]
    if not isinstance(pm_event_item, PMStageEventArtifactBindingV1) or not isinstance(
        review_item, CEReviewManifestArtifactBindingV1
    ):
        raise _fail("factory_stage_artifact_ce_event_binding_invalid", "CE binding prefix items are invalid")
    expected_pm_item = PMStageEventArtifactBindingV1(
        kind="pm_stage_event",
        event_id=_exact_identity(pm_stage_event.get("event_id"), field="pm_event.event_id"),
        chain_sequence=_exact_int(pm_stage_event.get("chain_sequence"), field="pm_event.chain_sequence", minimum=1),
        chain_event_hash=_exact_hash(pm_stage_event.get("chain_event_hash"), field="pm_event.chain_event_hash"),
        pm_immutable_snapshot_ref=pm_revalidated.item.immutable_snapshot_ref,
        pm_raw_sha256=pm_revalidated.item.raw_sha256,
        pm_canonical_json_sha256=pm_revalidated.item.canonical_json_sha256,
        pm_task_id_vector_sha256=pm_revalidated.item.task_id_vector_sha256,
        pm_target_files_projection_sha256=pm_revalidated.item.target_files_projection_sha256,
    )
    if pm_event_item != expected_pm_item:
        raise _fail("factory_stage_artifact_pm_event_binding_mismatch", "CE PM-event binding has drifted")

    try:
        review_snapshot = factory_store.read_stage_artifact_snapshot(
            run_id,
            review_item.immutable_snapshot_ref,
            review_item.raw_sha256,
            review_item.utf8_byte_count,
        )
    except FactoryArtifactSnapshotError as exc:
        raise _fail(
            "factory_stage_artifact_ce_review_snapshot_invalid",
            "Referenced CE review snapshot failed strict re-read",
            snapshot_error_code=exc.code,
        ) from exc
    review_document = _strict_json(review_snapshot.content)
    review_rows = _validate_review_document(review_document, factory_run_id=run_id)
    expected_review_item = CEReviewManifestArtifactBindingV1(
        kind="ce_review_manifest",
        logical_source_path=f"runtime/state/blueprints/{run_id}.review.json",
        immutable_snapshot_ref=review_snapshot.logical_ref,
        document_schema_version=_CE_REVIEW_SCHEMA,
        utf8_byte_count=len(review_snapshot.content),
        total_tasks=len(review_rows),
        generated_blueprints=len(review_rows),
        raw_sha256=review_snapshot.raw_sha256,
        canonical_json_sha256=_artifact_canonical_hash(
            "polaris.factory.stage_artifact.ce_review_manifest.v1",
            _CE_REVIEW_SCHEMA,
            review_document,
        ),
    )
    if review_item != expected_review_item:
        raise _fail("factory_stage_artifact_ce_review_binding_mismatch", "CE review binding has drifted")
    if len(review_rows) != len(pm_tasks) or {str(row["task_id"]) for row in review_rows} != set(pm_tasks):
        raise _fail("factory_stage_artifact_ce_review_task_set_mismatch", "CE review does not cover exact PM tasks")

    blueprint_items: list[CEBlueprintArtifactBindingV1] = []
    blueprint_documents: list[dict[str, Any]] = []
    aggregate_bytes = 0
    bound_items = binding.items[2:]
    if len(review_rows) != len(bound_items):
        raise _fail("factory_stage_artifact_ce_review_count_mismatch", "CE review and binding counts differ")
    for ordinal, (row, bound_item) in enumerate(zip(review_rows, bound_items, strict=True)):
        if not isinstance(bound_item, CEBlueprintArtifactBindingV1):
            raise _fail("factory_stage_artifact_ce_blueprint_item_invalid", "CE blueprint item type is invalid")
        task_id = str(row["task_id"])
        blueprint_id = str(row["blueprint_id"])
        logical_path = str(row["blueprint_path"])
        if (
            bound_item.ordinal != ordinal
            or bound_item.task_id != task_id
            or bound_item.blueprint_id != blueprint_id
            or bound_item.logical_source_path != logical_path
        ):
            raise _fail("factory_stage_artifact_ce_blueprint_order_invalid", "CE manifest/binding order differs")
        expected_pm_task = pm_tasks.get(task_id)
        if expected_pm_task is None:
            raise _fail("factory_stage_artifact_ce_task_identity_mismatch", "CE task is absent from PM contract")
        try:
            snapshot = factory_store.read_stage_artifact_snapshot(
                run_id,
                bound_item.immutable_snapshot_ref,
                bound_item.raw_sha256,
                bound_item.utf8_byte_count,
            )
        except FactoryArtifactSnapshotError as exc:
            raise _fail(
                "factory_stage_artifact_ce_blueprint_snapshot_invalid",
                "Referenced CE blueprint snapshot failed strict re-read",
                blueprint_id=blueprint_id,
                snapshot_error_code=exc.code,
            ) from exc
        aggregate_bytes += len(snapshot.content)
        if aggregate_bytes > _MAX_BLUEPRINT_AGGREGATE_BYTES:
            raise _fail(
                "factory_stage_artifact_blueprint_aggregate_limit_exceeded",
                "CE blueprint aggregate exceeds 64 MiB",
            )
        document = _strict_json(snapshot.content)
        try:
            provenance = query_blueprint_provenance(
                QueryBlueprintProvenanceV1(
                    blueprint=document,
                    expected_pm_task=expected_pm_task,
                    expected_factory_run_id=run_id,
                    expected_task_id=task_id,
                    expected_blueprint_id=blueprint_id,
                    expected_logical_path=logical_path,
                )
            )
        except (ChiefEngineerBlueprintErrorV1, TypeError, ValueError) as exc:
            raise _fail(
                "factory_stage_artifact_ce_blueprint_provenance_invalid",
                "CE public provenance query rejected the immutable blueprint",
                blueprint_id=blueprint_id,
            ) from exc
        if not provenance.matches:
            raise _fail("factory_stage_artifact_ce_blueprint_hash_mismatch", "CE blueprint hash mismatch")
        embedded_pm_task = document.get("pm_task")
        if type(embedded_pm_task) is not dict:
            raise _fail("factory_stage_artifact_ce_pm_task_invalid", "CE blueprint pm_task must be an object")
        expected_item = CEBlueprintArtifactBindingV1(
            kind="ce_blueprint",
            ordinal=ordinal,
            logical_source_path=logical_path,
            immutable_snapshot_ref=snapshot.logical_ref,
            document_schema_version=_CE_BLUEPRINT_SCHEMA,
            utf8_byte_count=len(snapshot.content),
            raw_sha256=snapshot.raw_sha256,
            canonical_json_sha256=_artifact_canonical_hash(
                "polaris.factory.stage_artifact.ce_blueprint.v1",
                _CE_BLUEPRINT_SCHEMA,
                document,
            ),
            blueprint_id=provenance.blueprint_id,
            task_id=provenance.task_id,
            factory_run_id=provenance.factory_run_id,
            hash_scheme=provenance.hash_scheme,
            embedded_blueprint_hash=provenance.embedded_blueprint_hash,
            recomputed_blueprint_hash=provenance.recomputed_blueprint_hash,
            embedded_pm_contract_hash=provenance.pm_contract_hash,
            recomputed_pm_contract_hash=provenance.recomputed_pm_contract_hash,
            embedded_pm_task_canonical_sha256=_pm_task_canonical_hash(embedded_pm_task, factory_run_id=run_id),
            expected_pm_task_canonical_sha256=_pm_task_canonical_hash(expected_pm_task, factory_run_id=run_id),
            embedded_pm_task_projection_sha256=_pm_task_projection_hash(embedded_pm_task, factory_run_id=run_id),
            target_files_projection_sha256=_pm_task_projection_hash(
                {"id": task_id, "target_files": list(provenance.target_files)},
                factory_run_id=run_id,
            ),
        )
        if expected_item != bound_item:
            raise _fail("factory_stage_artifact_ce_blueprint_binding_mismatch", "CE blueprint binding has drifted")
        blueprint_items.append(bound_item)
        blueprint_documents.append(document)

    return RevalidatedCEStageArtifactBindingV1(
        binding=binding,
        review_document=review_document,
        blueprint_items=tuple(blueprint_items),
        blueprint_documents=tuple(blueprint_documents),
    )


__all__ = [
    "CEBlueprintArtifactBindingV1",
    "CEReviewManifestArtifactBindingV1",
    "FactoryStageArtifactBindingError",
    "FactoryStageArtifactBindingsV1",
    "PMContractArtifactBindingV1",
    "PMStageEventArtifactBindingV1",
    "RevalidatedCEStageArtifactBindingV1",
    "RevalidatedPMStageArtifactBindingV1",
    "build_chief_engineer_stage_artifact_bindings",
    "build_pm_stage_artifact_bindings",
    "parse_factory_stage_artifact_json",
    "revalidate_chief_engineer_stage_artifact_binding",
    "revalidate_pm_stage_artifact_binding",
]
