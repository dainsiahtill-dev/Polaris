"""Unit tests for EvolutionIntegrityGuard."""

from __future__ import annotations

import pytest
from polaris.kernelone.cognitive.evolution.integrity import EvolutionIntegrityGuard
from polaris.kernelone.cognitive.evolution.models import EvolutionRecord, TriggerType

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_record(
    record_id: str = "evo_001",
    timestamp: str = "2026-04-15T12:00:00+00:00",
    trigger_type: TriggerType = TriggerType.SELF_REFLECTION,
    previous_belief_id: str | None = None,
    previous_confidence: float | None = None,
    new_belief_id: str | None = "belief_001",
    new_confidence: float | None = 0.8,
    context: str = "test context",
    rationale: str = "test rationale",
    verification_needed: bool = False,
) -> EvolutionRecord:
    return EvolutionRecord(
        record_id=record_id,
        timestamp=timestamp,
        trigger_type=trigger_type,
        previous_belief_id=previous_belief_id,
        previous_confidence=previous_confidence,
        new_belief_id=new_belief_id,
        new_confidence=new_confidence,
        context=context,
        rationale=rationale,
        verification_needed=verification_needed,
    )


# ------------------------------------------------------------------
# Sign & Verify single record
# ------------------------------------------------------------------


class TestSignAndVerify:
    def test_sign_returns_hex_string(self) -> None:
        guard = EvolutionIntegrityGuard(secret_key="test-secret")
        record = _make_record()
        sig = guard.sign_record(record)
        assert isinstance(sig, str)
        assert len(sig) == 64  # SHA-256 hex digest

    def test_verify_valid_signature(self) -> None:
        guard = EvolutionIntegrityGuard(secret_key="test-secret")
        record = _make_record()
        sig = guard.sign_record(record)
        assert guard.verify_record(record, sig) is True

    def test_verify_tampered_record(self) -> None:
        guard = EvolutionIntegrityGuard(secret_key="test-secret")
        record = _make_record()
        sig = guard.sign_record(record)
        tampered = _make_record(rationale="tampered rationale")
        assert guard.verify_record(tampered, sig) is False

    def test_verify_wrong_secret(self) -> None:
        guard_a = EvolutionIntegrityGuard(secret_key="secret-a")
        guard_b = EvolutionIntegrityGuard(secret_key="secret-b")
        record = _make_record()
        sig = guard_a.sign_record(record)
        assert guard_b.verify_record(record, sig) is False

    def test_deterministic_signatures(self) -> None:
        guard = EvolutionIntegrityGuard(secret_key="test-secret")
        record = _make_record()
        sig1 = guard.sign_record(record)
        sig2 = guard.sign_record(record)
        assert sig1 == sig2

    def test_different_records_different_signatures(self) -> None:
        guard = EvolutionIntegrityGuard(secret_key="test-secret")
        r1 = _make_record(record_id="evo_001")
        r2 = _make_record(record_id="evo_002")
        assert guard.sign_record(r1) != guard.sign_record(r2)


# ------------------------------------------------------------------
# Chain operations
# ------------------------------------------------------------------


class TestChain:
    def test_sign_chain_returns_pairs(self) -> None:
        guard = EvolutionIntegrityGuard(secret_key="test-secret")
        records = [_make_record(record_id=f"evo_{i:03d}") for i in range(3)]
        signed = guard.sign_chain(records)
        assert len(signed) == 3
        for _rec, sig in signed:
            assert isinstance(sig, str)
            assert len(sig) == 64

    def test_chain_tamper_detection(self) -> None:
        guard = EvolutionIntegrityGuard(secret_key="test-secret")

        # Create records with empty context (signing context), sign them,
        # and embed signatures into context (mirrors EvolutionStore behavior).
        r1 = _make_record(record_id="evo_001", context="")
        r2 = _make_record(record_id="evo_002", context="")

        sig1 = guard.sign_record(r1)
        sig2 = guard.sign_record(r2)

        signed_r1 = EvolutionRecord(
            record_id=r1.record_id,
            timestamp=r1.timestamp,
            trigger_type=r1.trigger_type,
            previous_belief_id=r1.previous_belief_id,
            previous_confidence=r1.previous_confidence,
            new_belief_id=r1.new_belief_id,
            new_confidence=r1.new_confidence,
            context=f"chain_sig:{sig1}",
            rationale=r1.rationale,
            verification_needed=r1.verification_needed,
        )
        signed_r2 = EvolutionRecord(
            record_id=r2.record_id,
            timestamp=r2.timestamp,
            trigger_type=r2.trigger_type,
            previous_belief_id=r2.previous_belief_id,
            previous_confidence=r2.previous_confidence,
            new_belief_id=r2.new_belief_id,
            new_confidence=r2.new_confidence,
            context=f"chain_sig:{sig2}",
            rationale=r2.rationale,
            verification_needed=r2.verification_needed,
        )

        # Untampered chain should be clean
        tampered = guard.verify_chain([signed_r1, signed_r2])
        assert tampered == []

        # Tamper with the first record's rationale but keep the signature
        tampered_record = EvolutionRecord(
            record_id=signed_r1.record_id,
            timestamp=signed_r1.timestamp,
            trigger_type=signed_r1.trigger_type,
            previous_belief_id=signed_r1.previous_belief_id,
            previous_confidence=signed_r1.previous_confidence,
            new_belief_id=signed_r1.new_belief_id,
            new_confidence=0.99,  # tampered value
            context=signed_r1.context,  # keep the old signature
            rationale="tampered!",
            verification_needed=signed_r1.verification_needed,
        )
        result = guard.verify_chain([tampered_record, signed_r2])
        assert "evo_001" in result

    def test_verify_chain_no_signatures(self) -> None:
        """Records without chain_sig prefix should be skipped."""
        guard = EvolutionIntegrityGuard(secret_key="test-secret")
        records = [_make_record(context="plain context")]
        tampered = guard.verify_chain(records)
        assert tampered == []

    def test_verify_chain_empty(self) -> None:
        guard = EvolutionIntegrityGuard(secret_key="test-secret")
        assert guard.verify_chain([]) == []


# ------------------------------------------------------------------
# Environment variable fallback
# ------------------------------------------------------------------


class TestEnvSecret:
    def test_default_key_works(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("COGNITIVE_EVOLUTION_HMAC_SECRET", raising=False)
        guard = EvolutionIntegrityGuard()
        record = _make_record()
        sig = guard.sign_record(record)
        assert guard.verify_record(record, sig) is True

    def test_env_key_used(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("COGNITIVE_EVOLUTION_HMAC_SECRET", "from-env")
        guard = EvolutionIntegrityGuard()
        record = _make_record()
        sig = guard.sign_record(record)

        guard2 = EvolutionIntegrityGuard(secret_key="from-env")
        assert guard2.verify_record(record, sig) is True
