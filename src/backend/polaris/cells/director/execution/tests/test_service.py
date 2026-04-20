"""Tests for service module."""

from __future__ import annotations

from unittest.mock import Mock

from polaris.cells.director.execution.service import (
    DirectorConfig,
    DirectorService,
    DirectorState,
)


class TestDirectorService:
    def test_init_basic(self):
        config = DirectorConfig(workspace="/workspace")
        service = DirectorService(config=config)
        assert service.config.workspace == "/workspace"
        assert service.state == DirectorState.IDLE

    def test_init_with_security(self):
        config = DirectorConfig(workspace="/workspace")
        mock_security = Mock()
        service = DirectorService(config=config, security=mock_security)
        assert service.security is mock_security
