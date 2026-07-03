"""Architecture fence for role strategy overlay contract terminology."""

from __future__ import annotations

from pathlib import Path

from polaris.kernelone.context.strategy_overlay_contracts import RoleOverlay

BACKEND_ROOT = Path(__file__).resolve().parents[3]
OVERLAY_CONTRACT_SOURCE = BACKEND_ROOT / "polaris" / "kernelone" / "context" / "strategy_overlay_contracts.py"


def test_role_overlay_accepts_external_role_tokens() -> None:
    """RoleOverlay keeps string tokens for external payload ingestion."""
    overlay = RoleOverlay(
        role="director",
        parent_profile_id="canonical_balanced",
        overlay_id="director.execution",
    )

    assert overlay.role == "director"


def test_role_overlay_contract_does_not_label_string_roles_as_compat() -> None:
    """String role tokens are a current external-payload boundary."""
    source = OVERLAY_CONTRACT_SOURCE.read_text(encoding="utf-8").lower()
    retired_phrase = "backward " + "compatibility"
    assert retired_phrase not in source
    assert "external payload tokens" in source
