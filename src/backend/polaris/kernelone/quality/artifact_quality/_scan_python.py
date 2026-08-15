"""Python acceptance-test scans for artifact quality evidence."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from polaris.kernelone.quality.artifact_quality._issues import (
    _file_artifact_quality_issue,
)
from polaris.kernelone.quality.artifact_quality._models import (
    _FileArtifactQualityEvidence,
)

_REQUIRED_TERMS_LOOP_RE = re.compile(r"for\s+term\s+in\s+REQUIRED_TERMS\b")


def _scan_python_acceptance_term_pair_evidence(
    root_full: Path,
    relative_paths: list[str],
) -> _FileArtifactQualityEvidence:
    """Flag unused plural-term tables left beside REQUIRED_TERMS assert loops.

    Live L2-11: tests defined REQUIRED_TERM_PAIRS (galaxy/galaxies) but still
    looped REQUIRED_TERMS, so assertIn('galaxy', '{"galaxies": 0}') failed.
    """

    errors: list[str] = []
    issues: list[Any] = []
    for raw_path in relative_paths:
        relative = str(Path(raw_path).as_posix()).lstrip("./")
        if not relative.endswith(".py"):
            continue
        path = root_full / relative
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if "REQUIRED_TERM_PAIRS" not in text:
            continue
        if _REQUIRED_TERMS_LOOP_RE.search(text) is None:
            continue
        raw = (
            f"{relative}: unused REQUIRED_TERM_PAIRS while loops still iterate "
            "REQUIRED_TERMS; 'galaxy' is not a substring of 'galaxies'. "
            "Iterate the pairs and accept either singular or plural."
        )
        errors.append(raw)
        issues.append(
            _file_artifact_quality_issue(
                raw,
                relative,
                code="python_unused_term_pairs",
                source="python_acceptance_term_scanner",
                metadata={
                    "language": "python",
                    "diagnostic_kind": "unused_required_term_pairs",
                },
            )
        )
        if len(errors) >= 20:
            break
    return _FileArtifactQualityEvidence(errors=tuple(errors), issues=tuple(issues))
