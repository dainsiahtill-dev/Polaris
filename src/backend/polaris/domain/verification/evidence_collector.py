"""Evidence Collector - Gather detailed evidence for verification decisions.

Collects comprehensive evidence package for audit trail and verification.
Supports both real-time collection and post-hoc analysis.

Migrated from: scripts/director/iteration/verification.py (evidence collection patterns)
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

_HASH_PREFIX_LENGTH = 16
_TEXT_HASH_ENCODING = "utf-8"


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode(_TEXT_HASH_ENCODING)).hexdigest()[:_HASH_PREFIX_LENGTH]


class EvidenceType(Enum):
    """Types of evidence that can be collected."""

    FILE_CHANGE = "file_change"
    TOOL_OUTPUT = "tool_output"
    VERIFICATION_RESULT = "verification_result"
    POLICY_CHECK = "policy_check"
    LLM_OUTPUT = "llm_output"
    TEST_RESULT = "test_result"
    AUDIT_TRAIL = "audit_trail"


@dataclass
class FileEvidence:
    """Evidence of a file change."""

    path: str
    change_type: str  # created, modified, deleted
    size_before: int | None = None
    size_after: int | None = None
    hash_before: str | None = None
    hash_after: str | None = None
    lines_added: int = 0
    lines_removed: int = 0
    content_preview: str = ""  # First 500 chars

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "change_type": self.change_type,
            "size_before": self.size_before,
            "size_after": self.size_after,
            "hash_before": self.hash_before,
            "hash_after": self.hash_after,
            "lines_added": self.lines_added,
            "lines_removed": self.lines_removed,
            "content_preview": self.content_preview[:200] if self.content_preview else "",
        }


@dataclass
class ToolEvidence:
    """Evidence of tool execution."""

    tool_name: str
    command: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "command": self.command,
            "exit_code": self.exit_code,
            "stdout_preview": self.stdout[:500] if self.stdout else "",
            "stderr_preview": self.stderr[:500] if self.stderr else "",
            "duration_ms": self.duration_ms,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class VerificationEvidence:
    """Evidence of verification result."""

    verifier: str  # e.g., "type_check", "lint", "test"
    passed: bool
    findings: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verifier": self.verifier,
            "passed": self.passed,
            "findings": self.findings[:10],  # Limit findings
            "warnings_count": len(self.warnings),
            "errors_count": len(self.errors),
            "metrics": self.metrics,
        }


@dataclass
class LLMEvidence:
    """Evidence of LLM interaction."""

    role: str  # planner, director, qa, etc.
    prompt_hash: str  # Hash of prompt for audit
    output_hash: str  # Hash of output for audit
    provider: str = ""
    model: str = ""
    tokens_used: int = 0
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "prompt_hash": self.prompt_hash,
            "output_hash": self.output_hash,
            "provider": self.provider,
            "model": self.model,
            "tokens_used": self.tokens_used,
            "duration_ms": self.duration_ms,
        }


@dataclass
class EvidencePackage:
    """Complete evidence package for a task/iteration."""

    task_id: str
    iteration: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # File changes
    file_changes: list[FileEvidence] = field(default_factory=list)

    # Tool executions
    tool_outputs: list[ToolEvidence] = field(default_factory=list)

    # Verification results
    verification_results: list[VerificationEvidence] = field(default_factory=list)

    # LLM interactions
    llm_interactions: list[LLMEvidence] = field(default_factory=list)

    # Policy checks
    policy_violations: list[str] = field(default_factory=list)
    policy_approvals: list[str] = field(default_factory=list)

    # Audit trail
    audit_entries: list[dict[str, Any]] = field(default_factory=list)

    # Summary
    summary: str = ""
    acceptance: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "iteration": self.iteration,
            "created_at": self.created_at.isoformat(),
            "file_changes": [fc.to_dict() for fc in self.file_changes],
            "tool_outputs": [to.to_dict() for to in self.tool_outputs],
            "verification_results": [vr.to_dict() for vr in self.verification_results],
            "llm_interactions": [llm.to_dict() for llm in self.llm_interactions],
            "policy_violations": self.policy_violations,
            "policy_approvals": self.policy_approvals,
            "audit_entries": self.audit_entries,
            "summary": self.summary,
            "acceptance": self.acceptance,
        }

    def compute_hash(self) -> str:
        """Compute a hash of this evidence package for tamper detection."""
        content = json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False)
        return _hash_text(content)

    def has_critical_issues(self) -> bool:
        """Check if evidence package indicates critical issues."""
        for vr in self.verification_results:
            if not vr.passed and vr.errors:
                return True
        return any(to.exit_code != 0 and "error" in to.stderr.lower() for to in self.tool_outputs)


class EvidenceCollector:
    """Collects and manages evidence throughout task execution."""

    def __init__(self, task_id: str, iteration: int = 0) -> None:
        self.task_id = task_id
        self.iteration = iteration
        self._package = EvidencePackage(
            task_id=task_id,
            iteration=iteration,
        )
        self._collected_types: set[EvidenceType] = set()

    def record_file_change(
        self,
        path: str,
        change_type: str,
        size_before: int | None = None,
        size_after: int | None = None,
        content_before: str | None = None,
        content_after: str | None = None,
    ) -> None:
        """Record evidence of a file change."""
        lines_added = 0
        lines_removed = 0

        if content_before and content_after:
            before_lines = content_before.splitlines()
            after_lines = content_after.splitlines()
            lines_before = len(before_lines)
            lines_after = len(after_lines)
            lines_added = max(0, lines_after - lines_before)
            lines_removed = max(0, lines_before - lines_after)

        hash_before = None
        hash_after = None

        if content_before:
            hash_before = _hash_text(content_before)
        if content_after:
            hash_after = _hash_text(content_after)

        evidence = FileEvidence(
            path=path,
            change_type=change_type,
            size_before=size_before,
            size_after=size_after,
            hash_before=hash_before,
            hash_after=hash_after,
            lines_added=lines_added,
            lines_removed=lines_removed,
            content_preview=content_after[:500] if content_after else "",
        )

        self._package.file_changes.append(evidence)
        self._collected_types.add(EvidenceType.FILE_CHANGE)

    def record_tool_execution(
        self,
        tool_name: str,
        command: str,
        exit_code: int,
        stdout: str = "",
        stderr: str = "",
        duration_ms: int = 0,
    ) -> None:
        """Record evidence of tool execution."""
        evidence = ToolEvidence(
            tool_name=tool_name,
            command=command,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
        )

        self._package.tool_outputs.append(evidence)
        self._collected_types.add(EvidenceType.TOOL_OUTPUT)

    def record_verification_result(
        self,
        verifier: str,
        passed: bool,
        findings: list[str] | None = None,
        warnings: list[str] | None = None,
        errors: list[str] | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> None:
        """Record verification result evidence."""
        evidence = VerificationEvidence(
            verifier=verifier,
            passed=passed,
            findings=findings or [],
            warnings=warnings or [],
            errors=errors or [],
            metrics=metrics or {},
        )

        self._package.verification_results.append(evidence)
        self._collected_types.add(EvidenceType.VERIFICATION_RESULT)

    def record_llm_interaction(
        self,
        role: str,
        prompt: str,
        output: str,
        provider: str = "",
        model: str = "",
        tokens_used: int = 0,
        duration_ms: int = 0,
    ) -> None:
        """Record LLM interaction evidence."""
        prompt_hash = _hash_text(prompt)
        output_hash = _hash_text(output)

        evidence = LLMEvidence(
            role=role,
            prompt_hash=prompt_hash,
            output_hash=output_hash,
            provider=provider,
            model=model,
            tokens_used=tokens_used,
            duration_ms=duration_ms,
        )

        self._package.llm_interactions.append(evidence)
        self._collected_types.add(EvidenceType.LLM_OUTPUT)

    def record_policy_violation(self, violation: str) -> None:
        """Record a policy violation."""
        self._package.policy_violations.append(violation)
        self._collected_types.add(EvidenceType.POLICY_CHECK)

    def record_policy_approval(self, approval: str) -> None:
        """Record a policy approval."""
        self._package.policy_approvals.append(approval)
        self._collected_types.add(EvidenceType.POLICY_CHECK)

    def record_audit_entry(self, entry: dict[str, Any]) -> None:
        """Record an audit trail entry."""
        entry_with_timestamp = {
            **entry,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        self._package.audit_entries.append(entry_with_timestamp)
        self._collected_types.add(EvidenceType.AUDIT_TRAIL)

    def set_summary(self, summary: str, acceptance: bool | None = None) -> None:
        """Set the final summary and acceptance decision."""
        self._package.summary = summary
        self._package.acceptance = acceptance

    def get_package(self) -> EvidencePackage:
        """Get the complete evidence package."""
        return self._package

    def has_evidence_type(self, evidence_type: EvidenceType) -> bool:
        """Check if specific evidence type has been collected."""
        return evidence_type in self._collected_types

    def is_complete(self) -> bool:
        """Check if evidence package is complete for verification."""
        required = {EvidenceType.FILE_CHANGE, EvidenceType.VERIFICATION_RESULT}
        return required.issubset(self._collected_types)

    def export_json(self) -> str:
        """Export evidence package as JSON string."""
        return json.dumps(self._package.to_dict(), indent=2, ensure_ascii=False)


def create_evidence_collector(task_id: str, iteration: int = 0) -> EvidenceCollector:
    """Create a new evidence collector for a task.

    Args:
        task_id: The task identifier
        iteration: Build iteration number

    Returns:
        Configured EvidenceCollector
    """
    return EvidenceCollector(task_id=task_id, iteration=iteration)
