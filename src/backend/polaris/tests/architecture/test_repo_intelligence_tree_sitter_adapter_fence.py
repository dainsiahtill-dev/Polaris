"""Architecture fence for repo-intelligence tree-sitter ABI handling."""

from __future__ import annotations

from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
TAGS_SOURCE = BACKEND_ROOT / "polaris" / "kernelone" / "context" / "repo_intelligence" / "tags.py"


def test_tree_sitter_abi_handling_is_not_labeled_compat_shim() -> None:
    """The dual binding support is current ABI adaptation, not a legacy shim."""
    source = TAGS_SOURCE.read_text(encoding="utf-8").lower()
    retired_phrase = "compatibility " + "shim"
    assert retired_phrase not in source
    assert "abi adapter" in source
