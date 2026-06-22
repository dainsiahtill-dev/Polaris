"""Regression tests for cognitive context hygiene."""

from __future__ import annotations

import json
from pathlib import Path

from polaris.kernelone.cognitive.context import (
    CognitiveSessionManager,
    ConversationTurn,
    classify_cognitive_control_prompt,
    sanitize_conversation_turn_for_persistence,
)


def _turn(message: str, *, response: str | None = None, role_id: str = "pm") -> ConversationTurn:
    return ConversationTurn(
        turn_id="turn_1",
        role_id=role_id,
        message=message,
        intent_type="unknown",
        confidence=0.0,
        execution_path="full_pipe",
        response=response,
        timestamp="2026-06-22T00:00:00+00:00",
    )


def test_classifies_pm_quality_retry_prompt() -> None:
    prompt = "\n".join(
        [
            "上一版 PM 合同未通过质量门禁，请重写并只输出 JSON。",
            "禁止输出 [TOOL_CALL]、<tool_call>、函数调用或任意工具参数。",
            "当前分数: 0",
            "强制要求：",
            "上一版输出片段：",
        ]
    )

    assert classify_cognitive_control_prompt(prompt) == "quality_gate_retry"


def test_classifies_architect_quality_retry_prompt() -> None:
    prompt = "\n".join(
        [
            "上一版架构文档未通过质量门禁，请重写（第 1 次重试）。",
            "请仅输出 JSON，字段 plan_markdown / architecture_markdown。",
            "禁止输出 TOOL_CALL、函数调用标签或目录探测指令。",
            "上一版输出片段：",
        ]
    )

    assert classify_cognitive_control_prompt(prompt) == "quality_gate_retry"


def test_sanitizes_role_adapter_control_prompt_and_echo() -> None:
    prompt = "\n".join(
        [
            "你是 Polaris PM，需要产出可执行任务合同。",
            "绝对禁止输出任何 TOOL_CALL/函数调用标签。",
            "请仅输出 JSON，格式如下：",
            "禁止返回 Markdown、解释文本、代码块或工具调用标签；仅返回一个 JSON 对象。",
        ]
    )
    response = f"=== THINKING PHASE ===\nIntent: {prompt}\n=== END THINKING PHASE ==="

    sanitized = sanitize_conversation_turn_for_persistence(_turn(prompt, response=response))

    assert sanitized.message == "[redacted:cognitive_control_prompt role=pm reason=role_adapter_generation_prompt]"
    assert (
        sanitized.response == "[redacted:cognitive_control_prompt_echo role=pm reason=role_adapter_generation_prompt]"
    )
    assert "你是 Polaris PM" not in sanitized.message
    assert "TOOL_CALL" not in str(sanitized.response)


def test_keeps_normal_user_message_unchanged() -> None:
    turn = _turn(
        "为什么 PM 上下文会出现禁止输出 TOOL_CALL 这句话？",
        response="这是一次上下文卫生审计问题。",
        role_id="director",
    )

    assert sanitize_conversation_turn_for_persistence(turn) is turn


def test_session_manager_persists_sanitized_turn(tmp_path: Path) -> None:
    manager = CognitiveSessionManager(workspace=str(tmp_path))
    try:
        manager.get_or_create_session("session-1", role_id="pm")
        prompt = "\n".join(
            [
                "上一版 PM 合同未通过质量门禁，请重写并只输出 JSON。",
                "禁止输出 [TOOL_CALL]、<tool_call>、函数调用或任意工具参数。",
                "当前分数: 0",
                "强制要求：",
                "上一版输出片段：",
            ]
        )
        manager.update_session("session-1", _turn(prompt, response=f"Intent: {prompt}"))

        session_file = tmp_path / ".polaris" / "cognitive_sessions" / "session-1.json"
        payload = json.loads(session_file.read_text(encoding="utf-8"))
        turn = payload["conversation_history"][0]

        assert turn["message"] == "[redacted:cognitive_control_prompt role=pm reason=quality_gate_retry]"
        assert turn["response"] == "[redacted:cognitive_control_prompt_echo role=pm reason=quality_gate_retry]"
        assert "上一版 PM 合同未通过质量门禁" not in json.dumps(payload, ensure_ascii=False)
        assert "禁止输出 [TOOL_CALL]" not in json.dumps(payload, ensure_ascii=False)
    finally:
        manager._stop_cleanup_thread()


def test_session_manager_sanitizes_legacy_file_on_load(tmp_path: Path) -> None:
    sessions_dir = tmp_path / ".polaris" / "cognitive_sessions"
    sessions_dir.mkdir(parents=True)
    prompt = "\n".join(
        [
            "上一版架构文档未通过质量门禁，请重写（第 1 次重试）。",
            "请仅输出 JSON，字段 plan_markdown / architecture_markdown。",
            "禁止输出 TOOL_CALL、函数调用标签或目录探测指令。",
            "上一版输出片段：",
        ]
    )
    legacy_payload = {
        "session_id": "legacy-session",
        "role_id": "architect",
        "posture": "transparent_reasoning",
        "created_at": "2026-06-22T00:00:00+00:00",
        "conversation_history": [
            {
                "turn_id": "turn_1",
                "role_id": "architect",
                "message": prompt,
                "intent_type": "unknown",
                "confidence": 0.0,
                "execution_path": "full_pipe",
                "response": f"Intent: {prompt}",
                "timestamp": "2026-06-22T00:00:00+00:00",
                "blocked": False,
                "block_reason": None,
            }
        ],
    }
    session_file = sessions_dir / "legacy-session.json"
    session_file.write_text(json.dumps(legacy_payload, ensure_ascii=False), encoding="utf-8")

    manager = CognitiveSessionManager(workspace=str(tmp_path))
    try:
        ctx = manager.get_session("legacy-session")
        assert ctx is not None
        assert (
            ctx.conversation_history[0].message
            == "[redacted:cognitive_control_prompt role=architect reason=quality_gate_retry]"
        )

        persisted = json.loads(session_file.read_text(encoding="utf-8"))
        persisted_turn = persisted["conversation_history"][0]
        assert (
            persisted_turn["message"] == "[redacted:cognitive_control_prompt role=architect reason=quality_gate_retry]"
        )
        assert "上一版架构文档未通过质量门禁" not in json.dumps(persisted, ensure_ascii=False)
    finally:
        manager._stop_cleanup_thread()
