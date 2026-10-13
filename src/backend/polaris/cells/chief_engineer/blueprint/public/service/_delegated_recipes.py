"""Validate delegated CE verifier recipes before immutable contract construction.

Planning profile classification grants no launch capability. Runtime command
policy must still validate actual artifact bytes and executable identity.
"""

import shlex
from collections.abc import Mapping
from typing import Any

from polaris.cells.control_plane.verifier_policy.public import classify_verifier_command_profile

from ..contracts import ChiefEngineerBlueprintErrorV1, VerificationCommandAuthorityV1


def is_task_intent(task: Any) -> bool:
    return bool(task.planning_contract_version == "pm.task_intent.v1")


def prepare_delegated_recipes(
    command: Any, requirements: Mapping[str, Any], carrier: Any,
) -> tuple[Mapping[str, Any], tuple[VerificationCommandAuthorityV1, ...]]:
    obligations = requirements.get("obligations", {})
    verification = obligations.get("verification", []) if isinstance(obligations, Mapping) else []
    entrypoints = obligations.get("entrypoints", []) if isinstance(obligations, Mapping) else []
    if not isinstance(verification, list) or not isinstance(entrypoints, list):
        return requirements, tuple(carrier.verification_command_authority)
    tasks = {task.task_id: task for task in command.tasks}
    authorities = {item.authority_hash: item for item in carrier.verification_command_authority}
    normalized_rows = []
    declared_hashes = set()

    def recipe(owner: Any, modality: Any, argv: Any, cwd: Any) -> VerificationCommandAuthorityV1:
        task = tasks.get(owner)
        if task is None or not is_task_intent(task) or task.verification_authority != "chief_engineer":
            raise ValueError("CE recipe owner lacks authenticated verification delegation")
        authority = VerificationCommandAuthorityV1(task_id=owner, modality=modality, argv=argv, cwd=cwd)
        profile_id, error_code = classify_verifier_command_profile(
            workspace=command.workspace,
            modality=authority.modality,
            argv=authority.argv,
            cwd=authority.cwd,
        )
        if not profile_id or error_code:
            raise ValueError(f"CE recipe fails verifier proof profile: {error_code or 'untrusted_verifier_command'}")
        authorities[authority.authority_hash] = authority
        declared_hashes.add(authority.authority_hash)
        return authority

    try:
        for raw in verification:
            if not isinstance(raw, Mapping):
                normalized_rows.append(raw)
                continue
            row = dict(raw)
            has_recipe = row.get("argv") not in (None, [])
            if "argv" in row or "cwd" in row:
                argv = row.pop("argv", None)
                cwd = row.pop("cwd", ".")
                if has_recipe:
                    if row.get("applicability") == "not_applicable":
                        raise ValueError("CE recipe cannot be attached to not_applicable verifier")
                    authority = recipe(row.get("owner_task_id"), row.get("modality"), argv, cwd)
                    claimed_hash = row.get("command_authority_hash")
                    if claimed_hash not in (None, authority.authority_hash):
                        raise ValueError("CE recipe command_authority_hash does not bind exact argv/cwd")
                    row["command_authority_hash"] = authority.authority_hash
            known_hash = row.get("command_authority_hash")
            owner = tasks.get(row.get("owner_task_id"))
            if row.get("applicability") != "not_applicable" and any(is_task_intent(task) for task in command.tasks):
                if owner is None:
                    raise ValueError("CE recipe owner is outside authenticated tasks")
                if is_task_intent(owner):
                    bound_authority = authorities.get(known_hash)
                    if bound_authority is None:
                        raise ValueError("delegated CE verifier requires exact argv/cwd recipe")
                    if bound_authority.task_id != owner.task_id or bound_authority.modality != row.get("modality"):
                        raise ValueError("CE recipe hash does not match its owner and modality")
                    if not has_recipe:
                        recipe(owner.task_id, bound_authority.modality, bound_authority.argv, bound_authority.cwd)
            if isinstance(known_hash, str) and known_hash in authorities and row.get("applicability") == "required":
                declared_hashes.add(known_hash)
            normalized_rows.append(row)

        for row in entrypoints:
            if not isinstance(row, Mapping) or row.get("applicability") == "not_applicable":
                continue
            owner = tasks.get(row.get("owner_task_id"))
            if owner is not None and is_task_intent(owner):
                authority = recipe(owner.task_id, "entrypoint", shlex.split(str(row.get("command") or "")), ".")
                if row.get("applicability") != "required":
                    declared_hashes.discard(authority.authority_hash)

        for authority in carrier.verification_command_authority:
            if is_task_intent(tasks[authority.task_id]) and authority.authority_hash not in declared_hashes:
                raise ValueError(
                    f"explicit user command constraint is missing from CE required recipes: {authority.command}"
                )
    except (TypeError, ValueError) as exc:
        raise ChiefEngineerBlueprintErrorV1(
            message=str(exc),
            code="invalid_chief_engineer_verification_recipe",
            details={"repair_operation": "verification_recipe_upsert", "requires_contract_correction": True},
        ) from exc
    prepared = dict(requirements)
    prepared["obligations"] = {**dict(obligations), "verification": normalized_rows}
    return prepared, tuple(authorities.values())
