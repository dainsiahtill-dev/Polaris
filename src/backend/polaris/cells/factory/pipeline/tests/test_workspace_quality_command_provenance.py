"""Runtime repair diagnostics must retain the command that selected a loader."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from polaris.cells.director.runtime.public import normalize_director_repair_diagnostics
from polaris.cells.factory.pipeline.internal.factory_workspace_quality_impl import _workspace_quality_repair_errors


def test_missing_compiled_entrypoint_retains_npm_start_without_duplicate_wrapper(tmp_path: Path) -> None:
    executor = SimpleNamespace(
        workspace=tmp_path,
        _trim_command_output=lambda text: text,
        _workspace_quality_repair_target_files=lambda: [],
    )
    errors = _workspace_quality_repair_errors(
        executor,
        [
            {
                "command": ["npm", "run", "start"],
                "passed": False,
                "exit_code": 1,
                "stdout_tail": "> example@1.0.0 start\n> node dist/entry/cli.js --frames 12\n",
                "stderr_tail": (
                    "Error: Cannot find module '/workspace/dist/entry/cli.js'\n"
                    "  code: 'MODULE_NOT_FOUND',\n  requireStack: []\nNode.js v22.23.2"
                ),
            }
        ],
    )
    diagnostics = normalize_director_repair_diagnostics(tuple(errors))

    assert len(diagnostics) == 1
    assert diagnostics[0].code == "javascript_module_error"
    assert "npm run start" in diagnostics[0].metadata["raw"]
    assert "Cannot find module '/workspace/dist/entry/cli.js'" in diagnostics[0].metadata["raw"]
