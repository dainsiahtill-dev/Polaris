"""Pure Director dispatch route-audit, coverage, and provider-health helpers.

Extracted from ``OrchestrationStageExecutor`` as part of the incremental
god-class decomposition. Functions that need ``workspace`` access take a
``Path`` argument; everything else is pure.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from polaris.cells.control_plane.run_ledger.public import FailureClassV1

from .factory_ce_evidence import llm_event_error_text
from .factory_deadline_policy import FactoryDeadlineAdmissionV1

_DIRECTOR_PROVIDER_RATE_LIMIT_TOKENS: tuple[str, ...] = (
    "429",
    "rate_limit",
    "rate limit",
    "rate-limited",
    "too many requests",
    "token plan",
    "quota",
    "用量上限",
)
_DIRECTOR_PROVIDER_UNAVAILABLE_TOKENS: tuple[str, ...] = (
    "provider_timeout",
    "request timeout",
    "transport timeout",
    "timed out",
    "circuit_open",
    "circuit breaker is open",
    "circuitopenerror",
)


def build_per_binding_route_events(per_binding: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now_iso = datetime.now(timezone.utc).isoformat()
    events: list[dict[str, Any]] = []
    for entry in per_binding:
        if not isinstance(entry, dict):
            continue
        provider_id = str(entry.get("provider_id") or "").strip()
        model = str(entry.get("model") or "").strip()
        binding_id = str(entry.get("binding_id") or "").strip()
        run_id = str(entry.get("run_id") or "").strip()
        status = str(entry.get("status") or "").strip().lower()
        if not provider_id or not model:
            continue
        event: dict[str, Any] = {
            "event": "llm_route_terminal",
            "role": "director",
            "provider_id": provider_id,
            "model": model,
            "binding_id": binding_id,
            "run_id": run_id,
            "status": status,
            "source": "llm",
            "cache_hit": False,
            "invocation": True,
            "terminal": True,
            "fail_closed": False,
            "timestamp": now_iso,
        }
        if status == "timeout" or entry.get("quarantined"):
            event["timeout_count"] = entry.get("timeout_count", 0)
        if entry.get("quarantined"):
            event["quarantined"] = True
            event["quarantine_reason"] = entry.get("quarantine_reason", "")
        if entry.get("skipped"):
            event["skipped"] = True
            event["skip_reason"] = entry.get("skip_reason", "")
            event["invocation"] = False
            event["fail_closed"] = True
        events.append(event)
    return events


def build_fail_closed_director_route_events(
    *,
    attempts: list[dict[str, Any]],
    stage_signals: list[dict[str, Any]],
    per_binding_route_events: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    try:
        from polaris.cells.factory.pipeline.internal.bench_gates import _norm_text, resolve_expected_llm_bindings
    except (ImportError, RuntimeError):
        return []
    expected = resolve_expected_llm_bindings(("director",))
    configured = expected.get("director") or []
    if not configured:
        return []
    observed_providers: set[str] = set()
    for event in per_binding_route_events or []:
        if not isinstance(event, dict):
            continue
        provider = _norm_text(event.get("provider_id") or event.get("provider"))
        model = _norm_text(event.get("model"))
        if provider and model:
            observed_providers.add(f"{provider}|{model}")
    for attempt in attempts:
        metadata = attempt.get("metadata") if isinstance(attempt, dict) else {}
        if not isinstance(metadata, dict):
            continue
        provider = _norm_text(metadata.get("provider_id") or metadata.get("provider"))
        model = _norm_text(metadata.get("model"))
        if provider and model:
            observed_providers.add(f"{provider}|{model}")
    for signal in stage_signals:
        if not isinstance(signal, dict):
            continue
        detail = str(signal.get("detail") or "")
        for binding in configured:
            provider = _norm_text(binding.get("provider_id") or binding.get("provider"))
            model = _norm_text(binding.get("model"))
            if provider and model and provider in detail and model in detail:
                observed_providers.add(f"{provider}|{model}")
    now_iso = datetime.now(timezone.utc).isoformat()
    fail_closed_events: list[dict[str, Any]] = []
    for binding in configured:
        provider = _norm_text(binding.get("provider_id") or binding.get("provider"))
        model = _norm_text(binding.get("model"))
        binding_id = _norm_text(binding.get("binding_id"))
        key = f"{provider}|{model}"
        if not provider or not model or key in observed_providers:
            continue
        fail_closed_events.append(
            {
                "event": "llm_route_fail_closed",
                "role": "director",
                "provider_id": provider,
                "model": model,
                "binding_id": binding_id,
                "source": "diagnostic",
                "cache_hit": False,
                "invocation": True,
                "terminal": False,
                "fail_closed": True,
                "fail_closed_reason": "no_dispatch_evidence_for_binding",
                "timestamp": now_iso,
            }
        )
    return fail_closed_events


def reclassify_binding_coverage_signals(
    stage_signals: list[dict[str, Any]],
    per_binding_route_events: list[dict[str, Any]],
) -> None:
    if not per_binding_route_events:
        return
    try:
        from polaris.cells.factory.pipeline.internal.bench_gates import _norm_text, resolve_expected_llm_bindings
    except (ImportError, RuntimeError):
        return
    expected = resolve_expected_llm_bindings(("director",))
    configured = expected.get("director") or []
    if not configured:
        return
    observed_loose: set[str] = set()
    for event in per_binding_route_events:
        if not isinstance(event, dict):
            continue
        provider = _norm_text(event.get("provider_id") or event.get("provider"))
        model = _norm_text(event.get("model"))
        if provider and model:
            observed_loose.add(f"{provider}|{model}")
    configured_loose: set[str] = set()
    for binding in configured:
        provider = _norm_text(binding.get("provider_id") or binding.get("provider"))
        model = _norm_text(binding.get("model"))
        if provider and model:
            configured_loose.add(f"{provider}|{model}")
    if not configured_loose or configured_loose != observed_loose:
        return
    has_timeout = any(
        str(ev.get("status") or "").strip().lower() == "timeout"
        for ev in per_binding_route_events
        if isinstance(ev, dict)
    )
    if not has_timeout:
        return
    for i, signal in enumerate(stage_signals):
        if not isinstance(signal, dict):
            continue
        if signal.get("code") != "director.binding_coverage_incomplete":
            continue
        timeout_bindings = [
            str(ev.get("binding_id") or f"{ev.get('provider_id')}|{ev.get('model')}")
            for ev in per_binding_route_events
            if isinstance(ev, dict) and str(ev.get("status") or "").strip().lower() == "timeout"
        ]
        stage_signals[i] = {
            "code": "director.binding_timeout",
            "severity": "error",
            "detail": f"All director bindings have terminal evidence but {len(timeout_bindings)} timed out: {', '.join(timeout_bindings[:8])}",
            "timeout_bindings": timeout_bindings,
            "observed_count": len(per_binding_route_events),
            "multi_route_required": True,
        }
        break


def director_admission_failure_projection(
    admission_decision: FactoryDeadlineAdmissionV1,
) -> tuple[str, str, str, str]:
    """Project one admission rejection without misreporting its cause."""

    reason = str(admission_decision.reason or "").strip()
    blockers = (
        admission_decision.dependency_schedule.blockers
        if reason == "invalid_pm_task_dependency_schedule"
        else admission_decision.budget_plan.blockers
    )
    blocker_detail = "; ".join(str(item) for item in blockers if str(item).strip())
    if reason == "invalid_pm_task_dependency_schedule":
        detail = "Director dispatch rejected an invalid PM task dependency schedule"
        if blocker_detail:
            detail = f"{detail}: {blocker_detail}"
        return (
            "director.dispatch_dependency_schedule_blocker",
            detail,
            "failed",
            "Director dispatch skipped because the PM task dependency schedule is invalid",
        )
    if reason == "no_active_director_tasks":
        return (
            "director.dispatch_no_active_tasks",
            "Director dispatch admission found no active PM tasks remaining",
            "completed",
            "Director dispatch complete: no active PM tasks remain to execute",
        )
    return (
        "director.dispatch_deadline_blocker",
        (
            "Factory deadline does not leave enough budget to start another Director "
            "LLM turn while preserving downstream quality-gate time"
        ),
        "timeout",
        "Director dispatch skipped because factory deadline budget is exhausted",
    )


def director_provider_health_failure_signal_from_events(
    events: list[dict[str, Any]],
) -> dict[str, Any] | None:
    for event in reversed(events):
        if not isinstance(event, dict):
            continue
        if str(event.get("role") or "").strip().lower() != "director":
            continue
        if bool(event.get("skipped")):
            continue
        event_name = str(event.get("event") or "").strip().lower()
        if event_name not in {"llm_error", "call_error", "error"} and not bool(event.get("terminal")):
            continue
        error_text = llm_event_error_text(event)
        if not error_text:
            continue
        lowered = error_text.lower()
        provider_id = str(event.get("provider_id") or "").strip()
        model = str(event.get("model") or "").strip()
        source_path = str(event.get("source_path") or "").strip()
        if any(token in lowered for token in _DIRECTOR_PROVIDER_RATE_LIMIT_TOKENS):
            return {
                "code": "director.provider_rate_limit",
                "severity": "error",
                "detail": "Director LLM provider rate limit/quota failure before tool dispatch",
                "provider_id": provider_id,
                "model": model,
                "source_path": source_path,
                "error_excerpt": error_text[:600],
                "failure_class": FailureClassV1.RESOURCE_BUDGET_EXHAUSTED.value,
                "responsible_layer": "model_provider",
                "repairable_by_director": False,
                "requires_ce_replan": False,
                "requires_pm_revision": False,
            }
        if any(token in lowered for token in _DIRECTOR_PROVIDER_UNAVAILABLE_TOKENS):
            return {
                "code": "director.provider_unavailable",
                "severity": "error",
                "detail": "Director LLM provider transport/circuit failure before tool dispatch",
                "provider_id": provider_id,
                "model": model,
                "source_path": source_path,
                "error_excerpt": error_text[:600],
                "failure_class": FailureClassV1.TEST_ENVIRONMENT_FAILURE.value,
                "responsible_layer": "model_provider",
                "repairable_by_director": False,
                "requires_ce_replan": False,
                "requires_pm_revision": False,
            }
    return None


def director_provider_health_failure_signal(workspace: Path) -> dict[str, Any] | None:
    try:
        from polaris.cells.factory.pipeline.internal.bench_gates import collect_llm_events
    except (ImportError, RuntimeError):
        return None
    try:
        events = collect_llm_events(workspace, None)
    except (RuntimeError, OSError, ValueError, TypeError):
        return None
    return director_provider_health_failure_signal_from_events(events)


def validate_director_binding_coverage(
    workspace: Path,
    additional_events: list[dict[str, Any]] | None = None,
) -> tuple[bool, list[dict[str, Any]]]:
    try:
        from polaris.cells.factory.pipeline.internal.bench_gates import (
            build_llm_route_audit,
            collect_llm_events,
            resolve_expected_llm_bindings,
        )
    except (ImportError, RuntimeError) as exc:
        return False, [
            {
                "code": "director.binding_coverage_audit_unavailable",
                "severity": "error",
                "detail": f"Director binding coverage audit is unavailable: {exc}",
            }
        ]
    expected = resolve_expected_llm_bindings(("director",))
    configured = expected.get("director") or []
    if not configured:
        return True, []
    try:
        events = collect_llm_events(workspace, None)
    except (RuntimeError, OSError, ValueError, TypeError):
        events = []
    if additional_events:
        seen_keys: set[tuple[str, ...]] = set()
        for ev in events:
            key = (
                str(ev.get("event") or ""),
                str(ev.get("provider_id") or ""),
                str(ev.get("model") or ""),
                str(ev.get("binding_id") or ""),
                str(ev.get("run_id") or ""),
            )
            seen_keys.add(key)
        for ev in additional_events:
            if not isinstance(ev, dict):
                continue
            key = (
                str(ev.get("event") or ""),
                str(ev.get("provider_id") or ""),
                str(ev.get("model") or ""),
                str(ev.get("binding_id") or ""),
                str(ev.get("run_id") or ""),
            )
            if key not in seen_keys:
                events.append(ev)
                seen_keys.add(key)
    audit = build_llm_route_audit(
        events, expected_bindings=expected, required_roles=("director",), require_all_director_routes=True
    )
    if audit.get("ok"):
        return True, []
    director_result = audit.get("roles", {}).get("director", {})
    missing = list(director_result.get("missing_bindings") or [])
    observed_count = int(director_result.get("observed_count") or 0)
    fail_closed_count = int(director_result.get("fail_closed_count") or 0)
    signals: list[dict[str, Any]] = []
    if missing:
        signals.append(
            {
                "code": "director.binding_coverage_incomplete",
                "severity": "error",
                "detail": f"Not all configured director bindings produced real LLM evidence. Observed={observed_count}, missing={len(missing)}, fail_closed(diagnostic)={fail_closed_count}. Missing: {', '.join(missing[:8])}",
                "missing_bindings": missing,
                "observed_count": observed_count,
                "fail_closed_count": fail_closed_count,
                "multi_route_required": True,
            }
        )
    elif observed_count == 0:
        signals.append(
            {
                "code": "director.no_real_llm_evidence",
                "severity": "error",
                "detail": "No real LLM terminal evidence found for any configured director binding.",
                "observed_count": 0,
                "fail_closed_count": fail_closed_count,
            }
        )
    else:
        signals.append(
            {
                "code": "director.binding_coverage_failed",
                "severity": "error",
                "detail": str(audit.get("summary") or "Director binding coverage audit failed"),
                "observed_count": observed_count,
                "fail_closed_count": fail_closed_count,
                "multi_route_required": True,
            }
        )
    return False, signals
