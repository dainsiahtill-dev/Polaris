"""REPO-MOVE-PARTIAL-DELETE-FAILURE: move effect truthfulness.

``apply_patch`` moves a file by writing the destination and then deleting the
source.  A source deletion that fails after a successful destination write
must not erase the observed write: reporting ``files_changed=[]`` for a move
that already wrote its destination hides a real file effect from every
downstream consumer (Run Ledger receipts, QA changed-file auditing, the
realtime file-edit projection).

These tests drive the real outer ``apply_patch`` handler through the real
``AgentAccelToolExecutor``, the real KernelOne filesystem boundary, and the
real director write-policy gate.  Only the *fault* is injected, and it is
always an explicitly labelled fault adapter:

* ``locked directory`` — a read-only parent directory makes the real
  ``os.remove`` fail with a genuine ``EACCES`` (no monkeypatching).
* ``_FaultRemovingAdapter`` — a labelled in-memory adapter used to force an
  uncertain adapter error; every other file effect stays real.

Every test asserts on physical evidence (what is on disk) as well as on the
projected result, because the defect is precisely a disagreement between
those two.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path
from typing import Any

import pytest
from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter
from polaris.kernelone.fs import KernelFileSystem
from polaris.kernelone.llm.toolkit.executor.core import AgentAccelToolExecutor
from polaris.kernelone.llm.toolkit.executor.handlers.repo import _handle_apply_patch

_MOVE_PATCH = (
    "*** Begin Patch\n"
    "*** Update File: source.txt\n"
    "*** Move to: dest.txt\n"
    "@@\n"
    "-old content\n"
    "+new content\n"
    "*** End Patch\n"
)


def _move_patch(source: str, destination: str) -> str:
    """Build a real apply_patch move from *source* to *destination*."""
    return (
        "*** Begin Patch\n"
        f"*** Update File: {source}\n"
        f"*** Move to: {destination}\n"
        "@@\n"
        "-old content\n"
        "+new content\n"
        "*** End Patch\n"
    )


def _executor(workspace: Path, *, scope: list[str] | None = None) -> AgentAccelToolExecutor:
    """Build a real executor bound to *workspace* with a real write scope."""
    return AgentAccelToolExecutor(
        str(workspace),
        allowed_tools=frozenset({"apply_patch"}),
        capability_scope=scope if scope is not None else ["source.txt", "dest.txt"],
    )


def _execute(executor: AgentAccelToolExecutor, patch: str = _MOVE_PATCH) -> dict[str, Any]:
    """Run one real ``apply_patch`` move and return the executor result."""
    return executor.execute("apply_patch", {"patch": patch})


def _run_handler(executor: AgentAccelToolExecutor, patch: str = _MOVE_PATCH) -> dict[str, Any]:
    """Run the real outer ``apply_patch`` handler and return its response."""
    return _handle_apply_patch(executor, patch=patch)


def _seed_move_pair(workspace: Path) -> None:
    """Create the real source file the move operates on."""
    (workspace / "source.txt").write_text("old content\n", encoding="utf-8")


def _read(path: Path) -> str | None:
    """Read a file as UTF-8, or None when it is absent."""
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


class _FaultRemovingAdapter:
    """Labelled fault adapter: real KFS boundary, failing source removal.

    The adapter delegates every operation to the real local adapter except
    ``remove``, which raises ``EACCES`` for the exact source path.  This is
    the explicitly labelled fault injection required for the uncertain-effect
    cases; all other file effects stay real.
    """

    def __init__(self, inner: Any, denied_path: str) -> None:
        self._inner = inner
        self._denied_path = denied_path
        self.remove_calls: list[str] = []

    def remove(self, path: str, *, missing_ok: bool = True) -> bool:
        self.remove_calls.append(str(path))
        if str(path) == self._denied_path:
            raise PermissionError(errno.EACCES, "labelled fault adapter: source removal denied", str(path))
        return self._inner.remove(path, missing_ok=missing_ok)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class TestLegalMove:
    """The happy path must stay exactly as it was."""

    def test_real_legal_move_reports_destination_and_removes_source(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        _seed_move_pair(workspace)

        executor = _executor(workspace)
        result = _execute(executor)

        assert result["ok"] is True, result
        payload = result["result"]
        assert payload["files_ok"] == 1
        # PHYSICAL-FACT CORRECTION: a completed move changed two paths (the
        # destination was written, the source was removed), so both are
        # confirmed changed paths, consistent with the delete operation
        # contract which reports the removed path as changed.
        assert payload["effect_receipt"]["files_changed"] == ["dest.txt", "source.txt"]
        assert payload["results"][0]["operation"] == "move"
        assert payload["results"][0]["source_file"] == "source.txt"
        assert payload["results"][0]["file"] == "dest.txt"

        # A legal move is not a partial move and claims no partial evidence.
        assert "partial" not in payload
        assert payload["effect_receipt"].get("partial") is None

        # Physical evidence: the destination holds the new content and the
        # source is gone.
        assert _read(workspace / "dest.txt") == "new content\n"
        assert not (workspace / "source.txt").exists()


class TestDenyBeforeWrite:
    """A denied write must be denied before any effect happens."""

    def test_scope_denied_move_writes_nothing(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        _seed_move_pair(workspace)

        # Only the source is authorized; the destination is outside the scope.
        executor = _executor(workspace, scope=["source.txt"])
        result = _execute(executor)

        assert result["ok"] is False, result
        assert result["error_type"] == "director_write_policy_denied"
        assert result["effect_receipt"]["files_changed"] == []

        # Physical evidence: neither file was touched.
        assert _read(workspace / "source.txt") == "old content\n"
        assert not (workspace / "dest.txt").exists()

    def test_scope_denied_move_is_not_reported_as_partial(self, tmp_path: Path) -> None:
        """A denial before any effect must not claim a partial effect."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        _seed_move_pair(workspace)

        executor = _executor(workspace, scope=["source.txt"])
        response = _run_handler(executor)

        assert response["ok"] is False
        assert response.get("partial") is None
        assert "partial_effects" not in response
        assert response["effect_receipt"]["files_changed"] == []
        assert response["effect_receipt"].get("partial") is None


class TestDestinationWrittenSourceDeleteDenied:
    """The reported defect: a failed source deletion hid a real write."""

    def test_real_permission_denied_delete_keeps_destination_write_visible(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        locked = workspace / "locked"
        locked.mkdir()
        source = locked / "source.txt"
        source.write_text("old content\n", encoding="utf-8")

        # A genuinely read-only parent directory makes the real os.remove fail
        # with EACCES.  No monkeypatching: the kernel is the real one.
        os.chmod(locked, 0o555)
        try:
            executor = _executor(workspace, scope=["locked/source.txt", "dest.txt"])
            response = _run_handler(executor, _move_patch("locked/source.txt", "dest.txt"))
        finally:
            os.chmod(locked, 0o755)

        assert response["ok"] is False, response

        # The outward error projection names the real cause.
        assert response["error_type"] == "partial_move_source_delete_failed"
        assert "Failed to move file" in response["error"]

        # The genuine successful destination-write observation is preserved.
        assert response["effect_receipt"]["files_changed"] == ["dest.txt"]
        assert response["effect_receipt"]["partial"] is True
        assert response["partial"] is True
        assert response["files_changed"] == ["dest.txt"]
        partial = response["partial_effects"][0]
        assert partial["error_type"] == "partial_move_source_delete_failed"
        assert partial["files_changed"] == ["dest.txt"]
        assert partial["source_file"] == "locked/source.txt"
        assert partial["destination_file"] == "dest.txt"
        assert partial["move_effects"]["destination_write"] == "succeeded"
        assert partial["move_effects"]["source_delete"] == "failed"

        # Physical evidence: the destination really was written, and the
        # source really was not deleted.
        assert _read(workspace / "dest.txt") == "new content\n"
        assert _read(source) == "old content\n"

    def test_failed_delete_does_not_invent_a_completed_deletion(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        _seed_move_pair(workspace)

        inner = LocalFileSystemAdapter()
        denied = str(workspace / "source.txt")
        fault_adapter = _FaultRemovingAdapter(inner, denied)
        executor = AgentAccelToolExecutor(str(workspace), allowed_tools=frozenset({"apply_patch"}))
        executor._kernel_fs = KernelFileSystem(str(workspace), fault_adapter)

        response = _run_handler(executor)

        assert response["ok"] is False, response
        partial = response["partial_effects"][0]
        assert partial["move_effects"]["source_delete"] == "failed"
        assert partial["move_effects"]["source_exists_after_failure"] is True

        # No delete receipt is claimed for the source, and the destination
        # write is still reported.
        assert response["effect_receipt"]["files_changed"] == ["dest.txt"]
        assert _read(workspace / "dest.txt") == "new content\n"
        assert _read(workspace / "source.txt") == "old content\n"

    def test_failed_delete_does_not_roll_back_the_destination_write(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        _seed_move_pair(workspace)

        inner = LocalFileSystemAdapter()
        fault_adapter = _FaultRemovingAdapter(inner, str(workspace / "source.txt"))
        executor = AgentAccelToolExecutor(str(workspace), allowed_tools=frozenset({"apply_patch"}))
        executor._kernel_fs = KernelFileSystem(str(workspace), fault_adapter)

        response = _run_handler(executor)

        # No blind rollback: the destination content stays on disk.
        assert _read(workspace / "dest.txt") == "new content\n"
        assert response["ok"] is False

    def test_source_delete_is_attempted_exactly_once(self, tmp_path: Path) -> None:
        """A failed delete must not be retried into a second effect."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        _seed_move_pair(workspace)

        inner = LocalFileSystemAdapter()
        fault_adapter = _FaultRemovingAdapter(inner, str(workspace / "source.txt"))
        executor = AgentAccelToolExecutor(str(workspace), allowed_tools=frozenset({"apply_patch"}))
        executor._kernel_fs = KernelFileSystem(str(workspace), fault_adapter)

        response = _run_handler(executor)

        assert response["ok"] is False, response
        assert len(fault_adapter.remove_calls) == 1


class TestPostEffectReportingFailure:
    """A reporting failure must not discard the observed physical effects."""

    def test_reporting_failure_keeps_the_completed_move_and_its_effects(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        _seed_move_pair(workspace)

        executor = _executor(workspace)

        import polaris.kernelone.llm.toolkit.executor.handlers.filesystem as filesystem_module

        def _broken_emit(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("labelled fault adapter: reporting channel unavailable")

        original_emit = filesystem_module._emit_file_written_event
        filesystem_module._emit_file_written_event = _broken_emit  # type: ignore[assignment]
        try:
            response = _run_handler(executor)
        finally:
            filesystem_module._emit_file_written_event = original_emit  # type: ignore[assignment]

        assert response["ok"] is True, response
        # PHYSICAL-FACT CORRECTION: the completed move changed two paths.
        assert response["effect_receipt"]["files_changed"] == ["dest.txt", "source.txt"]

        # Physical evidence: the move really completed.
        assert _read(workspace / "dest.txt") == "new content\n"
        assert not (workspace / "source.txt").exists()

        # The reporting failure is recorded as evidence rather than being
        # raised or silently dropped.
        operation = response["results"][0]
        assert operation["reporting_failure"]["stage"] == "file_written_event"
        assert operation["reporting_failure"]["files_changed"] == ["dest.txt", "source.txt"]
        assert "reporting channel unavailable" in operation["reporting_failure"]["error"]

    def test_reporting_failure_does_not_lose_the_destination_change(self, tmp_path: Path) -> None:
        """The changed-file evidence survives a broken reporting channel."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        _seed_move_pair(workspace)

        executor = _executor(workspace)

        import polaris.kernelone.llm.toolkit.executor.handlers.filesystem as filesystem_module

        def _broken_emit(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("labelled fault adapter: reporting channel unavailable")

        original_emit = filesystem_module._emit_file_written_event
        filesystem_module._emit_file_written_event = _broken_emit  # type: ignore[assignment]
        try:
            result = _execute(executor)
        finally:
            filesystem_module._emit_file_written_event = original_emit  # type: ignore[assignment]

        assert result["ok"] is True, result
        # PHYSICAL-FACT CORRECTION: the completed move changed two paths.
        assert result["result"]["effect_receipt"]["files_changed"] == ["dest.txt", "source.txt"]
        assert _read(workspace / "dest.txt") == "new content\n"


class _AfterUnlinkFaultAdapter:
    """Labelled fault adapter that fails *after* the real removal happened.

    The adapter performs the real ``os.remove`` and then raises.  This models
    an adapter whose call failed while the physical effect had already
    occurred, which is the case a failed call cannot distinguish from a
    no-effect call.  Every other operation stays real.
    """

    def __init__(self, inner: Any, faulting_path: str, *, error: BaseException) -> None:
        self._inner = inner
        self._faulting_path = faulting_path
        self._error = error
        self.remove_calls: list[str] = []

    def remove(self, path: str, *, missing_ok: bool = True) -> bool:
        if str(path) == self._faulting_path:
            self.remove_calls.append(str(path))
            self._inner.remove(path, missing_ok=missing_ok)
            raise self._error
        return self._inner.remove(path, missing_ok=missing_ok)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class TestUnknownPhysicalEffect:
    """A failed call after the effect happened is an unknown effect."""

    def test_failed_delete_after_real_unlink_is_not_reported_as_a_failed_deletion(self, tmp_path: Path) -> None:
        """The call failed, so the deletion outcome is unknown, not 'failed'."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        _seed_move_pair(workspace)

        inner = LocalFileSystemAdapter()
        fault = _AfterUnlinkFaultAdapter(
            inner,
            str(workspace / "source.txt"),
            error=OSError("labelled fault adapter: failed after the real unlink"),
        )
        executor = AgentAccelToolExecutor(str(workspace), allowed_tools=frozenset({"apply_patch"}))
        executor._kernel_fs = KernelFileSystem(str(workspace), fault)

        response = _run_handler(executor)

        assert response["ok"] is False, response
        effects = response["results"][0]["move_effects"]
        # The removal call failed, but the file is gone: the outcome is
        # unknown, never a fabricated successful or failed deletion.
        assert effects["source_delete"] == "unknown"
        assert effects["source_exists_after_failure"] is False
        assert effects["destination_write"] == "succeeded"

        # The confirmed destination write is retained, and the removed source
        # is a confirmed changed path too.
        assert response["effect_receipt"]["files_changed"] == ["dest.txt", "source.txt"]
        assert response["partial"] is True

        # Physical evidence: the destination was written and the source is
        # gone, exactly as the unknown effect states.
        assert _read(workspace / "dest.txt") == "new content\n"
        assert not (workspace / "source.txt").exists()

    def test_failed_delete_after_real_unlink_does_not_escape_unstructured(self, tmp_path: Path) -> None:
        """A non-OSError adapter failure must not escape the public execute."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        _seed_move_pair(workspace)

        inner = LocalFileSystemAdapter()
        fault = _AfterUnlinkFaultAdapter(
            inner,
            str(workspace / "source.txt"),
            error=RuntimeError("labelled fault adapter: failed after the real unlink"),
        )
        executor = AgentAccelToolExecutor(str(workspace), allowed_tools=frozenset({"apply_patch"}))
        executor._kernel_fs = KernelFileSystem(str(workspace), fault)

        # The public execute must return a structured result, not raise.
        result = _execute(executor)

        assert result["ok"] is False, result
        assert result["error_type"] == "move_source_delete_unknown"
        assert "labelled fault adapter" in result["error"]
        # The confirmed destination evidence is not lost.
        assert result["effect_receipt"]["files_changed"] == ["dest.txt", "source.txt"]
        assert _read(workspace / "dest.txt") == "new content\n"
        assert not (workspace / "source.txt").exists()

    def test_unknown_effect_is_not_retried_blindly(self, tmp_path: Path) -> None:
        """An unknown physical effect must not invite a blind replay."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        _seed_move_pair(workspace)

        inner = LocalFileSystemAdapter()
        fault = _AfterUnlinkFaultAdapter(
            inner,
            str(workspace / "source.txt"),
            error=RuntimeError("labelled fault adapter: failed after the real unlink"),
        )
        executor = AgentAccelToolExecutor(str(workspace), allowed_tools=frozenset({"apply_patch"}))
        executor._kernel_fs = KernelFileSystem(str(workspace), fault)

        result = _execute(executor)

        assert result["ok"] is False
        assert result["retryable"] is False
        # The removal was attempted exactly once.
        assert len(fault.remove_calls) == 1

    def test_pre_delete_denial_still_proves_the_deletion_did_not_happen(self, tmp_path: Path) -> None:
        """A denial before the effect proves no deletion happened."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        _seed_move_pair(workspace)

        inner = LocalFileSystemAdapter()
        fault_adapter = _FaultRemovingAdapter(inner, str(workspace / "source.txt"))
        executor = AgentAccelToolExecutor(str(workspace), allowed_tools=frozenset({"apply_patch"}))
        executor._kernel_fs = KernelFileSystem(str(workspace), fault_adapter)

        response = _run_handler(executor)

        effects = response["results"][0]["move_effects"]
        # The source is still present, so the deletion demonstrably did not
        # happen: "failed" is the accurate statement here.
        assert effects["source_delete"] == "failed"
        assert effects["source_exists_after_failure"] is True
        assert response["effect_receipt"]["files_changed"] == ["dest.txt"]
        assert _read(workspace / "source.txt") == "old content\n"


class TestSuccessfulMoveProjection:
    """A completed move changed two paths and must say so."""

    def test_successful_move_reports_both_confirmed_changed_paths(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        _seed_move_pair(workspace)

        executor = _executor(workspace)
        result = _execute(executor)

        assert result["ok"] is True, result
        payload = result["result"]
        # The destination was written and the source was removed: both are
        # confirmed changed paths, consistent with the delete operation
        # contract.
        assert payload["effect_receipt"]["files_changed"] == ["dest.txt", "source.txt"]
        operation = payload["results"][0]
        assert operation["files_changed"] == ["dest.txt", "source.txt"]
        assert operation["move_effects"]["destination_write"] == "succeeded"
        assert operation["move_effects"]["source_delete"] == "succeeded"
        assert operation["move_effects"]["source_exists_after_failure"] is False

        # Physical evidence agrees with the projection.
        assert _read(workspace / "dest.txt") == "new content\n"
        assert not (workspace / "source.txt").exists()

    def test_successful_move_reports_both_paths_at_executor_level(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        _seed_move_pair(workspace)

        executor = _executor(workspace)
        response = _run_handler(executor)

        assert response["ok"] is True
        assert response["effect_receipt"]["files_changed"] == ["dest.txt", "source.txt"]
        assert response["effect_receipt"]["move_effects"] == {
            "dest.txt:destination_write": "succeeded",
            "dest.txt:source_delete": "succeeded",
            "dest.txt:source_exists_after_failure": False,
        }
        # A successful move is not a partial effect.
        assert response.get("partial") is None


class TestOutwardProjection:
    """The executor-level projection must carry the real effect evidence."""

    def test_executor_projection_keeps_changed_files_for_partial_move(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        _seed_move_pair(workspace)

        inner = LocalFileSystemAdapter()
        fault_adapter = _FaultRemovingAdapter(inner, str(workspace / "source.txt"))
        executor = AgentAccelToolExecutor(str(workspace), allowed_tools=frozenset({"apply_patch"}))
        executor._kernel_fs = KernelFileSystem(str(workspace), fault_adapter)

        result = _execute(executor)

        # The executor keeps the handler evidence on the failed payload.
        assert result["ok"] is False
        assert result["effect_receipt"]["files_changed"] == ["dest.txt"]
        assert result["effect_receipt"]["partial"] is True
        assert result["handler_error_type"] == "partial_move_source_delete_failed"
        assert result["error_type"] == "partial_move_source_delete_failed"
        assert result["effect_receipt"]["partial_effects"][0]["move_effects"]["source_delete"] == "failed"

    def test_partial_move_is_not_retryable(self, tmp_path: Path) -> None:
        """A denied deletion must not invite a blind replay of the move."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        _seed_move_pair(workspace)

        inner = LocalFileSystemAdapter()
        fault_adapter = _FaultRemovingAdapter(inner, str(workspace / "source.txt"))
        executor = AgentAccelToolExecutor(str(workspace), allowed_tools=frozenset({"apply_patch"}))
        executor._kernel_fs = KernelFileSystem(str(workspace), fault_adapter)

        result = _execute(executor)

        assert result["ok"] is False
        assert result["retryable"] is False

    def test_legal_move_projection_is_unchanged(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        _seed_move_pair(workspace)

        executor = _executor(workspace)
        result = _execute(executor)

        assert result["ok"] is True
        # PHYSICAL-FACT CORRECTION: the completed move changed two paths.
        assert result["result"]["effect_receipt"]["files_changed"] == ["dest.txt", "source.txt"]
        assert result["result"]["effect_receipt"]["director_policy"]["dest.txt"]["delete"]["allowed"] is True


if __name__ == "__main__":  # pragma: no cover - manual execution helper
    raise SystemExit(pytest.main([__file__, "-v"]))


def test_failed_presence_observation_keeps_known_destination_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken observer after real unlink cannot erase the completed write."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _seed_move_pair(workspace)
    executor = _executor(workspace)
    original_remove = executor._kernel_fs.workspace_remove
    original_exists = executor._kernel_fs.workspace_exists
    attempts: list[str] = []

    def remove(path: str, *, missing_ok: bool = True) -> bool:
        attempts.append(path)
        original_remove(path, missing_ok=missing_ok)
        raise OSError("synthetic failure after real source removal")

    def exists(path: str) -> bool:
        if attempts and path == "source.txt":
            raise RuntimeError("synthetic presence observation unavailable")
        return original_exists(path)

    monkeypatch.setattr(executor._kernel_fs, "workspace_remove", remove)
    monkeypatch.setattr(executor._kernel_fs, "workspace_exists", exists)
    result = _execute(executor)

    assert result["ok"] is False
    assert result["retryable"] is False
    assert result["effect_receipt"]["files_changed"] == ["dest.txt"]
    effects = result["effect_receipt"]["move_effects"]
    assert effects["dest.txt:destination_write"] == "succeeded"
    assert effects["dest.txt:source_delete"] == "unknown"
    assert effects["dest.txt:source_exists_after_failure"] is None
    assert attempts == ["source.txt"]
    assert _read(workspace / "dest.txt") == "new content\n"
    assert not (workspace / "source.txt").exists()
