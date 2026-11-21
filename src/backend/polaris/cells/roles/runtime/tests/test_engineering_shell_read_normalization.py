from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from polaris.cells.roles.runtime.internal.engineering_tools import EngineeringTools
from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RunLimits
from polaris.cells.roles.runtime.tests.engineering_support import isolated_workspace as isolated_workspace


def _tools(workspace: Path) -> EngineeringTools:
    return EngineeringTools(
        workspace,
        PermissionScope((workspace,), (workspace,), frozenset({"read_file", "execute_command"}), False),
        RunLimits(time.time() + 15, 10000, 2000),
        asyncio.Event(),
    )


def test_sed_source_read_becomes_file_card(isolated_workspace: Path) -> None:
    (isolated_workspace / "app.js").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    tools = _tools(isolated_workspace)
    result = asyncio.run(
        tools.execute(
            "call-1",
            "execute_command",
            {"command": f"cd {isolated_workspace} && sed -n '2,3p' app.js"},
        )
    )
    payload = json.loads(result.content)
    assert result.tool == "read_file" and result.ok
    assert payload["file_card"]["content_preview"] == "two\nthree\n"
    assert payload["normalization"]["from"] == "execute_command"


def test_cat_source_read_becomes_file_card(isolated_workspace: Path) -> None:
    (isolated_workspace / "index.html").write_text("<html>ok</html>", encoding="utf-8")
    tools = _tools(isolated_workspace)
    result = asyncio.run(tools.execute("call-2", "execute_command", {"command": "cat index.html"}))
    assert result.tool == "read_file" and result.ok
    assert json.loads(result.content)["file_card"]["path"] == "index.html"


def test_mutating_or_complex_shell_is_not_normalized(isolated_workspace: Path) -> None:
    tools = _tools(isolated_workspace)
    name, _args = tools.normalize("execute_command", {"command": "sed -i 's/a/b/' app.js"})
    assert name == "execute_command"
    name, _args = tools.normalize("execute_command", {"command": "cat app.js > copy.js"})
    assert name == "execute_command"
