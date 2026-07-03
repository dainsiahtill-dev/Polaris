"""Architecture fence for retired LLM stream result aliases."""

from __future__ import annotations

from pathlib import Path

import polaris.kernelone.llm.engine.stream as stream_package
import polaris.kernelone.llm.engine.stream.config as stream_config
from polaris.kernelone.llm.engine.stream.config import LLMStreamResult

BACKEND_ROOT = Path(__file__).resolve().parents[3]
STREAM_CONFIG = BACKEND_ROOT / "polaris" / "kernelone" / "llm" / "engine" / "stream" / "config.py"
STREAM_PACKAGE = BACKEND_ROOT / "polaris" / "kernelone" / "llm" / "engine" / "stream" / "__init__.py"
STREAM_EXECUTOR_FACADE = BACKEND_ROOT / "polaris" / "kernelone" / "llm" / "engine" / "stream_executor.py"


def test_stream_result_alias_is_retired() -> None:
    """LLM streaming should expose only the unambiguous LLMStreamResult name."""
    assert stream_config.LLMStreamResult is LLMStreamResult
    assert stream_package.LLMStreamResult is LLMStreamResult

    assert not hasattr(stream_config, "StreamResult")
    assert "StreamResult" not in stream_package.__all__


def test_stream_executor_facade_is_retired() -> None:
    """The package-root stream module is the canonical streaming import surface."""
    assert not STREAM_EXECUTOR_FACADE.exists()


def test_stream_sources_do_not_reintroduce_stream_result_alias() -> None:
    """Source-level fence blocks the ambiguous LLM StreamResult alias."""
    config_source = STREAM_CONFIG.read_text(encoding="utf-8")
    assert "StreamResult = LLMStreamResult" not in config_source

    source = STREAM_PACKAGE.read_text(encoding="utf-8")
    lines = {line.strip() for line in source.splitlines()}
    assert "StreamResult," not in lines
    assert '"StreamResult",' not in lines
