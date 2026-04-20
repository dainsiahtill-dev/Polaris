"""Golden dataset generation from feedback events."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from polaris.kernelone.audit.omniscient.adapters.sanitization_hook import SanitizationHook


@dataclass(frozen=True)
class GoldenRecord:
    prompt: str
    rejected_response: str
    chosen_response: str
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt": self.prompt,
            "rejected_response": self.rejected_response,
            "chosen_response": self.chosen_response,
            "metadata": self.metadata,
        }


class GoldenDatasetPipeline:
    """Build and persist preference-learning golden records."""

    def __init__(self, *, sanitizer: SanitizationHook | None = None) -> None:
        self._sanitizer = sanitizer or SanitizationHook()

    def build_records(self, dialogs: list[dict[str, Any]]) -> list[GoldenRecord]:
        records: list[GoldenRecord] = []
        for item in dialogs:
            prompt = str(item.get("prompt", "") or "")
            chosen = str(item.get("chosen_response", item.get("accepted_response", "")) or "")
            rejected = str(item.get("rejected_response", item.get("discarded_response", "")) or "")
            metadata_raw = item.get("metadata", {})
            metadata = dict(metadata_raw) if isinstance(metadata_raw, dict) else {}

            sanitized_payload = self._sanitizer.sanitize(
                {
                    "prompt": prompt,
                    "chosen_response": chosen,
                    "rejected_response": rejected,
                }
            )
            records.append(
                GoldenRecord(
                    prompt=str(sanitized_payload.get("prompt", prompt)),
                    chosen_response=str(sanitized_payload.get("chosen_response", chosen)),
                    rejected_response=str(sanitized_payload.get("rejected_response", rejected)),
                    metadata=metadata,
                )
            )
        return records

    def write_jsonl(self, output_path: str | Path, records: list[GoldenRecord]) -> int:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
        return len(records)


__all__ = [
    "GoldenDatasetPipeline",
    "GoldenRecord",
]
