"""Fences for current LLM toolkit adapter evidence labels."""

from __future__ import annotations

from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
TOOLS_ROOT = BACKEND_ROOT / "polaris" / "infrastructure" / "llm" / "tools"


def test_toolkit_adapters_do_not_emit_legacy_evidence_labels() -> None:
    """Adapter evidence labels must describe the current KernelOne toolkit path."""
    offenders: list[str] = []
    for path in sorted(TOOLS_ROOT.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "legacy_parser" in source or "core.llm_toolkit" in source:
            offenders.append(path.relative_to(BACKEND_ROOT).as_posix())

    assert offenders == []
