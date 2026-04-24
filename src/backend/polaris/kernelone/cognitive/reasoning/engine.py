"""Critical Thinking Engine - Six Questions Protocol Implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from polaris.kernelone.cognitive.perception.models import IntentChain
from polaris.kernelone.cognitive.reasoning.models import (
    Assumption,
    DevilsAdvocateResult,
    ReasoningChain,
    SixQuestionsResult,
)

if TYPE_CHECKING:
    from polaris.kernelone.llm.invocations import LLMInvoker


class CriticalThinkingEngine:
    """
    Implements the Six Questions Protocol for critical thinking.

    Q1: What assumptions am I making?
    Q2: What could make this conclusion fail?
    Q3: What might I be missing? (Devil's Advocate)
    Q4: How confident should I be? (Probability assessment)
    Q5: What would being wrong cost?
    Q6: How can I verify this?
    """

    def __init__(self, llm_invoker: LLMInvoker | None = None) -> None:
        self._assumptions: list[Assumption] = []
        self._llm = llm_invoker

    async def analyze_with_llm(
        self,
        conclusion: str,
        intent_chain: IntentChain | None,
        context: str = "",
    ) -> ReasoningChain:
        """
        LLM-powered reasoning analysis.

        Uses the LLM to extract assumptions and perform deeper analysis
        when an LLM invoker is available.

        Falls back to rule-based analysis if no LLM is configured.
        """
        if self._llm is None:
            return await self.analyze(conclusion, intent_chain, context)

        try:
            # Build prompt for assumption extraction
            from polaris.kernelone.cognitive.reasoning.prompts import EXTRACT_ASSUMPTIONS_PROMPT

            prompt = EXTRACT_ASSUMPTIONS_PROMPT.format(
                conclusion=conclusion,
                context=context or "No additional context provided.",
            )

            response = await self._llm.invoke(prompt)

            # Parse LLM response into assumptions
            assumptions = self._parse_assumptions_from_llm(response, conclusion)

            # Run remaining analysis with extracted assumptions
            failure_conditions = self._identify_failure_conditions(assumptions)
            devils_advocate = await self._devils_advocate(conclusion, assumptions, context)
            probability, uncertainty_band, knowledge_status = self._assess_probability(assumptions, devils_advocate)
            cost_of_error, severity = self._assess_cost_of_error(conclusion, intent_chain)
            verification_steps, can_verify = self._plan_verification(assumptions)

            six_questions = SixQuestionsResult(
                assumptions=assumptions,
                failure_conditions=failure_conditions,
                devils_advocate=devils_advocate,
                conclusion_probability=probability,
                uncertainty_band=uncertainty_band,
                knowledge_status=knowledge_status,
                cost_of_error=cost_of_error,
                severity=severity,
                verification_steps=verification_steps,
                can_verify=can_verify,
            )

            blockers = self._identify_blockers(six_questions)
            should_proceed = probability >= 0.7 and severity != "critical" and (can_verify or probability >= 0.9)
            confidence_level = self._classify_confidence(probability)

            return ReasoningChain(
                conclusion=conclusion,
                six_questions=six_questions,
                confidence_level=confidence_level,
                should_proceed=should_proceed,
                blockers=blockers,
            )

        except (RuntimeError, ValueError):
            # LLM failed, fall back to rule-based
            return await self.analyze(conclusion, intent_chain, context)

    def _parse_assumptions_from_llm(self, response: str, conclusion: str) -> tuple[Assumption, ...]:
        """Parse LLM response to extract assumptions."""
        assumptions = []
        lines = response.strip().split("\n")
        assumption_id = 0

        current_text: list[str] = []
        current_confidence = 0.7
        current_conditions: list[str] = []
        current_hidden = True

        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            # Look for structured patterns
            lower_line = line.lower()

            if "assumption" in lower_line or "assume" in lower_line:
                if current_text:
                    # Save previous assumption
                    assumptions.append(
                        Assumption(
                            id=f"assumpt_{assumption_id}",
                            text=" ".join(current_text),
                            confidence=current_confidence,
                            conditions_for_failure=tuple(current_conditions),
                            evidence=(),
                            is_hidden=current_hidden,
                            source="llm",
                        )
                    )
                    assumption_id += 1
                    current_text = []
                    current_conditions = []

                # Extract the assumption text
                if ":" in line:
                    current_text.append(line.split(":", 1)[1].strip())
                else:
                    current_text.append(line)

            elif "confidence" in lower_line or "certain" in lower_line:
                # Try to extract confidence value
                parts = line.split(":")
                if len(parts) > 1:
                    try:
                        val = parts[1].strip().rstrip("/10").rstrip("%")
                        current_confidence = float(val) / (10 if float(val) > 1 else 1)
                    except (ValueError, IndexError):
                        current_confidence = 0.7

            elif "condition" in lower_line or "failure" in lower_line or "would make" in lower_line:
                if ":" in line:
                    current_conditions.append(line.split(":", 1)[1].strip())

            elif "hidden" in lower_line or "explicit" in lower_line or "implicit" in lower_line:
                current_hidden = "hidden" in lower_line or "implicit" in lower_line

        # Don't forget the last assumption
        if current_text:
            assumptions.append(
                Assumption(
                    id=f"assumpt_{assumption_id}",
                    text=" ".join(current_text),
                    confidence=current_confidence,
                    conditions_for_failure=tuple(current_conditions),
                    evidence=(),
                    is_hidden=current_hidden,
                    source="llm",
                )
            )

        # If no assumptions parsed, create a default one
        if not assumptions:
            assumptions.append(
                Assumption(
                    id="assumpt_0",
                    text="LLM analysis: " + conclusion[:100],
                    confidence=0.6,
                    conditions_for_failure=("analysis may be incomplete",),
                    evidence=(),
                    is_hidden=False,
                    source="llm",
                )
            )

        return tuple(assumptions)

    async def analyze(
        self,
        conclusion: str,
        intent_chain: IntentChain | None,
        context: str = "",
    ) -> ReasoningChain:
        """Run full Six Questions analysis on a conclusion."""

        # Q1: Extract assumptions
        assumptions = await self._extract_assumptions(conclusion, context, intent_chain)
        self._assumptions = list(assumptions)

        # Q2: Identify failure conditions
        failure_conditions = self._identify_failure_conditions(assumptions)

        # Q3: Devil's advocate (if enabled)
        devils_advocate = await self._devils_advocate(conclusion, assumptions, context)

        # Q4: Probability assessment
        probability, uncertainty_band, knowledge_status = self._assess_probability(assumptions, devils_advocate)

        # Q5: Cost of error
        cost_of_error, severity = self._assess_cost_of_error(conclusion, intent_chain)

        # Q6: Verification steps
        verification_steps, can_verify = self._plan_verification(assumptions)

        six_questions = SixQuestionsResult(
            assumptions=assumptions,
            failure_conditions=failure_conditions,
            devils_advocate=devils_advocate,
            conclusion_probability=probability,
            uncertainty_band=uncertainty_band,
            knowledge_status=knowledge_status,
            cost_of_error=cost_of_error,
            severity=severity,
            verification_steps=verification_steps,
            can_verify=can_verify,
        )

        # Determine if should proceed
        blockers = self._identify_blockers(six_questions)
        should_proceed = probability >= 0.7 and severity != "critical" and (can_verify or probability >= 0.9)

        confidence_level = self._classify_confidence(probability)

        return ReasoningChain(
            conclusion=conclusion,
            six_questions=six_questions,
            confidence_level=confidence_level,
            should_proceed=should_proceed,
            blockers=blockers,
        )

    async def _extract_assumptions(
        self,
        conclusion: str,
        context: str,
        intent_chain: IntentChain | None = None,
    ) -> tuple[Assumption, ...]:
        """Q1: What assumptions am I making?"""
        assumptions = []

        # Common assumption patterns - rule-based for v1.0
        if "should" in conclusion.lower():
            assumptions.append(
                Assumption(
                    id="assumpt_1",
                    text="I assume the current approach is correct",
                    confidence=0.7,
                    conditions_for_failure=("better approach exists",),
                    evidence=(),
                    is_hidden=True,
                )
            )

        if "because" in conclusion.lower():
            assumptions.append(
                Assumption(
                    id="assumpt_2",
                    text="I assume the stated reason is complete",
                    confidence=0.6,
                    conditions_for_failure=("additional reasons exist",),
                    evidence=(),
                    is_hidden=True,
                )
            )

        if "will" in conclusion.lower():
            assumptions.append(
                Assumption(
                    id="assumpt_3",
                    text="I assume the predicted outcome will occur",
                    confidence=0.5,
                    conditions_for_failure=("unforeseen circumstances",),
                    evidence=(),
                    is_hidden=True,
                )
            )

        # Intent-based domain assumptions (P1-3 enhancement)
        if intent_chain and intent_chain.surface_intent:
            intent_type = intent_chain.surface_intent.intent_type
            intent_assumptions = {
                "modify_file": Assumption(
                    id="intent_assumpt_modify",
                    text="修改操作可能引入语法错误或逻辑错误",
                    confidence=0.7,
                    conditions_for_failure=("语法错误", "逻辑错误"),
                    evidence=(),
                    is_hidden=True,
                    source="intent_type",
                ),
                "create_file": Assumption(
                    id="intent_assumpt_create",
                    text="新文件可能与现有架构规范不一致",
                    confidence=0.5,
                    conditions_for_failure=("架构不一致", "命名不规范"),
                    evidence=(),
                    is_hidden=True,
                    source="intent_type",
                ),
                "delete_file": Assumption(
                    id="intent_assumpt_delete",
                    text="删除操作可能影响其他模块依赖",
                    confidence=0.8,
                    conditions_for_failure=("模块依赖断裂", "未检测到的引用"),
                    evidence=(),
                    is_hidden=True,
                    source="intent_type",
                ),
                "read_file": Assumption(
                    id="intent_assumpt_read",
                    text="读取的内容可能已被外部修改",
                    confidence=0.3,
                    conditions_for_failure=("内容已过期", "并发修改"),
                    evidence=(),
                    is_hidden=True,
                    source="intent_type",
                ),
                "execute_tool": Assumption(
                    id="intent_assumpt_execute",
                    text="工具执行可能产生非预期的副作用",
                    confidence=0.6,
                    conditions_for_failure=("副作用", "非预期结果"),
                    evidence=(),
                    is_hidden=True,
                    source="intent_type",
                ),
            }
            if intent_type in intent_assumptions:
                assumptions.append(intent_assumptions[intent_type])

        if not assumptions:
            assumptions.append(
                Assumption(
                    id="assumpt_0",
                    text="No explicit assumptions detected",
                    confidence=1.0,
                    conditions_for_failure=(),
                    evidence=(),
                    is_hidden=False,
                )
            )

        return tuple(assumptions)

    def _identify_failure_conditions(
        self,
        assumptions: tuple[Assumption, ...],
    ) -> tuple[str, ...]:
        """Q2: What could make this conclusion fail?"""
        conditions = []
        for assumption in assumptions:
            for condition in assumption.conditions_for_failure:
                conditions.append(f"If assumption '{assumption.text}' fails: {condition}")
        return tuple(conditions)

    async def _devils_advocate(
        self,
        conclusion: str,
        assumptions: tuple[Assumption, ...],
        context: str,
    ) -> DevilsAdvocateResult | None:
        """Q3: What might I be missing?"""
        # Simple rule-based for v1.0
        counter_args = []

        if len(assumptions) < 2:
            counter_args.append("Limited assumptions considered - may be missing alternatives")

        if "should" in conclusion.lower():
            counter_args.append("'Should' implies value judgment - may not be universally true")

        if "will" in conclusion.lower():
            counter_args.append("Future prediction - unforeseen circumstances may intervene")

        if not counter_args:
            return None

        return DevilsAdvocateResult(
            counter_arguments=tuple(counter_args),
            strength=0.5,
            remaining_uncertainty=0.3,
        )

    def _assess_probability(
        self,
        assumptions: tuple[Assumption, ...],
        devils_advocate: DevilsAdvocateResult | None,
    ) -> tuple[float, tuple[float, float], str]:
        """Q4: How confident should I be?"""
        if not assumptions:
            return 0.5, (0.3, 0.7), "guessed"

        avg_confidence = sum(a.confidence for a in assumptions) / len(assumptions)

        # Adjust for devil's advocate
        if devils_advocate:
            avg_confidence *= 1.0 - devils_advocate.remaining_uncertainty * 0.3

        lower = max(0.0, avg_confidence - 0.2)
        upper = min(1.0, avg_confidence + 0.1)

        if avg_confidence >= 0.8:
            status = "known"
        elif avg_confidence >= 0.6:
            status = "inferred"
        else:
            status = "guessed"

        return avg_confidence, (lower, upper), status

    def _assess_cost_of_error(
        self,
        conclusion: str,
        intent_chain: IntentChain | None,
    ) -> tuple[str, str]:
        """Q5: What would being wrong cost?"""
        intent_type = (
            intent_chain.surface_intent.intent_type if intent_chain and intent_chain.surface_intent else "unknown"
        )

        cost_map = {
            "read_file": ("Minor - wasted time reading wrong info", "low"),
            "create_file": ("Must delete created file, wastes time", "medium"),
            "modify_file": ("Must restore original, potential data loss", "high"),
            "delete_file": ("Data loss - may be unrecoverable", "critical"),
            "explain": ("Misleading information, loss of trust", "medium"),
            "search": ("Wasted search time", "low"),
            "plan": ("Wasted planning effort, may misdirect", "medium"),
            "test": ("False test results", "medium"),
            "review": ("Missed issues, false confidence", "high"),
            "execute_command": ("Unintended command execution", "critical"),
        }

        return cost_map.get(intent_type, ("Unknown cost", "medium"))

    def _plan_verification(
        self,
        assumptions: tuple[Assumption, ...],
    ) -> tuple[tuple[str, ...], bool]:
        """Q6: How can I verify this?"""
        steps = []
        for assumption in assumptions:
            # Primary: use explicit evidence if available
            if assumption.evidence:
                steps.append(f"Verify: {assumption.text}")
            # Fallback: use conditions for failure as verification points
            elif assumption.conditions_for_failure:
                for condition in assumption.conditions_for_failure:
                    steps.append(f"Check: {condition}")

        return tuple(steps), len(steps) > 0

    def _identify_blockers(
        self,
        six_questions: SixQuestionsResult,
    ) -> tuple[str, ...]:
        """Identify issues that block proceeding."""
        blockers = []

        if six_questions.severity == "critical":
            blockers.append("Critical severity - requires explicit approval")

        if not six_questions.can_verify:
            blockers.append("Cannot verify - high risk")

        if six_questions.conclusion_probability < 0.5:
            blockers.append("Low probability - insufficient confidence")

        return tuple(blockers)

    def _classify_confidence(self, probability: float) -> str:
        if probability >= 0.8:
            return "high"
        elif probability >= 0.6:
            return "medium"
        elif probability >= 0.4:
            return "low"
        return "unknown"
