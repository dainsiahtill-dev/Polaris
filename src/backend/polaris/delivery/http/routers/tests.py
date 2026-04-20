"""Test-related routes for the LLM router."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from polaris.cells.llm.evaluation.public.service import run_readiness_tests as run_llm_tests
from polaris.cells.llm.provider_config.public.contracts import (
    LlmProviderConfigError,
    ProviderConfigValidationError,
    ProviderNotFoundError,
    RoleNotConfiguredError,
)
from polaris.cells.llm.provider_config.public.service import resolve_llm_test_execution_context
from polaris.delivery.http.routers._shared import get_state, require_auth
from polaris.kernelone.storage.io_paths import build_cache_root, resolve_artifact_path

from .sse_utils import create_sse_response, sse_event_generator

if TYPE_CHECKING:
    import asyncio

    from polaris.bootstrap.config import Settings

    from .llm_models import LlmTestPayload

router = APIRouter()
logger = logging.getLogger(__name__)


def _map_provider_config_error(exc: LlmProviderConfigError) -> HTTPException:
    """Map domain config errors to HTTP status codes."""
    if isinstance(exc, ProviderNotFoundError):
        logger.error("Provider not found: %s", exc)
        return HTTPException(status_code=404, detail="internal error")
    if isinstance(exc, RoleNotConfiguredError):
        logger.error("Role not configured: %s", exc)
        return HTTPException(status_code=404, detail="internal error")
    if isinstance(exc, ProviderConfigValidationError):
        logger.error("Provider config validation failed: %s", exc)
        return HTTPException(status_code=400, detail="internal error")
    logger.error("Provider config error: %s", exc)
    return HTTPException(status_code=400, detail="internal error")


@router.post("/llm/test", dependencies=[Depends(require_auth)])
async def llm_test(request: Request, payload: LlmTestPayload) -> dict[str, Any]:
    state = get_state(request)
    workspace_raw = state.settings.workspace
    workspace = str(workspace_raw) if not isinstance(workspace_raw, str) else workspace_raw
    cache_root = build_cache_root(state.settings.ramdisk_root or "", workspace)

    try:
        test_context = resolve_llm_test_execution_context(workspace, cache_root, payload.model_dump())
    except LlmProviderConfigError as exc:
        raise _map_provider_config_error(exc) from exc
    report = await run_llm_tests(
        workspace=workspace,
        settings=state.settings,
        provider_id=test_context.effective_provider_id,
        model=test_context.model,
        role=test_context.role or "connectivity",
        suites=list(test_context.suites),
        evaluation_mode=payload.evaluation_mode,
        api_key=payload.api_key,
        extra_headers=payload.headers,
        env_overrides=payload.env_overrides,
        prompt_override=payload.prompt_override,
        provider_cfg=test_context.provider_cfg if test_context.use_direct_config else None,
        skip_persistence=False,  # Always persist test results (connectivity matters!)
    )
    return report


@router.post("/llm/test/stream", dependencies=[Depends(require_auth)])
async def llm_test_stream(request: Request, payload: LlmTestPayload):
    """Stream LLM test results using Server-Sent Events (SSE)

    This endpoint provides real-time output from LLM tests as they execute,
    allowing the client to see progress for each test suite as it completes.
    Supports connectivity-only tests without role dependency when role='connectivity'.

    Scheme B: When role='connectivity' and base_url is provided, bypasses config loading
    and skips persistence (no ramdisk dependency).
    """
    state = get_state(request)
    workspace_raw = state.settings.workspace
    workspace = str(workspace_raw) if not isinstance(workspace_raw, str) else workspace_raw
    cache_root = build_cache_root(state.settings.ramdisk_root or "", workspace)

    try:
        test_context = resolve_llm_test_execution_context(workspace, cache_root, payload.model_dump())
    except LlmProviderConfigError as exc:
        raise _map_provider_config_error(exc) from exc

    async def _run_tests(queue: asyncio.Queue) -> None:
        role = test_context.role or "connectivity"
        suites = list(test_context.suites)
        await queue.put(
            {
                "type": "start",
                "data": {
                    "role": role,
                    "provider_id": test_context.effective_provider_id,
                    "model": test_context.model,
                    "suites": suites,
                },
            }
        )

        report = await run_llm_tests(
            workspace=workspace,
            settings=state.settings,
            provider_id=test_context.effective_provider_id,
            model=test_context.model,
            role=role,
            suites=suites,
            evaluation_mode=payload.evaluation_mode,
            api_key=payload.api_key,
            extra_headers=payload.headers,
            env_overrides=payload.env_overrides,
            prompt_override=payload.prompt_override,
            provider_cfg=test_context.provider_cfg if test_context.use_direct_config else None,
            skip_persistence=False,
        )
        suites_dict = report.get("suites")
        suites_payload = suites_dict if isinstance(suites_dict, dict) else {}
        for suite_name, suite_result in suites_payload.items():
            await queue.put({"type": "suite_start", "data": {"suite": suite_name}})
            await queue.put(
                {
                    "type": "suite_result",
                    "data": {"suite": suite_name, "result": suite_result},
                }
            )
            await queue.put(
                {
                    "type": "suite_complete",
                    "data": {"suite": suite_name, "result": suite_result},
                }
            )
        await queue.put({"type": "complete", "data": report})

    return create_sse_response(sse_event_generator(_run_tests))


@router.get("/llm/test/{test_run_id}", dependencies=[Depends(require_auth)])
def llm_test_report(request: Request, test_run_id: str) -> dict[str, Any]:
    state = get_state(request)
    workspace_raw = state.settings.workspace
    workspace = str(workspace_raw) if not isinstance(workspace_raw, str) else workspace_raw
    report_path = _resolve_test_path(state.settings, test_run_id, "report", workspace)
    if not report_path or not os.path.isfile(report_path):
        raise HTTPException(status_code=404, detail="report not found")
    try:
        with open(report_path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (RuntimeError, ValueError):
        raise HTTPException(status_code=500, detail="failed to read report")
    return _normalize_report_payload(data)


@router.get("/llm/test/{test_run_id}/transcript", dependencies=[Depends(require_auth)])
def llm_test_transcript(request: Request, test_run_id: str) -> dict[str, Any]:
    state = get_state(request)
    workspace_raw = state.settings.workspace
    workspace = str(workspace_raw) if not isinstance(workspace_raw, str) else workspace_raw
    transcript_path = _resolve_test_path(state.settings, test_run_id, "transcript", workspace)
    if not transcript_path or not os.path.isfile(transcript_path):
        raise HTTPException(status_code=404, detail="transcript not found")
    try:
        with open(transcript_path, encoding="utf-8") as handle:
            content = handle.read()
    except (RuntimeError, ValueError):
        raise HTTPException(status_code=500, detail="failed to read transcript")
    return {"ok": True, "content": content}


def _resolve_test_path(settings: Settings, run_id: str, filename: str, workspace: str) -> str:
    if not re.match(r"^[A-Za-z0-9_.-]+$", run_id or ""):
        raise HTTPException(status_code=400, detail="invalid test run id")
    cache_root = build_cache_root(settings.ramdisk_root or "", workspace)
    candidates: list[str] = []

    if filename == "report":
        # Legacy location
        candidates.append(
            resolve_artifact_path(
                workspace,
                cache_root,
                f"runtime/llm_tests/{run_id}/LLM_TEST_REPORT.json",
            )
        )
        # New evaluation framework location
        candidates.append(
            os.path.join(
                workspace,
                ".polaris",
                "runtime",
                "llm_tests",
                "reports",
                f"{run_id}.json",
            )
        )
    elif filename == "transcript":
        candidates.append(
            resolve_artifact_path(
                workspace,
                cache_root,
                f"runtime/llm_tests/{run_id}/LLM_TEST_TRANSCRIPT.md",
            )
        )
    else:
        candidates.append(resolve_artifact_path(workspace, cache_root, f"runtime/llm_tests/{run_id}/{filename}"))

    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    return candidates[0] if candidates else ""


def _normalize_report_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"ok": False, "error": "invalid report payload"}
    if "test_run_id" in payload and "target" in payload:
        return payload

    run_id = str(payload.get("run_id") or "")
    provider_id = str(payload.get("provider_id") or "")
    model = str(payload.get("model") or "")
    role = str(payload.get("role") or "")
    summary_raw = payload.get("summary")
    summary = summary_raw if isinstance(summary_raw, dict) else {}
    suites = payload.get("suites")

    suites_payload: dict[str, Any] = {}
    if isinstance(suites, dict):
        suites_payload = suites
    elif isinstance(suites, list):
        for item in suites:
            if not isinstance(item, dict):
                continue
            suite_name = str(item.get("suite_name") or item.get("name") or "").strip().lower()
            if not suite_name:
                continue
            total_cases = int(item.get("total_cases") or 0)
            passed_cases = int(item.get("passed_cases") or 0)
            failed_cases = int(item.get("failed_cases") or max(0, total_cases - passed_cases))
            suites_payload[suite_name] = {
                "ok": passed_cases >= total_cases if total_cases > 0 else False,
                "details": {
                    "total_cases": total_cases,
                    "passed_cases": passed_cases,
                    "failed_cases": failed_cases,
                    "latency_ms": int(item.get("total_latency_ms") or 0),
                },
                "cases": item.get("results") if isinstance(item.get("results"), list) else [],
            }

    ready = bool(summary.get("ready"))
    grade = str(summary.get("grade") or ("PASS" if ready else "FAIL")).upper()

    return {
        "schema_version": 1,
        "test_run_id": run_id,
        "timestamp": payload.get("timestamp"),
        "target": {
            "role": role,
            "provider_id": provider_id,
            "model": model,
        },
        "suites": suites_payload,
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "estimated": True,
        },
        "final": {
            "ready": ready,
            "grade": grade,
            "next_action": "proceed" if ready else "fix_failures",
        },
    }
