"""Tests for polaris.domain.cognitive_runtime.models."""

from __future__ import annotations

from polaris.domain.cognitive_runtime.models import (
    ChangeSetValidationResult,
    ContextHandoffPack,
    ContextSnapshot,
    DiffCellMapping,
    EditScopeLease,
    FitnessSpec,
    HandoffRehydration,
    ProjectionCompileRequest,
    PromotionDecision,
    PromotionDecisionRecord,
    Proposal,
    ReconcileResult,
    RollbackLedgerEntry,
    RuntimeReceipt,
    StructuredFindings,
    TurnEnvelope,
    _copy_mapping,
    _copy_mapping_tuple,
    _copy_optional_int,
    _copy_optional_str,
    _copy_tuple,
    _parse_structured_findings,
)


class TestCopyMapping:
    def test_none(self) -> None:
        assert _copy_mapping(None) == {}

    def test_dict(self) -> None:
        assert _copy_mapping({"a": 1}) == {"a": 1}


class TestCopyMappingTuple:
    def test_none(self) -> None:
        assert _copy_mapping_tuple(None) == ()

    def test_list(self) -> None:
        assert _copy_mapping_tuple([{"a": 1}]) == ({"a": 1},)

    def test_tuple(self) -> None:
        assert _copy_mapping_tuple(({"a": 1},)) == ({"a": 1},)

    def test_skips_non_dict(self) -> None:
        assert _copy_mapping_tuple([{"a": 1}, "skip"]) == ({"a": 1},)


class TestCopyTuple:
    def test_none(self) -> None:
        assert _copy_tuple(None) == ()

    def test_list(self) -> None:
        assert _copy_tuple(["a", "b"]) == ("a", "b")

    def test_filters_empty(self) -> None:
        assert _copy_tuple(["a", "", "  ", "b"]) == ("a", "b")


class TestCopyOptionalStr:
    def test_none(self) -> None:
        assert _copy_optional_str(None) is None

    def test_empty(self) -> None:
        assert _copy_optional_str("") is None

    def test_value(self) -> None:
        assert _copy_optional_str("hello") == "hello"


class TestCopyOptionalInt:
    def test_none(self) -> None:
        assert _copy_optional_int(None) is None

    def test_empty_str(self) -> None:
        assert _copy_optional_int("") is None

    def test_valid_int(self) -> None:
        assert _copy_optional_int(42) == 42

    def test_valid_str(self) -> None:
        assert _copy_optional_int("42") == 42

    def test_invalid(self) -> None:
        assert _copy_optional_int("abc") is None


class TestParseStructuredFindings:
    def test_none(self) -> None:
        assert _parse_structured_findings(None) is None

    def test_non_dict(self) -> None:
        assert _parse_structured_findings("not a dict") is None

    def test_valid(self) -> None:
        findings = _parse_structured_findings(
            {
                "confirmed_facts": ["f1"],
                "rejected_hypotheses": ["h1"],
                "open_questions": ["q1"],
                "relevant_refs": ["r1"],
                "source_turn_id": "t1",
                "extracted_at": "2024-01-01",
            }
        )
        assert findings is not None
        assert findings.confirmed_facts == ["f1"]
        assert findings.source_turn_id == "t1"


class TestContextSnapshot:
    def test_to_dict(self) -> None:
        snap = ContextSnapshot(
            workspace=".",
            role="director",
            query="test",
            run_id="r1",
            step=1,
            mode="interactive",
        )
        d = snap.to_dict()
        assert d["workspace"] == "."
        assert d["role"] == "director"


class TestTurnEnvelope:
    def test_defaults(self) -> None:
        env = TurnEnvelope(turn_id="t1")
        assert env.projection_version is None
        assert env.receipt_ids == ()

    def test_to_dict(self) -> None:
        env = TurnEnvelope(turn_id="t1", receipt_ids=("r1",))
        d = env.to_dict()
        assert d["turn_id"] == "t1"
        assert d["receipt_ids"] == ["r1"]

    def test_from_mapping(self) -> None:
        env = TurnEnvelope.from_mapping({"turn_id": "t1"})
        assert env is not None
        assert env.turn_id == "t1"

    def test_from_mapping_none(self) -> None:
        assert TurnEnvelope.from_mapping(None) is None

    def test_from_mapping_empty_turn_id(self) -> None:
        assert TurnEnvelope.from_mapping({}) is None

    def test_with_receipt_ids(self) -> None:
        env = TurnEnvelope(turn_id="t1", receipt_ids=("r1",))
        updated = env.with_receipt_ids(["r2", "r1"])
        assert updated.receipt_ids == ("r1", "r2")


class TestEditScopeLease:
    def test_to_dict(self) -> None:
        lease = EditScopeLease(
            lease_id="l1",
            workspace=".",
            requested_by="test",
            scope_paths=("src",),
            issued_at="2024-01-01",
            expires_at="2024-01-02",
        )
        d = lease.to_dict()
        assert d["lease_id"] == "l1"


class TestChangeSetValidationResult:
    def test_ok_property(self) -> None:
        result = ChangeSetValidationResult(
            validation_id="v1",
            workspace=".",
            changed_files=("a.py",),
            allowed_scope_paths=("src",),
            write_gate_allowed=True,
            impact_score=5,
            risk_level="medium",
        )
        assert result.ok is True

    def test_to_dict(self) -> None:
        result = ChangeSetValidationResult(
            validation_id="v1",
            workspace=".",
            changed_files=("a.py",),
            allowed_scope_paths=("src",),
            write_gate_allowed=True,
            impact_score=5,
            risk_level="medium",
        )
        d = result.to_dict()
        assert d["validation_id"] == "v1"


class TestRuntimeReceipt:
    def test_defaults(self) -> None:
        receipt = RuntimeReceipt(receipt_id="r1", receipt_type="test", workspace=".", created_at="2024-01-01")
        assert receipt.payload == {}

    def test_to_dict_no_envelope(self) -> None:
        receipt = RuntimeReceipt(receipt_id="r1", receipt_type="test", workspace=".", created_at="2024-01-01")
        d = receipt.to_dict()
        assert d["turn_envelope"] is None

    def test_to_dict_with_envelope(self) -> None:
        receipt = RuntimeReceipt(
            receipt_id="r1",
            receipt_type="test",
            workspace=".",
            created_at="2024-01-01",
            turn_envelope=TurnEnvelope(turn_id="t1"),
        )
        d = receipt.to_dict()
        assert d["turn_envelope"]["turn_id"] == "t1"

    def test_from_mapping(self) -> None:
        receipt = RuntimeReceipt.from_mapping(
            {
                "receipt_id": "r1",
                "receipt_type": "test",
                "workspace": ".",
                "created_at": "2024-01-01",
            }
        )
        assert receipt is not None
        assert receipt.receipt_id == "r1"

    def test_from_mapping_none(self) -> None:
        assert RuntimeReceipt.from_mapping(None) is None


class TestStructuredFindings:
    def test_defaults(self) -> None:
        findings = StructuredFindings()
        assert findings.confirmed_facts == []
        assert findings.source_turn_id == ""


class TestContextHandoffPack:
    def test_defaults(self) -> None:
        pack = ContextHandoffPack(handoff_id="h1", workspace=".", created_at="2024-01-01", session_id="s1")
        assert pack.run_id is None
        assert pack.artifact_refs == ()

    def test_to_dict_no_envelope(self) -> None:
        pack = ContextHandoffPack(handoff_id="h1", workspace=".", created_at="2024-01-01", session_id="s1")
        d = pack.to_dict()
        assert d["turn_envelope"] is None

    def test_to_dict_with_envelope(self) -> None:
        pack = ContextHandoffPack(
            handoff_id="h1",
            workspace=".",
            created_at="2024-01-01",
            session_id="s1",
            turn_envelope=TurnEnvelope(turn_id="t1"),
        )
        d = pack.to_dict()
        assert d["turn_envelope"]["turn_id"] == "t1"

    def test_from_mapping(self) -> None:
        pack = ContextHandoffPack.from_mapping(
            {
                "handoff_id": "h1",
                "workspace": ".",
                "created_at": "2024-01-01",
                "session_id": "s1",
            }
        )
        assert pack is not None
        assert pack.handoff_id == "h1"

    def test_from_mapping_none(self) -> None:
        assert ContextHandoffPack.from_mapping(None) is None

    def test_from_mapping_with_structured_findings(self) -> None:
        pack = ContextHandoffPack.from_mapping(
            {
                "handoff_id": "h1",
                "workspace": ".",
                "created_at": "2024-01-01",
                "session_id": "s1",
                "structured_findings": {
                    "confirmed_facts": ["f1"],
                },
            }
        )
        assert pack is not None
        assert pack.structured_findings is not None
        assert pack.structured_findings.confirmed_facts == ["f1"]


class TestHandoffRehydration:
    def test_to_dict(self) -> None:
        rehydration = HandoffRehydration(
            rehydration_id="r1",
            handoff_id="h1",
            workspace=".",
            created_at="2024-01-01",
            target_role="director",
        )
        d = rehydration.to_dict()
        assert d["rehydration_id"] == "r1"

    def test_from_mapping(self) -> None:
        rehydration = HandoffRehydration.from_mapping(
            {
                "rehydration_id": "r1",
                "handoff_id": "h1",
                "workspace": ".",
                "created_at": "2024-01-01",
                "target_role": "director",
            }
        )
        assert rehydration is not None
        assert rehydration.rehydration_id == "r1"

    def test_from_mapping_none(self) -> None:
        assert HandoffRehydration.from_mapping(None) is None

    def test_from_mapping_missing_required(self) -> None:
        assert HandoffRehydration.from_mapping({"rehydration_id": "r1"}) is None


class TestDiffCellMapping:
    def test_to_dict(self) -> None:
        mapping = DiffCellMapping(
            mapping_id="m1",
            workspace=".",
            created_at="2024-01-01",
            graph_catalog_path="cells.yaml",
        )
        d = mapping.to_dict()
        assert d["mapping_id"] == "m1"

    def test_from_mapping(self) -> None:
        mapping = DiffCellMapping.from_mapping(
            {
                "mapping_id": "m1",
                "workspace": ".",
                "created_at": "2024-01-01",
                "graph_catalog_path": "cells.yaml",
                "file_to_cells": {"a.py": ["cell1"]},
            }
        )
        assert mapping is not None
        assert mapping.file_to_cells == {"a.py": ("cell1",)}

    def test_from_mapping_none(self) -> None:
        assert DiffCellMapping.from_mapping(None) is None


class TestProjectionCompileRequest:
    def test_to_dict(self) -> None:
        req = ProjectionCompileRequest(
            request_id="r1",
            workspace=".",
            created_at="2024-01-01",
            requested_by="test",
            subject_ref="s1",
            status="queued",
        )
        d = req.to_dict()
        assert d["request_id"] == "r1"

    def test_from_mapping(self) -> None:
        req = ProjectionCompileRequest.from_mapping(
            {
                "request_id": "r1",
                "workspace": ".",
                "created_at": "2024-01-01",
                "requested_by": "test",
                "subject_ref": "s1",
                "status": "queued",
            }
        )
        assert req is not None
        assert req.request_id == "r1"

    def test_from_mapping_none(self) -> None:
        assert ProjectionCompileRequest.from_mapping(None) is None


class TestPromotionDecisionRecord:
    def test_to_dict(self) -> None:
        record = PromotionDecisionRecord(
            decision_id="d1",
            workspace=".",
            created_at="2024-01-01",
            subject_ref="s1",
            decision="promote",
        )
        d = record.to_dict()
        assert d["decision_id"] == "d1"

    def test_from_mapping(self) -> None:
        record = PromotionDecisionRecord.from_mapping(
            {
                "decision_id": "d1",
                "workspace": ".",
                "created_at": "2024-01-01",
                "subject_ref": "s1",
                "decision": "promote",
            }
        )
        assert record is not None
        assert record.decision == "promote"

    def test_from_mapping_none(self) -> None:
        assert PromotionDecisionRecord.from_mapping(None) is None


class TestRollbackLedgerEntry:
    def test_to_dict(self) -> None:
        entry = RollbackLedgerEntry(
            rollback_id="r1",
            workspace=".",
            created_at="2024-01-01",
            subject_ref="s1",
            reason="test",
        )
        d = entry.to_dict()
        assert d["rollback_id"] == "r1"

    def test_from_mapping(self) -> None:
        entry = RollbackLedgerEntry.from_mapping(
            {
                "rollback_id": "r1",
                "workspace": ".",
                "created_at": "2024-01-01",
                "subject_ref": "s1",
                "reason": "test",
            }
        )
        assert entry is not None
        assert entry.reason == "test"

    def test_from_mapping_none(self) -> None:
        assert RollbackLedgerEntry.from_mapping(None) is None


class TestPromotionDecision:
    def test_to_dict(self) -> None:
        decision = PromotionDecision(decision_id="d1", subject_ref="s1", status="approved")
        d = decision.to_dict()
        assert d["decision_id"] == "d1"


class TestReconcileResult:
    def test_to_dict(self) -> None:
        result = ReconcileResult(ok=True, proposal_id="p1", decision="approve")
        d = result.to_dict()
        assert d["ok"] is True


class TestProposal:
    def test_to_dict(self) -> None:
        proposal = Proposal(proposal_id="p1", proposal_type="test", subject_ref="s1")
        d = proposal.to_dict()
        assert d["proposal_id"] == "p1"


class TestFitnessSpec:
    def test_to_dict(self) -> None:
        spec = FitnessSpec(spec_id="f1", criteria={"coverage": 0.8})
        d = spec.to_dict()
        assert d["spec_id"] == "f1"
