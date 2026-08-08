"""Pure canonicalization for Chief Engineer project-completion contracts.

This module performs no I/O and owns no lifecycle status.  It only produces a
stable canonical seed and its content-derived identity so downstream cells can
bind the same completion obligations without creating another fact source.
"""

from __future__ import annotations

from collections.abc import Sequence

from polaris.cells.chief_engineer.blueprint.public.contracts import (
    _PROJECT_COMPLETION_CONTRACT_ID_PREFIX,
    _PROJECT_COMPLETION_CONTRACT_SCHEMA_V1,
    ProjectCompletionContractV1,
    ProjectCompletionObligationsV1,
    ProjectKindAuthorityV1,
    ProjectKindV1,
    VerificationCommandAuthorityV1,
    _canonical_project_completion_contract_seed,
    _canonicalize_completion_id_tuple,
    _project_completion_contract_hash,
    _project_completion_contract_id,
)

PROJECT_COMPLETION_CONTRACT_SCHEMA_V1 = _PROJECT_COMPLETION_CONTRACT_SCHEMA_V1
PROJECT_COMPLETION_CONTRACT_ID_PREFIX = _PROJECT_COMPLETION_CONTRACT_ID_PREFIX
canonicalize_exact_id_tuple = _canonicalize_completion_id_tuple
canonical_project_completion_contract_seed = _canonical_project_completion_contract_seed
project_completion_contract_hash = _project_completion_contract_hash
project_completion_contract_id = _project_completion_contract_id


def build_project_completion_contract(
    *,
    project_id: str,
    run_id: str,
    project_kind: ProjectKindV1,
    project_kind_authority: ProjectKindAuthorityV1,
    pm_contract_hash: str,
    covered_task_ids: Sequence[str],
    obligations: ProjectCompletionObligationsV1,
    completion_predicate_version: str,
    verifier_policy_hash: str,
    verifier_policy_snapshot_hash: str,
    verification_command_authority: Sequence[VerificationCommandAuthorityV1],
) -> ProjectCompletionContractV1:
    """Build one immutable, canonical, status-free completion contract."""

    return ProjectCompletionContractV1(
        project_id=project_id,
        run_id=run_id,
        project_kind=project_kind,
        project_kind_authority=project_kind_authority,
        pm_contract_hash=pm_contract_hash,
        covered_task_ids=tuple(covered_task_ids),
        obligations=obligations,
        completion_predicate_version=completion_predicate_version,
        verifier_policy_hash=verifier_policy_hash,
        verifier_policy_snapshot_hash=verifier_policy_snapshot_hash,
        verification_command_authority=tuple(verification_command_authority),
    )


__all__ = [
    "PROJECT_COMPLETION_CONTRACT_ID_PREFIX",
    "PROJECT_COMPLETION_CONTRACT_SCHEMA_V1",
    "build_project_completion_contract",
    "canonical_project_completion_contract_seed",
    "canonicalize_exact_id_tuple",
    "project_completion_contract_hash",
    "project_completion_contract_id",
]
