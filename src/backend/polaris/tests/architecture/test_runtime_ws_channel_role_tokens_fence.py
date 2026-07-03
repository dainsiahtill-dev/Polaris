"""Architecture guard for runtime WebSocket role-filter token naming."""

from __future__ import annotations

from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_CHANNEL_UTILS = _BACKEND_ROOT / "polaris" / "delivery" / "ws" / "endpoints" / "channel_utils.py"


def test_runtime_ws_role_tokens_do_not_use_consumer_alias() -> None:
    """Runtime observability roles are broader than TaskMarket consumers."""
    source = _CHANNEL_UTILS.read_text(encoding="utf-8")
    assert "RUNTIME_OBSERVABLE_ROLE_TOKENS" in source
    assert "CONSUMER_ROLE_TOKENS" not in source
