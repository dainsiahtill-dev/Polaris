"""Architecture fence for history materialization continuity boundaries."""

from __future__ import annotations

from pathlib import Path

from polaris.kernelone.context.history_materialization import SessionContinuityStrategy

BACKEND_ROOT = Path(__file__).resolve().parents[3]
HISTORY_MATERIALIZATION_SOURCE = (
    BACKEND_ROOT / "polaris" / "kernelone" / "context" / "history_materialization.py"
)


def test_session_continuity_strategy_keeps_current_direct_pack_api() -> None:
    """ContextGateway compression paths still use build_pack as current API."""
    assert hasattr(SessionContinuityStrategy, "build_pack")
    assert callable(SessionContinuityStrategy.build_pack)


def test_history_materialization_does_not_label_direct_pack_api_as_compat() -> None:
    """Direct pack access is an advanced API, not a deprecated compatibility path."""
    source = HISTORY_MATERIALIZATION_SOURCE.read_text(encoding="utf-8").lower()
    retired_phrase = "backward " + "compatibility"
    assert retired_phrase not in source
    assert "backward-compatible pack builder" not in source
