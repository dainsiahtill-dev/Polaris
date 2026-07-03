"""Architecture fence for ContextOS runtime package-root exports."""

from __future__ import annotations

from pathlib import Path

import polaris.kernelone.context.context_os.runtime as runtime_root

BACKEND_ROOT = Path(__file__).resolve().parents[3]
RUNTIME_INIT_SOURCE = (
    BACKEND_ROOT / "polaris" / "kernelone" / "context" / "context_os" / "runtime" / "__init__.py"
)
INTERNAL_PORT_EXPORTS = {
    "MAX_" + "INLINE_CHARS",
    "MAX_" + "STUB_CHARS",
    "_decision_" + "kind",
    "_extract_" + "assistant_followup_action",
    "_extract_" + "hard_constraints",
    "_is_" + "affirmative_response",
    "_is_" + "negative_response",
}


def test_contextos_runtime_package_root_exports_only_engine() -> None:
    """Runtime package root should not expose internal ports helpers."""
    assert runtime_root.__all__ == ["StateFirstContextOS"]
    assert hasattr(runtime_root, "StateFirstContextOS")
    for name in INTERNAL_PORT_EXPORTS:
        assert not hasattr(runtime_root, name), name


def test_contextos_runtime_source_does_not_describe_package_root_as_compat() -> None:
    """Package-root exports are current API, not a compatibility re-export bag."""
    source = RUNTIME_INIT_SOURCE.read_text(encoding="utf-8").lower()
    retired_phrase = "backward " + "compatibility"
    assert retired_phrase not in source
    for name in INTERNAL_PORT_EXPORTS:
        assert f'"{name}"' not in source
