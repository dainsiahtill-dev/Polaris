"""Factory-owned source-bound verification group orchestration."""

from __future__ import annotations

import os
import re
import shlex
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from polaris.cells.runtime.execution_broker.public import project_verification as broker

from .factory_workspace_quality import WorkspaceQualityRunner
from .native_validation_execution import await_verification_worker
from .native_validation_group import VerificationGroup
from .native_validation_inputs import (
    NativeValidationSourceBaseline,
    freeze_native_validation_baseline,
    overlay_native_validation_candidate,
)


class _CeVerifierArgv(list[str]):
    """Short-lived transport of an existing owner-authorized verifier recipe."""

    def __init__(self, authority: broker.RunProjectVerificationCommandV1) -> None:
        super().__init__(authority.argv)
        self.authority = authority


def required_clean_verifier_commands(executor: Any, run: Any, *, preparation: bool = False) -> list[list[str]]:
    from .factory_node_dependencies import authenticated_preparation_contract

    contract = authenticated_preparation_contract(executor, run)
    phase = {"build": 0, "lint": 1, "test": 2, "entrypoint": 3}
    commands: list[list[str]] = []
    for obligation in sorted(
        contract.obligations.verification, key=lambda row: (phase.get(row.modality, -1), row.obligation_id)
    ):
        if obligation.applicability != "required" or (obligation.modality == "environment_prep") is not preparation:
            continue
        authority = broker.authorize_project_verification_command(
            broker.ResolveProjectVerificationAuthorityQueryV1(
                workspace=str(executor.workspace),
                project_id=contract.project_id,
                run_id=run.id,
                completion_contract_hash=contract.contract_hash,
                obligation_id=obligation.obligation_id,
            )
        )
        if (
            type(authority) is not broker.RunProjectVerificationCommandV1
            or authority.command_authority_hash != obligation.command_authority_hash
            or authority.owner_task_id != obligation.owner_task_id
            or authority.modality != obligation.modality
        ):
            raise ValueError("clean_verifier_authority_binding_mismatch")
        commands.append(_CeVerifierArgv(authority))
    if not commands and not preparation:
        raise ValueError("clean_verifier_required_commands_missing")
    return commands


class NativeValidationSession:
    """Share disposable outputs within a candidate, never across candidates.

    Command results remain observations, not project/verifier receipt seals.
    Only the physical execution owner can release an issued group's staging.
    """

    @classmethod
    def from_factory(
        cls,
        executor: Any,
        run: Any,
        context: Mapping[str, Any],
        *,
        commands: Sequence[Sequence[str]] = (),
    ) -> NativeValidationSession:
        stages = run.metadata.get("stage_results")
        stage = stages.get("chief_engineer_review") if isinstance(stages, Mapping) else None
        if not isinstance(stage, Mapping) or stage.get("status") != "success":
            raise ValueError("verification session requires a committed Chief Engineer stage")
        artifacts = stage.get("artifacts")
        references = [str(item) for item in artifacts or () if str(item).startswith("runtime/blueprints/ce_portfolio_")]
        if len(references) != 1:
            raise ValueError("verification portfolio identity is missing or ambiguous")
        payload = executor._read_json_artifact_payload(references[0])
        contract = payload.get("project_completion_contract")
        if not isinstance(contract, Mapping) or contract.get("run_id") != run.id:
            raise ValueError("verification portfolio belongs to another Factory run")
        project_id = str(contract.get("project_id") or "")
        requested_id = executor._canonical_project_id(dict(context))
        if requested_id and project_id != requested_id:
            raise ValueError("verification portfolio belongs to another project")
        required = required_clean_verifier_commands(executor, run)
        workspace = Path(executor.workspace)
        tools: list[Path] = []
        tool_files: list[Path] = []
        names = [str(command[0]) for command in required if command]
        uses_rust = any(Path(name).name in {"cargo", "rustc", "rustdoc", "rustup"} for name in names)
        rustup_home = Path(os.environ.get("RUSTUP_HOME") or (Path.home() / ".rustup")) if uses_rust else None
        cargo_home = Path(os.environ.get("CARGO_HOME") or (Path.home() / ".cargo")) if uses_rust else None
        if rustup_home is not None and not rustup_home.is_dir():
            if any(
                (selected := shutil.which(name)) and Path(selected).resolve(strict=True).name == "rustup"
                for name in names
            ):
                raise ValueError("verification_rustup_home_unavailable")
            # Only an actual system Cargo can omit Rustup; never a Rustup proxy.
            rustup_home = cargo_home = None
        if any(Path(name).name in {"npm", "npx"} for name in names):
            names.append("node")
        for name in names:
            selected = shutil.which(name)
            if not selected:
                continue
            executable = Path(selected).resolve(strict=True)
            if any(executable.is_relative_to(root) for root in (Path("/usr"), Path("/bin"), Path("/sbin"))):
                continue
            python_version = re.fullmatch(r"python([0-9]+\.[0-9]+)", executable.name)
            if python_version is not None and executable.parent.name == "bin":
                prefix = executable.parent.parent
                stdlib = prefix / "lib" / ("python" + python_version[1])
                if not stdlib.is_dir():
                    raise ValueError("verification_python_stdlib_unavailable")
                tools.append(executable.parent)
                for child in stdlib.iterdir():
                    if child.name in {"site-packages", "__pycache__"}:
                        continue
                    if child.is_dir():
                        tools.append(child)
                    elif child.suffix == ".py":
                        tool_files.append(child)
                tool_files.extend(
                    path.resolve(strict=True)
                    for path in (prefix / "lib").glob("libpython" + python_version[1] + ".so*")
                )
                continue
            root = (
                executable.parent
                if uses_rust and executable.name == "rustup"
                else executable.parent.parent
                if executable.parent.name == "bin"
                else executable.parent
            )
            if root not in tools:
                tools.append(root)
        tools = [root for root in tools if not any(root != parent and root.is_relative_to(parent) for parent in tools)]
        session = cls(
            workspace=workspace,
            project_id=project_id,
            run_id=run.id,
            completion_contract_hash=str(contract.get("contract_hash") or ""),
            toolchain_roots=tools,
            rustup_home=rustup_home,
            cargo_home=cargo_home,
            toolchain_files=tuple(dict.fromkeys(tool_files)),
        )
        session._factory_executor = executor
        session._factory_run = run
        return session

    def __init__(
        self,
        *,
        workspace: Path,
        project_id: str,
        run_id: str,
        completion_contract_hash: str,
        dependency_roots: Mapping[str, Path] | None = None,
        toolchain_roots: Sequence[Path] = (),
        rustup_home: Path | None = None,
        cargo_home: Path | None = None,
        toolchain_files: Sequence[Path] = (),
    ) -> None:
        self.workspace = Path(workspace)
        self.project_id = project_id
        self.run_id = run_id
        self.completion_contract_hash = completion_contract_hash
        # None selects platform discovery; an explicit map, including an empty
        # one, is an admission decision and must never be broadened.
        self._discover_dependencies = dependency_roots is None
        self._factory_executor: Any = None
        self._factory_run: Any = None
        self.dependencies = dict(dependency_roots or {})
        self.toolchains = tuple(toolchain_roots)
        self.rustup_home = rustup_home
        self.cargo_home = cargo_home
        self.toolchain_files = tuple(toolchain_files)
        self.runner = WorkspaceQualityRunner(self.workspace)
        self.baseline = self._freeze()
        self._group: VerificationGroup | None = self._open_group(self.baseline.input_hashes, "baseline")

    @property
    def group(self) -> VerificationGroup:
        if self._group is None:
            self._group = self._open_group(self.baseline.input_hashes, "baseline")
        return self._group

    def _freeze(self) -> NativeValidationSourceBaseline:
        return freeze_native_validation_baseline(
            workspace=str(self.workspace),
            project_id=self.project_id,
            run_id=self.run_id,
            completion_contract_hash=self.completion_contract_hash,
        )

    def _open_group(self, inputs: Mapping[str, str], phase: str) -> VerificationGroup:
        if self._discover_dependencies:
            root = self.workspace / "node_modules"
            self.dependencies = {"node_modules": root} if root.is_dir() else {}
        return VerificationGroup(
            workspace=self.workspace,
            candidate_id=f"{self.run_id}:{phase}:{self.baseline.baseline_hash}",
            input_hashes=inputs,
            dependency_roots=self.dependencies,
            toolchain_roots=self.toolchains,
            staging_parent=None,
            rustup_home=self.rustup_home,
            cargo_home=self.cargo_home,
            toolchain_files=self.toolchain_files,
        )

    async def run_command(self, command: list[str], timeout_seconds: float) -> dict[str, Any]:
        authority = None
        cwd = "."
        if self._factory_executor is not None:
            # Restart/JSON/list copying never turns a carrier into authority.
            # Always re-resolve the current committed CE owner before execution.
            recipes = required_clean_verifier_commands(self._factory_executor, self._factory_run)
            candidate = command.authority if isinstance(command, _CeVerifierArgv) else None
            matched = [
                recipe.authority
                for recipe in recipes
                if isinstance(recipe, _CeVerifierArgv)
                and list(recipe) == list(command)
                and (candidate is None or recipe.authority == candidate)
            ]
            if len(matched) != 1:
                raise ValueError("clean_verifier_command_missing_or_ambiguous")
            authority = matched[0]
            cwd = authority.cwd
        group = self.group
        result = await await_verification_worker(
            lambda control: self.runner.run_isolated_command(command, timeout_seconds, group, control, cwd=cwd)
        )
        if authority is not None:
            fresh = broker.authorize_project_verification_command(
                broker.ResolveProjectVerificationAuthorityQueryV1(
                    workspace=authority.workspace,
                    project_id=authority.project_id,
                    run_id=authority.run_id,
                    completion_contract_hash=authority.completion_contract_hash,
                    obligation_id=authority.obligation_id,
                )
            )
            if fresh != authority:
                raise ValueError("clean_verifier_authority_changed_during_execution")
            result["ce_verification_binding"] = {
                "obligation_id": authority.obligation_id,
                "owner_task_id": authority.owner_task_id,
                "command_authority_hash": authority.command_authority_hash,
                "completion_contract_hash": authority.completion_contract_hash,
                "argv": list(authority.argv),
                "cwd": authority.cwd,
                "project_id": authority.project_id,
                "run_id": authority.run_id,
                "job_token_id": authority.job_token_id,
                "job_token_set_hash": authority.job_token_set_hash,
                "execution_policy_hash": authority.execution_policy_hash,
                "authority_revision": authority.authority_revision,
                "authoritative_receipt": False,
            }
            if authority.modality == "entrypoint" and result.get("passed") is not True:
                from polaris.cells.director.runtime.public import RepairDiagnosticV1

                from .factory_node_dependencies import authenticated_preparation_contract

                contract = authenticated_preparation_contract(self._factory_executor, self._factory_run)
                observation = result.get("verification_group", {})
                outputs = observation.get("output_hashes", {})
                records = observation.get("commands", ())
                # F16 projects only the current command, never prior build
                # history. Admission failures have no terminal process record.
                record = records[0] if len(records) == 1 and isinstance(records[0], Mapping) else {}
                observed_argv = record.get("argv", ())
                wrapped_argv = record.get("wrapped_argv", ())
                observed_cwd = (
                    wrapped_argv[wrapped_argv.index("--chdir") + 1]
                    if "--chdir" in wrapped_argv and wrapped_argv.index("--chdir") + 1 < len(wrapped_argv)
                    else None
                )
                execution_started = bool(
                    observed_argv
                    and tuple(observed_argv[1:]) == authority.argv[1:]
                    and Path(str(observed_argv[0])).resolve() == Path(authority.executable_path).resolve()
                    and observed_cwd == ("/workspace" if authority.cwd == "." else "/workspace/" + authority.cwd)
                    and record.get("process_owner_terminal") is True
                    and record.get("exit_code") == result.get("exit_code")
                )
                for entry in contract.obligations.entrypoints:
                    if (
                        entry.applicability == "required"
                        and entry.owner_task_id == authority.owner_task_id
                        and tuple(shlex.split(entry.command)) == authority.argv
                        and entry.runtime_path
                        and entry.runtime_path not in outputs
                    ):
                        scope = [
                            artifact.path
                            for artifact in contract.obligations.artifacts
                            if artifact.owner_task_id == authority.owner_task_id
                            and artifact.applicability == "required"
                            and (artifact.path == entry.source_path or artifact.semantic_role in {"config", "manifest"})
                        ]
                        diagnostic = RepairDiagnosticV1(
                            source="controlled_verifier",
                            code="failed_required_entrypoint",
                            path=entry.source_path,
                            message=f"artifact_quality_error: required entrypoint artifact missing: {entry.runtime_path}; expected command: {' '.join(authority.argv)}; "
                            + "; ".join(f"repair source ({path})" for path in scope),
                            metadata={
                                "runtime_path": entry.runtime_path,
                                "source_path": entry.source_path,
                                "expected_command": list(authority.argv),
                                "repair_possible_scope": scope,
                                "scope_is_authority": False,
                                "obligation_id": authority.obligation_id,
                                "execution_started": execution_started,
                                "command_receipt_missing": not execution_started,
                            },
                        )
                        result["repair_diagnostic"] = asdict(diagnostic)
                        result["stderr_tail"] = str(result.get("stderr_tail") or "") + "\n" + diagnostic.message
                        break
        return result

    def before_repair(self) -> None:
        """Freeze before claim, then release only the prior terminal group."""
        if self._group is not None:
            self._group.assert_inputs_current()
            self._group.close()
            self._group = None
        self.baseline = self._freeze()

    def candidate(
        self,
        *,
        pending: Mapping[str, Any],
        results: Sequence[Mapping[str, Any]],
    ) -> None:
        projection = pending.get("task_completion_projection")
        if not isinstance(projection, Mapping):
            raise ValueError("verification candidate lacks its CE completion projection")
        inputs = overlay_native_validation_candidate(
            self.baseline,
            pending=pending,
            task_completion_projection=projection,
            round_repair_results=results,
        )
        self._group = self._open_group(inputs.input_hashes, "candidate")

    def restored(self) -> None:
        """Revalidate restored bytes in a fresh group, not the rejected copy."""
        if self._group is not None:
            self._group.close()
        self._group = self._open_group(self.baseline.input_hashes, "restored")

    def close(self) -> None:
        if self._group is not None:
            self._group.close()
