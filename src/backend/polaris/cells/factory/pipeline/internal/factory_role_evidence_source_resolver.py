"""Canonical read-only A009B2b-1 Factory role-evidence source resolver."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from pathlib import Path

from polaris.cells.control_plane.run_ledger.public import RunLedger
from polaris.cells.events.fact_stream.public import (
    FactStreamQueryResultV1,
    QueryFactEventsV1,
    query_fact_events,
)
from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import (
    FactoryRoleEvidenceCutoffRequestV1,
)
from polaris.kernelone.events.final_request_evidence import role_final_request_policy
from polaris.kernelone.events.sourcing import EventEnvelope, decode_strict_event_record

from .factory_event_chain import validate_factory_event_chain
from .factory_role_evidence_authority import (
    FactoryRoleEvidenceAuthorityError,
    FactoryRoleEvidenceResolvedCutV1,
    FactoryRoleEvidenceSourceHeadV1,
    FactoryRoleEvidenceSourceItemV1,
    FactoryRoleEvidenceSourceSlotV1,
    FactoryRoleEvidenceStageAuthorityV1,
)
from .factory_run_models import FactoryRun
from .factory_stage_artifact_bindings import (
    revalidate_chief_engineer_stage_artifact_binding,
    revalidate_pm_stage_artifact_binding,
)
from .factory_store import FactoryStore


class CanonicalFactoryRoleEvidenceSourceAuthority:
    """Resolve one immutable Factory-issued source cut without mutation."""

    def __init__(
        self,
        *,
        workspace: str | Path,
        factory_store: FactoryStore,
        factory_event_loader: Callable[[str], tuple[dict[str, object], ...]],
        fact_query: Callable[[QueryFactEventsV1], FactStreamQueryResultV1] = query_fact_events,
    ) -> None:
        self._workspace = Path(workspace).resolve()
        self._factory_store = factory_store
        self._factory_event_loader = factory_event_loader
        self._fact_query = fact_query

    @staticmethod
    def _fail(code: str) -> FactoryRoleEvidenceAuthorityError:
        return FactoryRoleEvidenceAuthorityError(code)

    @staticmethod
    def _source_ref(factory_run_id: str, ref_kind: str) -> str:
        run_digest = hashlib.sha256(factory_run_id.encode("utf-8")).hexdigest()
        return f"factory.role_evidence.source.{run_digest}.{ref_kind}.v1"

    def _factory_chain(self, factory_run: FactoryRun) -> tuple[dict[str, object], ...]:
        try:
            loaded = self._factory_event_loader(factory_run.id)
            if type(loaded) is not tuple:
                raise TypeError("factory_event_loader_tuple_required")
            return validate_factory_event_chain(loaded, run_id=factory_run.id)
        except FactoryRoleEvidenceAuthorityError:
            raise
        except Exception as exc:
            raise self._fail("factory_role_evidence_source_factory_chain_invalid") from exc

    def _admission_slot(
        self,
        *,
        factory_run: FactoryRun,
        events: tuple[dict[str, object], ...],
    ) -> FactoryRoleEvidenceSourceSlotV1:
        if not events:
            raise self._fail("factory_role_evidence_source_admission_missing")
        admission = events[0]
        payload = admission.get("payload")
        if not isinstance(payload, Mapping):
            raise self._fail("factory_role_evidence_source_admission_invalid")
        expected_payload = {
            "factory_run_id": factory_run.id,
            "created_at": factory_run.created_at,
            "name": factory_run.config.name,
            "description": factory_run.config.description,
        }
        if dict(payload) != expected_payload:
            raise self._fail("factory_role_evidence_source_admission_drift")
        canonical_hash = admission.get("canonical_sha256")
        event_id = admission.get("event_id")
        sequence = admission.get("chain_sequence")
        event_hash = admission.get("chain_event_hash")
        head_event = events[-1]
        try:
            source_ref = self._source_ref(factory_run.id, "pm_raw_intent")
            return FactoryRoleEvidenceSourceSlotV1(
                ref_kind="pm_raw_intent",
                state="present",
                source_head=FactoryRoleEvidenceSourceHeadV1(
                    canonical_source_ref=source_ref,
                    source_fact_schema="factory.event_chain.v1",
                    source_fact_version="1",
                    source_head_fact_id=head_event.get("event_id"),  # type: ignore[arg-type]
                    source_head_sequence=head_event.get("chain_sequence"),  # type: ignore[arg-type]
                    source_head_hash=head_event.get("chain_event_hash"),  # type: ignore[arg-type]
                ),
                items=(
                    FactoryRoleEvidenceSourceItemV1(
                        ref_kind="pm_raw_intent",
                        canonical_ref=(
                            "factory.role_evidence.item."
                            f"{hashlib.sha256(factory_run.id.encode('utf-8')).hexdigest()}"
                            ".pm_raw_intent."
                            f"{hashlib.sha256(str(event_id).encode('utf-8')).hexdigest()}.v1"
                        ),
                        canonical_hash=canonical_hash,  # type: ignore[arg-type]
                        source_fact_id=event_id,  # type: ignore[arg-type]
                        source_fact_sequence=sequence,  # type: ignore[arg-type]
                        source_fact_hash=event_hash,  # type: ignore[arg-type]
                    ),
                ),
            )
        except (TypeError, ValueError) as exc:
            raise self._fail("factory_role_evidence_source_admission_invalid") from exc

    @staticmethod
    def _latest_successful_stage_event(
        events: tuple[dict[str, object], ...],
        *,
        stage: str,
    ) -> dict[str, object]:
        for event in reversed(events):
            result = event.get("result")
            if (
                event.get("type") == "stage_completed"
                and event.get("stage") == stage
                and type(result) is dict
                and result.get("stage") == stage
                and result.get("status") in {"success", "completed"}
                and "stage_artifact_bindings" in event
            ):
                return event
        raise FactoryRoleEvidenceAuthorityError(f"factory_role_evidence_source_stage_missing:{stage}")

    def _static_head(
        self,
        *,
        factory_run_id: str,
        ref_kind: str,
        events: tuple[dict[str, object], ...],
    ) -> FactoryRoleEvidenceSourceHeadV1:
        head = events[-1]
        return FactoryRoleEvidenceSourceHeadV1(
            canonical_source_ref=self._source_ref(factory_run_id, ref_kind),
            source_fact_schema="factory.event_chain.v1",
            source_fact_version="1",
            source_head_fact_id=head.get("event_id"),  # type: ignore[arg-type]
            source_head_sequence=head.get("chain_sequence"),  # type: ignore[arg-type]
            source_head_hash=head.get("chain_event_hash"),  # type: ignore[arg-type]
        )

    def _pm_static_slots(
        self,
        *,
        factory_run: FactoryRun,
        events: tuple[dict[str, object], ...],
    ) -> tuple[dict[str, FactoryRoleEvidenceSourceSlotV1], dict[str, object]]:
        pm_event = self._latest_successful_stage_event(events, stage="pm_planning")
        try:
            revalidated = revalidate_pm_stage_artifact_binding(
                factory_store=self._factory_store,
                factory_run_id=factory_run.id,
                stage_event=pm_event,
            )
            event_id = str(pm_event["event_id"])
            sequence = pm_event["chain_sequence"]
            event_hash = str(pm_event["chain_event_hash"])
            run_digest = hashlib.sha256(factory_run.id.encode("utf-8")).hexdigest()
            contract = FactoryRoleEvidenceSourceSlotV1(
                ref_kind="pm_contract",
                state="present",
                source_head=self._static_head(
                    factory_run_id=factory_run.id,
                    ref_kind="pm_contract",
                    events=events,
                ),
                items=(
                    FactoryRoleEvidenceSourceItemV1(
                        ref_kind="pm_contract",
                        canonical_ref=revalidated.item.immutable_snapshot_ref,
                        canonical_hash=revalidated.item.canonical_json_sha256,
                        source_fact_id=event_id,
                        source_fact_sequence=sequence,  # type: ignore[arg-type]
                        source_fact_hash=event_hash,
                    ),
                ),
            )
            targets = FactoryRoleEvidenceSourceSlotV1(
                ref_kind="target_files",
                state="present",
                source_head=self._static_head(
                    factory_run_id=factory_run.id,
                    ref_kind="target_files",
                    events=events,
                ),
                items=(
                    FactoryRoleEvidenceSourceItemV1(
                        ref_kind="target_files",
                        canonical_ref=(f"factory.role_evidence.item.{run_digest}.target_files.{event_hash}.v1"),
                        canonical_hash=revalidated.item.target_files_projection_sha256,
                        source_fact_id=event_id,
                        source_fact_sequence=sequence,  # type: ignore[arg-type]
                        source_fact_hash=event_hash,
                    ),
                ),
            )
        except FactoryRoleEvidenceAuthorityError:
            raise
        except Exception as exc:
            raise self._fail("factory_role_evidence_source_pm_binding_invalid") from exc
        return {"pm_contract": contract, "target_files": targets}, pm_event

    def _ce_static_slot(
        self,
        *,
        factory_run: FactoryRun,
        events: tuple[dict[str, object], ...],
        pm_event: dict[str, object],
    ) -> FactoryRoleEvidenceSourceSlotV1:
        ce_event = self._latest_successful_stage_event(events, stage="chief_engineer_review")
        try:
            revalidated = revalidate_chief_engineer_stage_artifact_binding(
                factory_store=self._factory_store,
                factory_run_id=factory_run.id,
                stage_event=ce_event,
                pm_stage_event=pm_event,
            )
            event_id = str(ce_event["event_id"])
            sequence = ce_event["chain_sequence"]
            event_hash = str(ce_event["chain_event_hash"])
            run_digest = hashlib.sha256(factory_run.id.encode("utf-8")).hexdigest()
            vector_hash = revalidated.binding.binding_vector_sha256
            return FactoryRoleEvidenceSourceSlotV1(
                ref_kind="ce_blueprint",
                state="present",
                source_head=self._static_head(
                    factory_run_id=factory_run.id,
                    ref_kind="ce_blueprint",
                    events=events,
                ),
                items=(
                    FactoryRoleEvidenceSourceItemV1(
                        ref_kind="ce_blueprint",
                        canonical_ref=(f"factory.role_evidence.item.{run_digest}.ce_blueprint.{vector_hash}.v1"),
                        canonical_hash=vector_hash,
                        source_fact_id=event_id,
                        source_fact_sequence=sequence,  # type: ignore[arg-type]
                        source_fact_hash=event_hash,
                    ),
                ),
            )
        except FactoryRoleEvidenceAuthorityError:
            raise
        except Exception as exc:
            raise self._fail("factory_role_evidence_source_ce_binding_invalid") from exc

    @staticmethod
    def _non_empty(value: object) -> bool:
        if value is None or value is False:
            return False
        if type(value) is str:
            return bool(value.strip())
        if isinstance(value, (Mapping, list, tuple)):
            return bool(value)
        return False

    @classmethod
    def _has_verifier_receipt_evidence(cls, event: Mapping[str, object]) -> bool:
        physical = event.get("physical_evidence")
        if not isinstance(physical, Mapping):
            return False
        for field in ("requirements", "entrypoint", "commands", "modalities"):
            if cls._non_empty(physical.get(field)):
                return True
        command_count = physical.get("command_count")
        if type(command_count) is int and command_count > 0:
            return True
        for field in (
            "effect_receipt",
            "effect_receipts",
            "tool_receipts",
            "write_receipts",
            "command_receipts",
            "batch_receipt",
            "batch_receipts",
            "repair_receipts",
            "director_repair_receipts",
            "repair_kernel_receipts",
            "deterministic_repair_receipts",
            "environment_prep_receipts",
            "director_environment_prep_receipts",
        ):
            if cls._non_empty(physical.get(field)):
                return True
        return False

    def _validated_dynamic_event(
        self,
        record: Mapping[str, object],
    ) -> tuple[EventEnvelope, dict[str, object], str]:
        try:
            encoded = json.dumps(
                dict(record),
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            envelope = decode_strict_event_record(encoded.decode("utf-8"))
        except (TypeError, UnicodeError, ValueError) as exc:
            raise self._fail("factory_role_evidence_dynamic_record_invalid") from exc
        if envelope.stream != "execution.control_plane":
            raise self._fail("factory_role_evidence_dynamic_envelope_invalid")
        payload = envelope.payload
        nested = payload.get("event")
        if (
            payload.get("schema_version") != "execution.control_plane.fact.v1"
            or type(payload.get("run_id")) is not str
            or not str(payload.get("run_id") or "").strip()
            or not isinstance(nested, Mapping)
        ):
            raise self._fail("factory_role_evidence_dynamic_payload_invalid")
        event = dict(nested)
        if event.get("event_type") != envelope.event_type:
            raise self._fail("factory_role_evidence_dynamic_envelope_invalid")
        nested_run_id = event.get("run_id")
        if nested_run_id is not None and nested_run_id != payload.get("run_id"):
            raise self._fail("factory_role_evidence_dynamic_binding_invalid")
        try:
            prepared = RunLedger(self._workspace, run_id=str(payload["run_id"])).prepare_idempotent_event(event)
        except (TypeError, ValueError) as exc:
            raise self._fail("factory_role_evidence_dynamic_content_identity_invalid") from exc
        content_id = event.get("content_id")
        if type(content_id) is not str or prepared.get("content_id") != content_id:
            raise self._fail("factory_role_evidence_dynamic_content_identity_invalid")
        digest = record.get("integrity_digest")
        expected_digest = EventEnvelope.integrity_digest_for_record(record)
        if type(digest) is not str or digest != expected_digest:
            raise self._fail("factory_role_evidence_dynamic_digest_invalid")
        return envelope, event, content_id

    def _validate_dynamic_gate_binding(
        self,
        *,
        envelope: EventEnvelope,
        event: Mapping[str, object],
        factory_run_id: str,
    ) -> None:
        if envelope.event_type != "gate_evaluated":
            raise self._fail("factory_role_evidence_dynamic_envelope_invalid")
        payload = envelope.payload
        job_token = event.get("job_token")
        if not isinstance(job_token, Mapping):
            raise self._fail("factory_role_evidence_dynamic_job_token_invalid")
        event_stage = event.get("stage")
        job_stage = job_token.get("stage")
        if (
            event.get("event_type") != "gate_evaluated"
            or payload.get("run_id") != job_token.get("run_id")
            or job_token.get("factory_run_id") != factory_run_id
            or type(event_stage) is not str
            or not event_stage
            or event_stage != event_stage.strip()
            or type(job_stage) is not str
            or event_stage != job_stage
        ):
            raise self._fail("factory_role_evidence_dynamic_binding_invalid")

    def _capture_dynamic_slots(
        self,
        *,
        factory_run_id: str,
    ) -> dict[str, FactoryRoleEvidenceSourceSlotV1]:
        query = QueryFactEventsV1(
            workspace=str(self._workspace),
            stream="execution.control_plane",
            offset=0,
            limit=4096,
            event_type=None,
            run_id=None,
            task_id=None,
            strict_integrity=True,
        )
        try:
            result = self._fact_query(query)
        except Exception as exc:
            raise self._fail("factory_role_evidence_dynamic_query_failed") from exc
        if type(result) is not FactStreamQueryResultV1:
            raise self._fail("factory_role_evidence_dynamic_query_result_invalid")
        if result.workspace != str(self._workspace) or result.stream != "execution.control_plane":
            raise self._fail("factory_role_evidence_dynamic_query_scope_mismatch")
        if result.next_offset != 0:
            raise self._fail("factory_role_evidence_dynamic_pagination_invalid")
        if result.total != len(result.events):
            raise self._fail("factory_role_evidence_dynamic_total_mismatch")
        if len(result.events) > 4096:
            raise self._fail("factory_role_evidence_dynamic_record_limit_exceeded")

        encoded_bytes = 0
        stream_validated: list[tuple[EventEnvelope, str]] = []
        gate_validated: list[tuple[EventEnvelope, dict[str, object], str, str]] = []
        for expected_sequence, record in enumerate(result.events, start=1):
            try:
                encoded_bytes += (
                    len(
                        json.dumps(
                            record,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            allow_nan=False,
                        ).encode("utf-8")
                    )
                    + 1
                )
            except (TypeError, ValueError) as exc:
                raise self._fail("factory_role_evidence_dynamic_record_invalid") from exc
            if encoded_bytes > 8 * 1024 * 1024:
                raise self._fail("factory_role_evidence_dynamic_byte_limit_exceeded")
            envelope, event, content_id = self._validated_dynamic_event(record)
            if envelope.seq != expected_sequence:
                raise self._fail("factory_role_evidence_dynamic_sequence_invalid")
            digest = str(record["integrity_digest"])
            stream_validated.append((envelope, digest))
            if envelope.event_type == "gate_evaluated":
                self._validate_dynamic_gate_binding(
                    envelope=envelope,
                    event=event,
                    factory_run_id=factory_run_id,
                )
                gate_validated.append((envelope, event, content_id, digest))

        if stream_validated:
            final_envelope, final_digest = stream_validated[-1]
            head_id = final_envelope.event_id
            head_sequence = final_envelope.seq
            head_hash = final_digest
        else:
            head_id = ""
            head_sequence = 0
            head_hash = "0" * 64

        selected: dict[str, list[FactoryRoleEvidenceSourceItemV1]] = {
            "failure_feedback": [],
            "workspace_quality": [],
            "verifier_receipts": [],
        }
        for envelope, event, content_id, digest in gate_validated:
            gate = event.get("gate")
            predicates = {
                "failure_feedback": isinstance(gate, Mapping) and gate.get("ok") is False,
                "workspace_quality": event.get("stage") == "workspace_validation",
                "verifier_receipts": self._has_verifier_receipt_evidence(event),
            }
            for ref_kind, matches in predicates.items():
                if not matches:
                    continue
                source_ref = self._source_ref(factory_run_id, ref_kind)
                selected[ref_kind].append(
                    FactoryRoleEvidenceSourceItemV1(
                        ref_kind=ref_kind,
                        canonical_ref=f"{source_ref}.fact.{envelope.seq}.{envelope.event_id}",
                        canonical_hash=content_id,
                        source_fact_id=envelope.event_id,
                        source_fact_sequence=envelope.seq,
                        source_fact_hash=digest,
                    )
                )

        slots: dict[str, FactoryRoleEvidenceSourceSlotV1] = {}
        for ref_kind, items in selected.items():
            slots[ref_kind] = FactoryRoleEvidenceSourceSlotV1(
                ref_kind=ref_kind,
                state="present" if items else "absent_at_request_time",
                source_head=FactoryRoleEvidenceSourceHeadV1(
                    canonical_source_ref=self._source_ref(factory_run_id, ref_kind),
                    source_fact_schema="polaris.event_envelope.v1",
                    source_fact_version="1",
                    source_head_fact_id=head_id,
                    source_head_sequence=head_sequence,
                    source_head_hash=head_hash,
                ),
                items=tuple(items),
            )
        return slots

    def resolve_source_cut(
        self,
        *,
        request: FactoryRoleEvidenceCutoffRequestV1,
        authority: FactoryRoleEvidenceStageAuthorityV1,
        factory_run: FactoryRun,
    ) -> FactoryRoleEvidenceResolvedCutV1:
        if type(request) is not FactoryRoleEvidenceCutoffRequestV1:
            raise TypeError("factory_role_evidence_cutoff_request_exact_type_required")
        if type(authority) is not FactoryRoleEvidenceStageAuthorityV1:
            raise TypeError("factory_role_evidence_stage_authority_exact_type_required")
        if type(factory_run) is not FactoryRun:
            raise TypeError("factory_run_exact_type_required")
        if authority.factory_run_id != factory_run.id:
            raise self._fail("factory_role_evidence_source_factory_run_mismatch")
        events = self._factory_chain(factory_run)
        slots_by_kind = {
            "pm_raw_intent": self._admission_slot(factory_run=factory_run, events=events),
        }
        policy = role_final_request_policy(request.role)
        pm_event: dict[str, object] | None = None
        if (
            "pm_contract" in policy.slot_order
            or "target_files" in policy.slot_order
            or "ce_blueprint" in policy.slot_order
        ):
            pm_slots, pm_event = self._pm_static_slots(factory_run=factory_run, events=events)
            slots_by_kind.update(pm_slots)
        if "ce_blueprint" in policy.slot_order:
            if pm_event is None:
                raise self._fail("factory_role_evidence_source_pm_binding_missing")
            slots_by_kind["ce_blueprint"] = self._ce_static_slot(
                factory_run=factory_run,
                events=events,
                pm_event=pm_event,
            )
        dynamic_kinds = {"failure_feedback", "workspace_quality", "verifier_receipts"}
        if dynamic_kinds.intersection(policy.slot_order):
            slots_by_kind.update(self._capture_dynamic_slots(factory_run_id=factory_run.id))
        try:
            slots = tuple(slots_by_kind[ref_kind] for ref_kind in policy.slot_order)
        except KeyError as exc:
            raise self._fail(f"factory_role_evidence_source_slot_unavailable:{exc.args[0]}") from exc
        return FactoryRoleEvidenceResolvedCutV1(
            role=request.role,
            policy_hash=policy.policy_hash,
            slots=slots,
        )


__all__ = ["CanonicalFactoryRoleEvidenceSourceAuthority"]
