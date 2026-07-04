"""Retired raw task-board compatibility module.

Production consumers must import ``TaskRuntimeService`` from
``polaris.cells.runtime.task_runtime.public.service`` and use task-row/session
APIs.  Raw task-board types are private to the owner cell and its direct tests.
This module intentionally exports only the runtime service as a migration aid;
it no longer exposes raw task entities, tools, or factories.
"""

from __future__ import annotations

from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService

__all__ = ["TaskRuntimeService"]
