"""Synthetic browser observations; actual K1 isolation, never Bench delivery proof."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

EVIDENCE_ROOT = (
    Path.home()
    / ".polaris/audit_archives/autonomous-delivery-20261018/engineering-v2-refactor-20261020/task17-native-vision/browser-implementation"
)
BROWSER = (
    Path.home()
    / ".cache/ms-playwright/chromium_headless_shell-1223/chrome-headless-shell-linux64/chrome-headless-shell"
)
PACKAGES = Path.home() / ".pyenv/versions/3.12.3/lib/python3.12/site-packages"


def _capture():
    # Failure names the missing production behavior before any fake is involved.
    import importlib.util

    module = "polaris.cells.roles.runtime.internal.engineering_browser"
    assert importlib.util.find_spec(module) is not None, "native browser capture adapter is absent"
    return importlib.import_module(module).capture_browser_observation


def _scope(workspace: Path, **overrides):
    from polaris.cells.roles.runtime.public.contracts.engineering_run import BrowserObservationConfig, PermissionScope

    values = {
        "read_roots": (workspace, PACKAGES / "playwright", PACKAGES / "pyee", PACKAGES / "greenlet", BROWSER.parent),
        "write_roots": (workspace,),
        "enabled_tools": frozenset({"browser_observe"}),
        "network_allowed": True,
        "browser": BrowserObservationConfig(
            executable=str(BROWSER), python_executable="/usr/bin/python3", python_paths=(str(PACKAGES),)
        ),
    }
    values.update(overrides)
    return PermissionScope(**values)


@pytest.fixture
def workspace():
    EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="browser-case-", dir=EVIDENCE_ROOT) as directory:
        root = Path(directory)
        (root / ".polaris").mkdir()
        (root / "index.html").write_text(
            '<!doctype html><meta charset="utf-8"><style>body{margin:0}#shape{width:80px;height:80px;background:red;animation:go .8s linear infinite alternate}@keyframes go{from{transform:translateX(10px)}to{transform:translateX(230px)}}</style><div id="shape"></div><button id="pause">Pause</button><script>document.querySelector("#pause").onclick=()=>document.querySelector("#shape").style.animationPlayState="paused";</script>',
            encoding="utf-8",
        )
        yield root


def _runner(workspace, scope):
    from polaris.kernelone.process.process_tree import ProcessTreeRunControl, run_process_tree_safe
    from polaris.kernelone.process.sandbox import build_workspace_sandbox_argv

    async def execute(argv, timeout):
        sandboxed = build_workspace_sandbox_argv(
            argv, workspace=workspace, cwd=workspace, read_roots=scope.read_roots, write_roots=(), network_allowed=False
        )
        return await asyncio.to_thread(
            run_process_tree_safe,
            sandboxed,
            cwd=workspace,
            timeout=timeout,
            encoding="utf-8",
            cancel_control=ProcessTreeRunControl(),
        )

    return execute


def _decode(image):
    from PIL import Image

    raw = base64.b64decode(image.data_base64, validate=True)
    assert hashlib.sha256(raw).hexdigest() == image.sha256
    decoded = Image.open(io.BytesIO(raw))
    assert decoded.size == (image.width, image.height)
    assert image.media_type == "image/jpeg"
    return raw


def test_actual_readonly_capture_returns_temporal_jpeg_pixels(workspace):
    capture = _capture()
    scope = _scope(workspace)
    source = (workspace / "index.html").read_bytes()
    result = asyncio.run(
        capture(
            "capture-1",
            {"viewport": {"width": 640, "height": 360}, "frame_times_ms": [0, 350]},
            workspace=workspace,
            scope=scope,
            run_process=_runner(workspace, scope),
        )
    )
    assert result.ok, result.content
    assert len(result.images) == 2
    first, second = (_decode(image) for image in result.images)
    assert first != second
    assert all((image.width, image.height) == (640, 360) for image in result.images)
    assert (workspace / "index.html").read_bytes() == source
    assert sorted(path.name for path in workspace.iterdir()) == [".polaris", "index.html"]


def test_actual_click_pause_freezes_later_frames(workspace):
    capture = _capture()
    scope = _scope(workspace)
    result = asyncio.run(
        capture(
            "capture-2",
            {"actions": [{"type": "click", "selector": "#pause", "wait_ms": 80}], "frame_times_ms": [0, 300]},
            workspace=workspace,
            scope=scope,
            run_process=_runner(workspace, scope),
        )
    )
    assert result.ok, result.content
    assert _decode(result.images[0]) == _decode(result.images[1])


@pytest.mark.parametrize(
    "arguments",
    [
        {"entry": "../outside.html"},
        {"entry": ".polaris/hidden.html"},
        {"entry": "https://example.com"},
        {"entry": "/etc/passwd"},
        {"entry": "index.html", "executable": "/bin/sh"},
        {"viewport": {"width": 319, "height": 360}},
        {"viewport": {"width": 640, "height": 1201}},
        {"frame_times_ms": []},
        {"frame_times_ms": [0, 1, 2, 3, 4]},
        {"frame_times_ms": [10001]},
        {"frame_times_ms": [20, 10]},
        {"frame_times_ms": [True]},
        {"actions": [{"type": "eval", "script": "unsafe"}]},
        {"actions": [{"type": "click", "selector": "#pause", "wait_ms": 9900}], "frame_times_ms": [200]},
    ],
)
def test_invalid_or_out_of_scope_input_is_rejected_before_dispatch(workspace, arguments):
    capture = _capture()
    scope = _scope(workspace)

    async def forbidden(_argv, _timeout):
        pytest.fail("invalid browser request reached physical dispatch")

    result = asyncio.run(capture("invalid", arguments, workspace=workspace, scope=scope, run_process=forbidden))
    assert not result.ok
    assert not result.images


def test_entry_symlink_and_explicit_empty_read_grants_are_rejected(workspace):
    capture = _capture()
    (workspace / "linked.html").symlink_to(workspace / "index.html")

    async def forbidden(_argv, _timeout):
        pytest.fail("unauthorized browser read reached physical dispatch")

    for arguments, scope in [({"entry": "linked.html"}, _scope(workspace)), ({}, _scope(workspace, read_roots=()))]:
        result = asyncio.run(capture("denied", arguments, workspace=workspace, scope=scope, run_process=forbidden))
        assert not result.ok
        assert not result.images


def test_browser_is_explicitly_unavailable_without_host_configuration(workspace):
    capture = _capture()

    async def forbidden(_argv, _timeout):
        pytest.fail("disabled browser reached physical dispatch")

    result = asyncio.run(
        capture("off", {}, workspace=workspace, scope=_scope(workspace, browser=None), run_process=forbidden)
    )
    assert not result.ok
    assert "unavailable" in result.content.lower()


def test_actual_page_network_requests_are_blocked(workspace):
    capture = _capture()
    (workspace / "index.html").write_text(
        '<!doctype html><script>fetch("https://invalid.example/").catch(()=>console.log("NETWORK_DENIED"))</script><h1>Local</h1>',
        encoding="utf-8",
    )
    scope = _scope(workspace)
    result = asyncio.run(
        capture(
            "net", {"frame_times_ms": [200]}, workspace=workspace, scope=scope, run_process=_runner(workspace, scope)
        )
    )
    assert result.ok, result.content
    payload = json.loads(result.content)
    assert any("invalid.example" in item for item in payload["blocked_requests"])
    assert payload["network_allowed"] is False


def test_process_cancel_and_unknown_drain_remain_with_physical_owner(workspace):
    capture = _capture()
    from polaris.kernelone.process.process_tree import ProcessTreeDrainError

    async def cancelled(_argv, _timeout):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(capture("cancel", {}, workspace=workspace, scope=_scope(workspace), run_process=cancelled))

    async def unknown(_argv, _timeout):
        raise ProcessTreeDrainError("synthetic unknown physical drain")

    with pytest.raises(ProcessTreeDrainError):
        asyncio.run(capture("unknown", {}, workspace=workspace, scope=_scope(workspace), run_process=unknown))


def test_target_local_driver_cannot_forge_qa_capture_even_in_readonly_role(workspace):
    capture = _capture()
    from polaris.cells.roles.runtime.public.contracts.engineering_run import BrowserObservationConfig

    driver = workspace / "fake-browser"
    driver.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    driver.chmod(0o755)
    scope = _scope(
        workspace, write_roots=(), browser=BrowserObservationConfig(str(driver), "/usr/bin/python3", (str(PACKAGES),))
    )

    async def forbidden(_argv, _timeout):
        pytest.fail("target-controlled browser driver reached trusted capture dispatch")

    result = asyncio.run(capture("forged", {}, workspace=workspace, scope=scope, run_process=forbidden))
    assert not result.ok
    assert not result.images


def test_target_import_shadow_cannot_replace_playwright_capture(workspace):
    capture = _capture()
    (workspace / "playwright.py").write_text('raise RuntimeError("TARGET_IMPORT_SHADOW")', encoding="utf-8")
    (workspace / "json.py").write_text('raise RuntimeError("TARGET_IMPORT_SHADOW")', encoding="utf-8")
    scope = _scope(workspace)
    result = asyncio.run(
        capture(
            "imports", {"frame_times_ms": [0]}, workspace=workspace, scope=scope, run_process=_runner(workspace, scope)
        )
    )
    assert result.ok, result.content
    assert len(result.images) == 1
    _decode(result.images[0])


def test_actual_keypress_action_can_activate_focused_button(workspace):
    capture = _capture()
    scope = _scope(workspace)
    result = asyncio.run(
        capture(
            "key",
            {
                "actions": [{"type": "keypress", "selector": "#pause", "key": "Enter", "wait_ms": 80}],
                "frame_times_ms": [0, 250],
            },
            workspace=workspace,
            scope=scope,
            run_process=_runner(workspace, scope),
        )
    )
    assert result.ok, result.content
    assert _decode(result.images[0]) == _decode(result.images[1])


@pytest.mark.parametrize(
    "stdout",
    [
        '{"images":',
        '{"images":[]}',
        '{"images":[{"media_type":"image/jpeg","data_base64":"not-base64","sha256":"x","width":1000,"height":750,"label":"bad"}]}',
    ],
)
def test_truncated_missing_or_malformed_capture_is_explicit_failure(workspace, stdout):
    capture = _capture()

    async def malformed(argv, _timeout):
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    result = asyncio.run(
        capture(
            "malformed", {"frame_times_ms": [0]}, workspace=workspace, scope=_scope(workspace), run_process=malformed
        )
    )
    assert not result.ok
    assert not result.images


def test_total_image_bytes_are_bounded_without_silent_frame_truncation(workspace):
    capture = _capture()
    import random

    from PIL import Image

    raw = io.BytesIO()
    Image.frombytes("RGB", (640, 480), random.Random(0).randbytes(640 * 480 * 3)).save(raw, format="JPEG", quality=90)
    data = raw.getvalue()
    assert len(data) < 1024 * 1024 < len(data) * 4
    image = {
        "media_type": "image/jpeg",
        "data_base64": base64.b64encode(data).decode("ascii"),
        "width": 640,
        "height": 480,
        "sha256": hashlib.sha256(data).hexdigest(),
        "label": "synthetic image",
    }

    async def oversized(argv, _timeout):
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps({"images": [image] * 4}), stderr="")

    result = asyncio.run(
        capture(
            "large",
            {"viewport": {"width": 640, "height": 480}, "frame_times_ms": [0, 1, 2, 3]},
            workspace=workspace,
            scope=_scope(workspace),
            run_process=oversized,
        )
    )
    assert not result.ok
    assert not result.images
    assert "limit" in result.content


def _live_capture_processes(workspace):
    """Observe only this fixture's browser descendants, without mutating them."""
    processes = {}
    for directory in Path("/proc").iterdir():
        if not directory.name.isdigit():
            continue
        try:
            values = (directory / "stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
            argv = (directory / "cmdline").read_bytes().split(b"\0")
            processes[int(directory.name)] = (int(values[1]), argv)
        except (OSError, ValueError, IndexError):
            continue
    browser_pids = []
    token = str(workspace).encode("utf-8")
    for pid, (_parent, argv) in processes.items():
        if not argv or Path(argv[0].decode("utf-8", errors="replace")).name != "chrome-headless-shell":
            continue
        parent = pid
        seen = set()
        while parent in processes and parent not in seen:
            seen.add(parent)
            next_parent, parent_argv = processes[parent]
            if parent != pid and any(token in value for value in parent_argv):
                browser_pids.append(pid)
                break
            parent = next_parent
    return browser_pids


@pytest.mark.parametrize("ending", ["cancel", "timeout"])
def test_actual_cancel_and_timeout_drain_an_observed_browser_tree(workspace, ending):
    capture = _capture()
    from polaris.kernelone.process.process_tree import (
        ProcessTreeCancelledError,
        ProcessTreeRunControl,
        run_process_tree_safe,
    )
    from polaris.kernelone.process.sandbox import build_workspace_sandbox_argv

    scope = _scope(workspace)
    control = ProcessTreeRunControl()
    observed = []

    async def run_case():
        async def execute(argv, _timeout):
            sandboxed = build_workspace_sandbox_argv(
                argv,
                workspace=workspace,
                cwd=workspace,
                read_roots=scope.read_roots,
                write_roots=(),
                network_allowed=False,
            )
            worker = asyncio.create_task(
                asyncio.to_thread(
                    run_process_tree_safe,
                    sandboxed,
                    cwd=workspace,
                    timeout=3.0 if ending == "timeout" else 20.0,
                    encoding="utf-8",
                    cancel_control=control,
                )
            )
            for _ in range(40):
                observed.extend(_live_capture_processes(workspace))
                if observed or worker.done():
                    break
                await asyncio.sleep(0.05)
            assert observed, "test never observed an actual browser before terminating it"
            if ending == "cancel":
                control.cancel()
            return await worker

        expected = ProcessTreeCancelledError if ending == "cancel" else subprocess.TimeoutExpired
        with pytest.raises(expected):
            await capture("drain", {"frame_times_ms": [10000]}, workspace=workspace, scope=scope, run_process=execute)

    asyncio.run(run_case())
    assert observed
    assert all(not Path("/proc", str(pid)).exists() for pid in observed)
    assert not _live_capture_processes(workspace)
