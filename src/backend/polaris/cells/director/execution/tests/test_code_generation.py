"""Tests for code_generation_engine module."""

from __future__ import annotations

import os
from unittest.mock import Mock, patch

import pytest
from polaris.cells.director.execution.internal.code_generation_engine import (
    CodeGenerationEngine,
    CodeGenerationPolicyViolationError,
    _raise_policy_violation,
    generate_bootstrap_with_llm,
    generate_fallback_code_content,
    generate_phase_aware_fallback_content,
)


class TestCodeGenerationPolicyViolationError:
    def test_raise_policy_violation(self):
        with pytest.raises(CodeGenerationPolicyViolationError) as exc_info:
            _raise_policy_violation("test_action")
        assert "test_action" in str(exc_info.value)


class TestCodeGenerationEngineInit:
    def test_init_basic(self):
        engine = CodeGenerationEngine(workspace="/tmp/workspace", executor=Mock())
        assert engine.workspace == "/tmp/workspace"


class TestResolveLlmTimeout:
    def test_resolve_llm_timeout_default(self):
        engine = CodeGenerationEngine("/tmp", Mock())
        assert engine.resolve_llm_timeout(60) == 60

    def test_resolve_llm_timeout_from_env(self):
        engine = CodeGenerationEngine("/tmp", Mock())
        with patch.dict(os.environ, {"KERNELONE_WORKER_LLM_TIMEOUT": "120"}):
            assert engine.resolve_llm_timeout(60) == 120

    def test_resolve_llm_timeout_invalid_env(self):
        engine = CodeGenerationEngine("/tmp", Mock())
        with patch.dict(os.environ, {"KERNELONE_WORKER_LLM_TIMEOUT": "invalid"}):
            assert engine.resolve_llm_timeout(60) == 60

    def test_resolve_llm_timeout_bounds_min(self):
        engine = CodeGenerationEngine("/tmp", Mock())
        with patch.dict(os.environ, {"KERNELONE_WORKER_LLM_TIMEOUT": "5"}):
            assert engine.resolve_llm_timeout(60) == 15

    def test_resolve_llm_timeout_bounds_max(self):
        engine = CodeGenerationEngine("/tmp", Mock())
        with patch.dict(os.environ, {"KERNELONE_WORKER_LLM_TIMEOUT": "500"}):
            assert engine.resolve_llm_timeout(60) == 300


class TestResolveTaskTimeoutBudget:
    def test_resolve_task_timeout_budget_default(self):
        engine = CodeGenerationEngine("/tmp", Mock())
        mock_task = Mock()
        mock_task.timeout_seconds = 0
        result = engine.resolve_task_timeout_budget(mock_task, rounds=1)
        assert 30 <= result <= 1800

    def test_resolve_task_timeout_budget_from_task(self):
        engine = CodeGenerationEngine("/tmp", Mock())
        mock_task = Mock()
        mock_task.timeout_seconds = 300
        assert engine.resolve_task_timeout_budget(mock_task, rounds=1) == 300

    def test_resolve_task_timeout_budget_from_env(self):
        engine = CodeGenerationEngine("/tmp", Mock())
        mock_task = Mock()
        mock_task.timeout_seconds = 0
        with patch.dict(os.environ, {"KERNELONE_WORKER_TOTAL_TIMEOUT": "600"}):
            assert engine.resolve_task_timeout_budget(mock_task, rounds=1) == 600


class TestRemainingTimeout:
    def test_remaining_timeout_future(self):
        import time

        engine = CodeGenerationEngine("/tmp", Mock())
        future_time = time.time() + 100
        result = engine.remaining_timeout(future_time)
        assert 90 <= result <= 100

    def test_remaining_timeout_past(self):
        import time

        engine = CodeGenerationEngine("/tmp", Mock())
        past_time = time.time() - 10
        assert engine.remaining_timeout(past_time) == 0


class TestEnvFlag:
    def test_env_flag_true_values(self):
        engine = CodeGenerationEngine("/tmp", Mock())
        for val in ["1", "true", "yes", "on"]:
            with patch.dict(os.environ, {"TEST_FLAG": val}):
                assert engine._env_flag("TEST_FLAG") is True

    def test_env_flag_false_values(self):
        engine = CodeGenerationEngine("/tmp", Mock())
        for val in ["0", "false", "no", "off"]:
            with patch.dict(os.environ, {"TEST_FLAG": val}):
                assert engine._env_flag("TEST_FLAG") is False


class TestAllowTemplateFallback:
    def test_allow_template_fallback_always_false(self):
        engine = CodeGenerationEngine("/tmp", Mock())
        assert engine.allow_template_fallback() is False


class TestIsLowSignalResponse:
    def test_is_low_signal_short_response(self):
        engine = CodeGenerationEngine("/tmp", Mock())
        assert engine.is_low_signal_response("Short") is True

    def test_is_low_signal_normal_response(self):
        engine = CodeGenerationEngine("/tmp", Mock())
        response = "This is a reasonably long response with enough content to pass the minimum character threshold of 180 characters that is needed for a proper test of the low signal detection function which looks at text length and refusal markers."
        assert engine.is_low_signal_response(response) is False

    def test_is_low_signal_refusal_markers(self):
        engine = CodeGenerationEngine("/tmp", Mock())
        for refusal in [
            "I cannot complete",
            "need more context",
            "cannot complete",
            "cannot complete",
            "need more context",
        ]:
            assert engine.is_low_signal_response(refusal) is True


class TestRegisterSpinGuard:
    def test_register_spin_guard_first_call(self):
        engine = CodeGenerationEngine("/tmp", Mock())
        tracker = {}
        engine.register_spin_guard(tracker, scope="scope1", prompt="p1", output="o1")
        assert tracker["scope1"]["repeat_count"] == 1

    def test_register_spin_guard_repeat_detection(self):
        engine = CodeGenerationEngine("/tmp", Mock())
        tracker = {}
        engine.register_spin_guard(tracker, scope="scope1", prompt="p1", output="o1")
        engine.register_spin_guard(tracker, scope="scope1", prompt="p1", output="o1")
        assert tracker["scope1"]["repeat_count"] == 2


class TestBlockedEntryPoints:
    def test_invoke_runtime_provider_blocked(self):
        engine = CodeGenerationEngine("/tmp", Mock())
        with pytest.raises(CodeGenerationPolicyViolationError):
            engine.invoke_runtime_provider(prompt="test", model="gpt-4", timeout=60)

    def test_invoke_ollama_blocked(self):
        engine = CodeGenerationEngine("/tmp", Mock())
        with pytest.raises(CodeGenerationPolicyViolationError):
            engine.invoke_ollama(prompt="test", model="llama2", timeout=60)

    def test_build_patch_retry_prompt_blocked(self):
        engine = CodeGenerationEngine("/tmp", Mock())
        with pytest.raises(CodeGenerationPolicyViolationError):
            engine.build_patch_retry_prompt(Mock(), round_files=["f.py"], round_label="r1")

    @pytest.mark.asyncio
    async def test_invoke_generation_with_retries_blocked(self):
        engine = CodeGenerationEngine("/tmp", Mock())
        result = await engine.invoke_generation_with_retries(
            task=Mock(),
            prompt="test",
            model="gpt-4",
            per_call_timeout=60,
            deadline_ts=9999999999,
            round_label="r1",
            round_files=[],
            spin_tracker={},
        )
        assert result[0] == []

    @pytest.mark.asyncio
    async def test_invoke_generation_with_runtime_codegen_applies_response(self, monkeypatch):
        monkeypatch.setenv("KERNELONE_DIRECTOR_RUNTIME_CODEGEN", "1")
        executor = Mock()
        executor._apply_response_operations.return_value = (
            [{"path": "src/app.py", "content": "print('ok')"}],
            [],
        )
        engine = CodeGenerationEngine("/tmp", executor)

        async def fake_invoke(**_: object) -> dict[str, object]:
            return {
                "response": "PATCH_FILE: src/app.py\n<<<<<<< SEARCH\n=======\nprint('ok')\n>>>>>>> REPLACE",
                "provider": "test-provider",
                "model": "test-model",
            }

        monkeypatch.setattr(engine, "_invoke_director_role_response", fake_invoke)

        task = Mock()
        task.id = "task-1"
        files, warnings = await engine.invoke_generation_with_retries(
            task=task,
            prompt="implement",
            model="ignored",
            per_call_timeout=60,
            deadline_ts=9999999999,
            round_label="r1",
            round_files=["src/app.py"],
            spin_tracker={},
        )

        assert files == [{"path": "src/app.py", "content": "print('ok')"}]
        assert warnings == []
        executor._apply_response_operations.assert_called_once()

    @pytest.mark.asyncio
    async def test_runtime_codegen_empty_response_does_not_count_existing_files(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KERNELONE_DIRECTOR_RUNTIME_CODEGEN", "1")
        existing_file = tmp_path / "src" / "app.py"
        existing_file.parent.mkdir()
        existing_file.write_text("print('old')\n", encoding="utf-8")
        executor = Mock()
        executor._apply_response_operations.return_value = ([], ["no_changes"])
        engine = CodeGenerationEngine(str(tmp_path), executor)

        async def fake_invoke(**_: object) -> dict[str, object]:
            return {
                "response": "",
                "provider": "test-provider",
                "model": "test-model",
            }

        monkeypatch.setattr(engine, "_invoke_director_role_response", fake_invoke)

        task = Mock()
        task.id = "task-1"
        files, warnings = await engine.invoke_generation_with_retries(
            task=task,
            prompt="implement",
            model="ignored",
            per_call_timeout=60,
            deadline_ts=9999999999,
            round_label="r1",
            round_files=["src/app.py"],
            spin_tracker={},
        )

        assert files == []
        assert "director_runtime_codegen_empty_response" in warnings
        executor._apply_response_operations.assert_not_called()

    @pytest.mark.asyncio
    async def test_runtime_codegen_invokes_director_in_proposal_mode(self, monkeypatch):
        captured: dict[str, object] = {}

        async def fake_generate_role_response(**kwargs: object) -> dict[str, object]:
            captured.update(kwargs)
            return {
                "response": "```file: src/app.py\nprint('ok')\n```",
                "provider": "test-provider",
                "model": "test-model",
            }

        monkeypatch.setattr(
            "polaris.cells.llm.dialogue.public.service.generate_role_response",
            fake_generate_role_response,
        )

        engine = CodeGenerationEngine("/tmp", Mock())
        task = Mock()
        task.id = "task-1"

        result = await engine._invoke_director_role_response(
            task=task,
            prompt="Create src/app.py with a tiny Python app.",
            timeout=15,
            round_label="1/1",
            round_files=["src/app.py"],
        )

        assert result["response"].startswith("```file: src/app.py")
        assert str(captured["message"]).startswith("[mode:propose]")
        assert "Create src/app.py" in str(captured["prompt_appendix"])
        assert "write_file" not in str(captured["prompt_appendix"])
        assert captured["validate_output"] is False
        assert captured["enable_cognitive"] is False
        context = captured["context"]
        assert isinstance(context, dict)
        assert context["delivery_mode"] == "propose_patch"
        assert context["disable_internal_tool_rounds"] is True


class TestBlockedModuleFunctions:
    def test_generate_fallback_code_content_blocked(self):
        with pytest.raises(CodeGenerationPolicyViolationError):
            generate_fallback_code_content("/path", "py", "task")

    def test_generate_phase_aware_fallback_content_blocked(self):
        with pytest.raises(CodeGenerationPolicyViolationError):
            generate_phase_aware_fallback_content("/path", "py", "task", "phase1")

    @pytest.mark.asyncio
    async def test_generate_bootstrap_with_llm_blocked(self):
        with pytest.raises(CodeGenerationPolicyViolationError):
            await generate_bootstrap_with_llm("/ws", "subj", "desc", "py", None)
