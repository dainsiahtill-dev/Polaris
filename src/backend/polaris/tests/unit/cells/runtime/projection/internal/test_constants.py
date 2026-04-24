"""Tests for polaris.cells.runtime.projection.internal.constants."""

from __future__ import annotations

import os

from polaris.cells.runtime.projection.internal.constants import DEFAULT_WORKSPACE


class TestProjectionConstants:
    def test_default_workspace_is_cwd(self) -> None:
        assert os.getcwd() == DEFAULT_WORKSPACE
