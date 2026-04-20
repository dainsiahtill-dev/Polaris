"""Knowledge Precipitation - Distills learnings into persistent knowledge."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Pattern rules for knowledge extraction based on intent types
PATTERN_RULES: dict[str, dict[str, str]] = {
    "modify_file": {
        "rule": "Always verify file syntax and structure after modification",
        "boundary": "Applies to interpreted languages; compiled languages need separate compilation check",
    },
    "delete_file": {
        "rule": "Verify no dependent references exist before deletion",
        "boundary": "Requires full dependency graph analysis; deletion may have cascading effects",
    },
    "create_file": {
        "rule": "New files should follow project conventions and include necessary imports",
        "boundary": "Template selection depends on project structure; not all file types have templates",
    },
    "execute_command": {
        "rule": "Validate command safety and expected output format before execution",
        "boundary": "Shell commands can have side effects; sandbox when possible",
    },
    "read_file": {
        "rule": "Verify file exists and is accessible before reading",
        "boundary": "Binary files may not be readable as text; encoding matters",
    },
    "write_content": {
        "rule": "Confirm target location exists and has appropriate permissions",
        "boundary": "Overwriting existing content is destructive; backup when possible",
    },
}

# Success/failure pattern detection
SUCCESS_PATTERNS = {
    "modify_file": [
        r"file.*modified.*successfully",
        r"changes.*applied",
        r"no syntax error",
    ],
    "delete_file": [
        r"file.*deleted",
        r"removed.*successfully",
        r"deletion.*complete",
    ],
    "create_file": [
        r"file.*created",
        r"new file.*generated",
        r"file.*written",
    ],
}

FAILURE_PATTERNS = {
    "modify_file": [
        r"syntax error",
        r"failed to write",
        r"permission denied",
        r"file not found",
    ],
    "delete_file": [
        r"cannot delete",
        r"file in use",
        r"permission denied",
        r"directory not empty",
    ],
    "create_file": [
        r"failed to create",
        r"permission denied",
        r"invalid path",
    ],
}


@dataclass(frozen=True)
class PrecipitatedKnowledge:
    """Knowledge distilled from experience."""

    rules_learned: tuple[str, ...] = field(default_factory=tuple)
    patterns_identified: tuple[str, ...] = field(default_factory=tuple)
    boundaries_updated: tuple[str, ...] = field(default_factory=tuple)
    knowledge_gaps: tuple[str, ...] = field(default_factory=tuple)


class KnowledgePrecipitation:
    """Distills learnings from task outcomes into persistent knowledge store."""

    def __init__(self) -> None:
        # In-memory cache of precipitated knowledge for current session
        self._session_knowledge: list[PrecipitatedKnowledge] = []

    def precipitate(
        self,
        task_result: dict[str, Any],
        reflection_output: Any | None = None,
    ) -> PrecipitatedKnowledge:
        """Precipitate knowledge from task execution and reflection.

        Analyzes task outcomes and reflection to extract:
        - Generalizable rules
        - Recurring patterns
        - Knowledge boundaries
        - Identified gaps

        Args:
            task_result: Dictionary containing task execution result
                Expected keys: intent_type, success, error_message (optional), quality (optional)
            reflection_output: Optional reflection output from MetaCognitionEngine

        Returns:
            PrecipitatedKnowledge with distilled learnings
        """
        intent_type = task_result.get("intent_type", "unknown")
        success = task_result.get("success", False)
        error_message = task_result.get("error_message", "")
        output_content = task_result.get("output", task_result.get("content", ""))

        rules_learned: list[str] = []
        patterns_identified: list[str] = []
        boundaries_updated: list[str] = []
        knowledge_gaps: list[str] = []

        # Use pattern detection to enhance success/failure determination
        detected_success = self._detect_success_pattern(intent_type, output_content)
        detected_failure = self._detect_failure_pattern(intent_type, error_message)

        # Combine explicit success with pattern detection
        # If success is explicitly True, trust it; otherwise use pattern detection
        if not success:
            if detected_failure:
                success = False
            elif detected_success:
                success = True

        # Extract rule from outcome
        if intent_type in PATTERN_RULES:
            pattern = PATTERN_RULES[intent_type]
            rule = pattern["rule"]
            boundary = pattern["boundary"]

            if success:
                rules_learned.append(f"RULE CONFIRMED: {rule}")
                patterns_identified.append(f"{intent_type}: successful execution pattern")
            else:
                rules_learned.append(f"RULE VIOLATED: {rule}")
                boundaries_updated.append(f"Boundary refined: {boundary}")

                # Identify specific failure pattern
                failure_type = self._classify_failure(intent_type, error_message)
                if failure_type:
                    patterns_identified.append(f"FAILURE PATTERN: {failure_type}")
                    knowledge_gaps.append(f"Need to handle: {failure_type}")

        # Extract from reflection output if available
        if reflection_output:
            self._extract_from_reflection(reflection_output, rules_learned, patterns_identified, knowledge_gaps)

        # Detect recurring patterns
        self._session_knowledge.append(
            PrecipitatedKnowledge(
                rules_learned=tuple(rules_learned),
                patterns_identified=tuple(patterns_identified),
                boundaries_updated=tuple(boundaries_updated),
                knowledge_gaps=tuple(knowledge_gaps),
            )
        )

        # Identify cross-session patterns
        if len(self._session_knowledge) > 1:
            recurring = self._find_recurring_patterns()
            patterns_identified.extend(recurring)

        return PrecipitatedKnowledge(
            rules_learned=tuple(rules_learned),
            patterns_identified=tuple(patterns_identified),
            boundaries_updated=tuple(boundaries_updated),
            knowledge_gaps=tuple(knowledge_gaps),
        )

    def _detect_success_pattern(self, intent_type: str, output: str) -> bool:
        """Detect success from output content using success patterns.

        Args:
            intent_type: The intent type to check patterns for
            output: The output content to match against

        Returns:
            True if any success pattern matches
        """
        if not output or intent_type not in SUCCESS_PATTERNS:
            return False

        return any(re.search(pattern, output, re.IGNORECASE) for pattern in SUCCESS_PATTERNS[intent_type])

    def _detect_failure_pattern(self, intent_type: str, error_message: str) -> bool:
        """Detect failure from error message using failure patterns.

        Args:
            intent_type: The intent type to check patterns for
            error_message: The error message to match against

        Returns:
            True if any failure pattern matches
        """
        if not error_message or intent_type not in FAILURE_PATTERNS:
            return False

        error_lower = error_message.lower()
        return any(re.search(pattern, error_lower, re.IGNORECASE) for pattern in FAILURE_PATTERNS[intent_type])

    def _classify_failure(self, intent_type: str, error_message: str) -> str | None:
        """Classify failure type based on error message."""
        if not error_message:
            return None

        error_lower = error_message.lower()

        if "syntax" in error_lower:
            return f"{intent_type}: syntax validation failure"
        if "permission" in error_lower:
            return f"{intent_type}: permission/access failure"
        if "not found" in error_lower:
            return f"{intent_type}: resource not found"
        if "timeout" in error_lower:
            return f"{intent_type}: operation timeout"
        if "conflict" in error_lower:
            return f"{intent_type}: resource conflict"

        return f"{intent_type}: unspecified failure ({error_message[:50]})"

    def _extract_from_reflection(
        self,
        reflection_output: Any,
        rules: list[str],
        patterns: list[str],
        gaps: list[str],
    ) -> None:
        """Extract knowledge from reflection output."""
        # Handle ReflectionOutput from meta_cognition (polaris.kernelone.cognitive.reasoning.meta_cognition.ReflectionOutput)
        if hasattr(reflection_output, "rules_learned"):
            learned = reflection_output.rules_learned
            if isinstance(learned, (list, tuple)):
                rules.extend(f"Rule: {rule}" for rule in learned)

        if hasattr(reflection_output, "patterns_identified"):
            reflection_patterns = reflection_output.patterns_identified
            if isinstance(reflection_patterns, (list, tuple)):
                patterns.extend(f"Reflection: {p}" for p in reflection_patterns)

        if hasattr(reflection_output, "knowledge_gaps"):
            reflection_gaps = reflection_output.knowledge_gaps
            if isinstance(reflection_gaps, (list, tuple)):
                gaps.extend(str(g) for g in reflection_gaps)

        # Handle legacy "lessons_learned" format
        if hasattr(reflection_output, "lessons_learned"):
            lessons = reflection_output.lessons_learned
            if isinstance(lessons, (list, tuple)):
                rules.extend(f"Lesson: {lesson}" for lesson in lessons)

        # Handle legacy "patterns" format
        if hasattr(reflection_output, "patterns") and not hasattr(reflection_output, "patterns_identified"):
            reflection_patterns = reflection_output.patterns
            if isinstance(reflection_patterns, (list, tuple)):
                patterns.extend(f"Reflection: {p}" for p in reflection_patterns)

    def _find_recurring_patterns(self) -> list[str]:
        """Find patterns that appear across multiple precipitations."""
        if len(self._session_knowledge) < 2:
            return []

        # Count pattern occurrences
        pattern_counts: dict[str, int] = {}
        for knowledge in self._session_knowledge:
            for pattern in knowledge.patterns_identified:
                pattern_counts[pattern] = pattern_counts.get(pattern, 0) + 1

        # Return patterns that appear more than once
        recurring = [f"RECURRING: {p} (seen {count}x)" for p, count in pattern_counts.items() if count > 1]
        return recurring

    def get_relevant_knowledge(self, intent_type: str, context: dict[str, Any] | None = None) -> PrecipitatedKnowledge:
        """Retrieve knowledge relevant to current intent type.

        Args:
            intent_type: The intent type to find relevant knowledge for
            context: Optional context including role_id, session_id, etc.

        Returns:
            PrecipitatedKnowledge with knowledge relevant to the intent type
        """
        relevant_rules: list[str] = []
        relevant_patterns: list[str] = []
        relevant_boundaries: list[str] = []
        relevant_gaps: list[str] = []

        # Get rules for this intent type
        if intent_type in PATTERN_RULES:
            pattern_info = PATTERN_RULES[intent_type]
            relevant_rules.append(f"Known rule: {pattern_info['rule']}")
            relevant_boundaries.append(f"Known boundary: {pattern_info['boundary']}")

        # Find patterns from session history
        for knowledge in self._session_knowledge:
            for rule_item in knowledge.rules_learned:
                if intent_type in rule_item.lower():
                    relevant_rules.append(rule_item)

            for pattern_item in knowledge.patterns_identified:
                if intent_type in pattern_item.lower():
                    relevant_patterns.append(pattern_item)

            for boundary in knowledge.boundaries_updated:
                if intent_type in boundary.lower():
                    relevant_boundaries.append(boundary)

            for gap in knowledge.knowledge_gaps:
                if intent_type in gap.lower():
                    relevant_gaps.append(gap)

        # Add known gaps for this intent type
        if intent_type in PATTERN_RULES:
            pattern_info = PATTERN_RULES[intent_type]
            relevant_gaps.append(f"Verify: {pattern_info['boundary']}")

        return PrecipitatedKnowledge(
            rules_learned=tuple(set(relevant_rules)),
            patterns_identified=tuple(set(relevant_patterns)),
            boundaries_updated=tuple(set(relevant_boundaries)),
            knowledge_gaps=tuple(set(relevant_gaps)),
        )
