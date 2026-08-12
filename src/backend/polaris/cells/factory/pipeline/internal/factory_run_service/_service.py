"""FactoryRunService class composition.

Private implementation module of the factory_run_service package.
"""

from __future__ import annotations

from ._service_core import _FactoryRunServiceCore
from ._service_lifecycle import _FactoryRunServiceLifecycleMixin
from ._service_physical import _FactoryRunServicePhysicalMixin
from ._service_stage import _FactoryRunServiceStageMixin


class FactoryRunService(
    _FactoryRunServiceStageMixin,
    _FactoryRunServiceLifecycleMixin,
    _FactoryRunServicePhysicalMixin,
    _FactoryRunServiceCore,
):
    """Formal service for Factory runs with persistence and recovery."""
