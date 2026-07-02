from __future__ import annotations

import json
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[3]
DESCRIPTOR_ROOT = BACKEND_ROOT / "polaris" / "cells"

DISALLOWED_SOURCE_PARTS = frozenset(
    {
        ".venv",
        "__pycache__",
        "fixtures",
        "generated",
        "testing",
        "tests",
        "venv",
    }
)


def _load_descriptor_pack(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict), f"{path} must contain a JSON object"
    return payload


def _is_main_descriptor_pack(path: Path) -> bool:
    relative_parts = path.relative_to(BACKEND_ROOT).parts
    return "fixtures" not in relative_parts


def test_descriptor_packs_only_describe_runtime_sources() -> None:
    offenders: list[str] = []
    descriptor_paths = sorted(
        path for path in DESCRIPTOR_ROOT.glob("**/generated/descriptor.pack.json") if _is_main_descriptor_pack(path)
    )
    assert descriptor_paths, "Expected at least one Cell descriptor pack"

    for descriptor_path in descriptor_paths:
        payload = _load_descriptor_pack(descriptor_path)
        capabilities = payload.get("capabilities", [])
        assert isinstance(capabilities, list), f"{descriptor_path}.capabilities must be a list"
        for capability in capabilities:
            if not isinstance(capability, dict):
                offenders.append(f"{descriptor_path}: capability must be an object")
                continue
            defined_in = str(capability.get("defined_in") or "").strip()
            if not defined_in:
                continue
            source_parts = set(Path(defined_in).parts)
            blocked_parts = sorted(source_parts & DISALLOWED_SOURCE_PARTS)
            if blocked_parts:
                offenders.append(f"{descriptor_path}: {defined_in} contains {', '.join(blocked_parts)}")

    assert not offenders, (
        "Cell descriptor packs are runtime capability context. They must not "
        f"publish tests, fixtures, generated code, virtualenvs, or caches: {offenders}"
    )
