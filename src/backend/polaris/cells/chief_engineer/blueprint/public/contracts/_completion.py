"""Project-completion obligation and authority DTOs for chief_engineer.blueprint."""

from __future__ import annotations

import hashlib
import json
import shlex
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from polaris.cells.chief_engineer.blueprint.public.contracts._helpers import (
    _PROJECT_COMPLETION_CONTRACT_ID_PREFIX,
    _PROJECT_COMPLETION_CONTRACT_SCHEMA_V1,
    _PROJECT_COMPLETION_MAX_ARTIFACTS,
    _PROJECT_COMPLETION_MAX_ENTRYPOINTS,
    _PROJECT_COMPLETION_MAX_TASK_IDS,
    _PROJECT_COMPLETION_MAX_VERIFICATIONS,
    _PROJECT_COMPLETION_MAX_VERIFIER_REFS,
    _PROVENANCE_MAX_PATH_BYTES,
    _optional_completion_command,
    _project_kind_authority_hash,
    _require_completion_token,
    _require_exact_non_empty,
    _require_provenance_path,
    _require_provenance_sha256,
    _require_provenance_text,
    _require_verification_command_proof,
    _require_verifier_argv,
    _require_verifier_cwd,
    _verification_command_authority_hash,
)

ArtifactSemanticRoleV1 = Literal[
    "source",
    "manifest",
    "test",
    "entrypoint",
    "config",
    "docs",
    "assets",
]
ObligationApplicabilityV1 = Literal["required", "optional", "not_applicable"]
EntrypointKindV1 = Literal["cli", "web", "api", "library"]
VerificationModalityV1 = Literal["environment_prep", "build", "test", "lint", "entrypoint"]
ProjectKindV1 = Literal["application", "library"]

_ARTIFACT_SEMANTIC_ROLES = frozenset({"source", "manifest", "test", "entrypoint", "config", "docs", "assets"})
_OBLIGATION_APPLICABILITIES = frozenset({"required", "optional", "not_applicable"})
_ENTRYPOINT_KINDS = frozenset({"cli", "web", "api", "library"})
_VERIFICATION_MODALITIES = frozenset({"environment_prep", "build", "test", "lint", "entrypoint"})
_PROJECT_KINDS = frozenset({"application", "library"})


def _require_literal(name: str, value: str, allowed: frozenset[str]) -> str:
    token = _require_exact_non_empty(name, value)
    if token not in allowed:
        raise ValueError(f"{name} must be one of {sorted(allowed)}")
    return token


def _canonicalize_completion_id_tuple(
    name: str,
    values: object,
    *,
    require_items: bool = False,
    max_items: int | None = None,
) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        raise TypeError(f"{name} must be a list or tuple of strings")
    if max_items is not None and len(values) > max_items:
        raise ValueError(f"{name} must contain at most {max_items} items")
    normalized = tuple(_require_completion_token(f"{name}[{index}]", value) for index, value in enumerate(values))
    result = tuple(sorted(set(normalized)))
    if require_items and not result:
        raise ValueError(f"{name} must contain at least one item")
    return result


def _canonical_project_completion_contract_seed(
    *,
    schema_version: str,
    project_id: str,
    run_id: str,
    project_kind: str,
    project_kind_authority: ProjectKindAuthorityV1,
    pm_contract_hash: str,
    covered_task_ids: tuple[str, ...],
    obligations: Mapping[str, Any],
    completion_predicate_version: str,
    verifier_policy_hash: str,
    verifier_policy_snapshot_hash: str,
    verification_command_authority: tuple[VerificationCommandAuthorityV1, ...],
) -> dict[str, Any]:
    return {
        "schema_version": schema_version,
        "project_id": project_id,
        "run_id": run_id,
        "project_kind": project_kind,
        "project_kind_authority": project_kind_authority.to_dict(),
        "pm_contract_hash": pm_contract_hash,
        "covered_task_ids": list(covered_task_ids),
        "obligations": dict(obligations),
        "completion_predicate_version": completion_predicate_version,
        "verifier_policy_hash": verifier_policy_hash,
        "verifier_policy_snapshot_hash": verifier_policy_snapshot_hash,
        "verification_command_authority": [item.to_dict() for item in verification_command_authority],
    }


def _project_completion_contract_hash(seed: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(seed),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _project_completion_contract_id(contract_hash: str) -> str:
    return f"{_PROJECT_COMPLETION_CONTRACT_ID_PREFIX}{contract_hash[:24]}"


@dataclass(frozen=True)
class ArtifactObligationV1:
    """One required, optional, or explicitly inapplicable project artifact."""

    obligation_id: str
    path: str
    semantic_role: ArtifactSemanticRoleV1
    applicability: ObligationApplicabilityV1
    owner_task_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "obligation_id",
            _require_completion_token("obligation_id", self.obligation_id),
        )
        object.__setattr__(self, "path", _require_provenance_path("path", self.path))
        object.__setattr__(
            self,
            "semantic_role",
            _require_literal("semantic_role", self.semantic_role, _ARTIFACT_SEMANTIC_ROLES),
        )
        object.__setattr__(
            self,
            "applicability",
            _require_literal("applicability", self.applicability, _OBLIGATION_APPLICABILITIES),
        )
        owner_task_id = (
            _require_completion_token("owner_task_id", self.owner_task_id) if self.owner_task_id is not None else None
        )
        object.__setattr__(self, "owner_task_id", owner_task_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "obligation_id": self.obligation_id,
            "path": self.path,
            "semantic_role": self.semantic_role,
            "applicability": self.applicability,
            "owner_task_id": self.owner_task_id,
        }


@dataclass(frozen=True)
class EntrypointObligationV1:
    """One language-neutral executable or explicitly N/A entrypoint."""

    obligation_id: str
    kind: EntrypointKindV1
    applicability: ObligationApplicabilityV1
    owner_task_id: str | None = None
    source_path: str | None = None
    runtime_path: str | None = None
    command: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "obligation_id",
            _require_completion_token("obligation_id", self.obligation_id),
        )
        object.__setattr__(self, "kind", _require_literal("kind", self.kind, _ENTRYPOINT_KINDS))
        applicability = _require_literal(
            "applicability",
            self.applicability,
            _OBLIGATION_APPLICABILITIES,
        )
        object.__setattr__(self, "applicability", applicability)
        owner_task_id = (
            _require_completion_token("owner_task_id", self.owner_task_id) if self.owner_task_id is not None else None
        )
        source_path = (
            _require_provenance_path("source_path", self.source_path) if self.source_path is not None else None
        )
        runtime_path = (
            _require_provenance_path("runtime_path", self.runtime_path) if self.runtime_path is not None else None
        )
        command = _optional_completion_command("command", self.command)
        if applicability == "not_applicable":
            if source_path is not None or runtime_path is not None or command is not None:
                raise ValueError("not_applicable entrypoint must not declare a locator")
        elif source_path is None and runtime_path is None:
            raise ValueError("active entrypoint must declare source_path or runtime_path")
        object.__setattr__(self, "source_path", source_path)
        object.__setattr__(self, "runtime_path", runtime_path)
        object.__setattr__(self, "command", command)
        object.__setattr__(self, "owner_task_id", owner_task_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "obligation_id": self.obligation_id,
            "kind": self.kind,
            "applicability": self.applicability,
            "owner_task_id": self.owner_task_id,
            "source_path": self.source_path,
            "runtime_path": self.runtime_path,
            "command": self.command,
        }


@dataclass(frozen=True)
class ProjectKindAuthorityV1:
    """Factory-owned application/library classification and its immutable source."""

    project_kind: ProjectKindV1
    source_ref: str
    source_hash: str
    justification: str
    authority_hash: str = field(init=False)

    def __post_init__(self) -> None:
        project_kind = _require_literal("project_kind", self.project_kind, _PROJECT_KINDS)
        source_ref = _require_provenance_text(
            "source_ref",
            self.source_ref,
            max_utf8_bytes=_PROVENANCE_MAX_PATH_BYTES,
        )
        source_hash = _require_provenance_sha256("source_hash", self.source_hash)
        justification = _require_provenance_text(
            "justification",
            self.justification,
            max_utf8_bytes=512,
        )
        object.__setattr__(self, "project_kind", project_kind)
        object.__setattr__(self, "source_ref", source_ref)
        object.__setattr__(self, "source_hash", source_hash)
        object.__setattr__(self, "justification", justification)
        object.__setattr__(
            self,
            "authority_hash",
            _project_kind_authority_hash(
                project_kind=project_kind,
                source_ref=source_ref,
                source_hash=source_hash,
                justification=justification,
            ),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "project_kind": self.project_kind,
            "source_ref": self.source_ref,
            "source_hash": self.source_hash,
            "justification": self.justification,
            "authority_hash": self.authority_hash,
        }


@dataclass(frozen=True)
class VerificationCommandAuthorityV1:
    """One exact PM-owned verifier command and its declared semantic modality."""

    task_id: str
    modality: VerificationModalityV1
    argv: tuple[str, ...]
    cwd: str = "."
    command: str = field(init=False)
    authority_hash: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_completion_token("task_id", self.task_id))
        object.__setattr__(
            self,
            "modality",
            _require_literal("modality", self.modality, _VERIFICATION_MODALITIES),
        )
        argv = _require_verifier_argv(self.argv)
        _require_verification_command_proof(argv)
        cwd = _require_verifier_cwd(self.cwd)
        command = _optional_completion_command("command", shlex.join(argv))
        if command is None:  # pragma: no cover - argv is non-empty by construction.
            raise ValueError("verification command must be non-empty")
        object.__setattr__(self, "argv", argv)
        object.__setattr__(self, "cwd", cwd)
        object.__setattr__(self, "command", command)
        object.__setattr__(
            self,
            "authority_hash",
            _verification_command_authority_hash(
                task_id=self.task_id,
                modality=self.modality,
                argv=argv,
                cwd=cwd,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "modality": self.modality,
            "argv": list(self.argv),
            "cwd": self.cwd,
            "command": self.command,
            "authority_hash": self.authority_hash,
        }


@dataclass(frozen=True)
class VerificationObligationV1:
    """One command-backed verifier or explicit N/A verifier modality."""

    obligation_id: str
    modality: VerificationModalityV1
    command: str | None
    applicability: ObligationApplicabilityV1
    covers_obligation_ids: tuple[str, ...] = field(default_factory=tuple)
    owner_task_id: str | None = None
    command_authority_hash: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "obligation_id",
            _require_completion_token("obligation_id", self.obligation_id),
        )
        object.__setattr__(
            self,
            "modality",
            _require_literal("modality", self.modality, _VERIFICATION_MODALITIES),
        )
        applicability = _require_literal(
            "applicability",
            self.applicability,
            _OBLIGATION_APPLICABILITIES,
        )
        object.__setattr__(self, "applicability", applicability)
        owner_task_id = (
            _require_completion_token("owner_task_id", self.owner_task_id) if self.owner_task_id is not None else None
        )
        command = _optional_completion_command("command", self.command)
        covers_obligation_ids = _canonicalize_completion_id_tuple(
            "covers_obligation_ids",
            self.covers_obligation_ids,
            max_items=_PROJECT_COMPLETION_MAX_VERIFIER_REFS,
        )
        if applicability == "not_applicable":
            if command is not None:
                raise ValueError("not_applicable verification must not declare a command")
            if covers_obligation_ids:
                raise ValueError("not_applicable verification must not cover obligations")
            if self.command_authority_hash is not None:
                raise ValueError("not_applicable verification must not declare command authority")
        elif command is None:
            raise ValueError("active verification obligation requires a command")
        command_authority_hash = (
            _require_provenance_sha256("command_authority_hash", self.command_authority_hash)
            if self.command_authority_hash is not None
            else None
        )
        # Standalone obligation values may be assembled before the PM authority table is
        # available. ProjectCompletionContractV1 is the authoritative boundary and rejects
        # every active verifier whose exact command_authority_hash is absent or mismatched.
        object.__setattr__(self, "command", command)
        object.__setattr__(self, "covers_obligation_ids", covers_obligation_ids)
        object.__setattr__(self, "owner_task_id", owner_task_id)
        object.__setattr__(self, "command_authority_hash", command_authority_hash)

    def to_dict(self) -> dict[str, Any]:
        return {
            "obligation_id": self.obligation_id,
            "modality": self.modality,
            "command": self.command,
            "applicability": self.applicability,
            "covers_obligation_ids": list(self.covers_obligation_ids),
            "owner_task_id": self.owner_task_id,
            "command_authority_hash": self.command_authority_hash,
        }


@dataclass(frozen=True)
class ProjectCompletionObligationsV1:
    """Canonical obligation set; it contains no execution or lifecycle status."""

    artifacts: tuple[ArtifactObligationV1, ...]
    entrypoints: tuple[EntrypointObligationV1, ...]
    verification: tuple[VerificationObligationV1, ...]

    def __post_init__(self) -> None:
        collection_caps = (
            ("artifacts", self.artifacts, _PROJECT_COMPLETION_MAX_ARTIFACTS),
            ("entrypoints", self.entrypoints, _PROJECT_COMPLETION_MAX_ENTRYPOINTS),
            ("verification", self.verification, _PROJECT_COMPLETION_MAX_VERIFICATIONS),
        )
        for name, collection_values, max_items in collection_caps:
            if not isinstance(collection_values, (list, tuple)):
                raise TypeError(f"{name} must be a list or tuple")
            if len(collection_values) > max_items:
                raise ValueError(f"{name} must contain at most {max_items} obligations")

        groups: tuple[tuple[str, object, type[Any]], ...] = (
            ("artifacts", self.artifacts, ArtifactObligationV1),
            ("entrypoints", self.entrypoints, EntrypointObligationV1),
            ("verification", self.verification, VerificationObligationV1),
        )
        for name, group_values, expected_type in groups:
            if not isinstance(group_values, (list, tuple)):
                raise TypeError(f"{name} must be a list or tuple")
            if any(type(value) is not expected_type for value in group_values):
                raise TypeError(f"{name} contains a non-exact obligation type")

        artifacts = tuple(sorted(self.artifacts, key=lambda value: value.obligation_id))
        entrypoints = tuple(sorted(self.entrypoints, key=lambda value: value.obligation_id))
        verification = tuple(sorted(self.verification, key=lambda value: value.obligation_id))
        all_ids = (
            [item.obligation_id for item in artifacts]
            + [item.obligation_id for item in entrypoints]
            + [item.obligation_id for item in verification]
        )
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("duplicate obligation_id across project completion obligations")

        artifact_paths = [item.path for item in artifacts]
        if len(artifact_paths) != len(set(artifact_paths)):
            raise ValueError("duplicate artifact path across project completion obligations")

        object.__setattr__(self, "artifacts", artifacts)
        object.__setattr__(self, "entrypoints", entrypoints)
        object.__setattr__(self, "verification", verification)

    def to_dict(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "artifacts": [item.to_dict() for item in self.artifacts],
            "entrypoints": [item.to_dict() for item in self.entrypoints],
            "verification": [item.to_dict() for item in self.verification],
        }


@dataclass(frozen=True)
class ProjectCompletionContractV1:
    """Immutable CE-owned obligations required for project completion.

    This contract deliberately contains no progress, pass/fail, or lifecycle
    fields.  Runtime owners bind evidence to its content hash; they do not
    rewrite the obligations while work is in flight.
    """

    project_id: str
    run_id: str
    project_kind: ProjectKindV1
    project_kind_authority: ProjectKindAuthorityV1
    pm_contract_hash: str
    covered_task_ids: tuple[str, ...]
    obligations: ProjectCompletionObligationsV1
    completion_predicate_version: str
    verifier_policy_hash: str
    verifier_policy_snapshot_hash: str
    verification_command_authority: tuple[VerificationCommandAuthorityV1, ...]
    schema_version: Literal["polaris.project_completion_contract.v1"] = _PROJECT_COMPLETION_CONTRACT_SCHEMA_V1
    contract_id: str = field(init=False)
    contract_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != _PROJECT_COMPLETION_CONTRACT_SCHEMA_V1:
            raise ValueError(f"schema_version must equal {_PROJECT_COMPLETION_CONTRACT_SCHEMA_V1!r}")
        object.__setattr__(
            self,
            "project_id",
            _require_completion_token("project_id", self.project_id),
        )
        object.__setattr__(self, "run_id", _require_completion_token("run_id", self.run_id))
        project_kind = _require_literal("project_kind", self.project_kind, _PROJECT_KINDS)
        object.__setattr__(self, "project_kind", project_kind)
        if type(self.project_kind_authority) is not ProjectKindAuthorityV1:
            raise TypeError("project_kind_authority must be exact ProjectKindAuthorityV1")
        if self.project_kind_authority.project_kind != project_kind:
            raise ValueError("project_kind must match Factory-owned project_kind_authority")
        object.__setattr__(
            self,
            "pm_contract_hash",
            _require_provenance_sha256("pm_contract_hash", self.pm_contract_hash),
        )
        object.__setattr__(
            self,
            "covered_task_ids",
            _canonicalize_completion_id_tuple(
                "covered_task_ids",
                self.covered_task_ids,
                require_items=True,
                max_items=_PROJECT_COMPLETION_MAX_TASK_IDS,
            ),
        )
        if type(self.obligations) is not ProjectCompletionObligationsV1:
            raise TypeError("obligations must be exact ProjectCompletionObligationsV1")
        object.__setattr__(
            self,
            "completion_predicate_version",
            _require_completion_token(
                "completion_predicate_version",
                self.completion_predicate_version,
            ),
        )
        object.__setattr__(
            self,
            "verifier_policy_hash",
            _require_provenance_sha256("verifier_policy_hash", self.verifier_policy_hash),
        )
        object.__setattr__(
            self,
            "verifier_policy_snapshot_hash",
            _require_provenance_sha256(
                "verifier_policy_snapshot_hash",
                self.verifier_policy_snapshot_hash,
            ),
        )
        if not isinstance(self.verification_command_authority, (list, tuple)):
            raise TypeError("verification_command_authority must be a list or tuple")
        command_authority: list[VerificationCommandAuthorityV1] = []
        command_authority_hashes: set[str] = set()
        for index, item in enumerate(self.verification_command_authority):
            if type(item) is not VerificationCommandAuthorityV1:
                raise TypeError(f"verification_command_authority[{index}] must be exact VerificationCommandAuthorityV1")
            if item.authority_hash in command_authority_hashes:
                continue
            command_authority_hashes.add(item.authority_hash)
            command_authority.append(item)
        command_authority.sort(key=lambda item: item.authority_hash)
        if not command_authority:
            raise ValueError("project completion contract requires verification command authority")
        object.__setattr__(self, "verification_command_authority", tuple(command_authority))

        required_artifacts = tuple(item for item in self.obligations.artifacts if item.applicability == "required")
        if not required_artifacts:
            raise ValueError("project completion contract requires at least one required artifact")

        required_delivery_verifiers = tuple(
            item
            for item in self.obligations.verification
            if item.applicability == "required" and item.modality in {"build", "test", "lint"}
        )
        if not required_delivery_verifiers:
            raise ValueError("project completion contract requires a real required build/test/lint verifier")

        test_artifact_declarations = tuple(item for item in self.obligations.artifacts if item.semantic_role == "test")
        if not test_artifact_declarations:
            raise ValueError("project completion contract requires a test artifact declaration")
        required_test_artifacts = tuple(item for item in test_artifact_declarations if item.applicability == "required")
        explicit_na_test_artifacts = tuple(
            item for item in test_artifact_declarations if item.applicability == "not_applicable"
        )
        test_verifier_declarations = tuple(item for item in self.obligations.verification if item.modality == "test")
        if not test_verifier_declarations:
            raise ValueError("project completion contract requires a test verifier declaration")
        required_tests = tuple(item for item in test_verifier_declarations if item.applicability == "required")
        explicit_na_tests = tuple(item for item in test_verifier_declarations if item.applicability == "not_applicable")
        if bool(required_test_artifacts) != bool(required_tests):
            raise ValueError("required test artifact and required test verifier must be declared together")
        if bool(explicit_na_test_artifacts) != bool(explicit_na_tests):
            raise ValueError("not_applicable test artifact and test verifier must be declared together")
        if required_test_artifacts and (explicit_na_test_artifacts or explicit_na_tests):
            raise ValueError("test obligations cannot be both required and not_applicable")
        if not required_test_artifacts and not explicit_na_test_artifacts:
            raise ValueError("test artifact and verifier must be required together or explicitly not_applicable")

        environment_declarations = tuple(
            item for item in self.obligations.verification if item.modality == "environment_prep"
        )
        if not environment_declarations:
            raise ValueError("project completion contract requires an environment_prep declaration")

        if project_kind == "application":
            if not required_test_artifacts or not required_tests:
                raise ValueError("application project requires a required test artifact and required test verifier")
            if explicit_na_test_artifacts or explicit_na_tests:
                raise ValueError("application test obligations cannot be not_applicable")
            required_environment_preparation = tuple(
                item
                for item in self.obligations.verification
                if item.modality == "environment_prep" and item.applicability == "required"
            )
            if not required_environment_preparation:
                raise ValueError("application project requires a command-backed required environment_prep verifier")
            if any(item.applicability == "not_applicable" for item in environment_declarations):
                raise ValueError("application environment_prep cannot be both required and not_applicable")
            required_entrypoints = tuple(
                item
                for item in self.obligations.entrypoints
                if item.applicability == "required" and item.kind != "library"
            )
            if not required_entrypoints:
                raise ValueError("application project requires a required entrypoint")
            invalid_executable_entrypoints = tuple(
                item
                for item in required_entrypoints
                if item.command is None or (item.source_path is None and item.runtime_path is None)
            )
            if invalid_executable_entrypoints:
                raise ValueError(
                    "every required application entrypoint must be an executable probe "
                    "with command and source_path or runtime_path; invalid obligation_ids="
                    f"{[item.obligation_id for item in invalid_executable_entrypoints]}"
                )
        else:
            library_requires_environment_preparation = any(
                item.applicability == "required" for item in environment_declarations
            )
            library_environment_preparation_na = any(
                item.applicability == "not_applicable" for item in environment_declarations
            )
            if not library_requires_environment_preparation and not library_environment_preparation_na:
                raise ValueError("library environment_prep must be required or explicitly not_applicable")
            if library_requires_environment_preparation and library_environment_preparation_na:
                raise ValueError("library environment_prep cannot be both required and not_applicable")
            explicit_na_entrypoint = any(
                item.kind == "library" and item.applicability == "not_applicable"
                for item in self.obligations.entrypoints
            )
            if not explicit_na_entrypoint:
                raise ValueError("library project requires an explicit not_applicable entrypoint")

            active_entrypoints = tuple(
                item for item in self.obligations.entrypoints if item.applicability != "not_applicable"
            )
            if active_entrypoints:
                raise ValueError("library project explicit N/A contract forbids active entrypoint obligations")

        artifact_ids = {item.obligation_id for item in self.obligations.artifacts}
        artifact_paths = {item.path for item in self.obligations.artifacts}
        entrypoint_ids = {item.obligation_id for item in self.obligations.entrypoints}
        known_obligation_ids = artifact_ids | entrypoint_ids
        covered_task_ids = set(self.covered_task_ids)
        unknown_command_authority_tasks = {
            item.task_id for item in self.verification_command_authority if item.task_id not in covered_task_ids
        }
        if unknown_command_authority_tasks:
            raise ValueError(
                "verification command authority task_id is outside covered_task_ids; "
                f"task_ids={sorted(unknown_command_authority_tasks)}"
            )
        all_obligations: tuple[
            ArtifactObligationV1 | EntrypointObligationV1 | VerificationObligationV1,
            ...,
        ] = (
            *self.obligations.artifacts,
            *self.obligations.entrypoints,
            *self.obligations.verification,
        )
        for obligation in all_obligations:
            if obligation.applicability == "not_applicable":
                if obligation.owner_task_id is not None:
                    raise ValueError(
                        f"not_applicable obligation {obligation.obligation_id!r} must not declare owner_task_id"
                    )
                continue
            if obligation.owner_task_id is None:
                raise ValueError(f"active obligation {obligation.obligation_id!r} requires owner_task_id")
            if obligation.owner_task_id not in covered_task_ids:
                raise ValueError(f"obligation {obligation.obligation_id!r} owner_task_id is outside covered_task_ids")
        required_test_artifact_ids = {
            item.obligation_id
            for item in self.obligations.artifacts
            if item.semantic_role == "test" and item.applicability == "required"
        }

        for entrypoint in self.obligations.entrypoints:
            if entrypoint.applicability != "not_applicable" and (
                entrypoint.source_path is None and entrypoint.runtime_path is None
            ):
                raise ValueError(f"active entrypoint {entrypoint.obligation_id!r} requires source_path or runtime_path")
            # Source entrypoints must bind declared delivery artifacts.
            # Runtime locators may name compiler-generated output (for example
            # ``src/main.ts`` -> ``dist/main.js``), so they derive from the
            # declared source artifact and exact PM-authorized command instead
            # of pretending generated output is a PM source target. A
            # runtime-only entrypoint still requires a declared artifact.
            if entrypoint.source_path is not None and entrypoint.source_path not in artifact_paths:
                raise ValueError(
                    f"entrypoint {entrypoint.obligation_id!r} references undeclared artifact path "
                    f"{entrypoint.source_path!r}"
                )
            if (
                entrypoint.source_path is None
                and entrypoint.runtime_path is not None
                and entrypoint.runtime_path not in artifact_paths
            ):
                raise ValueError(
                    f"entrypoint {entrypoint.obligation_id!r} references undeclared artifact path "
                    f"{entrypoint.runtime_path!r}"
                )

        active_entrypoints = tuple(
            item for item in self.obligations.entrypoints if item.applicability != "not_applicable"
        )
        entrypoint_verifiers = tuple(
            item
            for item in self.obligations.verification
            if item.applicability != "not_applicable" and item.modality == "entrypoint"
        )
        for entrypoint in active_entrypoints:
            if not any(
                entrypoint.obligation_id in verifier.covers_obligation_ids
                and entrypoint.owner_task_id == verifier.owner_task_id
                and entrypoint.command == verifier.command
                for verifier in entrypoint_verifiers
            ):
                raise ValueError(
                    f"active entrypoint {entrypoint.obligation_id!r} requires an exact PM-authorized "
                    "entrypoint verifier with the same owner and command"
                )

        authority_by_hash = {item.authority_hash: item for item in self.verification_command_authority}
        for verifier in self.obligations.verification:
            covered_ids = set(verifier.covers_obligation_ids)
            unknown_ids = covered_ids - known_obligation_ids
            if unknown_ids:
                raise ValueError(
                    f"verifier {verifier.obligation_id!r} references unknown obligation ids {sorted(unknown_ids)}"
                )
            if verifier.applicability == "not_applicable":
                continue
            authority = authority_by_hash.get(str(verifier.command_authority_hash))
            if authority is None:
                raise ValueError(f"verifier {verifier.obligation_id!r} command_authority_hash is not PM-authorized")
            if (
                authority.task_id != verifier.owner_task_id
                or authority.modality != verifier.modality
                or authority.command != verifier.command
            ):
                raise ValueError(
                    f"verifier {verifier.obligation_id!r} does not match its exact PM argv/cwd/modality owner authority"
                )
            if verifier.modality == "entrypoint":
                if not covered_ids or not covered_ids.issubset(entrypoint_ids):
                    raise ValueError(
                        f"entrypoint verifier {verifier.obligation_id!r} must cover entrypoint obligation ids only"
                    )
                continue
            if verifier.applicability == "required" and not covered_ids.intersection(artifact_ids):
                raise ValueError(
                    f"required non-entrypoint verifier {verifier.obligation_id!r} must cover owned artifact obligations"
                )
            if (
                verifier.modality == "test"
                and verifier.applicability == "required"
                and not covered_ids.intersection(required_test_artifact_ids)
            ):
                raise ValueError(
                    f"required test verifier {verifier.obligation_id!r} must cover at least one required test artifact"
                )

        contract_hash = _project_completion_contract_hash(self.to_seed_dict())
        object.__setattr__(self, "contract_hash", contract_hash)
        object.__setattr__(self, "contract_id", _project_completion_contract_id(contract_hash))

    def to_seed_dict(self) -> dict[str, Any]:
        """Return the canonical hash seed, excluding derived id and hash."""

        return _canonical_project_completion_contract_seed(
            schema_version=self.schema_version,
            project_id=self.project_id,
            run_id=self.run_id,
            project_kind=self.project_kind,
            project_kind_authority=self.project_kind_authority,
            pm_contract_hash=self.pm_contract_hash,
            covered_task_ids=self.covered_task_ids,
            obligations=self.obligations.to_dict(),
            completion_predicate_version=self.completion_predicate_version,
            verifier_policy_hash=self.verifier_policy_hash,
            verifier_policy_snapshot_hash=self.verifier_policy_snapshot_hash,
            verification_command_authority=self.verification_command_authority,
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ProjectCompletionContractV1:
        """Strictly reconstruct and verify one persisted completion contract."""

        if not isinstance(payload, Mapping):
            raise TypeError("project completion contract must be a mapping")
        expected_fields = {
            "schema_version",
            "project_id",
            "run_id",
            "project_kind",
            "project_kind_authority",
            "pm_contract_hash",
            "covered_task_ids",
            "obligations",
            "completion_predicate_version",
            "verifier_policy_hash",
            "verifier_policy_snapshot_hash",
            "verification_command_authority",
            "contract_id",
            "contract_hash",
        }
        if set(payload) != expected_fields:
            raise ValueError("project completion contract fields must match the canonical schema exactly")

        project_kind_authority_payload = payload.get("project_kind_authority")
        if not isinstance(project_kind_authority_payload, Mapping) or set(project_kind_authority_payload) != {
            "project_kind",
            "source_ref",
            "source_hash",
            "justification",
            "authority_hash",
        }:
            raise ValueError("project_kind_authority fields must match the canonical schema exactly")
        project_kind_authority = ProjectKindAuthorityV1(
            project_kind=project_kind_authority_payload["project_kind"],
            source_ref=project_kind_authority_payload["source_ref"],
            source_hash=project_kind_authority_payload["source_hash"],
            justification=project_kind_authority_payload["justification"],
        )
        if project_kind_authority_payload["authority_hash"] != project_kind_authority.authority_hash:
            raise ValueError("project_kind_authority derived identity is invalid")

        obligations_payload = payload.get("obligations")
        if not isinstance(obligations_payload, Mapping) or set(obligations_payload) != {
            "artifacts",
            "entrypoints",
            "verification",
        }:
            raise ValueError("project completion obligations fields must match the canonical schema exactly")

        def exact_rows(name: str, fields: set[str]) -> list[Mapping[str, Any]]:
            value = obligations_payload.get(name)
            if not isinstance(value, list):
                raise TypeError(f"obligations.{name} must be a list")
            rows: list[Mapping[str, Any]] = []
            for index, item in enumerate(value):
                if not isinstance(item, Mapping) or set(item) != fields:
                    raise ValueError(f"obligations.{name}[{index}] fields are invalid")
                rows.append(item)
            return rows

        artifacts = tuple(
            ArtifactObligationV1(
                obligation_id=item["obligation_id"],
                path=item["path"],
                semantic_role=item["semantic_role"],
                applicability=item["applicability"],
                owner_task_id=item["owner_task_id"],
            )
            for item in exact_rows(
                "artifacts",
                {"obligation_id", "path", "semantic_role", "applicability", "owner_task_id"},
            )
        )
        entrypoints = tuple(
            EntrypointObligationV1(
                obligation_id=item["obligation_id"],
                kind=item["kind"],
                applicability=item["applicability"],
                owner_task_id=item["owner_task_id"],
                source_path=item["source_path"],
                runtime_path=item["runtime_path"],
                command=item["command"],
            )
            for item in exact_rows(
                "entrypoints",
                {
                    "obligation_id",
                    "kind",
                    "applicability",
                    "owner_task_id",
                    "source_path",
                    "runtime_path",
                    "command",
                },
            )
        )
        verification = tuple(
            VerificationObligationV1(
                obligation_id=item["obligation_id"],
                modality=item["modality"],
                command=item["command"],
                applicability=item["applicability"],
                covers_obligation_ids=item["covers_obligation_ids"],
                owner_task_id=item["owner_task_id"],
                command_authority_hash=item["command_authority_hash"],
            )
            for item in exact_rows(
                "verification",
                {
                    "obligation_id",
                    "modality",
                    "command",
                    "applicability",
                    "covers_obligation_ids",
                    "owner_task_id",
                    "command_authority_hash",
                },
            )
        )
        command_authority_payload = payload.get("verification_command_authority")
        if not isinstance(command_authority_payload, list):
            raise TypeError("verification_command_authority must be a list")
        verification_command_authority_rows: list[VerificationCommandAuthorityV1] = []
        for index, item in enumerate(command_authority_payload):
            if not isinstance(item, Mapping) or set(item) != {
                "task_id",
                "modality",
                "argv",
                "cwd",
                "command",
                "authority_hash",
            }:
                raise ValueError(f"verification_command_authority[{index}] fields are invalid")
            authority = VerificationCommandAuthorityV1(
                task_id=item["task_id"],
                modality=item["modality"],
                argv=item["argv"],
                cwd=item["cwd"],
            )
            if item["command"] != authority.command or item["authority_hash"] != authority.authority_hash:
                raise ValueError(f"verification_command_authority[{index}] derived identity is invalid")
            verification_command_authority_rows.append(authority)
        verification_command_authority = tuple(verification_command_authority_rows)
        contract = cls(
            schema_version=payload["schema_version"],
            project_id=payload["project_id"],
            run_id=payload["run_id"],
            project_kind=payload["project_kind"],
            project_kind_authority=project_kind_authority,
            pm_contract_hash=payload["pm_contract_hash"],
            covered_task_ids=payload["covered_task_ids"],
            obligations=ProjectCompletionObligationsV1(
                artifacts=artifacts,
                entrypoints=entrypoints,
                verification=verification,
            ),
            completion_predicate_version=payload["completion_predicate_version"],
            verifier_policy_hash=payload["verifier_policy_hash"],
            verifier_policy_snapshot_hash=payload["verifier_policy_snapshot_hash"],
            verification_command_authority=verification_command_authority,
        )
        if payload["contract_id"] != contract.contract_id or payload["contract_hash"] != contract.contract_hash:
            raise ValueError("project completion contract derived identity is invalid")
        return contract

    def to_dict(self) -> dict[str, Any]:
        payload = self.to_seed_dict()
        payload["contract_id"] = self.contract_id
        payload["contract_hash"] = self.contract_hash
        return payload
