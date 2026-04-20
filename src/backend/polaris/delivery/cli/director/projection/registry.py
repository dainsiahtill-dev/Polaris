"""RendererRegistry — registers and dispatches ProjectionRenderer instances."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polaris.delivery.cli.director.projection.projection_layer import (
        ProjectionRenderer,
        WidgetUpdate,
    )

logger = logging.getLogger(__name__)


class RendererRegistry:
    """Map from event kind (str) to ProjectionRenderer.

    Allows the ProjectionLayer to dispatch events to the appropriate renderer
    without hard-coding the renderer list.
    """

    def __init__(self) -> None:
        self._renderers: dict[str, ProjectionRenderer] = {}

    def register(self, kind: str, renderer: ProjectionRenderer) -> None:
        """Register a renderer for a given event kind."""
        self._renderers[kind] = renderer
        logger.debug("Registered renderer %r for kind %r", renderer.__class__.__name__, kind)

    def get(self, kind: str) -> ProjectionRenderer | None:
        """Return the renderer for this kind, or None if not registered."""
        return self._renderers.get(kind)

    def dispatch(self, event: dict[str, Any]) -> list[WidgetUpdate]:
        """Dispatch an event to all matching renderers.

        Returns a list of WidgetUpdate objects (may be empty).
        """

        kind = event.get("type", "")
        renderer = self.get(kind)
        if renderer is None:
            return []

        update = renderer.render(event)  # type: ignore[union-attr]
        if update is None:
            return []
        return [update]
