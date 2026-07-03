"""Architecture fence for retired ResilienceManager fallback wrappers."""

from __future__ import annotations

from pathlib import Path

from polaris.kernelone.llm.engine.resilience import ResilienceManager

BACKEND_ROOT = Path(__file__).resolve().parents[3]
RESILIENCE_SOURCE = BACKEND_ROOT / "polaris" / "kernelone" / "llm" / "engine" / "resilience.py"
RETIRED_DIALOGUE_FALLBACK = "_".join(("generate", "dialogue", "fallback"))
RETIRED_INTERVIEW_FALLBACK = "_".join(("generate", "interview", "fallback"))
RETIRED_COMPAT_PHRASE = "; ".join(("Retained for backward compatibility", "returns generic fallback"))


def test_resilience_manager_exposes_single_fallback_content_builder() -> None:
    """Domain-specific fallback wrappers are retired; use the generic builder."""
    assert hasattr(ResilienceManager, "_generate_fallback_content")
    assert not hasattr(ResilienceManager, f"_{RETIRED_DIALOGUE_FALLBACK}")
    assert not hasattr(ResilienceManager, f"_{RETIRED_INTERVIEW_FALLBACK}")


def test_resilience_source_does_not_reintroduce_fallback_wrappers() -> None:
    """Block old task-type-specific fallback wrapper methods."""
    source = RESILIENCE_SOURCE.read_text(encoding="utf-8")
    assert f"_{RETIRED_DIALOGUE_FALLBACK}" not in source
    assert f"_{RETIRED_INTERVIEW_FALLBACK}" not in source
    assert RETIRED_COMPAT_PHRASE not in source
