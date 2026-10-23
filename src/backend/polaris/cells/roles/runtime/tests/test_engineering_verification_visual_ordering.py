"""Real service/SQLite/sandbox/browser/native bodies; only model replies scripted.

Disposable webpage fixtures establish ordering, never Bench or model quality.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.runtime.public.contracts.engineering_run import (
    BrowserObservationConfig,
    PermissionScope,
    RoleRunCommand,
    RunLimits,
)
from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService
from polaris.cells.roles.runtime.tests.engineering_support import (
    ScriptedProvider,
    isolated_workspace as isolated_workspace,
    scripted_provider as scripted_provider,
)
from polaris.cells.roles.runtime.tests.test_engineering_autonomous_verification import _response
from polaris.kernelone.fs import get_default_adapter, guarded_create_directory

_ROOT = Path(__file__).resolve().parents[7]
_ARTIFACT = ".polaris/audits/director-qa-handoff-20261023/implementation"
_BROWSER = (
    Path.home()
    / ".cache/ms-playwright/chromium_headless_shell-1223/chrome-headless-shell-linux64/chrome-headless-shell"
)
_PACKAGES = Path.home() / ".pyenv/versions/3.12.3/lib/python3.12/site-packages"
_HTML = '<!doctype html><meta charset="utf-8"><style>body{background:#fff;color:#123;font:32px sans-serif}</style><h1>Verified fixture</h1>'


def _scope(workspace: Path) -> PermissionScope:
    return PermissionScope(
        (workspace, _PACKAGES / "playwright", _PACKAGES / "pyee", _PACKAGES / "greenlet", _BROWSER.parent),
        (workspace,),
        frozenset({"read_file", "browser_observe", "verify_project", "execute_command", "report_review"}),
        False,
        BrowserObservationConfig(str(_BROWSER), "/usr/bin/python3", (str(_PACKAGES),), True),
    )


def _pixels(body: dict[str, Any]) -> list[bytes]:
    return [
        base64.b64decode(block["image_url"]["url"].split(",", 1)[1], validate=True)
        for message in body["messages"]
        if isinstance(message.get("content"), list)
        for block in message["content"]
        if block.get("type") == "image_url"
    ]


@pytest.mark.parametrize("mode", ["explicit", "implicit", "stale", "missing"])
@pytest.mark.parametrize("require_qa", [False, True])
async def test_real_checks_precede_current_own_pixels_and_reuse_receipt(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    require_qa: bool,
) -> None:
    """Removing ordering repair prevents child execution or causes another build."""
    assert _BROWSER.is_file() and (_PACKAGES / "playwright").is_dir()
    workspace = isolated_workspace / "fixture"
    guarded_create_directory(str(isolated_workspace), "fixture/dist", parents=True, exist_ok=True)
    fs = get_default_adapter()
    fs.write_text(str(workspace / "dist/index.html"), _HTML, encoding="utf-8")
    fs.write_text(
        str(workspace / "build.py"),
        "from pathlib import Path\n"
        "p=Path('dist/index.html')\n"
        "before=p.stat()\n"
        "p.write_text(p.read_text(encoding='utf-8'),encoding='utf-8')\n"
        "after=p.stat()\n"
        "assert (before.st_mtime_ns,before.st_ctime_ns)!=(after.st_mtime_ns,after.st_ctime_ns)\n"
        "with Path('dist/count.txt').open('a',encoding='utf-8') as out: out.write('build\\n')\n"
        "print('REAL_BUILD_METADATA_CHANGED')\n",
        encoding="utf-8",
    )
    checks = [
        "python3 build.py",
        "python3 -c \"from pathlib import Path; assert 'Verified fixture' in Path('dist/index.html').read_text(encoding='utf-8'); print('REAL_CORE_CHECK')\"",
    ]
    before = (workspace / "dist/index.html").stat()
    capture = {"entry": "dist/index.html", "viewport": {"width": 640, "height": 360}, "frame_times_ms": [0]}
    if mode == "stale":
        scripted_provider.enqueue_json(_response(("before-build", "browser_observe", capture)))
    scripted_provider.enqueue_json(
        _response() if mode == "implicit" else _response(("verify", "verify_project", {"commands": checks}))
    )
    # Explicitly end first: implicit owner must check before asking for pixels;
    # explicit owner must reuse its completed current receipt after feedback.
    if mode != "implicit":
        scripted_provider.enqueue_json(_response(text="Checks finished."))
    visual_responses = (
        (
            _response(("qa-read", "read_file", {"file": "build.py"}), ("qa-capture", "browser_observe", capture)),
            _response(
                (
                    "qa-review",
                    "report_review",
                    {"approved": True, "feedback": "Current own pixels and current source inspected."},
                )
            ),
        )
        if require_qa
        else (
            _response(("director-capture", "browser_observe", capture)),
            _response(text="Inspected actual resulting page; ready for completion."),
        )
    )
    for response in () if mode == "missing" else visual_responses:
        scripted_provider.enqueue_json(response)
    scripted_provider.client("openai", workspace, monkeypatch)
    service = EngineeringRunService(
        workspace,
        require_qa=require_qa,
        writable_output_roots=(workspace / "dist",),
        permission_resolver=lambda _row: _scope(workspace),
    )
    command = RoleRunCommand(
        "ordering-" + mode + "-" + str(require_qa),
        "director",
        "Verify and visually inspect the disposable webpage fixture.",
        workspace,
        required_checks=tuple(checks) if mode == "implicit" else (),
    )
    await service.start(command, scope=_scope(workspace), limits=RunLimits(time.time() + 45, 2_000_000, 64_000))
    await service.wait(command.session_id, workspace=workspace)
    events = service.store.load_events(command.session_id)
    row = service.snapshot(command.session_id, workspace=workspace)
    mode = mode + "-" + str(require_qa)
    for label, value in (("requests", scripted_provider.requests), ("events", events), ("snapshot", row)):
        fs.write_text(
            str(_ROOT / _ARTIFACT / f"{mode}-{label}.json"),
            json.dumps(value, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    child = [
        event
        for event in events
        if event["kind"] == "tool_started" and event["payload"].get("tool") == "execute_command"
    ]
    # This is the causal RED assertion: no image can prevent authorized real checks.
    assert len(child) == 2, [(event["kind"], event["payload"]) for event in events if event["kind"] == "tool_observed"]
    assert fs.read_text(str(workspace / "dist/count.txt"), encoding="utf-8") == "build\n"
    assert fs.read_text(str(workspace / "dist/index.html"), encoding="utf-8") == _HTML
    after = (workspace / "dist/index.html").stat()
    assert (before.st_mtime_ns, before.st_ctime_ns) != (after.st_mtime_ns, after.st_ctime_ns)
    assert not service.store.pending_tool_calls(command.session_id)
    completed = [event for event in events if event["kind"] == "verification_completed"]
    assert len(completed) == 1 and completed[0]["payload"]["verification"] == "passed"
    assert all(check["exit_code"] == 0 for check in completed[0]["payload"]["checks"])
    assert bool(completed[0]["payload"]["input_binding"].get("requested_by")) == (not mode.startswith("implicit-"))
    if mode.startswith("missing-"):
        assert service.store.load_run(command.session_id, workspace=workspace)["verification"] == "passed"
        assert row["project_outcome"] != "accepted"
        assert any(event["kind"] == "role_started" and event["role"] == "qa" for event in events) == require_qa
        assert not any(_pixels(request["body"]) for request in scripted_provider.requests)
        assert ("checks already passed" in json.dumps(scripted_provider.requests).lower()) == (not require_qa)
        return
    assert row["project_outcome"] == "accepted", row
    assert row["verification"] == "passed"
    assert row["qa_verdict"] == ("approved" if require_qa else "not_run")
    prepared = [event for event in events if event["kind"] == "request_prepared"]
    assert len(prepared) == len(scripted_provider.requests)
    proofs = []
    captures = [
        event
        for event in events
        if event["kind"] == "tool_observed" and event["payload"].get("call_id") in {"director-capture", "qa-capture"}
    ]
    assert len(captures) == 1
    assert captures[0]["role"] == ("qa" if require_qa else "director")
    for event in captures:
        images = event["payload"]["images"]
        raw = [base64.b64decode(image["data_base64"], validate=True) for image in images]
        assert raw and all(
            hashlib.sha256(data).hexdigest() == image["sha256"] for data, image in zip(raw, images, strict=True)
        )
        later = [
            (frame, request["body"])
            for frame, request in zip(prepared, scripted_provider.requests, strict=True)
            if frame["role"] == event["role"]
            and frame["payload"]["turn_seq"] > event["payload"]["turn_seq"]
            and all(data in _pixels(request["body"]) for data in raw)
        ]
        assert later
        frame, body = later[0]
        assert f"You are {'Director' if event['role'] == 'director' else 'QA'}" in body["messages"][0]["content"]
        physical = [
            item
            for item in events
            if item["kind"] == "llm_request_observed"
            and item["role"] == event["role"]
            and item["payload"].get("turn_seq") == frame["payload"]["turn_seq"]
        ]
        assert physical
        for index, data in enumerate(raw):
            fs.write_bytes(str(_ROOT / _ARTIFACT / f"{mode}-{event['role']}-{index}.jpg"), data)
        proofs.append(
            {
                "role": event["role"],
                "capture_seq": event["seq"],
                "capture_turn": event["payload"]["turn_seq"],
                "consuming_turn": frame["payload"]["turn_seq"],
                "image_sha256": [image["sha256"] for image in images],
                "physical_request_audits": [item["payload"] for item in physical],
            }
        )
        assert completed[0]["seq"] < event["seq"]
    fs.write_text(
        str(_ROOT / _ARTIFACT / f"{mode}-image-proof.json"),
        json.dumps(
            {
                "proofs": proofs,
                "before_stat": {"mtime_ns": before.st_mtime_ns, "ctime_ns": before.st_ctime_ns},
                "after_stat": {"mtime_ns": after.st_mtime_ns, "ctime_ns": after.st_ctime_ns},
                "builder_count": 1,
                "core_count": 1,
                "physical_requests": len(scripted_provider.requests),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    feedback = json.dumps(scripted_provider.requests, ensure_ascii=False)
    assert ("checks already passed" in feedback.lower()) == (not require_qa)
    if mode.startswith("stale-"):
        projections = [
            event["payload"].get("visual_projection", {}) for event in events if event["kind"] == "request_prepared"
        ]
        assert any(item.get("omitted_image_count", 0) for item in projections)
