"""Workflow client re-exports for workflow_activity Cell.

Re-exports the workflow/activity API singletons from embedded_api so that
internal modules can import from here instead of the embedded_api directly,
establishing workflow_activity as the canonical import boundary.

Migrated from:
  polaris/cells/orchestration/workflow_runtime/internal/workflow_client.py
"""

from __future__ import annotations

from polaris.cells.orchestration.workflow_activity.internal.embedded_api import (
    ActivityRegistry,
    EmbeddedActivityAPI,
    EmbeddedWorkflowAPI,
    WorkflowContext,
    WorkflowRegistry,
    clear_workflow_context,
    embedded_activity,
    embedded_workflow,
    get_activity_api,
    get_activity_registry,
    get_workflow_api,
    get_workflow_context,
    get_workflow_registry,
    set_workflow_context,
)

__all__ = [
    "ActivityRegistry",
    "EmbeddedActivityAPI",
    "EmbeddedWorkflowAPI",
    "WorkflowContext",
    "WorkflowRegistry",
    "clear_workflow_context",
    "embedded_activity",
    "embedded_workflow",
    "get_activity_api",
    "get_activity_registry",
    "get_workflow_api",
    "get_workflow_context",
    "get_workflow_registry",
    "set_workflow_context",
]
