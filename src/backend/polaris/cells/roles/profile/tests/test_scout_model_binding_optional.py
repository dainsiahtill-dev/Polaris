"""Scout is an OPTIONAL, configurable LLM model-binding role.

The LLM visual config editor now exposes Scout (探子) as an assignable role on
the frontend. On the backend this must hold:

* a configured ``roles.scout`` binding in ``llm_config.json`` IS consumed — it
  enriches the scout profile's provider_id/model (so scout escalation uses the
  user-chosen model); and
* the binding stays OPTIONAL — scout is NOT a CORE role, so a missing binding
  must NOT raise (whereas a missing CORE-role binding still does).

This locks the contract that lets Scout live in the editor without forcing every
deployment to assign it a model.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.roles.profile.internal import registry as registry_module
from polaris.cells.roles.profile.public.service import RoleProfileRegistry

# This file: cells/roles/profile/tests/ -> parents[1] = cells/roles/profile.
_CORE_ROLES_YAML: Path = Path(__file__).resolve().parents[1] / "config" / "roles" / "core_roles.yaml"

_GET_ROLE_MODEL = "polaris.kernelone.llm.runtime_config.get_role_model"


def _fresh_registry() -> RoleProfileRegistry:
    assert _CORE_ROLES_YAML.exists(), f"SSOT config missing: {_CORE_ROLES_YAML}"
    reg = RoleProfileRegistry()
    reg.load_from_yaml(_CORE_ROLES_YAML)
    assert reg.get_profile("scout") is not None, "scout role must be present in core_roles.yaml"
    return reg


def test_scout_binding_is_consumed_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """A configured scout binding enriches the scout profile's provider/model."""
    reg = _fresh_registry()
    # All roles resolve to a binding so CORE roles don't raise; assert scout enriched.
    monkeypatch.setattr(_GET_ROLE_MODEL, lambda role_id: ("openai", "gpt-5"))

    registry_module._ensure_role_model_bindings(reg)

    scout = reg.get_profile("scout")
    assert scout is not None
    assert scout.provider_id == "openai"
    assert scout.model == "gpt-5"


def test_scout_binding_is_optional_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing scout binding must NOT raise (scout is not a CORE role)."""
    reg = _fresh_registry()

    def fake_get_role_model(role_id: str) -> tuple[str, str]:
        # CORE roles get a binding; scout deliberately gets none.
        return ("", "") if role_id == "scout" else ("openai", "gpt-5")

    monkeypatch.setattr(_GET_ROLE_MODEL, fake_get_role_model)

    # Must complete without raising even though scout has no binding.
    registry_module._ensure_role_model_bindings(reg)

    scout = reg.get_profile("scout")
    assert scout is not None
    assert not scout.provider_id, "unbound scout must stay unbound, not fabricated"


def test_core_role_missing_binding_still_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Contrast: a missing CORE-role binding still fails closed (scout differs)."""
    reg = _fresh_registry()
    monkeypatch.setattr(_GET_ROLE_MODEL, lambda role_id: ("", ""))

    with pytest.raises(ValueError):
        registry_module._ensure_role_model_bindings(reg)
