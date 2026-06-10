"""Recon-required finalize gate (ADR-0091) 单元测试。

验证：
1. has_successful_recon_execution 谓词真值表（含别名/大小写归一化）
2. TransactionConfig.recon_required 默认 False（非侦察角色零爆炸半径）
3. 门禁阻断零侦察 FINAL_ANSWER → BlockedReason.NO_RECON_PERFORMED
4. 成功侦察执行后正常 finalize
5. 失败的侦察调用不构成落地证据
6. 拒绝响应豁免（REFUSAL_MARKERS，与写侧门禁一致）
7. TurnEngine._resolve_recon_required 信号派生（profile 档位 + env 灰度）
"""

from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from polaris.cells.roles.kernel.internal.transaction.contract_guards import (
    has_successful_recon_execution,
)
from polaris.cells.roles.kernel.internal.transaction.delivery_contract import BlockedReason
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
from polaris.cells.roles.kernel.internal.turn_engine.engine import TurnEngine
from polaris.cells.roles.kernel.internal.turn_state_machine import TurnState, TurnStateMachine
from polaris.cells.roles.kernel.internal.turn_transaction_controller import (
    TransactionConfig,
    TurnTransactionController,
)
from polaris.cells.roles.kernel.public.turn_contracts import TurnDecision
from polaris.kernelone.benchmark.unified_judge import _SCOUT_RECON_TOOLS
from polaris.kernelone.tool_execution.tool_categories import SCOUT_RECON_TOOLS

# ============ helpers ============


def _execution(tool_name: str, status: str = "success") -> dict[str, Any]:
    return {"tool_name": tool_name, "call_id": f"call_{tool_name}", "status": status, "duration_ms": 5}


def _decision(turn_id: str, visible_message: str) -> TurnDecision:
    return cast(
        TurnDecision,
        {"turn_id": turn_id, "kind": "FINAL_ANSWER", "visible_message": visible_message},
    )


def _controller(*, recon_required: bool) -> TurnTransactionController:
    return TurnTransactionController(
        llm_provider=AsyncMock(),
        tool_runtime=AsyncMock(),
        config=TransactionConfig(recon_required=recon_required),
    )


def _decoded_state_machine(turn_id: str) -> TurnStateMachine:
    sm = TurnStateMachine(turn_id=turn_id)
    sm.transition_to(TurnState.CONTEXT_BUILT)
    sm.transition_to(TurnState.DECISION_REQUESTED)
    sm.transition_to(TurnState.DECISION_RECEIVED)
    sm.transition_to(TurnState.DECISION_DECODED)
    return sm


class _StubContextPolicy:
    def __init__(self, recon_mode: bool) -> None:
        self.recon_mode = recon_mode


class _StubProfile:
    def __init__(self, recon_mode: bool) -> None:
        self.context_policy = _StubContextPolicy(recon_mode)


# ============ 1. 谓词真值表 ============


def test_recon_predicate_zero_executions_is_false() -> None:
    assert has_successful_recon_execution([]) is False


def test_recon_predicate_failed_recon_only_is_false() -> None:
    assert has_successful_recon_execution([_execution("repo_rg", status="error")]) is False


def test_recon_predicate_one_successful_recon_is_true() -> None:
    assert has_successful_recon_execution([_execution("repo_rg")]) is True


def test_recon_predicate_non_recon_tool_only_is_false() -> None:
    assert has_successful_recon_execution([_execution("edit_blocks")]) is False


def test_recon_predicate_normalizes_case_and_dashes() -> None:
    assert has_successful_recon_execution([_execution("Repo-RG")]) is True


def test_recon_predicate_resolves_legacy_aliases() -> None:
    # TOOL_ALIASES: rg -> repo_rg
    assert has_successful_recon_execution([_execution("rg")]) is True


def test_recon_predicate_mixed_failed_then_success() -> None:
    executions = [_execution("repo_rg", status="error"), _execution("read_file")]
    assert has_successful_recon_execution(executions) is True


def test_recon_predicate_skips_non_mapping_entries() -> None:
    assert has_successful_recon_execution(cast(list, ["garbage", None, 42])) is False


# ============ 2. SSOT 一致性（ADR-0091 R4） ============


def test_judge_and_kernel_share_recon_tool_ssot() -> None:
    """评测 judge 与内核门禁必须引用同一侦察工具集合。"""
    assert _SCOUT_RECON_TOOLS is SCOUT_RECON_TOOLS
    # 哨兵成员：核心检索/读取/子代理派发工具必须在集合内
    for tool in ("repo_rg", "repo_tree", "read_file", "scout_probe"):
        assert tool in SCOUT_RECON_TOOLS


# ============ 3. 配置默认值（零爆炸半径） ============


def test_transaction_config_recon_required_defaults_false() -> None:
    assert TransactionConfig().recon_required is False


# ============ 4. 门禁行为 ============


@pytest.mark.asyncio
async def test_gate_blocks_zero_recon_final_answer() -> None:
    """recon_required 内核中，零侦察 FINAL_ANSWER 必须被阻断（R1）。"""
    controller = _controller(recon_required=True)
    turn_id = "turn_recon_block"
    sm = _decoded_state_machine(turn_id)
    ledger = TurnLedger(turn_id=turn_id)

    result = await controller._handle_final_answer(
        _decision(turn_id, "答案在 core/contracts.py 的 SessionContext。"), sm, ledger
    )

    assert result["kind"] == "recon_bypass_blocked"
    finalization = cast(dict[str, Any], result["finalization"])
    assert finalization["blocked_reason"] == BlockedReason.NO_RECON_PERFORMED.value
    assert ledger.mutation_obligation.blocked_reason is BlockedReason.NO_RECON_PERFORMED
    assert sm.current_state == TurnState.COMPLETED


@pytest.mark.asyncio
async def test_gate_passes_with_successful_recon() -> None:
    """已有成功侦察执行的 FINAL_ANSWER 正常 finalize。"""
    controller = _controller(recon_required=True)
    turn_id = "turn_recon_pass"
    sm = _decoded_state_machine(turn_id)
    ledger = TurnLedger(turn_id=turn_id)
    ledger.record_tool_execution("repo_rg", "call_1", "success", 12)

    result = await controller._handle_final_answer(
        _decision(turn_id, "答案在 core/contracts.py 的 SessionContext。"), sm, ledger
    )

    assert result["kind"] == "final_answer"
    assert ledger.mutation_obligation.blocked_reason is None


@pytest.mark.asyncio
async def test_gate_blocks_when_all_recon_failed() -> None:
    """全部失败的侦察调用不构成落地证据。"""
    controller = _controller(recon_required=True)
    turn_id = "turn_recon_failed_only"
    sm = _decoded_state_machine(turn_id)
    ledger = TurnLedger(turn_id=turn_id)
    ledger.record_tool_execution("repo_rg", "call_1", "error", 12)

    result = await controller._handle_final_answer(
        _decision(turn_id, "答案在 core/contracts.py 的 SessionContext。"), sm, ledger
    )

    assert result["kind"] == "recon_bypass_blocked"


@pytest.mark.asyncio
async def test_gate_exempts_refusal() -> None:
    """合法拒绝响应豁免（R2，与写侧 REFUSAL_MARKERS 豁免一致）。"""
    controller = _controller(recon_required=True)
    turn_id = "turn_recon_refusal"
    sm = _decoded_state_machine(turn_id)
    ledger = TurnLedger(turn_id=turn_id)

    result = await controller._handle_final_answer(
        _decision(turn_id, "无法在当前工作区定位该符号，请提供更多上下文。"), sm, ledger
    )

    assert result["kind"] == "final_answer"


@pytest.mark.asyncio
async def test_non_recon_config_takes_prechange_path() -> None:
    """recon_required=False（默认）时，零侦察 FINAL_ANSWER 行为不变（R3）。"""
    controller = _controller(recon_required=False)
    turn_id = "turn_non_recon_parity"
    sm = _decoded_state_machine(turn_id)
    ledger = TurnLedger(turn_id=turn_id)

    result = await controller._handle_final_answer(
        _decision(turn_id, "答案在 core/contracts.py 的 SessionContext。"), sm, ledger
    )

    assert result["kind"] == "final_answer"


# ============ 5. 信号派生（R3） ============


def test_resolve_recon_required_from_profile_policy() -> None:
    assert TurnEngine._resolve_recon_required("scout", _StubProfile(recon_mode=True)) is True
    assert TurnEngine._resolve_recon_required("scout", _StubProfile(recon_mode=False)) is False
    assert TurnEngine._resolve_recon_required("director", _StubProfile(recon_mode=False)) is False


def test_resolve_recon_required_env_is_scout_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNELONE_SCOUT_RECON_MODE", "1")
    assert TurnEngine._resolve_recon_required("scout", _StubProfile(recon_mode=False)) is True
    assert TurnEngine._resolve_recon_required("director", _StubProfile(recon_mode=False)) is False
    monkeypatch.setenv("KERNELONE_SCOUT_RECON_MODE", "off")
    assert TurnEngine._resolve_recon_required("scout", _StubProfile(recon_mode=False)) is False


def test_resolve_recon_required_handles_missing_context_policy() -> None:
    class _Bare:
        pass

    assert TurnEngine._resolve_recon_required("scout", _Bare()) is False
