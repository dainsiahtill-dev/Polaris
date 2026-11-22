from __future__ import annotations

import asyncio
import json
from pathlib import Path

from polaris.cells.roles.runtime.internal.engineering_resident_agi import (
    VALID_TYPES,
    ResidentAGIRuntime,
    _normalize_decision,
    collect_signals,
)


def test_collect_signals_is_deterministic(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(json.dumps({"name": "app"}), encoding="utf-8")
    signals = collect_signals(tmp_path)
    assert "web:package.json" in signals["signals"]
    assert signals["has_package_json"] is True


def test_decision_accepted_when_type_valid() -> None:
    payload = {
        "schema_version": "resident_agi_decision.v1",
        "project_type": "web",
        "capabilities": ["web:frontend", "node:runtime"],
        "mcp_hints": [],
        "guidance": "用浏览器验收",
        "reason": "检测到 package.json",
    }
    signals = {"signals": ["web:package.json"]}
    validated = _normalize_decision(payload, signals)
    assert validated is not None and validated["project_type"] == "web"
    assert "browser_observe" in validated["capabilities"]
    assert validated["signals"] == ["web:package.json"]


def test_decision_rejected_for_unknown_type() -> None:
    payload = {
        "schema_version": "resident_agi_decision.v1",
        "project_type": "unknown_type",
        "capabilities": [],
        "mcp_hints": [],
        "guidance": "",
        "reason": "",
    }
    assert _normalize_decision(payload, {"signals": []}) is None


def test_valid_types_cover_expected_families() -> None:
    assert {"web", "rust", "go", "python", "reverse_engineering", "generic"} <= VALID_TYPES


class _InstantPool:
    def _post(self, request: dict[str, object]) -> str:
        return json.dumps({"project_type": "web", "reason": "signals"})


async def _wait_done(runtime: ResidentAGIRuntime) -> None:
    for _ in range(200):
        if runtime.done:
            return
        await asyncio.sleep(0.01)


def test_poll_returns_a_finished_decision_once() -> None:
    """A finished decision is consumed: the caller must not re-read it forever."""

    async def scenario() -> None:
        runtime = ResidentAGIRuntime(_InstantPool())  # type: ignore[arg-type]
        assert runtime.idle
        scheduled = runtime.start({"signals": ["web:package.json"]})
        assert scheduled is True
        await _wait_done(runtime)
        assert runtime.poll() is not None
        assert runtime.poll() is None
        assert runtime.idle

    asyncio.run(scenario())


def test_start_never_replaces_an_existing_job() -> None:
    """Neither an in-flight nor a finished-but-unconsumed job is replaced."""

    async def scenario() -> None:
        runtime = ResidentAGIRuntime(_InstantPool())  # type: ignore[arg-type]
        assert runtime.start({"signals": ["web:package.json"]}) is True
        assert runtime.start({"signals": []}) is False
        await _wait_done(runtime)
        idle_after_done = runtime.idle
        assert idle_after_done is False
        assert runtime.start({"signals": []}) is False
        assert runtime.poll() is not None

    asyncio.run(scenario())
