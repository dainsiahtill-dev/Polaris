"""Tests for the pure Chief Engineer project-completion contract builder."""

from __future__ import annotations

import inspect
import shlex
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from typing import Any

import polaris.cells.chief_engineer.blueprint.public.contracts as completion_contracts_module
import pytest
from polaris.cells.chief_engineer.blueprint.internal.project_completion_contract import (
    project_completion_contract_hash,
)
from polaris.cells.chief_engineer.blueprint.public import (
    ArtifactObligationV1,
    EntrypointObligationV1,
    ProjectCompletionContractV1,
    ProjectCompletionObligationsV1,
    ProjectKindAuthorityV1,
    VerificationCommandAuthorityV1,
    VerificationObligationV1,
    build_project_completion_contract,
)

_HASH_A = "a" * 64
_HASH_B = "b" * 64


def _project_kind_authority(project_kind: str = "application") -> ProjectKindAuthorityV1:
    return ProjectKindAuthorityV1(
        project_kind=project_kind,  # type: ignore[arg-type]
        source_ref="factory.committed_pm_catalog",
        source_hash="d" * 64,
        justification=(
            "catalog_explicit_library"
            if project_kind == "library"
            else "conservative_application_without_explicit_library_authority"
        ),
    )


def _application_obligations() -> ProjectCompletionObligationsV1:
    return ProjectCompletionObligationsV1(
        artifacts=(
            ArtifactObligationV1(
                obligation_id="artifact-tests",
                path="tests/test_main.py",
                semantic_role="test",
                applicability="required",
                owner_task_id="TASK-A",
            ),
            ArtifactObligationV1(
                obligation_id="artifact-main",
                path="src/main.py",
                semantic_role="source",
                applicability="required",
                owner_task_id="TASK-A",
            ),
        ),
        entrypoints=(
            EntrypointObligationV1(
                obligation_id="entrypoint-cli",
                kind="cli",
                source_path="src/main.py",
                command="python -m src.main",
                applicability="required",
                owner_task_id="TASK-A",
            ),
        ),
        verification=(
            VerificationObligationV1(
                obligation_id="verify-environment",
                modality="environment_prep",
                command="python -m pip install -e .",
                applicability="required",
                covers_obligation_ids=("artifact-main",),
                owner_task_id="TASK-A",
            ),
            VerificationObligationV1(
                obligation_id="verify-entrypoint",
                modality="entrypoint",
                command="python -m src.main",
                applicability="required",
                covers_obligation_ids=("entrypoint-cli",),
                owner_task_id="TASK-A",
            ),
            VerificationObligationV1(
                obligation_id="verify-test",
                modality="test",
                command="pytest -q",
                applicability="required",
                covers_obligation_ids=("artifact-tests",),
                owner_task_id="TASK-A",
            ),
        ),
    )


def _build_application(**overrides: Any) -> ProjectCompletionContractV1:
    obligations = overrides.pop("obligations", _application_obligations())
    authorities: list[VerificationCommandAuthorityV1] = []
    verification: list[VerificationObligationV1] = []
    for verifier in obligations.verification:
        if verifier.applicability == "not_applicable":
            verification.append(verifier)
            continue
        authority = VerificationCommandAuthorityV1(
            task_id=verifier.owner_task_id or "TASK-A",
            modality=verifier.modality,
            argv=tuple(shlex.split(str(verifier.command))),
            cwd=".",
        )
        authorities.append(authority)
        verification.append(replace(verifier, command_authority_hash=authority.authority_hash))
    obligations = replace(obligations, verification=tuple(verification))
    values: dict[str, Any] = {
        "project_id": "project-1",
        "run_id": "run-1",
        "project_kind": "application",
        "project_kind_authority": _project_kind_authority(),
        "pm_contract_hash": _HASH_A,
        "covered_task_ids": ("TASK-B", "TASK-A", "TASK-A"),
        "obligations": obligations,
        "completion_predicate_version": "project-completion.v1",
        "verifier_policy_hash": _HASH_B,
        "verifier_policy_snapshot_hash": "c" * 64,
        "verification_command_authority": tuple(authorities),
    }
    values.update(overrides)
    if "project_kind" in overrides and "project_kind_authority" not in overrides:
        values["project_kind_authority"] = _project_kind_authority(overrides["project_kind"])
    return build_project_completion_contract(**values)


def _hash_audit_obligations() -> ProjectCompletionObligationsV1:
    """Return obligations valid as both application and explicit-N/A library."""

    return ProjectCompletionObligationsV1(
        artifacts=(
            ArtifactObligationV1("artifact-api", "src/api.py", "source", "optional", "TASK-A"),
            ArtifactObligationV1("artifact-main", "src/main.py", "source", "required", "TASK-A"),
            ArtifactObligationV1("artifact-manifest", "pyproject.toml", "manifest", "required", "TASK-A"),
            ArtifactObligationV1("artifact-runtime", "dist/main.py", "entrypoint", "required", "TASK-A"),
            ArtifactObligationV1("artifact-test", "tests/test_main.py", "test", "required", "TASK-A"),
        ),
        entrypoints=(
            EntrypointObligationV1(
                "entrypoint-cli",
                "cli",
                "required",
                "TASK-A",
                source_path="src/main.py",
                runtime_path="dist/main.py",
                command="python dist/main.py",
            ),
            EntrypointObligationV1(
                "entrypoint-api",
                "api",
                "optional",
                "TASK-A",
                source_path="src/api.py",
                command="python -m src.api",
            ),
        ),
        verification=(
            VerificationObligationV1(
                "verify-build",
                "build",
                "python -m compileall src",
                "required",
                ("artifact-main",),
                "TASK-A",
            ),
            VerificationObligationV1(
                "verify-entrypoint",
                "entrypoint",
                "python dist/main.py",
                "required",
                ("entrypoint-cli",),
                "TASK-A",
            ),
            VerificationObligationV1(
                "verify-entrypoint-api",
                "entrypoint",
                "python -m src.api",
                "optional",
                ("entrypoint-api",),
                "TASK-A",
            ),
            VerificationObligationV1(
                "verify-lint",
                "lint",
                "ruff check src",
                "optional",
                ("artifact-main",),
                "TASK-A",
            ),
            VerificationObligationV1(
                "verify-setup",
                "environment_prep",
                "python -m pip install -e .",
                "required",
                ("artifact-manifest",),
                "TASK-A",
            ),
            VerificationObligationV1(
                "verify-test",
                "test",
                "pytest -q",
                "required",
                ("artifact-test",),
                "TASK-A",
            ),
        ),
    )


def test_builder_canonicalizes_order_deduplicates_task_ids_and_hashes_all_fields() -> None:
    contract = _build_application()

    assert contract.covered_task_ids == ("TASK-A", "TASK-B")
    assert tuple(item.obligation_id for item in contract.obligations.artifacts) == (
        "artifact-main",
        "artifact-tests",
    )
    assert len(contract.contract_hash) == 64
    assert contract.contract_id == f"project-completion-{contract.contract_hash[:24]}"
    assert contract == _build_application(covered_task_ids=("TASK-A", "TASK-B"))
    assert contract.contract_hash != _build_application(run_id="run-2").contract_hash
    assert contract.to_dict()["obligations"]["artifacts"][0]["path"] == "src/main.py"

    with pytest.raises(FrozenInstanceError):
        contract.project_id = "mutated"  # type: ignore[misc]


def test_hash_seed_excludes_derived_identity_and_every_seed_component_changes_hash() -> None:
    obligations = _hash_audit_obligations()
    base = _build_application(obligations=obligations)
    seed = base.to_seed_dict()

    assert set(seed) == {
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
    }
    assert "contract_id" not in seed
    assert "contract_hash" not in seed
    assert project_completion_contract_hash(seed) == base.contract_hash
    reconstructed = _build_application(
        covered_task_ids=tuple(seed["covered_task_ids"]),
        obligations=obligations,
    )
    assert reconstructed.contract_hash == base.contract_hash
    assert reconstructed.contract_id == base.contract_id

    seed_mutations: list[dict[str, Any]] = []
    for key, value in (
        ("schema_version", "polaris.project_completion_contract.v2"),
        ("project_id", "project-2"),
        ("run_id", "run-2"),
        ("project_kind", "library"),
        ("pm_contract_hash", "c" * 64),
        ("covered_task_ids", ["TASK-A", "TASK-C"]),
        ("completion_predicate_version", "project-completion.v2"),
        ("verifier_policy_hash", "d" * 64),
        ("verifier_policy_snapshot_hash", "e" * 64),
    ):
        mutated = deepcopy(seed)
        mutated[key] = value
        seed_mutations.append(mutated)

    nested_mutations = (
        ("project_kind_authority", None, "project_kind", "library"),
        ("project_kind_authority", None, "source_ref", "factory.other_source"),
        ("project_kind_authority", None, "source_hash", "e" * 64),
        ("project_kind_authority", None, "justification", "different_authority"),
        ("project_kind_authority", None, "authority_hash", "f" * 64),
        ("artifacts", 0, "obligation_id", "artifact-api-v2"),
        ("artifacts", 0, "path", "src/http_api.py"),
        ("artifacts", 0, "semantic_role", "config"),
        ("artifacts", 0, "applicability", "required"),
        ("entrypoints", 0, "obligation_id", "entrypoint-api-v2"),
        ("entrypoints", 0, "kind", "web"),
        ("entrypoints", 0, "source_path", "src/http_api.py"),
        ("entrypoints", 0, "runtime_path", "dist/api.py"),
        ("entrypoints", 0, "command", "python -m src.http_api"),
        ("entrypoints", 0, "applicability", "required"),
        ("verification", 2, "obligation_id", "verify-lint-v2"),
        ("verification", 2, "modality", "build"),
        ("verification", 2, "command", "ruff check src tests"),
        ("verification", 2, "applicability", "required"),
        ("verification", 2, "covers_obligation_ids", ["artifact-manifest"]),
    )
    for group, index, field_name, value in nested_mutations:
        mutated = deepcopy(seed)
        if group == "project_kind_authority":
            mutated[group][field_name] = value
        else:
            assert index is not None
            mutated["obligations"][group][index][field_name] = value
        seed_mutations.append(mutated)

    mutation_hashes = {project_completion_contract_hash(item) for item in seed_mutations}
    assert base.contract_hash not in mutation_hashes
    assert len(mutation_hashes) == len(seed_mutations)


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("project_id", " project-1"),
        ("run_id", "run-1 "),
        ("completion_predicate_version", ""),
        ("pm_contract_hash", "A" * 64),
        ("verifier_policy_hash", "not-a-hash"),
    ),
)
def test_contract_rejects_non_exact_identity_or_invalid_hash(field_name: str, value: str) -> None:
    with pytest.raises(ValueError):
        _build_application(**{field_name: value})


@pytest.mark.parametrize(
    "path",
    ("/tmp/main.py", "../main.py", "src/../main.py", "src\\main.py", "./src/main.py"),
)
def test_artifact_paths_are_canonical_safe_posix_relative(path: str) -> None:
    with pytest.raises(ValueError, match=r"POSIX|relative|dot|parent|canonical"):
        ArtifactObligationV1(
            obligation_id="artifact-main",
            path=path,
            semantic_role="source",
            applicability="required",
        )


def test_obligation_ids_are_unique_across_all_kinds_and_artifact_paths_are_unique() -> None:
    duplicate_id = "same-id"
    with pytest.raises(ValueError, match="duplicate obligation_id"):
        ProjectCompletionObligationsV1(
            artifacts=(ArtifactObligationV1(duplicate_id, "src/main.py", "source", "required"),),
            entrypoints=(
                EntrypointObligationV1(
                    duplicate_id,
                    "cli",
                    "required",
                    source_path="src/main.py",
                ),
            ),
            verification=(),
        )

    with pytest.raises(ValueError, match="duplicate artifact path"):
        ProjectCompletionObligationsV1(
            artifacts=(
                ArtifactObligationV1("source-a", "src/main.py", "source", "required"),
                ArtifactObligationV1("source-b", "src/main.py", "entrypoint", "required"),
            ),
            entrypoints=(),
            verification=(),
        )


def test_application_requires_artifact_entrypoint_and_test_verification() -> None:
    with pytest.raises(ValueError, match="required artifact"):
        _build_application(
            obligations=ProjectCompletionObligationsV1(
                artifacts=(ArtifactObligationV1("optional-doc", "README.md", "docs", "optional"),),
                entrypoints=_application_obligations().entrypoints,
                verification=_application_obligations().verification,
            )
        )

    with pytest.raises(ValueError, match="test artifact declaration"):
        _build_application(
            obligations=ProjectCompletionObligationsV1(
                artifacts=(ArtifactObligationV1("artifact-main", "src/main.py", "source", "required"),),
                entrypoints=_application_obligations().entrypoints,
                verification=_application_obligations().verification,
            )
        )

    with pytest.raises(ValueError, match="required entrypoint"):
        _build_application(
            obligations=ProjectCompletionObligationsV1(
                artifacts=_application_obligations().artifacts,
                entrypoints=(),
                verification=_application_obligations().verification,
            )
        )

    with pytest.raises(ValueError, match="test verifier declaration"):
        _build_application(
            obligations=ProjectCompletionObligationsV1(
                artifacts=_application_obligations().artifacts,
                entrypoints=_application_obligations().entrypoints,
                verification=(
                    VerificationObligationV1(
                        "verify-lint",
                        "lint",
                        "ruff check .",
                        "required",
                        ("artifact-main",),
                    ),
                ),
            )
        )


def test_application_rejects_explicit_not_applicable_test_exemption() -> None:
    obligations = ProjectCompletionObligationsV1(
        artifacts=(
            ArtifactObligationV1(
                "artifact-main",
                "src/main.py",
                "source",
                "required",
                "TASK-A",
            ),
            ArtifactObligationV1(
                "artifact-test-na",
                "tests",
                "test",
                "not_applicable",
            ),
        ),
        entrypoints=_application_obligations().entrypoints,
        verification=(
            _application_obligations().verification[0],
            VerificationObligationV1(
                "verify-build",
                "build",
                "python -m compileall src",
                "required",
                ("artifact-main",),
                "TASK-A",
            ),
            VerificationObligationV1(
                "verify-test-na",
                "test",
                None,
                "not_applicable",
            ),
            _application_obligations().verification[1],
        ),
    )

    with pytest.raises(ValueError, match=r"application.*required test"):
        _build_application(obligations=obligations)


def test_application_requires_command_backed_environment_preparation() -> None:
    with pytest.raises(ValueError, match="environment_prep"):
        _build_application(
            obligations=replace(
                _application_obligations(),
                verification=tuple(
                    item for item in _application_obligations().verification if item.modality != "environment_prep"
                ),
            )
        )


def test_library_requires_delivery_verifier_even_when_tests_are_explicitly_na() -> None:
    artifacts = (
        ArtifactObligationV1("artifact-lib", "src/lib.py", "source", "required", "TASK-A"),
        ArtifactObligationV1("artifact-test-na", "tests", "test", "not_applicable"),
    )
    verification = (
        VerificationObligationV1(
            "verify-environment",
            "environment_prep",
            "python -m pip install -e .",
            "required",
            ("artifact-lib",),
            "TASK-A",
        ),
        VerificationObligationV1("verify-test-na", "test", None, "not_applicable"),
    )
    with pytest.raises(ValueError, match=r"build/test/lint"):
        _build_application(
            project_kind="library",
            obligations=ProjectCompletionObligationsV1(
                artifacts=artifacts,
                entrypoints=(EntrypointObligationV1("entrypoint-na", "library", "not_applicable"),),
                verification=verification,
            ),
        )


def test_library_accepts_explicit_na_tests_environment_and_entrypoint_with_build() -> None:
    obligations = ProjectCompletionObligationsV1(
        artifacts=(
            ArtifactObligationV1("artifact-lib", "src/lib.py", "source", "required", "TASK-A"),
            ArtifactObligationV1("artifact-test-na", "tests", "test", "not_applicable"),
        ),
        entrypoints=(EntrypointObligationV1("entrypoint-na", "library", "not_applicable"),),
        verification=(
            VerificationObligationV1(
                "verify-build",
                "build",
                "python -m compileall src",
                "required",
                ("artifact-lib",),
                "TASK-A",
            ),
            VerificationObligationV1("verify-test-na", "test", None, "not_applicable"),
            VerificationObligationV1("verify-environment-na", "environment_prep", None, "not_applicable"),
        ),
    )

    assert _build_application(project_kind="library", obligations=obligations).project_kind == "library"


def test_library_requires_explicit_not_applicable_entrypoint_and_real_tests() -> None:
    obligations = ProjectCompletionObligationsV1(
        artifacts=(
            ArtifactObligationV1("artifact-lib", "src/lib.py", "source", "required", "TASK-A"),
            ArtifactObligationV1("artifact-test", "tests/test_lib.py", "test", "required", "TASK-A"),
        ),
        entrypoints=(
            EntrypointObligationV1(
                "entrypoint-na",
                "library",
                "not_applicable",
            ),
        ),
        verification=(
            VerificationObligationV1(
                "verify-build",
                "build",
                "python -m compileall src",
                "required",
                ("artifact-lib",),
                "TASK-A",
            ),
            VerificationObligationV1(
                "verify-test",
                "test",
                "pytest -q",
                "required",
                ("artifact-test",),
                "TASK-A",
            ),
            VerificationObligationV1("verify-environment-na", "environment_prep", None, "not_applicable"),
        ),
    )

    contract = _build_application(project_kind="library", obligations=obligations)
    assert contract.project_kind == "library"

    with pytest.raises(ValueError, match="not_applicable entrypoint"):
        _build_application(
            project_kind="library",
            obligations=ProjectCompletionObligationsV1(
                artifacts=obligations.artifacts,
                entrypoints=(),
                verification=obligations.verification,
            ),
        )


def test_library_not_applicable_entrypoint_is_exclusive_but_tests_remain_required() -> None:
    base = ProjectCompletionObligationsV1(
        artifacts=(
            ArtifactObligationV1("artifact-lib", "src/lib.py", "source", "required", "TASK-A"),
            ArtifactObligationV1("artifact-test", "tests/test_lib.py", "test", "required", "TASK-A"),
        ),
        entrypoints=(EntrypointObligationV1("entrypoint-na", "library", "not_applicable"),),
        verification=(
            VerificationObligationV1(
                "verify-build",
                "build",
                "python -m compileall src",
                "required",
                ("artifact-lib",),
                "TASK-A",
            ),
            VerificationObligationV1(
                "verify-test",
                "test",
                "pytest -q",
                "required",
                ("artifact-test",),
                "TASK-A",
            ),
            VerificationObligationV1("verify-environment-na", "environment_prep", None, "not_applicable"),
        ),
    )

    assert _build_application(project_kind="library", obligations=base).project_kind == "library"

    with pytest.raises(ValueError, match="active entrypoint"):
        _build_application(
            project_kind="library",
            obligations=replace(
                base,
                entrypoints=(
                    *base.entrypoints,
                    EntrypointObligationV1(
                        "entrypoint-cli",
                        "cli",
                        "required",
                        "TASK-A",
                        source_path="src/lib.py",
                        command="python src/lib.py",
                    ),
                ),
            ),
        )


@pytest.mark.parametrize(
    ("entrypoint", "error_pattern"),
    (
        (
            EntrypointObligationV1(
                "entrypoint-cli",
                "cli",
                "required",
                "TASK-A",
                source_path="src/undeclared.py",
                command="python src/undeclared.py",
            ),
            "undeclared artifact path",
        ),
        (
            EntrypointObligationV1(
                "entrypoint-cli",
                "cli",
                "required",
                "TASK-A",
                source_path="src/main.py",
                runtime_path="dist/undeclared.py",
                command="python dist/undeclared.py",
            ),
            "exact PM-authorized entrypoint verifier",
        ),
    ),
)
def test_entrypoint_paths_must_reference_declared_artifacts(
    entrypoint: EntrypointObligationV1,
    error_pattern: str,
) -> None:
    with pytest.raises(ValueError, match=error_pattern):
        _build_application(obligations=replace(_application_obligations(), entrypoints=(entrypoint,)))


def test_verifier_references_form_a_coherent_obligation_graph() -> None:
    base = _application_obligations()
    test_verifier = next(item for item in base.verification if item.modality == "test")
    entrypoint_verifier = next(item for item in base.verification if item.modality == "entrypoint")
    environment_verifier = next(item for item in base.verification if item.modality == "environment_prep")

    with pytest.raises(ValueError, match="unknown obligation"):
        _build_application(
            obligations=replace(
                base,
                verification=(
                    environment_verifier,
                    replace(test_verifier, covers_obligation_ids=("artifact-missing",)),
                    entrypoint_verifier,
                ),
            )
        )

    with pytest.raises(ValueError, match="required test artifact"):
        _build_application(
            obligations=replace(
                base,
                verification=(
                    environment_verifier,
                    replace(test_verifier, covers_obligation_ids=("artifact-main",)),
                    entrypoint_verifier,
                ),
            )
        )

    with pytest.raises(ValueError, match="entrypoint verifier"):
        _build_application(
            obligations=replace(
                base,
                verification=(
                    environment_verifier,
                    test_verifier,
                    replace(entrypoint_verifier, covers_obligation_ids=("artifact-main",)),
                ),
            )
        )

    with pytest.raises(ValueError, match="owned artifact"):
        _build_application(
            obligations=replace(
                base,
                verification=(
                    *base.verification,
                    VerificationObligationV1(
                        "verify-build",
                        "build",
                        "python -m compileall src",
                        "required",
                        ("entrypoint-cli",),
                        "TASK-A",
                    ),
                ),
            )
        )


def test_every_active_obligation_has_exact_covered_task_owner() -> None:
    base = _application_obligations()
    with pytest.raises(ValueError, match="requires owner_task_id"):
        _build_application(
            obligations=replace(
                base,
                artifacts=(replace(base.artifacts[0], owner_task_id=None), *base.artifacts[1:]),
            )
        )

    with pytest.raises(ValueError, match="outside covered_task_ids"):
        _build_application(
            obligations=replace(
                base,
                verification=(replace(base.verification[0], owner_task_id="TASK-UNKNOWN"), *base.verification[1:]),
            )
        )

    library = ProjectCompletionObligationsV1(
        artifacts=(
            ArtifactObligationV1("artifact-lib", "src/lib.py", "source", "required", "TASK-A"),
            ArtifactObligationV1("artifact-test", "tests/test_lib.py", "test", "required", "TASK-A"),
        ),
        entrypoints=(
            EntrypointObligationV1(
                "entrypoint-na",
                "library",
                "not_applicable",
                owner_task_id="TASK-A",
            ),
        ),
        verification=(
            VerificationObligationV1(
                "verify-build",
                "build",
                "python -m compileall src",
                "required",
                ("artifact-lib",),
                "TASK-A",
            ),
            VerificationObligationV1(
                "verify-test",
                "test",
                "pytest -q",
                "required",
                ("artifact-test",),
                "TASK-A",
            ),
            VerificationObligationV1("verify-environment-na", "environment_prep", None, "not_applicable"),
        ),
    )
    with pytest.raises(ValueError, match=r"not_applicable obligation.*must not declare owner_task_id"):
        _build_application(project_kind="library", obligations=library)


@pytest.mark.parametrize("value", ("bad\nidentity", "bad\u200bidentity", "x" * 129))
def test_identity_tokens_are_control_free_and_bounded(value: str) -> None:
    with pytest.raises(ValueError, match=r"control|128"):
        ArtifactObligationV1(value, "src/main.py", "source", "required")
    with pytest.raises(ValueError, match=r"control|128"):
        _build_application(project_id=value)


@pytest.mark.parametrize(
    "command",
    (
        "pytest\n-q",
        "python\x00main.py",
        "python\u200bmain.py",
        "python\u202emain.py",
        "\ufeffpython main.py",
        "pytest\u2028-q",
        "x" * 4097,
    ),
)
def test_commands_are_single_line_control_free_and_bounded(command: str) -> None:
    with pytest.raises(ValueError, match=r"single-line|control|4096"):
        VerificationObligationV1(
            "verify-test",
            "test",
            command,
            "required",
            ("artifact-tests",),
        )


@pytest.mark.parametrize(
    "argv",
    (
        ("echo", "ok"),
        ("printf", "ok"),
        ("python", "--version"),
        ("node", "--help"),
        ("python", "-m", "src.main", "--help"),
        ("true",),
        ("python", "-c", "pass"),
    ),
)
def test_verification_command_authority_rejects_non_proof_commands(
    argv: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError, match="proof-of-work"):
        VerificationCommandAuthorityV1(
            task_id="TASK-A",
            modality="test",
            argv=argv,
        )


@pytest.mark.parametrize(
    "argv",
    (
        ("python", "-m", "venv", ".venv"),
        ("go", "mod", "download"),
        ("cargo", "fetch"),
        ("npm", "install"),
        ("mvn", "dependency:go-offline"),
        ("cmake", "-S", ".", "-B", "build"),
    ),
)
def test_environment_preparation_accepts_commands_that_create_or_resolve_environment(
    argv: tuple[str, ...],
) -> None:
    authority = VerificationCommandAuthorityV1(
        task_id="TASK-A",
        modality="environment_prep",
        argv=argv,
    )

    assert authority.argv == argv


def test_completion_contract_rejects_nested_authority_subclass_lookalike() -> None:
    class LookalikeAuthority(VerificationCommandAuthorityV1):
        def to_dict(self) -> dict[str, Any]:
            payload = super().to_dict()
            payload["shadow_authority"] = "forged"
            return payload

    base = _build_application()
    lookalikes = tuple(
        LookalikeAuthority(
            task_id=item.task_id,
            modality=item.modality,
            argv=item.argv,
            cwd=item.cwd,
        )
        for item in base.verification_command_authority
    )

    with pytest.raises(TypeError, match="exact VerificationCommandAuthorityV1"):
        replace(base, verification_command_authority=lookalikes)

    class LookalikeProjectKindAuthority(ProjectKindAuthorityV1):
        def to_dict(self) -> dict[str, str]:
            payload = super().to_dict()
            payload["shadow_authority"] = "forged"
            return payload

    kind_lookalike = LookalikeProjectKindAuthority(
        project_kind="application",
        source_ref="factory.committed_pm_catalog",
        source_hash="d" * 64,
        justification="conservative_application_without_explicit_library_authority",
    )
    with pytest.raises(TypeError, match="exact ProjectKindAuthorityV1"):
        replace(base, project_kind_authority=kind_lookalike)

    class LookalikeArtifact(ArtifactObligationV1):
        pass

    with pytest.raises(TypeError, match="non-exact obligation type"):
        ProjectCompletionObligationsV1(
            artifacts=(
                LookalikeArtifact(
                    "artifact-main",
                    "src/main.py",
                    "source",
                    "required",
                    "TASK-A",
                ),
            ),
            entrypoints=(),
            verification=(),
        )


def test_obligation_collection_and_task_id_caps_fail_closed() -> None:
    with pytest.raises(ValueError, match=r"artifacts.*512"):
        ProjectCompletionObligationsV1(
            artifacts=tuple(
                ArtifactObligationV1(
                    f"artifact-{index}",
                    f"src/generated_{index}.py",
                    "source",
                    "optional",
                )
                for index in range(513)
            ),
            entrypoints=(),
            verification=(),
        )

    with pytest.raises(ValueError, match=r"entrypoints.*32"):
        replace(
            _application_obligations(),
            entrypoints=tuple(
                EntrypointObligationV1(
                    f"entrypoint-{index}",
                    "cli",
                    "optional",
                    source_path="src/main.py",
                    command=f"python src/main.py --mode={index}",
                )
                for index in range(33)
            ),
        )

    with pytest.raises(ValueError, match=r"verification.*64"):
        replace(
            _application_obligations(),
            verification=tuple(
                VerificationObligationV1(
                    f"verify-{index}",
                    "lint",
                    f"ruff check src --config=config/{index}.toml",
                    "optional",
                    ("artifact-main",),
                )
                for index in range(65)
            ),
        )

    with pytest.raises(ValueError, match=r"covered_task_ids.*256"):
        _build_application(covered_task_ids=tuple(f"TASK-{index}" for index in range(257)))


def test_public_contract_module_does_not_import_internal_builder() -> None:
    source = inspect.getsource(completion_contracts_module)
    assert "blueprint.internal.project_completion_contract" not in source


def test_active_verification_requires_command_and_not_applicable_forbids_one() -> None:
    with pytest.raises(ValueError, match="command"):
        VerificationObligationV1("verify-test", "test", None, "required")
    with pytest.raises(ValueError, match=r"not_applicable.*command"):
        VerificationObligationV1(
            "verify-test",
            "test",
            "pytest -q",
            "not_applicable",
        )


def test_required_entrypoint_needs_a_source_or_runtime_path() -> None:
    with pytest.raises(ValueError, match="source_path or runtime_path"):
        EntrypointObligationV1("entrypoint-cli", "cli", "required")

    with pytest.raises(ValueError, match="source_path or runtime_path"):
        EntrypointObligationV1(
            "entrypoint-cli",
            "cli",
            "required",
            command="python -m src.main",
        )


def test_required_application_entrypoint_needs_command_and_source_or_runtime_path() -> None:
    with pytest.raises(ValueError, match="executable probe"):
        _build_application(
            obligations=ProjectCompletionObligationsV1(
                artifacts=_application_obligations().artifacts,
                entrypoints=(
                    EntrypointObligationV1(
                        "entrypoint-cli",
                        "cli",
                        "required",
                        source_path="src/main.py",
                    ),
                ),
                verification=_application_obligations().verification,
            )
        )

    with pytest.raises(ValueError, match="executable probe"):
        _build_application(
            obligations=ProjectCompletionObligationsV1(
                artifacts=_application_obligations().artifacts,
                entrypoints=(
                    *_application_obligations().entrypoints,
                    EntrypointObligationV1(
                        "entrypoint-web",
                        "web",
                        "required",
                        source_path="web/index.html",
                    ),
                ),
                verification=_application_obligations().verification,
            )
        )


@pytest.mark.parametrize(
    ("path_field", "path"),
    (
        ("source_path", "/tmp/main.py"),
        ("source_path", "../main.py"),
        ("source_path", "src\\main.py"),
        ("runtime_path", "/tmp/main.py"),
        ("runtime_path", "dist/../main.py"),
        ("runtime_path", "dist\\main.py"),
    ),
)
def test_entrypoint_paths_are_canonical_safe_posix_relative(path_field: str, path: str) -> None:
    kwargs = {
        "obligation_id": "entrypoint-cli",
        "kind": "cli",
        "applicability": "required",
        "command": "python main.py",
        path_field: path,
    }
    with pytest.raises(ValueError, match=r"POSIX|relative|dot|parent|canonical"):
        EntrypointObligationV1(**kwargs)  # type: ignore[arg-type]
