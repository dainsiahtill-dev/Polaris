from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from polaris.delivery.http.workspace import active_workspace_value, requested_or_active_workspace


def test_active_workspace_ignores_mock_placeholder_and_falls_back_to_workspace() -> None:
    settings = MagicMock()
    settings.workspace = " C:/Repo/Active "

    assert active_workspace_value(settings) == "C:/Repo/Active"


def test_active_workspace_prefers_workspace_path() -> None:
    settings = MagicMock()
    settings.workspace = "C:/Repo/Stale"
    settings.workspace_path = " C:/Temp/Product "

    assert active_workspace_value(settings) == "C:/Temp/Product"


def test_active_workspace_supports_pathlike_values() -> None:
    workspace = Path("target-project")
    settings = MagicMock()
    settings.workspace_path = workspace
    settings.workspace = "C:/Repo/Stale"

    assert active_workspace_value(settings) == str(workspace)


def test_requested_or_active_workspace_uses_active_workspace_for_dot_request() -> None:
    settings = MagicMock()
    settings.workspace = "C:/Repo/Stale"
    settings.workspace_path = "C:/Temp/Product"

    assert requested_or_active_workspace(settings, ".") == "C:/Temp/Product"


def test_requested_or_active_workspace_preserves_explicit_request() -> None:
    settings = MagicMock()
    settings.workspace = "C:/Repo/Stale"
    settings.workspace_path = "C:/Temp/Product"

    assert requested_or_active_workspace(settings, " C:/Explicit ") == "C:/Explicit"
