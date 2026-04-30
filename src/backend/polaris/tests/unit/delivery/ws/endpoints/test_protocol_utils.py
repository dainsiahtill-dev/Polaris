"""Tests for polaris.delivery.ws.endpoints.protocol_utils."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from polaris.delivery.ws.endpoints.protocol_utils import (
    build_v2_subscription_subjects,
    resolve_runtime_v2_workspace_key,
    resolve_v2_subject,
)


class TestResolveV2Subject:
    def test_known_channel(self) -> None:
        result = resolve_v2_subject("ws-key", "log.system")
        assert result == "hp.runtime.ws-key.log.system"

    def test_unknown_channel(self) -> None:
        result = resolve_v2_subject("ws-key", "custom.channel")
        assert result == "hp.runtime.ws-key.custom.channel"


class TestBuildV2SubscriptionSubjects:
    def test_single_channel(self) -> None:
        result = build_v2_subscription_subjects("ws-key", ["log.system"])
        assert result == ["hp.runtime.ws-key.log.system"]

    def test_multiple_channels(self) -> None:
        result = build_v2_subscription_subjects("ws-key", ["log.system", "log.llm"])
        assert "hp.runtime.ws-key.log.system" in result
        assert "hp.runtime.ws-key.log.llm" in result

    def test_wildcard(self) -> None:
        result = build_v2_subscription_subjects("ws-key", ["*"])
        assert result == ["hp.runtime.ws-key.>"]

    def test_all_alias(self) -> None:
        result = build_v2_subscription_subjects("ws-key", ["all"])
        assert result == ["hp.runtime.ws-key.>"]

    def test_deduplicates(self) -> None:
        result = build_v2_subscription_subjects("ws-key", ["log.system", "log.system"])
        assert len(result) == 1


class TestResolveRuntimeV2WorkspaceKey:
    @patch("polaris.cells.runtime.projection.public.service.resolve_workspace_runtime_context")
    def test_connection_workspace(self, mock_resolve) -> None:
        mock_context = MagicMock()
        mock_context.workspace_key = "hashed-key"
        mock_resolve.return_value = mock_context
        result = resolve_runtime_v2_workspace_key(
            connection_workspace="/path/to/workspace",
            requested_workspace="",
        )
        assert result == "hashed-key"

    @patch("polaris.cells.runtime.projection.public.service.resolve_workspace_runtime_context")
    def test_fallback_to_basename(self, mock_resolve) -> None:
        mock_resolve.side_effect = ValueError("fail")
        result = resolve_runtime_v2_workspace_key(
            connection_workspace="/path/to/workspace",
            requested_workspace="",
        )
        assert result == "workspace"

    @patch("polaris.cells.runtime.projection.public.service.resolve_workspace_runtime_context")
    def test_fallback_to_requested(self, mock_resolve) -> None:
        mock_resolve.side_effect = ValueError("fail")
        result = resolve_runtime_v2_workspace_key(
            connection_workspace="",
            requested_workspace="/other/workspace",
        )
        assert result == "workspace"

    @patch("polaris.cells.runtime.projection.public.service.resolve_workspace_runtime_context")
    def test_default_fallback(self, mock_resolve) -> None:
        mock_resolve.side_effect = ValueError("fail")
        result = resolve_runtime_v2_workspace_key(
            connection_workspace="",
            requested_workspace="",
        )
        assert result == "default"

    @patch("polaris.cells.runtime.projection.public.service.resolve_workspace_runtime_context")
    def test_strips_trailing_slashes(self, mock_resolve) -> None:
        mock_resolve.side_effect = ValueError("fail")
        result = resolve_runtime_v2_workspace_key(
            connection_workspace="/path/to/workspace/",
            requested_workspace="",
        )
        assert result == "workspace"
