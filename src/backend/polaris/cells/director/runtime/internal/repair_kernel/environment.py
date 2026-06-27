"""Runtime-owned environment preparation contracts for repair revalidation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .contracts import RepairReceipt, sha256_text

ENVIRONMENT_REFRESH_REQUIREMENT_SCHEMA = "director.environment_refresh_requirement.v1"
ENVIRONMENT_PREP_PLAN_SCHEMA = "director.environment_prep_plan.v1"
ENVIRONMENT_PREP_RECEIPT_SCHEMA = "director.environment_prep_receipt.v1"
ENVIRONMENT_PREP_CATALOG_SCHEMA = "director.environment_prep_catalog.v1"
ENVIRONMENT_PREP_COMMAND_TEMPLATE_VERSION = "director.environment_prep.command_templates.v1"


@dataclass(frozen=True)
class EnvironmentPrepCatalogEntry:
    """One deterministic dependency-preparation command template."""

    ecosystem: str
    package_manager: str
    manifests: tuple[str, ...]
    lockfiles: tuple[str, ...]
    command: tuple[str, ...]
    reason: str
    network_allowed: bool = True
    scripts_allowed: bool = False
    workspace_writes_allowed: bool = True
    global_writes_allowed: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "ecosystem", _non_empty(self.ecosystem, "ecosystem"))
        object.__setattr__(self, "package_manager", _non_empty(self.package_manager, "package_manager"))
        object.__setattr__(self, "manifests", _tuple_paths(self.manifests))
        object.__setattr__(self, "lockfiles", _tuple_paths(self.lockfiles))
        object.__setattr__(self, "command", _tuple_str(self.command))
        object.__setattr__(self, "reason", _non_empty(self.reason, "reason"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "director.environment_prep_catalog_entry.v1",
            "ecosystem": self.ecosystem,
            "package_manager": self.package_manager,
            "manifests": list(self.manifests),
            "lockfiles": list(self.lockfiles),
            "command": list(self.command),
            "reason": self.reason,
            "network_allowed": self.network_allowed,
            "scripts_allowed": self.scripts_allowed,
            "workspace_writes_allowed": self.workspace_writes_allowed,
            "global_writes_allowed": self.global_writes_allowed,
            "authoritative_repair": False,
        }


@dataclass(frozen=True)
class EnvironmentRefreshRequirement:
    """A manifest-derived requirement to refresh dependencies before verify."""

    ecosystem: str
    package_manager: str
    manifest: str
    command: tuple[str, ...]
    reason: str
    receipt_id: str = ""
    lockfile: str = ""
    manifest_after_hash: str = ""
    lockfile_after_hash: str = ""
    freshness_key: str = ""
    schema_version: str = ENVIRONMENT_REFRESH_REQUIREMENT_SCHEMA

    def __post_init__(self) -> None:
        manifest = _normalize_path(self.manifest)
        lockfile = _normalize_path(self.lockfile)
        command = _tuple_str(self.command)
        freshness_key = str(self.freshness_key or "").strip() or environment_freshness_key(
            ecosystem=self.ecosystem,
            package_manager=self.package_manager,
            manifest_path=manifest,
            manifest_hash=self.manifest_after_hash,
            lockfile_path=lockfile,
            lockfile_hash=self.lockfile_after_hash,
            command=command,
        )
        object.__setattr__(self, "schema_version", _non_empty(self.schema_version, "schema_version"))
        object.__setattr__(self, "ecosystem", _non_empty(self.ecosystem, "ecosystem"))
        object.__setattr__(self, "package_manager", _non_empty(self.package_manager, "package_manager"))
        object.__setattr__(self, "manifest", manifest)
        object.__setattr__(self, "lockfile", lockfile)
        object.__setattr__(self, "command", command)
        object.__setattr__(self, "reason", _non_empty(self.reason, "reason"))
        object.__setattr__(self, "receipt_id", str(self.receipt_id or "").strip())
        object.__setattr__(self, "manifest_after_hash", str(self.manifest_after_hash or "").strip())
        object.__setattr__(self, "lockfile_after_hash", str(self.lockfile_after_hash or "").strip())
        object.__setattr__(self, "freshness_key", freshness_key)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "ecosystem": self.ecosystem,
            "package_manager": self.package_manager,
            "manifest": self.manifest,
            "lockfile": self.lockfile,
            "command": list(self.command),
            "reason": self.reason,
            "receipt_id": self.receipt_id,
            "manifest_after_hash": self.manifest_after_hash,
            "lockfile_after_hash": self.lockfile_after_hash,
            "freshness_key": self.freshness_key,
            "writes_allowed": False,
            "authoritative_repair": False,
        }


@dataclass(frozen=True)
class EnvironmentPrepPlan:
    """A runtime-owned command plan that an adapter may execute."""

    requirement: EnvironmentRefreshRequirement
    command: tuple[str, ...]
    plan_id: str = ""
    cwd: str = "."
    timeout_seconds: int = 120
    policy: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = ENVIRONMENT_PREP_PLAN_SCHEMA

    def __post_init__(self) -> None:
        command = _tuple_str(self.command)
        plan_id = str(self.plan_id or "").strip() or stable_environment_prep_id(
            "environment_prep_plan",
            self.requirement.freshness_key,
            command,
        )
        object.__setattr__(self, "schema_version", _non_empty(self.schema_version, "schema_version"))
        object.__setattr__(self, "plan_id", plan_id)
        object.__setattr__(self, "command", command)
        object.__setattr__(self, "cwd", _normalize_path(self.cwd) or ".")
        object.__setattr__(self, "timeout_seconds", max(1, int(self.timeout_seconds)))
        object.__setattr__(self, "policy", dict(self.policy or {}))
        object.__setattr__(self, "metadata", dict(self.metadata or {}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_id": self.plan_id,
            "ecosystem": self.requirement.ecosystem,
            "package_manager": self.requirement.package_manager,
            "manifest": self.requirement.manifest,
            "lockfile": self.requirement.lockfile,
            "command": list(self.command),
            "cwd": self.cwd,
            "timeout_seconds": self.timeout_seconds,
            "freshness_key": self.requirement.freshness_key,
            "source_receipt_id": self.requirement.receipt_id,
            "policy": dict(self.policy),
            "metadata": dict(self.metadata),
            "requirement": self.requirement.to_dict(),
        }


@dataclass(frozen=True)
class EnvironmentPrepReceipt:
    """Evidence returned by an adapter after executing an environment prep plan."""

    plan_id: str
    ecosystem: str
    package_manager: str
    command: tuple[str, ...]
    exit_code: int | None
    status: str
    duration_ms: int | None = None
    manifest: str = ""
    lockfile: str = ""
    manifest_hash_before: str = ""
    manifest_hash_after: str = ""
    lockfile_hash_before: str = ""
    lockfile_hash_after: str = ""
    stdout_ref: str = ""
    stderr_ref: str = ""
    freshness_key: str = ""
    skipped_reason: str = ""
    error_code: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = ENVIRONMENT_PREP_RECEIPT_SCHEMA

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _non_empty(self.schema_version, "schema_version"))
        object.__setattr__(self, "plan_id", _non_empty(self.plan_id, "plan_id"))
        object.__setattr__(self, "ecosystem", _non_empty(self.ecosystem, "ecosystem"))
        object.__setattr__(self, "package_manager", _non_empty(self.package_manager, "package_manager"))
        object.__setattr__(self, "command", _tuple_str(self.command))
        object.__setattr__(self, "status", _non_empty(self.status, "status"))
        object.__setattr__(self, "duration_ms", None if self.duration_ms is None else max(0, int(self.duration_ms)))
        object.__setattr__(self, "manifest", _normalize_path(self.manifest))
        object.__setattr__(self, "lockfile", _normalize_path(self.lockfile))
        object.__setattr__(self, "metadata", dict(self.metadata or {}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_id": self.plan_id,
            "ecosystem": self.ecosystem,
            "package_manager": self.package_manager,
            "command": list(self.command),
            "exit_code": self.exit_code,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "manifest": self.manifest,
            "lockfile": self.lockfile,
            "manifest_hash_before": self.manifest_hash_before,
            "manifest_hash_after": self.manifest_hash_after,
            "lockfile_hash_before": self.lockfile_hash_before,
            "lockfile_hash_after": self.lockfile_hash_after,
            "stdout_ref": self.stdout_ref,
            "stderr_ref": self.stderr_ref,
            "freshness_key": self.freshness_key,
            "skipped_reason": self.skipped_reason,
            "error_code": self.error_code,
            "authoritative_repair": False,
            "metadata": dict(self.metadata),
        }


def environment_prep_catalog() -> tuple[EnvironmentPrepCatalogEntry, ...]:
    """Return deterministic package-manager command templates."""

    return (
        EnvironmentPrepCatalogEntry(
            ecosystem="node",
            package_manager="npm",
            manifests=("package.json",),
            lockfiles=("package-lock.json", "npm-shrinkwrap.json"),
            command=("npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"),
            reason="node_lockfile_changed_before_revalidation",
        ),
        EnvironmentPrepCatalogEntry(
            ecosystem="node",
            package_manager="npm",
            manifests=("package.json",),
            lockfiles=(),
            command=("npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"),
            reason="node_manifest_changed_before_revalidation",
        ),
        EnvironmentPrepCatalogEntry(
            ecosystem="node",
            package_manager="pnpm",
            manifests=("package.json",),
            lockfiles=("pnpm-lock.yaml",),
            command=("pnpm", "install", "--frozen-lockfile", "--ignore-scripts"),
            reason="pnpm_lockfile_changed_before_revalidation",
        ),
        EnvironmentPrepCatalogEntry(
            ecosystem="node",
            package_manager="yarn",
            manifests=("package.json",),
            lockfiles=("yarn.lock",),
            command=("yarn", "install", "--frozen-lockfile", "--ignore-scripts"),
            reason="yarn_lockfile_changed_before_revalidation",
        ),
        EnvironmentPrepCatalogEntry(
            ecosystem="python",
            package_manager="pip",
            manifests=("requirements.txt",),
            lockfiles=(),
            command=("python", "-m", "pip", "install", "-r", "requirements.txt"),
            reason="python_requirements_changed_before_revalidation",
        ),
        EnvironmentPrepCatalogEntry(
            ecosystem="python",
            package_manager="uv",
            manifests=("pyproject.toml",),
            lockfiles=("uv.lock",),
            command=("uv", "sync", "--frozen"),
            reason="python_uv_lockfile_changed_before_revalidation",
        ),
        EnvironmentPrepCatalogEntry(
            ecosystem="python",
            package_manager="poetry",
            manifests=("pyproject.toml",),
            lockfiles=("poetry.lock",),
            command=("poetry", "install", "--no-root"),
            reason="python_poetry_lockfile_changed_before_revalidation",
        ),
        EnvironmentPrepCatalogEntry(
            ecosystem="rust",
            package_manager="cargo",
            manifests=("Cargo.toml",),
            lockfiles=("Cargo.lock",),
            command=("cargo", "fetch"),
            reason="rust_manifest_changed_before_revalidation",
        ),
        EnvironmentPrepCatalogEntry(
            ecosystem="go",
            package_manager="go",
            manifests=("go.mod",),
            lockfiles=("go.sum",),
            command=("go", "mod", "download"),
            reason="go_manifest_changed_before_revalidation",
        ),
        EnvironmentPrepCatalogEntry(
            ecosystem="java",
            package_manager="maven",
            manifests=("pom.xml",),
            lockfiles=(),
            command=("mvn", "dependency:go-offline"),
            reason="maven_manifest_changed_before_revalidation",
        ),
        EnvironmentPrepCatalogEntry(
            ecosystem="java",
            package_manager="gradle",
            manifests=("build.gradle", "build.gradle.kts"),
            lockfiles=("gradle.lockfile",),
            command=("gradle", "dependencies"),
            reason="gradle_manifest_changed_before_revalidation",
        ),
        EnvironmentPrepCatalogEntry(
            ecosystem="ruby",
            package_manager="bundler",
            manifests=("Gemfile",),
            lockfiles=("Gemfile.lock",),
            command=("bundle", "install"),
            reason="ruby_gemfile_changed_before_revalidation",
        ),
        EnvironmentPrepCatalogEntry(
            ecosystem="php",
            package_manager="composer",
            manifests=("composer.json",),
            lockfiles=("composer.lock",),
            command=("composer", "install", "--no-interaction", "--no-scripts"),
            reason="php_composer_manifest_changed_before_revalidation",
        ),
    )


def environment_refresh_metadata_for_files(
    *,
    files_changed: Sequence[str],
    after_hashes: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return repair receipt metadata for manifest writes."""

    requirements = environment_refresh_requirements_for_changed_files(
        files_changed=files_changed,
        after_hashes=after_hashes or {},
        receipt_id="",
    )
    if not requirements:
        return {}
    return {
        "environment_refresh_required": True,
        "environment_refresh_requirements": [requirement.to_dict() for requirement in requirements],
    }


def environment_refresh_requirements_for_changed_files(
    *,
    files_changed: Sequence[str],
    after_hashes: Mapping[str, str] | None = None,
    receipt_id: str = "",
    workspace: str | Path | None = None,
) -> tuple[EnvironmentRefreshRequirement, ...]:
    """Return refresh requirements for changed manifest or lockfile paths."""

    changed = {_normalize_path(path) for path in files_changed if _normalize_path(path)}
    if not changed:
        return ()
    hash_map = {_normalize_path(key): str(value) for key, value in dict(after_hashes or {}).items()}
    workspace_hashes = _workspace_hashes(workspace)
    requirements: list[EnvironmentRefreshRequirement] = []
    for entry in _select_catalog_entries(changed, workspace):
        manifest = _select_path(entry.manifests, changed, workspace)
        if not manifest:
            continue
        lockfile = _select_path(entry.lockfiles, changed, workspace)
        requirements.append(
            EnvironmentRefreshRequirement(
                ecosystem=entry.ecosystem,
                package_manager=entry.package_manager,
                manifest=manifest,
                lockfile=lockfile,
                command=entry.command,
                reason=entry.reason,
                receipt_id=receipt_id,
                manifest_after_hash=hash_map.get(manifest) or workspace_hashes.get(manifest, ""),
                lockfile_after_hash=hash_map.get(lockfile) or workspace_hashes.get(lockfile, ""),
            )
        )
    return _dedupe_requirements(requirements)


def environment_refresh_requirements_from_receipts(
    receipts: Sequence[RepairReceipt | Mapping[str, Any]],
    *,
    workspace: str | Path | None = None,
) -> tuple[dict[str, Any], ...]:
    """Project unique environment refresh requirements from repair receipts."""

    requirements: list[EnvironmentRefreshRequirement] = []
    for receipt in receipts:
        receipt_id = _receipt_id(receipt)
        explicit = _receipt_metadata(receipt).get("environment_refresh_requirements")
        if isinstance(explicit, Sequence) and not isinstance(explicit, str | bytes):
            requirements.extend(
                requirement
                for item in explicit
                if isinstance(item, Mapping)
                for requirement in [_normalize_requirement(item, receipt_id=receipt_id)]
                if requirement is not None
            )
            continue
        requirements.extend(
            environment_refresh_requirements_for_changed_files(
                files_changed=_receipt_files_changed(receipt),
                after_hashes=_receipt_after_hashes(receipt),
                receipt_id=receipt_id,
                workspace=workspace,
            )
        )
    return tuple(requirement.to_dict() for requirement in _dedupe_requirements(requirements))


def environment_prep_plans_from_requirements(
    requirements: Sequence[EnvironmentRefreshRequirement | Mapping[str, Any]],
    *,
    workspace: str | Path | None = None,
    previous_prep_receipts: Sequence[EnvironmentPrepReceipt | Mapping[str, Any]] = (),
) -> tuple[EnvironmentPrepPlan, ...]:
    """Build prep plans, skipping already fresh successful prep receipts."""

    fresh_keys = {
        str(_mapping_get(receipt, "freshness_key") or "").strip()
        for receipt in previous_prep_receipts
        if str(_mapping_get(receipt, "status") or "") in {"succeeded", "skipped_fresh"}
    }
    plans: list[EnvironmentPrepPlan] = []
    for raw_requirement in requirements:
        requirement = _coerce_requirement(raw_requirement)
        if requirement is None or requirement.freshness_key in fresh_keys:
            continue
        entry = _catalog_entry_for_requirement(requirement)
        if entry is None:
            continue
        plans.append(
            EnvironmentPrepPlan(
                requirement=requirement,
                command=entry.command,
                timeout_seconds=180 if entry.ecosystem in {"node", "java", "ruby", "php"} else 120,
                policy={
                    "command_source": "director.runtime.environment_prep_catalog",
                    "command_template_version": ENVIRONMENT_PREP_COMMAND_TEMPLATE_VERSION,
                    "network_allowed": entry.network_allowed,
                    "scripts_allowed": entry.scripts_allowed,
                    "workspace_writes_allowed": entry.workspace_writes_allowed,
                    "global_writes_allowed": entry.global_writes_allowed,
                    "llm_generated_command_allowed": False,
                    "agi_execution_authority": False,
                    "authoritative_repair": False,
                },
                metadata={
                    "workspace": str(Path(workspace).resolve()) if workspace is not None else "",
                    "source_receipt_id": requirement.receipt_id,
                },
            )
        )
    return tuple(plans)


def environment_prep_catalog_summary() -> dict[str, Any]:
    """Return a public, read-only catalog projection."""

    entries = environment_prep_catalog()
    return {
        "schema_version": ENVIRONMENT_PREP_CATALOG_SCHEMA,
        "owner_cell": "director.runtime",
        "access": "read_only",
        "entry_count": len(entries),
        "ecosystems": sorted({entry.ecosystem for entry in entries}),
        "package_managers": sorted({entry.package_manager for entry in entries}),
        "command_template_version": ENVIRONMENT_PREP_COMMAND_TEMPLATE_VERSION,
        "scripts_allowed_by_default": False,
        "llm_generated_commands_allowed": False,
        "adapter_runner_binding_only": True,
        "authoritative_repair": False,
        "items": [entry.to_dict() for entry in entries],
    }


def environment_freshness_key(
    *,
    ecosystem: str,
    package_manager: str,
    manifest_path: str,
    manifest_hash: str,
    lockfile_path: str = "",
    lockfile_hash: str = "",
    command: Sequence[str] = (),
) -> str:
    """Stable key for reusing environment prep evidence."""

    return sha256_text(
        "|".join(
            (
                ENVIRONMENT_PREP_COMMAND_TEMPLATE_VERSION,
                str(ecosystem or ""),
                str(package_manager or ""),
                _normalize_path(manifest_path),
                str(manifest_hash or ""),
                _normalize_path(lockfile_path),
                str(lockfile_hash or ""),
                "\x1f".join(_tuple_str(command)),
            )
        )
    )


def stable_environment_prep_id(prefix: str, *parts: Any) -> str:
    """Return a stable environment-prep identifier."""

    return f"{prefix}_{sha256_text('|'.join(str(part) for part in parts))[:24]}"


def _select_catalog_entries(
    changed_paths: set[str],
    workspace: str | Path | None,
) -> tuple[EnvironmentPrepCatalogEntry, ...]:
    entries: list[EnvironmentPrepCatalogEntry] = []
    for entry in environment_prep_catalog():
        if changed_paths.intersection(entry.manifests) or changed_paths.intersection(entry.lockfiles):
            if entry.ecosystem == "node" and entry.lockfiles and not _lockfile_available(
                entry.lockfiles,
                changed_paths,
                workspace,
            ):
                continue
            entries.append(entry)
    node_lock_entries = [entry for entry in entries if entry.ecosystem == "node" and entry.lockfiles]
    if node_lock_entries:
        return tuple(
            entry for entry in entries if entry.ecosystem != "node" or entry.lockfiles or entry.manifests != ("package.json",)
        )
    return tuple(entries)


def _lockfile_available(
    candidates: Sequence[str],
    changed_paths: set[str],
    workspace: str | Path | None,
) -> bool:
    if changed_paths.intersection(candidates):
        return True
    if workspace is None:
        return False
    root = Path(workspace)
    return any((root / candidate).is_file() for candidate in candidates)


def _select_path(candidates: Sequence[str], changed_paths: set[str], workspace: str | Path | None) -> str:
    for candidate in candidates:
        if candidate in changed_paths:
            return candidate
    if workspace is not None:
        root = Path(workspace)
        for candidate in candidates:
            if (root / candidate).is_file():
                return candidate
    return candidates[0] if candidates else ""


def _catalog_entry_for_requirement(requirement: EnvironmentRefreshRequirement) -> EnvironmentPrepCatalogEntry | None:
    for entry in environment_prep_catalog():
        if (
            entry.ecosystem == requirement.ecosystem
            and entry.package_manager == requirement.package_manager
            and entry.command == requirement.command
        ):
            return entry
    return None


def _coerce_requirement(
    value: EnvironmentRefreshRequirement | Mapping[str, Any],
) -> EnvironmentRefreshRequirement | None:
    if isinstance(value, EnvironmentRefreshRequirement):
        return value
    return _normalize_requirement(value, receipt_id=str(value.get("receipt_id") or ""))


def _normalize_requirement(item: Mapping[str, Any], *, receipt_id: str) -> EnvironmentRefreshRequirement | None:
    ecosystem = str(item.get("ecosystem") or "").strip()
    package_manager = str(item.get("package_manager") or "").strip()
    manifest = _normalize_path(str(item.get("manifest") or item.get("manifest_path") or ""))
    command = _tuple_str(item.get("command") or ())
    if not ecosystem and manifest == "package.json":
        ecosystem = "node"
        package_manager = "npm"
    if not ecosystem or not package_manager or not manifest or not command:
        return None
    candidate = EnvironmentRefreshRequirement(
        ecosystem=ecosystem,
        package_manager=package_manager,
        manifest=manifest,
        lockfile=str(item.get("lockfile") or item.get("lockfile_path") or ""),
        command=command,
        reason=str(item.get("reason") or "manifest_changed_before_revalidation"),
        receipt_id=str(item.get("receipt_id") or receipt_id),
        manifest_after_hash=str(item.get("manifest_after_hash") or ""),
        lockfile_after_hash=str(item.get("lockfile_after_hash") or ""),
        freshness_key=str(item.get("freshness_key") or ""),
    )
    return candidate if _catalog_entry_for_requirement(candidate) is not None else None


def _dedupe_requirements(
    requirements: Sequence[EnvironmentRefreshRequirement],
) -> tuple[EnvironmentRefreshRequirement, ...]:
    result: list[EnvironmentRefreshRequirement] = []
    seen: set[str] = set()
    for requirement in requirements:
        if requirement.freshness_key in seen:
            continue
        seen.add(requirement.freshness_key)
        result.append(requirement)
    return tuple(result)


def _workspace_hashes(workspace: str | Path | None) -> dict[str, str]:
    if workspace is None:
        return {}
    root = Path(workspace)
    hashes: dict[str, str] = {}
    for entry in environment_prep_catalog():
        for rel_path in (*entry.manifests, *entry.lockfiles):
            path = root / rel_path
            if not path.is_file():
                continue
            try:
                hashes[rel_path] = sha256_text(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError):
                continue
    return hashes


def _receipt_id(receipt: RepairReceipt | Mapping[str, Any]) -> str:
    if isinstance(receipt, RepairReceipt):
        return receipt.receipt_id
    return str(receipt.get("receipt_id") or "")


def _receipt_metadata(receipt: RepairReceipt | Mapping[str, Any]) -> Mapping[str, Any]:
    metadata = receipt.metadata if isinstance(receipt, RepairReceipt) else receipt.get("metadata")
    return metadata if isinstance(metadata, Mapping) else {}


def _receipt_files_changed(receipt: RepairReceipt | Mapping[str, Any]) -> tuple[str, ...]:
    if isinstance(receipt, RepairReceipt):
        return receipt.files_changed
    raw = receipt.get("files_changed")
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
        return ()
    return tuple(str(item) for item in raw)


def _receipt_after_hashes(receipt: RepairReceipt | Mapping[str, Any]) -> Mapping[str, str]:
    raw = receipt.after_hashes if isinstance(receipt, RepairReceipt) else receipt.get("after_hashes")
    if not isinstance(raw, Mapping):
        return {}
    return {_normalize_path(key): str(value) for key, value in raw.items()}


def _mapping_get(value: EnvironmentPrepReceipt | Mapping[str, Any], key: str) -> Any:
    if isinstance(value, EnvironmentPrepReceipt):
        return getattr(value, key)
    return value.get(key)


def _normalize_path(value: str | Path) -> str:
    return str(value or "").strip().replace("\\", "/").lstrip("./")


def _tuple_paths(value: Sequence[str]) -> tuple[str, ...]:
    return tuple(path for path in (_normalize_path(item) for item in value or ()) if path)


def _tuple_str(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    try:
        values = tuple(value)
    except TypeError:
        values = (value,)
    return tuple(str(item).strip() for item in values if str(item or "").strip())


def _non_empty(value: str, name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} must be non-empty")
    return normalized
