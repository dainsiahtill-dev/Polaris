"""Director publishes authored task intent separately from generic guidance."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from .test_director_adapter_build_evidence import _make_adapter


@pytest.mark.parametrize(
    "instruction",
    ["Create src/main.ts and package.json.", "Read src/main.ts only. Do not modify files."],
)
def test_director_projects_current_goal_without_changing_scope_or_task(tmp_path: Path, instruction: str) -> None:
    task = {"subject": "Current task", "metadata": {"goal": instruction, "language": "typescript"}}
    original = deepcopy(task)
    context = {
        "target_files": ["src/main.ts", "package.json"],
        "director_execution_envelope": {"authorization": {"allowed_write_paths": ["src/main.ts"]}},
        "platform_tool_contract": {"single_batch": True, "task_instruction": "stale task"},
    }
    before_scope = deepcopy(context["director_execution_envelope"])
    _make_adapter(tmp_path)._build_director_message(task, context=context)
    assert context["platform_tool_contract"]["task_instruction"] == instruction
    assert context["platform_tool_contract"]["single_batch"] is True
    assert context["director_execution_envelope"] == before_scope
    assert task == original
