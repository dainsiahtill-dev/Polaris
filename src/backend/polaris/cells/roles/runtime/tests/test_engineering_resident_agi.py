from __future__ import annotations

import json
from pathlib import Path

from polaris.cells.roles.runtime.internal.engineering_resident_agi import (
    VALID_TYPES,
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


def test_runtime_poll_consumes_a_finished_decision_and_start_is_gated() -> None:
    """A finished decision is returned once; a fresh one must be scheduled."""

    import asyncio

    from polaris.cells.roles.runtime.internal.engineering_resident_agi import ResidentAGIRuntime

    class _Pool:
        def _post(self, request: dict[str, object]) -> str:
            return json.dumps({"project_type": "web", "reason": "signals"})

    async def scenario() -> None:
        runtime = ResidentAGIRuntime(_Pool())  # type: ignore[arg-type]
        assert runtime.idle is True
        assert runtime.start({"signals": ["web:package.json"]}) is True
        assert runtime.idle is False
        # A decision still in flight is never abandoned for a newer request.
        assert runtime.start({"signals": []}) is False
        for _ in range(200):
            if runtime.done:
                break
            await asyncio.sleep(0.01)
        # A finished-but-unconsumed job is not idle: replacing it would throw away
        # a decision the run already paid for.
        assert runtime.done and runtime.idle is False
        assert runtime.start({"signals": []}) is False
        assert runtime.poll() is not None
        # Consumed: without a new start the caller must not re-read the stale
        # first decision forever.
        assert runtime.poll() is None
        assert runtime.idle is True

    asyncio.run(scenario())
