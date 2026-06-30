"""Architecture fence for role-routing constraint naming."""

from __future__ import annotations

from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
ROUTING_ROOT = BACKEND_ROOT / "polaris/kernelone/role/routing"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_role_routing_uses_constraints_not_compatibility_module() -> None:
    retired_path = ROUTING_ROOT / "compatibility.py"
    canonical_path = ROUTING_ROOT / "constraints.py"
    engine_path = ROUTING_ROOT / "engine.py"

    assert not retired_path.exists(), "Retired role.routing.compatibility module was recreated."
    assert canonical_path.is_file(), "Role routing constraints must live in constraints.py."

    engine_source = _read_text(engine_path)
    assert "polaris.kernelone.role.routing.compatibility" not in engine_source
    assert "polaris.kernelone.role.routing.constraints" in engine_source
    assert "RoutingConstraintEngine" in engine_source


def test_role_recipes_use_aliases_not_legacy_adapter() -> None:
    retired_path = ROUTING_ROOT / "adapters" / "legacy_recipe.py"
    loader_path = BACKEND_ROOT / "polaris/kernelone/role/loaders.py"
    composer_path = BACKEND_ROOT / "polaris/kernelone/role/composer.py"
    schema_path = BACKEND_ROOT / "polaris/assets/roles/schema/recipe.schema.yaml"

    assert not retired_path.exists(), "Retired role.routing.adapters.legacy_recipe module was recreated."

    loader_source = _read_text(loader_path)
    composer_source = _read_text(composer_path)
    schema_source = _read_text(schema_path)

    assert "load_by_legacy_id" not in loader_source
    assert "load_by_legacy_id" not in composer_source
    assert "legacy_id" not in schema_source
    assert "load_by_alias" in loader_source
    assert "load_by_alias" in composer_source
    assert "aliases:" in schema_source
