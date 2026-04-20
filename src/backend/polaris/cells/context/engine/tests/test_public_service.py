from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

from polaris.cells.context.engine.public.service import (
    build_context_window,
    get_anthropomorphic_context_v2,
)
from polaris.kernelone.context.engine import ContextBudget, ContextItem, ContextPack

if TYPE_CHECKING:
    import pytest


def _base_pack() -> ContextPack:
    return ContextPack(
        request_hash="req_1",
        items=[
            ContextItem(
                kind="docs",
                provider="docs",
                content_or_pointer="Base context payload",
                size_est=8,
                priority=10,
                reason="base context",
            )
        ],
        compression_log=[],
        rendered_prompt="Base context payload",
        rendered_messages=[{"role": "user", "content": "Base context payload"}],
        total_tokens=8,
        total_chars=len("Base context payload"),
    )


def _context_override() -> dict[str, object]:
    return {
        "session_continuity": {
            "summary": "Older discussion preserved continuity for the restore flow.",
            "source_message_count": 4,
        },
        "state_first_context_os": {
            "adapter_id": "code",
            "run_card": {
                "current_goal": "Fix context.engine continuity overlay",
                "hard_constraints": [
                    "Keep roles.session as the raw truth owner.",
                ],
                "open_loops": ["Verify HTTP restore consumers stay canonical."],
                "active_entities": ["SessionContinuityEngine", "context.engine"],
                "active_artifacts": ["art_1"],
                "next_action_hint": "Expose the overlay through the public service.",
            },
            "context_slice_plan": {
                "plan_id": "plan_1",
                "budget_tokens": 2048,
                "roots": ["current_task"],
                "included": [],
                "excluded": [],
                "pressure_level": "soft",
            },
            "episode_cards": [{"episode_id": "ep_1"}],
        },
    }


class TestBuildContextWindowContextOSOverlay:
    def test_build_context_window_prepends_context_os_overlay(self) -> None:
        with patch(
            "polaris.cells.context.engine.public.service._build_context_pack",
            return_value=_base_pack(),
        ):
            pack, _, budget, sources = build_context_window(
                project_root=".",
                role="director",
                query="continue fixing context engine",
                step=3,
                run_id="run_1",
                mode="interactive",
                context_override=_context_override(),
                session_id="sess_1",
            )

        assert isinstance(budget, ContextBudget)
        assert sources
        assert pack.items[0].provider == "context_os_overlay"
        assert "【State-First Context OS】" in pack.rendered_prompt
        assert "Current goal: Fix context.engine continuity overlay" in pack.rendered_prompt
        assert pack.rendered_messages[0]["content"] == pack.rendered_prompt
        assert any(
            entry.get("action") == "context_os_overlay"
            and entry.get("summary", {}).get("current_goal") == "Fix context.engine continuity overlay"
            for entry in pack.compression_log
        )

    def test_build_context_window_without_override_keeps_pack_unchanged(self) -> None:
        original = _base_pack()
        with patch(
            "polaris.cells.context.engine.public.service._build_context_pack",
            return_value=original,
        ):
            pack, _, _, _ = build_context_window(
                project_root=".",
                role="director",
                query="continue fixing context engine",
                step=3,
                run_id="run_1",
                mode="interactive",
            )

        assert pack.rendered_prompt == "Base context payload"
        assert pack.items[0].provider == "docs"
        assert not any(entry.get("action") == "context_os_overlay" for entry in pack.compression_log)

    def test_build_context_window_can_disable_overlay_via_override_flag(self) -> None:
        original = _base_pack()
        override = _context_override()
        override["state_first_context_os_enabled"] = False
        with patch(
            "polaris.cells.context.engine.public.service._build_context_pack",
            return_value=original,
        ):
            pack, _, _, _ = build_context_window(
                project_root=".",
                role="director",
                query="continue fixing context engine",
                step=3,
                run_id="run_1",
                mode="interactive",
                context_override=override,
            )

        assert pack.rendered_prompt == "Base context payload"
        assert not any(entry.get("action") == "context_os_overlay" for entry in pack.compression_log)

    def test_build_context_window_can_disable_overlay_via_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        original = _base_pack()
        monkeypatch.setenv("POLARIS_CONTEXT_OS_ENABLED", "off")
        with patch(
            "polaris.cells.context.engine.public.service._build_context_pack",
            return_value=original,
        ):
            pack, _, _, _ = build_context_window(
                project_root=".",
                role="director",
                query="continue fixing context engine",
                step=3,
                run_id="run_1",
                mode="interactive",
                context_override=_context_override(),
            )

        assert pack.rendered_prompt == "Base context payload"
        assert not any(entry.get("action") == "context_os_overlay" for entry in pack.compression_log)

    def test_build_context_window_loads_session_override_when_session_id_present(self) -> None:
        class _FakeRoleSessionService:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return None

            def get_context_config_dict(self, session_id: str):
                return _context_override()

        with (
            patch(
                "polaris.cells.context.engine.public.service._build_context_pack",
                return_value=_base_pack(),
            ),
            patch(
                "polaris.cells.roles.session.public.RoleSessionService",
                _FakeRoleSessionService,
            ),
        ):
            pack, _, _, _ = build_context_window(
                project_root=".",
                role="director",
                query="continue fixing context engine",
                step=3,
                run_id="run_1",
                mode="interactive",
                session_id="sess_implicit",
            )

        assert pack.items[0].provider == "context_os_overlay"
        assert "State-First Context OS" in pack.rendered_prompt


class TestAnthropomorphicContextV2ContextOSOverlay:
    def test_get_anthropomorphic_context_v2_returns_context_os_summary(self) -> None:
        with (
            patch(
                "polaris.cells.context.engine.public.service._build_context_pack",
                return_value=_base_pack(),
            ),
            patch(
                "polaris.cells.context.engine.public.service.init_anthropomorphic_modules",
                return_value=None,
            ),
            patch(
                "polaris.cells.context.engine.public.service.get_persona_text",
                return_value="Persona",
            ),
        ):
            payload = get_anthropomorphic_context_v2(
                project_root=".",
                role="director",
                query="continue fixing context engine",
                step=5,
                run_id="run_2",
                phase="execution",
                context_override=_context_override(),
                session_id="sess_2",
            )

        assert payload["persona_instruction"] == "Persona"
        assert "【Session Continuity】" in payload["anthropomorphic_context"]
        assert payload["context_os_summary"]["adapter_id"] == "code"
        assert payload["context_os_summary"]["current_goal"] == "Fix context.engine continuity overlay"
