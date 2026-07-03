"""Architecture fence for active ContextOS contract terminology."""

from __future__ import annotations

from pathlib import Path

from polaris.kernelone.context.context_os.contract_tests import BehaviorParityTest

BACKEND_ROOT = Path(__file__).resolve().parents[3]
CONTEXT_OS_ROOT = BACKEND_ROOT / "polaris" / "kernelone" / "context" / "context_os"
ACTIVE_CONTEXT_OS_SOURCES = (
    CONTEXT_OS_ROOT / "contract_tests.py",
    CONTEXT_OS_ROOT / "snapshot_summary.py",
    CONTEXT_OS_ROOT / "pipeline" / "stages.py",
)


def test_behavior_parity_contract_helper_has_current_name() -> None:
    """ContextOS contract-test helpers use current baseline/candidate naming."""
    assert BehaviorParityTest.__name__ == "BehaviorParityTest"
    assert hasattr(BehaviorParityTest, "get_implementations")


def test_active_contextos_contract_language_avoids_retired_terms() -> None:
    """Active ContextOS surfaces should not describe current behavior as old architecture."""
    forbidden_terms = (
        "backward " + "compatibility",
        "legacy" + "_class",
        "new" + "_class",
        "deprecated " + "model",
        "convert " + "legacy dataclass",
    )

    for source_path in ACTIVE_CONTEXT_OS_SOURCES:
        source = source_path.read_text(encoding="utf-8").lower()
        for term in forbidden_terms:
            assert term not in source, f"{term!r} remains in {source_path}"

