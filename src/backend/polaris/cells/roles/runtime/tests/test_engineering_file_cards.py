from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path

from polaris.cells.roles.runtime.internal.engineering_tools import EngineeringTools
from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RunLimits
from polaris.cells.roles.runtime.tests.engineering_support import isolated_workspace as isolated_workspace
from polaris.delivery.http.v2.context_manifest import build_projection_manifest


def test_read_file_returns_bounded_file_card(isolated_workspace: Path) -> None:
    source = "def alpha():\n    return 1\n\nclass Beta:\n    pass\n" + ("# padding\n" * 2000)
    (isolated_workspace / "a.py").write_text(source, encoding="utf-8")
    tools = EngineeringTools(
        isolated_workspace,
        PermissionScope((isolated_workspace,), (), frozenset({"read_file"}), False),
        RunLimits(time.time() + 15, 1_000_000, 64_000),
        asyncio.Event(),
        revision_provider=lambda: 7,
    )
    result = asyncio.run(tools.execute("read-1", "read_file", {"file": "a.py"}))
    assert result.ok, result.content
    card = json.loads(result.content)["file_card"]
    assert card["schema_version"] == "engineering.file_card.v1"
    assert card["path"] == "a.py" and card["revision"] == 7
    assert card["sha256"] == hashlib.sha256(source.encode()).hexdigest()
    assert card["content_ref"] == f"filecard:{card['sha256']}"
    assert {"alpha", "Beta"} <= set(card["symbols"])
    assert len(card["content_preview"].encode("utf-8")) <= 8000
    assert card["content_bounds"]["truncated"] is True


def test_content_ref_recovers_exact_snapshot_beyond_preview(isolated_workspace: Path) -> None:
    source = "def alpha():\n    return 1\n" + ("# padding\n" * 2000)
    (isolated_workspace / "a.py").write_text(source, encoding="utf-8")
    tools = EngineeringTools(
        isolated_workspace,
        PermissionScope((isolated_workspace,), (), frozenset({"read_file"}), False),
        RunLimits(time.time() + 15, 1_000_000, 64_000),
        asyncio.Event(),
    )
    first = asyncio.run(tools.execute("read-1", "read_file", {"file": "a.py"}))
    card = json.loads(first.content)["file_card"]
    tools.file_card_resolver = lambda _ref: card
    recovered = asyncio.run(
        tools.execute(
            "read-2",
            "read_file",
            {"content_ref": card["content_ref"], "start_line": 1000, "end_line": 1005},
        )
    )
    restored = json.loads(recovered.content)["file_card"]
    assert restored["recovered_from"] == card["content_ref"]
    assert restored["content_bounds"]["start_line"] == 1000
    assert restored["content_bounds"]["end_line"] == 1005
    assert "# padding" in restored["content_preview"]
    assert restored["stale"] is False


def test_content_ref_recovers_card_and_marks_stale_source(isolated_workspace: Path) -> None:
    path = isolated_workspace / "a.py"
    path.write_text("def alpha():\n    return 1\n", encoding="utf-8")
    tools = EngineeringTools(
        isolated_workspace,
        PermissionScope((isolated_workspace,), (), frozenset({"read_file"}), False),
        RunLimits(time.time() + 15, 1_000_000, 64_000),
        asyncio.Event(),
    )
    first = asyncio.run(tools.execute("read-1", "read_file", {"file": "a.py"}))
    card = json.loads(first.content)["file_card"]
    path.write_text("def alpha():\n    return 2\n", encoding="utf-8")
    tools.file_card_resolver = lambda _ref: card
    recovered = asyncio.run(tools.execute("read-2", "read_file", {"content_ref": card["content_ref"]}))
    restored = json.loads(recovered.content)["file_card"]
    assert restored["stale"] is True and restored["current_sha256"] != card["sha256"]


def test_manifest_projects_file_card_reference(isolated_workspace: Path) -> None:
    source = "def alpha():\n    return 1\n"
    (isolated_workspace / "a.py").write_text(source, encoding="utf-8")
    tools = EngineeringTools(
        isolated_workspace,
        PermissionScope((isolated_workspace,), (), frozenset({"read_file"}), False),
        RunLimits(time.time() + 15, 1_000_000, 64_000),
        asyncio.Event(),
    )
    result = asyncio.run(tools.execute("read-1", "read_file", {"file": "a.py"}))
    card = json.loads(result.content)["file_card"]
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
            "payload": {"tool": "read_file", "call_id": "read-1", "ok": True, "content": result.content},
        }
    ]
    manifest = build_projection_manifest(run, events, {"messages": [], "call_id": "run:1"})
    node = manifest["nodes"][0]
    assert node["kind"] == "file_card"
    assert node["projection_ref"] == card["content_ref"]
    assert node["original_tokens"] == (card["size_bytes"] + 3) // 4
