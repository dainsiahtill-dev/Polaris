"""Validate delegated CE verifier recipes before immutable contract construction.

Planning profile classification grants no launch capability. Runtime command
policy must still validate actual artifact bytes and executable identity.
"""

import shlex
from collections.abc import Mapping
from typing import Any

from polaris.cells.control_plane.verifier_policy.public import get_verifier_command_profile_feedback

from ..contracts import ChiefEngineerBlueprintErrorV1, VerificationCommandAuthorityV1


def is_task_intent(task: Any) -> bool:
    return bool(task.planning_contract_version == "pm.task_intent.v1")


def prepare_delegated_recipes(
    command: Any,
    requirements: Mapping[str, Any],
    carrier: Any,
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
    recipe_diagnostics: list[dict[str, Any]] = []
    active_recipe: dict[str, Any] = {}
    recipe_errors: list[str] = []

    def record_failure(exc: Exception, diagnostic_count: int) -> None:
        recipe_errors.append(str(exc))
        if len(recipe_diagnostics) == diagnostic_count:
            recipe_diagnostics.append(
                {
                    **active_recipe,
                    "profile_feedback": {
                        "error_code": "invalid_verification_recipe",
                        "detail": str(exc),
                        "guidance_only": True,
                        "execution_authority": False,
                    },
                }
            )

    def recipe(owner: Any, modality: Any, argv: Any, cwd: Any) -> VerificationCommandAuthorityV1:
        task = tasks.get(owner)
        if task is None or not is_task_intent(task) or task.verification_authority != "chief_engineer":
            raise ValueError("CE recipe owner lacks authenticated verification delegation")
        authority = VerificationCommandAuthorityV1(task_id=owner, modality=modality, argv=argv, cwd=cwd)
        feedback = get_verifier_command_profile_feedback(
            workspace=command.workspace,
            modality=authority.modality,
            argv=authority.argv,
            cwd=authority.cwd,
        )
        profile_id, error_code = feedback["profile_id"], feedback["error_code"]
        if not profile_id or error_code:
            allowed_modalities = [
                candidate_modality
                for candidate_modality in ("environment_prep", "build", "test", "lint", "entrypoint")
                if get_verifier_command_profile_feedback(
                    workspace=command.workspace,
                    modality=candidate_modality,
                    argv=authority.argv,
                    cwd=authority.cwd,
                )["profile_id"]
            ]
            recipe_diagnostics.append(
                {
                    **active_recipe,
                    "modality": authority.modality,
                    "argv": list(authority.argv),
                    "cwd": authority.cwd,
                    "profile_feedback": feedback,
                    "expected_modality": authority.modality,
                    "allowed_recipe_modalities": allowed_modalities,
                    "allowed_recipe_collections": (
                        ["verification"] + (["entrypoints"] if "entrypoint" in allowed_modalities else [])
                        if allowed_modalities
                        else []
                    ),
                }
            )
            raise ValueError(f"CE recipe fails verifier proof profile: {error_code or 'untrusted_verifier_command'}")
        authorities[authority.authority_hash] = authority
        declared_hashes.add(authority.authority_hash)
        return authority

    for index, raw in enumerate(verification):
        diagnostic_count = len(recipe_diagnostics)
        try:
            if not isinstance(raw, Mapping):
                normalized_rows.append(raw)
                continue
            row = dict(raw)
            if any(is_task_intent(task) for task in command.tasks):
                # The model proposes semantic recipes, including N/A rows with
                # no argv. Machine hash presence is supplied before strict DTO
                # canonicalization; a supplied forged claim is still rejected.
                row.setdefault("command_authority_hash", None)
            active_recipe = {
                "collection": "verification",
                "source_path": ["obligations", "verification", index],
                "obligation_id": row.get("obligation_id"),
                "owner_task_id": row.get("owner_task_id"),
                "command_authority_hash": row.get("command_authority_hash"),
                "modality": row.get("modality"),
                "argv": row.get("argv"),
                "cwd": row.get("cwd", "."),
            }
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
                        recipe_diagnostics.append(
                            {
                                **active_recipe,
                                "modality": authority.modality,
                                "argv": list(authority.argv),
                                "cwd": authority.cwd,
                                "profile_feedback": {
                                    "error_code": "command_authority_hash_mismatch",
                                    "guidance_only": True,
                                    "execution_authority": False,
                                },
                                "allowed_recipe_modalities": [authority.modality],
                            }
                        )
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
        except (TypeError, ValueError) as exc:
            record_failure(exc, diagnostic_count)

    for index, row in enumerate(entrypoints):
        diagnostic_count = len(recipe_diagnostics)
        try:
            if not isinstance(row, Mapping) or row.get("applicability") == "not_applicable":
                continue
            owner = tasks.get(row.get("owner_task_id"))
            if owner is not None and is_task_intent(owner):
                active_recipe = {
                    "collection": "entrypoints",
                    "source_path": ["obligations", "entrypoints", index],
                    "obligation_id": row.get("obligation_id"),
                    "owner_task_id": row.get("owner_task_id"),
                    "command": row.get("command"),
                }
                authority = recipe(owner.task_id, "entrypoint", shlex.split(str(row.get("command") or "")), ".")
                if row.get("applicability") != "required":
                    declared_hashes.discard(authority.authority_hash)
        except (TypeError, ValueError) as exc:
            record_failure(exc, diagnostic_count)

    for authority in carrier.verification_command_authority:
        task = tasks.get(authority.task_id)
        if task is not None and is_task_intent(task) and authority.authority_hash not in declared_hashes:
            active_recipe = {
                "collection": "verification",
                "source_path": ["obligations", "verification"],
                "owner_task_id": authority.task_id,
                "modality": authority.modality,
                "argv": list(authority.argv),
                "cwd": authority.cwd,
                "command_authority_hash": authority.authority_hash,
            }
            record_failure(
                ValueError(
                    f"explicit user command constraint is missing from CE required recipes: {authority.command}"
                ),
                len(recipe_diagnostics),
            )
    if recipe_errors:
        collections = {row.get("collection") for row in recipe_diagnostics}
        raise ChiefEngineerBlueprintErrorV1(
            message="; ".join(recipe_errors),
            code="invalid_chief_engineer_verification_recipe",
            details={
                "repair_operation": "entrypoint_upsert"
                if collections == {"entrypoints"}
                else "verification_recipe_upsert",
                "requires_contract_correction": True,
                "verification_recipe_diagnostics": recipe_diagnostics,
            },
        )
    prepared = dict(requirements)
    prepared["obligations"] = {**dict(obligations), "verification": normalized_rows}
    return prepared, tuple(authorities.values())
