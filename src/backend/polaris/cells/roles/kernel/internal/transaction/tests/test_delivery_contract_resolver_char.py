"""Characterization tests for ``resolve_turn_delivery_contract``.

UTF-8 编码验证: 本文所有文本使用 UTF-8。

Pins the CURRENT behavior of the Phase-1b delivery-contract resolution
extracted from ``TurnTransactionController._execute_turn`` (REMAINING_06
blueprint step 2), covering the branch-matrix gaps flagged by the blueprint:

* ``KERNELONE_DELIVERY_MODE_TRACE`` trace logging (no behavior change, no raise)
* no-user-turn anomaly flag (DELIVERY_CONTRACT_NO_USER_TURN)
* downgrade-no-write-tools (DELIVERY_CONTRACT_DOWNGRADED_NO_WRITE_TOOLS)
* multi-turn materialize inheritance (DELIVERY_CONTRACT_INHERITED)
* explicit-marker override callable penetration

The two role-policy helpers are injected as callables (mirroring how the facade
passes its monkeypatchable bound methods) so this test does not depend on the
hybrid SLM cascade.
"""

from __future__ import annotations

from typing import Any

import pytest

from polaris.cells.roles.kernel.internal.transaction.delivery_contract import (
    DeliveryContract,
    DeliveryMode,
)
from polaris.cells.roles.kernel.internal.transaction.delivery_contract_resolver import (
    resolve_turn_delivery_contract,
)
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger


def _analyze_only() -> DeliveryContract:
    return DeliveryContract(mode=DeliveryMode.ANALYZE_ONLY, requires_mutation=False)


def _materialize() -> DeliveryContract:
    return DeliveryContract(mode=DeliveryMode.MATERIALIZE_CHANGES, requires_mutation=True)


async def _resolve_analyze(_msg: str) -> DeliveryContract:
    return _analyze_only()


async def _resolve_materialize(_msg: str) -> DeliveryContract:
    return _materialize()


def _no_inherit(_context: list[dict[str, Any]], _latest: str) -> DeliveryContract | None:
    return None


@pytest.mark.asyncio
async def test_no_user_turn_records_anomaly_and_keeps_analyze() -> None:
    ledger = TurnLedger(turn_id="t_no_user")
    # Context present but no user message => latest_user_request == "".
    context: list[dict[str, Any]] = [{"role": "assistant", "content": "hi"}]

    contract = await resolve_turn_delivery_contract(
        turn_id="t_no_user",
        context=context,
        tool_definitions=[{"type": "function", "function": {"name": "read_file"}}],
        ledger=ledger,
        resolve_delivery_mode_hybrid=_resolve_analyze,
        inherit_materialize_from_history=_no_inherit,
    )

    assert contract.mode == DeliveryMode.ANALYZE_ONLY
    assert any(flag.get("type") == "DELIVERY_CONTRACT_NO_USER_TURN" for flag in ledger.anomaly_flags)


@pytest.mark.asyncio
async def test_trace_env_does_not_change_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNELONE_DELIVERY_MODE_TRACE", "1")
    ledger = TurnLedger(turn_id="t_trace")
    context: list[dict[str, Any]] = [{"role": "user", "content": "总结 README"}]

    contract = await resolve_turn_delivery_contract(
        turn_id="t_trace",
        context=context,
        tool_definitions=[{"type": "function", "function": {"name": "read_file"}}],
        ledger=ledger,
        resolve_delivery_mode_hybrid=_resolve_analyze,
        inherit_materialize_from_history=_no_inherit,
    )

    # Trace branch is pure logging — contract is still ANALYZE_ONLY.
    assert contract.mode == DeliveryMode.ANALYZE_ONLY


@pytest.mark.asyncio
async def test_downgrade_when_no_write_tools() -> None:
    ledger = TurnLedger(turn_id="t_downgrade")
    context: list[dict[str, Any]] = [{"role": "user", "content": "实现登录功能"}]

    contract = await resolve_turn_delivery_contract(
        turn_id="t_downgrade",
        context=context,
        tool_definitions=[{"type": "function", "function": {"name": "read_file"}}],
        ledger=ledger,
        resolve_delivery_mode_hybrid=_resolve_materialize,
        inherit_materialize_from_history=_no_inherit,
    )

    assert contract.mode == DeliveryMode.PROPOSE_PATCH
    assert contract.requires_mutation is False
    assert any(flag.get("type") == "DELIVERY_CONTRACT_DOWNGRADED_NO_WRITE_TOOLS" for flag in ledger.anomaly_flags)


@pytest.mark.asyncio
async def test_no_downgrade_when_write_tool_present() -> None:
    ledger = TurnLedger(turn_id="t_keep")
    context: list[dict[str, Any]] = [{"role": "user", "content": "实现登录功能"}]

    contract = await resolve_turn_delivery_contract(
        turn_id="t_keep",
        context=context,
        tool_definitions=[
            {"type": "function", "function": {"name": "read_file"}},
            {"type": "function", "function": {"name": "write_file"}},
        ],
        ledger=ledger,
        resolve_delivery_mode_hybrid=_resolve_materialize,
        inherit_materialize_from_history=_no_inherit,
    )

    assert contract.mode == DeliveryMode.MATERIALIZE_CHANGES
    assert not any(flag.get("type") == "DELIVERY_CONTRACT_DOWNGRADED_NO_WRITE_TOOLS" for flag in ledger.anomaly_flags)


@pytest.mark.asyncio
async def test_inherit_materialize_from_history_callable_penetrates() -> None:
    ledger = TurnLedger(turn_id="t_inherit")
    context: list[dict[str, Any]] = [{"role": "user", "content": "继续"}]

    def _force_inherit(_context: list[dict[str, Any]], _latest: str) -> DeliveryContract | None:
        return _materialize()

    contract = await resolve_turn_delivery_contract(
        turn_id="t_inherit",
        context=context,
        tool_definitions=[
            {"type": "function", "function": {"name": "read_file"}},
            {"type": "function", "function": {"name": "write_file"}},
        ],
        ledger=ledger,
        resolve_delivery_mode_hybrid=_resolve_analyze,
        inherit_materialize_from_history=_force_inherit,
    )

    assert contract.mode == DeliveryMode.MATERIALIZE_CHANGES
    assert any(flag.get("type") == "DELIVERY_CONTRACT_INHERITED" for flag in ledger.anomaly_flags)


@pytest.mark.asyncio
async def test_explicit_materialize_marker_overrides_analyze() -> None:
    ledger = TurnLedger(turn_id="t_marker")
    context: list[dict[str, Any]] = [{"role": "user", "content": "[mode:materialize]\n实现 worker.txt"}]

    contract = await resolve_turn_delivery_contract(
        turn_id="t_marker",
        context=context,
        tool_definitions=[
            {"type": "function", "function": {"name": "read_file"}},
            {"type": "function", "function": {"name": "write_file"}},
        ],
        ledger=ledger,
        resolve_delivery_mode_hybrid=_resolve_analyze,
        inherit_materialize_from_history=_no_inherit,
    )

    assert contract.mode == DeliveryMode.MATERIALIZE_CHANGES
    assert any(flag.get("type") == "DELIVERY_CONTRACT_EXPLICIT_MARKER_OVERRIDDEN" for flag in ledger.anomaly_flags)
