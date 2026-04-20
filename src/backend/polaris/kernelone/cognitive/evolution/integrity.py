"""Evolution Record Integrity - HMAC-SHA256 chain verification."""

from __future__ import annotations

import hashlib
import hmac
import os

from polaris.kernelone.cognitive.evolution.models import EvolutionRecord


class EvolutionIntegrityGuard:
    """HMAC-SHA256 integrity guard for evolution records.

    Each record is signed over its canonical field representation.
    Records can optionally form a hash chain where each signature
    incorporates the previous record's signature.
    """

    def __init__(self, secret_key: str | None = None) -> None:
        raw = secret_key or os.environ.get(
            "COGNITIVE_EVOLUTION_HMAC_SECRET",
            "default-dev-key-change-in-prod",
        )
        self._secret = raw.encode("utf-8")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def sign_record(self, record: EvolutionRecord) -> str:
        """Generate an HMAC-SHA256 signature for *record*.

        The signature covers the canonical byte representation of the
        record's identity and data fields.
        """
        payload = self._canonical_bytes(record)
        return hmac.new(self._secret, payload, hashlib.sha256).hexdigest()

    def verify_record(self, record: EvolutionRecord, signature: str) -> bool:
        """Verify a single record's HMAC signature.

        Returns ``True`` when the computed signature matches *signature*.
        """
        expected = self.sign_record(record)
        return hmac.compare_digest(expected, signature)

    def verify_chain(self, records: list[EvolutionRecord]) -> list[str]:
        """Verify an ordered list of evolution records.

        Returns:
            A list of ``record_id`` values for records whose signature
            is invalid.  An empty list means the full chain is intact.
        """
        tampered: list[str] = []
        for record in records:
            sig = self._extract_chain_signature(record)
            if sig is None:
                continue
            # Reconstruct the original record (before signature was embedded
            # into the context field) so we can verify against it.
            original = self._strip_chain_signature(record)
            if not self.verify_record(original, sig):
                tampered.append(record.record_id)
        return tampered

    def sign_chain(
        self,
        records: list[EvolutionRecord],
    ) -> list[tuple[EvolutionRecord, str]]:
        """Sign an ordered chain and return ``(record, signature)`` pairs.

        Each signature incorporates the previous record's signature so that
        the chain is tamper-evident: modifying any record invalidates all
        subsequent signatures.
        """
        prev_sig = ""
        signed: list[tuple[EvolutionRecord, str]] = []
        for record in records:
            payload = self._canonical_bytes(record) + prev_sig.encode("utf-8")
            sig = hmac.new(self._secret, payload, hashlib.sha256).hexdigest()
            signed.append((record, sig))
            prev_sig = sig
        return signed

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _canonical_bytes(record: EvolutionRecord) -> bytes:
        """Produce a deterministic byte representation of *record*."""
        parts = [
            record.record_id,
            record.timestamp,
            record.trigger_type.value,
            str(record.previous_belief_id),
            str(record.previous_confidence),
            str(record.new_belief_id),
            str(record.new_confidence),
            record.context,
            record.rationale,
            str(record.verification_needed),
        ]
        return "|".join(parts).encode("utf-8")

    @staticmethod
    def _extract_chain_signature(record: EvolutionRecord) -> str | None:
        """Try to recover a chain signature stored in ``context``."""
        prefix = "chain_sig:"
        if record.context.startswith(prefix):
            return record.context[len(prefix) :]
        return None

    @staticmethod
    def _strip_chain_signature(record: EvolutionRecord) -> EvolutionRecord:
        """Return a copy of *record* with the ``chain_sig:`` prefix removed from context."""
        prefix = "chain_sig:"
        if record.context.startswith(prefix):
            return EvolutionRecord(
                record_id=record.record_id,
                timestamp=record.timestamp,
                trigger_type=record.trigger_type,
                previous_belief_id=record.previous_belief_id,
                previous_confidence=record.previous_confidence,
                new_belief_id=record.new_belief_id,
                new_confidence=record.new_confidence,
                context="",
                rationale=record.rationale,
                verification_needed=record.verification_needed,
            )
        return record
