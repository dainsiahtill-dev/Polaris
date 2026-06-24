"""Public service facade for Polaris local instances."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from polaris.cells.instances.internal.service import InstanceRegistry, InstanceSupervisor


def get_instance_supervisor(home: Path | None = None) -> InstanceSupervisor:
    return InstanceSupervisor(InstanceRegistry(home))


def list_instances(home: Path | None = None) -> list[dict[str, Any]]:
    return get_instance_supervisor(home).list_instances()


__all__ = ["InstanceRegistry", "InstanceSupervisor", "get_instance_supervisor", "list_instances"]
