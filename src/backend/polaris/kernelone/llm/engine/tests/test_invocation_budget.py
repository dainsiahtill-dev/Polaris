"""Deadline inheritance and stop signaling are constraints, never grants."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from polaris.kernelone.llm.engine import invocation_budget as module
from polaris.kernelone.llm.engine.invocation_budget import (
    ProviderInvocationBudget,
    bind_provider_invocation_budget,
    get_provider_invocation_budget,
)


def test_nested_budget_cannot_extend_parent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module.time, "monotonic", lambda: 100.0)
    with bind_provider_invocation_budget(5) as parent:
        with bind_provider_invocation_budget(60) as child:
            assert child.remaining() == 5.0
            assert parent.remaining() == 5.0
        assert parent.stop_reason == ""
        with bind_provider_invocation_budget(2) as child:
            assert child.remaining() == 2.0
        assert parent.remaining() == 5.0
    assert get_provider_invocation_budget() is None


def test_parent_stop_is_irreversible_and_reaches_child() -> None:
    with bind_provider_invocation_budget(10) as parent, bind_provider_invocation_budget(10) as child:
        parent.cancel("caller_cancelled")
        parent.cancel("scope_closed")
        assert child.stop_reason == "caller_cancelled"
        with pytest.raises(TimeoutError, match="caller_cancelled"):
            child.remaining()


def test_worker_backoff_wakes_on_cancellation() -> None:
    entered = threading.Event()
    with bind_provider_invocation_budget(10) as budget, ThreadPoolExecutor(max_workers=1) as pool:

        def wait() -> bool:
            entered.set()
            return budget.wait_for_stop(10)

        future = pool.submit(wait)
        assert entered.wait(1)
        budget.cancel("caller_cancelled")
        assert future.result(timeout=0.5) is True


def test_unsubscribed_callback_is_not_called() -> None:
    called: list[str] = []
    with bind_provider_invocation_budget(10) as budget:
        unsubscribe = budget.on_cancel(lambda: called.append("closed"))
        unsubscribe()
        budget.cancel("caller_cancelled")
    assert called == []


def test_callback_failure_is_visible_and_does_not_skip_other_close(caplog: pytest.LogCaptureFixture) -> None:
    called: list[str] = []
    with bind_provider_invocation_budget(10) as budget:

        def failure() -> None:
            raise RuntimeError("test callback failure")

        budget.on_cancel(failure)
        budget.on_cancel(lambda: called.append("closed"))
        budget.cancel("caller_cancelled")
        assert budget.cancellation_callback_errors == ("RuntimeError",)
    assert called == ["closed"]
    assert "provider cancellation callback failed" in caplog.text


@pytest.mark.parametrize("timeout", [True, 0, -1, float("inf"), float("nan")])
def test_invalid_budget_cannot_be_bound(timeout: float) -> None:
    with pytest.raises(ValueError), bind_provider_invocation_budget(timeout):
        pytest.fail("invalid budget admitted")


def test_expired_budget_rejects_work_before_any_callback() -> None:
    budget = ProviderInvocationBudget(0)
    with pytest.raises(TimeoutError, match="deadline"):
        budget.remaining()
    closed: list[str] = []
    budget.on_cancel(lambda: closed.append("closed"))
    assert closed == ["closed"]
