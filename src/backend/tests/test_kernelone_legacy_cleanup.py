from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from polaris.kernelone.context.compaction import (
    RoleContextCompressor,
)
from polaris.kernelone.memory.project_profile import (
    ProjectProfile,
    get_or_load_profile,
)


class _TranscriptRecorder:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def record_message(self, **payload: Any) -> None:
        self.records.append(dict(payload))


def test_role_context_compressor_uses_record_message_contract(tmp_path: Path) -> None:
    recorder = _TranscriptRecorder()
    compressor = RoleContextCompressor(
        workspace=str(tmp_path),
        transcript_service=recorder,
        config={"micro_compact_keep": 1},
    )
    messages = [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "tool-1", "name": "list_files"}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tool-1", "content": "A" * 120}]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "tool-2", "name": "read_file"}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tool-2", "content": "B" * 120}]},
    ]

    result = compressor.micro_compact(messages)

    assert result[1]["content"][0]["content"] == "[Previous: used list_files]"
    assert recorder.records
    assert recorder.records[0]["metadata"]["type"] == "context_micro_compact"


def test_get_or_load_profile_returns_profile_instance_and_persists(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    base_dir = tmp_path / "runtime"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "pyproject.toml").write_text(
        '[project]\nname = "demo"\n[tool.poetry.dependencies]\npython = "3.11"\n',
        encoding="utf-8",
    )

    profile = get_or_load_profile(str(workspace), str(base_dir))

    assert isinstance(profile, ProjectProfile)
    assert profile.workspace == str(workspace)
    assert (base_dir / "workspace" / "brain" / "project_profile.json").exists()


def test_removed_legacy_modules_are_no_longer_importable() -> None:
    removed_modules = (
        "polaris.kernelone.audit.unified_audit_core",
        "polaris.kernelone.policy.runtime_policy",
    )

    for module_name in removed_modules:
        try:
            importlib.import_module(module_name)
        except ModuleNotFoundError:
            continue
        raise AssertionError(f"legacy module still importable: {module_name}")
