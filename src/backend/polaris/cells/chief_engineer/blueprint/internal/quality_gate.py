"""Deterministic quality-gate evaluator for Chief Engineer blueprints.

The quality gate is intentionally pure-Python (no LLM) so it stays
deterministic and fail-closed. It examines a blueprint payload and
emits a :class:`QualityGateResultV1` with blocker / warning / info
buckets.

Rules (Tier-1 baseline):
  Blocker:
    - ``target_files`` missing or empty
    - ``acceptance_criteria`` missing or empty
    - any risk with severity ``blocker`` / ``critical`` whose status is
      ``open`` (read from the caller-supplied risk list)
  Warning:
    - ``execution_checklist`` missing or empty
    - ``dependencies`` empty when ``len(acceptance_criteria) > 3``
    - any risk with severity ``high`` whose status is ``open``
  Info:
    - ``recommendations`` shorter than 2 entries
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from polaris.cells.chief_engineer.blueprint.public.contracts import (
    QualityGateResultV1,
    RiskRecordV1,
    RiskSeverity,
    RiskStatus,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
            elif isinstance(item, Mapping):
                for key in ("path", "file", "title", "text", "id", "value", "name"):
                    raw = item.get(key)
                    if isinstance(raw, str) and raw.strip():
                        out.append(raw.strip())
                        break
        return out
    return []


def _risk_from_dict(payload: Mapping[str, Any]) -> RiskRecordV1 | None:
    try:
        severity_raw = str(payload.get("severity") or "medium").strip().lower()
        status_raw = str(payload.get("status") or "open").strip().lower()
        return RiskRecordV1(
            risk_id=str(payload.get("risk_id") or ""),
            task_id=str(payload.get("task_id") or ""),
            title=str(payload.get("title") or ""),
            severity=RiskSeverity(severity_raw),
            owner=str(payload.get("owner") or ""),
            mitigation=str(payload.get("mitigation") or ""),
            status=RiskStatus(status_raw),
            detected_at=str(payload.get("detected_at") or ""),
        )
    except (ValueError, TypeError):
        return None


def _coerce_risks(risks: Iterable[Any]) -> list[RiskRecordV1]:
    out: list[RiskRecordV1] = []
    for item in risks:
        if isinstance(item, RiskRecordV1):
            out.append(item)
        elif isinstance(item, Mapping):
            record = _risk_from_dict(item)
            if record is not None:
                out.append(record)
    return out


def _semantic_blockers_from_blueprint(blueprint: Mapping[str, Any]) -> list[str]:
    contract = blueprint.get("contract_completeness")
    if not isinstance(contract, Mapping):
        return []

    blockers: list[str] = []
    semantic_blockers = contract.get("semantic_blockers")
    if isinstance(semantic_blockers, (list, tuple)):
        for item in semantic_blockers:
            token = str(item or "").strip()
            if token:
                blockers.append(token)

    alignment = contract.get("semantic_alignment")
    if isinstance(alignment, Mapping) and alignment.get("ready") is False and not blockers:
        blockers.append("semantic_alignment: delivery contract terms do not match CE blueprint handoff fields")
    return blockers


def evaluate_quality_gate(
    blueprint: Mapping[str, Any],
    *,
    risks: Iterable[Any] | None = None,
    evaluated_at: str | None = None,
) -> QualityGateResultV1:
    """Compute a structured quality-gate result for the blueprint.

    Args:
        blueprint: The blueprint payload (or any dict with the same shape).
        risks: Optional iterable of :class:`RiskRecordV1` or risk dicts.
            These are merged with any structured risk records embedded in
            ``blueprint["risk_register"]``. The retired flat
            ``blueprint["risks"]`` list (free-text strings) is NOT consulted
            here — only structured risk records carry severity/status.
        evaluated_at: Override for the evaluation timestamp. Defaults to
            current UTC.

    Returns:
        A :class:`QualityGateResultV1` with blocker / warning / info
        buckets and aggregate counts.
    """
    target_files = _string_list(blueprint.get("target_files"))
    acceptance = _string_list(blueprint.get("acceptance_criteria"))
    checklist = _string_list(blueprint.get("execution_checklist"))
    dependencies = _string_list(blueprint.get("dependencies"))
    recommendations = _string_list(blueprint.get("recommendations"))

    blockers: list[str] = []
    warnings: list[str] = []
    info: list[str] = []

    if not target_files:
        blockers.append("target_files is empty; handoff requires at least one target file.")
    if not acceptance:
        blockers.append("acceptance_criteria is empty; handoff requires QA-evaluable acceptance.")

    if not checklist:
        warnings.append("execution_checklist is empty; Director will lack ordered steps.")

    if not dependencies and len(acceptance) > 3:
        warnings.append(
            f"dependencies empty while acceptance_criteria has {len(acceptance)} items; "
            "consider explicit ordering to avoid race conditions."
        )

    if len(recommendations) < 2:
        info.append("recommendations is short; consider adding a release-readiness and a risk-mitigation note.")

    for blocker in _semantic_blockers_from_blueprint(blueprint):
        blockers.append(f"contract semantic blocker: {blocker}")

    # Risks: merge caller-supplied + blueprint-embedded risk register.
    all_risks: list[RiskRecordV1] = []
    if risks is not None:
        all_risks.extend(_coerce_risks(risks))
    embedded = blueprint.get("risk_register")
    if isinstance(embedded, (list, tuple)):
        all_risks.extend(_coerce_risks(embedded))
    for record in all_risks:
        if record.status != RiskStatus.OPEN:
            continue
        if record.severity in (RiskSeverity.BLOCKER, RiskSeverity.CRITICAL):
            blockers.append(f"open risk {record.risk_id} severity={record.severity.value} title={record.title!r}")
        elif record.severity == RiskSeverity.HIGH:
            warnings.append(f"open risk {record.risk_id} severity=high title={record.title!r}")

    return QualityGateResultV1(
        passed=len(blockers) == 0,
        blocker_count=len(blockers),
        warning_count=len(warnings),
        info_count=len(info),
        blockers=tuple(blockers),
        warnings=tuple(warnings),
        info=tuple(info),
        evaluated_at=str(evaluated_at or _utc_now()),
    )


__all__ = ["evaluate_quality_gate"]
