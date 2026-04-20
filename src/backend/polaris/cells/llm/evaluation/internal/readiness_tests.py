"""Readiness Tests Use Case

就绪性测试用例，输出前端兼容的 legacy report 结构。

✅ MIGRATION COMPLETED (2026-04-09): AIStreamEvent 已迁移到 cell-defined 类型。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from polaris.cells.llm.evaluation.internal.constants import REQUIRED_SUITES_BY_ROLE
from polaris.cells.llm.evaluation.internal.runner import AIStreamEvent, EvaluationRequest, EvaluationRunner

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from polaris.bootstrap.config import Settings


def _normalize_role(role: str | None) -> str:
    token = str(role or "").strip().lower()
    return token or "default"


def _normalize_suites(role: str, suites: list[str] | None) -> list[str]:
    normalized_role = _normalize_role(role)
    if not suites:
        return list(REQUIRED_SUITES_BY_ROLE.get(normalized_role, REQUIRED_SUITES_BY_ROLE["default"]))
    out: list[str] = []
    for suite in suites:
        item = str(suite or "").strip().lower()
        if not item or item in out:
            continue
        out.append(item)
    return out


def _suite_to_legacy(suite: dict[str, Any]) -> dict[str, Any]:
    total_cases = int(suite.get("total_cases") or 0)
    passed_cases = int(suite.get("passed_cases") or 0)
    failed_cases = int(suite.get("failed_cases") or max(0, total_cases - passed_cases))
    ok = passed_cases >= total_cases if total_cases > 0 else False

    cases: list[dict[str, Any]] = []
    for case in suite.get("results") or []:
        if not isinstance(case, dict):
            continue
        case_id = str(case.get("case_id") or case.get("id") or "unknown")
        output = str(case.get("output") or "")
        item: dict[str, Any] = {
            "id": case_id,
            "ok": bool(case.get("passed")),
            "passed": bool(case.get("passed")),
            "output": output,
            "score": float(case.get("score") or 0.0),
        }
        if case.get("error"):
            item["error"] = str(case.get("error"))
            item["reason"] = str(case.get("error"))
        if case.get("latency_ms") is not None:
            item["latency_ms"] = int(case.get("latency_ms") or 0)
        cases.append(item)

    details: dict[str, Any] = {
        "total_cases": total_cases,
        "passed_cases": passed_cases,
        "failed_cases": failed_cases,
    }
    if suite.get("total_latency_ms") is not None:
        details["latency_ms"] = int(suite.get("total_latency_ms") or 0)

    return {
        "ok": ok,
        "status": "PASS" if ok else "FAIL",
        "cases": cases,
        "details": details,
    }


def _report_to_legacy(
    report: dict[str, Any],
    *,
    role: str,
    provider_id: str,
    model: str,
) -> dict[str, Any]:
    suites_payload: dict[str, Any] = {}
    for suite in report.get("suites") or []:
        if not isinstance(suite, dict):
            continue
        suite_name = str(suite.get("suite_name") or "").strip().lower()
        if not suite_name:
            continue
        suites_payload[suite_name] = _suite_to_legacy(suite)

    raw_summary = report.get("summary")
    summary: dict[str, Any] = raw_summary if isinstance(raw_summary, dict) else {}
    ready = bool(summary.get("ready"))
    grade = str(summary.get("grade") or ("PASS" if ready else "FAIL")).upper()

    output = {
        "schema_version": 1,
        "test_run_id": str(report.get("run_id") or ""),
        "timestamp": report.get("timestamp"),
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
    if report.get("report_id"):
        output["report_id"] = report.get("report_id")
    return output


async def run_readiness_tests(
    workspace: str,
    settings: Settings,
    provider_id: str,
    model: str,
    role: str = "default",
    suites: list[str] | None = None,
    provider_cfg: dict[str, Any] | None = None,
    *,
    api_key: str | None = None,
    evaluation_mode: str | None = None,
    skip_persistence: bool = False,
    extra_headers: dict[str, str] | None = None,
    env_overrides: dict[str, str] | None = None,
    prompt_override: str | None = None,
) -> dict[str, Any]:
    """运行就绪性测试并返回兼容 legacy 的报告结构。"""
    del evaluation_mode, extra_headers, env_overrides, prompt_override

    normalized_role = _normalize_role(role)
    normalized_suites = _normalize_suites(normalized_role, suites)

    runner = EvaluationRunner(workspace=workspace, settings=settings)
    request = EvaluationRequest(
        provider_id=provider_id,
        model=model,
        role=normalized_role,
        suites=normalized_suites,
        context={
            "provider_cfg": provider_cfg or {},
            "api_key": api_key,
        },
        options={"update_index": not skip_persistence},
    )

    report = await runner.run(request)
    return _report_to_legacy(
        report.to_dict(),
        role=normalized_role,
        provider_id=provider_id,
        model=model,
    )


async def run_readiness_tests_streaming(
    workspace: str,
    settings: Settings,
    provider_id: str,
    model: str,
    role: str = "default",
    suites: list[str] | None = None,
    provider_cfg: dict[str, Any] | None = None,
    *,
    api_key: str | None = None,
    evaluation_mode: str | None = None,
    skip_persistence: bool = False,
    extra_headers: dict[str, str] | None = None,
    env_overrides: dict[str, str] | None = None,
    prompt_override: str | None = None,
) -> AsyncGenerator[AIStreamEvent, None]:
    """流式运行就绪性测试（兼容事件格式）。"""
    normalized_role = _normalize_role(role)
    normalized_suites = _normalize_suites(normalized_role, suites)

    yield AIStreamEvent.complete(
        {
            "event": "evaluation_start",
            "role": normalized_role,
            "provider_id": provider_id,
            "model": model,
            "suites": normalized_suites,
        }
    )

    report = await run_readiness_tests(
        workspace=workspace,
        settings=settings,
        provider_id=provider_id,
        model=model,
        role=normalized_role,
        suites=normalized_suites,
        provider_cfg=provider_cfg,
        api_key=api_key,
        evaluation_mode=evaluation_mode,
        skip_persistence=skip_persistence,
        extra_headers=extra_headers,
        env_overrides=env_overrides,
        prompt_override=prompt_override,
    )

    raw_suites_payload = report.get("suites")
    suites_payload: dict[str, Any] = raw_suites_payload if isinstance(raw_suites_payload, dict) else {}
    for suite_name, suite_result in suites_payload.items():
        yield AIStreamEvent.chunk_event(f"suite_start:{suite_name}")
        suite_ok = bool(suite_result.get("ok")) if isinstance(suite_result, dict) else False
        yield AIStreamEvent.chunk_event(f"suite_result:{suite_name}:{'PASS' if suite_ok else 'FAIL'}")

    yield AIStreamEvent.complete(
        {
            "event": "evaluation_complete",
            "report": report,
        }
    )


__all__ = ["run_readiness_tests", "run_readiness_tests_streaming"]
