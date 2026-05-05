"""conftest for polaris.cells test package."""

from __future__ import annotations

import polaris.cells.roles.runtime.public as runtime_public
from polaris.cells.roles.runtime.internal.session_orchestrator import RoleSessionOrchestrator

runtime_public.RoleSessionOrchestrator = RoleSessionOrchestrator
