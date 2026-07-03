"""Architecture fence for the retired LLM exceptions TimeoutError export."""

from __future__ import annotations

from pathlib import Path

import polaris.kernelone.llm.exceptions as llm_exceptions

BACKEND_ROOT = Path(__file__).resolve().parents[3]
LLM_EXCEPTIONS_SOURCE = BACKEND_ROOT / "polaris" / "kernelone" / "llm" / "exceptions.py"
RETIRED_TIMEOUT_ALIAS = "".join(("Timeout", "Error"))
RETIRED_COMPAT_TITLE = " ".join(("Backward", "Compatibility", "Aliases"))


def test_llm_exceptions_timeout_alias_is_not_exported() -> None:
    """LLM exceptions expose LLMTimeoutError rather than a TimeoutError alias."""
    assert hasattr(llm_exceptions, "LLMTimeoutError")
    assert not hasattr(llm_exceptions, RETIRED_TIMEOUT_ALIAS)
    assert RETIRED_TIMEOUT_ALIAS not in llm_exceptions.__all__


def test_llm_exceptions_source_does_not_reintroduce_timeout_alias() -> None:
    """Block the retired LLM-local TimeoutError export and compatibility title."""
    source = LLM_EXCEPTIONS_SOURCE.read_text(encoding="utf-8")
    assert f'"{RETIRED_TIMEOUT_ALIAS}"' not in source
    assert RETIRED_COMPAT_TITLE not in source
