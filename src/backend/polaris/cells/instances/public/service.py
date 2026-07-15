"""Public service facade for Polaris local instances."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, TypedDict

from polaris.cells.instances.internal.service import (
    InstanceRegistry,
    InstanceRegistryError,
    InstanceSupervisor,
    RegistryCorruptionError,
    RegistryReadError,
    maybe_start_instance_watchdog,
    normalize_instance_id,
)


class DegradedInstanceListProjection(TypedDict):
    degraded: Literal[True]
    items: list[dict[str, Any]]
    error: dict[str, Any]


InstanceListResult = list[dict[str, Any]] | DegradedInstanceListProjection


class PublicInstanceSupervisor:
    """Delivery-facing facade with an explicit degraded read projection."""

    def __init__(self, registry: InstanceRegistry) -> None:
        self._delegate = InstanceSupervisor(registry)

    def list_instances(self) -> InstanceListResult:
        try:
            return self._delegate.list_instances()
        except InstanceRegistryError as exc:
            return {
                "degraded": True,
                "items": [],
                "error": exc.to_dict(),
            }

    def start_instance(self, request: dict[str, Any]) -> dict[str, Any]:
        return self._delegate.start_instance(request)

    def stop_instance(self, instance_id: str) -> dict[str, Any]:
        return self._delegate.stop_instance(instance_id)

    def restart_instance(self, instance_id: str) -> dict[str, Any]:
        return self._delegate.restart_instance(instance_id)

    def delete_instance(self, instance_id: str) -> bool:
        return self._delegate.delete_instance(instance_id)

    def health(self, instance_id: str) -> dict[str, Any]:
        return self._delegate.health(instance_id)

    def get_logs(self, instance_id: str, stream: str, lines: int = 200) -> str:
        return self._delegate.get_logs(instance_id, stream, lines)


def get_instance_supervisor(home: Path | None = None) -> PublicInstanceSupervisor:
    return PublicInstanceSupervisor(InstanceRegistry(home))


def list_instances(home: Path | None = None) -> InstanceListResult:
    return get_instance_supervisor(home).list_instances()


__all__ = [
    "DegradedInstanceListProjection",
    "InstanceListResult",
    "InstanceRegistry",
    "InstanceRegistryError",
    "InstanceSupervisor",
    "PublicInstanceSupervisor",
    "RegistryCorruptionError",
    "RegistryReadError",
    "get_instance_supervisor",
    "list_instances",
    "maybe_start_instance_watchdog",
    "normalize_instance_id",
]
