"""Architecture fence for meta-prompting role normalization ownership."""

from __future__ import annotations

from pathlib import Path

from polaris.cells.adapters.kernelone import RoleProviderAdapter
from polaris.kernelone.prompts import meta_prompting

BACKEND_ROOT = Path(__file__).resolve().parents[3]
META_PROMPTING_SOURCE = BACKEND_ROOT / "polaris" / "kernelone" / "prompts" / "meta_prompting.py"


def test_meta_prompting_uses_role_provider_adapter_boundary() -> None:
    """KernelOne prompt helpers must use the Cells adapter, not duplicate role rules."""
    bound_owner = getattr(meta_prompting.normalize_role_alias, "__self__", None)
    assert isinstance(bound_owner, RoleProviderAdapter)
    assert meta_prompting.normalize_role_alias("docs") == "architect"
    assert meta_prompting.normalize_role_alias("auditor") == "qa"


def test_meta_prompting_source_does_not_label_current_adapter_as_compat() -> None:
    """The ACGA role adapter is current architecture, not a compatibility shim."""
    source = META_PROMPTING_SOURCE.read_text(encoding="utf-8").lower()
    retired_phrase = "backward " + "compatibility"
    assert retired_phrase not in source
    assert "re-exported from role_alias" not in source
