"""Runtime-root authority tests for audit diagnosis."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests
from polaris.cells.audit.diagnosis.internal.toolkit import service


def test_explicit_workspace_resolves_locally_without_backend_hint(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    expected = workspace / ".polaris" / "runtime"

    with (
        patch.object(
            service,
            "resolve_storage_roots",
            return_value=SimpleNamespace(runtime_root=str(expected)),
        ) as resolve,
        patch.object(service, "_resolve_runtime_root_from_backend") as backend_hint,
    ):
        actual = service.resolve_runtime_root(workspace=str(workspace))

    assert actual == expected.resolve()
    resolve.assert_called_once_with(str(workspace))
    backend_hint.assert_not_called()


def test_backend_layout_hint_disables_environment_proxy(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    response = MagicMock(status_code=200)
    response.json.return_value = {"runtime_root": str(runtime_root)}
    session = MagicMock()
    session.get.return_value = response
    session_context = MagicMock()
    session_context.__enter__.return_value = session

    with patch.object(service.requests, "Session", return_value=session_context):
        actual = service._resolve_runtime_root_from_backend("http://127.0.0.1:49978")

    assert actual == runtime_root.resolve()
    assert session.trust_env is False
    session.get.assert_called_once_with(
        "http://127.0.0.1:49978/v2/runtime/storage/layout",
        headers={},
        timeout=3,
    )


def test_backend_layout_hint_treats_timeout_as_missing_hint() -> None:
    session = MagicMock()
    session.get.side_effect = requests.Timeout("slow proxy")
    session_context = MagicMock()
    session_context.__enter__.return_value = session

    with patch.object(service.requests, "Session", return_value=session_context):
        actual = service._resolve_runtime_root_from_backend("http://127.0.0.1:49978")

    assert actual is None
