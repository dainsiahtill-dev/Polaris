"""Schema constants and validation helpers for chief_engineer.blueprint contracts."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Mapping
from pathlib import PurePosixPath, PureWindowsPath
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
