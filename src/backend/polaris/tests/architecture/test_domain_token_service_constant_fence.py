"""Architecture guard for token-estimation ownership."""

from __future__ import annotations

from polaris.domain.services.token_service import TokenService
from polaris.kernelone.llm.engine.token_estimator import TokenEstimator


def test_token_service_does_not_republish_estimator_constants() -> None:
    """Token estimation constants belong to the KernelOne estimator."""
    assert hasattr(TokenEstimator, "CHARS_PER_TOKEN")
    assert not hasattr(TokenService, "CHARS_PER_TOKEN")
