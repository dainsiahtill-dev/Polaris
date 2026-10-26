"""Shared synthetic inputs for library-shaped project completion contracts.

Synthetic input data only; not an authority source and not a production API.

This module owns the shared *shape* of the library project completion contract
used by Chief Engineer and Factory contract tests. It builds plain dictionaries
and public ``VerificationCommandAuthorityV1`` DTOs only. It never issues
authority, never writes a catalog contract, and never calls a private carrier
issuer; authority ownership stays with its current owner.
"""

from __future__ import annotations

from typing import Any

from polaris.cells.chief_engineer.blueprint.public import (
    VerificationCommandAuthorityV1,
    VerificationModalityV1,
)

__all__ = [
    "DEFAULT_LIBRARY_BUILD_ARGV",
    "build_library_completion_requirements",
    "build_verification_command_authority",
]

#: Build verifier recipe used by the Chief Engineer library contract fixtures.
#: The Factory characterization suite deliberately keeps a different build
#: recipe (``compileall .``) because that suite freezes observed behavior of a
#: different workspace layout; both recipes are passed explicitly instead of
#: being hidden behind two duplicated function bodies.
DEFAULT_LIBRARY_BUILD_ARGV: tuple[str, ...] = ("python", "-m", "compileall", "src")


def build_verification_command_authority(
    task_id: str,
    modality: VerificationModalityV1,
    argv: tuple[str, ...],
) -> VerificationCommandAuthorityV1:
    """Build one public verification command authority DTO.

    Args:
        task_id: Owning task id bound to the verifier recipe.
        modality: Public verification modality literal.
        argv: Exact verifier argv tuple.

    Returns:
        The public ``VerificationCommandAuthorityV1`` DTO carrying its own
        ``authority_hash``.
    """

    return VerificationCommandAuthorityV1(
        task_id=task_id,
        modality=modality,
        argv=argv,
        cwd=".",
    )


def build_library_completion_requirements(
    *artifact_paths: str,
    owner_task_ids: tuple[str, ...],
    test_path: str,
    test_owner_task_id: str,
    build_argv: tuple[str, ...] = DEFAULT_LIBRARY_BUILD_ARGV,
) -> dict[str, Any]:
    """Build the library-shaped project completion contract input.

    The returned mapping is a fresh mutable structure on every call; callers
    may mutate it without affecting other callers.

    Args:
        *artifact_paths: Source artifact paths owned by ``owner_task_ids``.
        owner_task_ids: One owner task id per artifact path.
        test_path: Path of the test artifact.
        test_owner_task_id: Task id owning the test artifact.
        build_argv: Exact build verifier argv. Callers that freeze a different
            workspace layout pass their own recipe explicitly so the produced
            ``command_authority_hash`` stays unchanged.

    Returns:
        A fresh completion contract mapping with artifacts, one not-applicable
        library entrypoint, and build/test/environment verification rows.
    """

    assert len(owner_task_ids) == len(artifact_paths)
    artifact_rows: list[dict[str, Any]] = [
        {
            "obligation_id": f"artifact-{index}",
            "path": path,
            "semantic_role": "source",
            "applicability": "required",
            "owner_task_id": owner_task_ids[index - 1],
        }
        for index, path in enumerate(artifact_paths, start=1)
    ]
    test_artifact_id = "artifact-test"
    artifact_rows.append(
        {
            "obligation_id": test_artifact_id,
            "path": test_path,
            "semantic_role": "test",
            "applicability": "required",
            "owner_task_id": test_owner_task_id,
        }
    )
    build_authority = build_verification_command_authority(
        owner_task_ids[0],
        "build",
        build_argv,
    )
    test_authority = build_verification_command_authority(
        test_owner_task_id,
        "test",
        ("pytest", "-q"),
    )
    return {
        "obligations": {
            "artifacts": artifact_rows,
            "entrypoints": [
                {
                    "obligation_id": "entrypoint-library-na",
                    "kind": "library",
                    "applicability": "not_applicable",
                    "owner_task_id": None,
                    "source_path": None,
                    "runtime_path": None,
                    "command": None,
                }
            ],
            "verification": [
                {
                    "obligation_id": "verify-build",
                    "modality": "build",
                    "command_authority_hash": build_authority.authority_hash,
                    "applicability": "required",
                    "covers_obligation_ids": [artifact_rows[0]["obligation_id"]],
                    "owner_task_id": owner_task_ids[0],
                },
                {
                    "obligation_id": "verify-test",
                    "modality": "test",
                    "command_authority_hash": test_authority.authority_hash,
                    "applicability": "required",
                    "covers_obligation_ids": [test_artifact_id],
                    "owner_task_id": test_owner_task_id,
                },
                {
                    "obligation_id": "verify-environment-na",
                    "modality": "environment_prep",
                    "command_authority_hash": None,
                    "applicability": "not_applicable",
                    "covers_obligation_ids": [],
                    "owner_task_id": None,
                },
            ],
        },
    }
