"""Read-only readiness checks for statically known verifier input artifacts."""

from __future__ import annotations

from typing import Any

from polaris.cells.control_plane.verifier_policy.public import (
    get_verifier_command_profile_feedback,
    normalize_verifier_command_argv,
)
from polaris.cells.orchestration.pm_planning.public.artifact_contracts import (
    _pm_artifact_relative_path,
    _pm_direct_verifier_script,
    _pm_typed_verifiers,
)


def _literal_verifier_input(argv: list[str], cwd: str) -> str:
    script: str = _pm_direct_verifier_script(argv, cwd)
    if script:
        return script
    if argv[0] == "tsc":
        args = argv[1:]
    elif argv[:2] == ["npx", "tsc"]:
        args = argv[2:]
    elif argv[:3] == ["npx", "--no-install", "tsc"]:
        args = argv[3:]
    else:
        return ""
    projects: list[str] = []
    for index, arg in enumerate(args):
        if arg in {"-p", "--project"} and index + 1 < len(args):
            projects.append(args[index + 1])
        elif arg.startswith("--project="):
            projects.append(arg.removeprefix("--project="))
    if len(projects) != 1:
        return ""
    path = _pm_artifact_relative_path(projects[0])
    if not path or path.startswith("-"):
        return ""
    return path if cwd == "." else f"{cwd}/{path}"


def verification_operand_readiness_diagnostics(
    tasks: list[dict[str, Any]], *, workspace: str = ""
) -> list[dict[str, Any]]:
    """Diagnose only exact declared producers; do not infer npm scripts or effects."""

    producers: dict[str, set[str]] = {}
    dependencies: dict[str, set[str]] = {}
    for index, task in enumerate(tasks):
        task_id = str(task.get("id") or f"TASK-{index + 1}").strip()
        raw_deps = task.get("depends_on", task.get("dependencies", []))
        dependencies[task_id] = (
            {item for item in raw_deps if isinstance(item, str)} if isinstance(raw_deps, list) else set()
        )
        targets = task.get("target_files")
        paths = list(targets) if isinstance(targets, list) else []
        outputs = task.get("generated_outputs")
        if isinstance(outputs, list):
            paths.extend(row.get("path") for row in outputs if isinstance(row, dict))
        for value in paths:
            path = _pm_artifact_relative_path(value)
            if path:
                producers.setdefault(path, set()).add(task_id)

    def dependency_closure(task_id: str) -> set[str]:
        visited: set[str] = set()
        pending = list(dependencies.get(task_id, set()))
        while pending:
            dependency = pending.pop()
            if dependency in visited:
                continue
            visited.add(dependency)
            pending.extend(dependencies.get(dependency, set()) - visited)
        return visited

    diagnostics: list[dict[str, Any]] = []
    for index, task in enumerate(tasks):
        task_id = str(task.get("id") or f"TASK-{index + 1}").strip()
        available_owners = dependency_closure(task_id) | {task_id}
        metadata = task.get("metadata")
        source = metadata.get("pm_artifact_source_path") if isinstance(metadata, dict) else None
        source_path = source if isinstance(source, str) and source.startswith("$") else f"$.tasks[{index}]"
        commands = task.get("verification_commands")
        if not isinstance(commands, list):
            continue
        for command_index, command in enumerate(commands):
            command_path = f"{source_path}.verification_commands[{command_index}]"
            if isinstance(command, dict):
                try:
                    normalized_argv = normalize_verifier_command_argv(command.get("argv"))
                except (TypeError, ValueError) as exc:
                    diagnostics.append(
                        {
                            "code": "verification_command_argv_invalid",
                            "severity": "error",
                            "path": f"{command_path}.argv",
                            "task_id": task_id,
                            "repair_fields": ["verification_commands"],
                            "message": str(exc),
                        }
                    )
                    continue
                try:
                    profile_feedback = get_verifier_command_profile_feedback(
                        workspace=workspace,
                        modality=command.get("modality", ""),
                        argv=normalized_argv,
                        cwd=command.get("cwd", "."),
                    )
                    profile_id = str(profile_feedback["profile_id"])
                    error_code = str(profile_feedback["error_code"])
                except (TypeError, ValueError) as exc:
                    diagnostics.append(
                        {
                            "code": "verification_command_profile_invalid",
                            "severity": "error",
                            "path": command_path,
                            "task_id": task_id,
                            "repair_fields": ["verification_commands"],
                            "message": str(exc),
                        }
                    )
                    continue
                if error_code:
                    uncertain = error_code == "verifier_workspace_binding_missing"
                    diagnostics.append(
                        {
                            "code": "verification_command_profile_uncertain"
                            if uncertain
                            else "verification_command_profile_unsupported",
                            "severity": "warning" if uncertain else "error",
                            "path": command_path,
                            "task_id": task_id,
                            "profile_id": profile_id,
                            "policy_error_code": error_code,
                            "repair_fields": ["verification_commands"],
                            "message": f"Verifier command planning classification: {error_code}; "
                            + (f"{profile_feedback['reason']} " if profile_feedback.get("reason") else "")
                            + "execution still requires current policy and actual proof.",
                            "evidence": {"command_profile_feedback": profile_feedback},
                        }
                    )
            parsed = _pm_typed_verifiers({"verification_commands": [command]})
            if not parsed:
                continue
            _, argv, cwd = parsed[0]
            operand = _literal_verifier_input(argv, cwd)
            owners = producers.get(operand, set())
            if not owners:
                continue
            if len(owners) == 1 and owners <= available_owners:
                continue
            ambiguous = len(owners) > 1
            diagnostics.append(
                {
                    "code": "verification_operand_producer_ambiguous"
                    if ambiguous
                    else "verification_operand_not_ready",
                    "severity": "warning" if ambiguous else "error",
                    "path": command_path,
                    "task_id": task_id,
                    "operand_path": operand,
                    "producer_task_ids": sorted(owners),
                    "available_task_ids": sorted(available_owners),
                    "argv": list(argv),
                    "cwd": cwd,
                    "repair_fields": ["verification_commands", "depends_on"],
                    "message": (
                        f"Verifier input {operand!r} has multiple declared producers; readiness is unknown."
                        if ambiguous
                        else f"Verifier input {operand!r} is produced by {next(iter(owners))!r}, which is not owned by "
                        f"or a dependency of {task_id!r}; correct the planning command or dependency before acceptance."
                    ),
                }
            )
    return diagnostics
