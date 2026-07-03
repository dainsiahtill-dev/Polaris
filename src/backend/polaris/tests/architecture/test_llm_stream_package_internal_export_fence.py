"""Architecture fence for retired LLM stream package-root internal exports."""

from __future__ import annotations

from pathlib import Path

import polaris.kernelone.llm.engine.stream as stream_root

BACKEND_ROOT = Path(__file__).resolve().parents[3]
STREAM_INIT_SOURCE = BACKEND_ROOT / "polaris" / "kernelone" / "llm" / "engine" / "stream" / "__init__.py"
RETIRED_INTERNAL_EXPORTS = {
    "_StreamResultTracker",
    "_ToolCallAccumulator",
    "_debug_compact_payload",
    "_debug_tool_arguments",
    "_normalize_arguments",
    "_provider_supports_structured_stream",
    "_safe_text_length",
    "_tool_accumulator_key",
}


def test_stream_package_root_does_not_export_internal_tool_helpers() -> None:
    """Internal stream helpers must be imported from their owning submodules."""
    for name in RETIRED_INTERNAL_EXPORTS:
        assert not hasattr(stream_root, name), name
        assert name not in stream_root.__all__, name


def test_stream_package_root_source_does_not_reintroduce_internal_exports() -> None:
    """Block package-root re-export of internal stream helper symbols."""
    source = STREAM_INIT_SOURCE.read_text(encoding="utf-8")
    assert "backward compatibility" not in source.lower()
    for name in RETIRED_INTERNAL_EXPORTS:
        assert name not in source
