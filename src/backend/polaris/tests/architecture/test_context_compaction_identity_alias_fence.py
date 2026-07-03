"""Architecture fence for context compaction identity alias terminology."""

from __future__ import annotations

from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
COMPACTION_SOURCE = BACKEND_ROOT / "polaris" / "kernelone" / "context" / "compaction.py"


def test_role_context_identity_uses_source_payload_alias_language() -> None:
    """Identity field synchronization should not be labeled as legacy architecture."""
    source = COMPACTION_SOURCE.read_text(encoding="utf-8")
    assert "Source-payload aliases retained" in source
    assert "Legacy " + "aliases retained" not in source
    assert "new/" + "legacy identity" not in source
    assert "backward " + "compatible with Director" not in source
