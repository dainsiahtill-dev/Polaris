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
        "capabilities": ["browser_observe"],
        "mcp_hints": [],
        "guidance": "用浏览器验收",
        "reason": "检测到 package.json",
    }
    signals = {"signals": ["web:package.json"]}
    validated = _normalize_decision(payload, signals)
    assert validated is not None and validated["project_type"] == "web"
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
