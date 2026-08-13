"""DEO-4 physical executor construction authority regressions."""

from __future__ import annotations

import copy
import hashlib
import pickle

import pytest
from polaris.cells.roles.adapters.internal.director import execution_tools
from polaris.cells.roles.adapters.internal.director.execution_tools import (
    DirectorToolExecutionAuthorityError,
    DirectorToolExecutor,
    _create_director_tool_executor,
)


def test_direct_constructor_fails_closed(tmp_path) -> None:
    with pytest.raises(DirectorToolExecutionAuthorityError) as exc_info:
        DirectorToolExecutor(str(tmp_path))

    assert exc_info.value.code == "directed_effect_physical_executor_authority_required"


def test_private_factory_creates_authorized_executor(tmp_path) -> None:
    executor = _create_director_tool_executor(str(tmp_path))

    assert executor.supports_tool("read_file") is True
    assert executor.supports_tool("write_file") is True


def test_edit_file_result_exposes_exact_content_hashes_for_factory_settlement(tmp_path) -> None:
    """A real edit must survive the tool-result -> Factory mutation boundary."""

    source = tmp_path / "src" / "models.py"
    source.parent.mkdir(parents=True)
    before = "MOOD = 'calm'\n"
    after = "MOOD = 'bright'\n"
    source.write_text(before, encoding="utf-8")
    executor = _create_director_tool_executor(str(tmp_path))

    result = executor.execute_tool(
        "edit_file",
        {
            "file": "src/models.py",
            "search": before,
            "replace": after,
            "allowed_scope": ["src/models.py"],
        },
    )

    assert result["ok"] is True
    assert result["replacements"] == 1
    assert result["before_sha256"] == hashlib.sha256(before.encode("utf-8")).hexdigest()
    assert result["after_sha256"] == hashlib.sha256(after.encode("utf-8")).hexdigest()
    assert result["before_sha256"] != result["after_sha256"]
    assert source.read_text(encoding="utf-8") == after


def test_execute_revalidates_process_local_instance_identity_before_dispatch(tmp_path) -> None:
    executor = _create_director_tool_executor(str(tmp_path))
    execution_tools._DIRECTED_EFFECT_PHYSICAL_EXECUTOR_INSTANCES.discard(executor)

    with pytest.raises(DirectorToolExecutionAuthorityError) as exc_info:
        executor.execute_tool("read_file", {"path": "missing.txt"})

    assert exc_info.value.code == "directed_effect_physical_executor_authority_required"


def test_manual_instance_state_clone_has_no_physical_execution_authority(tmp_path) -> None:
    executor = _create_director_tool_executor(str(tmp_path))
    clone = object.__new__(DirectorToolExecutor)
    clone.__dict__.update(executor.__dict__)

    with pytest.raises(DirectorToolExecutionAuthorityError) as exc_info:
        clone.execute_tool("read_file", {"path": "missing.txt"})

    assert exc_info.value.code == "directed_effect_physical_executor_authority_required"


@pytest.mark.parametrize("transport", [pickle.dumps, copy.copy, copy.deepcopy])
def test_authorized_executor_cannot_cross_copy_or_serialization_boundary(tmp_path, transport) -> None:
    executor = _create_director_tool_executor(str(tmp_path))

    with pytest.raises(DirectorToolExecutionAuthorityError) as exc_info:
        transport(executor)

    assert exc_info.value.code == "directed_effect_physical_executor_transport_forbidden"
