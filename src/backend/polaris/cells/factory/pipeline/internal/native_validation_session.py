"""Factory-owned source-bound verification group orchestration."""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .factory_workspace_quality import WorkspaceQualityRunner
from .native_validation_execution import await_verification_worker
from .native_validation_group import VerificationGroup
from .native_validation_inputs import (
    NativeValidationSourceBaseline,
    freeze_native_validation_baseline,
    overlay_native_validation_candidate,
)


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
        workspace = Path(executor.workspace)
        tools: list[Path] = []
        tool_files: list[Path] = []
        names = [str(command[0]) for command in commands if command]
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
        return cls(
            workspace=workspace,
            project_id=project_id,
            run_id=run.id,
            completion_contract_hash=str(contract.get("contract_hash") or ""),
            toolchain_roots=tools,
            rustup_home=rustup_home,
            cargo_home=cargo_home,
            toolchain_files=tuple(dict.fromkeys(tool_files)),
        )

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
        group = self.group
        return await await_verification_worker(
            lambda control: self.runner.run_isolated_command(command, timeout_seconds, group, control)
        )

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
