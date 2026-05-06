# SHIM: mig-application-batch1 — migration shim pending full Cell migration (2026-03-20)
"""Regression tests for confirmed defects in application/orchestration module.

M2: qa_orchestrator.py plan_audit() string evidence_paths iterates over characters
    instead of treating the string as a single path when evidence_paths is a str.

Covers:
    - M2: string argument to evidence_paths must not be iterated per-character
"""

from __future__ import annotations

from polaris.application.orchestration.qa_orchestrator import (
    QaAuditConfig,
    QaOrchestrator,
)


class TestQaOrchestratorEvidencePathsRegression:
    """Regression tests for plan_audit evidence_paths handling."""

    def test_plan_audit_string_evidence_paths_not_character_iterated(self) -> None:
        """Verify plan_audit does not treat a string evidence_paths as an iterable of chars.

        Bug (M2): The original code was:
            merged_evidence = tuple(str(p) for p in evidence_paths if str(p).strip())
        When evidence_paths="path/to/file", this iterates over characters, producing
        ('p','a','t','h','/','t','o','/','f','i','l','e').

        Fix: evidence_paths should be treated as a single path (wrapped in a tuple),
        or the caller should pass a list. The fixed code should handle both cases.
        """
        config = QaAuditConfig(workspace="/tmp")
        orch = QaOrchestrator(config)

        result = orch.plan_audit(task_id="t", evidence_paths="path/to/file")  # type: ignore[arg-type]  # testing str input that triggers the per-char iteration bug

        evidence = result["evidence_paths"]

        # Must NOT be a tuple of single characters from the string
        assert evidence != ("p", "a", "t", "h", "/", "t", "o", "/", "f", "i", "l", "e"), (
            f"BUG M2: evidence_paths was iterated per character: {evidence!r}"
        )

        # Must be either an empty tuple or a tuple containing the full string as one element
        assert evidence == () or evidence == ("path/to/file",) or evidence == ("path/to/file",), (
            f"evidence_paths should be () or a single-element tuple, got: {evidence!r}"
        )

    def test_plan_audit_list_evidence_paths_unchanged(self) -> None:
        """Verify list evidence_paths still works correctly (regression guard)."""
        config = QaAuditConfig(workspace="/tmp")
        orch = QaOrchestrator(config)

        paths = ["src/main.py", "tests/test_main.py"]
        result = orch.plan_audit(task_id="t", evidence_paths=paths)  # type: ignore[arg-type]

        evidence = result["evidence_paths"]
        assert evidence == ("src/main.py", "tests/test_main.py")

    def test_plan_audit_empty_string_evidence_paths(self) -> None:
        """Verify empty-string evidence_paths does not produce an empty-char tuple."""
        config = QaAuditConfig(workspace="/tmp")
        orch = QaOrchestrator(config)

        result = orch.plan_audit(task_id="t", evidence_paths="")  # type: ignore[arg-type]

        evidence = result["evidence_paths"]
        # Empty string stripped yields nothing; the tuple must be empty
        assert evidence == (), f"Empty string evidence_paths should yield (), got: {evidence!r}"
        # Must NOT be ('e',) which would happen if empty string is iterated
        assert "e" not in evidence, f"BUG M2: evidence_paths contains char 'e' from empty string: {evidence!r}"

    def test_plan_audit_none_evidence_paths_uses_config_default(self) -> None:
        """Verify None evidence_paths falls back to config default."""
        config = QaAuditConfig(workspace="/tmp", evidence_paths=("default/path",))
        orch = QaOrchestrator(config)

        result = orch.plan_audit(task_id="t", evidence_paths=None)

        evidence = result["evidence_paths"]
        assert evidence == ("default/path",)
