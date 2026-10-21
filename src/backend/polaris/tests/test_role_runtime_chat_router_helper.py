from __future__ import annotations

from typing import Any

import pytest
from polaris.delivery.http.routers import role_chat_jetstream, role_runtime_chat


@pytest.mark.parametrize(
    "payload",
    [
        {"intent": "execute"},
        {"engine_version": "old"},
        {"intent": ["execute"]},
        {"execute": True},
        {"mode": "execute"},
    ],
)
def test_nonstreaming_chat_rejects_ambiguous_engine_intent(payload: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        role_runtime_chat.engineering_chat_intent(payload)


@pytest.mark.asyncio
async def test_execute_role_chat_jetstream_rejects_retired_runtime(monkeypatch, tmp_path) -> None:
    """The old publisher no longer constructs runtime or reports synthetic completion."""
    from fastapi import HTTPException

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("retired runtime constructed")

    monkeypatch.setattr(role_chat_jetstream, "RoleRuntimeService", forbidden, raising=False)
    with pytest.raises(HTTPException) as caught:
        await role_chat_jetstream.execute_role_chat_jetstream(
            role="director",
            workspace=str(tmp_path),
            message="build it",
            payload={},
            default_domain="code",
            host_kind="role_chat_jetstream",
        )
    assert caught.value.status_code == 410
    assert isinstance(caught.value.detail, dict)
    assert caught.value.detail["error_code"] == "legacy_engine_retired"
