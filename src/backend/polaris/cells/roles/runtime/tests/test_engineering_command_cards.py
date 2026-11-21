from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from polaris.cells.roles.runtime.internal.engineering_command_cards import build_command_card
from polaris.cells.roles.runtime.internal.engineering_tools import EngineeringTools
from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RunLimits
from polaris.cells.roles.runtime.tests.engineering_support import isolated_workspace as isolated_workspace
from polaris.delivery.http.v2.context_manifest import build_projection_manifest


def test_execute_command_returns_bounded_card_and_exact_recovery(isolated_workspace: Path) -> None:
    output = "ERROR: displayWidth mismatch\n" + ("x" * 9000)
    tools = EngineeringTools(
        isolated_workspace,
        PermissionScope((isolated_workspace,), (), frozenset({"execute_command", "read_command_result"}), False),
        RunLimits(time.time() + 15, 1_000_000, 64_000),
        asyncio.Event(),
    )
    result = asyncio.run(tools.execute("cmd-1", "execute_command", {"command": f"printf '{output}'"}))
    assert result.ok
    card = json.loads(result.content)["command_card"]
    assert card["summary"].startswith("ERROR: displayWidth mismatch")
    assert len(card["stdout_preview"]) <= 4000
    assert card["presentation_truncated"] is True
    recovered = asyncio.run(tools.execute("cmd-2", "read_command_result", {"content_ref": card["content_ref"]}))
    full = json.loads(recovered.content)["command_result"]
    assert "displayWidth mismatch" in full["stdout"]
    assert len(full["stdout"]) > 8000


def test_failed_command_summary_preserves_output_excerpt() -> None:
    card = build_command_card("run browser", "", "browser launch missing", 1, "command:abc")
    assert card["summary"] == "browser launch missing"


def test_manifest_recognizes_command_card(isolated_workspace: Path) -> None:
    tools = EngineeringTools(
        isolated_workspace,
        PermissionScope((isolated_workspace,), (), frozenset({"execute_command"}), False),
        RunLimits(time.time() + 15, 1_000_000, 64_000),
        asyncio.Event(),
    )
    result = asyncio.run(
        tools.execute("cmd-1", "execute_command", {"command": "printf 'ERROR: displayWidth mismatch\\n'"})
    )
    run = {
        "session_id": "run",
        "workspace": str(isolated_workspace),
        "workspace_revision": 1,
        "verification": "not_run",
    }
    events = [
        {
            "seq": 1,
            "kind": "tool_observed",
            "role": "director",
            "payload": {"tool": "execute_command", "call_id": "cmd-1", "ok": True, "content": result.content},
        }
    ]
    manifest = build_projection_manifest(run, events, {"messages": []})
    node = manifest["nodes"][0]
    assert node["kind"] == "command_card"
    assert node["projection_ref"].startswith("command:")
