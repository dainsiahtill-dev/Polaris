"""Role LLM connectivity probe for the agentic-eval CLI.

Sends a minimal message to each role and verifies the role can respond
without errors before a benchmark run proceeds.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from ._coerce import _as_dict, _as_list

__all__ = [
    "_ALL_PROBE_ROLES",
    "_DEFAULT_PROBE_TIMEOUT",
    "_PROBE_MESSAGE",
    "_print_probe_human",
    "_probe_role",
    "_run_probe_async",
    "run_probe",
]

# Probe timeout per role (seconds)
_DEFAULT_PROBE_TIMEOUT = 30.0
_PROBE_MESSAGE = "Hello, respond with just the word 'ok'."
_ALL_PROBE_ROLES = ("pm", "architect", "chief_engineer", "director", "qa")


async def _probe_role(workspace: str, role: str, timeout_seconds: float) -> dict[str, Any]:
    """Probe a single role's LLM accessibility.

    Sends a minimal message and verifies the role can respond without errors.
    Uses validate_output=False to avoid false negatives from role-specific
    output schema validation (the probe only checks connectivity).

    Returns:
        {"role": str, "ok": bool, "error": str or None,
         "output_preview": str or None, "duration_ms": int}
    """
    started_at = time.perf_counter()
    error_msg: str | None = None
    output_preview: str | None = None
    ok_flag = False

    try:
        from polaris.cells.roles.runtime.public.contracts import (
            ExecuteRoleSessionCommandV1,
        )
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        result = await asyncio.wait_for(
            RoleRuntimeService().execute_role_session(
                ExecuteRoleSessionCommandV1(
                    role=role,
                    session_id=f"agentic-eval-probe-{role}-{uuid4().hex}",
                    workspace=workspace,
                    user_message=_PROBE_MESSAGE,
                    domain="general",
                    metadata={
                        "role_runtime_required": True,
                        "cognitive_runtime_required": True,
                        "context_os_expected": True,
                        "source": "agentic_eval_probe",
                        "runtime_fallback_used": False,
                        "fallback_policy": "fail_closed",
                    },
                    stream=False,
                    host_kind="agentic_eval_probe",
                )
            ),
            timeout=timeout_seconds,
        )
        result_dict = {
            "response": str(getattr(result, "output", "") or ""),
            "thinking": str(getattr(result, "thinking", "") or ""),
            "error": str(getattr(result, "error_message", "") or ""),
            "metadata": _as_dict(getattr(result, "metadata", {})),
        }
        ok_flag = not bool(result_dict.get("error"))
        output_preview = str(result_dict.get("response") or "").strip()[:120]
        error_text = str(result_dict.get("error") or "").strip()
        if error_text:
            error_msg = error_text
        elif not output_preview:
            error_msg = "empty response from role"
        # Resolve provider/model from runtime config (authoritative binding)
        from polaris.kernelone.llm.runtime_config import RuntimeConfigManager

        role_cfg = RuntimeConfigManager().get_role_config(role)
        if role_cfg:
            provider_id = role_cfg.provider_id
            model_name = role_cfg.model
        else:
            metadata = _as_dict(result_dict.get("metadata"))
            provider_id = str(metadata.get("provider") or metadata.get("provider_type") or "unknown").strip()
            model_name = str(metadata.get("model") or metadata.get("llm_model") or "unknown").strip()
    except asyncio.TimeoutError:
        error_msg = f"timeout after {timeout_seconds}s"
        provider_id = "unknown"
        model_name = "unknown"
    except (RuntimeError, ValueError) as exc:
        error_msg = f"exception: {exc}"
        provider_id = "unknown"
        model_name = "unknown"

    duration_ms = round((time.perf_counter() - started_at) * 1000)
    return {
        "role": role,
        "ok": ok_flag,
        "error": error_msg,
        "output_preview": output_preview,
        "duration_ms": duration_ms,
        "provider": provider_id,
        "model": model_name,
    }


async def _run_probe_async(
    workspace: str,
    roles: tuple[str, ...] | None = None,
    timeout_seconds: float = _DEFAULT_PROBE_TIMEOUT,
) -> dict[str, Any]:
    """Run probe for all specified roles concurrently.

    Args:
        workspace: Workspace directory path.
        roles: Tuple of role names to probe. Defaults to all 5 roles.
        timeout_seconds: Timeout per individual role probe.

    Returns:
        {"ok": bool, "roles": {role: probe_result}, "failed_roles": [role],
         "passed_roles": [role]}
    """
    target_roles = tuple(roles) if roles else _ALL_PROBE_ROLES

    tasks = [_probe_role(workspace, role, timeout_seconds) for role in target_roles]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    role_results: dict[str, dict[str, Any]] = {}
    failed_roles: list[str] = []
    passed_roles: list[str] = []

    for role, result in zip(target_roles, results, strict=True):
        if isinstance(result, Exception):
            role_results[role] = {
                "role": role,
                "ok": False,
                "error": f"exception: {result}",
                "output_preview": None,
                "duration_ms": 0,
            }
            failed_roles.append(role)
        else:
            role_results[role] = result  # type: ignore[assignment]
            if isinstance(result, dict) and result.get("ok"):
                passed_roles.append(role)
            else:
                failed_roles.append(role)

    all_ok = len(failed_roles) == 0
    return {
        "ok": all_ok,
        "roles": role_results,
        "failed_roles": failed_roles,
        "passed_roles": passed_roles,
    }


def run_probe(
    workspace: str,
    roles: list[str] | tuple[str, ...] | None = None,
    timeout_seconds: float = _DEFAULT_PROBE_TIMEOUT,
    output_format: str = "human",
) -> dict[str, Any]:
    """Synchronous entry point for the role probe.

    Args:
        workspace: Workspace directory path.
        roles: List or tuple of role names to probe. Defaults to all 5 roles.
        timeout_seconds: Timeout per role probe (default 30s).
        output_format: "human" or "json".

    Returns:
        Dict with overall ok status and per-role results. Exits process
        with non-zero code if any role fails when output_format is "human".
    """
    # Normalise roles to tuple
    if roles is None:
        target_roles: tuple[str, ...] = _ALL_PROBE_ROLES
    elif isinstance(roles, list):
        target_roles = tuple(roles)
    else:
        target_roles = roles

    probe_result = asyncio.run(_run_probe_async(workspace, target_roles, timeout_seconds))

    if output_format == "json":
        print(json.dumps(probe_result, ensure_ascii=False, indent=2))
    else:
        _print_probe_human(probe_result)

    return probe_result


def _print_probe_human(probe_result: Mapping[str, Any]) -> None:
    """Print probe results in human-readable format."""
    role_results = _as_dict(probe_result.get("roles"))
    failed_roles = _as_list(probe_result.get("failed_roles"))
    passed_roles = _as_list(probe_result.get("passed_roles"))

    print(f"[agentic-eval probe] status={'PASS' if probe_result.get('ok') else 'FAIL'}")
    print(
        f"[agentic-eval probe] passed={len(passed_roles)}/{len(role_results)} "
        f"failed={len(failed_roles)}/{len(role_results)}"
    )

    for role, result in role_results.items():
        result_dict = _as_dict(result)
        status_tag = "PASS" if result_dict.get("ok") else "FAIL"
        duration_ms = result_dict.get("duration_ms", 0)
        provider = result_dict.get("provider") or "unknown"
        model = result_dict.get("model") or "unknown"
        error = result_dict.get("error")
        preview = result_dict.get("output_preview")
        binding = f"{provider}/{model}"
        error_suffix = f" error={error}" if error else ""
        preview_suffix = f" output={preview!r}" if preview else ""
        print(
            f"[agentic-eval probe] {role}: {status_tag} "
            f"binding={binding} duration_ms={duration_ms}{error_suffix}{preview_suffix}"
        )

    if failed_roles:
        print(f"[agentic-eval probe] failed_roles={','.join(failed_roles)}")
    else:
        print("[agentic-eval probe] all roles accessible")
