"""A009B3-B3.0 runtime-private Factory evidence binding contracts."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, fields, replace

import pytest
from polaris.cells.roles.kernel.internal.llm_caller.factory_role_evidence_binding import (
    FactoryRoleEvidenceBindingV1,
    FactoryRoleEvidenceSourceHeadV1,
    bind_factory_role_evidence,
    get_factory_role_evidence_binding,
)
from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import (
    FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING_SCHEMA,
    FactoryRoleEvidenceAuthorityBindingV1,
    FactoryRoleEvidenceCutoffAckV1,
    FactoryRoleEvidenceCutoffPort,
    FactoryRoleEvidenceCutoffProofV1,
    FactoryRoleEvidenceCutoffRequestV1,
    bind_factory_role_evidence_authority,
    contains_factory_role_evidence_runtime_authority,
    get_factory_role_evidence_authority_binding,
)
from polaris.kernelone.events.final_request_evidence import (
    RoleFinalRequestEvidenceAnchorV1,
    RoleFinalRequestEvidenceSlotV1,
    RoleFinalRequestPolicyFactsV1,
)


class _CutoffPort:
    async def acquire_cutoff(
        self,
        request: FactoryRoleEvidenceCutoffRequestV1,
    ) -> FactoryRoleEvidenceCutoffAckV1:
        del request
        raise AssertionError("contract-only fake must not be called")

    async def resolve_cutoff_proof(
        self,
        *,
        ack: FactoryRoleEvidenceCutoffAckV1,
    ) -> FactoryRoleEvidenceCutoffProofV1:
        del ack
        raise AssertionError("contract-only fake must not be called")


class _SlotWrapper:
    __slots__ = ("payload",)

    def __init__(self, payload: object) -> None:
        self.payload = payload


class _RaisingSlotWrapper:
    __slots__ = ("payload",)

    def __getattribute__(self, name: str) -> object:
        if name == "payload":
            raise RuntimeError("hostile descriptor")
        return super().__getattribute__(name)


@dataclass
class _RaisingDataclassWrapper:
    payload: object

    def __getattribute__(self, name: str) -> object:
        if name == "payload":
            raise RuntimeError("hostile dataclass attribute")
        return super().__getattribute__(name)


def _authority_binding(**overrides: object) -> FactoryRoleEvidenceAuthorityBindingV1:
    values: dict[str, object] = {
        "schema_version": FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING_SCHEMA,
        "verification_scope": "factory",
        "factory_run_id": "factory-run-1",
        "role": "pm",
        "cutoff_port": _CutoffPort(),
        "attempt_budget": 3,
        "execution_authority_hash": "a" * 64,
    }
    values.update(overrides)
    return FactoryRoleEvidenceAuthorityBindingV1(**values)  # type: ignore[arg-type]


def _proof_binding() -> FactoryRoleEvidenceBindingV1:
    role = "chief_engineer"
    slots = tuple(
        RoleFinalRequestEvidenceSlotV1.create(
            ref_kind=ref_kind,
            state="present" if ref_kind != "workspace_quality" else "absent_at_request_time",
            canonical_source_ref=f"factory/sources/{ref_kind}",
            source_fact_schema="polaris.test_fact.v1",
            source_fact_version="1",
            factory_run_id="factory-run-proof",
            run_id="role-run-proof",
            role=role,
            request_freeze_id="freeze-proof",
            cutoff_fact_id="cutoff-proof",
            cutoff_fact_sequence=7,
            cutoff_fact_hash="b" * 64,
            source_head_sequence=4,
            source_head_hash="b" * 64,
            execution_authority_hash="b" * 64,
            items=(
                RoleFinalRequestEvidenceAnchorV1.create(
                    ref_kind=ref_kind,
                    canonical_source_ref=f"factory/sources/{ref_kind}",
                    canonical_ref=f"runtime/facts/{ref_kind}/item-proof.json",
                    canonical_hash="b" * 64,
                    source_fact_schema="polaris.test_fact.v1",
                    source_fact_version="1",
                    factory_run_id="factory-run-proof",
                    run_id="role-run-proof",
                    role=role,
                    request_freeze_id="freeze-proof",
                    cutoff_fact_id="cutoff-proof",
                    cutoff_fact_sequence=7,
                    cutoff_fact_hash="b" * 64,
                    source_fact_id=f"fact-{ref_kind}-proof",
                    source_fact_sequence=3,
                    source_fact_hash="b" * 64,
                    source_head_sequence=4,
                    source_head_hash="b" * 64,
                    execution_authority_hash="b" * 64,
                ),
            )
            if ref_kind != "workspace_quality"
            else (),
        )
        for ref_kind in ("pm_contract", "target_files", "workspace_quality")
    )
    facts = RoleFinalRequestPolicyFactsV1.create(role=role, slots=slots)
    source_heads = tuple(
        FactoryRoleEvidenceSourceHeadV1(
            canonical_source_ref=slot.canonical_source_ref,
            source_fact_schema=slot.source_fact_schema,
            source_fact_version=slot.source_fact_version,
            source_head_fact_id=f"head-{slot.ref_kind}-proof",
            source_head_sequence=slot.source_head_sequence,
            source_head_hash=slot.source_head_hash,
        )
        for slot in slots
    )
    ack = FactoryRoleEvidenceCutoffAckV1(
        schema_version="polaris.factory_role_evidence_cutoff_ack.v1",
        factory_run_id="factory-run-proof",
        run_id="role-run-proof",
        role=role,
        turn_id="turn-proof",
        call_id="call-proof",
        request_freeze_id="freeze-proof",
        semantic_candidate_hash="a" * 64,
        attempt_budget=3,
        execution_authority_hash="b" * 64,
        authority_stream=("factory.role_evidence_authority." + hashlib.sha256(b"factory-run-proof").hexdigest()),
        cutoff_fact_id="cutoff-proof",
        cutoff_fact_sequence=7,
        cutoff_fact_hash="b" * 64,
        cutoff_body_hash="c" * 64,
        cutoff_fragment_vector_hash="d" * 64,
        cutoff_fragment_count=2,
    )
    proof = FactoryRoleEvidenceCutoffProofV1.create(
        ack=ack,
        source_head_vector=source_heads,
        policy_facts=facts,
    )
    return FactoryRoleEvidenceBindingV1.from_cutoff_proof(proof)


def test_pre_cutoff_authority_binding_has_only_honest_runtime_fields() -> None:
    binding = _authority_binding()

    assert isinstance(binding.cutoff_port, FactoryRoleEvidenceCutoffPort)
    assert tuple(field.name for field in fields(binding)) == (
        "schema_version",
        "verification_scope",
        "factory_run_id",
        "role",
        "cutoff_port",
        "attempt_budget",
        "execution_authority_hash",
    )
    assert not hasattr(binding, "to_record")
    assert not hasattr(binding, "from_record")
    assert not hasattr(binding, "cutoff_fact_id")
    assert not hasattr(binding, "source_head_vector")
    assert not hasattr(binding, "policy_facts")
    assert not hasattr(binding, "request_freeze_id")


def test_runtime_authority_leak_predicate_recurses_slots_mappings_and_ports() -> None:
    binding = _authority_binding()

    assert contains_factory_role_evidence_runtime_authority(binding) is True
    assert contains_factory_role_evidence_runtime_authority({"nested": [(binding,)]}) is True
    assert contains_factory_role_evidence_runtime_authority({"nested": binding.cutoff_port}) is True
    assert contains_factory_role_evidence_runtime_authority(_SlotWrapper(binding)) is True
    assert contains_factory_role_evidence_runtime_authority(_SlotWrapper(_SlotWrapper(binding.cutoff_port))) is True
    assert contains_factory_role_evidence_runtime_authority({"safe": ["opaque", 32, None]}) is False

    cycle = _SlotWrapper(None)
    cycle.payload = cycle
    assert contains_factory_role_evidence_runtime_authority(cycle) is False
    assert contains_factory_role_evidence_runtime_authority(_RaisingSlotWrapper()) is True


def test_runtime_authority_leak_predicate_fails_closed_on_deep_mro_slots() -> None:
    base = type("_DeepSlotBase", (), {"__slots__": ("payload",)})
    derived = base
    for index in range(65):
        derived = type(f"_DeepSlotLevel{index}", (derived,), {"__slots__": ()})
    candidate = derived()
    candidate.payload = _authority_binding()

    assert len(type(candidate).__mro__) > 64
    assert contains_factory_role_evidence_runtime_authority(candidate) is True


def test_runtime_authority_leak_predicate_fails_closed_on_dataclass_attribute_error() -> None:
    candidate = _RaisingDataclassWrapper(payload="safe")

    assert contains_factory_role_evidence_runtime_authority(candidate) is True


@pytest.mark.parametrize(
    ("override", "error_type", "error"),
    [
        ({"schema_version": 1}, TypeError, "schema_version_type_invalid"),
        ({"schema_version": "wrong"}, ValueError, "factory_role_evidence_authority_binding_schema_mismatch"),
        ({"verification_scope": 1}, TypeError, "verification_scope_type_invalid"),
        ({"verification_scope": "other"}, ValueError, "verification_scope_mismatch"),
        ({"factory_run_id": 1}, TypeError, "factory_run_id_type_invalid"),
        ({"factory_run_id": "  "}, ValueError, "factory_run_id_missing"),
        ({"role": 1}, TypeError, "role_type_invalid"),
        ({"role": "unknown"}, ValueError, "role_final_request_policy_unknown_role:unknown"),
        ({"cutoff_port": object()}, TypeError, "factory_role_evidence_cutoff_port_required"),
        ({"attempt_budget": True}, TypeError, "attempt_budget_type_invalid"),
        ({"attempt_budget": 0}, ValueError, "attempt_budget_invalid"),
        ({"execution_authority_hash": 1}, TypeError, "execution_authority_hash_type_invalid"),
        ({"execution_authority_hash": "A" * 64}, ValueError, "execution_authority_hash_invalid"),
    ],
)
def test_pre_cutoff_authority_binding_rejects_malformed_fields(
    override: dict[str, object],
    error_type: type[Exception],
    error: str,
) -> None:
    with pytest.raises(error_type, match=error):
        _authority_binding(**override)


def test_authority_binder_requires_exact_typed_value() -> None:
    binding = _authority_binding()

    class _BindingSubclass(FactoryRoleEvidenceAuthorityBindingV1):
        pass

    subclass = _BindingSubclass(
        schema_version=binding.schema_version,
        verification_scope=binding.verification_scope,
        factory_run_id=binding.factory_run_id,
        role=binding.role,
        cutoff_port=binding.cutoff_port,
        attempt_budget=binding.attempt_budget,
        execution_authority_hash=binding.execution_authority_hash,
    )
    with (
        pytest.raises(TypeError, match="factory_role_evidence_authority_binding_exact_type_required"),
        bind_factory_role_evidence_authority(subclass),
    ):
        pytest.fail("subclass binding must not enter context")
    with (
        pytest.raises(TypeError, match="factory_role_evidence_authority_binding_exact_type_required"),
        bind_factory_role_evidence_authority({}),  # type: ignore[arg-type]
    ):
        pytest.fail("mapping binding must not enter context")


def test_authority_binder_revalidates_tampered_exact_instance() -> None:
    binding = _authority_binding()
    object.__setattr__(binding, "attempt_budget", 0)

    with pytest.raises(ValueError, match="attempt_budget_invalid"), bind_factory_role_evidence_authority(binding):
        pytest.fail("tampered binding must not enter context")
    assert get_factory_role_evidence_authority_binding() is None


def test_authority_binding_nested_restore_and_exception_cleanup() -> None:
    outer = _authority_binding(factory_run_id="factory-outer")
    inner = _authority_binding(factory_run_id="factory-inner", role="qa")

    assert get_factory_role_evidence_authority_binding() is None
    with bind_factory_role_evidence_authority(outer):
        assert get_factory_role_evidence_authority_binding() is outer
        with pytest.raises(RuntimeError, match="boom"), bind_factory_role_evidence_authority(inner):
            assert get_factory_role_evidence_authority_binding() is inner
            raise RuntimeError("boom")
        assert get_factory_role_evidence_authority_binding() is outer
    assert get_factory_role_evidence_authority_binding() is None


def test_pre_and_post_cutoff_bindings_coexist_without_aliasing() -> None:
    authority = _authority_binding()
    proof = _proof_binding()

    with bind_factory_role_evidence_authority(authority):
        assert get_factory_role_evidence_authority_binding() is authority
        assert get_factory_role_evidence_binding() is None
        with bind_factory_role_evidence(proof):
            assert get_factory_role_evidence_authority_binding() is authority
            assert get_factory_role_evidence_binding() is proof
        assert get_factory_role_evidence_authority_binding() is authority
        assert get_factory_role_evidence_binding() is None
    assert get_factory_role_evidence_authority_binding() is None


def test_post_cutoff_binding_requires_nested_exact_public_cutoff_proof() -> None:
    proof = _proof_binding()

    assert "cutoff_proof" in {field.name for field in fields(proof)}
    from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import (
        FactoryRoleEvidenceCutoffProofV1,
    )

    assert type(proof.cutoff_proof) is FactoryRoleEvidenceCutoffProofV1
    assert proof.signed_factory_binding_ref == proof.cutoff_proof.signed_factory_binding_ref
    assert proof.signed_factory_binding_hash == proof.cutoff_proof.signed_factory_binding_hash
    assert proof.source_head_vector == proof.cutoff_proof.source_head_vector
    assert proof.policy_facts == proof.cutoff_proof.policy_facts


def test_arbitrary_well_shaped_binding_hash_cannot_validate_without_matching_nested_proof() -> None:
    proof = _proof_binding()
    forged = replace(proof, signed_factory_binding_hash="c" * 64)

    assert forged.validation_error(expected_role="chief_engineer") == (
        "signed_factory_binding_hash_proof_projection_mismatch"
    )


def test_post_cutoff_binder_rejects_subclass_uninitialized_and_tampered_proof() -> None:
    proof = _proof_binding()

    class _ForgedProof(FactoryRoleEvidenceBindingV1):
        def validation_error(self, *, expected_role: str) -> str:
            del expected_role
            return ""

    forged = _ForgedProof(**{field.name: getattr(proof, field.name) for field in fields(proof)})
    with (
        pytest.raises(TypeError, match="factory_role_evidence_binding_exact_type_required"),
        bind_factory_role_evidence(forged),
    ):
        pytest.fail("post-cutoff subclass must not enter context")

    uninitialized = object.__new__(FactoryRoleEvidenceBindingV1)
    with (
        pytest.raises(RuntimeError, match="factory_role_evidence_binding_malformed:unreadable"),
        bind_factory_role_evidence(uninitialized),
    ):
        pytest.fail("uninitialized post-cutoff proof must not enter context")

    object.__setattr__(proof, "source_head_vector_hash", "0" * 64)
    with (
        pytest.raises(
            RuntimeError,
            match=("factory_role_evidence_binding_malformed:source_head_vector_hash_proof_projection_mismatch"),
        ),
        bind_factory_role_evidence(proof),
    ):
        pytest.fail("tampered post-cutoff proof must not enter context")
    assert get_factory_role_evidence_binding() is None


def test_authority_binding_isolated_across_concurrent_tasks() -> None:
    async def _run() -> None:
        first = _authority_binding(factory_run_id="factory-first")
        second = _authority_binding(factory_run_id="factory-second", role="director")
        entered = [asyncio.Event(), asyncio.Event()]
        release = asyncio.Event()

        async def _observe(
            binding: FactoryRoleEvidenceAuthorityBindingV1,
            ready: asyncio.Event,
        ) -> FactoryRoleEvidenceAuthorityBindingV1 | None:
            with bind_factory_role_evidence_authority(binding):
                ready.set()
                await release.wait()
                return get_factory_role_evidence_authority_binding()

        tasks = [
            asyncio.create_task(_observe(first, entered[0])),
            asyncio.create_task(_observe(second, entered[1])),
        ]
        await asyncio.gather(*(event.wait() for event in entered))
        assert get_factory_role_evidence_authority_binding() is None
        release.set()
        assert await asyncio.gather(*tasks) == [first, second]
        assert get_factory_role_evidence_authority_binding() is None

    asyncio.run(_run())


def test_authority_binding_resets_after_task_cancellation() -> None:
    async def _run() -> None:
        binding = _authority_binding()
        entered = asyncio.Event()
        observed_after_exit: list[FactoryRoleEvidenceAuthorityBindingV1 | None] = []

        async def _wait_forever() -> None:
            try:
                with bind_factory_role_evidence_authority(binding):
                    entered.set()
                    await asyncio.Future()
            finally:
                observed_after_exit.append(get_factory_role_evidence_authority_binding())

        task = asyncio.create_task(_wait_forever())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert observed_after_exit == [None]
        assert get_factory_role_evidence_authority_binding() is None

    asyncio.run(_run())
