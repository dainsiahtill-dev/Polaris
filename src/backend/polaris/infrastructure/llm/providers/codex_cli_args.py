from __future__ import annotations

import re
from typing import Any


def _supports_reasoning_effort(model: str) -> bool:
    """Heuristic check for models that accept reasoning.effort config."""
    if not model:
        return False
    lowered = model.strip().lower()
    if lowered.startswith(("gpt-", "o1", "o3", "o4")):
        return True
    return "codex" in lowered


def _build_codex_exec_args(model: str, config: dict[str, Any]) -> list[str]:
    """Build Codex CLI exec arguments based on official documentation

    Reference: https://docs.openai.com/codex/cli/non-interactive

    Args:
        model: Model name to use
        config: Provider configuration

    Returns:
        List of command line arguments for codex exec
    """
    opts = config.get("codex_exec") or {}
    if not isinstance(opts, dict):
        # Default args for Codex CLI with JSON mode (read-only for safety)
        return [
            "exec",
            "--skip-git-repo-check",
            "--model",
            model,
            "--sandbox",
            "read-only",  # Safer default
            "--json",
        ]

    args: list[str] = ["exec"]

    # Working directory (--cd, -C)
    cd = str(opts.get("cd") or "").strip()
    if cd:
        args += ["--cd", cd]

    # Color control (--color) - disable for JSON mode
    color = str(opts.get("color") or "").strip()
    if color and color.lower() in ("always", "never", "auto"):
        args += ["--color", color.lower()]
    else:
        args += ["--color", "never"]  # Default for JSON mode

    # Git repo check (--skip-git-repo-check)
    if bool(opts.get("skip_git_repo_check", True)):
        args.append("--skip-git-repo-check")

    # Sandbox strategy (--sandbox, -s)
    # Default to read-only for safety unless explicitly specified
    sandbox = str(opts.get("sandbox") or "").strip()
    if sandbox:
        valid_sandboxes = ["read-only", "workspace-write", "danger-full-access"]
        if sandbox.lower() in valid_sandboxes:
            args += ["--sandbox", sandbox.lower()]
        else:
            args += ["--sandbox", "read-only"]  # Safe default
    else:
        args += ["--sandbox", "read-only"]  # Safe default

    # Model selection (--model, -m)
    if model:
        args += ["--model", model]

    # JSON mode (--json, --experimental-json)
    json_mode = opts.get("json")
    if json_mode is not False:  # Default to True unless explicitly disabled
        if json_mode == "experimental":
            args.append("--experimental-json")
        else:
            args.append("--json")

    # Approval control was removed from recent codex exec CLI; skip to avoid errors.

    # OSS provider (--oss)
    if bool(opts.get("oss")):
        args.append("--oss")

    # Additional directories (--add-dir)
    add_dirs = opts.get("add_dirs") or []
    if isinstance(add_dirs, (list, tuple)):
        for entry in add_dirs:
            path_value = str(entry or "").strip()
            if path_value:
                args += ["--add-dir", path_value]

    # Images (--image, -i)
    images = opts.get("images") or []
    if isinstance(images, (list, tuple)):
        for entry in images:
            image_value = str(entry or "").strip()
            if image_value:
                args += ["--image", image_value]

    # Output schema (--output-schema)
    output_schema = str(opts.get("output_schema") or "").strip()
    if output_schema:
        args += ["--output-schema", output_schema]

    # Output last message (--output-last-message, -o)
    output_last = str(opts.get("output_last_message") or opts.get("output") or "").strip()
    if output_last:
        args += ["--output-last-message", output_last]

    # Profile selection (--profile, -p)
    profile = str(opts.get("profile") or "").strip()
    if profile:
        args += ["--profile", profile]

    # Config overrides (--config, -c)
    config_overrides = opts.get("config") or opts.get("config_overrides") or []
    if isinstance(config_overrides, (list, tuple)):
        supports_effort = _supports_reasoning_effort(model)
        for entry in config_overrides:
            kv = str(entry or "").strip()
            if kv and "=" in kv:
                key, value = kv.split("=", 1)
                key = key.strip()
                if key in ("model_reasoning_effort", "reasoning.effort") and not supports_effort:
                    continue
                args += ["--config", f"{key.strip()}={value.strip()}"]

    # Special automation flags (mutually exclusive in most cases)

    # YOLO mode (--yolo) - most permissive
    if bool(opts.get("yolo")):
        args.append("--yolo")
        # YOLO implies no need for other safety flags
    # Full auto mode (--full-auto) - automation preset
    elif bool(opts.get("full_auto")):
        args.append("--full-auto")

    return args


_REASONING_EFFORT_RANK = {
    "low": 1,
    "medium": 2,
    "high": 3,
    "xhigh": 4,
}


def _pick_reasoning_effort_fallback(error_text: str) -> str | None:
    """Pick a safe reasoning.effort fallback based on CLI error details."""
    if not error_text:
        return None
    if "reasoning.effort" not in error_text and "reasoning effort" not in error_text:
        return None

    supported: list[str] = []
    match = re.search(r"Supported values are:(.*)$", error_text, re.IGNORECASE | re.DOTALL)
    if match:
        supported = [
            value.lower()
            for value in re.findall(r"'([a-zA-Z0-9_-]+)'", match.group(1))
            if value.lower() in _REASONING_EFFORT_RANK
        ]
    if supported:
        return max(supported, key=lambda effort: _REASONING_EFFORT_RANK[effort])

    mentions = [
        value.lower()
        for value in re.findall(r"'([a-zA-Z0-9_-]+)'", error_text)
        if value.lower() in _REASONING_EFFORT_RANK
    ]
    if "xhigh" in mentions:
        return "high"
    if "high" in mentions:
        return "medium"
    if "medium" in mentions:
        return "low"
    return None


def _set_codex_config_override(args: list[str], key: str, value: str) -> list[str]:
    """Insert or replace a --config key=value override for codex exec."""
    updated: list[str] = []
    replaced = False
    idx = 0
    while idx < len(args):
        item = args[idx]
        if item == "--config" and idx + 1 < len(args):
            kv = str(args[idx + 1])
            if kv.split("=", 1)[0].strip() == key:
                updated.extend(["--config", f"{key}={value}"])
                idx += 2
                replaced = True
                continue
        updated.append(item)
        idx += 1

    if not replaced:
        inserted = False
        for pos, item in enumerate(updated):
            if item == "{prompt}":
                updated = [*updated[:pos], "--config", f"{key}={value}", *updated[pos:]]
                inserted = True
                break
        if not inserted:
            updated.extend(["--config", f"{key}={value}"])

    return updated
