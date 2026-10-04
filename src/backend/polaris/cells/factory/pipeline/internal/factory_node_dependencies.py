"""Read-only Node dependency readiness; observations never grant installation."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from polaris.cells.chief_engineer.blueprint.public import (
    ProjectCompletionContractV1,
    QueryProjectCompletionContractV1,
    query_project_completion_contract,
)
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter

from .factory_run_models import FactoryRun

_SECTIONS = ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies")
_PACKAGE_NAME = re.compile(r"(?:@[a-zA-Z0-9_.-]+/)?[a-zA-Z0-9_.-]+\Z")
NODE_PREPARATION_COMMAND = ("npm", "install", "--ignore-scripts", "--no-audit", "--no-fund")


@dataclass(frozen=True)
class NodeDependencyReadiness:
    ready: bool
    reason: str


def authenticated_preparation_contract(executor: Any, run: FactoryRun) -> ProjectCompletionContractV1:
    """Resolve the same exact persisted CE identity used by native validation."""
    stages = run.metadata.get("stage_results")
    stage = stages.get("chief_engineer_review") if isinstance(stages, Mapping) else None
    if not isinstance(stage, Mapping) or stage.get("status") != "success":
        raise ValueError("dependency_preparation_requires_committed_ce_stage")
    refs = [
        str(value) for value in stage.get("artifacts", ()) if str(value).startswith("runtime/blueprints/ce_portfolio_")
    ]
    if len(refs) != 1:
        raise ValueError("dependency_preparation_ce_identity_ambiguous")
    payload = executor._read_json_artifact_payload(refs[0])
    contract = payload.get("project_completion_contract")
    if not isinstance(contract, Mapping) or contract.get("run_id") != run.id:
        raise ValueError("dependency_preparation_ce_run_mismatch")
    return query_project_completion_contract(
        QueryProjectCompletionContractV1(
            workspace=str(executor.workspace),
            project_id=str(contract.get("project_id") or ""),
            run_id=run.id,
            contract_hash=str(contract.get("contract_hash") or ""),
        )
    )


def preparation_protects_lock(workspace: Path, contract: ProjectCompletionContractV1 | None) -> bool:
    """Unknown ownership protects the lock; only an exact CE owner query can exclude it."""
    if contract is None:
        return True
    if type(contract) is not ProjectCompletionContractV1:
        raise ValueError("dependency_preparation_contract_invalid")
    current = query_project_completion_contract(
        QueryProjectCompletionContractV1(
            workspace=str(workspace),
            project_id=contract.project_id,
            run_id=contract.run_id,
            contract_hash=contract.contract_hash,
        )
    )
    return any(
        item.path == "package-lock.json" and item.applicability != "not_applicable"
        for item in current.obligations.artifacts
    )


def preparation_source_hashes(workspace: Path, *, protect_lock: bool) -> dict[str, str]:
    """Observe authored files around existing preparation; never restore unowned bytes."""
    fs = KernelFileSystem(str(workspace), get_default_adapter())
    dependencies = workspace / "node_modules"
    if dependencies.is_symlink():
        raise ValueError("dependency_preparation_node_modules_symlink")
    # Inspect the whole installed tree, including packages absent from the old
    # lock. A stale lock may return before readiness examines those packages.
    for root, directories, files in os.walk(dependencies, followlinks=False):
        for name in [*directories, *files]:
            path = Path(root) / name
            if path.is_symlink() and not path.resolve(strict=True).is_relative_to(dependencies.resolve()):
                raise ValueError("dependency_preparation_dependency_symlink_escape")
    hashes: dict[str, str] = {}
    for root, directories, files in os.walk(workspace, followlinks=False):
        directories[:] = [name for name in directories if name not in {".git", ".polaris", "node_modules"}]
        for name in [*directories, *files]:
            path = Path(root) / name
            if path.is_symlink():
                raise ValueError(f"dependency_preparation_source_symlink:{path.relative_to(workspace)}")
        for name in files:
            relative = (Path(root) / name).relative_to(workspace).as_posix()
            if relative == "package-lock.json" and not protect_lock:
                continue
            hashes[relative] = hashlib.sha256(fs.workspace_read_bytes(relative)).hexdigest()
    return hashes


def _json_object(fs: KernelFileSystem, path: str) -> dict[str, Any]:
    # Reject lexical links before KFS performs its normal workspace boundary check.
    candidate = Path(fs.workspace) / path
    root = Path(fs.workspace)
    for part in (candidate, *candidate.parents):
        if part == root:
            break
        if part.is_symlink():
            raise ValueError(f"node_dependency_symlink:{path}")
    value = json.loads(fs.workspace_read_text(path, encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"node_dependency_metadata_not_object:{path}")
    return value


def node_dependency_readiness(workspace: Path) -> NodeDependencyReadiness:
    """Compare current declarations with lock and installed metadata, without I/O effects.

    Optional/peer packages are not promoted into required dependencies. Unknown
    lock formats and missing metadata require preparation, never inferred readiness.
    """
    fs = KernelFileSystem(str(workspace), get_default_adapter())
    try:
        manifest = _json_object(fs, "package.json")
        declared: dict[str, dict[str, str]] = {}
        for section in _SECTIONS:
            raw = manifest.get(section, {})
            if not isinstance(raw, dict) or any(
                not isinstance(name, str)
                or not _PACKAGE_NAME.fullmatch(name)
                or any(part in {".", ".."} for part in name.split("/"))
                or not isinstance(spec, str)
                or not spec.strip()
                for name, spec in raw.items()
            ):
                return NodeDependencyReadiness(False, f"invalid_dependency_declarations:{section}")
            declared[section] = raw
        required = {**declared["dependencies"], **declared["devDependencies"]}
        required = {name: spec for name, spec in required.items() if name not in declared["optionalDependencies"]}
        if not any(declared.values()):
            return NodeDependencyReadiness(True, "no_dependencies")
        lock = _json_object(fs, "package-lock.json")
        packages = lock.get("packages")
        if lock.get("lockfileVersion") not in (2, 3) or not isinstance(packages, dict):
            return NodeDependencyReadiness(False, "lock_metadata_unproved")
        root = packages.get("")
        if not isinstance(root, dict) or any(root.get(section, {}) != declared[section] for section in _SECTIONS):
            return NodeDependencyReadiness(False, "manifest_lock_drift")
        for name in required:
            if f"node_modules/{name}" not in packages:
                return NodeDependencyReadiness(False, f"required_package_unlocked:{name}")
        for path, metadata in packages.items():
            if path == "":
                continue
            parts = PurePosixPath(path).parts if isinstance(path, str) else ()
            if not parts or parts[0] != "node_modules" or any(part in {".", ".."} for part in parts):
                return NodeDependencyReadiness(False, "lock_package_path_invalid")
            if not isinstance(metadata, dict) or metadata.get("link"):
                return NodeDependencyReadiness(False, "lock_package_metadata_unproved")
            if metadata.get("optional") is True and not (workspace / path).exists():
                continue
            installed = _json_object(fs, path + "/package.json")
            version = metadata.get("version")
            if not isinstance(version, str) or installed.get("version") != version:
                return NodeDependencyReadiness(False, f"installed_package_version_drift:{path}")
        return NodeDependencyReadiness(True, "manifest_lock_installed_match")
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        return NodeDependencyReadiness(False, f"dependency_metadata_unproved:{type(exc).__name__}:{exc}")
