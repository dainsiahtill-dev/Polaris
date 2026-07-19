"""Private non-executable contracts for speculation shadow keys."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True, slots=True)
class SyntheticShadowToolKeyV1:
    """A non-executable, immutable key for private speculation shadow state."""

    source_tool_call_id: str
    canonical_tool_name: str
    shadow_phase: Literal["candidate", "resolver", "write_phase"]
    shadow_key_hash: str = field(init=False)
    executable: Literal[False] = field(init=False, default=False)

    def __post_init__(self) -> None:
        if not self.source_tool_call_id:
            raise ValueError("source_tool_call_id must be non-empty")
        if not self.canonical_tool_name.startswith("__"):
            raise ValueError("synthetic shadow canonical_tool_name must be private")

        payload = {
            "kind": "synthetic_shadow_tool_key.v1",
            "source_tool_call_id": self.source_tool_call_id,
            "canonical_tool_name": self.canonical_tool_name,
            "shadow_phase": self.shadow_phase,
        }
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        object.__setattr__(self, "shadow_key_hash", hashlib.sha256(encoded.encode("utf-8")).hexdigest())

    @classmethod
    def build(
        cls,
        *,
        source_tool_call_id: str,
        canonical_tool_name: str,
        shadow_phase: Literal["candidate", "resolver", "write_phase"],
    ) -> SyntheticShadowToolKeyV1:
        return cls(
            source_tool_call_id=source_tool_call_id,
            canonical_tool_name=canonical_tool_name,
            shadow_phase=shadow_phase,
        )
