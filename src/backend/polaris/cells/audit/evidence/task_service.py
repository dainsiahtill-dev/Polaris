"""Evidence construction service for worker execution results.

This module is the single source of truth for turning worker outputs into
domain `TaskEvidence` records.
"""

from __future__ import annotations

from typing import Any

from polaris.domain.entities import TaskEvidence
from polaris.domain.language import detect_language as _detect_language

_MAX_EVIDENCE_CONTENT_CHARS = 1000


def detect_language(file_path: str) -> str:
    """Detect a file language from its path."""
    return _detect_language(str(file_path or ""))


def build_file_evidence(files_created: list[dict[str, Any]]) -> list[TaskEvidence]:
    """Build evidence items for created or modified files."""
    evidence_list: list[TaskEvidence] = []
    for file_info in files_created:
        if not isinstance(file_info, dict):
            continue
        path = str(file_info.get("path") or "").strip()
        content = str(file_info.get("content") or "")
        evidence_list.append(
            TaskEvidence(
                type="file",
                path=path or None,
                content=content[:_MAX_EVIDENCE_CONTENT_CHARS],
                metadata={
                    "size": len(content),
                    "language": detect_language(path),
                },
            )
        )
    return evidence_list


def build_error_evidence(error: str, duration_ms: int) -> list[TaskEvidence]:
    """Build evidence items for execution failures."""
    content = str(error or "")[:_MAX_EVIDENCE_CONTENT_CHARS]
    return [
        TaskEvidence(
            type="error",
            path=None,
            content=content,
            metadata={"duration_ms": int(duration_ms)},
        )
    ]


class EvidenceService:
    """Service wrapper around task evidence builders."""

    @staticmethod
    def build_file_evidence(files_created: list[dict[str, Any]]) -> list[TaskEvidence]:
        return build_file_evidence(files_created)

    @staticmethod
    def build_error_evidence(error: str, duration_ms: int) -> list[TaskEvidence]:
        return build_error_evidence(error, duration_ms)

    @staticmethod
    def detect_language(file_path: str) -> str:
        return detect_language(file_path)


__all__ = [
    "EvidenceService",
    "build_error_evidence",
    "build_file_evidence",
    "detect_language",
]
