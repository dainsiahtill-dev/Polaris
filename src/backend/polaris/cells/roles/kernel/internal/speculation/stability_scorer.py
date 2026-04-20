from __future__ import annotations

import hashlib
import json
import time

from polaris.cells.roles.kernel.internal.speculation.models import (
    CandidateToolCall,
    FieldMutation,
    ParseState,
)

# Score component weights (must sum to 1.0)
_WEIGHT_SCHEMA_VALID = 0.25
_WEIGHT_END_TAG_SEEN = 0.15
_WEIGHT_CRITICAL_FIELD_QUIESCENCE = 0.35
_WEIGHT_OVERWRITE_PENALTY = 0.15
_WEIGHT_CANONICAL_HASH_CONSISTENCY = 0.10

# Critical fields that must stabilize for high-confidence speculation
_CRITICAL_FIELDS = frozenset({"path", "query", "command", "content", "tool_name"})

# Quiescence window in milliseconds
_QUIESCENCE_WINDOW_MS = 120.0


class StabilityScorer:
    """Compute a stability score for a ``CandidateToolCall``.

    The score is a weighted combination of:
    - schema_valid (25%)
    - end_tag_seen (15%)
    - critical_field_quiescence (35%)
    - overwrite_penalty (15%)
    - canonical_hash_consistency (10%)

    A score \u003e= 0.82 is the default threshold for triggering speculative
    execution (see ``ToolSpecPolicy.min_stability_score``).
    """

    def __init__(
        self,
        *,
        quiescence_window_ms: float = _QUIESCENCE_WINDOW_MS,
    ) -> None:
        """Initialize the scorer.

        Args:
            quiescence_window_ms: Time window in ms during which no critical
                field mutation must occur for full quiescence credit.
        """
        self._quiescence_window_ms = quiescence_window_ms
        self._last_seen_hashes: dict[str, str] = {}

    def score(self, candidate: CandidateToolCall) -> float:
        """Calculate the stability score for ``candidate``.

        Args:
            candidate: The candidate tool call to evaluate.

        Returns:
            A float in the range ``[0.0, 1.0]``.
        """
        if not candidate.partial_args and not candidate.tool_name:
            return 0.0

        # 1. Schema valid component
        schema_component = _WEIGHT_SCHEMA_VALID if candidate.schema_valid else 0.0

        # 2. End tag component
        end_tag_component = _WEIGHT_END_TAG_SEEN if candidate.end_tag_seen else 0.0

        # 3. Critical field quiescence component
        quiescence_component = _WEIGHT_CRITICAL_FIELD_QUIESCENCE * self._critical_field_quiescence(candidate)

        # 4. Overwrite penalty component
        overwrite_component = _WEIGHT_OVERWRITE_PENALTY * self._overwrite_factor(candidate)

        # 5. Canonical hash consistency component
        hash_component = _WEIGHT_CANONICAL_HASH_CONSISTENCY * self._canonical_hash_consistency(candidate)

        total = schema_component + end_tag_component + quiescence_component + overwrite_component + hash_component
        # Clamp to [0.0, 1.0]
        return max(0.0, min(1.0, total))

    def update_parse_state(self, candidate: CandidateToolCall) -> ParseState:
        """Derive the parse state from the candidate's current characteristics.

        This method may *regress* the parse state if stability signals degrade
        (e.g. a critical field is overwritten).

        Args:
            candidate: The candidate to evaluate and update in-place.

        Returns:
            The updated parse state.
        """
        current_score = self.score(candidate)

        # Determine base state from structural properties
        base_state: ParseState
        if candidate.schema_valid and candidate.end_tag_seen:
            base_state = "schema_valid"
        elif candidate.end_tag_seen:
            base_state = "syntactic_complete"
        elif candidate.partial_args:
            base_state = "incomplete"
        else:
            base_state = "incomplete"

        # Semantic stability requires:
        #   - schema_valid base state
        #   - score above the typical policy threshold (0.82)
        #   - no recent critical field overwrite
        if (
            base_state == "schema_valid"
            and current_score >= 0.82
            and not self._has_recent_critical_overwrite(candidate)
        ):
            candidate.parse_state = "semantically_stable"
        else:
            candidate.parse_state = base_state

        candidate.stability_score = current_score
        return candidate.parse_state

    def _critical_field_quiescence(self, candidate: CandidateToolCall) -> float:
        """Return a factor in [0.0, 1.0] based on critical field stability.

        If no critical field has changed in the last quiescence window,
        returns 1.0. If a critical field changed very recently, returns 0.0.
        """
        now_ms = time.monotonic() * 1000.0
        last_critical_mutation_ms = self._last_critical_mutation_ms(candidate)

        if last_critical_mutation_ms is None:
            # No critical field has ever been mutated — consider fully quiescent
            # if we have at least one critical field present
            if any(k in candidate.partial_args for k in _CRITICAL_FIELDS):
                return 1.0
            return 0.5

        elapsed_ms = now_ms - last_critical_mutation_ms
        if elapsed_ms >= self._quiescence_window_ms:
            return 1.0
        # Linear ramp from 0.0 to 1.0 across the window
        return elapsed_ms / self._quiescence_window_ms

    def _overwrite_factor(self, candidate: CandidateToolCall) -> float:
        """Return a penalty factor in [0.0, 1.0].

        1.0 means no overwrites; 0.0 means a critical field was recently
        overwritten.
        """
        if self._has_recent_critical_overwrite(candidate):
            return 0.0
        return 1.0

    def _canonical_hash_consistency(self, candidate: CandidateToolCall) -> float:
        """Return 1.0 if the candidate's canonical hash is stable across calls.

        Computes a deterministic hash of the current critical fields and
        compares it to the previous invocation for the same candidate.
        """
        current_hash = self._compute_canonical_hash(candidate)
        previous_hash = self._last_seen_hashes.get(candidate.candidate_id)

        if previous_hash is None:
            # First time seeing this candidate — neutral
            self._last_seen_hashes[candidate.candidate_id] = current_hash
            return 0.5

        if current_hash == previous_hash:
            return 1.0

        # Hash changed — update tracking and penalize
        self._last_seen_hashes[candidate.candidate_id] = current_hash
        return 0.0

    def _compute_canonical_hash(self, candidate: CandidateToolCall) -> str:
        """Compute a SHA-256 hash of critical fields in canonical order."""
        payload = {
            "tool_name": candidate.tool_name,
            "args": {k: candidate.partial_args.get(k) for k in sorted(_CRITICAL_FIELDS) if k in candidate.partial_args},
        }
        text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _last_critical_mutation_ms(self, candidate: CandidateToolCall) -> float | None:
        """Return the timestamp (in ms) of the most recent critical mutation."""
        critical_mutations = [m for m in candidate.mutation_history if m.field_path in _CRITICAL_FIELDS]
        if not critical_mutations:
            return None
        return max(m.ts_monotonic for m in critical_mutations) * 1000.0

    def _has_recent_critical_overwrite(self, candidate: CandidateToolCall) -> bool:
        """Return True if a critical field was overwritten in the last window."""
        last_ms = self._last_critical_mutation_ms(candidate)
        if last_ms is None:
            return False
        now_ms = time.monotonic() * 1000.0
        return (now_ms - last_ms) < self._quiescence_window_ms

    def _is_critical_overwrite(self, mutation: FieldMutation) -> bool:
        """Return True if the mutation represents a critical field overwrite."""
        return mutation.field_path in _CRITICAL_FIELDS and mutation.old_value is not ...
