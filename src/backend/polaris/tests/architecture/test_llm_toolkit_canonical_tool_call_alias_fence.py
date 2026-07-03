"""Architecture fence for retired LLM toolkit parser type aliases."""

from __future__ import annotations

from pathlib import Path

from polaris.kernelone.llm.contracts.tool import ToolCall
from polaris.kernelone.llm.toolkit.parsers import CanonicalToolCallParser
from polaris.kernelone.llm.toolkit.parsers.canonical import CanonicalToolCallParser as ParserFromModule

BACKEND_ROOT = Path(__file__).resolve().parents[3]
PARSERS_ROOT = BACKEND_ROOT / "polaris" / "kernelone" / "llm" / "toolkit" / "parsers"
TOOLKIT_ROOT = BACKEND_ROOT / "polaris" / "kernelone" / "llm" / "toolkit"


def test_toolkit_does_not_export_canonical_tool_call_alias() -> None:
    """Parser APIs should expose ToolCall directly, not the retired alias."""
    import polaris.kernelone.llm.toolkit as toolkit
    import polaris.kernelone.llm.toolkit.parsers as parsers
    import polaris.kernelone.llm.toolkit.parsers.canonical as canonical

    assert ParserFromModule is CanonicalToolCallParser
    assert not hasattr(canonical, "CanonicalToolCall")
    assert not hasattr(parsers, "CanonicalToolCall")
    assert not hasattr(toolkit, "CanonicalToolCall")
    assert "CanonicalToolCall" not in parsers.__all__
    assert "CanonicalToolCall" not in toolkit.__all__

    parsed = CanonicalToolCallParser().parse(
        [{"id": "call_1", "function": {"name": "repo_tree", "arguments": "{}"}}],
        format_hint="openai",
    )
    assert parsed
    assert isinstance(parsed[0], ToolCall)


def test_toolkit_sources_do_not_reintroduce_canonical_tool_call_alias() -> None:
    """Block the retired `CanonicalToolCall = ToolCall` parser alias."""
    offenders: list[str] = []
    for path in sorted((PARSERS_ROOT, TOOLKIT_ROOT)):
        for source_file in sorted(path.glob("*.py")):
            source = source_file.read_text(encoding="utf-8")
            if "CanonicalToolCall = ToolCall" in source or '"CanonicalToolCall"' in source:
                offenders.append(source_file.relative_to(BACKEND_ROOT).as_posix())

    assert offenders == []
