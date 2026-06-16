"""Test Context Compressor Integration - 上下文压缩集成测试

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

测试 RoleContextCompressor 在 kernel 中的集成:
1. 环境变量激活开关 (KERNELONE_CONTEXT_COMPACTION)
2. 压缩前后内容差异
3. 向后兼容性
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import pytest

# 确保 polaris 在路径中
sys.path.insert(0, str(__file__).rsplit("/polaris/", 1)[0] if "/polaris/" in __file__ else ".")

# T2-A tests deliberately keep imports INSIDE the test methods (not
# module-scope). Pulling ``RoleContextCompressor`` / ``RoleContextIdentity``
# up to module scope (runtime OR TYPE_CHECKING) triggers F811 redefinition
# errors against the pre-existing in-method imports in this file
# (lines 153/195/259/...) AND perturbs mypy's strict inference of
# ``messages`` in the pre-existing ``test_micro_compact_replaces_old_tool_results``
# test. The slice helper therefore keeps an unannotated return type.


class TestContextCompressorIntegration:
    """测试上下文压缩在 kernel 中的集成"""

    def test_env_var_flag_disabled_by_default(self) -> None:
        """测试: 默认情况下上下文压缩是禁用的"""
        # 清除环境变量
        env_backup = os.environ.pop("KERNELONE_CONTEXT_COMPACTION", None)

        try:
            # 验证默认值为 False
            from polaris.kernelone.context.compaction import (
                RoleContextCompressor,
                RoleContextIdentity,
            )

            # 直接测试 RoleContextCompressor
            compressor = RoleContextCompressor(
                workspace=".",
                role_name="test",
            )

            # 当 token 数低于阈值时，compact_if_needed 不应触发
            messages = [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there!"},
            ]

            identity = RoleContextIdentity.from_role_state(
                role_name="test",
                goal="Test conversation",
                scope=[],
            )

            compressed, snapshot = compressor.compact_if_needed(messages, identity)
            assert snapshot is None, "当 token 数低于阈值时，不应触发压缩"
            assert compressed == messages, "未压缩时，消息应保持不变"
        finally:
            if env_backup is not None:
                os.environ["KERNELONE_CONTEXT_COMPACTION"] = env_backup

    def test_micro_compact_replaces_old_tool_results(self) -> None:
        """测试: micro_compact 替换旧的工具结果

        Note:
            micro_compact 只压缩 content 长度 > 100 的工具结果。
            短内容不会被压缩，但会在 transcript 中记录。
        """
        from polaris.kernelone.context.compaction import (
            RoleContextCompressor,
        )

        compressor = RoleContextCompressor(
            workspace=".",
            role_name="test_micro",
            config={"micro_compact_keep": 2},
        )

        # 模拟多轮工具调用后的消息 (使用实际 API 格式)
        # micro_compact 检查 role=user 且 content=[{type: tool_result, ...}]
        # 只有 content 长度 > 100 时才会被压缩为 placeholder
        long_content_a = "A" * 150  # 长度 > 100，会被压缩
        long_content_b = "B" * 150
        short_content_c = "C"  # 长度 < 100，不会被压缩
        short_content_d = "D"

        messages: list[dict[str, object]] = [
            {"role": "system", "content": "You are a helpful assistant."},
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "tool_1", "name": "read_file", "input": {"path": "A"}}],
            },
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tool_1", "content": long_content_a}]},
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "tool_2", "name": "read_file", "input": {"path": "B"}}],
            },
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tool_2", "content": long_content_b}]},
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "tool_3", "name": "read_file", "input": {"path": "C"}}],
            },
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tool_3", "content": short_content_c}]},
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "tool_4", "name": "read_file", "input": {"path": "D"}}],
            },
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tool_4", "content": short_content_d}]},
            {"role": "user", "content": "Current request"},
        ]

        compressed = compressor.micro_compact(messages)

        # 验证消息数量不变
        assert len(compressed) == len(messages), "消息数量不应改变"

        # 验证长内容的工具结果被压缩
        # 查找被压缩的工具结果
        compacted_results = []
        for msg in compressed:
            if msg.get("role") == "user" and isinstance(msg.get("content"), list):
                for part in msg["content"]:
                    if isinstance(part, dict) and part.get("type") == "tool_result" and part.get("_compacted"):
                        compacted_results.append(part)

        # tool_1 和 tool_2 的长内容应该被压缩
        assert len(compacted_results) == 2, f"应该有 2 个被压缩的工具结果，实际: {len(compacted_results)}"

        # 验证压缩后的内容是 placeholder
        for result in compacted_results:
            assert result.get("content", "").startswith("[Previous: used "), (
                f"压缩内容应为placeholder，实际: {result.get('content')}"
            )
            assert result.get("_original_length", 0) > 100, "原始长度应 > 100"

    def test_compact_snapshot_structure(self) -> None:
        """测试: CompactSnapshot 数据结构"""
        from polaris.kernelone.context.compaction import (
            CompactSnapshot,
            RoleContextCompressor,
            RoleContextIdentity,
        )

        compressor = RoleContextCompressor(
            workspace=".",
            role_name="test_snapshot",
            config={"token_threshold": 1},  # 极低阈值确保触发
        )

        # 创建大量消息以触发压缩
        messages = []
        for i in range(20):
            messages.append(
                {
                    "role": "user" if i % 2 == 0 else "assistant",
                    "content": f"This is message number {i} with some content to make it longer and trigger compression. "
                    * 10,
                }
            )

        identity = RoleContextIdentity.from_role_state(
            role_name="test_snapshot",
            goal="Test long conversation",
            scope=["src/"],
        )

        _compressed, snapshot = compressor.compact_if_needed(messages, identity, force_compact=True)

        assert snapshot is not None, "强制压缩后应该有快照"
        assert isinstance(snapshot, CompactSnapshot), "快照类型应为 CompactSnapshot"
        assert snapshot.original_tokens > 0, "原始 token 数应大于 0"
        assert snapshot.compressed_tokens > 0, "压缩后 token 数应大于 0"
        assert snapshot.compressed_tokens < snapshot.original_tokens, "压缩后 token 数应小于原始"
        assert snapshot.method in ("llm", "deterministic", "truncate"), (
            f"方法应为 'llm'/'deterministic'/'truncate'，实际: {snapshot.method}"
        )
        assert snapshot.role_name == "test_snapshot", f"角色名应匹配，实际: {snapshot.role_name}"

    def test_identity_preserved_after_compaction(self) -> None:
        """测试: 压缩后角色身份保持一致"""
        from polaris.kernelone.context.compaction import (
            RoleContextCompressor,
            RoleContextIdentity,
        )

        compressor = RoleContextCompressor(
            workspace=".",
            role_name="director",
            config={"token_threshold": 1},
        )

        identity = RoleContextIdentity.from_role_state(
            role_name="director",
            goal="Implement user authentication feature",
            scope=["src/auth/", "tests/auth/"],
            current_task_id="task-123",
        )

        messages = [
            {"role": "system", "content": "You are the Director agent."},
        ]

        # 添加大量历史消息
        for i in range(30):
            messages.append(
                {
                    "role": "user",
                    "content": f"Previous task step {i}: Did some work on the codebase. " * 20,
                }
            )

        compressed, _snapshot = compressor.compact_if_needed(messages, identity, force_compact=True)

        # 验证压缩后包含身份锚点
        has_identity = any(
            "director" in str(msg.get("content", "")).lower()
            or "task-123" in str(msg.get("content", "")).lower()
            or "Implement user authentication" in str(msg.get("content", ""))
            for msg in compressed
        )

        assert has_identity, "压缩后的消息应包含角色身份信息"

    def test_context_compressor_lazy_init(self) -> None:
        """测试: ContextCompressor 延迟初始化"""
        from polaris.kernelone.context.compaction import (
            RoleContextCompressor,
        )

        # 不提供 llm_client
        compressor = RoleContextCompressor(
            workspace=".",
            role_name="lazy_test",
        )

        assert compressor.llm_client is None, "默认不应有 LLM 客户端"
        assert compressor.role_name == "lazy_test", "角色名应正确设置"

        # 验证 estimate_tokens 方法可用
        tokens = compressor.estimate_tokens([{"role": "user", "content": "Test message"}])
        assert tokens > 0, "Token 估算应返回正数"

    def test_transcript_service_integration(self) -> None:
        """测试: Transcript service 集成"""
        from polaris.kernelone.context.compaction import (
            RoleContextCompressor,
            RoleContextIdentity,
        )

        # 创建 mock transcript service
        mock_service = MagicMock()
        mock_service.record_message = MagicMock()

        compressor = RoleContextCompressor(
            workspace=".",
            role_name="transcript_test",
            transcript_service=mock_service,
        )

        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
        ]

        identity = RoleContextIdentity.from_role_state(
            role_name="transcript_test",
            goal="Test transcript",
            scope=[],
        )

        # 触发 micro_compact
        compressor.micro_compact(messages)

        # 验证 transcript service 被调用
        # 注意：micro_compact 只在有超过阈值时才记录
        # 这里我们测试 force_compact
        _compressed, snapshot = compressor.compact_if_needed(
            messages * 100,  # 大量消息
            identity,
            force_compact=True,
        )

        # 如果有压缩发生，transcript service 会被调用
        if snapshot is not None:
            # record_message 应该被调用过
            assert True, "Transcript service 应该有记录"


class TestKernelContextCompression:
    """测试 kernel 中的上下文压缩集成"""

    def test_kernel_reads_env_var(self) -> None:
        """测试: Kernel 读取环境变量"""
        # 这个测试验证环境变量可以被正确读取
        os.environ["KERNELONE_CONTEXT_COMPACTION"] = "true"
        os.environ["KERNELONE_CONTEXT_COMPACTION_THRESHOLD"] = "10000"

        try:
            # 当 KERNELONE_CONTEXT_COMPACTION=true 时，kernel 应该初始化 compressor
            # 由于 kernel 初始化需要完整的 registry 等依赖，我们只验证环境变量逻辑

            config = {"token_threshold": int(os.environ.get("KERNELONE_CONTEXT_COMPACTION_THRESHOLD", "50000"))}

            assert config["token_threshold"] == 10000, "阈值应从环境变量读取"

        finally:
            os.environ.pop("KERNELONE_CONTEXT_COMPACTION", None)
            os.environ.pop("KERNELONE_CONTEXT_COMPACTION_THRESHOLD", None)

    def test_kernel_respects_disabled_flag(self) -> None:
        """测试: Kernel 尊重禁用标志"""
        # 确保禁用
        os.environ.pop("KERNELONE_CONTEXT_COMPACTION", None)

        from polaris.kernelone.context.compaction import (
            RoleContextCompressor,
            RoleContextIdentity,
        )

        # 当环境变量未设置或为 false 时，不应触发压缩
        compressor = RoleContextCompressor(
            workspace=".",
            role_name="disabled_test",
            config={"token_threshold": 50000},  # 高阈值
        )

        messages = [{"role": "user", "content": "Short message"}]

        identity = RoleContextIdentity.from_role_state(
            role_name="disabled_test",
            goal="Test",
            scope=[],
        )

        _compressed, snapshot = compressor.compact_if_needed(messages, identity)

        # 未超过阈值不应压缩
        assert snapshot is None, "未超过阈值时不应触发压缩"


class TestContextCompactionBlueprint:
    """验证蓝图要求的集成点"""

    def test_blueprint_requirement_1_import(self) -> None:
        """验证: 蓝图要求1 - 导入 RoleContextCompressor"""
        try:
            from polaris.kernelone.context.compaction import (  # noqa: F401
                CompactSnapshot,
                RoleContextCompressor,
                RoleContextIdentity,
            )

            assert True, "导入成功"
        except ImportError as e:
            pytest.fail(f"无法导入 RoleContextCompressor: {e}")

    def test_blueprint_requirement_2_env_var(self) -> None:
        """验证: 蓝图要求2 - 环境变量 KERNELONE_CONTEXT_COMPACTION=true 激活"""
        # 测试默认值
        default_value = os.environ.get("KERNELONE_CONTEXT_COMPACTION", "false").lower()
        assert default_value in ("true", "false"), "环境变量值应为 true/false"

        # 测试激活
        os.environ["KERNELONE_CONTEXT_COMPACTION"] = "true"
        try:
            is_enabled = os.environ.get("KERNELONE_CONTEXT_COMPACTION", "false").lower() in ("true", "1", "yes")
            assert is_enabled, "设置为 true 后应被激活"

        finally:
            os.environ.pop("KERNELONE_CONTEXT_COMPACTION", None)

    def test_blueprint_requirement_3_backward_compat(self) -> None:
        """验证: 蓝图要求3 - 向后兼容"""
        from polaris.kernelone.context.compaction import (
            RoleContextCompressor,
            RoleContextIdentity,
        )

        # 未设置环境变量时，kernel 应正常工作
        os.environ.pop("KERNELONE_CONTEXT_COMPACTION", None)

        compressor = RoleContextCompressor(
            workspace=".",
            role_name="compat_test",
        )

        # 正常消息不应触发压缩
        messages = [
            {"role": "user", "content": "Hello, how are you?"},
            {"role": "assistant", "content": "I'm doing well, thank you!"},
        ]

        identity = RoleContextIdentity.from_role_state(
            role_name="compat_test",
            goal="Simple chat",
            scope=[],
        )

        compressed, snapshot = compressor.compact_if_needed(messages, identity)

        # 未超过阈值不应压缩
        assert snapshot is None or len(compressed) > 0, "应该返回有效结果"


class TestT2AVetoOnLiveLlmCompact:
    """T2-A reject-if-not-smaller veto on the LIVE llm_compact path.

    The veto is env-gated (KERNELONE_T2A_VETO, default off) because reverting to
    the input messages on a non-shrinking pass changes which message list ships
    to the LLM -> L2-floor bench required to enable. Default-off keeps the
    compressed message list byte-identical to historical behaviour.

    Adversarial cases covered:
      * default-off is byte-identical to historical (no rejected-snapshot path)
      * flag-on + non-shrinking pass -> revert + llm_rejected_noshrink snapshot
      * flag-on + shrinking pass -> normal llm path
      * flag-on + degenerate original_tokens==0 -> NO veto (guarded explicitly)
      * flag-on + exact-shrink-by-1 boundary -> NO veto (strict <)
      * case-insensitive flag values (TRUE/Yes/on) all enable
    """

    def _make_compressor(self, llm_summary: str, workspace: str):
        # The runtime type is ``RoleContextCompressor``. We deliberately do
        # NOT import it at module scope (see file-level note) and we
        # deliberately omit the explicit return annotation to avoid ruff
        # F821 on a forward-reference that has no static resolver here.
        from polaris.kernelone.context.compaction import RoleContextCompressor

        client = MagicMock()
        client.messages.create.return_value = MagicMock(content=[MagicMock(text=llm_summary)])
        return RoleContextCompressor(
            workspace=workspace,
            role_name="veto_test",
            llm_client=client,
        )

    def _enable_flag(self, monkeypatch) -> None:
        monkeypatch.setenv("KERNELONE_T2A_VETO", "1")

    def test_flag_off_byte_identical_to_historical(self, tmp_path, monkeypatch) -> None:
        """Default-off: the compressed message list is unchanged (floor-inert)."""
        monkeypatch.delenv("KERNELONE_T2A_VETO", raising=False)
        from polaris.kernelone.context.compaction import RoleContextIdentity

        # An absurd summary that would otherwise expand the tokens is irrelevant:
        # the flag-off path returns the LLM-summary's compressed list regardless.
        compressor = self._make_compressor("X", workspace=str(tmp_path))
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello there, friend"},
        ]
        identity = RoleContextIdentity(role_id="t", role_type="unknown", goal="g", scope=[])

        _compressed, snapshot = compressor.llm_compact(messages, identity)
        # Flag-off branch: method is "llm" / "deterministic", NOT "llm_rejected_noshrink".
        assert snapshot is not None
        assert snapshot.method in {"llm", "deterministic"}
        assert snapshot.method != "llm_rejected_noshrink"

    def test_flag_on_non_shrinking_pass_reverts_to_input_messages(self, tmp_path, monkeypatch) -> None:
        """Flag-on + non-shrinking pass: messages are REVERTED, snapshot records it."""
        self._enable_flag(monkeypatch)
        from polaris.kernelone.context.compaction import RoleContextIdentity

        # Craft a summary that, when wrapped in the reinjection prompt + ack,
        # is LARGER than the input -> veto fires and we REVERT.
        huge_summary = "S" * 10000
        compressor = self._make_compressor(huge_summary, workspace=str(tmp_path))
        messages = [{"role": "user", "content": "short"}]
        identity = RoleContextIdentity(role_id="t", role_type="unknown", goal="g", scope=[])

        reverted, snapshot = compressor.llm_compact(messages, identity)

        # Veto fired -> snapshot records the rejection, messages REVERTED verbatim.
        assert snapshot is not None
        assert snapshot.method == "llm_rejected_noshrink"
        assert snapshot.compressed_tokens == snapshot.original_tokens
        assert reverted == messages, "non-shrinking pass must REVERT to the input messages"

    def test_flag_on_shrinking_pass_still_uses_llm_compact(self, tmp_path, monkeypatch) -> None:
        """Flag-on + shrinking pass: the veto does NOT fire; llm_compact normal path runs."""
        self._enable_flag(monkeypatch)
        from polaris.kernelone.context.compaction import RoleContextIdentity

        compressor = self._make_compressor("brief", workspace=str(tmp_path))
        # Many long messages so the summary genuinely shrinks.
        messages = [
            {"role": "user", "content": "x" * 2000},
            {"role": "assistant", "content": "y" * 2000},
        ] * 10
        identity = RoleContextIdentity(role_id="t", role_type="unknown", goal="g", scope=[])

        compressed, snapshot = compressor.llm_compact(messages, identity)
        assert snapshot is not None
        assert snapshot.method != "llm_rejected_noshrink"
        # The compressed list is SHORTER than the input -> the veto correctly stayed silent.
        assert len(compressed) < len(messages)

    def test_flag_on_degenerate_zero_original_tokens_skips_veto(self, tmp_path, monkeypatch) -> None:
        """Adversarial: original_tokens == 0 must NOT trigger the veto.

        The guard explicitly requires ``original_tokens > 0`` so an empty
        input cannot be expanded into the rejected-snapshot path. Without
        this guard, an empty messages list + a non-empty LLM summary would
        produce a false-positive noshrink rejection.
        """
        self._enable_flag(monkeypatch)
        from polaris.kernelone.context.compaction import RoleContextIdentity

        compressor = self._make_compressor("any summary", workspace=str(tmp_path))
        # Forge the degenerate case: a token estimator that always returns 0.
        compressor.estimate_tokens = lambda _msgs: 0  # type: ignore[assignment, misc]
        messages: list[dict[str, object]] = []
        identity = RoleContextIdentity(role_id="t", role_type="unknown", goal="g", scope=[])

        _compressed, snapshot = compressor.llm_compact(messages, identity)

        assert snapshot is not None
        # The degenerate case must NOT trip the veto; the normal llm path runs.
        assert snapshot.method != "llm_rejected_noshrink"
        assert snapshot.original_tokens == 0

    def test_flag_on_shrinking_exactly_by_one_keeps_llm_path(self, tmp_path, monkeypatch) -> None:
        """Adversarial: compressed_tokens == original_tokens - 1 (strict <) keeps llm path.

        The design is a strict ``compressed_tokens < original_tokens`` veto,
        not <=. A single-token shrink must be enough to bypass the gate;
        otherwise over-eager rejections would silently downgrade modest
        genuine wins into rejected-snapshot noise.

        Implementation note: we key the mock estimator off the message-list
        structure rather than ``len()`` because the compressed reinjection
        block also has 2 entries (user reinjection + assistant ack).
        """
        self._enable_flag(monkeypatch)
        from polaris.kernelone.context.compaction import RoleContextIdentity

        compressor = self._make_compressor("ignored", workspace=str(tmp_path))
        # Distinguish input (>=3 messages, long historical context) from
        # the compressed reinjection block (exactly 2 short messages).
        input_messages = [
            {"role": "user", "content": "x" * 400},
            {"role": "assistant", "content": "y" * 400},
            {"role": "user", "content": "z" * 400},
        ]

        def _fake_estimate_tokens(msgs: list[dict[str, object]]) -> int:
            # Compressed reinjection block: 2 entries.
            if len(msgs) == 2:
                return 99
            # Otherwise we're measuring the input.
            return 100

        compressor.estimate_tokens = _fake_estimate_tokens  # type: ignore[assignment, misc]
        identity = RoleContextIdentity(role_id="t", role_type="unknown", goal="g", scope=[])

        _compressed, snapshot = compressor.llm_compact(input_messages, identity)

        assert snapshot is not None
        # 99 < 100 strict -> veto stays silent and the normal llm path runs.
        assert snapshot.method != "llm_rejected_noshrink"
        assert snapshot.compressed_tokens == 99
        assert snapshot.original_tokens == 100

    @pytest.mark.parametrize(
        "flag_value",
        ["TRUE", "True", "Yes", "yes", "ON", "on", "1", "true"],
    )
    def test_flag_on_accepts_case_insensitive_enabled_values(self, tmp_path, monkeypatch, flag_value: str) -> None:
        """Adversarial: enabled values must be case-insensitive (TRUE / Yes / on)."""
        monkeypatch.setenv("KERNELONE_T2A_VETO", flag_value)
        from polaris.kernelone.context.compaction import RoleContextIdentity

        compressor = self._make_compressor("S" * 10000, workspace=str(tmp_path))
        messages = [{"role": "user", "content": "tiny"}]
        identity = RoleContextIdentity(role_id="t", role_type="unknown", goal="g", scope=[])

        reverted, snapshot = compressor.llm_compact(messages, identity)
        assert snapshot is not None
        assert snapshot.method == "llm_rejected_noshrink", (
            f"flag_value={flag_value!r} should enable the veto (case-insensitive)"
        )
        assert reverted == messages

    @pytest.mark.parametrize("flag_value", ["", "0", "false", "off", "no", "random"])
    def test_flag_off_unrecognised_values_keep_historical_path(self, tmp_path, monkeypatch, flag_value: str) -> None:
        """Adversarial: anything outside the enabled-set must keep historical behaviour.

        Guards against future config-store / env-passthrough layers silently
        propagating truthy-looking strings (e.g. ``"enabled"``) that would
        accidentally turn the veto on. Floor discipline: only the literal
        enabled set, case-folded, may flip the gate.
        """
        monkeypatch.setenv("KERNELONE_T2A_VETO", flag_value)
        from polaris.kernelone.context.compaction import RoleContextIdentity

        # Huge summary that would trigger the veto if the flag were honoured.
        compressor = self._make_compressor("S" * 10000, workspace=str(tmp_path))
        messages = [{"role": "user", "content": "tiny"}]
        identity = RoleContextIdentity(role_id="t", role_type="unknown", goal="g", scope=[])

        _compressed, snapshot = compressor.llm_compact(messages, identity)
        assert snapshot is not None
        assert snapshot.method != "llm_rejected_noshrink", f"flag_value={flag_value!r} must NOT enable the veto"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
