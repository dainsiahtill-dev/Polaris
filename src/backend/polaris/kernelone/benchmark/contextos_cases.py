"""ContextOS Reliability & Stability Benchmark Cases

Dedicated benchmark suite for verifying ContextOS behavior:
- Long session compression (长会话压缩)
- Context desynchronization detection (上下文失焦检测)
- Incorrect truncation detection (错误截断检测)
- Context loss detection (上下文丢失检测)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from polaris.kernelone.benchmark.unified_models import JudgeConfig, UnifiedBenchmarkCase


@dataclass(frozen=True)
class ContextOSBenchmarkCases:
    """ContextOS-specific benchmark cases"""

    @staticmethod
    def long_session_compression(workspace: str = ".") -> list[UnifiedBenchmarkCase]:
        """Generate long session compression test cases

        Tests that ContextOS properly handles sessions with 50+ turns
        without infinite context growth.
        """
        cases = []
        for i in range(1, 4):
            cases.append(
                UnifiedBenchmarkCase(
                    case_id=f"contextos_long_session_{i:03d}",
                    role="director",
                    title=f"Long Session Compression Test {i}",
                    description=f"Verification: ContextOS compression in {i * 30}+ turns session",
                    prompt="Simulate a long session with multiple tool calls and verify compression works correctly",
                    workspace_fixture="long_session" if i == 1 else "",
                    budget_conditions=ContextOSBenchmarkCases._long_session_budget(),
                    judge=JudgeConfig(
                        validators=(
                            "contextos_long_session_compression",
                            "contextos_incorrect_truncation",
                        ),
                        score_threshold=0.85,
                        mode="strategy",
                    ),
                    tags=("contextos", "long_session", "compression"),
                )
            )
        return cases

    @staticmethod
    def context_desynchronization(workspace: str = ".") -> list[UnifiedBenchmarkCase]:
        """Generate context desynchronization test cases

        Tests that ContextOS maintains synchronization with conversation state
        across turns.
        """
        cases = []
        for i in range(1, 4):
            cases.append(
                UnifiedBenchmarkCase(
                    case_id=f"contextos_desync_{i:03d}",
                    role="director",
                    title=f"Context Desynchronization Test {i}",
                    description=f"Verification: ContextOS token tracking across {i * 20}+ turns",
                    prompt="Verify that context tokens are properly tracked across multiple turns",
                    workspace_fixture="long_session" if i == 1 else "",
                    budget_conditions=ContextOSBenchmarkCases._standard_budget(),
                    judge=JudgeConfig(
                        validators=(
                            "contextos_desynchronization",
                            "contextos_incorrect_truncation",
                        ),
                        score_threshold=0.90,
                        mode="strategy",
                    ),
                    tags=("contextos", "desynchronization", "token_tracking"),
                )
            )
        return cases

    @staticmethod
    def incorrect_truncation(workspace: str = ".") -> list[UnifiedBenchmarkCase]:
        """Generate incorrect truncation test cases

        Tests that ContextOS doesn't truncate context inappropriately,
        losing critical conversation history.
        """
        cases = []
        for i in range(1, 4):
            cases.append(
                UnifiedBenchmarkCase(
                    case_id=f"contextos_truncation_{i:03d}",
                    role="director",
                    title=f"Incorrect Truncation Test {i}",
                    description="Verification: ContextOS truncation preserves important context",
                    prompt="Verify that truncation doesn't remove critical conversation history",
                    workspace_fixture="long_session" if i == 1 else "",
                    budget_conditions=ContextOSBenchmarkCases._standard_budget(),
                    judge=JudgeConfig(
                        validators=("contextos_incorrect_truncation",),
                        score_threshold=0.80,
                        mode="strategy",
                    ),
                    tags=("contextos", "truncation", "history_preservation"),
                )
            )
        return cases

    @staticmethod
    def context_loss(workspace: str = ".") -> list[UnifiedBenchmarkCase]:
        """Generate context loss test cases

        Tests that ContextOS doesn't lose entire turns or significant content.
        """
        cases = [
            UnifiedBenchmarkCase(
                case_id="contextos_loss_001",
                role="director",
                title="Context Loss Test 1",
                description="Verification: ContextOS never has null context_tokens",
                prompt="Verify that ContextOS always tracks context tokens (no null values)",
                workspace_fixture="long_session",
                budget_conditions=ContextOSBenchmarkCases._standard_budget(),
                judge=JudgeConfig(
                    validators=("contextos_loss",),
                    required_output_substrings=("context_tokens_before", "context_tokens_after"),
                    score_threshold=0.95,
                    mode="strategy",
                ),
                tags=("contextos", "loss", "null_prevention"),
            ),
        ]
        return cases

    @staticmethod
    def all_benchmarks(workspace: str = ".") -> list[UnifiedBenchmarkCase]:
        """Get all ContextOS benchmark cases"""
        all_cases: list[UnifiedBenchmarkCase] = []
        all_cases.extend(ContextOSBenchmarkCases.long_session_compression(workspace))
        all_cases.extend(ContextOSBenchmarkCases.context_desynchronization(workspace))
        all_cases.extend(ContextOSBenchmarkCases.incorrect_truncation(workspace))
        all_cases.extend(ContextOSBenchmarkCases.context_loss(workspace))
        return all_cases

    @staticmethod
    def _long_session_budget() -> Any:
        """Budget for long session tests"""
        from polaris.kernelone.benchmark.unified_models import BudgetConditions

        return BudgetConditions(
            max_tokens=500_000,
            max_turns=100,
            max_wall_time_seconds=600.0,
        )

    @staticmethod
    def _standard_budget() -> Any:
        """Standard budget for tests"""
        from polaris.kernelone.benchmark.unified_models import BudgetConditions

        return BudgetConditions(
            max_tokens=200_000,
            max_turns=50,
            max_wall_time_seconds=300.0,
        )


# -----------------------------------------------------------------------------
# Pre-built Case Instances
# -----------------------------------------------------------------------------


LONG_SESSION_CASES = ContextOSBenchmarkCases.long_session_compression()
CONTEXT_DESCYNC_CASES = ContextOSBenchmarkCases.context_desynchronization()
INCORRECT_TRUNCATION_CASES = ContextOSBenchmarkCases.incorrect_truncation()
CONTEXT_LOSS_CASES = ContextOSBenchmarkCases.context_loss()

ALL_CONTEXTOS_CASES = ContextOSBenchmarkCases.all_benchmarks()


__all__ = [
    "ALL_CONTEXTOS_CASES",
    "CONTEXT_DESCYNC_CASES",
    "CONTEXT_LOSS_CASES",
    "INCORRECT_TRUNCATION_CASES",
    "LONG_SESSION_CASES",
    # Case generators
    "ContextOSBenchmarkCases",
]
