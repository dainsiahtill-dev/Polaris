"""Unit tests for the factory event channel mapping.

The factory events (PM / CE / Director / QA chain) flow through the
platform's NAT JetStream pipeline the same way as runtime events. This
tests the subject builder for the new channels:

- event.factory:all  -> hp.runtime.<workspace_key>.event.factory.>  (workspace-scoped wildcard)
- event.factory:<rid> -> hp.runtime.<workspace_key>.event.factory.<rid>  (workspace-scoped pin)
"""

from __future__ import annotations

import unittest

from polaris.delivery.ws.endpoints.protocol_utils import (
    build_v2_subscription_subjects,
)


class TestFactoryChannelMapping(unittest.TestCase):
    def test_factory_all_subscribes_to_workspace_scoped_wildcard(self) -> None:
        subjects = build_v2_subscription_subjects("user-ws", ["event.factory:all"])
        assert subjects == ["hp.runtime.user-ws.event.factory.>"]

    def test_factory_alias_also_subscribes_to_workspace_scoped_wildcard(self) -> None:
        subjects = build_v2_subscription_subjects("user-ws", ["event.factory"])
        assert subjects == ["hp.runtime.user-ws.event.factory.>"]

    def test_factory_pinned_run_id_uses_workspace_aware_subject(self) -> None:
        subjects = build_v2_subscription_subjects("user-ws", ["event.factory:run-abc-123"])
        assert subjects == ["hp.runtime.user-ws.event.factory.run-abc-123"]

    def test_factory_channel_respects_per_workspace_subscriptions(self) -> None:
        # Two different workspaces — each gets its own factory subject, not
        # a leak across workspaces.
        for ws in ("alpha", "beta"):
            with self.subTest(workspace=ws):
                subjects = build_v2_subscription_subjects(ws, ["event.factory:all"])
                assert subjects == [f"hp.runtime.{ws}.event.factory.>"]

    def test_factory_channel_coexists_with_runtime_channels(self) -> None:
        subjects = build_v2_subscription_subjects(
            "user-ws",
            ["log.llm", "event.factory:all", "event.bench:bench-1"],
        )
        assert "hp.runtime.user-ws.log.llm" in subjects
        assert "hp.runtime.user-ws.event.factory.>" in subjects
        # Bench subject is workspace-agnostic; that's by design.
        assert "hp.runtime.bench.bench-1" in subjects
        assert len(subjects) == 3

    def test_malformed_factory_run_id_is_dropped(self) -> None:
        # The token regex is ``[A-Za-z0-9_-]{1,64}``; anything outside that
        # class (path separators, spaces, control characters) is dropped to
        # prevent subject injection. Leading dot / hyphen are still inside
        # the safe class because factory run ids are server-generated.
        for bad in ("../escape", "sub/dir", "with space", "run!@#", ""):
            with self.subTest(token=bad):
                subjects = build_v2_subscription_subjects("user-ws", [f"event.factory:{bad}"])
                assert subjects == []

    def test_factory_default_wildcard_still_workspace_scoped(self) -> None:
        # ``*`` and ``all`` are still workspace-scoped; we must NOT leak
        # factory events to the wildcard subscription of an unrelated
        # workspace.
        for ch in ("*", "all"):
            with self.subTest(channel=ch):
                subjects = build_v2_subscription_subjects("user-ws", [ch])
                assert subjects == ["hp.runtime.user-ws.>"]


if __name__ == "__main__":
    unittest.main()
