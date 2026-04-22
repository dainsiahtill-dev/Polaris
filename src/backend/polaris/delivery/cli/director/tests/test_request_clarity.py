from __future__ import annotations

from polaris.delivery.cli.director.console_host import (
    RequestClarity,
    _assess_director_request_clarity,
    _is_continuation_intent,
)


def test_assess_director_request_clarity_handles_file_path_regex() -> None:
    clarity = _assess_director_request_clarity("请修改 polaris/delivery/cli/terminal_console.py 第120行")
    assert clarity in {
        RequestClarity.EXECUTABLE,
        RequestClarity.SEMI_CLEAR,
    }


def test_assess_director_request_clarity_marks_empty_as_vague() -> None:
    assert _assess_director_request_clarity("") == RequestClarity.VAGUE


def test_is_continuation_intent_detects_chinese_continue() -> None:
    assert _is_continuation_intent("继续") is True
    assert _is_continuation_intent("继续执行") is True
    assert _is_continuation_intent("继续干") is True


def test_is_continuation_intent_detects_english_continue() -> None:
    assert _is_continuation_intent("continue") is True
    assert _is_continuation_intent("go on") is True
    assert _is_continuation_intent("proceed") is True
    assert _is_continuation_intent("next") is True


def test_is_continuation_intent_detects_affirmative() -> None:
    assert _is_continuation_intent("ok") is True
    assert _is_continuation_intent("好的") is True
    assert _is_continuation_intent("sure") is True
    assert _is_continuation_intent("yes") is True


def test_is_continuation_intent_rejects_non_continuation() -> None:
    assert _is_continuation_intent("修复 bug") is False
    assert _is_continuation_intent("添加功能") is False
    assert _is_continuation_intent("") is False
    assert _is_continuation_intent("分析一下") is False


def test_assess_director_request_clarity_treats_continuation_as_executable() -> None:
    """Continuation intents must pass the clarity gate so the orchestrator
    can preserve the original goal and inject the hint into Instruction."""
    assert _assess_director_request_clarity("继续") == RequestClarity.EXECUTABLE
    assert _assess_director_request_clarity("continue") == RequestClarity.EXECUTABLE
    assert _assess_director_request_clarity("go on") == RequestClarity.EXECUTABLE
    assert _assess_director_request_clarity("下一步") == RequestClarity.EXECUTABLE
    assert _assess_director_request_clarity("ok") == RequestClarity.EXECUTABLE
