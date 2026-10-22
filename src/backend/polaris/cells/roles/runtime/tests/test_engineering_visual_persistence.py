"""Host visual grants and original image bytes survive the same SQLite owner."""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from polaris.bootstrap.config import EngineeringPermissionProfile, ServerConfig, Settings
from polaris.bootstrap.engineering_runtime import resolve_engineering_profile
from polaris.cells.roles.runtime.internal.engineering_session import EngineeringSessionStore
from polaris.cells.roles.runtime.public.contracts.engineering_run import (
    BrowserObservationConfig,
    ImageObservation,
    PermissionScope,
    RoleRunCommand,
    RunLimits,
    ToolObservation,
)
from pydantic import ValidationError


@pytest.fixture
def workspace():
    root = Path(__file__).resolve().parents[7] / ".polaris/test-workspaces/visual-persistence"
    root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="visual-persistence-", dir=root) as directory:
        yield Path(directory)


def browser_payload():
    return {
        "executable": "/opt/visual/chromium",
        "python_executable": "/opt/visual/python",
        "python_paths": ["/opt/visual/site-packages"],
        "require_visual_review": True,
    }


def create_run(workspace, *, browser=None):
    store = EngineeringSessionStore(workspace)
    command = RoleRunCommand("visual-run", "director", "Inspect the local page", workspace)
    scope = PermissionScope((), (), frozenset(), False, browser=browser)
    limits = RunLimits(1900000000.0, 10000, 2000)
    store.create_run(command, scope=scope, limits=limits, engine_version="engineering_v2", request_key="visual-key")
    return store, command, scope, limits


def test_host_profile_resolves_visual_runtime_without_adding_filesystem_or_tools(workspace):
    settings = Settings(
        workspace=workspace,
        server=ServerConfig.model_validate(
            {
                "engineering_permission_profiles": {
                    "selected": {"read_roots": [], "write_roots": [], "enabled_tools": [], "browser": browser_payload()}
                }
            }
        ),
    )
    # The server payload consumed by host settings must retain this exact grant.
    restored_server = ServerConfig.model_validate_json(settings.server.model_dump_json())
    restored_settings = Settings(workspace=workspace, server=restored_server)
    scope = resolve_engineering_profile(restored_settings, workspace, "selected")
    assert scope.read_roots == ()
    assert scope.write_roots == ()
    assert scope.enabled_tools == frozenset()
    assert scope.network_allowed is False
    assert scope.browser == BrowserObservationConfig(
        "/opt/visual/chromium", "/opt/visual/python", ("/opt/visual/site-packages",), True
    )


def test_browser_tool_remains_explicit_host_selection(workspace):
    settings = Settings(
        workspace=workspace,
        server=ServerConfig.model_validate(
            {
                "engineering_permission_profiles": {
                    "visual": {
                        "read_roots": [],
                        "write_roots": [],
                        "enabled_tools": ["browser_observe"],
                        "browser": browser_payload(),
                    }
                }
            }
        ),
    )
    assert resolve_engineering_profile(settings, workspace, "visual").enabled_tools == frozenset({"browser_observe"})
    default_scope = resolve_engineering_profile(Settings(workspace=workspace), workspace, "workspace")
    assert default_scope.browser is None
    assert "browser_observe" not in default_scope.enabled_tools


@pytest.mark.parametrize(
    "changes",
    [
        {"executable": "chromium"},
        {"python_executable": ""},
        {"python_paths": ["relative/packages"]},
        {"python_paths": ["/opt/visual/\0packages"]},
        {"require_visual_review": "false"},
        {"remote_url": "https://example.invalid/"},
    ],
)
def test_invalid_host_browser_configuration_is_rejected(changes):
    with pytest.raises(ValidationError):
        EngineeringPermissionProfile.model_validate({"browser": {**browser_payload(), **changes}})


def test_browser_scope_survives_cold_reopen_and_idempotency_compares_original_runtime(workspace):
    browser = BrowserObservationConfig("/opt/visual/chromium", "/opt/visual/python", ("/opt/visual/packages",), True)
    store, command, scope, limits = create_run(workspace, browser=browser)
    cold = EngineeringSessionStore(workspace)
    row = cold.load_run(command.session_id, workspace=workspace)
    assert row["scope"] == {
        "read_roots": [],
        "write_roots": [],
        "enabled_tools": [],
        "network_allowed": False,
        "browser": {
            "executable": "/opt/visual/chromium",
            "python_executable": "/opt/visual/python",
            "python_paths": ["/opt/visual/packages"],
            "require_visual_review": True,
        },
    }
    assert (
        cold.create_run(
            replace(command, session_id="new-http-id"),
            scope=scope,
            limits=limits,
            engine_version="engineering_v2",
            request_key="visual-key",
        )
        == command.session_id
    )
    with pytest.raises(ValueError, match="conflict"):
        cold.create_run(
            command,
            scope=replace(scope, browser=replace(browser, executable="/opt/other/chromium")),
            limits=limits,
            engine_version="engineering_v2",
            request_key="visual-key",
        )
    assert store.load_run(command.session_id, workspace=workspace)["scope"] == row["scope"]


def test_original_image_bytes_survive_observation_event_and_cold_sqlite_reopen(workspace):
    store, command, *_ = create_run(workspace)
    # A tiny fixed PNG fixture is an attachment transport probe, not visual QA.
    data = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a3ioAAAAASUVORK5CYII="
    )
    image = ImageObservation(
        "image/png", base64.b64encode(data).decode("ascii"), 1, 1, hashlib.sha256(data).hexdigest(), "Frame 1"
    )
    observation = ToolObservation(
        "capture-1", "browser_observe", True, "Captured current page", 0, False, images=(image,)
    )
    store.record_tool_dispatch(
        command.session_id,
        turn_seq=1,
        call_id=observation.call_id,
        tool=observation.tool,
        arguments={"entry": "index.html"},
        role="director",
    )
    store.record_tool_observation(command.session_id, turn_seq=1, observation=observation, role="director")
    cold = EngineeringSessionStore(workspace)
    with sqlite3.connect(cold.database_path) as connection:
        durable = json.loads(connection.execute("SELECT observation FROM tool_calls").fetchone()[0])
    event = next(event for event in cold.load_events(command.session_id) if event["kind"] == "tool_observed")
    for value in (durable, event["payload"]):
        attachment = value["images"][0]
        assert base64.b64decode(attachment["data_base64"], validate=True) == data
        assert attachment["sha256"] == hashlib.sha256(data).hexdigest()
        assert attachment["media_type"] == "image/png"
        assert attachment["label"] == "Frame 1"
    assert cold.pending_tool_calls(command.session_id) == []


def test_nonvisual_scope_and_observations_keep_historical_payloads(workspace):
    store, command, scope, limits = create_run(workspace)
    observation = ToolObservation("read-1", "read_file", True, "Existing source", None, False)
    store.record_tool_dispatch(
        command.session_id, turn_seq=1, call_id="read-1", tool="read_file", arguments={}, role="director"
    )
    store.record_tool_observation(command.session_id, turn_seq=1, observation=observation, role="director")
    store.update_run(command.session_id, checks=(observation,))
    cold = EngineeringSessionStore(workspace)
    row = cold.load_run(command.session_id, workspace=workspace)
    assert "browser" not in row["scope"]
    expected = {
        "call_id": "read-1",
        "tool": "read_file",
        "ok": True,
        "content": "Existing source",
        "exit_code": None,
        "interrupted": False,
        "timed_out": False,
    }
    assert row["checks"] == [expected]
    assert next(event for event in cold.load_events(command.session_id) if event["kind"] == "tool_observed")[
        "payload"
    ] == {
        "turn_seq": 1,
        **expected,
    }
    with sqlite3.connect(cold.database_path) as connection:
        assert json.loads(connection.execute("SELECT observation FROM tool_calls").fetchone()[0]) == expected
    assert (
        cold.create_run(
            replace(command, session_id="new-http-id"),
            scope=scope,
            limits=limits,
            engine_version="engineering_v2",
            request_key="visual-key",
        )
        == command.session_id
    )
