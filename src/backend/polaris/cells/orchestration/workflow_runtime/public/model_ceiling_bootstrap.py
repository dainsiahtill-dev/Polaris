"""Bootstrap-only binding surface for model-ceiling owner evidence."""

from __future__ import annotations

from polaris.cells.orchestration.workflow_runtime.internal.model_ceiling_authority import (
    bind_model_ceiling_owner_observation_port as _bind_model_ceiling_owner_observation_port,
    clear_model_ceiling_owner_observation_port as _clear_model_ceiling_owner_observation_port,
)
from polaris.cells.orchestration.workflow_runtime.public.model_ceiling import ModelCeilingOwnerObservationPortV1


def bind_model_ceiling_owner_observation_port(port: ModelCeilingOwnerObservationPortV1) -> None:
    """Bind the process-composition adapter; bootstrap callers only."""

    _bind_model_ceiling_owner_observation_port(port)


def clear_model_ceiling_owner_observation_port(port: ModelCeilingOwnerObservationPortV1) -> None:
    """Release the exact process-composition binding."""

    _clear_model_ceiling_owner_observation_port(port)


__all__ = [
    "bind_model_ceiling_owner_observation_port",
    "clear_model_ceiling_owner_observation_port",
]
