from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from polaris.cells.llm.dialogue.internal.role_dialogue import generate_role_response_streaming


class TestGenerateRoleResponseStreamingSessionResolution:
    def test_missing_session_id_creates_fresh_ad_hoc_session(self) -> None:
        captured = {}

        async def fake_stream(command):
            captured["command"] = command
            yield {"type": "content_chunk", "content": "hello"}
            yield {
                "type": "complete",
                "result": SimpleNamespace(
                    content="hello",
                    thinking=None,
                    profile_version=None,
                    tool_policy_id=None,
                ),
            }

        async def run() -> None:
            queue: asyncio.Queue = asyncio.Queue()
            with (
                patch(
                    "polaris.cells.roles.session.internal.role_session_service.RoleSessionService.create_ad_hoc_session",
                    return_value=SimpleNamespace(id="sess-fresh"),
                ) as create_ad_hoc,
                patch(
                    "polaris.cells.roles.session.internal.role_session_service.RoleSessionService.find_or_create_ad_hoc",
                    side_effect=AssertionError("legacy reuse path should not be used"),
                ),
                patch(
                    "polaris.cells.roles.runtime.public.service.stream_role_session_command",
                    side_effect=fake_stream,
                ),
            ):
                await generate_role_response_streaming(
                    workspace="C:/repo",
                    settings=None,
                    role="pm",
                    message="继续",
                    output_queue=queue,
                )
                create_ad_hoc.assert_called_once()

        asyncio.run(run())
        command = captured["command"]
        assert command.session_id == "sess-fresh"
        assert command.history == ()
