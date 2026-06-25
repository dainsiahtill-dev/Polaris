"""LLM context bridge for repair receipts."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .contracts import RepairReceipt


def build_repair_receipt_context(receipts: Sequence[RepairReceipt], *, max_items: int = 8) -> dict[str, Any]:
    """Build compact non-authoritative context for the next Director LLM turn."""

    items: list[dict[str, Any]] = []
    for receipt in list(receipts)[-max_items:]:
        items.append(
            {
                "receipt_id": receipt.receipt_id,
                "source_tool": receipt.source_tool,
                "status": receipt.status,
                "mode": receipt.mode,
                "authoritative": receipt.authoritative,
                "files_changed": list(receipt.files_changed),
                "after_hashes": dict(receipt.after_hashes),
                "advisor_notes": [
                    {
                        "source": note.source,
                        "confidence": note.confidence,
                        "authoritative": note.authoritative,
                    }
                    for note in receipt.advisor_notes
                ],
            }
        )
    return {
        "kind": "director_repair_receipt_context",
        "instruction": (
            "Continue from these platform repair receipts. Do not undo applied "
            "deterministic repairs unless a later verifier proves they regressed."
        ),
        "receipts": items,
        "agi_advisory_supported": True,
        "agi_advisory_active": False,
        "agi_advisory_authoritative": False,
    }
