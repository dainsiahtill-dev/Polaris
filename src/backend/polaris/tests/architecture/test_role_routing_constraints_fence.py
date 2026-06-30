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
