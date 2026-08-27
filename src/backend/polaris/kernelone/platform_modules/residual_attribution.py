"""Map Factory/bench residual signals to exactly one platform module_id.

Unattended automation requires fail-closed, deterministic residual attribution
before any repair or rebench. Effect-path modules (M01–M08) outrank semantic
M10; four-pillar measure failures stay on M09 unless a control-plane signature
already won.

Complexity: O(n) over residual text fields (signatures, error codes, reasons).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from polaris.kernelone.platform_modules.registry import MODULE_CASCADE_ORDER, get_module

# Effect ladder: observation → authority → tools → context → lease → fanout → chain → ledger → semantic → measure.
_EFFECT_LADDER: tuple[str, ...] = MODULE_CASCADE_ORDER

# (substring match on normalized text) → module_id. First match in ladder order wins
# among matches; we score by earliest cascade index among all hits.
_SIGNATURE_HINTS: tuple[tuple[str, str], ...] = (
    # M01 observation
    ("event_wait_timeout", "M01_event_wait"),
    ("runtime_v2_connection_failed", "M01_event_wait"),
    ("connection_failed", "M01_event_wait"),
    ("keepalive", "M01_event_wait"),
    # M02 authority
    ("authority_closed", "M02_physical_attempt_authority"),
    ("grant_close", "M02_physical_attempt_authority"),
    ("physical_attempt", "M02_physical_attempt_authority"),
    ("execution_attempt", "M02_physical_attempt_authority"),
    # M03 tools / DEO
    ("deo_", "M03_tool_batch_deo"),
    ("edit_blocks", "M03_tool_batch_deo"),
    ("directed_effect", "M03_tool_batch_deo"),
    ("tool_dispatch_dropped", "M03_tool_batch_deo"),
    ("path_scope", "M03_tool_batch_deo"),
    ("policy_denied", "M03_tool_batch_deo"),
    ("tool_result_failed", "M03_tool_batch_deo"),
    # M04 context
    ("current_user_final", "M04_final_request_context"),
    ("final_request", "M04_final_request_context"),
    ("context_snapshot", "M04_final_request_context"),
    ("sibling_export_pin", "M04_final_request_context"),
    ("provider_request", "M04_final_request_context"),
    # M05 lease
    ("lease_expired", "M05_stage_lease_heartbeat"),
    ("lease_fence", "M05_stage_lease_heartbeat"),
    ("heartbeat", "M05_stage_lease_heartbeat"),
    ("stage_lease", "M05_stage_lease_heartbeat"),
    # M06 multi-task / boundary authority
    ("canonical_task_boundary", "M06_director_multi_task"),
    ("task_boundary", "M06_director_multi_task"),
    ("task_runtime_not_converged", "M06_director_multi_task"),
    ("task_runtime_not_completed", "M06_director_multi_task"),
    ("director.inflight_timeout", "M06_director_multi_task"),
    ("director.dispatch_timeout", "M06_director_multi_task"),
    ("director.taskboard_not_converged", "M06_director_multi_task"),
    ("materialization_settle", "M06_director_multi_task"),
    ("multi_task", "M06_director_multi_task"),
    ("incomplete_task", "M06_director_multi_task"),
    # M07 stage chain
    ("pm_to_director", "M07_factory_stage_chain"),
    ("chief_engineer", "M07_factory_stage_chain"),
    ("stage_chain", "M07_factory_stage_chain"),
    ("handoff", "M07_factory_stage_chain"),
    ("factory_stage", "M07_factory_stage_chain"),
    # M08 ledger lifecycle
    ("tool_lifecycle", "M08_run_ledger_tool_lifecycle"),
    ("lifecycle_missing", "M08_run_ledger_tool_lifecycle"),
    ("run_ledger_integrity", "M08_run_ledger_tool_lifecycle"),
    ("run_ledger_projection", "M08_run_ledger_tool_lifecycle"),
    ("ledger_projection_missing", "M08_run_ledger_tool_lifecycle"),
    ("missing_required_modalit", "M08_run_ledger_tool_lifecycle"),
    ("failed_required_modalit", "M08_run_ledger_tool_lifecycle"),
    ("run_ledger", "M08_run_ledger_tool_lifecycle"),
    # M10 semantic / materialization quality (only after effect text absent)
    ("typescript_ts", "M10_materialization_semantic_quality"),
    ("ts230", "M10_materialization_semantic_quality"),
    ("tsc ", "M10_materialization_semantic_quality"),
    ("repair_coverage", "M10_materialization_semantic_quality"),
    ("materialization_quality", "M10_materialization_semantic_quality"),
    ("deterministic_typescript", "M10_materialization_semantic_quality"),
    # M09 four pillars measure
    ("real_run_gate", "M09_four_pillars_gates"),
    ("entrypoint_smoke", "M09_four_pillars_gates"),
    ("build_test_lint", "M09_four_pillars_gates"),
    ("four_pillar", "M09_four_pillars_gates"),
    ("delivery_depth", "M09_four_pillars_gates"),
)


@dataclass(frozen=True, slots=True)
class ResidualAttributionV1:
    """One residual → one module, with unattended next-step contract."""

    schema_version: str
    primary_module_id: str
    defect_subtype: str
    root_cause_signature: str
    failure_category: str
    ladder_matched_hints: tuple[str, ...]
    forbidden_same_round: tuple[str, ...]
    gate_commands: tuple[str, ...]
    delivery_status: str
    preconditions: dict[str, bool | str | int]
    status: str
    next_action: str
    evidence_notes: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return payload


def _normalize_blob(parts: Sequence[object]) -> str:
    chunks: list[str] = []
    for part in parts:
        if part is None:
            continue
        if isinstance(part, Mapping):
            chunks.append(" ".join(f"{key}={value}" for key, value in part.items()))
        elif isinstance(part, (list, tuple, set)):
            chunks.append(" ".join(str(item) for item in part))
        else:
            chunks.append(str(part))
    return " ".join(chunks).strip().lower()


def _cascade_index(module_id: str) -> int:
    try:
        return int(MODULE_CASCADE_ORDER.index(module_id))
    except ValueError:
        return int(len(MODULE_CASCADE_ORDER) + 1)


def _match_module_from_text(blob: str) -> tuple[str | None, tuple[str, ...]]:
    if not blob:
        return None, ()
    hits: list[tuple[int, str, str]] = []
    for hint, module_id in _SIGNATURE_HINTS:
        if hint in blob:
            hits.append((_cascade_index(module_id), module_id, hint))
    if not hits:
        return None, ()
    hits.sort(key=lambda item: (item[0], item[2]))
    # Earliest effect-ladder module among matches.
    best_index = hits[0][0]
    earliest = [item for item in hits if item[0] == best_index]
    module_id = earliest[0][1]
    matched = tuple(item[2] for item in hits if item[1] == module_id)
    return module_id, matched


def _gate_commands_for(module_id: str) -> tuple[str, ...]:
    return (
        f"python src/backend/scripts/platform_modules/run_module_gates.py --module {module_id}",
        "python src/backend/scripts/platform_modules/run_module_gates.py --mode cascade "
        "--json-out /tmp/platform-module-cascade.json",
    )


def _forbidden_peers(module_id: str) -> tuple[str, ...]:
    return tuple(mid for mid in MODULE_CASCADE_ORDER if mid != module_id)


def attribute_residual(
    *,
    root_cause_signature: str = "",
    failure_category: str = "",
    failure_reasons: Sequence[str] | None = None,
    error_code: str = "",
    director_detail: str = "",
    real_run_gate_ok: bool | None = None,
    chain_ok: bool | None = None,
    tsc_clean: bool | None = None,
    m10_coverage_gap_count: int | None = None,
    evidence_notes: Sequence[str] | None = None,
) -> ResidualAttributionV1:
    """Attribute one residual bag to exactly one platform module_id.

    Prefer structured signature/error_code; fall back to free-text reasons.
    When delivery is green but chain is red with boundary/runtime language,
    force M06 (r181 class) over generic measure/M10 noise.
    """

    reasons = tuple(str(item) for item in (failure_reasons or ()) if str(item).strip())
    blob = _normalize_blob(
        [
            root_cause_signature,
            failure_category,
            error_code,
            director_detail,
            *reasons,
        ]
    )
    module_id, matched = _match_module_from_text(blob)

    explicit_delivery_hints = tuple(
        hint
        for hint, hinted_module_id in _SIGNATURE_HINTS
        if hinted_module_id == "M09_four_pillars_gates" and hint != "real_run_gate" and hint in blob
    )

    # r181: real_run green + control-plane boundary/runtime language → M06 wins
    # even if real_run_gate also appears in failure_reasons.
    # R90: an explicit product/verifier failure is causally upstream of the
    # TaskRuntime row failed by terminal workspace validation. It must remain
    # M09 rather than being overwritten by the downstream boundary symptom.
    if module_id == "M06_director_multi_task" and explicit_delivery_hints:
        module_id = "M09_four_pillars_gates"
        matched = explicit_delivery_hints
    elif real_run_gate_ok is True and any(
        token in blob
        for token in (
            "canonical_task_boundary",
            "task_runtime_not_converged",
            "task_runtime_not_completed",
            "task_boundary",
        )
    ):
        module_id = "M06_director_multi_task"
        matched = tuple(dict.fromkeys((*matched, "real_run_green_boundary_authority")))

    if module_id is None and real_run_gate_ok is False and chain_ok is False:
        module_id = "M09_four_pillars_gates"
        matched = ("real_run_or_chain_failed_default_measure",)
    if module_id is None and tsc_clean is False:
        module_id = "M10_materialization_semantic_quality"
        matched = ("tsc_not_clean",)
    if module_id is None:
        module_id = "M07_factory_stage_chain"
        matched = ("unclassified_fail_closed_stage_chain",)

    # Validate module exists.
    try:
        get_module(module_id)
    except KeyError:
        module_id = "M07_factory_stage_chain"

    # Semantic M10 only if coverage gap or tsc red and no earlier effect hit
    # already preferred via ladder match — already handled by earliest cascade.

    delivery_status = classify_delivery_status(
        real_run_gate_ok=real_run_gate_ok,
        chain_ok=chain_ok,
        control_plane_signature=root_cause_signature or error_code,
    )

    subtype = _stable_token(error_code or root_cause_signature or (matched[0] if matched else "unknown"))
    next_action = f"run module gate for {module_id}; workflow owner decides later scheduling"
    preconditions: dict[str, bool | str | int] = {
        "effect_ladder_applied": True,
        "single_module_required": True,
        "real_run_gate_ok": "unknown" if real_run_gate_ok is None else bool(real_run_gate_ok),
        "chain_ok": "unknown" if chain_ok is None else bool(chain_ok),
        "tsc_clean": "unknown" if tsc_clean is None else bool(tsc_clean),
        "m10_coverage_gap_count": "unknown" if m10_coverage_gap_count is None else int(m10_coverage_gap_count),
    }
    return ResidualAttributionV1(
        schema_version="platform.residual_attribution.v1",
        primary_module_id=module_id,
        defect_subtype=subtype,
        root_cause_signature=str(root_cause_signature or f"{failure_category}:{subtype}").strip(),
        failure_category=str(failure_category or "control_plane").strip() or "control_plane",
        ladder_matched_hints=matched,
        forbidden_same_round=_forbidden_peers(module_id),
        gate_commands=_gate_commands_for(module_id),
        delivery_status=delivery_status,
        preconditions=preconditions,
        status="attributed",
        next_action=next_action,
        evidence_notes=tuple(str(item) for item in (evidence_notes or ()) if str(item).strip()),
    )


def attribute_factory_audit_record(record: Mapping[str, Any]) -> ResidualAttributionV1:
    """Attribute one factory_audits.json record (bench audit schema)."""

    taxonomy = record.get("failure_taxonomy")
    taxonomy_map: Mapping[str, Any] = taxonomy if isinstance(taxonomy, Mapping) else {}

    real_run = record.get("real_run_gate")
    real_ok: bool | None = None
    if isinstance(real_run, Mapping) and "ok" in real_run:
        real_ok = bool(real_run.get("ok"))
    # All checks passed → treat delivery+chain as green when flags absent.
    if real_ok is None and record.get("all_checks_passed") is True:
        real_ok = True

    chain = record.get("chain")
    chain_ok: bool | None = None
    if isinstance(chain, Mapping) and "exit_code" in chain:
        chain_ok = int(chain.get("exit_code") or 1) == 0
    elif str(record.get("chain_state") or "").strip().lower() in {"pass", "ok", "success"}:
        chain_ok = True
    elif str(record.get("chain_state") or "").strip().lower() in {"fail", "failed"}:
        chain_ok = False
    if chain_ok is None and record.get("all_checks_passed") is True:
        chain_ok = True

    director_detail = ""
    error_code = ""
    if isinstance(chain, Mapping):
        terminal = chain.get("factory_terminal_status")
        if isinstance(terminal, Mapping):
            roles = terminal.get("roles")
            if isinstance(roles, Mapping):
                director = roles.get("director")
                if isinstance(director, Mapping):
                    director_detail = str(director.get("detail") or "")
            failure = terminal.get("failure")
            if isinstance(failure, Mapping):
                error_code = str(failure.get("code") or failure.get("error_code") or "")
                if not director_detail:
                    director_detail = str(failure.get("detail") or failure.get("root_cause_hint") or "")

    reasons_raw = record.get("failure_reasons")
    reasons: list[str] = []
    if isinstance(reasons_raw, (list, tuple)):
        reasons = [str(item) for item in reasons_raw]
    tax_reasons = taxonomy_map.get("reasons")
    if isinstance(tax_reasons, (list, tuple)):
        reasons.extend(str(item) for item in tax_reasons if str(item).strip())
    tax_evidence = taxonomy_map.get("evidence")
    if isinstance(tax_evidence, (list, tuple)):
        reasons.extend(str(item) for item in tax_evidence if str(item).strip())
    gates = record.get("factory_gates")
    if isinstance(gates, list):
        for gate in gates:
            if isinstance(gate, Mapping) and gate.get("ok") is False:
                reasons.append(f"gate:{gate.get('gate')}={gate.get('detail')}")

    coverage = record.get("director_repair_coverage_gap_summary")
    gap_count: int | None = None
    if isinstance(coverage, Mapping) and "coverage_gap_count" in coverage:
        try:
            gap_count = int(coverage.get("coverage_gap_count") or 0)
        except (TypeError, ValueError):
            gap_count = None

    root_sig = str(record.get("root_cause_signature") or taxonomy_map.get("root_cause_signature") or "")
    failure_cat = str(record.get("failure_category") or taxonomy_map.get("category") or "")

    return attribute_residual(
        root_cause_signature=root_sig,
        failure_category=failure_cat,
        failure_reasons=reasons,
        error_code=error_code,
        director_detail=director_detail,
        real_run_gate_ok=real_ok,
        chain_ok=chain_ok,
        m10_coverage_gap_count=gap_count,
        evidence_notes=(
            f"project_id={record.get('project_id') or ''}",
            f"factory_run_id={record.get('factory_run_id') or record.get('run_id') or ''}",
        ),
    )


def build_factory_audits_attribution_pack(
    payload: Mapping[str, Any],
    *,
    source_path: str = "",
) -> dict[str, Any]:
    """Build residual attribution pack from an in-memory factory_audits payload.

    Prefers failed records for ``primary`` so unattended supervisors target a
    real residual, not a green all_checks_passed row.
    """

    records = payload.get("records") if isinstance(payload, Mapping) else None
    if not isinstance(records, list):
        records = [payload] if isinstance(payload, Mapping) else []
    attributed: list[dict[str, Any]] = []
    failed_attrs: list[dict[str, Any]] = []
    for item in records:
        if not isinstance(item, Mapping):
            continue
        attr = attribute_factory_audit_record(item).to_dict()
        attributed.append(attr)
        if item.get("all_checks_passed") is not True:
            failed_attrs.append(attr)
    primary = failed_attrs[0] if failed_attrs else (attributed[0] if attributed else attribute_residual().to_dict())
    return {
        "schema_version": "platform.factory_audits_attribution.v1",
        "source_path": str(source_path or ""),
        "record_count": len(attributed),
        "failed_record_count": len(failed_attrs),
        "primary": primary,
        "records": attributed,
        "goal_audit": payload.get("goal_audit") if isinstance(payload, Mapping) else {},
    }


def attribute_factory_audits_file(path: str) -> dict[str, Any]:
    """Load factory_audits.json and attribute each record + aggregate goal_audit."""

    import json
    from pathlib import Path

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        payload = {}
    return build_factory_audits_attribution_pack(payload, source_path=str(path))


def classify_delivery_status(
    *,
    real_run_gate_ok: bool | None,
    chain_ok: bool | None,
    control_plane_signature: str = "",
) -> str:
    """Project unattended terminal class (not a second success SSoT)."""

    sig = str(control_plane_signature or "").strip().lower()
    if real_run_gate_ok is True and chain_ok is True:
        return "DELIVERY_AND_CHAIN_VERIFIED"
    if real_run_gate_ok is True and chain_ok is False:
        if any(
            token in sig
            for token in (
                "task_runtime",
                "task_boundary",
                "canonical_task_boundary",
                "control_plane",
            )
        ):
            return "DELIVERY_VERIFIED_CHAIN_CONTROL_PLANE_FAIL"
        return "DELIVERY_VERIFIED_CHAIN_INCOMPLETE"
    if real_run_gate_ok is False and chain_ok is True:
        return "CHAIN_OK_DELIVERY_FAILED"
    if real_run_gate_ok is False:
        return "DELIVERY_FAILED"
    return "STATUS_UNKNOWN"


def _stable_token(value: str) -> str:
    raw = str(value or "").strip().lower()
    normalized = "".join(ch if ch.isalnum() or ch in "_.:-" else "_" for ch in raw)
    return "_".join(part for part in normalized.split("_") if part) or "unknown"


__all__ = [
    "ResidualAttributionV1",
    "attribute_factory_audit_record",
    "attribute_factory_audits_file",
    "attribute_residual",
    "build_factory_audits_attribution_pack",
    "classify_delivery_status",
]
