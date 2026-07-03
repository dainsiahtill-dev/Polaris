"""Prompt assembly helpers for RoleExecutionKernel turns."""

from __future__ import annotations

import logging
from typing import Any

from polaris.cells.roles.kernel.internal.kernel.request_tool_gating import tool_contract_requires_single_batch
from polaris.cells.roles.profile.public.service import RoleProfile, RoleTurnRequest

logger = logging.getLogger(__name__)


def build_system_prompt_for_request(
    *,
    prompt_builder: Any,
    profile: RoleProfile,
    request: RoleTurnRequest,
    prompt_appendix: str,
    workspace: str,
) -> str:
    """Build a role system prompt with per-turn prompt layer controls."""
    domain = str(getattr(request, "domain", "") or "").strip().lower() or "code"
    context_override = getattr(request, "context_override", None)
    request_message = str(getattr(request, "message", "") or "")
    prompt_layer_options = resolve_prompt_layer_options(context_override, message=request_message)
    effective_prompt_appendix = append_prompt_profiles_for_request(
        profile=profile,
        request=request,
        prompt_appendix=prompt_appendix,
        context_override=context_override,
        message=request_message,
        workspace=workspace,
    )
    try:
        if prompt_layer_options:
            # Explicit kwargs prevent stray option keys from binding to the
            # prompt builder's positional parameters.
            return prompt_builder.build_system_prompt(
                profile,
                effective_prompt_appendix,
                domain=domain,
                message=request_message,
                include_working_memory_contract=prompt_layer_options.get("include_working_memory_contract", True),
                include_tool_policy=prompt_layer_options.get("include_tool_policy", True),
            )
        return prompt_builder.build_system_prompt(
            profile,
            effective_prompt_appendix,
            domain=domain,
            message=request_message,
        )
    except TypeError:
        return prompt_builder.build_system_prompt(profile, effective_prompt_appendix)


def append_prompt_profiles_for_request(
    *,
    profile: RoleProfile,
    request: RoleTurnRequest,
    prompt_appendix: str,
    context_override: Any,
    message: str,
    workspace: str,
) -> str:
    """Append language/task prompt profiles when this turn is an engineering task."""

    if "[POLARIS PROMPT PROFILE]" in str(prompt_appendix or ""):
        return prompt_appendix
    if not should_attach_prompt_profiles(context_override, message=message):
        return prompt_appendix
    try:
        from polaris.cells.roles.kernel.internal.prompt_profiles import build_prompt_profile_appendix

        profile_appendix, audit = build_prompt_profile_appendix(
            workspace=str(getattr(request, "workspace", "") or workspace or ""),
            role_id=str(getattr(profile, "role_id", "") or ""),
            message=message,
            context_override=dict(context_override) if isinstance(context_override, dict) else None,
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.warning("prompt_profile_selection_failed: %s", exc)
        return prompt_appendix

    if isinstance(context_override, dict):
        context_override["prompt_profile_audit"] = audit
        context_override["selected_prompt_profile_ids"] = list(audit.get("selected_prompt_profile_ids") or [])
        context_override["prompt_profile_appendix"] = profile_appendix
    if not profile_appendix:
        return prompt_appendix
    if not str(prompt_appendix or "").strip():
        return profile_appendix
    return f"{prompt_appendix.rstrip()}\n\n{profile_appendix}"


def should_attach_prompt_profiles(context_override: Any, *, message: str) -> bool:
    """Avoid profile bloat for ordinary chat; default it for concrete engineering tasks."""

    if isinstance(context_override, dict):
        delivery_mode = str(context_override.get("delivery_mode") or "").strip().lower()
        codegen_mode = str(context_override.get("director_runtime_codegen_mode") or "").strip().lower()
        if bool(context_override.get("director_runtime_codegen")) and (
            delivery_mode == "propose_patch" or codegen_mode == "proposal_then_apply"
        ):
            return False
        explicit_keys = {
            "prompt_profile",
            "prompt_profile_id",
            "prompt_profile_ids",
            "prompt_profiles",
        }
        if any(key in context_override for key in explicit_keys):
            return True
        engineering_keys = {
            "target_files",
            "files",
            "changed_files",
            "repair_target_files",
            "missing_target_files",
            "director_quality_repair",
            "delivery_mode",
            "task_type",
            "artifact",
            "artifact_type",
            "language",
            "prompt_language",
        }
        if any(key in context_override for key in engineering_keys):
            return True
        metadata = context_override.get("metadata")
        if isinstance(metadata, dict) and any(key in metadata for key in explicit_keys | engineering_keys):
            return True

    message_text = str(message or "")
    message_lower = message_text.lower()
    if "pm task contract /" in message_lower or "chief engineer blueprint" in message_lower:
        return True
    if any(token in message_lower for token in ("typescript", "python", "react", "vue", "rust", "golang")):
        return True
    return any(
        suffix in message_lower
        for suffix in (
            ".py",
            ".ts",
            ".tsx",
            ".js",
            ".jsx",
            ".vue",
            ".go",
            ".rs",
            ".java",
            "package.json",
            "tsconfig.json",
            "pyproject.toml",
        )
    )


def resolve_prompt_layer_options(context_override: Any, *, message: str | None = None) -> dict[str, bool]:
    """Resolve per-turn prompt layer switches from explicit runtime context."""
    if not isinstance(context_override, dict):
        return {}

    delivery_mode = str(context_override.get("delivery_mode") or "").strip().lower()
    codegen_mode = str(context_override.get("director_runtime_codegen_mode") or "").strip().lower()
    forced_tool_name = _forced_tool_choice_name(context_override.get("_transaction_kernel_forced_tool_choice"))
    is_forced_write_turn = forced_tool_name in {
        "append_to_file",
        "edit_blocks",
        "edit_file",
        "repo_apply_diff",
        "write_file",
    }
    message_text = str(message or "")
    message_lower = message_text.lower()
    is_director_codegen_bridge = bool(context_override.get("director_runtime_codegen")) and (
        delivery_mode == "propose_patch" or codegen_mode == "proposal_then_apply"
    )
    is_factory_contract_materialization = (
        "pm task contract /" in message_lower
        and "chief engineer blueprint" in message_lower
        and "请通过运行时正式写入工具完成修改" in message_text
    )
    is_single_batch_execution = (
        delivery_mode in {"materialize_changes", "propose_patch"}
        or tool_contract_requires_single_batch(context_override)
        or is_factory_contract_materialization
        or "materialization quality repair mode" in message_lower
        or "[director_quality_repair:" in message_lower
        or ("artifact quality scan failed" in message_lower and "do not read files first" in message_lower)
    )
    suppress_working_memory = bool(
        context_override.get("suppress_working_memory_contract")
        or context_override.get("_transaction_kernel_suppress_session_patch")
        or is_director_codegen_bridge
        or is_single_batch_execution
        or is_forced_write_turn
    )
    suppress_tool_policy = bool(context_override.get("suppress_tool_policy_prompt") or is_director_codegen_bridge)

    options: dict[str, bool] = {}
    if suppress_working_memory:
        options["include_working_memory_contract"] = False
    if suppress_tool_policy:
        options["include_tool_policy"] = False
    return options


def _forced_tool_choice_name(raw_choice: Any) -> str:
    if isinstance(raw_choice, dict):
        function_payload = raw_choice.get("function")
        if isinstance(function_payload, dict):
            return str(function_payload.get("name") or "").strip().lower()
        return str(raw_choice.get("name") or "").strip().lower()
    return str(raw_choice or "").strip().lower()


__all__ = [
    "append_prompt_profiles_for_request",
    "build_system_prompt_for_request",
    "resolve_prompt_layer_options",
    "should_attach_prompt_profiles",
]
