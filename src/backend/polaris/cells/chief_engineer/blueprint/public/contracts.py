from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import shlex
import unicodedata
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal

_PROVENANCE_MAX_IDENTITY_BYTES = 256
_PROVENANCE_MAX_PATH_BYTES = 1024
_PROVENANCE_MAX_TARGET_FILES = 512
_PROVENANCE_BLUEPRINT_SCHEMA = "chief_engineer.blueprint.v1"
_PROVENANCE_SNAPSHOT_SCHEMA = "chief_engineer.blueprint_provenance.v1"
_PROVENANCE_HASH_SCHEME = "chief_engineer.blueprint_hash.v1"
_PROJECT_COMPLETION_CONTRACT_SCHEMA_V1: Literal["polaris.project_completion_contract.v1"] = (
    "polaris.project_completion_contract.v1"
)
_PROJECT_COMPLETION_CONTRACT_ID_PREFIX = "project-completion-"
_PROJECT_COMPLETION_MAX_TOKEN_BYTES = 128
_PROJECT_COMPLETION_MAX_COMMAND_BYTES = 4096
_PROJECT_COMPLETION_MAX_TASK_IDS = 256
_PROJECT_COMPLETION_MAX_ARTIFACTS = 512
_PROJECT_COMPLETION_MAX_ENTRYPOINTS = 32
_PROJECT_COMPLETION_MAX_VERIFICATIONS = 64
_PROJECT_COMPLETION_MAX_VERIFIER_REFS = _PROJECT_COMPLETION_MAX_ARTIFACTS + _PROJECT_COMPLETION_MAX_ENTRYPOINTS


def project_completion_verifier_policy_snapshot_hash(snapshot: Mapping[str, Any]) -> str:
    """Bind the exact Factory-compiled verifier-policy snapshot without reimplementing its owner hash."""

    if not isinstance(snapshot, Mapping) or not snapshot:
        raise ValueError("verifier_policy_snapshot must be a non-empty mapping")
    try:
        encoded = json.dumps(
            {"domain": "polaris.ce.verifier_policy_snapshot.v1", "snapshot": dict(snapshot)},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("verifier_policy_snapshot must be canonical JSON") from exc
    return hashlib.sha256(encoded).hexdigest()


def project_completion_catalog_snapshot_hash(snapshot: Mapping[str, Any]) -> str:
    """Hash the exact platform catalog snapshot consumed by CE authority derivation."""

    if not isinstance(snapshot, Mapping):
        raise TypeError("catalog_snapshot must be a mapping")
    try:
        encoded = json.dumps(
            {"domain": "polaris.ce.project_catalog_snapshot.v1", "snapshot": dict(snapshot)},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("catalog_snapshot must be canonical JSON") from exc
    return hashlib.sha256(encoded).hexdigest()


def _require_non_empty(name: str, value: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} must be a non-empty string")
    return normalized


def _require_exact_non_empty(name: str, value: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be an exact non-empty string")
    return value


def _require_completion_token(name: str, value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be an exact non-empty string")
    if any(unicodedata.category(character).startswith("C") for character in value):
        raise ValueError(f"{name} must not contain control characters")
    if len(value.encode("utf-8")) > _PROJECT_COMPLETION_MAX_TOKEN_BYTES:
        raise ValueError(f"{name} exceeds {_PROJECT_COMPLETION_MAX_TOKEN_BYTES} UTF-8 bytes")
    return value


def _optional_completion_command(name: str, value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be an exact non-empty string")
    if len(value.splitlines()) != 1:
        raise ValueError(f"{name} must be single-line")
    if any(unicodedata.category(character).startswith("C") for character in value):
        raise ValueError(f"{name} must not contain control characters")
    if len(value.encode("utf-8")) > _PROJECT_COMPLETION_MAX_COMMAND_BYTES:
        raise ValueError(f"{name} exceeds {_PROJECT_COMPLETION_MAX_COMMAND_BYTES} UTF-8 bytes")
    return value


def _require_verifier_argv(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError("verification command argv must be a non-empty list or tuple")
    argv = tuple(_require_completion_token(f"argv[{index}]", item) for index, item in enumerate(value))
    if len(argv) > 128:
        raise ValueError("verification command argv must contain at most 128 items")
    return argv


def _require_verifier_cwd(value: object) -> str:
    if value == ".":
        return "."
    return _require_provenance_path("cwd", value)


def _verification_command_authority_hash(*, task_id: str, modality: str, argv: tuple[str, ...], cwd: str) -> str:
    encoded = json.dumps(
        {
            "domain": "polaris.project_completion_verification_command_authority.v1",
            "task_id": task_id,
            "modality": modality,
            "argv": list(argv),
            "cwd": cwd,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _project_kind_authority_hash(
    *,
    project_kind: str,
    source_ref: str,
    source_hash: str,
    justification: str,
) -> str:
    encoded = json.dumps(
        {
            "domain": "polaris.project_completion_project_kind_authority.v1",
            "project_kind": project_kind,
            "source_ref": source_ref,
            "source_hash": source_hash,
            "justification": justification,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


_NON_PROOF_EXECUTABLES = frozenset(
    {
        ":",
        "echo",
        "false",
        "noop",
        "no-op",
        "printf",
        "true",
        "pwd",
        "which",
        "where",
        "whereis",
    }
)
_INTROSPECTION_ARGUMENTS = frozenset({"--help", "-h", "help", "--version", "-V", "version"})
_INTROSPECTION_TOOL_EXECUTABLES = frozenset(
    {
        "bun",
        "cargo",
        "cmake",
        "deno",
        "dotnet",
        "go",
        "java",
        "javac",
        "mvn",
        "node",
        "npm",
        "npx",
        "php",
        "pip",
        "pip3",
        "pnpm",
        "pytest",
        "python",
        "python3",
        "ruby",
        "ruff",
        "rustc",
        "tsc",
        "yarn",
    }
)
_SHORT_VERSION_EXECUTABLES = frozenset(
    {"bun", "deno", "go", "java", "node", "npm", "php", "python", "python3", "ruby", "rustc"}
)
_INLINE_INTERPRETER_FLAGS = frozenset({"-c", "-e", "--eval"})
_NOOP_INLINE_PROGRAMS = frozenset(
    {
        "0",
        "pass",
        "print('ok')",
        'print("ok")',
        "exit(0)",
        "sys.exit(0)",
        "console.log('ok')",
        'console.log("ok")',
    }
)


def _require_verification_command_proof(argv: tuple[str, ...]) -> None:
    """Reject commands that can succeed without proving any delivery work."""

    executable = PurePosixPath(argv[0].replace("\\", "/")).name.casefold()
    if executable in _NON_PROOF_EXECUTABLES:
        raise ValueError("verification command must provide proof-of-work, not a no-op")
    introspection_requested = any(argument in _INTROSPECTION_ARGUMENTS for argument in argv[1:])
    if introspection_requested and (len(argv) == 2 or executable in _INTROSPECTION_TOOL_EXECUTABLES):
        raise ValueError("verification command must provide proof-of-work, not help/version introspection")
    if len(argv) == 2 and argv[1] == "-v" and executable in _SHORT_VERSION_EXECUTABLES:
        raise ValueError("verification command must provide proof-of-work, not version introspection")
    if len(argv) >= 3 and argv[1] in _INLINE_INTERPRETER_FLAGS:
        inline_program = " ".join(argv[2:]).strip().casefold().rstrip(";")
        if inline_program in _NOOP_INLINE_PROGRAMS:
            raise ValueError("verification command must provide proof-of-work, not an inline no-op")


def _require_provenance_text(name: str, value: object, *, max_utf8_bytes: int) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be a string")
    if not value or value != value.strip():
        raise ValueError(f"{name} must be an exact non-empty string")
    if unicodedata.normalize("NFC", value) != value:
        raise ValueError(f"{name} must be NFC-normalized")
    if any(unicodedata.category(character).startswith("C") for character in value):
        raise ValueError(f"{name} must not contain control characters")
    if len(value.encode("utf-8")) > max_utf8_bytes:
        raise ValueError(f"{name} exceeds {max_utf8_bytes} UTF-8 bytes")
    return value


def _require_provenance_identity(name: str, value: object) -> str:
    return _require_provenance_text(name, value, max_utf8_bytes=_PROVENANCE_MAX_IDENTITY_BYTES)


def _require_provenance_blueprint_id(name: str, value: object) -> str:
    token = _require_provenance_identity(name, value)
    if token in {".", ".."} or "/" in token or "\\" in token:
        raise ValueError(f"{name} must be a safe filename token")
    return token


def _require_provenance_path(name: str, value: object) -> str:
    path = _require_provenance_text(name, value, max_utf8_bytes=_PROVENANCE_MAX_PATH_BYTES)
    if "\\" in path:
        raise ValueError(f"{name} must use POSIX separators")
    windows_path = PureWindowsPath(path)
    posix_path = PurePosixPath(path)
    if windows_path.drive or windows_path.root or posix_path.is_absolute():
        raise ValueError(f"{name} must be a relative POSIX path")
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"{name} must not contain empty, dot, or parent components")
    if PurePosixPath(*parts).as_posix() != path:
        raise ValueError(f"{name} must be a canonical POSIX path")
    return path


def _require_provenance_sha256(name: str, value: object) -> str:
    token = _require_provenance_text(name, value, max_utf8_bytes=64)
    if len(token) != 64 or any(character not in "0123456789abcdef" for character in token):
        raise ValueError(f"{name} must be lower-case 64-hex")
    return token


def _strict_provenance_target_paths(
    name: str,
    value: object,
    *,
    require_list: bool,
) -> tuple[str, ...]:
    expected_type = list if require_list else tuple
    if type(value) is not expected_type:
        raise TypeError(f"{name} must be a {expected_type.__name__}")
    if len(value) > _PROVENANCE_MAX_TARGET_FILES:
        raise ValueError(f"{name} must contain at most {_PROVENANCE_MAX_TARGET_FILES} paths")
    paths = tuple(_require_provenance_path(f"{name}[{index}]", item) for index, item in enumerate(value))
    if len(set(paths)) != len(paths):
        raise ValueError(f"{name} must not contain duplicate paths")
    return paths


def _to_dict_copy(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(payload or {})


def _string_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        rows = [value]
    elif isinstance(value, (list, tuple, set)):
        rows = list(value)
    else:
        return ()
    return tuple(str(item).strip() for item in rows if str(item or "").strip())


def _strict_unique_string_tuple(
    name: str,
    value: Any,
    *,
    require_items: bool = False,
) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise TypeError(f"{name} must be a list or tuple of strings")

    rows: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        token = str(item).strip()
        if not token:
            raise ValueError(f"{name}[{index}] must be a non-empty string")
        if token in seen:
            continue
        seen.add(token)
        rows.append(token)
    if require_items and not rows:
        raise ValueError(f"{name} must contain at least one item")
    return tuple(rows)


def _normalize_relative_portfolio_path(name: str, value: str) -> str:
    token = _require_non_empty(name, value)
    if "\x00" in token or "://" in token or token.startswith("~"):
        raise ValueError(f"{name} must be a workspace-relative path")

    windows_path = PureWindowsPath(token)
    normalized_path = PurePosixPath(token.replace("\\", "/"))
    if windows_path.drive or windows_path.root or normalized_path.is_absolute():
        raise ValueError(f"{name} must be a workspace-relative path")
    if any(part == ".." for part in normalized_path.parts):
        raise ValueError(f"{name} must not contain parent traversal")

    parts = tuple(part for part in normalized_path.parts if part not in {"", "."})
    if not parts:
        raise ValueError(f"{name} must identify a path below the workspace root")
    return PurePosixPath(*parts).as_posix()


def _require_safe_filename_token(name: str, value: str) -> str:
    token = _require_non_empty(name, value)
    if token in {".", ".."} or any(char in token for char in ("/", "\\", "\x00")):
        raise ValueError(f"{name} must be a safe filename token")
    return token


def _relative_path_tuple(
    name: str,
    value: Any,
    *,
    require_items: bool = False,
) -> tuple[str, ...]:
    raw_paths = _strict_unique_string_tuple(name, value, require_items=require_items)
    paths: list[str] = []
    seen: set[str] = set()
    for index, raw_path in enumerate(raw_paths):
        path = _normalize_relative_portfolio_path(f"{name}[{index}]", raw_path)
        if path in seen:
            continue
        seen.add(path)
        paths.append(path)
    if require_items and not paths:
        raise ValueError(f"{name} must contain at least one workspace-relative path")
    return tuple(paths)


def _json_safe_mapping(value: Mapping[str, Any] | None) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for key, item in dict(value or {}).items():
        if isinstance(item, Mapping):
            data[str(key)] = _json_safe_mapping(item)
        elif isinstance(item, (list, tuple, set)):
            data[str(key)] = [_json_safe_mapping(v) if isinstance(v, Mapping) else v for v in item]
        else:
            data[str(key)] = item
    return data


# ---------------------------------------------------------------------------
# Enums (Tier-1 governance surface)
# ---------------------------------------------------------------------------


class RiskSeverity(str, Enum):
    """Severity ladder for a Risk Register entry."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
    BLOCKER = "blocker"


class RiskStatus(str, Enum):
    """Lifecycle status of a Risk Register entry."""

    OPEN = "open"
    MITIGATING = "mitigating"
    ACCEPTED = "accepted"
    RESOLVED = "resolved"
    REVERTED = "reverted"


class TechDebtSeverity(str, Enum):
    """Severity ladder for a Tech-Debt Ledger entry."""

    TRIVIAL = "trivial"
    MINOR = "minor"
    MAJOR = "major"
    SEVERE = "severe"
    FATAL = "fatal"


class TechDebtStatus(str, Enum):
    """Lifecycle status of a Tech-Debt Ledger entry."""

    REGISTERED = "registered"
    ACKNOWLEDGED = "acknowledged"
    SCHEDULED = "scheduled"
    PAID = "paid"
    WONTFIX = "wontfix"


class RollbackStrategy(str, Enum):
    """Rollback strategy attached to a blueprint."""

    GIT_REVERT = "git_revert"
    MANIFEST_RESTORE = "manifest_restore"
    FILE_SNAPSHOT = "file_snapshot"


class ADRStatus(str, Enum):
    """Lifecycle status of an Architecture Decision Record (decision log).

    This is the human-facing decision-log status — distinct from the
    internal construction-plan ADR compiler in ``adr_store.py``.
    """

    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    SUPERSEDED = "superseded"
    DEPRECATED = "deprecated"
    REJECTED = "rejected"


class TechRadarRing(str, Enum):
    """ThoughtWorks-style Tech-Radar ring for a library / technology.

    ``adopt`` and ``trial`` are permitted; ``hold`` and ``deprecated`` are
    stack-policy violations when a blueprint depends on them.
    """

    ADOPT = "adopt"
    TRIAL = "trial"
    HOLD = "hold"
    DEPRECATED = "deprecated"


class IncidentSeverity(str, Enum):
    """Incident severity ladder for a post-mortem (sev1 = most severe)."""

    SEV1 = "sev1"
    SEV2 = "sev2"
    SEV3 = "sev3"
    SEV4 = "sev4"


class ReleaseDecision(str, Enum):
    """Executive release / change-advisory verdict.

    ``go`` = clear; ``conditional_go`` = warnings only (ship with awareness);
    ``no_go`` = at least one hard blocker (release must not proceed).
    """

    GO = "go"
    CONDITIONAL_GO = "conditional_go"
    NO_GO = "no_go"


class PostMortemStatus(str, Enum):
    """Lifecycle status of a post-mortem / incident review."""

    DRAFT = "draft"
    REVIEWING = "reviewing"
    PUBLISHED = "published"
    ACTIONS_OPEN = "actions_open"
    CLOSED = "closed"


@dataclass(frozen=True)
class GenerateTaskBlueprintCommandV1:
    task_id: str
    workspace: str
    objective: str
    run_id: str | None = None
    constraints: Mapping[str, Any] = field(default_factory=dict)
    context: Mapping[str, Any] = field(default_factory=dict)
    llm_blueprint: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "objective", _require_non_empty("objective", self.objective))
        object.__setattr__(self, "constraints", _to_dict_copy(self.constraints))
        object.__setattr__(self, "context", _to_dict_copy(self.context))
        object.__setattr__(self, "llm_blueprint", _to_dict_copy(self.llm_blueprint))


@dataclass(frozen=True)
class ChiefEngineerPortfolioTaskV1:
    """Authoritative PM task facts supplied to a project-level CE portfolio.

    The Chief Engineer may advise on these facts but cannot add target or scope
    paths. Paths are normalized to workspace-relative POSIX form so downstream
    scope intersections remain deterministic across operating systems.
    """

    task_id: str
    objective: str
    target_files: tuple[str, ...]
    scope_paths: tuple[str, ...] = field(default_factory=tuple)
    dependencies: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        task_id = _require_non_empty("task_id", self.task_id)
        dependencies = _strict_unique_string_tuple("dependencies", self.dependencies)
        if task_id in dependencies:
            raise ValueError("dependencies must not contain the task's own task_id")

        object.__setattr__(self, "task_id", task_id)
        object.__setattr__(self, "objective", _require_non_empty("objective", self.objective))
        object.__setattr__(
            self,
            "target_files",
            _relative_path_tuple("target_files", self.target_files, require_items=True),
        )
        object.__setattr__(self, "scope_paths", _relative_path_tuple("scope_paths", self.scope_paths))
        object.__setattr__(self, "dependencies", dependencies)

    def to_dict(self) -> dict[str, Any]:
        """Return the normalized PM-authoritative task projection."""

        return {
            "task_id": self.task_id,
            "objective": self.objective,
            "target_files": list(self.target_files),
            "scope_paths": list(self.scope_paths),
            "dependencies": list(self.dependencies),
        }


_PORTFOLIO_AUTHORITY_CARRIER_SEAL = object()
_PORTFOLIO_AUTHORITY_SIGNING_KEY = secrets.token_bytes(32)


@dataclass(frozen=True, slots=True, init=False)
class _ChiefEngineerPortfolioAuthorityCarrierV1:
    """Opaque, one-run authority issued only by the Factory owner path.

    This type is intentionally absent from the public Cell exports.  Its
    constructor requires a module-private identity seal, so public callers
    cannot replace committed PM/catalog/verifier evidence with self-consistent
    hashes.  The CE owner still revalidates mutable catalog state immediately
    before persistence.
    """

    workspace: str
    run_id: str
    project_id: str
    pm_stage_event_id: str
    pm_contract_hash: str
    tasks: tuple[ChiefEngineerPortfolioTaskV1, ...]
    catalog_snapshot: Mapping[str, Any]
    catalog_snapshot_hash: str
    catalog_version: str
    catalog_receipt_hash: str
    verifier_policy_hash: str
    verifier_policy_snapshot: Mapping[str, Any]
    verifier_policy_snapshot_hash: str
    verifier_policy_receipt_hash: str
    verification_command_authority: tuple[VerificationCommandAuthorityV1, ...]
    authority_signature: str

    def __init__(
        self,
        *,
        _seal: object,
        workspace: str,
        run_id: str,
        project_id: str,
        pm_stage_event_id: str,
        pm_contract_hash: str,
        tasks: tuple[ChiefEngineerPortfolioTaskV1, ...],
        catalog_snapshot: Mapping[str, Any],
        catalog_snapshot_hash: str,
        verifier_policy_hash: str,
        verifier_policy_snapshot: Mapping[str, Any],
        verifier_policy_snapshot_hash: str,
        verification_command_authority: tuple[VerificationCommandAuthorityV1, ...],
    ) -> None:
        if _seal is not _PORTFOLIO_AUTHORITY_CARRIER_SEAL:
            raise TypeError("Chief Engineer portfolio authority carrier is owner-issued only")
        normalized_workspace = _require_non_empty("workspace", workspace)
        normalized_run_id = _require_completion_token("run_id", run_id)
        normalized_project_id = _require_completion_token("project_id", project_id)
        normalized_pm_event = _require_completion_token("pm_stage_event_id", pm_stage_event_id)
        normalized_pm_hash = _require_provenance_sha256("pm_contract_hash", pm_contract_hash)
        normalized_catalog = deepcopy(dict(catalog_snapshot))
        normalized_catalog_hash = _require_provenance_sha256("catalog_snapshot_hash", catalog_snapshot_hash)
        if project_completion_catalog_snapshot_hash(normalized_catalog) != normalized_catalog_hash:
            raise ValueError("catalog_snapshot_hash does not bind catalog_snapshot")
        normalized_policy = deepcopy(dict(verifier_policy_snapshot))
        normalized_policy_hash = _require_provenance_sha256("verifier_policy_hash", verifier_policy_hash)
        normalized_policy_snapshot_hash = _require_provenance_sha256(
            "verifier_policy_snapshot_hash", verifier_policy_snapshot_hash
        )
        if project_completion_verifier_policy_snapshot_hash(normalized_policy) != normalized_policy_snapshot_hash:
            raise ValueError("verifier_policy_snapshot_hash does not bind verifier_policy_snapshot")
        if normalized_policy.get("policy_hash") != normalized_policy_hash:
            raise ValueError("verifier_policy_snapshot policy_hash does not match verifier_policy_hash")
        if normalized_policy.get("schema_version") != "evidence_policy.v1" or (
            normalized_policy.get("source") != "control_plane.verifier_policy.evidence_policy_compiler"
        ):
            raise ValueError("verifier_policy_snapshot is not an evidence policy compiler snapshot")
        required_modalities = normalized_policy.get("required_evidence_modalities")
        if not isinstance(required_modalities, list) or "command" not in required_modalities:
            raise ValueError("verifier_policy_snapshot must require command evidence")
        normalized_tasks = tuple(tasks)
        if not normalized_tasks or any(type(task) is not ChiefEngineerPortfolioTaskV1 for task in normalized_tasks):
            raise TypeError("authority carrier tasks must be exact ChiefEngineerPortfolioTaskV1 values")
        normalized_commands = tuple(verification_command_authority)
        if not normalized_commands or any(type(item) is not VerificationCommandAuthorityV1 for item in normalized_commands):
            raise TypeError("authority carrier commands must be exact VerificationCommandAuthorityV1 values")
        task_ids = {task.task_id for task in normalized_tasks}
        if any(item.task_id not in task_ids for item in normalized_commands):
            raise ValueError("verification command authority task_id is outside carrier tasks")
        catalog_receipt_hash = _portfolio_authority_receipt_hash(
            domain="catalog",
            workspace=normalized_workspace,
            run_id=normalized_run_id,
            project_id=normalized_project_id,
            pm_stage_event_id=normalized_pm_event,
            pm_contract_hash=normalized_pm_hash,
            evidence_hash=normalized_catalog_hash,
        )
        verifier_policy_receipt_hash = _portfolio_authority_receipt_hash(
            domain="verifier_policy",
            workspace=normalized_workspace,
            run_id=normalized_run_id,
            project_id=normalized_project_id,
            pm_stage_event_id=normalized_pm_event,
            pm_contract_hash=normalized_pm_hash,
            evidence_hash=normalized_policy_snapshot_hash,
        )
        for name, value in (
            ("workspace", normalized_workspace),
            ("run_id", normalized_run_id),
            ("project_id", normalized_project_id),
            ("pm_stage_event_id", normalized_pm_event),
            ("pm_contract_hash", normalized_pm_hash),
            ("tasks", normalized_tasks),
            ("catalog_snapshot", normalized_catalog),
            ("catalog_snapshot_hash", normalized_catalog_hash),
            ("catalog_version", f"sha256:{normalized_catalog_hash}"),
            ("catalog_receipt_hash", catalog_receipt_hash),
            ("verifier_policy_hash", normalized_policy_hash),
            ("verifier_policy_snapshot", normalized_policy),
            ("verifier_policy_snapshot_hash", normalized_policy_snapshot_hash),
            ("verifier_policy_receipt_hash", verifier_policy_receipt_hash),
            ("verification_command_authority", normalized_commands),
        ):
            object.__setattr__(self, name, value)
        object.__setattr__(self, "authority_signature", _portfolio_authority_carrier_signature(self))


def _portfolio_authority_receipt_hash(
    *,
    domain: str,
    workspace: str,
    run_id: str,
    project_id: str,
    pm_stage_event_id: str,
    pm_contract_hash: str,
    evidence_hash: str,
) -> str:
    seed = {
        "schema_version": "polaris.ce_portfolio_authority_receipt.v1",
        "domain": domain,
        "workspace": str(Path(workspace).resolve()),
        "run_id": run_id,
        "project_id": project_id,
        "pm_stage_event_id": pm_stage_event_id,
        "pm_contract_hash": pm_contract_hash,
        "evidence_hash": evidence_hash,
    }
    return hashlib.sha256(
        json.dumps(seed, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _issue_chief_engineer_portfolio_authority_carrier(
    **kwargs: Any,
) -> _ChiefEngineerPortfolioAuthorityCarrierV1:
    """Private Factory-owner bridge; deliberately not exported by the Cell."""

    return _ChiefEngineerPortfolioAuthorityCarrierV1(_seal=_PORTFOLIO_AUTHORITY_CARRIER_SEAL, **kwargs)


def _portfolio_authority_carrier_signature(carrier: _ChiefEngineerPortfolioAuthorityCarrierV1) -> str:
    seed = {
        "schema_version": "polaris.ce_portfolio_authority_carrier.v1",
        "workspace": str(Path(carrier.workspace).resolve()),
        "run_id": carrier.run_id,
        "project_id": carrier.project_id,
        "pm_stage_event_id": carrier.pm_stage_event_id,
        "pm_contract_hash": carrier.pm_contract_hash,
        "tasks": [task.to_dict() for task in carrier.tasks],
        "catalog_snapshot_hash": carrier.catalog_snapshot_hash,
        "catalog_version": carrier.catalog_version,
        "catalog_receipt_hash": carrier.catalog_receipt_hash,
        "verifier_policy_hash": carrier.verifier_policy_hash,
        "verifier_policy_snapshot_hash": carrier.verifier_policy_snapshot_hash,
        "verifier_policy_receipt_hash": carrier.verifier_policy_receipt_hash,
        "verification_command_authority_hashes": sorted(
            item.authority_hash for item in carrier.verification_command_authority
        ),
    }
    encoded = json.dumps(seed, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(_PORTFOLIO_AUTHORITY_SIGNING_KEY, encoded, hashlib.sha256).hexdigest()


def _verify_chief_engineer_portfolio_authority_carrier(
    carrier: _ChiefEngineerPortfolioAuthorityCarrierV1,
) -> bool:
    """Verify opaque owner signature without exposing signing material."""

    if type(carrier) is not _ChiefEngineerPortfolioAuthorityCarrierV1:
        return False
    return hmac.compare_digest(carrier.authority_signature, _portfolio_authority_carrier_signature(carrier))


@dataclass(frozen=True)
class BuildChiefEngineerBlueprintPortfolioCommandV1:
    """Build one CE portfolio using an opaque Factory-issued authority carrier."""

    workspace: str
    run_id: str
    tasks: tuple[ChiefEngineerPortfolioTaskV1, ...]
    authority_carrier: object | None = field(default=None, repr=False)
    llm_blueprint: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.tasks, (list, tuple)):
            raise TypeError("tasks must be a list or tuple of ChiefEngineerPortfolioTaskV1")
        if not self.tasks:
            raise ValueError("tasks must contain at least one task")

        tasks: list[ChiefEngineerPortfolioTaskV1] = []
        seen_task_ids: set[str] = set()
        for index, task in enumerate(self.tasks):
            if type(task) is not ChiefEngineerPortfolioTaskV1:
                raise TypeError(f"tasks[{index}] must be exact ChiefEngineerPortfolioTaskV1")
            if task.task_id in seen_task_ids:
                raise ValueError(f"duplicate task_id in portfolio command: {task.task_id}")
            seen_task_ids.add(task.task_id)
            tasks.append(task)

        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "run_id", _require_completion_token("run_id", self.run_id))
        llm_blueprint = _to_dict_copy(self.llm_blueprint)
        if llm_blueprint:
            if type(self.authority_carrier) is not _ChiefEngineerPortfolioAuthorityCarrierV1:
                raise TypeError("advisory portfolio requires exact Factory-issued authority_carrier")
            carrier = self.authority_carrier
            if carrier.workspace != self.workspace or carrier.run_id != self.run_id or carrier.tasks != tuple(tasks):
                raise ValueError("authority_carrier identity does not match portfolio command")
        elif self.authority_carrier is not None:
            raise ValueError("offline diagnostic portfolio must not carry execution authority")
        object.__setattr__(self, "tasks", tuple(tasks))
        object.__setattr__(self, "llm_blueprint", llm_blueprint)


@dataclass(frozen=True, slots=True)
class QueryProjectCompletionContractV1:
    """Read one persisted CE-owned completion contract by exact authority identity."""

    workspace: str
    project_id: str
    run_id: str
    contract_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "project_id", _require_completion_token("project_id", self.project_id))
        object.__setattr__(self, "run_id", _require_completion_token("run_id", self.run_id))
        object.__setattr__(
            self,
            "contract_hash",
            _require_provenance_sha256("contract_hash", self.contract_hash),
        )


@dataclass(frozen=True)
class ChiefEngineerProjectInterfaceContractV1:
    """Shared task/file ownership and advisory project interface declarations."""

    contract_id: str
    contract_ref: str
    contract_hash: str
    task_file_ownership: Mapping[str, tuple[str, ...]]
    file_task_ownership: Mapping[str, tuple[str, ...]]
    provider_declarations: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    consumer_declarations: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        contract_id = _require_safe_filename_token("contract_id", self.contract_id)
        contract_ref = _require_non_empty("contract_ref", self.contract_ref)
        path_part, separator, fragment = contract_ref.partition("#")
        if separator != "#" or fragment != "project_interface_contract":
            raise ValueError("contract_ref must target the project_interface_contract fragment")
        normalized_path = _normalize_relative_portfolio_path("contract_ref", path_part)
        normalized_ref = f"{normalized_path}#project_interface_contract"

        if not isinstance(self.task_file_ownership, Mapping):
            raise TypeError("task_file_ownership must be a mapping")
        if not isinstance(self.file_task_ownership, Mapping):
            raise TypeError("file_task_ownership must be a mapping")

        task_file_ownership: dict[str, tuple[str, ...]] = {}
        expected_file_owners: dict[str, list[str]] = {}
        for raw_task_id, raw_paths in self.task_file_ownership.items():
            task_id = _require_non_empty("task_file_ownership task_id", str(raw_task_id))
            if task_id in task_file_ownership:
                raise ValueError(f"duplicate task_file_ownership task_id: {task_id}")
            paths = _relative_path_tuple(
                f"task_file_ownership[{task_id!r}]",
                raw_paths,
                require_items=True,
            )
            task_file_ownership[task_id] = paths
            for path in paths:
                expected_file_owners.setdefault(path, []).append(task_id)

        file_task_ownership: dict[str, tuple[str, ...]] = {}
        for raw_path, raw_task_ids in self.file_task_ownership.items():
            path = _normalize_relative_portfolio_path("file_task_ownership path", str(raw_path))
            if path in file_task_ownership:
                raise ValueError(f"duplicate normalized file_task_ownership path: {path}")
            file_task_ownership[path] = _strict_unique_string_tuple(
                f"file_task_ownership[{path!r}]",
                raw_task_ids,
                require_items=True,
            )

        expected_reverse = {path: tuple(task_ids) for path, task_ids in expected_file_owners.items()}
        if file_task_ownership != expected_reverse:
            raise ValueError("file_task_ownership must be the exact reverse of task_file_ownership")

        def normalize_declarations(name: str, value: Any) -> tuple[dict[str, Any], ...]:
            if not isinstance(value, (list, tuple)):
                raise TypeError(f"{name} must be a list or tuple of mappings")
            declarations: list[dict[str, Any]] = []
            for index, declaration in enumerate(value):
                if not isinstance(declaration, Mapping):
                    raise TypeError(f"{name}[{index}] must be a mapping")
                declarations.append(_json_safe_mapping(declaration))
            return tuple(declarations)

        object.__setattr__(self, "contract_id", contract_id)
        object.__setattr__(self, "contract_ref", normalized_ref)
        object.__setattr__(self, "contract_hash", _require_non_empty("contract_hash", self.contract_hash))
        object.__setattr__(self, "task_file_ownership", task_file_ownership)
        object.__setattr__(self, "file_task_ownership", file_task_ownership)
        object.__setattr__(
            self,
            "provider_declarations",
            normalize_declarations("provider_declarations", self.provider_declarations),
        )
        object.__setattr__(
            self,
            "consumer_declarations",
            normalize_declarations("consumer_declarations", self.consumer_declarations),
        )

    def to_reference(self) -> dict[str, str]:
        """Return the stable shared interface-contract identity."""

        return {
            "schema_version": "chief_engineer.project_interface_contract.reference.v1",
            "project_interface_contract_id": self.contract_id,
            "project_interface_contract_ref": self.contract_ref,
            "project_interface_contract_hash": self.contract_hash,
        }

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical shared project interface contract."""

        return {
            "schema_version": "chief_engineer.project_interface_contract.v1",
            "project_interface_contract_id": self.contract_id,
            "project_interface_contract_ref": self.contract_ref,
            "project_interface_contract_hash": self.contract_hash,
            "task_file_ownership": {task_id: list(paths) for task_id, paths in self.task_file_ownership.items()},
            "file_task_ownership": {path: list(task_ids) for path, task_ids in self.file_task_ownership.items()},
            "provider_declarations": [_json_safe_mapping(declaration) for declaration in self.provider_declarations],
            "consumer_declarations": [_json_safe_mapping(declaration) for declaration in self.consumer_declarations],
            "ownership_authority": "pm_authoritative_tasks",
            "interface_declaration_authority": "chief_engineer_advisory_only",
            "authoritative": False,
        }


@dataclass(frozen=True)
class ChiefEngineerBlueprintPortfolioV1:
    """Immutable project-level CE advice with task-scoped canonical overlays."""

    portfolio_id: str
    workspace: str
    run_id: str
    portfolio_path: str
    portfolio_hash: str
    task_ids: tuple[str, ...]
    task_overlays: Mapping[str, Mapping[str, Any]]
    scope_advisory: Mapping[str, Mapping[str, Any]]
    project_interface_contract: ChiefEngineerProjectInterfaceContractV1
    project_interface_contract_ref: str
    project_interface_contract_hash: str
    project_completion_contract: ProjectCompletionContractV1 | None
    project_completion_contract_ref: str | None
    project_completion_contract_hash: str | None
    risk_flags: tuple[str, ...]
    llm_blueprint_consumed: bool
    usage_mode: Literal["advisory_overlay", "offline_diagnostic_only"]
    authority: Literal["advisory_only"] = "advisory_only"
    immutable: bool = True
    handoff_ready: Literal[False] = False
    execution_authorized: Literal[False] = False

    def __post_init__(self) -> None:
        portfolio_id = _require_safe_filename_token("portfolio_id", self.portfolio_id)
        portfolio_hash = _require_non_empty("portfolio_hash", self.portfolio_hash)

        portfolio_path = _normalize_relative_portfolio_path("portfolio_path", self.portfolio_path)
        expected_path = f"runtime/blueprints/{portfolio_id}.json"
        if portfolio_path != expected_path:
            raise ValueError(f"portfolio_path must equal {expected_path!r}")

        task_ids = _strict_unique_string_tuple("task_ids", self.task_ids, require_items=True)
        if len(task_ids) != len(self.task_ids):
            raise ValueError("task_ids must not contain duplicates")
        if not isinstance(self.task_overlays, Mapping):
            raise TypeError("task_overlays must be a mapping keyed by task_id")
        if not isinstance(self.scope_advisory, Mapping):
            raise TypeError("scope_advisory must be a mapping keyed by task_id")

        expected_task_ids = set(task_ids)
        if set(self.task_overlays) != expected_task_ids:
            raise ValueError("task_overlays keys must exactly match task_ids")
        if set(self.scope_advisory) != expected_task_ids:
            raise ValueError("scope_advisory keys must exactly match task_ids")
        if not isinstance(self.project_interface_contract, ChiefEngineerProjectInterfaceContractV1):
            raise TypeError("project_interface_contract must be ChiefEngineerProjectInterfaceContractV1")
        if set(self.project_interface_contract.task_file_ownership) != expected_task_ids:
            raise ValueError("project_interface_contract task ownership must exactly match task_ids")

        interface_ref = _require_non_empty(
            "project_interface_contract_ref",
            self.project_interface_contract_ref,
        )
        interface_hash = _require_non_empty(
            "project_interface_contract_hash",
            self.project_interface_contract_hash,
        )
        if interface_ref != self.project_interface_contract.contract_ref:
            raise ValueError("project_interface_contract_ref must match the shared contract")
        if interface_hash != self.project_interface_contract.contract_hash:
            raise ValueError("project_interface_contract_hash must match the shared contract")

        if not isinstance(self.llm_blueprint_consumed, bool):
            raise TypeError("llm_blueprint_consumed must be a bool")
        expected_usage_mode = "advisory_overlay" if self.llm_blueprint_consumed else "offline_diagnostic_only"
        if self.usage_mode != expected_usage_mode:
            raise ValueError(f"usage_mode must be {expected_usage_mode!r}")

        completion_contract = self.project_completion_contract
        completion_ref = self.project_completion_contract_ref
        completion_hash = self.project_completion_contract_hash
        if self.llm_blueprint_consumed:
            if type(completion_contract) is not ProjectCompletionContractV1:
                raise TypeError("advisory portfolio requires exact ProjectCompletionContractV1")
            expected_completion_ref = f"{portfolio_path}#project_completion_contract"
            if completion_ref != expected_completion_ref:
                raise ValueError("project_completion_contract_ref must target the portfolio contract fragment")
            if completion_hash != completion_contract.contract_hash:
                raise ValueError("project_completion_contract_hash must match the project completion contract")
            if completion_contract.run_id != self.run_id:
                raise ValueError("project completion contract run_id must match portfolio run_id")
            if completion_contract.covered_task_ids != tuple(sorted(task_ids)):
                raise ValueError("project completion contract must cover the exact portfolio task set")
        elif completion_contract is not None or completion_ref is not None or completion_hash is not None:
            raise ValueError("offline diagnostic portfolio cannot bind a project completion contract")

        task_overlays: dict[str, dict[str, Any]] = {}
        scope_advisory: dict[str, dict[str, Any]] = {}
        required_overlay_keys = {
            "authority",
            "construction_plan",
            "execution_authorized",
            "handoff_ready",
            "llm_blueprint_consumed",
            "portfolio_hash",
            "portfolio_id",
            "portfolio_path",
            "project_interface_contract_hash",
            "project_interface_contract_ref",
            "project_completion_contract_hash",
            "project_completion_contract_ref",
            "reference",
            "risk_flags",
            "scope_for_apply",
            "usage_mode",
        }
        for task_id in task_ids:
            overlay = self.task_overlays[task_id]
            if not isinstance(overlay, Mapping):
                raise TypeError(f"task_overlays[{task_id!r}] must be a mapping")
            if set(overlay) != required_overlay_keys:
                raise ValueError(f"task_overlays[{task_id!r}] does not match the canonical portfolio overlay shape")
            construction_plan = overlay.get("construction_plan")
            if not isinstance(construction_plan, Mapping):
                raise TypeError(f"task_overlays[{task_id!r}].construction_plan must be a mapping")
            if overlay.get("portfolio_id") != portfolio_id:
                raise ValueError(f"task_overlays[{task_id!r}] portfolio_id binding mismatch")
            if overlay.get("portfolio_path") != portfolio_path:
                raise ValueError(f"task_overlays[{task_id!r}] portfolio_path binding mismatch")
            if overlay.get("portfolio_hash") != portfolio_hash:
                raise ValueError(f"task_overlays[{task_id!r}] portfolio_hash binding mismatch")
            if overlay.get("project_interface_contract_ref") != interface_ref:
                raise ValueError(f"task_overlays[{task_id!r}] interface ref binding mismatch")
            if overlay.get("project_interface_contract_hash") != interface_hash:
                raise ValueError(f"task_overlays[{task_id!r}] interface hash binding mismatch")
            if overlay.get("project_completion_contract_ref") != completion_ref:
                raise ValueError(f"task_overlays[{task_id!r}] completion ref binding mismatch")
            if overlay.get("project_completion_contract_hash") != completion_hash:
                raise ValueError(f"task_overlays[{task_id!r}] completion hash binding mismatch")
            if overlay.get("authority") != "advisory_only":
                raise ValueError(f"task_overlays[{task_id!r}] authority must be advisory_only")
            if overlay.get("llm_blueprint_consumed") is not self.llm_blueprint_consumed:
                raise ValueError(f"task_overlays[{task_id!r}] LLM consumption binding mismatch")
            if overlay.get("usage_mode") != self.usage_mode:
                raise ValueError(f"task_overlays[{task_id!r}] usage_mode binding mismatch")
            if overlay.get("handoff_ready") is not False:
                raise ValueError(f"task_overlays[{task_id!r}] cannot declare handoff readiness")
            if overlay.get("execution_authorized") is not False:
                raise ValueError(f"task_overlays[{task_id!r}] cannot authorize execution")

            reference = overlay.get("reference")
            if not isinstance(reference, Mapping):
                raise TypeError(f"task_overlays[{task_id!r}].reference must be a mapping")
            expected_reference = {
                "schema_version": "chief_engineer.blueprint_portfolio.task_reference.v1",
                "task_id": task_id,
                "portfolio_id": portfolio_id,
                "portfolio_path": portfolio_path,
                "portfolio_hash": portfolio_hash,
                "project_interface_contract_ref": interface_ref,
                "project_interface_contract_hash": interface_hash,
                "project_completion_contract_ref": completion_ref,
                "project_completion_contract_hash": completion_hash,
            }
            if dict(reference) != expected_reference:
                raise ValueError(f"task_overlays[{task_id!r}].reference binding mismatch")

            task_overlays[task_id] = {
                "construction_plan": _json_safe_mapping(construction_plan),
                "scope_for_apply": list(
                    _relative_path_tuple(
                        f"task_overlays[{task_id!r}].scope_for_apply",
                        overlay.get("scope_for_apply"),
                    )
                ),
                "risk_flags": list(
                    _strict_unique_string_tuple(
                        f"task_overlays[{task_id!r}].risk_flags",
                        overlay.get("risk_flags"),
                    )
                ),
                "portfolio_id": portfolio_id,
                "portfolio_path": portfolio_path,
                "portfolio_hash": portfolio_hash,
                "project_interface_contract_ref": interface_ref,
                "project_interface_contract_hash": interface_hash,
                "project_completion_contract_ref": completion_ref,
                "project_completion_contract_hash": completion_hash,
                "reference": expected_reference,
                "llm_blueprint_consumed": self.llm_blueprint_consumed,
                "usage_mode": self.usage_mode,
                "authority": "advisory_only",
                "handoff_ready": False,
                "execution_authorized": False,
            }

            advisory = self.scope_advisory[task_id]
            if not isinstance(advisory, Mapping):
                raise TypeError(f"scope_advisory[{task_id!r}] must be a mapping")
            scope_advisory[task_id] = _json_safe_mapping(advisory)

        if self.authority != "advisory_only":
            raise ValueError("authority must be 'advisory_only'")
        if self.immutable is not True:
            raise ValueError("immutable portfolios cannot be disabled")
        if self.handoff_ready is not False:
            raise ValueError("portfolio cannot declare handoff readiness")
        if self.execution_authorized is not False:
            raise ValueError("portfolio cannot authorize execution")

        object.__setattr__(self, "portfolio_id", portfolio_id)
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        object.__setattr__(self, "portfolio_path", portfolio_path)
        object.__setattr__(self, "portfolio_hash", portfolio_hash)
        object.__setattr__(self, "task_ids", task_ids)
        object.__setattr__(self, "task_overlays", task_overlays)
        object.__setattr__(self, "scope_advisory", scope_advisory)
        object.__setattr__(self, "project_interface_contract_ref", interface_ref)
        object.__setattr__(self, "project_interface_contract_hash", interface_hash)
        object.__setattr__(self, "project_completion_contract_ref", completion_ref)
        object.__setattr__(self, "project_completion_contract_hash", completion_hash)
        object.__setattr__(self, "risk_flags", _strict_unique_string_tuple("risk_flags", self.risk_flags))

    def to_reference(self) -> dict[str, Any]:
        """Return the durable identity needed by downstream projections."""

        return {
            "schema_version": "chief_engineer.blueprint_portfolio.reference.v1",
            "portfolio_id": self.portfolio_id,
            "portfolio_path": self.portfolio_path,
            "portfolio_hash": self.portfolio_hash,
            "project_interface_contract_ref": self.project_interface_contract_ref,
            "project_interface_contract_hash": self.project_interface_contract_hash,
            "project_completion_contract_ref": self.project_completion_contract_ref,
            "project_completion_contract_hash": self.project_completion_contract_hash,
        }

    @property
    def reference(self) -> dict[str, Any]:
        """Return a fresh immutable-portfolio reference mapping."""

        return self.to_reference()

    def to_task_blueprint_context(self) -> dict[str, Any]:
        """Return canonical evidence fields for ``generate_task_blueprint`` context."""

        return {
            "blueprint_portfolio_ref": self.portfolio_path,
            "blueprint_portfolio_hash": self.portfolio_hash,
            "project_interface_contract_ref": self.project_interface_contract_ref,
            "project_interface_contract_hash": self.project_interface_contract_hash,
            "project_interface_contract": self.project_interface_contract.to_dict(),
            "project_completion_contract_ref": self.project_completion_contract_ref,
            "project_completion_contract_hash": self.project_completion_contract_hash,
            "project_completion_contract": (
                self.project_completion_contract.to_dict() if self.project_completion_contract is not None else None
            ),
        }

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical JSON payload persisted by the public service."""

        return {
            "schema_version": "chief_engineer.blueprint_portfolio.v1",
            "kind": "chief_engineer_blueprint_portfolio",
            "portfolio_id": self.portfolio_id,
            "portfolio_path": self.portfolio_path,
            "portfolio_hash": self.portfolio_hash,
            "workspace": self.workspace,
            "run_id": self.run_id,
            "task_ids": list(self.task_ids),
            "task_overlays": {task_id: _json_safe_mapping(self.task_overlays[task_id]) for task_id in self.task_ids},
            "scope_advisory": {task_id: _json_safe_mapping(self.scope_advisory[task_id]) for task_id in self.task_ids},
            "project_interface_contract": self.project_interface_contract.to_dict(),
            "project_interface_contract_ref": self.project_interface_contract_ref,
            "project_interface_contract_hash": self.project_interface_contract_hash,
            "project_completion_contract": (
                self.project_completion_contract.to_dict() if self.project_completion_contract is not None else None
            ),
            "project_completion_contract_ref": self.project_completion_contract_ref,
            "project_completion_contract_hash": self.project_completion_contract_hash,
            "risk_flags": list(self.risk_flags),
            "llm_blueprint_consumed": self.llm_blueprint_consumed,
            "usage_mode": self.usage_mode,
            "authority": self.authority,
            "authoritative": False,
            "mainline_authority": False,
            "handoff_ready": self.handoff_ready,
            "execution_authorized": self.execution_authorized,
            "contract_authority": "pm_authoritative_tasks",
            "immutable": self.immutable,
            "reference": self.to_reference(),
        }


@dataclass(frozen=True)
class GetBlueprintStatusQueryV1:
    task_id: str
    workspace: str
    run_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))


@dataclass(frozen=True)
class QueryBlueprintProvenanceV1:
    """Validate one already-captured blueprint mapping without storage access."""

    blueprint: Mapping[str, Any]
    expected_pm_task: Mapping[str, Any]
    expected_factory_run_id: str
    expected_task_id: str
    expected_blueprint_id: str
    expected_logical_path: str

    def __post_init__(self) -> None:
        if not isinstance(self.blueprint, Mapping):
            raise TypeError("blueprint must be a mapping")
        if not isinstance(self.expected_pm_task, Mapping) or not self.expected_pm_task:
            raise ValueError("expected_pm_task must be a non-empty mapping")
        object.__setattr__(self, "blueprint", deepcopy(dict(self.blueprint)))
        object.__setattr__(self, "expected_pm_task", deepcopy(dict(self.expected_pm_task)))
        object.__setattr__(
            self,
            "expected_factory_run_id",
            _require_provenance_identity("expected_factory_run_id", self.expected_factory_run_id),
        )
        object.__setattr__(
            self,
            "expected_task_id",
            _require_provenance_identity("expected_task_id", self.expected_task_id),
        )
        blueprint_id = _require_provenance_blueprint_id("expected_blueprint_id", self.expected_blueprint_id)
        object.__setattr__(self, "expected_blueprint_id", blueprint_id)
        object.__setattr__(
            self,
            "expected_logical_path",
            _require_provenance_path("expected_logical_path", self.expected_logical_path),
        )


@dataclass(frozen=True)
class TaskBlueprintGeneratedEventV1:
    event_id: str
    task_id: str
    workspace: str
    blueprint_path: str
    generated_at: str
    risk_level: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "blueprint_path", _require_non_empty("blueprint_path", self.blueprint_path))
        object.__setattr__(self, "generated_at", _require_non_empty("generated_at", self.generated_at))


@dataclass(frozen=True)
class ArchitectureDecisionV1:
    """Structured Chief Engineer architecture or dependency decision."""

    concern: str
    decision: str
    selected_libraries: tuple[str, ...] = field(default_factory=tuple)
    options_considered: tuple[str, ...] = field(default_factory=tuple)
    rationale: str = ""
    constraints: tuple[str, ...] = field(default_factory=tuple)
    risk_level: str = "medium"
    evidence: Mapping[str, Any] = field(default_factory=dict)
    decision_status: str = "decision"
    source: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "concern", _require_non_empty("concern", self.concern))
        object.__setattr__(self, "decision", _require_non_empty("decision", self.decision))
        object.__setattr__(self, "selected_libraries", _string_tuple(self.selected_libraries))
        object.__setattr__(self, "options_considered", _string_tuple(self.options_considered))
        object.__setattr__(self, "rationale", str(self.rationale or "").strip())
        object.__setattr__(self, "constraints", _string_tuple(self.constraints))
        risk_level = str(self.risk_level or "medium").strip().lower() or "medium"
        object.__setattr__(self, "risk_level", risk_level)
        object.__setattr__(self, "evidence", _json_safe_mapping(self.evidence))
        decision_status = str(self.decision_status or "decision").strip().lower() or "decision"
        object.__setattr__(self, "decision_status", decision_status)
        object.__setattr__(self, "source", str(self.source or "").strip())

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> ArchitectureDecisionV1:
        """Build a decision contract from a persisted JSON payload."""

        evidence = _json_safe_mapping(payload.get("evidence") if isinstance(payload.get("evidence"), Mapping) else {})
        decision_status = str(
            payload.get("decision_status")
            or payload.get("status")
            or ("guidance" if evidence.get("guidance_only") is True else "decision")
        ).strip()
        return cls(
            concern=str(payload.get("concern") or payload.get("area") or "").strip(),
            decision=str(payload.get("decision") or payload.get("selected") or "").strip(),
            selected_libraries=_string_tuple(
                payload.get("selected_libraries") or payload.get("libraries") or payload.get("technologies")
            ),
            options_considered=_string_tuple(
                payload.get("options_considered") or payload.get("options") or payload.get("alternatives")
            ),
            rationale=str(payload.get("rationale") or payload.get("reason") or "").strip(),
            constraints=_string_tuple(payload.get("constraints")),
            risk_level=str(payload.get("risk_level") or "medium").strip(),
            evidence=evidence,
            decision_status=decision_status,
            source=str(payload.get("source") or "").strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe architecture decision payload."""

        return {
            "schema_version": "chief_engineer.architecture_decision.v1",
            "concern": self.concern,
            "decision": self.decision,
            "selected_libraries": list(self.selected_libraries),
            "options_considered": list(self.options_considered),
            "rationale": self.rationale,
            "constraints": list(self.constraints),
            "risk_level": self.risk_level,
            "evidence": _json_safe_mapping(self.evidence),
            "decision_status": self.decision_status,
            "source": self.source,
        }


@dataclass(frozen=True)
class TaskBlueprintResultV1:
    ok: bool
    task_id: str
    workspace: str
    status: str
    blueprint_id: str | None = None
    blueprint_path: str | None = None
    blueprint_hash: str = ""
    summary: str = ""
    recommendations: tuple[str, ...] = field(default_factory=tuple)
    risks: tuple[str, ...] = field(default_factory=tuple)
    # D-05: Rich blueprint fields for Director context injection
    target_files: tuple[str, ...] = field(default_factory=tuple)
    acceptance_criteria: tuple[str, ...] = field(default_factory=tuple)
    execution_checklist: tuple[str, ...] = field(default_factory=tuple)
    scope_paths: tuple[str, ...] = field(default_factory=tuple)
    objective: str = ""
    dependencies: tuple[str, ...] = field(default_factory=tuple)
    architecture_decisions: tuple[ArchitectureDecisionV1, ...] = field(default_factory=tuple)
    selected_libraries: tuple[str, ...] = field(default_factory=tuple)
    # Existing target file export summaries so downstream Director tasks
    # (e.g. test generation) know the actual API of files created by earlier tasks.
    existing_target_files: tuple[dict[str, str], ...] = field(default_factory=tuple)
    module_interface_contract: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        if self.blueprint_id is not None:
            object.__setattr__(self, "blueprint_id", str(self.blueprint_id).strip() or None)
        object.__setattr__(self, "blueprint_hash", str(self.blueprint_hash or "").strip())
        object.__setattr__(self, "recommendations", tuple(str(v) for v in self.recommendations))
        object.__setattr__(self, "risks", tuple(str(v) for v in self.risks))
        object.__setattr__(self, "target_files", tuple(str(v) for v in self.target_files))
        object.__setattr__(self, "acceptance_criteria", tuple(str(v) for v in self.acceptance_criteria))
        object.__setattr__(self, "execution_checklist", tuple(str(v) for v in self.execution_checklist))
        object.__setattr__(self, "scope_paths", tuple(str(v) for v in self.scope_paths))
        object.__setattr__(self, "objective", str(self.objective or "").strip())
        object.__setattr__(self, "dependencies", tuple(str(v) for v in self.dependencies))
        decisions: list[ArchitectureDecisionV1] = []
        for item in self.architecture_decisions:
            if isinstance(item, ArchitectureDecisionV1):
                decisions.append(item)
            elif isinstance(item, Mapping):
                decisions.append(ArchitectureDecisionV1.from_mapping(item))
        object.__setattr__(self, "architecture_decisions", tuple(decisions))
        object.__setattr__(self, "selected_libraries", _string_tuple(self.selected_libraries))
        object.__setattr__(
            self,
            "existing_target_files",
            tuple(dict(item) for item in self.existing_target_files if isinstance(item, Mapping)),
        )
        object.__setattr__(self, "module_interface_contract", _to_dict_copy(self.module_interface_contract))


@dataclass(frozen=True)
class TaskBlueprintProvenanceSnapshotV1:
    """Pure provenance verdict for one exact Chief Engineer blueprint payload."""

    logical_path: str
    factory_run_id: str
    task_id: str
    blueprint_id: str
    embedded_blueprint_hash: str
    recomputed_blueprint_hash: str
    matches: bool
    pm_contract_hash: str
    recomputed_pm_contract_hash: str
    pm_task_canonical_hash: str
    target_files: tuple[str, ...] = field(default_factory=tuple)
    blueprint_schema_version: str = "chief_engineer.blueprint.v1"
    schema_version: str = "chief_engineer.blueprint_provenance.v1"
    hash_scheme: str = "chief_engineer.blueprint_hash.v1"

    def __post_init__(self) -> None:
        logical_path = _require_provenance_path("logical_path", self.logical_path)
        factory_run_id = _require_provenance_identity("factory_run_id", self.factory_run_id)
        task_id = _require_provenance_identity("task_id", self.task_id)
        blueprint_id = _require_provenance_blueprint_id("blueprint_id", self.blueprint_id)
        if logical_path != f"runtime/blueprints/{blueprint_id}.json":
            raise ValueError("logical_path must match blueprint_id")
        object.__setattr__(self, "logical_path", logical_path)
        object.__setattr__(self, "factory_run_id", factory_run_id)
        object.__setattr__(self, "task_id", task_id)
        object.__setattr__(self, "blueprint_id", blueprint_id)
        for field_name in (
            "embedded_blueprint_hash",
            "recomputed_blueprint_hash",
            "pm_contract_hash",
            "recomputed_pm_contract_hash",
            "pm_task_canonical_hash",
        ):
            object.__setattr__(self, field_name, _require_provenance_sha256(field_name, getattr(self, field_name)))
        if type(self.matches) is not bool:
            raise TypeError("matches must be a bool")
        if self.blueprint_schema_version != _PROVENANCE_BLUEPRINT_SCHEMA:
            raise ValueError(f"blueprint_schema_version must equal {_PROVENANCE_BLUEPRINT_SCHEMA!r}")
        if self.schema_version != _PROVENANCE_SNAPSHOT_SCHEMA:
            raise ValueError(f"schema_version must equal {_PROVENANCE_SNAPSHOT_SCHEMA!r}")
        if self.hash_scheme != _PROVENANCE_HASH_SCHEME:
            raise ValueError(f"hash_scheme must equal {_PROVENANCE_HASH_SCHEME!r}")
        object.__setattr__(
            self,
            "target_files",
            _strict_provenance_target_paths("target_files", self.target_files, require_list=False),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "blueprint_schema_version": self.blueprint_schema_version,
            "hash_scheme": self.hash_scheme,
            "logical_path": self.logical_path,
            "factory_run_id": self.factory_run_id,
            "task_id": self.task_id,
            "blueprint_id": self.blueprint_id,
            "embedded_blueprint_hash": self.embedded_blueprint_hash,
            "recomputed_blueprint_hash": self.recomputed_blueprint_hash,
            "matches": self.matches,
            "pm_contract_hash": self.pm_contract_hash,
            "recomputed_pm_contract_hash": self.recomputed_pm_contract_hash,
            "pm_task_canonical_hash": self.pm_task_canonical_hash,
            "target_files": list(self.target_files),
        }


class ChiefEngineerBlueprintErrorV1(RuntimeError):  # noqa: N818
    """Raised when `chief_engineer.blueprint` contract processing fails."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "chief_engineer_blueprint_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(_require_non_empty("message", message))
        self.code = _require_non_empty("code", code)
        self.details = _to_dict_copy(details)


# Public v1 alias retained for import stability; external consumers may still use the unversioned name.
ChiefEngineerBlueprintError = ChiefEngineerBlueprintErrorV1


# ---------------------------------------------------------------------------
# Risk Register contracts (Tier-1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RiskRecordV1:
    """A single Risk Register entry.

    Attributes:
        risk_id: Unique risk identifier (e.g. ``risk-{task_id}-{nonce}``).
        task_id: Owning PM task id; ``"workspace"`` for cross-task risks.
        title: Human-readable risk title (caller-supplied, must not be
            project-specific code — Polaris §8).
        severity: ``RiskSeverity`` member.
        owner: Role or person accountable for mitigation.
        mitigation: Short description of the planned mitigation.
        status: ``RiskStatus`` member.
        detected_at: ISO-8601 timestamp (UTC).
        links: Free-form references (paths, ADR ids, ticket ids).
        supersedes: Optional prior risk id this one replaces.
        history: Append-only status change log; never shrinks.
    """

    risk_id: str
    task_id: str
    title: str
    severity: RiskSeverity
    owner: str
    mitigation: str
    status: RiskStatus
    detected_at: str
    links: tuple[str, ...] = field(default_factory=tuple)
    supersedes: str | None = None
    history: tuple[dict[str, str], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "risk_id", _require_non_empty("risk_id", self.risk_id))
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "title", _require_non_empty("title", self.title))
        object.__setattr__(self, "owner", _require_non_empty("owner", self.owner))
        object.__setattr__(self, "mitigation", str(self.mitigation or "").strip())
        object.__setattr__(self, "detected_at", _require_non_empty("detected_at", self.detected_at))
        if not isinstance(self.severity, RiskSeverity):
            object.__setattr__(self, "severity", RiskSeverity(str(self.severity).strip().lower()))
        if not isinstance(self.status, RiskStatus):
            object.__setattr__(self, "status", RiskStatus(str(self.status).strip().lower()))
        object.__setattr__(self, "links", tuple(str(v) for v in self.links))
        object.__setattr__(
            self,
            "history",
            tuple(dict(item) for item in self.history if isinstance(item, Mapping)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_id": self.risk_id,
            "task_id": self.task_id,
            "title": self.title,
            "severity": self.severity.value,
            "owner": self.owner,
            "mitigation": self.mitigation,
            "status": self.status.value,
            "detected_at": self.detected_at,
            "links": list(self.links),
            "supersedes": self.supersedes,
            "history": [dict(item) for item in self.history],
        }


@dataclass(frozen=True)
class RegisterRiskCommandV1:
    """Register a new risk in the workspace Risk Register."""

    task_id: str
    title: str
    severity: RiskSeverity
    owner: str
    mitigation: str
    workspace: str
    links: tuple[str, ...] = field(default_factory=tuple)
    supersedes: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "title", _require_non_empty("title", self.title))
        object.__setattr__(self, "owner", _require_non_empty("owner", self.owner))
        object.__setattr__(self, "mitigation", str(self.mitigation or "").strip())
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        if not isinstance(self.severity, RiskSeverity):
            object.__setattr__(self, "severity", RiskSeverity(str(self.severity).strip().lower()))
        object.__setattr__(self, "links", tuple(str(v) for v in self.links))


@dataclass(frozen=True)
class ListRisksQueryV1:
    """Filter Risk Register entries."""

    workspace: str
    task_id: str | None = None
    severity: RiskSeverity | None = None
    status: RiskStatus | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        if self.task_id is not None:
            object.__setattr__(self, "task_id", str(self.task_id).strip() or None)
        if self.severity is not None and not isinstance(self.severity, RiskSeverity):
            object.__setattr__(self, "severity", RiskSeverity(str(self.severity).strip().lower()))
        if self.status is not None and not isinstance(self.status, RiskStatus):
            object.__setattr__(self, "status", RiskStatus(str(self.status).strip().lower()))


@dataclass(frozen=True)
class UpdateRiskStatusCommandV1:
    """Transition a risk to a new status."""

    workspace: str
    risk_id: str
    status: RiskStatus
    note: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "risk_id", _require_non_empty("risk_id", self.risk_id))
        object.__setattr__(self, "note", str(self.note or "").strip())
        if not isinstance(self.status, RiskStatus):
            object.__setattr__(self, "status", RiskStatus(str(self.status).strip().lower()))


@dataclass(frozen=True)
class RiskEventV1:
    """Audit event emitted on risk state change."""

    event_id: str
    risk_id: str
    workspace: str
    action: str
    actor: str
    at: str
    note: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "risk_id", _require_non_empty("risk_id", self.risk_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "action", _require_non_empty("action", self.action))
        object.__setattr__(self, "actor", _require_non_empty("actor", self.actor))
        object.__setattr__(self, "at", _require_non_empty("at", self.at))
        object.__setattr__(self, "note", str(self.note or "").strip())


# ---------------------------------------------------------------------------
# Tech-Debt Ledger contracts (Tier-1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TechDebtRecordV1:
    """A single Technical-Debt Ledger entry."""

    debt_id: str
    title: str
    description: str
    severity: TechDebtSeverity
    surface: str
    owner: str
    evidence: tuple[str, ...]
    status: TechDebtStatus
    registered_at: str
    history: tuple[dict[str, str], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "debt_id", _require_non_empty("debt_id", self.debt_id))
        object.__setattr__(self, "title", _require_non_empty("title", self.title))
        object.__setattr__(self, "description", str(self.description or "").strip())
        object.__setattr__(self, "surface", _require_non_empty("surface", self.surface))
        object.__setattr__(self, "owner", _require_non_empty("owner", self.owner))
        object.__setattr__(self, "evidence", tuple(str(v) for v in self.evidence))
        object.__setattr__(self, "registered_at", _require_non_empty("registered_at", self.registered_at))
        if not isinstance(self.severity, TechDebtSeverity):
            object.__setattr__(self, "severity", TechDebtSeverity(str(self.severity).strip().lower()))
        if not isinstance(self.status, TechDebtStatus):
            object.__setattr__(self, "status", TechDebtStatus(str(self.status).strip().lower()))
        object.__setattr__(
            self,
            "history",
            tuple(dict(item) for item in self.history if isinstance(item, Mapping)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "debt_id": self.debt_id,
            "title": self.title,
            "description": self.description,
            "severity": self.severity.value,
            "surface": self.surface,
            "owner": self.owner,
            "evidence": list(self.evidence),
            "status": self.status.value,
            "registered_at": self.registered_at,
            "history": [dict(item) for item in self.history],
        }


@dataclass(frozen=True)
class RegisterTechDebtCommandV1:
    """Register a new tech-debt entry."""

    title: str
    description: str
    severity: TechDebtSeverity
    surface: str
    owner: str
    workspace: str
    evidence: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "title", _require_non_empty("title", self.title))
        object.__setattr__(self, "description", str(self.description or "").strip())
        object.__setattr__(self, "surface", _require_non_empty("surface", self.surface))
        object.__setattr__(self, "owner", _require_non_empty("owner", self.owner))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "evidence", tuple(str(v) for v in self.evidence))
        if not isinstance(self.severity, TechDebtSeverity):
            object.__setattr__(self, "severity", TechDebtSeverity(str(self.severity).strip().lower()))


@dataclass(frozen=True)
class ListTechDebtQueryV1:
    """Filter Tech-Debt Ledger entries."""

    workspace: str
    severity: TechDebtSeverity | None = None
    surface: str | None = None
    status: TechDebtStatus | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        if self.severity is not None and not isinstance(self.severity, TechDebtSeverity):
            object.__setattr__(self, "severity", TechDebtSeverity(str(self.severity).strip().lower()))
        if self.status is not None and not isinstance(self.status, TechDebtStatus):
            object.__setattr__(self, "status", TechDebtStatus(str(self.status).strip().lower()))
        if self.surface is not None:
            object.__setattr__(self, "surface", str(self.surface).strip() or None)


@dataclass(frozen=True)
class UpdateTechDebtStatusCommandV1:
    """Transition a tech-debt entry to a new status."""

    workspace: str
    debt_id: str
    status: TechDebtStatus
    note: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "debt_id", _require_non_empty("debt_id", self.debt_id))
        object.__setattr__(self, "note", str(self.note or "").strip())
        if not isinstance(self.status, TechDebtStatus):
            object.__setattr__(self, "status", TechDebtStatus(str(self.status).strip().lower()))


@dataclass(frozen=True)
class TechDebtEventV1:
    """Audit event emitted on tech-debt state change."""

    event_id: str
    debt_id: str
    workspace: str
    action: str
    actor: str
    at: str
    note: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "debt_id", _require_non_empty("debt_id", self.debt_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "action", _require_non_empty("action", self.action))
        object.__setattr__(self, "actor", _require_non_empty("actor", self.actor))
        object.__setattr__(self, "at", _require_non_empty("at", self.at))
        object.__setattr__(self, "note", str(self.note or "").strip())


# ---------------------------------------------------------------------------
# Quality Gate and Rollback Link (Tier-1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QualityGateResultV1:
    """Structured quality-gate result for a blueprint.

    Attributes:
        passed: ``True`` iff ``blocker_count == 0``.
        blocker_count: Number of blocking issues.
        warning_count: Number of warnings (non-blocking).
        info_count: Number of informational notes.
        blockers: Blocking issues (must be resolved before handoff).
        warnings: Warnings (advisory).
        info: Informational notes.
        evaluated_at: ISO-8601 timestamp (UTC).
    """

    passed: bool
    blocker_count: int
    warning_count: int
    info_count: int
    blockers: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    info: tuple[str, ...] = field(default_factory=tuple)
    evaluated_at: str = ""

    def __post_init__(self) -> None:
        for field_name in ("blocker_count", "warning_count", "info_count"):
            value = getattr(self, field_name)
            if value < 0:
                raise ValueError(f"{field_name} must be >= 0; got {value}")
        object.__setattr__(self, "blockers", tuple(str(v) for v in self.blockers))
        object.__setattr__(self, "warnings", tuple(str(v) for v in self.warnings))
        object.__setattr__(self, "info", tuple(str(v) for v in self.info))
        object.__setattr__(self, "passed", bool(self.blocker_count == 0))

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "blocker_count": self.blocker_count,
            "warning_count": self.warning_count,
            "info_count": self.info_count,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
            "info": list(self.info),
            "evaluated_at": self.evaluated_at,
        }


@dataclass(frozen=True)
class RollbackLinkV1:
    """Rollback linkage attached to a blueprint.

    Attributes:
        enabled: Whether rollback is provisioned for this blueprint.
        strategy: ``RollbackStrategy`` member.
        marker_path: Path to the stash / snapshot / manifest.
        preconditions: Safe-state checks that CURRENTLY HOLD for this rollback.
            Each is listed only when satisfied (e.g. ``"no_blocker_risks_open"``
            appears only when no open blocker/critical risk exists); a check's
            ABSENCE means it is not yet satisfied — a gate still to clear.
    """

    enabled: bool
    strategy: RollbackStrategy
    marker_path: str
    preconditions: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "enabled", bool(self.enabled))
        if not isinstance(self.strategy, RollbackStrategy):
            object.__setattr__(
                self,
                "strategy",
                RollbackStrategy(str(self.strategy).strip().lower()),
            )
        object.__setattr__(self, "marker_path", _require_non_empty("marker_path", self.marker_path))
        object.__setattr__(self, "preconditions", tuple(str(v) for v in self.preconditions))

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "strategy": self.strategy.value,
            "marker_path": self.marker_path,
            "preconditions": list(self.preconditions),
        }


# ---------------------------------------------------------------------------
# Architecture Decision Log contracts (Tier-2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ADRRecordV1:
    """A single Architecture Decision Record (human-facing decision log).

    Distinct from the internal construction-plan ADR compiler
    (``adr_store.py``): this records *why* a technical decision was made,
    in the canonical ADR shape (context / decision / consequences /
    alternatives), for a real 技术总监's decision ownership.

    Attributes:
        adr_id: Unique decision id (e.g. ``adr_{slug}_{nonce}``).
        title: Short decision title (caller-supplied — Polaris §8).
        status: ``ADRStatus`` member.
        context: The forces / problem that motivated the decision.
        decision: The decision that was made.
        consequences: Resulting trade-offs (positive and negative).
        alternatives: Options that were considered and rejected.
        related_task_ids: Tasks / blueprints this decision governs.
        owner: Role or person accountable for the decision.
        decided_at: ISO-8601 timestamp (UTC).
        supersedes: Optional prior ADR id this one replaces.
        history: Append-only status change log; never shrinks.
    """

    adr_id: str
    title: str
    status: ADRStatus
    context: str
    decision: str
    consequences: str
    owner: str
    decided_at: str
    alternatives: tuple[str, ...] = field(default_factory=tuple)
    related_task_ids: tuple[str, ...] = field(default_factory=tuple)
    supersedes: str | None = None
    history: tuple[dict[str, str], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "adr_id", _require_non_empty("adr_id", self.adr_id))
        object.__setattr__(self, "title", _require_non_empty("title", self.title))
        object.__setattr__(self, "context", str(self.context or "").strip())
        object.__setattr__(self, "decision", _require_non_empty("decision", self.decision))
        object.__setattr__(self, "consequences", str(self.consequences or "").strip())
        object.__setattr__(self, "owner", _require_non_empty("owner", self.owner))
        object.__setattr__(self, "decided_at", _require_non_empty("decided_at", self.decided_at))
        if not isinstance(self.status, ADRStatus):
            object.__setattr__(self, "status", ADRStatus(str(self.status).strip().lower()))
        object.__setattr__(self, "alternatives", tuple(str(v) for v in self.alternatives))
        object.__setattr__(self, "related_task_ids", tuple(str(v) for v in self.related_task_ids))
        object.__setattr__(
            self,
            "history",
            tuple(dict(item) for item in self.history if isinstance(item, Mapping)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "adr_id": self.adr_id,
            "title": self.title,
            "status": self.status.value,
            "context": self.context,
            "decision": self.decision,
            "consequences": self.consequences,
            "owner": self.owner,
            "decided_at": self.decided_at,
            "alternatives": list(self.alternatives),
            "related_task_ids": list(self.related_task_ids),
            "supersedes": self.supersedes,
            "history": [dict(item) for item in self.history],
        }


@dataclass(frozen=True)
class RegisterADRCommandV1:
    """Record a new Architecture Decision Record."""

    title: str
    decision: str
    owner: str
    workspace: str
    context: str = ""
    consequences: str = ""
    alternatives: tuple[str, ...] = field(default_factory=tuple)
    related_task_ids: tuple[str, ...] = field(default_factory=tuple)
    supersedes: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "title", _require_non_empty("title", self.title))
        object.__setattr__(self, "decision", _require_non_empty("decision", self.decision))
        object.__setattr__(self, "owner", _require_non_empty("owner", self.owner))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "context", str(self.context or "").strip())
        object.__setattr__(self, "consequences", str(self.consequences or "").strip())
        object.__setattr__(self, "alternatives", tuple(str(v) for v in self.alternatives))
        object.__setattr__(self, "related_task_ids", tuple(str(v) for v in self.related_task_ids))


@dataclass(frozen=True)
class ListADRsQueryV1:
    """Filter Architecture Decision Records."""

    workspace: str
    status: ADRStatus | None = None
    task_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        if self.status is not None and not isinstance(self.status, ADRStatus):
            object.__setattr__(self, "status", ADRStatus(str(self.status).strip().lower()))
        if self.task_id is not None:
            object.__setattr__(self, "task_id", str(self.task_id).strip() or None)


@dataclass(frozen=True)
class UpdateADRStatusCommandV1:
    """Transition an ADR to a new status."""

    workspace: str
    adr_id: str
    status: ADRStatus
    note: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "adr_id", _require_non_empty("adr_id", self.adr_id))
        object.__setattr__(self, "note", str(self.note or "").strip())
        if not isinstance(self.status, ADRStatus):
            object.__setattr__(self, "status", ADRStatus(str(self.status).strip().lower()))


@dataclass(frozen=True)
class ADREventV1:
    """Audit event emitted on ADR state change."""

    event_id: str
    adr_id: str
    workspace: str
    action: str
    actor: str
    at: str
    note: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "adr_id", _require_non_empty("adr_id", self.adr_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "action", _require_non_empty("action", self.action))
        object.__setattr__(self, "actor", _require_non_empty("actor", self.actor))
        object.__setattr__(self, "at", _require_non_empty("at", self.at))
        object.__setattr__(self, "note", str(self.note or "").strip())


# ---------------------------------------------------------------------------
# Tech Radar contracts (Tier-2 stack/library policy)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TechRadarEntryV1:
    """A single Tech-Radar entry (a library/technology placed on a ring)."""

    entry_id: str
    library: str
    ring: TechRadarRing
    rationale: str
    owner: str
    decided_at: str
    supersedes: str | None = None
    history: tuple[dict[str, str], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "entry_id", _require_non_empty("entry_id", self.entry_id))
        object.__setattr__(self, "library", _require_non_empty("library", self.library))
        object.__setattr__(self, "rationale", str(self.rationale or "").strip())
        object.__setattr__(self, "owner", _require_non_empty("owner", self.owner))
        object.__setattr__(self, "decided_at", _require_non_empty("decided_at", self.decided_at))
        if not isinstance(self.ring, TechRadarRing):
            object.__setattr__(self, "ring", TechRadarRing(str(self.ring).strip().lower()))
        object.__setattr__(
            self,
            "history",
            tuple(dict(item) for item in self.history if isinstance(item, Mapping)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "library": self.library,
            "ring": self.ring.value,
            "rationale": self.rationale,
            "owner": self.owner,
            "decided_at": self.decided_at,
            "supersedes": self.supersedes,
            "history": [dict(item) for item in self.history],
        }


@dataclass(frozen=True)
class RegisterTechRadarCommandV1:
    """Place a library on a Tech-Radar ring."""

    library: str
    ring: TechRadarRing
    owner: str
    workspace: str
    rationale: str = ""
    supersedes: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "library", _require_non_empty("library", self.library))
        object.__setattr__(self, "owner", _require_non_empty("owner", self.owner))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "rationale", str(self.rationale or "").strip())
        if not isinstance(self.ring, TechRadarRing):
            object.__setattr__(self, "ring", TechRadarRing(str(self.ring).strip().lower()))


@dataclass(frozen=True)
class ListTechRadarQueryV1:
    """Filter Tech-Radar entries."""

    workspace: str
    ring: TechRadarRing | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        if self.ring is not None and not isinstance(self.ring, TechRadarRing):
            object.__setattr__(self, "ring", TechRadarRing(str(self.ring).strip().lower()))


@dataclass(frozen=True)
class UpdateTechRadarRingCommandV1:
    """Move a Tech-Radar entry to a new ring."""

    workspace: str
    entry_id: str
    ring: TechRadarRing
    note: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "entry_id", _require_non_empty("entry_id", self.entry_id))
        object.__setattr__(self, "note", str(self.note or "").strip())
        if not isinstance(self.ring, TechRadarRing):
            object.__setattr__(self, "ring", TechRadarRing(str(self.ring).strip().lower()))


@dataclass(frozen=True)
class StackPolicyViolationV1:
    """A blueprint dependency that violates the Tech Radar (hold/deprecated)."""

    library: str
    ring: TechRadarRing
    rationale: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "library", _require_non_empty("library", self.library))
        object.__setattr__(self, "rationale", str(self.rationale or "").strip())
        if not isinstance(self.ring, TechRadarRing):
            object.__setattr__(self, "ring", TechRadarRing(str(self.ring).strip().lower()))

    def to_dict(self) -> dict[str, Any]:
        return {"library": self.library, "ring": self.ring.value, "rationale": self.rationale}


@dataclass(frozen=True)
class TechRadarEventV1:
    """Audit event emitted on Tech-Radar state change."""

    event_id: str
    entry_id: str
    workspace: str
    action: str
    actor: str
    at: str
    note: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "entry_id", _require_non_empty("entry_id", self.entry_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "action", _require_non_empty("action", self.action))
        object.__setattr__(self, "actor", _require_non_empty("actor", self.actor))
        object.__setattr__(self, "at", _require_non_empty("at", self.at))
        object.__setattr__(self, "note", str(self.note or "").strip())


# ---------------------------------------------------------------------------
# Post-Mortem / Incident Review contracts (Tier-2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PostMortemRecordV1:
    """A single post-mortem / incident review (blameless, learning-oriented)."""

    incident_id: str
    title: str
    severity: IncidentSeverity
    summary: str
    root_cause: str
    impact: str
    status: PostMortemStatus
    occurred_at: str
    owner: str
    recorded_at: str
    timeline: tuple[str, ...] = field(default_factory=tuple)
    action_items: tuple[str, ...] = field(default_factory=tuple)
    related_risk_ids: tuple[str, ...] = field(default_factory=tuple)
    history: tuple[dict[str, str], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "incident_id", _require_non_empty("incident_id", self.incident_id))
        object.__setattr__(self, "title", _require_non_empty("title", self.title))
        object.__setattr__(self, "summary", str(self.summary or "").strip())
        object.__setattr__(self, "root_cause", str(self.root_cause or "").strip())
        object.__setattr__(self, "impact", str(self.impact or "").strip())
        object.__setattr__(self, "occurred_at", _require_non_empty("occurred_at", self.occurred_at))
        object.__setattr__(self, "owner", _require_non_empty("owner", self.owner))
        object.__setattr__(self, "recorded_at", _require_non_empty("recorded_at", self.recorded_at))
        if not isinstance(self.severity, IncidentSeverity):
            object.__setattr__(self, "severity", IncidentSeverity(str(self.severity).strip().lower()))
        if not isinstance(self.status, PostMortemStatus):
            object.__setattr__(self, "status", PostMortemStatus(str(self.status).strip().lower()))
        object.__setattr__(self, "timeline", tuple(str(v) for v in self.timeline))
        object.__setattr__(self, "action_items", tuple(str(v) for v in self.action_items))
        object.__setattr__(self, "related_risk_ids", tuple(str(v) for v in self.related_risk_ids))
        object.__setattr__(
            self,
            "history",
            tuple(dict(item) for item in self.history if isinstance(item, Mapping)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "title": self.title,
            "severity": self.severity.value,
            "summary": self.summary,
            "root_cause": self.root_cause,
            "impact": self.impact,
            "status": self.status.value,
            "occurred_at": self.occurred_at,
            "owner": self.owner,
            "recorded_at": self.recorded_at,
            "timeline": list(self.timeline),
            "action_items": list(self.action_items),
            "related_risk_ids": list(self.related_risk_ids),
            "history": [dict(item) for item in self.history],
        }


@dataclass(frozen=True)
class RegisterPostMortemCommandV1:
    """Record a new post-mortem / incident review."""

    title: str
    severity: IncidentSeverity
    occurred_at: str
    owner: str
    workspace: str
    summary: str = ""
    root_cause: str = ""
    impact: str = ""
    timeline: tuple[str, ...] = field(default_factory=tuple)
    action_items: tuple[str, ...] = field(default_factory=tuple)
    related_risk_ids: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "title", _require_non_empty("title", self.title))
        object.__setattr__(self, "occurred_at", _require_non_empty("occurred_at", self.occurred_at))
        object.__setattr__(self, "owner", _require_non_empty("owner", self.owner))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "summary", str(self.summary or "").strip())
        object.__setattr__(self, "root_cause", str(self.root_cause or "").strip())
        object.__setattr__(self, "impact", str(self.impact or "").strip())
        if not isinstance(self.severity, IncidentSeverity):
            object.__setattr__(self, "severity", IncidentSeverity(str(self.severity).strip().lower()))
        object.__setattr__(self, "timeline", tuple(str(v) for v in self.timeline))
        object.__setattr__(self, "action_items", tuple(str(v) for v in self.action_items))
        object.__setattr__(self, "related_risk_ids", tuple(str(v) for v in self.related_risk_ids))


@dataclass(frozen=True)
class ListPostMortemsQueryV1:
    """Filter post-mortems."""

    workspace: str
    severity: IncidentSeverity | None = None
    status: PostMortemStatus | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        if self.severity is not None and not isinstance(self.severity, IncidentSeverity):
            object.__setattr__(self, "severity", IncidentSeverity(str(self.severity).strip().lower()))
        if self.status is not None and not isinstance(self.status, PostMortemStatus):
            object.__setattr__(self, "status", PostMortemStatus(str(self.status).strip().lower()))


@dataclass(frozen=True)
class UpdatePostMortemStatusCommandV1:
    """Transition a post-mortem to a new status."""

    workspace: str
    incident_id: str
    status: PostMortemStatus
    note: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "incident_id", _require_non_empty("incident_id", self.incident_id))
        object.__setattr__(self, "note", str(self.note or "").strip())
        if not isinstance(self.status, PostMortemStatus):
            object.__setattr__(self, "status", PostMortemStatus(str(self.status).strip().lower()))


@dataclass(frozen=True)
class PostMortemEventV1:
    """Audit event emitted on post-mortem state change."""

    event_id: str
    incident_id: str
    workspace: str
    action: str
    actor: str
    at: str
    note: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "incident_id", _require_non_empty("incident_id", self.incident_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "action", _require_non_empty("action", self.action))
        object.__setattr__(self, "actor", _require_non_empty("actor", self.actor))
        object.__setattr__(self, "at", _require_non_empty("at", self.at))
        object.__setattr__(self, "note", str(self.note or "").strip())


# ---------------------------------------------------------------------------
# Release Readiness / Change-Advisory contract (Tier-2 capstone)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReleaseReadinessV1:
    """Executive GO / NO-GO that aggregates the whole governance surface.

    A read-time synthesis (NOT a stored ledger) of the existing capabilities:
    open blocker/critical risks, per-blueprint quality-gate blockers, open
    sev1/sev2 incidents, stack-policy violations, and unpaid fatal/severe
    tech debt. ``decision`` is ``no_go`` if any hard blocker is present,
    ``conditional_go`` if only warnings, else ``go``.

    Attributes:
        decision: ``ReleaseDecision`` verdict.
        workspace: Assessed workspace.
        blocker_count: Number of release-blocking signals.
        warning_count: Number of advisory signals.
        blockers: Blocking signal messages (``"<source>: <detail>"``).
        warnings: Advisory signal messages.
        signals: Per-source structured counts (risk / quality_gate /
            post_mortem / stack_policy / tech_debt).
        assessed_at: ISO-8601 timestamp (UTC).
    """

    decision: ReleaseDecision
    workspace: str
    blocker_count: int
    warning_count: int
    blockers: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    signals: dict[str, Any] = field(default_factory=dict)
    assessed_at: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        for field_name in ("blocker_count", "warning_count"):
            value = getattr(self, field_name)
            if value < 0:
                raise ValueError(f"{field_name} must be >= 0; got {value}")
        if not isinstance(self.decision, ReleaseDecision):
            object.__setattr__(self, "decision", ReleaseDecision(str(self.decision).strip().lower()))
        object.__setattr__(self, "blockers", tuple(str(v) for v in self.blockers))
        object.__setattr__(self, "warnings", tuple(str(v) for v in self.warnings))
        if not isinstance(self.signals, dict):
            object.__setattr__(self, "signals", dict(self.signals or {}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "workspace": self.workspace,
            "blocker_count": self.blocker_count,
            "warning_count": self.warning_count,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
            "signals": dict(self.signals),
            "assessed_at": self.assessed_at,
        }


@dataclass(frozen=True)
class GovernanceSummaryV1:
    """Aggregate governance view attached to a blueprint.

    Attributes:
        blueprint_id: Owning blueprint.
        risk_summary: Counts and severities for related risks.
        tech_debt_summary: Counts and severities for related tech debt.
        quality_gate: Quality gate result.
        rollback: Rollback linkage.
    """

    blueprint_id: str
    risk_summary: dict[str, Any]
    tech_debt_summary: dict[str, Any]
    quality_gate: QualityGateResultV1
    rollback: RollbackLinkV1

    def __post_init__(self) -> None:
        object.__setattr__(self, "blueprint_id", _require_non_empty("blueprint_id", self.blueprint_id))
        if not isinstance(self.risk_summary, dict):
            object.__setattr__(self, "risk_summary", dict(self.risk_summary or {}))
        if not isinstance(self.tech_debt_summary, dict):
            object.__setattr__(self, "tech_debt_summary", dict(self.tech_debt_summary or {}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "blueprint_id": self.blueprint_id,
            "risk_summary": dict(self.risk_summary),
            "tech_debt_summary": dict(self.tech_debt_summary),
            "quality_gate": self.quality_gate.to_dict(),
            "rollback": self.rollback.to_dict(),
        }


@dataclass(frozen=True)
class HandoffDecisionV1:
    """Director-handoff gate decision for a blueprint.

    The enforcement primitive that closes the quality-gate loop: a real
    技术总监 blocks handoff to the Director when the blueprint carries
    blocking quality issues or open blocker/critical risks.

    Attributes:
        allowed: ``True`` iff the blueprint may be handed to the Director.
        blueprint_id: Owning blueprint id.
        task_id: Owning PM task id (best-effort).
        blocker_count: Number of blocking quality-gate issues.
        warning_count: Number of (non-blocking) quality-gate warnings.
        open_blocker_risk_count: Open risks of severity critical/blocker.
        blockers: The blocking issue messages (gate + risk-derived).
        reason: One-line human-readable decision rationale.
        evaluated_at: ISO-8601 timestamp (UTC).
    """

    allowed: bool
    blueprint_id: str
    blocker_count: int
    warning_count: int
    open_blocker_risk_count: int
    task_id: str = ""
    blockers: tuple[str, ...] = field(default_factory=tuple)
    reason: str = ""
    evaluated_at: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "blueprint_id", _require_non_empty("blueprint_id", self.blueprint_id))
        for field_name in ("blocker_count", "warning_count", "open_blocker_risk_count"):
            value = getattr(self, field_name)
            if value < 0:
                raise ValueError(f"{field_name} must be >= 0; got {value}")
        object.__setattr__(self, "blockers", tuple(str(v) for v in self.blockers))
        object.__setattr__(self, "allowed", bool(self.allowed))

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "blueprint_id": self.blueprint_id,
            "task_id": self.task_id,
            "blocker_count": self.blocker_count,
            "warning_count": self.warning_count,
            "open_blocker_risk_count": self.open_blocker_risk_count,
            "blockers": list(self.blockers),
            "reason": self.reason,
            "evaluated_at": self.evaluated_at,
        }


@dataclass(frozen=True)
class CeHandoffDecisionBindingsV1:
    """Immutable hash bindings for `ce_handoff_decision.v1`."""

    pm_contract_hash: str
    blueprint_hash: str
    execution_profile_hash: str
    pm_contract_ref: str = ""
    blueprint_ref: str = ""
    execution_profile_ref: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "pm_contract_hash",
            _require_non_empty("pm_contract_hash", self.pm_contract_hash),
        )
        object.__setattr__(
            self,
            "blueprint_hash",
            _require_non_empty("blueprint_hash", self.blueprint_hash),
        )
        object.__setattr__(
            self,
            "execution_profile_hash",
            _require_non_empty("execution_profile_hash", self.execution_profile_hash),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "pm_contract_ref": self.pm_contract_ref,
            "pm_contract_hash": self.pm_contract_hash,
            "blueprint_ref": self.blueprint_ref,
            "blueprint_hash": self.blueprint_hash,
            "execution_profile_ref": self.execution_profile_ref,
            "execution_profile_hash": self.execution_profile_hash,
        }


@dataclass(frozen=True)
class CeHandoffDecisionV1:
    """Schema-compatible Chief Engineer handoff authority object.

    This strict object complements the base `HandoffDecisionV1`. It binds
    the Director handoff verdict to PM contract, blueprint, and execution
    profile hashes so downstream execution can fail closed on stale or
    incomplete evidence.
    """

    decision_id: str
    task_id: str
    blueprint_id: str
    allowed: bool
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    evaluated_at: str
    evaluator: str
    policy_version: str
    bindings: CeHandoffDecisionBindingsV1
    decision_hash: str
    reason: str = ""
    risk_assessment: Mapping[str, Any] = field(default_factory=dict)
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)
    schema_version: str = "polaris.ce_handoff_decision.v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision_id", _require_non_empty("decision_id", self.decision_id))
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "blueprint_id", _require_non_empty("blueprint_id", self.blueprint_id))
        object.__setattr__(self, "allowed", bool(self.allowed))
        object.__setattr__(self, "blockers", _string_tuple(self.blockers))
        object.__setattr__(self, "warnings", _string_tuple(self.warnings))
        object.__setattr__(self, "evaluated_at", _require_non_empty("evaluated_at", self.evaluated_at))
        object.__setattr__(self, "evaluator", _require_non_empty("evaluator", self.evaluator))
        object.__setattr__(self, "policy_version", _require_non_empty("policy_version", self.policy_version))
        object.__setattr__(self, "decision_hash", _require_non_empty("decision_hash", self.decision_hash))
        object.__setattr__(self, "risk_assessment", _json_safe_mapping(self.risk_assessment))
        object.__setattr__(self, "evidence_refs", _string_tuple(self.evidence_refs))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "decision_id": self.decision_id,
            "task_id": self.task_id,
            "blueprint_id": self.blueprint_id,
            "allowed": self.allowed,
            "reason": self.reason,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
            "risk_assessment": dict(self.risk_assessment),
            "evaluated_at": self.evaluated_at,
            "evaluator": self.evaluator,
            "policy_version": self.policy_version,
            "bindings": self.bindings.to_dict(),
            "evidence_refs": list(self.evidence_refs),
            "decision_hash": self.decision_hash,
        }


ArtifactSemanticRoleV1 = Literal[
    "source",
    "manifest",
    "test",
    "entrypoint",
    "config",
    "docs",
    "assets",
]
ObligationApplicabilityV1 = Literal["required", "optional", "not_applicable"]
EntrypointKindV1 = Literal["cli", "web", "api", "library"]
VerificationModalityV1 = Literal["environment_prep", "build", "test", "lint", "entrypoint"]
ProjectKindV1 = Literal["application", "library"]

_ARTIFACT_SEMANTIC_ROLES = frozenset({"source", "manifest", "test", "entrypoint", "config", "docs", "assets"})
_OBLIGATION_APPLICABILITIES = frozenset({"required", "optional", "not_applicable"})
_ENTRYPOINT_KINDS = frozenset({"cli", "web", "api", "library"})
_VERIFICATION_MODALITIES = frozenset({"environment_prep", "build", "test", "lint", "entrypoint"})
_PROJECT_KINDS = frozenset({"application", "library"})


def _require_literal(name: str, value: str, allowed: frozenset[str]) -> str:
    token = _require_exact_non_empty(name, value)
    if token not in allowed:
        raise ValueError(f"{name} must be one of {sorted(allowed)}")
    return token


def _canonicalize_completion_id_tuple(
    name: str,
    values: object,
    *,
    require_items: bool = False,
    max_items: int | None = None,
) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        raise TypeError(f"{name} must be a list or tuple of strings")
    if max_items is not None and len(values) > max_items:
        raise ValueError(f"{name} must contain at most {max_items} items")
    normalized = tuple(_require_completion_token(f"{name}[{index}]", value) for index, value in enumerate(values))
    result = tuple(sorted(set(normalized)))
    if require_items and not result:
        raise ValueError(f"{name} must contain at least one item")
    return result


def _canonical_project_completion_contract_seed(
    *,
    schema_version: str,
    project_id: str,
    run_id: str,
    project_kind: str,
    project_kind_authority: ProjectKindAuthorityV1,
    pm_contract_hash: str,
    covered_task_ids: tuple[str, ...],
    obligations: Mapping[str, Any],
    completion_predicate_version: str,
    verifier_policy_hash: str,
    verifier_policy_snapshot_hash: str,
    verification_command_authority: tuple[VerificationCommandAuthorityV1, ...],
) -> dict[str, Any]:
    return {
        "schema_version": schema_version,
        "project_id": project_id,
        "run_id": run_id,
        "project_kind": project_kind,
        "project_kind_authority": project_kind_authority.to_dict(),
        "pm_contract_hash": pm_contract_hash,
        "covered_task_ids": list(covered_task_ids),
        "obligations": dict(obligations),
        "completion_predicate_version": completion_predicate_version,
        "verifier_policy_hash": verifier_policy_hash,
        "verifier_policy_snapshot_hash": verifier_policy_snapshot_hash,
        "verification_command_authority": [item.to_dict() for item in verification_command_authority],
    }


def _project_completion_contract_hash(seed: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(seed),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _project_completion_contract_id(contract_hash: str) -> str:
    return f"{_PROJECT_COMPLETION_CONTRACT_ID_PREFIX}{contract_hash[:24]}"


@dataclass(frozen=True)
class ArtifactObligationV1:
    """One required, optional, or explicitly inapplicable project artifact."""

    obligation_id: str
    path: str
    semantic_role: ArtifactSemanticRoleV1
    applicability: ObligationApplicabilityV1
    owner_task_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "obligation_id",
            _require_completion_token("obligation_id", self.obligation_id),
        )
        object.__setattr__(self, "path", _require_provenance_path("path", self.path))
        object.__setattr__(
            self,
            "semantic_role",
            _require_literal("semantic_role", self.semantic_role, _ARTIFACT_SEMANTIC_ROLES),
        )
        object.__setattr__(
            self,
            "applicability",
            _require_literal("applicability", self.applicability, _OBLIGATION_APPLICABILITIES),
        )
        owner_task_id = (
            _require_completion_token("owner_task_id", self.owner_task_id) if self.owner_task_id is not None else None
        )
        object.__setattr__(self, "owner_task_id", owner_task_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "obligation_id": self.obligation_id,
            "path": self.path,
            "semantic_role": self.semantic_role,
            "applicability": self.applicability,
            "owner_task_id": self.owner_task_id,
        }


@dataclass(frozen=True)
class EntrypointObligationV1:
    """One language-neutral executable or explicitly N/A entrypoint."""

    obligation_id: str
    kind: EntrypointKindV1
    applicability: ObligationApplicabilityV1
    owner_task_id: str | None = None
    source_path: str | None = None
    runtime_path: str | None = None
    command: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "obligation_id",
            _require_completion_token("obligation_id", self.obligation_id),
        )
        object.__setattr__(self, "kind", _require_literal("kind", self.kind, _ENTRYPOINT_KINDS))
        applicability = _require_literal(
            "applicability",
            self.applicability,
            _OBLIGATION_APPLICABILITIES,
        )
        object.__setattr__(self, "applicability", applicability)
        owner_task_id = (
            _require_completion_token("owner_task_id", self.owner_task_id) if self.owner_task_id is not None else None
        )
        source_path = (
            _require_provenance_path("source_path", self.source_path) if self.source_path is not None else None
        )
        runtime_path = (
            _require_provenance_path("runtime_path", self.runtime_path) if self.runtime_path is not None else None
        )
        command = _optional_completion_command("command", self.command)
        if applicability == "not_applicable":
            if source_path is not None or runtime_path is not None or command is not None:
                raise ValueError("not_applicable entrypoint must not declare a locator")
        elif source_path is None and runtime_path is None:
            raise ValueError("active entrypoint must declare source_path or runtime_path")
        object.__setattr__(self, "source_path", source_path)
        object.__setattr__(self, "runtime_path", runtime_path)
        object.__setattr__(self, "command", command)
        object.__setattr__(self, "owner_task_id", owner_task_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "obligation_id": self.obligation_id,
            "kind": self.kind,
            "applicability": self.applicability,
            "owner_task_id": self.owner_task_id,
            "source_path": self.source_path,
            "runtime_path": self.runtime_path,
            "command": self.command,
        }


@dataclass(frozen=True)
class ProjectKindAuthorityV1:
    """Factory-owned application/library classification and its immutable source."""

    project_kind: ProjectKindV1
    source_ref: str
    source_hash: str
    justification: str
    authority_hash: str = field(init=False)

    def __post_init__(self) -> None:
        project_kind = _require_literal("project_kind", self.project_kind, _PROJECT_KINDS)
        source_ref = _require_provenance_text(
            "source_ref",
            self.source_ref,
            max_utf8_bytes=_PROVENANCE_MAX_PATH_BYTES,
        )
        source_hash = _require_provenance_sha256("source_hash", self.source_hash)
        justification = _require_provenance_text(
            "justification",
            self.justification,
            max_utf8_bytes=512,
        )
        object.__setattr__(self, "project_kind", project_kind)
        object.__setattr__(self, "source_ref", source_ref)
        object.__setattr__(self, "source_hash", source_hash)
        object.__setattr__(self, "justification", justification)
        object.__setattr__(
            self,
            "authority_hash",
            _project_kind_authority_hash(
                project_kind=project_kind,
                source_ref=source_ref,
                source_hash=source_hash,
                justification=justification,
            ),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "project_kind": self.project_kind,
            "source_ref": self.source_ref,
            "source_hash": self.source_hash,
            "justification": self.justification,
            "authority_hash": self.authority_hash,
        }


@dataclass(frozen=True)
class VerificationCommandAuthorityV1:
    """One exact PM-owned verifier command and its declared semantic modality."""

    task_id: str
    modality: VerificationModalityV1
    argv: tuple[str, ...]
    cwd: str = "."
    command: str = field(init=False)
    authority_hash: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_completion_token("task_id", self.task_id))
        object.__setattr__(
            self,
            "modality",
            _require_literal("modality", self.modality, _VERIFICATION_MODALITIES),
        )
        argv = _require_verifier_argv(self.argv)
        _require_verification_command_proof(argv)
        cwd = _require_verifier_cwd(self.cwd)
        command = _optional_completion_command("command", shlex.join(argv))
        if command is None:  # pragma: no cover - argv is non-empty by construction.
            raise ValueError("verification command must be non-empty")
        object.__setattr__(self, "argv", argv)
        object.__setattr__(self, "cwd", cwd)
        object.__setattr__(self, "command", command)
        object.__setattr__(
            self,
            "authority_hash",
            _verification_command_authority_hash(
                task_id=self.task_id,
                modality=self.modality,
                argv=argv,
                cwd=cwd,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "modality": self.modality,
            "argv": list(self.argv),
            "cwd": self.cwd,
            "command": self.command,
            "authority_hash": self.authority_hash,
        }


@dataclass(frozen=True)
class VerificationObligationV1:
    """One command-backed verifier or explicit N/A verifier modality."""

    obligation_id: str
    modality: VerificationModalityV1
    command: str | None
    applicability: ObligationApplicabilityV1
    covers_obligation_ids: tuple[str, ...] = field(default_factory=tuple)
    owner_task_id: str | None = None
    command_authority_hash: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "obligation_id",
            _require_completion_token("obligation_id", self.obligation_id),
        )
        object.__setattr__(
            self,
            "modality",
            _require_literal("modality", self.modality, _VERIFICATION_MODALITIES),
        )
        applicability = _require_literal(
            "applicability",
            self.applicability,
            _OBLIGATION_APPLICABILITIES,
        )
        object.__setattr__(self, "applicability", applicability)
        owner_task_id = (
            _require_completion_token("owner_task_id", self.owner_task_id) if self.owner_task_id is not None else None
        )
        command = _optional_completion_command("command", self.command)
        covers_obligation_ids = _canonicalize_completion_id_tuple(
            "covers_obligation_ids",
            self.covers_obligation_ids,
            max_items=_PROJECT_COMPLETION_MAX_VERIFIER_REFS,
        )
        if applicability == "not_applicable":
            if command is not None:
                raise ValueError("not_applicable verification must not declare a command")
            if covers_obligation_ids:
                raise ValueError("not_applicable verification must not cover obligations")
            if self.command_authority_hash is not None:
                raise ValueError("not_applicable verification must not declare command authority")
        elif command is None:
            raise ValueError("active verification obligation requires a command")
        command_authority_hash = (
            _require_provenance_sha256("command_authority_hash", self.command_authority_hash)
            if self.command_authority_hash is not None
            else None
        )
        # Standalone obligation values may be assembled before the PM authority table is
        # available. ProjectCompletionContractV1 is the authoritative boundary and rejects
        # every active verifier whose exact command_authority_hash is absent or mismatched.
        object.__setattr__(self, "command", command)
        object.__setattr__(self, "covers_obligation_ids", covers_obligation_ids)
        object.__setattr__(self, "owner_task_id", owner_task_id)
        object.__setattr__(self, "command_authority_hash", command_authority_hash)

    def to_dict(self) -> dict[str, Any]:
        return {
            "obligation_id": self.obligation_id,
            "modality": self.modality,
            "command": self.command,
            "applicability": self.applicability,
            "covers_obligation_ids": list(self.covers_obligation_ids),
            "owner_task_id": self.owner_task_id,
            "command_authority_hash": self.command_authority_hash,
        }


@dataclass(frozen=True)
class ProjectCompletionObligationsV1:
    """Canonical obligation set; it contains no execution or lifecycle status."""

    artifacts: tuple[ArtifactObligationV1, ...]
    entrypoints: tuple[EntrypointObligationV1, ...]
    verification: tuple[VerificationObligationV1, ...]

    def __post_init__(self) -> None:
        collection_caps = (
            ("artifacts", self.artifacts, _PROJECT_COMPLETION_MAX_ARTIFACTS),
            ("entrypoints", self.entrypoints, _PROJECT_COMPLETION_MAX_ENTRYPOINTS),
            ("verification", self.verification, _PROJECT_COMPLETION_MAX_VERIFICATIONS),
        )
        for name, collection_values, max_items in collection_caps:
            if not isinstance(collection_values, (list, tuple)):
                raise TypeError(f"{name} must be a list or tuple")
            if len(collection_values) > max_items:
                raise ValueError(f"{name} must contain at most {max_items} obligations")

        groups: tuple[tuple[str, object, type[Any]], ...] = (
            ("artifacts", self.artifacts, ArtifactObligationV1),
            ("entrypoints", self.entrypoints, EntrypointObligationV1),
            ("verification", self.verification, VerificationObligationV1),
        )
        for name, group_values, expected_type in groups:
            if not isinstance(group_values, (list, tuple)):
                raise TypeError(f"{name} must be a list or tuple")
            if any(type(value) is not expected_type for value in group_values):
                raise TypeError(f"{name} contains a non-exact obligation type")

        artifacts = tuple(sorted(self.artifacts, key=lambda value: value.obligation_id))
        entrypoints = tuple(sorted(self.entrypoints, key=lambda value: value.obligation_id))
        verification = tuple(sorted(self.verification, key=lambda value: value.obligation_id))
        all_ids = (
            [item.obligation_id for item in artifacts]
            + [item.obligation_id for item in entrypoints]
            + [item.obligation_id for item in verification]
        )
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("duplicate obligation_id across project completion obligations")

        artifact_paths = [item.path for item in artifacts]
        if len(artifact_paths) != len(set(artifact_paths)):
            raise ValueError("duplicate artifact path across project completion obligations")

        object.__setattr__(self, "artifacts", artifacts)
        object.__setattr__(self, "entrypoints", entrypoints)
        object.__setattr__(self, "verification", verification)

    def to_dict(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "artifacts": [item.to_dict() for item in self.artifacts],
            "entrypoints": [item.to_dict() for item in self.entrypoints],
            "verification": [item.to_dict() for item in self.verification],
        }


@dataclass(frozen=True)
class ProjectCompletionContractV1:
    """Immutable CE-owned obligations required for project completion.

    This contract deliberately contains no progress, pass/fail, or lifecycle
    fields.  Runtime owners bind evidence to its content hash; they do not
    rewrite the obligations while work is in flight.
    """

    project_id: str
    run_id: str
    project_kind: ProjectKindV1
    project_kind_authority: ProjectKindAuthorityV1
    pm_contract_hash: str
    covered_task_ids: tuple[str, ...]
    obligations: ProjectCompletionObligationsV1
    completion_predicate_version: str
    verifier_policy_hash: str
    verifier_policy_snapshot_hash: str
    verification_command_authority: tuple[VerificationCommandAuthorityV1, ...]
    schema_version: Literal["polaris.project_completion_contract.v1"] = _PROJECT_COMPLETION_CONTRACT_SCHEMA_V1
    contract_id: str = field(init=False)
    contract_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != _PROJECT_COMPLETION_CONTRACT_SCHEMA_V1:
            raise ValueError(f"schema_version must equal {_PROJECT_COMPLETION_CONTRACT_SCHEMA_V1!r}")
        object.__setattr__(
            self,
            "project_id",
            _require_completion_token("project_id", self.project_id),
        )
        object.__setattr__(self, "run_id", _require_completion_token("run_id", self.run_id))
        project_kind = _require_literal("project_kind", self.project_kind, _PROJECT_KINDS)
        object.__setattr__(self, "project_kind", project_kind)
        if type(self.project_kind_authority) is not ProjectKindAuthorityV1:
            raise TypeError("project_kind_authority must be exact ProjectKindAuthorityV1")
        if self.project_kind_authority.project_kind != project_kind:
            raise ValueError("project_kind must match Factory-owned project_kind_authority")
        object.__setattr__(
            self,
            "pm_contract_hash",
            _require_provenance_sha256("pm_contract_hash", self.pm_contract_hash),
        )
        object.__setattr__(
            self,
            "covered_task_ids",
            _canonicalize_completion_id_tuple(
                "covered_task_ids",
                self.covered_task_ids,
                require_items=True,
                max_items=_PROJECT_COMPLETION_MAX_TASK_IDS,
            ),
        )
        if type(self.obligations) is not ProjectCompletionObligationsV1:
            raise TypeError("obligations must be exact ProjectCompletionObligationsV1")
        object.__setattr__(
            self,
            "completion_predicate_version",
            _require_completion_token(
                "completion_predicate_version",
                self.completion_predicate_version,
            ),
        )
        object.__setattr__(
            self,
            "verifier_policy_hash",
            _require_provenance_sha256("verifier_policy_hash", self.verifier_policy_hash),
        )
        object.__setattr__(
            self,
            "verifier_policy_snapshot_hash",
            _require_provenance_sha256(
                "verifier_policy_snapshot_hash",
                self.verifier_policy_snapshot_hash,
            ),
        )
        if not isinstance(self.verification_command_authority, (list, tuple)):
            raise TypeError("verification_command_authority must be a list or tuple")
        command_authority: list[VerificationCommandAuthorityV1] = []
        command_authority_hashes: set[str] = set()
        for index, item in enumerate(self.verification_command_authority):
            if type(item) is not VerificationCommandAuthorityV1:
                raise TypeError(f"verification_command_authority[{index}] must be exact VerificationCommandAuthorityV1")
            if item.authority_hash in command_authority_hashes:
                continue
            command_authority_hashes.add(item.authority_hash)
            command_authority.append(item)
        command_authority.sort(key=lambda item: item.authority_hash)
        if not command_authority:
            raise ValueError("project completion contract requires verification command authority")
        object.__setattr__(self, "verification_command_authority", tuple(command_authority))

        required_artifacts = tuple(item for item in self.obligations.artifacts if item.applicability == "required")
        if not required_artifacts:
            raise ValueError("project completion contract requires at least one required artifact")

        required_delivery_verifiers = tuple(
            item
            for item in self.obligations.verification
            if item.applicability == "required" and item.modality in {"build", "test", "lint"}
        )
        if not required_delivery_verifiers:
            raise ValueError("project completion contract requires a real required build/test/lint verifier")

        test_artifact_declarations = tuple(item for item in self.obligations.artifacts if item.semantic_role == "test")
        if not test_artifact_declarations:
            raise ValueError("project completion contract requires a test artifact declaration")
        required_test_artifacts = tuple(item for item in test_artifact_declarations if item.applicability == "required")
        explicit_na_test_artifacts = tuple(
            item for item in test_artifact_declarations if item.applicability == "not_applicable"
        )
        test_verifier_declarations = tuple(item for item in self.obligations.verification if item.modality == "test")
        if not test_verifier_declarations:
            raise ValueError("project completion contract requires a test verifier declaration")
        required_tests = tuple(item for item in test_verifier_declarations if item.applicability == "required")
        explicit_na_tests = tuple(item for item in test_verifier_declarations if item.applicability == "not_applicable")
        if bool(required_test_artifacts) != bool(required_tests):
            raise ValueError("required test artifact and required test verifier must be declared together")
        if bool(explicit_na_test_artifacts) != bool(explicit_na_tests):
            raise ValueError("not_applicable test artifact and test verifier must be declared together")
        if required_test_artifacts and (explicit_na_test_artifacts or explicit_na_tests):
            raise ValueError("test obligations cannot be both required and not_applicable")
        if not required_test_artifacts and not explicit_na_test_artifacts:
            raise ValueError("test artifact and verifier must be required together or explicitly not_applicable")

        environment_declarations = tuple(
            item for item in self.obligations.verification if item.modality == "environment_prep"
        )
        if not environment_declarations:
            raise ValueError("project completion contract requires an environment_prep declaration")

        if project_kind == "application":
            if not required_test_artifacts or not required_tests:
                raise ValueError("application project requires a required test artifact and required test verifier")
            if explicit_na_test_artifacts or explicit_na_tests:
                raise ValueError("application test obligations cannot be not_applicable")
            required_environment_preparation = tuple(
                item
                for item in self.obligations.verification
                if item.modality == "environment_prep" and item.applicability == "required"
            )
            if not required_environment_preparation:
                raise ValueError("application project requires a command-backed required environment_prep verifier")
            if any(item.applicability == "not_applicable" for item in environment_declarations):
                raise ValueError("application environment_prep cannot be both required and not_applicable")
            required_entrypoints = tuple(
                item
                for item in self.obligations.entrypoints
                if item.applicability == "required" and item.kind != "library"
            )
            if not required_entrypoints:
                raise ValueError("application project requires a required entrypoint")
            invalid_executable_entrypoints = tuple(
                item
                for item in required_entrypoints
                if item.command is None or (item.source_path is None and item.runtime_path is None)
            )
            if invalid_executable_entrypoints:
                raise ValueError(
                    "every required application entrypoint must be an executable probe "
                    "with command and source_path or runtime_path; invalid obligation_ids="
                    f"{[item.obligation_id for item in invalid_executable_entrypoints]}"
                )
        else:
            library_requires_environment_preparation = any(
                item.applicability == "required" for item in environment_declarations
            )
            library_environment_preparation_na = any(
                item.applicability == "not_applicable" for item in environment_declarations
            )
            if not library_requires_environment_preparation and not library_environment_preparation_na:
                raise ValueError("library environment_prep must be required or explicitly not_applicable")
            if library_requires_environment_preparation and library_environment_preparation_na:
                raise ValueError("library environment_prep cannot be both required and not_applicable")
            explicit_na_entrypoint = any(
                item.kind == "library" and item.applicability == "not_applicable"
                for item in self.obligations.entrypoints
            )
            if not explicit_na_entrypoint:
                raise ValueError("library project requires an explicit not_applicable entrypoint")

            active_entrypoints = tuple(
                item for item in self.obligations.entrypoints if item.applicability != "not_applicable"
            )
            if active_entrypoints:
                raise ValueError("library project explicit N/A contract forbids active entrypoint obligations")

        artifact_ids = {item.obligation_id for item in self.obligations.artifacts}
        artifact_paths = {item.path for item in self.obligations.artifacts}
        entrypoint_ids = {item.obligation_id for item in self.obligations.entrypoints}
        known_obligation_ids = artifact_ids | entrypoint_ids
        covered_task_ids = set(self.covered_task_ids)
        unknown_command_authority_tasks = {
            item.task_id for item in self.verification_command_authority if item.task_id not in covered_task_ids
        }
        if unknown_command_authority_tasks:
            raise ValueError(
                "verification command authority task_id is outside covered_task_ids; "
                f"task_ids={sorted(unknown_command_authority_tasks)}"
            )
        all_obligations: tuple[
            ArtifactObligationV1 | EntrypointObligationV1 | VerificationObligationV1,
            ...,
        ] = (
            *self.obligations.artifacts,
            *self.obligations.entrypoints,
            *self.obligations.verification,
        )
        for obligation in all_obligations:
            if obligation.applicability == "not_applicable":
                if obligation.owner_task_id is not None:
                    raise ValueError(
                        f"not_applicable obligation {obligation.obligation_id!r} must not declare owner_task_id"
                    )
                continue
            if obligation.owner_task_id is None:
                raise ValueError(f"active obligation {obligation.obligation_id!r} requires owner_task_id")
            if obligation.owner_task_id not in covered_task_ids:
                raise ValueError(f"obligation {obligation.obligation_id!r} owner_task_id is outside covered_task_ids")
        required_test_artifact_ids = {
            item.obligation_id
            for item in self.obligations.artifacts
            if item.semantic_role == "test" and item.applicability == "required"
        }

        for entrypoint in self.obligations.entrypoints:
            if entrypoint.applicability != "not_applicable" and (
                entrypoint.source_path is None and entrypoint.runtime_path is None
            ):
                raise ValueError(f"active entrypoint {entrypoint.obligation_id!r} requires source_path or runtime_path")
            # Source entrypoints must bind declared delivery artifacts.
            # Runtime locators may name compiler-generated output (for example
            # ``src/main.ts`` -> ``dist/main.js``), so they derive from the
            # declared source artifact and exact PM-authorized command instead
            # of pretending generated output is a PM source target. A
            # runtime-only entrypoint still requires a declared artifact.
            if entrypoint.source_path is not None and entrypoint.source_path not in artifact_paths:
                raise ValueError(
                    f"entrypoint {entrypoint.obligation_id!r} references undeclared artifact path "
                    f"{entrypoint.source_path!r}"
                )
            if (
                entrypoint.source_path is None
                and entrypoint.runtime_path is not None
                and entrypoint.runtime_path not in artifact_paths
            ):
                raise ValueError(
                    f"entrypoint {entrypoint.obligation_id!r} references undeclared artifact path "
                    f"{entrypoint.runtime_path!r}"
                )

        active_entrypoints = tuple(
            item for item in self.obligations.entrypoints if item.applicability != "not_applicable"
        )
        entrypoint_verifiers = tuple(
            item
            for item in self.obligations.verification
            if item.applicability != "not_applicable" and item.modality == "entrypoint"
        )
        for entrypoint in active_entrypoints:
            if not any(
                entrypoint.obligation_id in verifier.covers_obligation_ids
                and entrypoint.owner_task_id == verifier.owner_task_id
                and entrypoint.command == verifier.command
                for verifier in entrypoint_verifiers
            ):
                raise ValueError(
                    f"active entrypoint {entrypoint.obligation_id!r} requires an exact PM-authorized "
                    "entrypoint verifier with the same owner and command"
                )

        authority_by_hash = {item.authority_hash: item for item in self.verification_command_authority}
        for verifier in self.obligations.verification:
            covered_ids = set(verifier.covers_obligation_ids)
            unknown_ids = covered_ids - known_obligation_ids
            if unknown_ids:
                raise ValueError(
                    f"verifier {verifier.obligation_id!r} references unknown obligation ids {sorted(unknown_ids)}"
                )
            if verifier.applicability == "not_applicable":
                continue
            authority = authority_by_hash.get(str(verifier.command_authority_hash))
            if authority is None:
                raise ValueError(f"verifier {verifier.obligation_id!r} command_authority_hash is not PM-authorized")
            if (
                authority.task_id != verifier.owner_task_id
                or authority.modality != verifier.modality
                or authority.command != verifier.command
            ):
                raise ValueError(
                    f"verifier {verifier.obligation_id!r} does not match its exact PM argv/cwd/modality owner authority"
                )
            if verifier.modality == "entrypoint":
                if not covered_ids or not covered_ids.issubset(entrypoint_ids):
                    raise ValueError(
                        f"entrypoint verifier {verifier.obligation_id!r} must cover entrypoint obligation ids only"
                    )
                continue
            if verifier.applicability == "required" and not covered_ids.intersection(artifact_ids):
                raise ValueError(
                    f"required non-entrypoint verifier {verifier.obligation_id!r} must cover owned artifact obligations"
                )
            if (
                verifier.modality == "test"
                and verifier.applicability == "required"
                and not covered_ids.intersection(required_test_artifact_ids)
            ):
                raise ValueError(
                    f"required test verifier {verifier.obligation_id!r} must cover at least one required test artifact"
                )

        contract_hash = _project_completion_contract_hash(self.to_seed_dict())
        object.__setattr__(self, "contract_hash", contract_hash)
        object.__setattr__(self, "contract_id", _project_completion_contract_id(contract_hash))

    def to_seed_dict(self) -> dict[str, Any]:
        """Return the canonical hash seed, excluding derived id and hash."""

        return _canonical_project_completion_contract_seed(
            schema_version=self.schema_version,
            project_id=self.project_id,
            run_id=self.run_id,
            project_kind=self.project_kind,
            project_kind_authority=self.project_kind_authority,
            pm_contract_hash=self.pm_contract_hash,
            covered_task_ids=self.covered_task_ids,
            obligations=self.obligations.to_dict(),
            completion_predicate_version=self.completion_predicate_version,
            verifier_policy_hash=self.verifier_policy_hash,
            verifier_policy_snapshot_hash=self.verifier_policy_snapshot_hash,
            verification_command_authority=self.verification_command_authority,
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ProjectCompletionContractV1:
        """Strictly reconstruct and verify one persisted completion contract."""

        if not isinstance(payload, Mapping):
            raise TypeError("project completion contract must be a mapping")
        expected_fields = {
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
            "contract_id",
            "contract_hash",
        }
        if set(payload) != expected_fields:
            raise ValueError("project completion contract fields must match the canonical schema exactly")

        project_kind_authority_payload = payload.get("project_kind_authority")
        if not isinstance(project_kind_authority_payload, Mapping) or set(project_kind_authority_payload) != {
            "project_kind",
            "source_ref",
            "source_hash",
            "justification",
            "authority_hash",
        }:
            raise ValueError("project_kind_authority fields must match the canonical schema exactly")
        project_kind_authority = ProjectKindAuthorityV1(
            project_kind=project_kind_authority_payload["project_kind"],
            source_ref=project_kind_authority_payload["source_ref"],
            source_hash=project_kind_authority_payload["source_hash"],
            justification=project_kind_authority_payload["justification"],
        )
        if project_kind_authority_payload["authority_hash"] != project_kind_authority.authority_hash:
            raise ValueError("project_kind_authority derived identity is invalid")

        obligations_payload = payload.get("obligations")
        if not isinstance(obligations_payload, Mapping) or set(obligations_payload) != {
            "artifacts",
            "entrypoints",
            "verification",
        }:
            raise ValueError("project completion obligations fields must match the canonical schema exactly")

        def exact_rows(name: str, fields: set[str]) -> list[Mapping[str, Any]]:
            value = obligations_payload.get(name)
            if not isinstance(value, list):
                raise TypeError(f"obligations.{name} must be a list")
            rows: list[Mapping[str, Any]] = []
            for index, item in enumerate(value):
                if not isinstance(item, Mapping) or set(item) != fields:
                    raise ValueError(f"obligations.{name}[{index}] fields are invalid")
                rows.append(item)
            return rows

        artifacts = tuple(
            ArtifactObligationV1(
                obligation_id=item["obligation_id"],
                path=item["path"],
                semantic_role=item["semantic_role"],
                applicability=item["applicability"],
                owner_task_id=item["owner_task_id"],
            )
            for item in exact_rows(
                "artifacts",
                {"obligation_id", "path", "semantic_role", "applicability", "owner_task_id"},
            )
        )
        entrypoints = tuple(
            EntrypointObligationV1(
                obligation_id=item["obligation_id"],
                kind=item["kind"],
                applicability=item["applicability"],
                owner_task_id=item["owner_task_id"],
                source_path=item["source_path"],
                runtime_path=item["runtime_path"],
                command=item["command"],
            )
            for item in exact_rows(
                "entrypoints",
                {
                    "obligation_id",
                    "kind",
                    "applicability",
                    "owner_task_id",
                    "source_path",
                    "runtime_path",
                    "command",
                },
            )
        )
        verification = tuple(
            VerificationObligationV1(
                obligation_id=item["obligation_id"],
                modality=item["modality"],
                command=item["command"],
                applicability=item["applicability"],
                covers_obligation_ids=item["covers_obligation_ids"],
                owner_task_id=item["owner_task_id"],
                command_authority_hash=item["command_authority_hash"],
            )
            for item in exact_rows(
                "verification",
                {
                    "obligation_id",
                    "modality",
                    "command",
                    "applicability",
                    "covers_obligation_ids",
                    "owner_task_id",
                    "command_authority_hash",
                },
            )
        )
        command_authority_payload = payload.get("verification_command_authority")
        if not isinstance(command_authority_payload, list):
            raise TypeError("verification_command_authority must be a list")
        verification_command_authority_rows: list[VerificationCommandAuthorityV1] = []
        for index, item in enumerate(command_authority_payload):
            if not isinstance(item, Mapping) or set(item) != {
                "task_id",
                "modality",
                "argv",
                "cwd",
                "command",
                "authority_hash",
            }:
                raise ValueError(f"verification_command_authority[{index}] fields are invalid")
            authority = VerificationCommandAuthorityV1(
                task_id=item["task_id"],
                modality=item["modality"],
                argv=item["argv"],
                cwd=item["cwd"],
            )
            if item["command"] != authority.command or item["authority_hash"] != authority.authority_hash:
                raise ValueError(f"verification_command_authority[{index}] derived identity is invalid")
            verification_command_authority_rows.append(authority)
        verification_command_authority = tuple(verification_command_authority_rows)
        contract = cls(
            schema_version=payload["schema_version"],
            project_id=payload["project_id"],
            run_id=payload["run_id"],
            project_kind=payload["project_kind"],
            project_kind_authority=project_kind_authority,
            pm_contract_hash=payload["pm_contract_hash"],
            covered_task_ids=payload["covered_task_ids"],
            obligations=ProjectCompletionObligationsV1(
                artifacts=artifacts,
                entrypoints=entrypoints,
                verification=verification,
            ),
            completion_predicate_version=payload["completion_predicate_version"],
            verifier_policy_hash=payload["verifier_policy_hash"],
            verifier_policy_snapshot_hash=payload["verifier_policy_snapshot_hash"],
            verification_command_authority=verification_command_authority,
        )
        if payload["contract_id"] != contract.contract_id or payload["contract_hash"] != contract.contract_hash:
            raise ValueError("project completion contract derived identity is invalid")
        return contract

    def to_dict(self) -> dict[str, Any]:
        payload = self.to_seed_dict()
        payload["contract_id"] = self.contract_id
        payload["contract_hash"] = self.contract_hash
        return payload


__all__ = [
    "ADREventV1",
    "ADRRecordV1",
    "ADRStatus",
    "ArchitectureDecisionV1",
    "ArtifactObligationV1",
    "ArtifactSemanticRoleV1",
    "BuildChiefEngineerBlueprintPortfolioCommandV1",
    "CeHandoffDecisionBindingsV1",
    "CeHandoffDecisionV1",
    "ChiefEngineerBlueprintError",
    "ChiefEngineerBlueprintErrorV1",
    "ChiefEngineerBlueprintPortfolioV1",
    "ChiefEngineerPortfolioTaskV1",
    "ChiefEngineerProjectInterfaceContractV1",
    "EntrypointKindV1",
    "EntrypointObligationV1",
    "GenerateTaskBlueprintCommandV1",
    "GetBlueprintStatusQueryV1",
    "GovernanceSummaryV1",
    "HandoffDecisionV1",
    "IncidentSeverity",
    "ListADRsQueryV1",
    "ListPostMortemsQueryV1",
    "ListRisksQueryV1",
    "ListTechDebtQueryV1",
    "ListTechRadarQueryV1",
    "ObligationApplicabilityV1",
    "PostMortemEventV1",
    "PostMortemRecordV1",
    "PostMortemStatus",
    "ProjectCompletionContractV1",
    "ProjectCompletionObligationsV1",
    "ProjectKindAuthorityV1",
    "ProjectKindV1",
    "QualityGateResultV1",
    "QueryBlueprintProvenanceV1",
    "QueryProjectCompletionContractV1",
    "RegisterADRCommandV1",
    "RegisterPostMortemCommandV1",
    "RegisterRiskCommandV1",
    "RegisterTechDebtCommandV1",
    "RegisterTechRadarCommandV1",
    "ReleaseDecision",
    "ReleaseReadinessV1",
    "RiskEventV1",
    "RiskRecordV1",
    "RiskSeverity",
    "RiskStatus",
    "RollbackLinkV1",
    "RollbackStrategy",
    "StackPolicyViolationV1",
    "TaskBlueprintGeneratedEventV1",
    "TaskBlueprintProvenanceSnapshotV1",
    "TaskBlueprintResultV1",
    "TechDebtEventV1",
    "TechDebtRecordV1",
    "TechDebtSeverity",
    "TechDebtStatus",
    "TechRadarEntryV1",
    "TechRadarEventV1",
    "TechRadarRing",
    "UpdateADRStatusCommandV1",
    "UpdatePostMortemStatusCommandV1",
    "UpdateRiskStatusCommandV1",
    "UpdateTechDebtStatusCommandV1",
    "UpdateTechRadarRingCommandV1",
    "VerificationCommandAuthorityV1",
    "VerificationModalityV1",
    "VerificationObligationV1",
    "project_completion_verifier_policy_snapshot_hash",
]
