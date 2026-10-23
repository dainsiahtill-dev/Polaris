"""Synthetic offline built-page semantics over the real service/K1 browser owner."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from polaris.cells.roles.runtime.public.contracts.engineering_run import ToolObservation
from polaris.cells.roles.runtime.tests.test_engineering_browser_complete_flow import _dispatch, _seed
from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter, guarded_create_directory

READY = "ESM_ABSOLUTE_CSS_READY:rgb(12, 34, 56)"


@pytest.fixture
def built_workspace(tmp_path: Path) -> Path:
    fs = KernelFileSystem(str(tmp_path), LocalFileSystemAdapter(), read_only_paths=True)
    for directory in ["public/dist/nested", "public/dist/assets"]:
        fs.workspace_mkdir(directory, parents=True, exist_ok=True)
    files = {
        "public/dist/nested/index.html": '<!doctype html><meta charset="utf-8"><title>Synthetic built app</title><link rel="stylesheet" href="/assets/style.css"><output id="marker">MODULE_NOT_EXECUTED</output><script type="module" src="/assets/boot.mjs"></script>',
        "public/dist/assets/style.css": "#marker{color:rgb(12,34,56);font:22px sans-serif}",
        "public/dist/assets/boot.mjs": 'import {label} from "./feature.mjs"; const node=document.querySelector("#marker");node.textContent=label+":"+getComputedStyle(node).color;',
        "public/dist/assets/feature.mjs": 'export const label="ESM_ABSOLUTE_CSS_READY";',
    }
    for name, content in files.items():
        fs.workspace_write_text_atomic(name, content, encoding="utf-8")
    return tmp_path


def save_capture(observation: ToolObservation, label: str) -> None:
    root = Path(
        os.environ.get("POLARIS_TEST_AUDIT_ROOT", str(Path(__file__).resolve().parents[7] / ".polaris/test-workspaces"))
    ) / (label + "-" + uuid4().hex)
    guarded_create_directory(
        str(root.parents[2]), root.relative_to(root.parents[2]).as_posix(), parents=True, exist_ok=True
    )
    fs = get_default_adapter()
    fs.write_text(
        str(root / "observation.json"),
        json.dumps({"ok": observation.ok, "content": observation.content}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    for n, image in enumerate(observation.images):
        data = base64.b64decode(image.data_base64, validate=True)
        assert hashlib.sha256(data).hexdigest() == image.sha256
        fs.write_bytes(str(root / (str(n) + ".jpg")), data)


async def test_static_origin_executes_absolute_esm_and_css_without_rewriting(built_workspace: Path) -> None:
    service, command, scope, limits = _seed(built_workspace, "static-origin")
    fs = get_default_adapter()
    paths = [
        built_workspace / "public/dist" / name
        for name in ["nested/index.html", "assets/style.css", "assets/boot.mjs", "assets/feature.mjs"]
    ]
    before = {str(p): fs.read_bytes(str(p)) for p in paths}
    workspace_fs = KernelFileSystem(str(built_workspace), LocalFileSystemAdapter(), read_only_paths=True)
    metadata_before = {str(p): workspace_fs.workspace_lstat(str(p)) for p in paths}
    observation = await _dispatch(
        service,
        command,
        scope,
        limits,
        {"entry": "public/dist/nested/index.html", "web_root": "public/dist", "frame_times_ms": [100]},
    )
    save_capture(observation, "static-origin")
    assert observation.ok, observation.content
    payload = json.loads(observation.content)
    assert READY in payload["dom"]["body_text"], payload
    assert payload["web_root"] == "public/dist"
    assert payload["origin_mode"] == "static"
    assert payload["network_allowed"] is False
    assert not payload["page_errors"]
    assert {str(p): fs.read_bytes(str(p)) for p in paths} == before
    assert {str(p): workspace_fs.workspace_lstat(str(p)) for p in paths} == metadata_before
    assert not service.store.pending_tool_calls(command.session_id)


async def test_legacy_file_mode_still_reports_unexecuted_esm(built_workspace: Path) -> None:
    service, command, scope, limits = _seed(built_workspace, "legacy-file")
    observation = await _dispatch(
        service, command, scope, limits, {"entry": "public/dist/nested/index.html", "frame_times_ms": [100]}
    )
    save_capture(observation, "legacy-file")
    assert observation.ok, observation.content
    payload = json.loads(observation.content)
    assert "MODULE_NOT_EXECUTED" in payload["dom"]["body_text"]
    assert READY not in payload["dom"]["body_text"]


@pytest.mark.parametrize(
    "arguments,reason",
    [
        ({"entry": "public/dist/nested/index.html", "web_root": ""}, "web_root"),
        ({"entry": "public/dist/nested/index.html", "web_root": "/tmp"}, "workspace-relative"),
        ({"entry": "public/dist/nested/index.html", "web_root": "../outside"}, "workspace-relative"),
        ({"entry": "public/dist/nested/index.html", "web_root": "."}, "read permission"),
        ({"entry": "public/dist/nested/index.html", "web_root": "public/dist/assets"}, "contain entry"),
        ({"entry": "public/dist/nested/index.html", "web_root": "public/.hidden"}, "hidden"),
    ],
)
async def test_explicit_static_root_rejects_invalid_authority_before_process(
    built_workspace: Path, arguments: dict, reason: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.roles.runtime.internal.engineering_tools import EngineeringTools

    service, command, scope, limits = _seed(built_workspace, "invalid-root")

    async def forbidden(*args: object, **kwargs: object) -> object:
        pytest.fail("invalid web root reached physical process")

    monkeypatch.setattr(EngineeringTools, "_process", forbidden)
    observation = await _dispatch(service, command, scope, limits, arguments)
    assert not observation.ok and not observation.images
    assert reason in observation.content, observation.content
    assert not service.store.pending_tool_calls(command.session_id)


async def test_static_origin_rejects_other_roots_hidden_links_methods_and_network(built_workspace: Path) -> None:
    import os

    fs = KernelFileSystem(str(built_workspace), LocalFileSystemAdapter(), read_only_paths=True)
    fs.workspace_write_text_atomic("public/private.txt", "SYNTHETIC_OUTSIDE_ROOT_SECRET", encoding="utf-8")
    fs.workspace_write_text_atomic("public/dist/.env", "SYNTHETIC_HIDDEN_SECRET", encoding="utf-8")
    # Native fixture boundary only: KFS deliberately exposes no symlink writer.
    os.symlink(str(built_workspace / "public/private.txt"), str(built_workspace / "public/dist/assets/link.txt"))
    requests = [
        "/.env",
        "/%2eenv",
        "/%252e%252e/private.txt",
        "/assets/link.txt",
        "/../private.txt",
        "https://other.invalid/private.txt",
        "https://polaris.invalid:444/private.txt",
    ]
    script = (
        "const urls="
        + json.dumps(requests)
        + ';Promise.all(urls.map(url=>fetch(url).then(r=>r.ok?r.text():"DENIED").catch(()=>"DENIED"))).then(values=>document.querySelector("#marker").textContent=values.every(v=>v==="DENIED")?"ALL_DENIED":"LEAK:"+values.join("|"));fetch("/assets/feature.mjs",{method:"POST",body:"DENIED_WRITE"}).catch(()=>{});fetch("file://'
        + str(built_workspace / "public/private.txt")
        + '").catch(()=>{});'
    )
    fs.workspace_write_text_atomic(
        "public/dist/security.html",
        '<output id="marker">WAIT</output><script>' + script + "</script>",
        encoding="utf-8",
    )
    service, command, scope, limits = _seed(built_workspace, "static-security")
    observation = await _dispatch(
        service,
        command,
        scope,
        limits,
        {"entry": "public/dist/security.html", "web_root": "public/dist", "frame_times_ms": [200]},
    )
    save_capture(observation, "static-security")
    assert observation.ok, observation.content
    payload = json.loads(observation.content)
    assert "ALL_DENIED" in payload["dom"]["body_text"], payload
    assert "SECRET" not in payload["dom"]["body_text"]
    assert payload["network_allowed"] is False
    assert "https://other.invalid/private.txt" in payload["blocked_requests"]
    assert any("POST" in str(error) for error in payload["static_resources"]["errors"])
    assert any("link" in str(error) for error in payload["static_resources"]["errors"])
    assert not service.store.pending_tool_calls(command.session_id)


@pytest.mark.parametrize("case", ["single", "total"])
async def test_actual_static_snapshot_byte_limits_fail_explicitly(built_workspace: Path, case: str) -> None:
    fs = KernelFileSystem(str(built_workspace), LocalFileSystemAdapter(), read_only_paths=True)
    if case == "single":
        fs.workspace_write_bytes("public/dist/large.bin", b"x" * (16 * 1024 * 1024 + 1))
        script = 'fetch("/large.bin",{method:"HEAD"}).catch(()=>{});'
    else:
        for n in range(4):
            fs.workspace_write_bytes("public/dist/part" + str(n) + ".bin", b"x" * (16 * 1024 * 1024))
        script = 'Promise.all([0,1,2,3].map(n=>fetch("/part"+n+".bin",{method:"HEAD"}))).catch(()=>{});'
    fs.workspace_write_text_atomic(
        "public/dist/budget.html",
        "<output>Explicit budget fixture</output><script>" + script + "</script>",
        encoding="utf-8",
    )
    service, command, scope, limits = _seed(built_workspace, "resource-budget-" + case)
    observation = await _dispatch(
        service,
        command,
        scope,
        limits,
        {"entry": "public/dist/budget.html", "web_root": "public/dist", "frame_times_ms": [300]},
    )
    save_capture(observation, "static-budget-" + case)
    assert not observation.ok and not observation.images
    assert "static resource" in observation.content.lower() and "budget" in observation.content.lower(), (
        observation.content
    )
    failure = json.loads(observation.content)
    assert failure["origin_mode"] == "static" and failure["web_root"] == "public/dist"
    assert failure["static_resources"]["errors"]
    assert failure["static_resources"]["bytes_read"] <= 64 * 1024 * 1024
    assert not service.store.pending_tool_calls(command.session_id)


async def test_static_head_boundary_preserves_empty_body_and_inclusive_single_limit(built_workspace: Path) -> None:
    fs = KernelFileSystem(str(built_workspace), LocalFileSystemAdapter(), read_only_paths=True)
    fs.workspace_write_bytes("public/dist/exact.bin", b"x" * (16 * 1024 * 1024))
    fs.workspace_write_text_atomic(
        "public/dist/head.html",
        '<output id="marker">WAIT</output><script>fetch("/exact.bin",{method:"HEAD"}).then(async r=>document.querySelector("#marker").textContent="HEAD:"+r.headers.get("content-length")+":"+(await r.text()).length)</script>',
        encoding="utf-8",
    )
    service, command, scope, limits = _seed(built_workspace, "static-head")
    observation = await _dispatch(
        service,
        command,
        scope,
        limits,
        {"entry": "public/dist/head.html", "web_root": "public/dist", "frame_times_ms": [150]},
    )
    assert observation.ok, observation.content
    payload = json.loads(observation.content)
    assert "HEAD:16777216:0" in payload["dom"]["body_text"]
    assert any(r["size"] == 16777216 and r["method"] == "HEAD" for r in payload["static_resources"]["reads"])


async def test_static_root_and_entry_symlinks_are_rejected(built_workspace: Path) -> None:
    import os

    os.symlink("dist", str(built_workspace / "public/root-alias"))
    os.symlink("nested/index.html", str(built_workspace / "public/dist/alias.html"))
    service, command, scope, limits = _seed(built_workspace, "static-symlink")
    for arguments in [
        {"entry": "public/dist/nested/index.html", "web_root": "public/root-alias"},
        {"entry": "public/dist/alias.html", "web_root": "public/dist"},
    ]:
        observation = await _dispatch(service, command, scope, limits, arguments)
        assert not observation.ok and not observation.images
        assert "symlink" in observation.content.lower() or "link" in observation.content.lower(), observation.content


async def test_static_capture_cancel_uses_original_owner_and_drains(
    built_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from polaris.cells.roles.runtime.internal.engineering_tools import EngineeringTools

    service, command, scope, limits = _seed(built_workspace, "static-cancel")
    entered = asyncio.Event()
    cancel = asyncio.Event()
    original = EngineeringTools._process

    async def watched(self: EngineeringTools, argv: list[str], *, timeout: float, writes: tuple[Path, ...]) -> object:
        entered.set()
        return await original(self, argv, timeout=timeout, writes=writes)

    monkeypatch.setattr(EngineeringTools, "_process", watched)
    active = asyncio.create_task(
        _dispatch(
            service,
            command,
            scope,
            limits,
            {
                "entry": "public/dist/nested/index.html",
                "web_root": "public/dist",
                "actions": [{"type": "click", "selector": "#marker", "wait_ms": 9000}],
                "frame_times_ms": [0],
            },
            cancel,
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=5)
    await asyncio.sleep(1)
    cancel.set()
    observation = await active
    assert observation.interrupted and not observation.ok and not observation.images
    assert "cancelled_and_drained" in observation.content
    assert not service.store.pending_tool_calls(command.session_id)


async def test_static_unknown_drain_keeps_original_pending_identity(
    built_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.roles.runtime.internal.engineering_tools import EngineeringTools
    from polaris.kernelone.process.process_tree import ProcessTreeDrainError

    service, command, scope, limits = _seed(built_workspace, "static-unknown")

    async def unproved(*args: object, **kwargs: object) -> object:
        raise ProcessTreeDrainError("synthetic original-owner drain remains UNKNOWN")

    monkeypatch.setattr(EngineeringTools, "_process", unproved)
    with pytest.raises(ProcessTreeDrainError, match="UNKNOWN"):
        await _dispatch(
            service, command, scope, limits, {"entry": "public/dist/nested/index.html", "web_root": "public/dist"}
        )
    assert len(service.store.pending_tool_calls(command.session_id)) == 1
    assert not service.store.load_events(command.session_id, kinds=("visual_observation_completed",))


@pytest.mark.parametrize("phase", ["dom", "cleanup"])
@pytest.mark.parametrize("resource_size", [32, 16 * 1024 * 1024 + 1], ids=["bounded-control", "oversized"])
async def test_late_resource_budget_is_finalized_before_success(
    built_workspace: Path, resource_size: int, phase: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise actual late reads; cleanup injects a real finalizer browser call."""
    from polaris.cells.roles.runtime.internal import engineering_browser

    fs = KernelFileSystem(str(built_workspace), LocalFileSystemAdapter(), read_only_paths=True)
    fs.workspace_write_bytes("public/dist/late.bin", b"x" * resource_size)
    late_request = """() => {
        fetch('/late.bin',{method:'HEAD'}).catch(()=>{});
        const end=performance.now()+300;while(performance.now()<end){};
        return 'LATE_DOM_RESOURCE_REQUEST';
    }"""
    html = '<!doctype html><title>Late resource probe</title><input id="probe">'
    if phase == "dom":
        html += (
            "<script>Object.defineProperty(document.querySelector('#probe'),'value',{get:"
            + late_request
            + "});</script>"
        )
    else:
        # Test-only lifecycle seam: retain real Chromium/KFS/route code, but
        # schedule the request in cleanup after every normal sampling operation.
        close = "                context.close()"
        assert engineering_browser._BROWSER_PROGRAM.count(close) == 1
        monkeypatch.setattr(
            engineering_browser,
            "_BROWSER_PROGRAM",
            engineering_browser._BROWSER_PROGRAM.replace(
                close, "                page.evaluate(" + repr(late_request) + ")\n" + close
            ),
        )
    fs.workspace_write_text_atomic("public/dist/late.html", html, encoding="utf-8")
    service, command, scope, limits = _seed(built_workspace, "late-budget-" + phase)
    observation = await _dispatch(
        service,
        command,
        scope,
        limits,
        {"entry": "public/dist/late.html", "web_root": "public/dist", "frame_times_ms": [0]},
    )
    save_capture(observation, "late-budget-" + phase + "-" + str(resource_size))
    payload = json.loads(observation.content)
    if resource_size > 16 * 1024 * 1024:
        assert not observation.ok and not observation.images, observation.content
        assert "budget" in payload["error"]
        assert payload["origin_mode"] == "static" and payload["web_root"] == "public/dist"
        assert any(
            error["method"] == "HEAD" and error["url"].endswith("/late.bin")
            for error in payload["static_resources"]["errors"]
        )
    else:
        assert observation.ok and observation.images, observation.content
        if phase == "dom":
            assert payload["dom"]["controls"][0]["value"] == "LATE_DOM_RESOURCE_REQUEST"
        assert payload["static_resources"]["read_count"] == 2
        assert any(
            resource["method"] == "HEAD" and resource["path"] == "late.bin" and resource["size"] == resource_size
            for resource in payload["static_resources"]["reads"]
        )
    assert not service.store.pending_tool_calls(command.session_id)
