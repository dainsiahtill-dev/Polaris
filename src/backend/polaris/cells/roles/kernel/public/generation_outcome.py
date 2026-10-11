"""Bounded, non-authoritative facts from one fresh nonstream generation."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from hashlib import sha256
from typing import Any

from polaris.kernelone.llm.budget_policy import HARD_OUTPUT_TOKEN_CLAMP

GENERATION_OUTCOME_SCHEMA = "roles.kernel.nonstream_generation_outcome.v1"


@dataclass(frozen=True, slots=True)
class RoleGenerationOutcomeV1:
    schema_version: str
    run_id: str
    call_id: str
    native_finish_reason: str | None
    max_tokens: int | None
    response_sha256: str
    request_freeze_id: str | None = None
    final_semantic_request_hash: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != GENERATION_OUTCOME_SCHEMA:
            raise ValueError("invalid generation outcome schema")
        if not isinstance(self.run_id, str) or not self.run_id.strip() or len(self.run_id) > 256:
            raise ValueError("invalid generation run identity")
        for value, size in ((self.call_id, 32), (self.response_sha256, 64)):
            if not isinstance(value, str) or not re.fullmatch(rf"[0-9a-f]{{{size}}}", value):
                raise ValueError("invalid generation identity/hash")
        if self.native_finish_reason is not None and (
            not isinstance(self.native_finish_reason, str)
            or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", self.native_finish_reason)
        ):
            raise ValueError("invalid native reason")
        if self.max_tokens is not None and (
            type(self.max_tokens) is not int or not 0 < self.max_tokens <= HARD_OUTPUT_TOKEN_CLAMP
        ):
            raise ValueError("invalid observed allowance")
        if (self.request_freeze_id is None) != (self.final_semantic_request_hash is None):
            raise ValueError("partial freeze binding")
        if self.request_freeze_id is not None:
            for freeze_value, size in ((self.request_freeze_id, 32), (self.final_semantic_request_hash, 64)):
                if not isinstance(freeze_value, str) or not re.fullmatch(rf"[0-9a-f]{{{size}}}", freeze_value):
                    raise ValueError("invalid freeze binding")

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_payload(cls, value: object) -> RoleGenerationOutcomeV1 | None:
        if not isinstance(value, Mapping) or set(value) != set(cls.__dataclass_fields__):
            return None
        try:
            return cls(**dict(value))
        except (TypeError, ValueError):
            return None


def bind_role_generation_outcome(
    metadata: Mapping[str, Any] | None, *, response_text: str, expected_run_id: str
) -> tuple[RoleGenerationOutcomeV1 | None, str | None]:
    """Digest mismatch means association unavailable; it establishes no cause."""
    if not isinstance(metadata, Mapping) or "generation_outcome" not in metadata:
        return None, "generation_outcome_missing"
    outcome = RoleGenerationOutcomeV1.from_payload(metadata["generation_outcome"])
    if outcome is None:
        return None, "generation_outcome_malformed"
    if outcome.run_id != expected_run_id:
        return None, "generation_outcome_run_mismatch"
    if outcome.response_sha256 != sha256(response_text.encode("utf-8")).hexdigest():
        return None, "generation_outcome_response_binding_unavailable"
    return outcome, None


__all__ = ["RoleGenerationOutcomeV1", "bind_role_generation_outcome"]
