"""Architecture fence for retired AIExecutor execution aliases."""

from __future__ import annotations

from pathlib import Path

from polaris.kernelone.llm.engine.executor import AIExecutor

BACKEND_ROOT = Path(__file__).resolve().parents[3]
EXECUTOR_SOURCE = BACKEND_ROOT / "polaris" / "kernelone" / "llm" / "engine" / "executor.py"
EXECUTOR_TEST_SOURCE = (
    BACKEND_ROOT / "polaris" / "kernelone" / "llm" / "engine" / "tests" / "test_executor.py"
)


def test_ai_executor_exposes_canonical_invoke_methods_only() -> None:
    """AIExecutor callers must use invoke()/invoke_stream(), not retired aliases."""
    assert hasattr(AIExecutor, "invoke")
    assert hasattr(AIExecutor, "invoke_stream")
    assert not hasattr(AIExecutor, "execute")
    assert not hasattr(AIExecutor, "execute_stream")


def test_ai_executor_sources_do_not_reintroduce_execution_aliases() -> None:
    """Block reintroducing compatibility wrappers over invoke()/invoke_stream()."""
    offenders: list[str] = []
    retired_fragments = (
        ".".join(("AIExecutor", "execute is deprecated")),
        ".".join(("AIExecutor", "execute_stream is deprecated")),
        " ".join(("async", "def", "execute(self,", "request:", "AIRequest)")),
        " ".join(("async", "def", "execute_stream(self,", "request:", "AIRequest)")),
        "_".join(("test", "execute", "deprecated")),
        " ".join(("兼容别名", "(deprecated)")),
    )

    for source_file in (EXECUTOR_SOURCE, EXECUTOR_TEST_SOURCE):
        source = source_file.read_text(encoding="utf-8")
        if any(fragment in source for fragment in retired_fragments):
            offenders.append(source_file.relative_to(BACKEND_ROOT).as_posix())

    assert offenders == []
