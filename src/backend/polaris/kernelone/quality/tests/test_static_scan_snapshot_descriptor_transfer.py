"""Real child directory acquisition must retain ownership until transfer."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from types import FrameType
from typing import Any

import pytest
from polaris.kernelone.quality import interface_ledger, scan_workspace_artifact_quality_evidence
from polaris.kernelone.quality.interface_ledger import WorkspaceReadSnapshot


def _descriptors() -> dict[int, str]:
    result: dict[int, str] = {}
    for name in os.listdir("/proc/self/fd"):
        try:
            result[int(name)] = os.readlink(f"/proc/self/fd/{name}")
        except FileNotFoundError:
            continue
    return result


@pytest.mark.parametrize("entry", ["root_init", "root_read", "nested_read"])
@pytest.mark.parametrize("fault", ["none", "interrupt", "oserror", "unicode"])
def test_acquired_child_has_cleanup_owner(tmp_path: Path, entry: str, fault: str) -> None:
    """Loss of pending ownership leaks a witnessed FD; swallowing interrupts fails too."""
    root = tmp_path / "工作区"
    nested = root / "目录"
    nested.mkdir(parents=True)
    source = nested / "main.py"
    text = "value='中文'\n"
    source.write_text(text, encoding="utf-8")
    reader = None if entry == "root_init" else WorkspaceReadSnapshot(root)
    function = "opener" if entry == "nested_read" else "_open_workspace_root"
    target = nested if entry == "nested_read" else root
    module_file = str(Path(interface_ledger.__file__).resolve())
    observed: dict[str, Any] = {}
    injected: BaseException | None = {
        "none": None,
        "interrupt": KeyboardInterrupt("child handoff interruption"),
        "oserror": OSError("child handoff refusal"),
        "unicode": UnicodeDecodeError("utf-8", b"\xff", 0, 1, "child handoff decode refusal"),
    }[fault]

    def trace(frame: FrameType, event: str, _arg: Any) -> Any:
        if observed or event != "line" or frame.f_code.co_filename != module_file:
            return trace
        if frame.f_code.co_name != function:
            return trace
        child = frame.f_locals.get("child")
        parent = frame.f_locals.get("directory")
        if not isinstance(child, int) or child == parent:
            return trace
        try:
            child_target = os.readlink(f"/proc/self/fd/{child}")
        except FileNotFoundError:
            # A completed transfer can retain a closed parent local until
            # the next iteration; it is not an acquired child witness.
            return trace
        if child_target != str(target):
            return trace
        identity = os.fstat(child)
        assert stat.S_ISDIR(identity.st_mode)
        expected = target.stat()
        assert (identity.st_dev, identity.st_ino) == (expected.st_dev, expected.st_ino)
        observed.update(
            filename=frame.f_code.co_filename,
            line=frame.f_lineno,
            function=function,
            child_fd=child,
            parent_fd=parent,
            child_identity=[identity.st_dev, identity.st_ino, identity.st_mode],
            child_target=str(target),
            fd_targets=_descriptors(),
        )
        if injected is not None:
            sys.settrace(None)
            raise injected
        return trace

    before = _descriptors()
    original_trace = sys.gettrace()
    caught: KeyboardInterrupt | None = None
    result = None
    try:
        sys.settrace(trace)
        try:
            if reader is None:
                reader = WorkspaceReadSnapshot(root)
            else:
                result = reader.read(source)
        except KeyboardInterrupt as exc:
            caught = exc
    finally:
        sys.settrace(original_trace)
        after = _descriptors()
        # Restore the test environment on RED using only the exact fixture FD.
        if observed:
            child_fd = observed["child_fd"]
            if child_fd not in before and after.get(child_fd) == str(target):
                value = os.fstat(child_fd)
                if [value.st_dev, value.st_ino, value.st_mode] == observed["child_identity"]:
                    os.close(child_fd)
    print(
        json.dumps(
            {
                "entry": entry,
                "fault": fault,
                "before": before,
                "trace": observed,
                "after": after,
                "propagated_interrupt": caught is injected and caught is not None,
            },
            ensure_ascii=False,
        )
    )
    assert observed, "actual acquired child semantic handoff was never reached"
    if fault == "interrupt":
        assert caught is injected, "KeyboardInterrupt must propagate unchanged"
    else:
        assert caught is None
        if result is not None:
            assert result.status == ("readable" if fault == "none" else "unknown")
            if fault == "none":
                assert result.text == text
                assert result.content_sha256 == hashlib.sha256(text.encode("utf-8")).hexdigest()
                assert reader is not None
                assert reader.read(source) is result
            else:
                assert str(injected) in result.detail
        elif fault != "none":
            assert reader is not None
            bound_result = reader.read(source)
            assert bound_result.status == "unknown"
            assert str(injected) in bound_result.detail
        else:
            assert reader is not None
            assert reader.read(source).text == text
    assert after == before, "acquired child directory descriptor escaped its cleanup owner"
    assert _descriptors() == before


def test_success_missing_and_decode_unknown_close_nested_descriptors(tmp_path: Path) -> None:
    nested = tmp_path / "中文"
    nested.mkdir()
    (nested / "valid.py").write_text("value='合法'\n", encoding="utf-8")
    (nested / "invalid.py").write_bytes(b"\xff")
    before = _descriptors()
    reader = WorkspaceReadSnapshot(tmp_path)
    assert reader.read(nested / "valid.py").text == "value='合法'\n"
    assert reader.read(nested / "absent.py").status == "missing"
    assert reader.read(nested / "invalid.py").status == "unknown"
    assert _descriptors() == before


@pytest.mark.parametrize("package", ['{"scripts":[]}', '{"scripts":{}}'])
def test_unknown_package_retains_independent_known_blocker(tmp_path: Path, package: str) -> None:
    (tmp_path / "main.py").write_text(
        "REQUIRED_TERM_PAIRS=[]\nREQUIRED_TERMS=[]\nfor term in REQUIRED_TERMS:\n    pass\n", encoding="utf-8"
    )
    (tmp_path / "package.json").write_text(package, encoding="utf-8")
    before = _descriptors()
    evidence = scan_workspace_artifact_quality_evidence(
        str(tmp_path), relative_paths=["main.py"], scan_mode="pure_static"
    )
    assert evidence.scan_complete == (package == '{"scripts":{}}')
    assert any(issue.code == "python_unused_term_pairs" for issue in evidence.issues)
    assert _descriptors() == before
