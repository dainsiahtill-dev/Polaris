"""Shared pytest fixtures for the OrchestrationStageExecutor characterization suite.

Split from the historical monolithic characterization test file. The autouse
FactStream-bootstrap fixture below was module-scoped in the monolith; it now
lives here so every per-domain characterization module inherits it (preserving
the original behavior where each ``tmp_path``-using test gets a provisioned
FactStream workspace before it runs).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)


@pytest.fixture(autouse=True)
def _bootstrap_real_fact_stream_workspace(request: pytest.FixtureRequest) -> None:
    """Provision FactStream before characterization tests use a real workspace."""

    if "tmp_path" not in request.fixturenames:
        return
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(Path(request.getfixturevalue("tmp_path")).resolve()),
            streams=fact_stream_bootstrap_streams(),
            maintenance_reason="factory_stage_executor_characterization_test_bootstrap",
        )
    )
