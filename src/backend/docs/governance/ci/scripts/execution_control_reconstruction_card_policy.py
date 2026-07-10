"""Policy for execution-control-plane reconstruction card validation."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

RULE_ID = "execution_control_reconstruction_card"

SCHEMA_RELATIVE_PATH = Path(
    "src/backend/docs/governance/schemas/execution-control-plane-reconstruction-card.schema.yaml"
)
TEMPLATE_RELATIVE_PATH = Path(
    "src/backend/docs/governance/templates/verification-cards/execution-control-plane-reconstruction-card.template.yaml"
)
BACKEND_SCHEMA_RELATIVE_PATH = Path("docs/governance/schemas/execution-control-plane-reconstruction-card.schema.yaml")
BACKEND_TEMPLATE_RELATIVE_PATH = Path(
    "docs/governance/templates/verification-cards/execution-control-plane-reconstruction-card.template.yaml"
)


@dataclass(frozen=True)
class ExecutionControlReconstructionCardPolicyResult:
    """Evaluation result for execution-control-plane reconstruction cards."""

    rule_id: str
    passed: bool
    evidence: tuple[str, ...] = ()
    violations: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def _read_yaml_mapping(path: Path) -> tuple[Mapping[str, Any] | None, str]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return None, f"Failed to read YAML {path}: {exc}"
    if not isinstance(payload, Mapping):
        return None, f"YAML document must be a mapping: {path}"
    return payload, ""


def _resolve_existing_path(workspace: Path, *relative_paths: Path) -> Path:
    for relative in relative_paths:
        candidate = workspace / relative
        if candidate.exists():
            return candidate
    return workspace / relative_paths[0]


def _string_present(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _mapping_at(payload: Mapping[str, Any], *keys: str) -> Mapping[str, Any] | None:
    current: object = payload
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current if isinstance(current, Mapping) else None


def _require_string(
    payload: Mapping[str, Any],
    *,
    label: str,
    keys: tuple[str, ...],
) -> list[str]:
    parent = _mapping_at(payload, *keys[:-1]) if len(keys) > 1 else payload
    if not isinstance(parent, Mapping) or not _string_present(parent.get(keys[-1])):
        return [f"{label}: architecture_reliable=true requires non-empty {'.'.join(keys)}"]
    return []


def _reliability_claim_violations(card: Mapping[str, Any], *, label: str) -> list[str]:
    sign_off = card.get("sign_off")
    if not isinstance(sign_off, Mapping) or not bool(sign_off.get("architecture_reliable")):
        return []

    violations: list[str] = []
    required_strings: tuple[tuple[str, ...], ...] = (
        ("sign_off", "completed_by"),
        ("sign_off", "completed_at"),
        ("sign_off", "verified_by"),
        ("sign_off", "verified_at"),
        ("fact_chain_required_for_signoff", "final_provider_request", "evidence_ref"),
        ("fact_chain_required_for_signoff", "provider_response", "evidence_ref"),
        ("fact_chain_required_for_signoff", "tool_lifecycle_receipt", "evidence_ref"),
        ("fact_chain_required_for_signoff", "task_boundary_verdict", "evidence_ref"),
        ("fact_chain_required_for_signoff", "task_runtime_observable_projection", "evidence_ref"),
        ("fact_chain_required_for_signoff", "run_ledger_projection", "evidence_ref"),
        ("fact_chain_required_for_signoff", "qa_verdict", "evidence_ref"),
        ("fact_chain_required_for_signoff", "qa_verdict", "failure_class"),
        ("fact_chain_required_for_signoff", "qa_verdict", "responsible_layer"),
        ("fact_chain_required_for_signoff", "factory_bench_report", "evidence_ref"),
        ("fact_chain_required_for_signoff", "factory_bench_report", "requested_project_id"),
        ("fact_chain_required_for_signoff", "factory_bench_report", "canonical_project_id"),
        ("fact_chain_required_for_signoff", "factory_bench_report", "instance_id"),
        ("fact_chain_required_for_signoff", "factory_bench_report", "workspace"),
    )
    for keys in required_strings:
        violations.extend(_require_string(card, label=label, keys=keys))

    fact_chain = _mapping_at(card, "fact_chain_required_for_signoff")
    if isinstance(fact_chain, Mapping):
        effect_receipts = fact_chain.get("effect_receipts")
        if not isinstance(effect_receipts, Mapping) or not effect_receipts.get("evidence_refs"):
            violations.append(f"{label}: architecture_reliable=true requires effect receipt evidence_refs")

        tool_lifecycle = fact_chain.get("tool_lifecycle_receipt")
        if isinstance(tool_lifecycle, Mapping):
            for field in ("native_tool_calls_count", "decoded_tool_calls_count", "dispatched_tool_calls_count"):
                if tool_lifecycle.get(field) is None:
                    violations.append(f"{label}: architecture_reliable=true requires tool_lifecycle_receipt.{field}")
        else:
            violations.append(f"{label}: architecture_reliable=true requires tool_lifecycle_receipt")

        for node_name in ("task_runtime_observable_projection", "run_ledger_projection"):
            node = fact_chain.get(node_name)
            if not isinstance(node, Mapping) or node.get("projection_mismatch") is not False:
                violations.append(f"{label}: architecture_reliable=true requires {node_name}.projection_mismatch=false")

        factory_report = fact_chain.get("factory_bench_report")
        if isinstance(factory_report, Mapping):
            for field in ("backend_port", "frontend_port"):
                if factory_report.get(field) is None:
                    violations.append(f"{label}: architecture_reliable=true requires factory_bench_report.{field}")

    negative_controls = card.get("negative_controls")
    if isinstance(negative_controls, Mapping):
        for name, raw_control in negative_controls.items():
            if not isinstance(raw_control, Mapping):
                violations.append(f"{label}: negative_controls.{name} must be a mapping")
                continue
            if raw_control.get("expected") is not False:
                violations.append(
                    f"{label}: architecture_reliable=true requires negative_controls.{name}.expected=false"
                )
            if not _string_present(raw_control.get("evidence")):
                violations.append(f"{label}: architecture_reliable=true requires negative_controls.{name}.evidence")
    else:
        violations.append(f"{label}: architecture_reliable=true requires negative_controls")

    return violations


def _validate_payload(
    *,
    schema: Mapping[str, Any],
    path: Path,
    payload: Mapping[str, Any],
) -> tuple[list[str], list[str]]:
    evidence: list[str] = []
    violations: list[str] = []
    try:
        Draft202012Validator(schema).validate(payload)
    except ValidationError as exc:
        violations.append(f"{path}: schema validation failed at {'/'.join(str(p) for p in exc.path)}: {exc.message}")
        return evidence, violations

    evidence.append(f"{path}: schema validation passed")
    violations.extend(_reliability_claim_violations(payload, label=str(path)))
    return evidence, violations


def evaluate_execution_control_reconstruction_card(
    workspace: Path,
    *,
    card_paths: Iterable[Path | str] = (),
) -> ExecutionControlReconstructionCardPolicyResult:
    """Validate execution-control-plane reconstruction card template and cards.

    Complexity:
        O(c + n) time over cards and negative-control fields; O(c + n) space for
        emitted evidence and violations.
    """

    schema_path = _resolve_existing_path(workspace, SCHEMA_RELATIVE_PATH, BACKEND_SCHEMA_RELATIVE_PATH)
    template_path = _resolve_existing_path(workspace, TEMPLATE_RELATIVE_PATH, BACKEND_TEMPLATE_RELATIVE_PATH)
    evidence: list[str] = []
    violations: list[str] = []
    warnings: list[str] = []

    schema, schema_error = _read_yaml_mapping(schema_path)
    if schema_error:
        return ExecutionControlReconstructionCardPolicyResult(
            rule_id=RULE_ID,
            passed=False,
            violations=(schema_error,),
        )
    assert schema is not None

    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        return ExecutionControlReconstructionCardPolicyResult(
            rule_id=RULE_ID,
            passed=False,
            violations=(f"{schema_path}: invalid JSON schema: {exc.message}",),
        )
    evidence.append(f"{schema_path}: JSON schema is valid")

    raw_targets = list(card_paths)
    targets = [workspace / target if not Path(target).is_absolute() else Path(target) for target in raw_targets]
    if not targets:
        targets = [template_path]
        warnings.append("No card paths provided; validated the template only")

    for target in targets:
        payload, error = _read_yaml_mapping(target)
        if error:
            violations.append(error)
            continue
        assert payload is not None
        target_evidence, target_violations = _validate_payload(schema=schema, path=target, payload=payload)
        evidence.extend(target_evidence)
        violations.extend(target_violations)

    return ExecutionControlReconstructionCardPolicyResult(
        rule_id=RULE_ID,
        passed=not violations,
        evidence=tuple(evidence),
        violations=tuple(violations),
        warnings=tuple(warnings),
    )
