"""Platform verifier policy service.

This cell owns configuration for optional evidence modalities. It deliberately
does not execute Browser, visual-LLM, or user-script verifiers; execution
providers consume this public policy later and must emit Run Ledger evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from polaris.cells.control_plane.verifier_policy.public.contracts import (
    OPTIONAL_VERIFIER_MODALITIES,
    SUPPORTED_EVIDENCE_MODALITIES,
    CompileEvidencePolicyCommandV1,
    ControlPlaneVerifierPolicyV1Error,
    EvidencePolicyResultV1,
    ReadVerifierPolicyQueryV1,
    UpdateVerifierPolicyCommandV1,
    VerifierPolicyResultV1,
)

SCHEMA_VERSION = 1
POLICY_SOURCE = "control_plane.verifier_policy"
POLICY_RELATIVE_PATH = ".polaris/verifier_policy.json"


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


def _available_from_env(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _normalize_script_path(value: Any) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        raise ControlPlaneVerifierPolicyV1Error("custom script path must be non-empty")
    if raw.startswith("/") or raw.startswith("../") or "/../" in raw or raw == "..":
        raise ControlPlaneVerifierPolicyV1Error("custom script path must be workspace-relative")
    return raw.lstrip("./")


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


def _write_config(path: Path, config: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(path)


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
    _write_config(_policy_path(workspace), normalized)
    return VerifierPolicyResultV1(policy=_policy_payload(workspace, normalized))


__all__ = [
    "POLICY_RELATIVE_PATH",
    "POLICY_SOURCE",
    "compile_evidence_policy",
    "read_verifier_policy",
    "update_verifier_policy",
    "verifier_policy_to_gate_policy",
]
