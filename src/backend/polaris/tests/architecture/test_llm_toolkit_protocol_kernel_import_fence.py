"""Architecture guard for protocol package ownership."""

from __future__ import annotations

from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
PRODUCTION_PROTOCOL_CONSUMERS = (
    BACKEND_ROOT / "polaris" / "kernelone" / "llm" / "toolkit" / "__init__.py",
    BACKEND_ROOT / "polaris" / "kernelone" / "llm" / "toolkit" / "audit.py",
    BACKEND_ROOT / "polaris" / "kernelone" / "llm" / "toolkit" / "streaming_patch_buffer.py",
    BACKEND_ROOT
    / "polaris"
    / "kernelone"
    / "llm"
    / "toolkit"
    / "tool_normalization"
    / "normalizers"
    / "_shared.py",
    BACKEND_ROOT
    / "polaris"
    / "cells"
    / "director"
    / "tasking"
    / "internal"
    / "file_apply_service.py",
)


def test_production_protocol_consumers_do_not_import_protocol_kernel() -> None:
    """Production consumers must import the canonical protocol package."""
    for path in PRODUCTION_PROTOCOL_CONSUMERS:
        source = path.read_text(encoding="utf-8")
        assert "protocol_kernel" not in source, str(path)
