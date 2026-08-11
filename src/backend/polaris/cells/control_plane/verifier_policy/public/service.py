"""Platform verifier policy service.

This cell owns configuration for optional evidence modalities. It deliberately
does not execute Browser, visual-LLM, or user-script verifiers; execution
providers consume this public policy later and must emit Run Ledger evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import sys
from pathlib import Path
from typing import Any

from polaris.cells.control_plane.verifier_policy.internal.trusted_command_profiles import (
    evaluate_builtin_proof,
    resolve_builtin_profile,
)
from polaris.cells.control_plane.verifier_policy.public.contracts import (
    OPTIONAL_VERIFIER_MODALITIES,
    SUPPORTED_EVIDENCE_MODALITIES,
    CompileEvidencePolicyCommandV1,
    ControlPlaneVerifierPolicyV1Error,
    EvaluateVerifierCommandPolicyQueryV1,
    EvidencePolicyResultV1,
    ReadVerifierPolicyQueryV1,
    UpdateVerifierPolicyCommandV1,
    VerifierCommandPolicyDecisionV1,
    VerifierPolicyResultV1,
)
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter

SCHEMA_VERSION = 1
POLICY_SOURCE = "control_plane.verifier_policy"
POLICY_RELATIVE_PATH = ".polaris/verifier_policy.json"
_EPHEMERAL_EXECUTABLE_ROOTS = tuple(Path(item).resolve() for item in ("/tmp", "/var/tmp", "/dev/shm"))


def _policy_path(workspace: Path) -> Path:
    return workspace / POLICY_RELATIVE_PATH


def _bool_value(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
    return bool(value)


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_items = value.replace(";", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        raw_items = []
    output: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        token = str(item or "").strip().lower()
        if token and token not in seen:
            output.append(token)
            seen.add(token)
    return output


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _configured_trusted_executable_roots() -> tuple[Path, ...]:
    roots = [Path(item).resolve() for item in ("/bin", "/sbin", "/usr", "/opt") if Path(item).exists()]
    for item in (sys.prefix, sys.base_prefix):
        root = Path(item).expanduser().resolve()
        if root.exists() and root not in roots:
            roots.append(root)
    raw = os.environ.get("KERNELONE_VERIFIER_TRUSTED_EXECUTABLE_ROOTS", "")
    for item in raw.split(os.pathsep):
        if not item:
            continue
        root = Path(item).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ControlPlaneVerifierPolicyV1Error("trusted executable root must be a directory")
        if any(_is_within(root, ephemeral) for ephemeral in _EPHEMERAL_EXECUTABLE_ROOTS):
            raise ControlPlaneVerifierPolicyV1Error("trusted executable root must not be ephemeral")
        if root not in roots:
            roots.append(root)
    return tuple(roots)


def _resolve_executable_identity(
    query: EvaluateVerifierCommandPolicyQueryV1,
    *,
    custom_script: bool,
) -> tuple[str, str, str, str]:
    """Resolve an exact executable call path, realpath and digest.

    Bare command names intentionally consume the operator-controlled PATH;
    explicit paths are accepted only below built-in/configured trusted roots.
    A hash-pinned custom script is the sole workspace executable exception.
    """

    raw = query.argv[0]
    workspace = Path(query.workspace).expanduser().resolve()
    cwd = workspace if query.cwd == "." else (workspace / query.cwd).resolve()
    explicit = "/" in raw.replace("\\", "/")
    if explicit:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = cwd / candidate
        selected = candidate.absolute()
    else:
        located = shutil.which(raw)
        if not located:
            return "", "", "", "trusted_verifier_executable_missing"
        selected = Path(located).expanduser().absolute()
    try:
        realpath = selected.resolve(strict=True)
    except OSError:
        return "", "", "", "trusted_verifier_executable_missing"
    if not realpath.is_file():
        return "", "", "", "trusted_verifier_executable_missing"
    in_workspace = _is_within(selected, workspace) or _is_within(realpath, workspace)
    ephemeral = any(
        _is_within(selected, root) or _is_within(realpath, root) for root in _EPHEMERAL_EXECUTABLE_ROOTS
    )
    custom_workspace_executable = custom_script and explicit and len(query.argv) == 1
    if ephemeral or (custom_workspace_executable and not in_workspace) or (not custom_workspace_executable and in_workspace):
        return "", "", "", "untrusted_verifier_executable"
    if (
        explicit
        and not custom_workspace_executable
        and not any(_is_within(realpath, root) for root in _configured_trusted_executable_roots())
    ):
        return "", "", "", "untrusted_verifier_executable"
    try:
        executable_hash = hashlib.sha256(realpath.read_bytes()).hexdigest()
    except OSError:
        return "", "", "", "trusted_verifier_executable_unreadable"
    return str(selected), str(realpath), executable_hash, ""


def _node_script_name(argv: tuple[str, ...]) -> str:
    executable = Path(argv[0].replace("\\", "/")).name.casefold()
    if executable not in {"npm", "pnpm", "yarn", "bun"} or len(argv) < 2:
        return ""
    if argv[1] in {"test", "start", "build", "lint", "dev"}:
        return argv[1]
    if len(argv) >= 3 and argv[1] in {"run", "run-script"}:
        return argv[2]
    return ""


def _node_script_content_evidence(query: EvaluateVerifierCommandPolicyQueryV1) -> tuple[str, str]:
    """Bind package-script authority to current manifest/runner bytes.

    A package script may invoke one direct Node program for an entrypoint or a
    self-contained test harness. That target remains target-controlled, so its
    current bytes are part of the policy decision hash and are re-evaluated by
    ExecutionBroker before launch. Shell wrappers, eval, missing files and
    escaping paths remain rejected.
    """

    script_name = _node_script_name(query.argv)
    if not script_name:
        return "", ""
    manifest_path = Path(query.workspace) / query.cwd / "package.json"
    try:
        raw = manifest_path.read_bytes()
        manifest = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return "", "untrusted_package_script"
    scripts = manifest.get("scripts") if isinstance(manifest, dict) else None
    script = scripts.get(script_name) if isinstance(scripts, dict) else None
    if not isinstance(script, str) or not script.strip():
        return "", "untrusted_package_script"
    normalized = " ".join(script.casefold().split())
    no_op_prefixes = ("echo ", "true", "exit 0", ":", "printf ")
    no_op_tokens = ("--collect-only", "--watch", "--watchall", "--fix")
    if normalized in {"echo", "true", "exit 0", ":"} or normalized.startswith(no_op_prefixes) or any(
        token in normalized for token in no_op_tokens
    ):
        return "", "non_proving_package_script"
    try:
        tokens = tuple(shlex.split(script, posix=True))
    except ValueError:
        return "", "non_proving_package_script"
    direct_node_script_hash = ""
    if tokens and Path(tokens[0].replace("\\", "/")).name.casefold() == "node":
        if query.modality not in {"test", "entrypoint"} or len(tokens) != 2 or tokens[1].startswith("-"):
            return "", "non_proving_package_script"
        raw_target = tokens[1].replace("\\", "/")
        target = Path(raw_target)
        if target.is_absolute() or ".." in target.parts or target.suffix.casefold() not in {".js", ".mjs", ".cjs"}:
            return "", "untrusted_package_script"
        workspace = Path(query.workspace).expanduser().resolve()
        cwd = workspace if query.cwd == "." else (workspace / query.cwd).resolve()
        target_path = (cwd / target).resolve()
        if not _is_within(target_path, workspace) or not target_path.is_file():
            return "", "untrusted_package_script"
        try:
            direct_node_script_hash = hashlib.sha256(target_path.read_bytes()).hexdigest()
        except OSError:
            return "", "untrusted_package_script"
    if not _node_script_matches_proof_runner(query.modality, tokens):
        return "", "non_proving_package_script"
    return _stable_hash(
        {
            "domain": "control_plane.verifier_policy.node_package_script.v1",
            "manifest_sha256": hashlib.sha256(raw).hexdigest(),
            "direct_node_script_sha256": direct_node_script_hash,
        }
    ), ""


def _node_script_matches_proof_runner(modality: str, tokens: tuple[str, ...]) -> bool:
    """Allow one direct, proof-producing runner; reject shell/program wrappers."""

    if not tokens or any(token in {";", "&&", "||", "|", "&"} for token in tokens):
        return False
    executable = Path(tokens[0].replace("\\", "/")).name.casefold()
    arguments = tuple(item.casefold() for item in tokens[1:])
    if executable == "node":
        return (
            modality in {"test", "entrypoint"}
            and len(arguments) == 1
            and not arguments[0].startswith("-")
            and arguments[0].endswith((".js", ".mjs", ".cjs"))
        )
    option_names = {item.split("=", 1)[0] for item in arguments if item.startswith("-")}
    if option_names.intersection(
        {"--collect-only", "--watch", "--watchall", "--fix", "--fix-dry-run", "--unsafe-fixes", "--no-run"}
    ):
        return False
    allowed: dict[str, dict[str, frozenset[str]]] = {
        "test": {
            "pytest": frozenset(),
            "py.test": frozenset(),
            "vitest": frozenset({"run"}),
            "jest": frozenset(),
            "mocha": frozenset(),
            "ava": frozenset(),
            "tap": frozenset(),
        },
        "build": {
            "tsc": frozenset(),
            "vite": frozenset({"build"}),
            "webpack": frozenset(),
            "rollup": frozenset(),
            "esbuild": frozenset(),
        },
        "lint": {
            "eslint": frozenset(),
            "biome": frozenset({"check", "lint"}),
            "prettier": frozenset({"--check"}),
        },
        "entrypoint": {
            "vite": frozenset({"preview"}),
            "next": frozenset({"start"}),
        },
    }
    runner = allowed.get(modality, {}).get(executable)
    if runner is None:
        return False
    return not runner or (bool(arguments) and arguments[0] in runner)


def _available_from_env(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _normalize_script_path(value: Any) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        raise ControlPlaneVerifierPolicyV1Error("custom script path must be non-empty")
    if raw.startswith("/") or raw.startswith("../") or "/../" in raw or raw == "..":
        raise ControlPlaneVerifierPolicyV1Error("custom script path must be workspace-relative")
    return raw.lstrip("./")


def _normalize_optional_sha256(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    if len(raw) != 64 or any(character not in "0123456789abcdef" for character in raw):
        raise ControlPlaneVerifierPolicyV1Error("custom script content_sha256 must be a SHA-256 hex digest")
    return raw


def _normalize_custom_scripts(value: Any) -> list[dict[str, Any]]:
    raw_items = value if isinstance(value, list) else []
    scripts: list[dict[str, Any]] = []
    for index, raw_item in enumerate(raw_items[:50]):
        if not isinstance(raw_item, dict):
            continue
        script_path = _normalize_script_path(raw_item.get("path") or raw_item.get("script"))
        script_id = str(raw_item.get("id") or Path(script_path).stem or f"script-{index + 1}").strip()
        modality = str(raw_item.get("modality") or "custom_script").strip().lower()
        scripts.append(
            {
                "id": script_id,
                "path": script_path,
                "modality": modality or "custom_script",
                "enabled": _bool_value(raw_item.get("enabled"), True),
                "required": _bool_value(raw_item.get("required"), False),
                "content_sha256": _normalize_optional_sha256(raw_item.get("content_sha256") or raw_item.get("sha256")),
            }
        )
    return scripts


def _default_config() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "browser_enabled": False,
        "visual_enabled": False,
        "llm_judge_enabled": False,
        "custom_script_enabled": False,
        "required_modalities": [],
        "custom_scripts": [],
    }


def _read_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return _default_config()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ControlPlaneVerifierPolicyV1Error(f"failed to read verifier policy: {exc}") from exc
    if not isinstance(raw, dict):
        raise ControlPlaneVerifierPolicyV1Error("verifier policy must be a JSON object")
    return {**_default_config(), **raw}


def _enabled_modalities(config: dict[str, Any]) -> list[str]:
    enabled: list[str] = []
    if _bool_value(config.get("browser_enabled")):
        enabled.append("browser")
    if _bool_value(config.get("visual_enabled")):
        enabled.append("visual")
    if _bool_value(config.get("llm_judge_enabled")):
        enabled.append("llm_judge")
    if _bool_value(config.get("custom_script_enabled")):
        enabled.append("custom_script")
    return enabled


def _normalize_config(config: dict[str, Any]) -> dict[str, Any]:
    normalized = _default_config()
    for key in ("browser_enabled", "visual_enabled", "llm_judge_enabled", "custom_script_enabled"):
        normalized[key] = _bool_value(config.get(key), False)
    normalized["custom_scripts"] = _normalize_custom_scripts(config.get("custom_scripts"))
    required = _string_list(config.get("required_modalities"))
    for script in normalized["custom_scripts"]:
        if script.get("required"):
            required.append(str(script.get("modality") or "custom_script").strip().lower() or "custom_script")
    normalized["required_modalities"] = list(dict.fromkeys(required))
    unknown = [item for item in normalized["required_modalities"] if item not in OPTIONAL_VERIFIER_MODALITIES]
    if unknown:
        raise ControlPlaneVerifierPolicyV1Error("unsupported required verifier modalities: " + ", ".join(unknown))
    enabled = set(_enabled_modalities(normalized))
    missing_enabled = [item for item in normalized["required_modalities"] if item not in enabled]
    if missing_enabled:
        raise ControlPlaneVerifierPolicyV1Error(
            "required verifier modalities must be enabled first: " + ", ".join(missing_enabled)
        )
    return normalized


def _ensure_required_modalities_available(config: dict[str, Any]) -> None:
    """Reject impossible hard requirements before they enter gate policy.

    Custom scripts are user-authored verifier declarations: persisting them is
    safe, while execution remains fail-closed behind the explicit runtime env.
    """

    environment = _environment_status()
    missing_available: list[str] = []
    for item in _string_list(config.get("required_modalities")):
        if item == "custom_script":
            continue
        status = environment.get(item)
        if not isinstance(status, dict) or not bool(status.get("available")):
            missing_available.append(item)
    if missing_available:
        raise ControlPlaneVerifierPolicyV1Error(
            "required verifier modalities are not available in this environment: " + ", ".join(missing_available)
        )


def _environment_status() -> dict[str, Any]:
    browser_available = _available_from_env("KERNELONE_BROWSER_VERIFIER_AVAILABLE")
    multimodal_available = _available_from_env("KERNELONE_MULTIMODAL_QA_ENABLED")
    custom_script_available = _available_from_env("KERNELONE_CUSTOM_VERIFIER_SCRIPTS_ENABLED")
    return {
        "browser": {
            "available": browser_available,
            "reason": ""
            if browser_available
            else "Set KERNELONE_BROWSER_VERIFIER_AVAILABLE=1 to advertise browser verifier support.",
        },
        "visual": {
            "available": browser_available and multimodal_available,
            "reason": ""
            if browser_available and multimodal_available
            else "Requires browser verifier support and KERNELONE_MULTIMODAL_QA_ENABLED=1.",
        },
        "llm_judge": {
            "available": multimodal_available,
            "reason": ""
            if multimodal_available
            else "Set KERNELONE_MULTIMODAL_QA_ENABLED=1 to advertise multimodal QA support.",
        },
        "custom_script": {
            "available": custom_script_available,
            "reason": ""
            if custom_script_available
            else "Set KERNELONE_CUSTOM_VERIFIER_SCRIPTS_ENABLED=1 before executing custom verifier scripts.",
        },
        "qa": {"available": True, "reason": ""},
        "code": {"available": True, "reason": ""},
        "command": {"available": True, "reason": ""},
        "tool_receipt": {"available": True, "reason": ""},
        "verifier": {"available": True, "reason": ""},
        "domain": {"available": True, "reason": ""},
        "api_contract": {"available": True, "reason": ""},
        "integration": {"available": True, "reason": ""},
        "performance": {"available": False, "reason": "Performance verifier provider is not configured."},
        "security": {"available": False, "reason": "Security verifier provider is not configured."},
        "device": {"available": False, "reason": "Device verifier provider is not configured."},
        "plugin_compat": {"available": True, "reason": ""},
        "accessibility": {
            "available": browser_available,
            "reason": "" if browser_available else "Requires browser verifier support.",
        },
    }


def _policy_payload(workspace: Path, config: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_config(config)
    enabled = _enabled_modalities(normalized)
    environment = _environment_status()
    capabilities = {
        name: {
            "enabled": name in enabled,
            "required": name in normalized["required_modalities"],
            "available": bool(environment[name]["available"]),
            "reason": str(environment[name]["reason"]),
        }
        for name in OPTIONAL_VERIFIER_MODALITIES
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "source": POLICY_SOURCE,
        "workspace": str(workspace),
        "config_path": str(_policy_path(workspace)),
        "enabled_modalities": enabled,
        "required_modalities": list(normalized["required_modalities"]),
        "custom_scripts": list(normalized["custom_scripts"]),
        "capabilities": capabilities,
        "environment": environment,
        "safety": {
            "optional_by_default": True,
            "internal_harness_owned": False,
            "executes_verifiers": False,
            "requires_explicit_user_enablement": True,
        },
    }


def verifier_policy_to_gate_policy(policy: dict[str, Any]) -> dict[str, Any]:
    """Return the Run Ledger gate-policy fragment for a verifier policy.

    This function is intentionally pure so harnesses and runtime writers can
    consume the same platform policy without depending on UI or
    internal-harness fields.
    """

    enabled = _string_list(policy.get("enabled_modalities"))
    required = _string_list(policy.get("required_modalities"))
    raw_custom_scripts = policy.get("custom_scripts")
    custom_scripts = raw_custom_scripts if isinstance(raw_custom_scripts, list) else []
    return {
        "source": POLICY_SOURCE,
        "enabled_evidence_modalities": enabled,
        "required_evidence_modalities": required,
        "custom_scripts": list(custom_scripts),
    }


def _task_text(command: CompileEvidencePolicyCommandV1) -> str:
    parts = [
        command.project_type,
        command.language,
        " ".join(command.target_files),
        " ".join(command.acceptance_criteria),
    ]
    return "\n".join(parts).lower()


def _infer_project_profile(command: CompileEvidencePolicyCommandV1) -> str:
    project_type = command.project_type
    text = _task_text(command)
    if project_type:
        return project_type
    if any(term in text for term in ("canvas", "webgl", "html5", "interactive_visual", "browser")):
        return "web_ui"
    if any(term in text for term in ("game", "游戏", "physics", "collision", "fps")):
        return "game"
    if any(term in text for term in ("api", "service", "endpoint", "health check", "openapi")):
        return "api_service"
    if any(term in text for term in ("mobile", "android", "ios", "device", "apk", "ipa")):
        return "mobile_app"
    if any(term in text for term in ("plugin", "extension", "host lifecycle")):
        return "plugin_platform"
    if any(path.endswith((".html", ".tsx", ".jsx", ".vue", ".svelte")) for path in command.target_files):
        return "web_ui"
    return "generic"


def _add_unique(target: list[str], *items: str) -> None:
    for item in items:
        token = str(item or "").strip().lower()
        if token and token not in target:
            target.append(token)


def _compile_required_modalities(command: CompileEvidencePolicyCommandV1, profile: str) -> tuple[list[str], list[str]]:
    required: list[str] = []
    advisory: list[str] = []
    _add_unique(required, "qa")
    if command.target_files:
        _add_unique(required, "code", "tool_receipt")
    text = _task_text(command)
    if any(term in text for term in ("build", "test", "lint", "compile", "npm ", "pytest", "go test", "cargo")):
        _add_unique(required, "command")
    if profile in {"web_ui", "interactive_visual", "frontend", "html5_canvas"}:
        _add_unique(required, "browser")
        _add_unique(advisory, "visual", "accessibility", "performance")
    elif profile in {"game", "game_loop", "simulation"}:
        _add_unique(required, "domain")
        _add_unique(advisory, "visual", "performance", "browser", "device")
    elif profile in {"api_service", "microservice"}:
        _add_unique(required, "api_contract", "integration")
        _add_unique(advisory, "security", "performance")
    elif profile in {"mobile_app", "desktop_app"}:
        _add_unique(required, "command")
        _add_unique(advisory, "device", "visual")
    elif profile in {"plugin_platform", "plugin"}:
        _add_unique(required, "plugin_compat", "security", "api_contract")
    _add_unique(required, *command.explicit_required_modalities)
    _add_unique(advisory, *command.explicit_advisory_modalities)
    required = [item for item in required if item in SUPPORTED_EVIDENCE_MODALITIES]
    advisory = [item for item in advisory if item in SUPPORTED_EVIDENCE_MODALITIES and item not in required]
    return required, advisory


def compile_evidence_policy(command: CompileEvidencePolicyCommandV1) -> EvidencePolicyResultV1:
    """Compile a QA evidence policy without executing any verifier.

    The compiler must run before Director execution. QA later compares this
    declaration with Run Ledger evidence instead of inventing requirements at
    verdict time.
    """

    if not isinstance(command, CompileEvidencePolicyCommandV1):
        raise TypeError("command must be CompileEvidencePolicyCommandV1")
    workspace = Path(command.workspace).expanduser().resolve()
    persisted_policy = read_verifier_policy(ReadVerifierPolicyQueryV1(workspace=str(workspace))).policy
    enabled = _string_list(persisted_policy.get("enabled_modalities"))
    environment = _environment_status()
    profile = _infer_project_profile(command)
    required, advisory = _compile_required_modalities(command, profile)
    persisted_required = _string_list(persisted_policy.get("required_modalities"))
    for modality in persisted_required:
        _add_unique(required, modality)
    hard_required = set(_string_list(command.explicit_required_modalities)) | set(persisted_required)
    effective_required: list[str] = []
    waived_modalities: list[dict[str, str]] = []
    unavailable_required_blockers = []
    for modality in required:
        status = environment.get(modality)
        available = bool(status.get("available")) if isinstance(status, dict) else False
        reason = str(status.get("reason") if isinstance(status, dict) else "modality is not available")
        if available:
            _add_unique(effective_required, modality)
            continue
        if modality in hard_required:
            _add_unique(effective_required, modality)
            unavailable_required_blockers.append({"modality": modality, "reason": reason})
            continue
        waived_modalities.append({"modality": modality, "reason": reason})
        _add_unique(advisory, modality)
    required = effective_required
    advisory = [item for item in advisory if item not in required]
    enabled_all = list(dict.fromkeys([*enabled, *required]))
    inputs = {
        "workspace": str(workspace),
        "task_id": command.task_id,
        "run_id": command.run_id,
        "project_profile": profile,
        "language": command.language,
        "target_files": list(command.target_files),
        "acceptance_criteria": list(command.acceptance_criteria),
        "explicit_required_modalities": list(command.explicit_required_modalities),
        "explicit_advisory_modalities": list(command.explicit_advisory_modalities),
        "risk_level": command.risk_level,
        "persisted_policy_hash": _stable_hash(persisted_policy),
    }
    policy = {
        "schema_version": "evidence_policy.v1",
        "source": "control_plane.verifier_policy.evidence_policy_compiler",
        "workspace": str(workspace),
        "run_id": command.run_id,
        "task_id": command.task_id,
        "project_profile": profile,
        "risk_level": command.risk_level,
        "enabled_evidence_modalities": enabled_all,
        "required_evidence_modalities": required,
        "advisory_modalities": advisory,
        "waived_modalities": waived_modalities,
        "unavailable_required_blockers": unavailable_required_blockers,
        "compiler_inputs_hash": _stable_hash(inputs),
        "environment": environment,
    }
    policy["policy_hash"] = _stable_hash(policy)
    policy["gate_policy"] = {
        "source": policy["source"],
        "enabled_evidence_modalities": enabled_all,
        "required_evidence_modalities": required,
        "advisory_modalities": advisory,
        "unavailable_required_blockers": unavailable_required_blockers,
        "policy_hash": policy["policy_hash"],
    }
    return EvidencePolicyResultV1(policy=policy)


def _rejected_command_policy(
    query: EvaluateVerifierCommandPolicyQueryV1,
    *,
    error_code: str,
    detail: str,
) -> VerifierCommandPolicyDecisionV1:
    return VerifierCommandPolicyDecisionV1(
        authorized=False,
        error_code=error_code,
        detail=detail,
        profile_id="",
        normalized_argv=query.argv,
        normalized_cwd=query.cwd,
        input_obligation_ids=query.input_obligation_ids,
        executable_path="",
        executable_realpath="",
        executable_hash="",
        policy_decision_hash="",
    )


def _custom_script_profile(
    query: EvaluateVerifierCommandPolicyQueryV1,
    policy: dict[str, Any],
) -> tuple[str, str, str]:
    """Return custom profile id, error code, and bound content hash."""

    if not _available_from_env("KERNELONE_CUSTOM_VERIFIER_SCRIPTS_ENABLED"):
        return "", "untrusted_verifier_command", ""
    if "custom_script" not in _string_list(policy.get("enabled_modalities")):
        return "", "untrusted_verifier_command", ""
    argv = query.argv
    executable = Path(argv[0].replace("\\", "/")).name.casefold()
    if executable in {"python", "python3", "py", "node", "bash", "sh", "pwsh", "powershell"}:
        candidate = argv[1] if len(argv) == 2 else ""
    else:
        candidate = argv[0] if len(argv) == 1 else ""
    if not candidate:
        return "", "untrusted_verifier_command", ""
    try:
        candidate_path = _normalize_script_path(candidate)
    except ControlPlaneVerifierPolicyV1Error:
        return "", "untrusted_verifier_command", ""
    if query.cwd != ".":
        candidate_path = _normalize_script_path(f"{query.cwd}/{candidate_path}")
    scripts = policy.get("custom_scripts")
    rows = scripts if isinstance(scripts, list) else []
    for row in rows:
        if not isinstance(row, dict) or not bool(row.get("enabled", True)):
            continue
        if str(row.get("modality") or "custom_script").strip().lower() not in {"custom_script", query.modality}:
            continue
        if str(row.get("path") or "") != candidate_path:
            continue
        expected_hash = _normalize_optional_sha256(row.get("content_sha256"))
        if not expected_hash:
            return "", "custom_verifier_unpinned", ""
        absolute_path = (Path(query.workspace).expanduser().resolve() / candidate_path).resolve()
        workspace = Path(query.workspace).expanduser().resolve()
        if workspace not in absolute_path.parents or not absolute_path.is_file():
            return "", "custom_verifier_missing", ""
        actual_hash = hashlib.sha256(absolute_path.read_bytes()).hexdigest()
        if actual_hash != expected_hash:
            return "", "custom_verifier_content_drift", actual_hash
        return f"custom_script:{row.get('id') or Path(candidate_path).stem!s}", "", actual_hash
    return "", "untrusted_verifier_command", ""


def evaluate_verifier_command_policy(
    query: EvaluateVerifierCommandPolicyQueryV1,
) -> VerifierCommandPolicyDecisionV1:
    """Evaluate a command proposal without minting execution authority.

    The decision binds the immutable completion identity and complete declared
    verifier input closure. ExecutionBroker must re-evaluate this query while
    atomically consuming its own one-use launch capability.
    """

    if type(query) is not EvaluateVerifierCommandPolicyQueryV1:
        raise TypeError("query must be exact EvaluateVerifierCommandPolicyQueryV1")
    profile_id, error_code = resolve_builtin_profile(query.modality, query.argv)
    policy = read_verifier_policy(ReadVerifierPolicyQueryV1(workspace=query.workspace)).policy
    custom_content_hash = ""
    package_script_evidence_hash, package_script_error = _node_script_content_evidence(query)
    if profile_id and package_script_error:
        return _rejected_command_policy(
            query,
            error_code=package_script_error,
            detail="package script is absent, mutable-only, watch-mode, or non-proving",
        )
    if not profile_id and error_code != "non_proving_verifier_command":
        profile_id, custom_error, custom_content_hash = _custom_script_profile(query, policy)
        if profile_id:
            error_code = ""
        elif custom_error != "untrusted_verifier_command":
            error_code = custom_error
    if not profile_id:
        return _rejected_command_policy(
            query,
            error_code=error_code or "untrusted_verifier_command",
            detail="verifier command does not match an enabled platform-owned proof profile",
        )
    executable_path, executable_realpath, executable_hash, executable_error = _resolve_executable_identity(
        query,
        custom_script=profile_id.startswith("custom_script:"),
    )
    if executable_error:
        return _rejected_command_policy(
            query,
            error_code=executable_error,
            detail="verifier executable is missing, ephemeral, target-controlled, or outside trusted roots",
        )
    decision_payload = {
        "schema_version": "verifier_command_policy_decision.v1",
        "source": POLICY_SOURCE,
        "workspace": str(Path(query.workspace).expanduser().resolve()),
        "project_id": query.project_id,
        "run_id": query.run_id,
        "task_id": query.task_id,
        "completion_contract_hash": query.completion_contract_hash,
        "verifier_obligation_id": query.verifier_obligation_id,
        "command_authority_hash": query.command_authority_hash,
        "modality": query.modality,
        "argv": list(query.argv),
        "cwd": query.cwd,
        "input_obligation_ids": list(query.input_obligation_ids),
        "profile_id": profile_id,
        "executable_path": executable_path,
        "executable_realpath": executable_realpath,
        "executable_hash": executable_hash,
        "custom_content_hash": custom_content_hash,
        "package_script_evidence_hash": package_script_evidence_hash,
        "persisted_policy_hash": _stable_hash(policy),
    }
    return VerifierCommandPolicyDecisionV1(
        authorized=True,
        error_code="",
        detail="verifier command matches a platform-owned proof profile",
        profile_id=profile_id,
        normalized_argv=query.argv,
        normalized_cwd=query.cwd,
        input_obligation_ids=query.input_obligation_ids,
        executable_path=executable_path,
        executable_realpath=executable_realpath,
        executable_hash=executable_hash,
        policy_decision_hash=_stable_hash(decision_payload),
    )


def evaluate_verifier_proof(
    *,
    profile_id: str,
    modality: str,
    exit_code: int | None,
    timed_out: bool,
    output_bytes: bytes,
) -> bool:
    """Evaluate physical verifier output using platform-owned profile semantics."""

    return evaluate_builtin_proof(profile_id, modality, exit_code, timed_out, output_bytes)


def _write_config(workspace: Path, config: dict[str, Any]) -> None:
    fs = KernelFileSystem(str(workspace), get_default_adapter())
    fs.workspace_write_text_atomic(
        POLICY_RELATIVE_PATH,
        json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def read_verifier_policy(query: ReadVerifierPolicyQueryV1) -> VerifierPolicyResultV1:
    """Read platform verifier policy for the workspace."""

    workspace = Path(query.workspace).expanduser().resolve()
    config = _read_config(_policy_path(workspace))
    return VerifierPolicyResultV1(policy=_policy_payload(workspace, config))


def update_verifier_policy(command: UpdateVerifierPolicyCommandV1) -> VerifierPolicyResultV1:
    """Persist platform verifier policy for the workspace."""

    workspace = Path(command.workspace).expanduser().resolve()
    current = _read_config(_policy_path(workspace))
    updates = {
        key: value
        for key, value in {
            "browser_enabled": command.browser_enabled,
            "visual_enabled": command.visual_enabled,
            "llm_judge_enabled": command.llm_judge_enabled,
            "custom_script_enabled": command.custom_script_enabled,
        }.items()
        if value is not None
    }
    next_config = {
        **current,
        **updates,
        "required_modalities": list(command.required_modalities),
        "custom_scripts": list(command.custom_scripts),
    }
    normalized = _normalize_config(next_config)
    _ensure_required_modalities_available(normalized)
    _write_config(workspace, normalized)
    return VerifierPolicyResultV1(policy=_policy_payload(workspace, normalized))


__all__ = [
    "POLICY_RELATIVE_PATH",
    "POLICY_SOURCE",
    "compile_evidence_policy",
    "evaluate_verifier_command_policy",
    "evaluate_verifier_proof",
    "read_verifier_policy",
    "update_verifier_policy",
    "verifier_policy_to_gate_policy",
]
