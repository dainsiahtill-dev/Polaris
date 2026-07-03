"""Architecture guards for token-service ownership."""

from __future__ import annotations

import inspect

from polaris.domain.services.token_service import TokenService, get_token_service
from polaris.kernelone.llm.engine.token_estimator import TokenEstimator


def test_token_service_does_not_republish_estimator_constants() -> None:
    """Token estimation constants belong to the KernelOne estimator."""
    assert hasattr(TokenEstimator, "CHARS_PER_TOKEN")
    assert not hasattr(TokenService, "CHARS_PER_TOKEN")


def test_token_service_persistence_uses_kfs_logical_paths_only() -> None:
    """TokenService should not accept absolute state-file paths."""
    constructor_parameters = inspect.signature(TokenService).parameters
    accessor_parameters = inspect.signature(get_token_service).parameters
    retired_parameter = "state" + "_file"

    assert retired_parameter not in constructor_parameters
    assert retired_parameter not in accessor_parameters
    assert "kfs_logical_path" in constructor_parameters
    assert "kfs_logical_path" in accessor_parameters
