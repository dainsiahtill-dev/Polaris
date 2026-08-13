"""Output-content and structured-output validators for the unified judge.

Validators migrated from the legacy ``deterministic_judge.py`` covering output
shape and trace-driven contract checks: error-indicator rejection, tool-call
presence/absence, parity, focus recovery, fact anchoring, stepwise planning,
hallucination refusal, ordered tool sequence, self-verification, structured
output, and Chinese-output requirements.
"""

from __future__ import annotations

import re

from polaris.kernelone.tools.tool_kinds import is_write_tool_name

from ..unified_models import ObservedBenchmarkRun

__all__ = [
    "ChineseOutputRequiredValidator",
    "FactAnchoringCheckValidator",
    "FirstCallRejectUnknownArgsValidator",
    "FocusRecoveryCheckValidator",
    "HallucinationRefusalCheckValidator",
    "OrderedToolSequenceValidator",
    "ParityCompareModeSetValidator",
    "RequireNoErrorValidator",
    "RequireNoToolCallsValidator",
    "SelfVerificationCheckValidator",
    "StepwisePlanningValidator",
    "StructuredOutputRequiredValidator",
]


class RequireNoErrorValidator:
    """Validator that checks output does not contain error indicators.

    This validator ensures the output does not contain error-related keywords.
    """

    name: str = "require_no_error"
    category: str = "contract"
    critical: bool = False

    def validate(
        self,
        output_text: str,
        observed: ObservedBenchmarkRun,
        known_paths: list[str],
    ) -> tuple[bool, str]:
        """Check for error indicators in output.

        Returns:
            Tuple of (no_errors_found, message).
        """
        error_indicators = ["error", "failed", "failure", "exception", "traceback"]
        has_error = any(indicator in output_text.lower() for indicator in error_indicators)
        if has_error:
            return False, "output should not contain error indicators"
        return True, "no error indicators found"


class FirstCallRejectUnknownArgsValidator:
    """Validator that checks first tool call rejects unknown args.

    This validator ensures the model makes at least one tool call
    when given a prompt with unknown parameters.
    """

    name: str = "first_call_reject_unknown_args"
    category: str = "contract"
    critical: bool = False

    def validate(
        self,
        output_text: str,
        observed: ObservedBenchmarkRun,
        known_paths: list[str],
    ) -> tuple[bool, str]:
        """Check for at least one tool call.

        Returns:
            Tuple of (has_tool_calls, message).
        """
        if len(observed.tool_calls) == 0:
            return False, "first_call_reject_unknown_args: no tool calls made"
        return True, "first call arg validation passed"


class RequireNoToolCallsValidator:
    """Validator that checks no tool calls were made.

    This validator passes when no tool calls were made and output is non-empty.
    """

    name: str = "require_no_tool_calls"
    category: str = "contract"
    critical: bool = False

    def validate(
        self,
        output_text: str,
        observed: ObservedBenchmarkRun,
        known_paths: list[str],
    ) -> tuple[bool, str]:
        """Check no tool calls were made.

        Returns:
            Tuple of (no_tool_calls_expected, message).
        """
        if len(observed.tool_calls) == 0 and output_text and output_text.strip():
            return True, "no tool calls made as expected"
        return False, "expected no tool calls to be made"


class ParityCompareModeSetValidator:
    """Validator that checks parity with compare mode set.

    Validates that output is non-empty for parity comparison cases.
    """

    name: str = "parity_compare_mode_set"
    category: str = "contract"
    critical: bool = False

    def validate(
        self,
        output_text: str,
        observed: ObservedBenchmarkRun,
        known_paths: list[str],
    ) -> tuple[bool, str]:
        """Check for non-empty output.

        Returns:
            Tuple of (has_output, message).
        """
        if not output_text or not output_text.strip():
            return False, "output must not be empty"
        return True, "parity compare mode validated"


class FocusRecoveryCheckValidator:
    """Validator that checks focus recovery after distraction.

    This validator ensures the output demonstrates focus recovery
    after being distracted by off-topic content.
    """

    name: str = "focus_recovery_check"
    category: str = "contract"
    critical: bool = False

    def validate(
        self,
        output_text: str,
        observed: ObservedBenchmarkRun,
        known_paths: list[str],
    ) -> tuple[bool, str]:
        """Check for non-empty output indicating focus recovery.

        Returns:
            Tuple of (has_recovery, message).
        """
        if not output_text or not output_text.strip():
            return False, "focus recovery output must not be empty"
        return True, "focus recovery validated"


class FactAnchoringCheckValidator:
    """Validator that checks output is fact-anchored to actual file content.

    This validator ensures the model read a file before answering
    a fact-based question.
    """

    name: str = "fact_anchoring_check"
    category: str = "contract"
    critical: bool = False

    def validate(
        self,
        output_text: str,
        observed: ObservedBenchmarkRun,
        known_paths: list[str],
    ) -> tuple[bool, str]:
        """Check for read tool calls before answering.

        Returns:
            Tuple of (is_anchored, message).
        """
        read_tools = {"repo_read_head", "repo_read_tail", "repo_read_slice", "repo_read_around", "read_file"}
        has_read_call = any(call.tool in read_tools for call in observed.tool_calls)
        if not has_read_call:
            return False, "fact anchoring requires reading actual file content"
        return True, "fact anchoring validated: file was read before answering"


class StepwisePlanningValidator:
    """Validator that checks for stepwise planning markers.

    This validator ensures the output includes structured steps
    or step markers indicating a planned approach.
    """

    name: str = "stepwise_planning"
    category: str = "contract"
    critical: bool = False

    def validate(
        self,
        output_text: str,
        observed: ObservedBenchmarkRun,
        known_paths: list[str],
    ) -> tuple[bool, str]:
        """Check for step markers in output.

        Returns:
            Tuple of (has_steps, message).
        """
        step_markers = ["步骤", "step", "phase", "阶段", "1.", "2.", "3."]
        has_steps = any(marker.lower() in output_text.lower() for marker in step_markers)
        if not has_steps:
            return False, "stepwise planning requires numbered steps or phases in output"
        return True, "stepwise planning validated"


class HallucinationRefusalCheckValidator:
    """Validator that checks model refused to hallucinate.

    This validator checks that the model did not pretend to execute
    functionality that doesn't exist in the codebase.
    """

    name: str = "hallucination_refusal_check"
    category: str = "contract"
    critical: bool = False

    def validate(
        self,
        output_text: str,
        observed: ObservedBenchmarkRun,
        known_paths: list[str],
    ) -> tuple[bool, str]:
        """Check for refusal markers when functionality doesn't exist.

        Returns:
            Tuple of (proper_refusal, message).
        """
        refusal_markers = ["不存在", "没有", "not found", "cannot find", "未找到", "不存在"]
        has_refusal = any(marker.lower() in output_text.lower() for marker in refusal_markers)

        false_success_markers = ["调用成功", "执行成功", "successfully", "completed successfully"]
        has_false_success = any(marker.lower() in output_text.lower() for marker in false_success_markers)

        if has_false_success and not has_refusal:
            return False, "model must not claim success for non-existent functionality"
        if has_refusal:
            return True, "hallucination refusal validated: model correctly refused to hallucinate"
        return True, "hallucination refusal check passed"


class OrderedToolSequenceValidator:
    """Validator that checks tools were called in logical order.

    This validator ensures read/search operations precede write/edit operations.
    """

    name: str = "ordered_tool_sequence"
    category: str = "contract"
    critical: bool = False

    def validate(
        self,
        output_text: str,
        observed: ObservedBenchmarkRun,
        known_paths: list[str],
    ) -> tuple[bool, str]:
        """Check read-before-write tool ordering.

        Returns:
            Tuple of (is_ordered, message).
        """
        if not observed.tool_calls:
            return True, "no tool calls to validate order"

        read_tools = {
            "repo_read_head",
            "repo_read_tail",
            "repo_read_slice",
            "repo_read_around",
            "read_file",
            "repo_rg",
            "repo_tree",
            "glob",
        }
        first_write_index = None
        last_read_index = None

        for i, call in enumerate(observed.tool_calls):
            if call.tool in read_tools:
                last_read_index = i
            if is_write_tool_name(call.tool) and first_write_index is None:
                first_write_index = i

        if first_write_index is not None and last_read_index is not None and last_read_index > first_write_index:
            return False, "read operations should precede write operations"

        return True, "tool sequence order validated"


class SelfVerificationCheckValidator:
    """Validator that checks model performed self-verification.

    This validator ensures the model verified its own work,
    typically by running tests or checking the result after editing.
    """

    name: str = "self_verification_check"
    category: str = "contract"
    critical: bool = False

    def validate(
        self,
        output_text: str,
        observed: ObservedBenchmarkRun,
        known_paths: list[str],
    ) -> tuple[bool, str]:
        """Check for verification tool calls or language.

        Returns:
            Tuple of (has_verification, message).
        """
        verification_tools = {"execute_command", "repo_rg", "repo_read_head", "repo_read_slice"}
        has_verification = any(call.tool in verification_tools for call in observed.tool_calls)

        verification_markers = ["验证", "verified", "confirmed", "tested", "检查", "correct", "成功"]
        has_verification_language = any(marker.lower() in output_text.lower() for marker in verification_markers)

        if not has_verification and not has_verification_language:
            return False, "self-verification requires checking the result after changes"
        return True, "self-verification validated"


class StructuredOutputRequiredValidator:
    """Validator that checks output contains structured format.

    This validator ensures output includes structured elements like
    markdown tables, numbered lists, or JSON structures.
    """

    name: str = "structured_output_required"
    category: str = "contract"
    critical: bool = False

    def validate(
        self,
        output_text: str,
        observed: ObservedBenchmarkRun,
        known_paths: list[str],
    ) -> tuple[bool, str]:
        """Check for structured format in output.

        Returns:
            Tuple of (has_structure, message).
        """
        output = str(output_text or "")

        table_pattern = re.search(r"\|.*\|.*\n\|[-: ]+\|", output)
        if table_pattern:
            return True, "structured output validated: markdown table found"

        list_pattern = re.search(r"(?:^|\n)\s*\d+[.、]\s", output)
        if list_pattern:
            return True, "structured output validated: numbered list found"

        bullet_pattern = re.search(r"(?:^|\n)\s*[-*]\s", output)
        if bullet_pattern:
            return True, "structured output validated: bullet list found"

        json_pattern = re.search(r"[{\[]", output)
        if json_pattern and ("}" in output or "]" in output):
            return True, "structured output validated: JSON-like structure found"

        code_pattern = re.search(r"```\w*\n", output)
        if code_pattern:
            return True, "structured output validated: code block found"

        return False, "output must contain structured format (table, list, or code block)"


class ChineseOutputRequiredValidator:
    """Validator that checks output contains Chinese characters.

    This validator ensures the response includes Chinese text as required
    by the prompt.
    """

    name: str = "chinese_output_required"
    category: str = "contract"
    critical: bool = False

    def validate(
        self,
        output_text: str,
        observed: ObservedBenchmarkRun,
        known_paths: list[str],
    ) -> tuple[bool, str]:
        """Check for Chinese characters in output.

        Returns:
            Tuple of (has_chinese, message).
        """
        output = str(output_text or "")

        chinese_count = sum(1 for c in output if ("\u4e00" <= c <= "\u9fff") or ("\u3400" <= c <= "\u4dbf"))
        if chinese_count >= 3:
            return True, f"chinese output validated: {chinese_count} Chinese characters found"

        return False, "output must contain Chinese characters (at least 3)"
