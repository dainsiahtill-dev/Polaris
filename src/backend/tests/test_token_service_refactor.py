"""Tests for token_service lazy-import refactor.

Verifies:
- Module loads without KernelOne imports at the top level
- TYPE_CHECKING import avoids blocking
- Service works with and without KFS adapter
- estimate_tokens and budget functions work correctly
"""

from __future__ import annotations

from unittest.mock import patch

from polaris.domain.services.token_service import (
    BudgetStatus,
    TokenEstimate,
    TokenService,
    estimate_tokens,
    reset_token_service,
)


class TestTokenServiceLazyImport:
    """TokenService loads without blocking KernelOne imports."""

    def test_module_imports_without_kernelone_error(self):
        # If KernelOne adapter is not set, the module should still be importable
        # This test verifies the refactor: no RuntimeError at import time
        # The service will simply not persist without a bootstrapped adapter.
        reset_token_service()
        svc = TokenService()  # No KFS, no state_file
        assert svc.budget_limit is None
        assert svc._used_tokens == 0

    def test_estimate_tokens_no_kernelone(self):
        reset_token_service()
        svc = TokenService(budget_limit=1000)
        assert svc.estimate_tokens("hello world") > 0
        # 11 chars / 4 = 2 tokens minimum
        assert svc.estimate_tokens("hello world") == 2

    def test_estimate_message_tokens(self):
        reset_token_service()
        svc = TokenService()
        est = svc.estimate_message_tokens("test content", role="user")
        assert isinstance(est, TokenEstimate)
        assert est.estimated is True
        # base(4) + role_overhead(3) + content(1) = 8
        assert est.prompt_tokens >= 7

    def test_budget_check_under_limit(self):
        reset_token_service()
        svc = TokenService(budget_limit=1000)
        allowed, reason = svc.check_budget(500)
        assert allowed is True
        assert "OK:" in reason

    def test_budget_check_exceeded(self):
        reset_token_service()
        svc = TokenService(budget_limit=100)
        svc._used_tokens = 95
        allowed, reason = svc.check_budget(10)
        assert allowed is False
        assert "Budget exceeded" in reason

    def test_budget_check_warning_approaching_limit(self):
        reset_token_service()
        svc = TokenService(budget_limit=1000)
        svc._used_tokens = 950
        allowed, reason = svc.check_budget(10)
        assert allowed is True
        assert "WARNING" in reason

    def test_record_usage_and_budget_status(self):
        reset_token_service()
        svc = TokenService(budget_limit=500)
        svc.record_usage(100)
        status = svc.get_budget_status()
        assert isinstance(status, BudgetStatus)
        assert status.used_tokens == 100
        assert status.budget_limit == 500
        assert status.remaining_tokens == 400
        assert status.percent_used == 20.0

    def test_record_usage_no_limit(self):
        reset_token_service()
        svc = TokenService()
        svc.record_usage(1000)
        status = svc.get_budget_status()
        assert status.used_tokens == 1000
        assert status.remaining_tokens is None
        assert status.percent_used == 0.0

    def test_should_truncate_output(self):
        reset_token_service()
        svc = TokenService()
        big_output = "x" * 60 * 1024  # 60KB > 50KB limit
        should, _lines = svc.should_truncate_output(big_output)
        assert should is True

        small_output = "hello"
        should, _ = svc.should_truncate_output(small_output)
        assert should is False

    def test_truncate_output_with_notice(self):
        reset_token_service()
        svc = TokenService()
        big = "line1\n" * 10000  # ~60KB, exceeds MAX_OUTPUT_SIZE (51,200)
        truncated = svc.truncate_output(big)
        assert len(truncated) <= svc.MAX_OUTPUT_SIZE
        assert "[..." in truncated

    def test_create_preview(self):
        reset_token_service()
        svc = TokenService()
        big = "x" * 2000
        preview = svc.create_preview(big, preview_size=100)
        assert len(preview) <= 120  # small buffer over preview_size
        assert "[..." in preview

    def test_global_estimate_tokens_function(self):
        reset_token_service()
        result = estimate_tokens("test")
        assert result >= 1


class TestTokenServiceWithKFSDisabled:
    """Service gracefully handles missing KFS adapter (no persistence, no crash)."""

    def test_init_with_no_adapter_no_state_file(self):
        reset_token_service()
        # KFS adapter not set → should not raise, just skip persistence.
        # Mock raises RuntimeError to simulate un-bootstrapped adapter.
        with patch(
            "polaris.kernelone.fs.registry.get_default_adapter",
            side_effect=RuntimeError(
                "Default KernelFileSystemAdapter not set. It must be injected by the bootstrap layer."
            ),
        ):
            svc = TokenService(
                budget_limit=100,
                kfs_logical_path="runtime/state/test.json",
            )
        assert svc._fs is None
        # Persist should be a no-op
        svc._persist_state()  # Should not raise
        assert svc._used_tokens == 0
