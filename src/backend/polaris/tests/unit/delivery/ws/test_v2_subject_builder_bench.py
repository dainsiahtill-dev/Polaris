"""Unit tests for the bench channel mapping in
``build_v2_subscription_subjects`` — the bridge that lets the front-end's
``useFactoryBench`` hook subscribe to NAT JetStream subjects through the
same WebSocket that already carries log.llm / log.process / etc.
"""

from __future__ import annotations

import unittest

from polaris.delivery.ws.endpoints.protocol_utils import (
    build_v2_subscription_subjects,
)


class TestBenchChannelMapping(unittest.TestCase):
    def test_pinned_session_id_maps_to_workspace_agnostic_subject(self) -> None:
        subjects = build_v2_subscription_subjects(
            "user-project-ws",
            ["event.bench:bench-1781715008-b52d4a"],
        )
        assert subjects == ["hp.runtime.bench.bench-1781715008-b52d4a"], f"unexpected subject(s): {subjects}"

    def test_event_bench_all_subscribes_to_global_wildcard(self) -> None:
        subjects = build_v2_subscription_subjects(
            "user-project-ws",
            ["event.bench"],
        )
        assert subjects == ["hp.runtime.bench.>"], f"unexpected subject(s): {subjects}"

    def test_event_bench_all_alias_also_subscribes_to_global_wildcard(self) -> None:
        subjects = build_v2_subscription_subjects(
            "user-project-ws",
            ["event.bench:all"],
        )
        assert subjects == ["hp.runtime.bench.>"], f"unexpected subject(s): {subjects}"

    def test_bench_channel_coexists_with_workspace_channel(self) -> None:
        subjects = build_v2_subscription_subjects(
            "user-project-ws",
            ["log.llm", "event.bench:bench-x"],
        )
        # Both subjects present, workspace-agnostic bench subject is independent
        # of the user's workspace_key.
        assert "hp.runtime.user-project-ws.log.llm" in subjects
        assert "hp.runtime.bench.bench-x" in subjects
        assert len(subjects) == 2

    def test_default_wildcard_still_workspace_scoped(self) -> None:
        """``*`` and ``all`` must remain workspace-scoped — we do NOT want
        to leak bench events into the wildcard subscription of an unrelated
        workspace."""
        for ch in ("*", "all"):
            with self.subTest(channel=ch):
                subjects = build_v2_subscription_subjects("user-project-ws", [ch])
                assert subjects == ["hp.runtime.user-project-ws.>"]

    def test_malformed_bench_token_is_dropped(self) -> None:
        """Defence in depth: a malicious or buggy client cannot escape the
        ``hp.runtime.bench.`` subject by smuggling a path separator into
        the session id slot."""
        for bad in (
            "../escape",
            "sub/dir",
            ".hidden",
            "with space",
            "very-long-" + "x" * 100,
        ):
            with self.subTest(token=bad):
                subjects = build_v2_subscription_subjects("user-project-ws", [f"event.bench:{bad}"])
                assert subjects == [], f"expected no subject for bad token {bad!r}, got {subjects}"

    def test_empty_session_id_after_prefix_is_dropped(self) -> None:
        subjects = build_v2_subscription_subjects("user-project-ws", ["event.bench:"])
        assert subjects == []

    def test_bench_pin_with_safe_token_keeps_dedup(self) -> None:
        subjects = build_v2_subscription_subjects(
            "user-project-ws",
            ["event.bench:bench-1", "event.bench:bench-1"],
        )
        # set-dedup keeps a single subject.
        assert subjects == ["hp.runtime.bench.bench-1"]


if __name__ == "__main__":
    unittest.main()
