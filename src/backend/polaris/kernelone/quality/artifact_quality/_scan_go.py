"""Go module compile scans for artifact quality evidence."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from polaris.kernelone.quality.artifact_quality._constants import (
    _ARTIFACT_QUALITY_GO_UNDEFINED_RE,
    _GO_PROJECT_CHECK_FLAG,
)
from polaris.kernelone.quality.artifact_quality._issues import (
    _file_artifact_quality_issue,
)
from polaris.kernelone.quality.artifact_quality._models import (
    _FileArtifactQualityEvidence,
)

_GO_COMPILE_LINE_RE = re.compile(r"(?P<path>[^:\s\n]+\.go):(?P<line>\d+):(?P<column>\d+):\s*(?P<message>[^\n]+)")


def _scan_go_project_compile_evidence(root_full: Path, relative_paths: list[str]) -> _FileArtifactQualityEvidence:
    """Compile the Go module, including tests, without executing assertions.

    Director existing-scope preflight uses this scanner. QA later runs the
    real ``go test ./...`` verifier. Compile-only keeps assertion failures
    out of preflight so they stay on the QA/same-task path.
    """

    if os.environ.get(_GO_PROJECT_CHECK_FLAG, "1").strip().lower() in {"0", "false", "no", "off"}:
        return _FileArtifactQualityEvidence()
    if not (root_full / "go.mod").is_file():
        return _FileArtifactQualityEvidence()
    if not any(Path(path).suffix.lower() == ".go" or Path(path).name == "go.mod" for path in relative_paths):
        return _FileArtifactQualityEvidence()
    if shutil.which("go") is None:
        return _FileArtifactQualityEvidence()
    try:
        proc = subprocess.run(
            ["go", "test", "-count=0", "./..."],
            cwd=str(root_full),
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return _FileArtifactQualityEvidence()
    if proc.returncode == 0:
        return _FileArtifactQualityEvidence()
    output = f"{proc.stdout}\n{proc.stderr}"
    errors: list[str] = []
    issues: list[Any] = []
    seen: set[str] = set()
    for match in _GO_COMPILE_LINE_RE.finditer(output):
        relative_path = str(match.group("path") or "").replace("\\", "/").lstrip("./")
        message = str(match.group("message") or "").strip()
        if not relative_path or not message:
            continue
        raw = f"{relative_path}:{match.group('line')}:{match.group('column')}: {message}"
        if raw in seen:
            continue
        seen.add(raw)
        errors.append(raw)
        metadata: dict[str, str] = {
            "language": "go",
            "line": str(match.group("line") or ""),
            "column": str(match.group("column") or ""),
        }
        undefined = _ARTIFACT_QUALITY_GO_UNDEFINED_RE.search(message)
        if undefined is not None:
            metadata["diagnostic_kind"] = "undefined_identifier"
            metadata["identifier"] = str(undefined.group("identifier") or "").strip()
        issues.append(
            _file_artifact_quality_issue(
                raw,
                relative_path,
                code="go_compile_error",
                source="go_project_compile_scanner",
                metadata=metadata,
            )
        )
        if len(errors) >= 20:
            break
    if not errors:
        detail = " ".join(line.strip() for line in output.splitlines() if line.strip())[:400]
        raw = f"Artifact quality scan failed: go test -count=0 ./... failed: {detail}"
        errors.append(raw)
        issues.append(
            _file_artifact_quality_issue(
                raw,
                "go.mod",
                code="go_compile_error",
                source="go_project_compile_scanner",
                metadata={"language": "go", "diagnostic_kind": "go_compile_failed"},
            )
        )
    return _FileArtifactQualityEvidence(errors=tuple(errors), issues=tuple(issues))


_GO_TEST_FAIL_LINE_RE = re.compile(r"(?P<path>[^:\s\n]+\.go):(?P<line>\d+):\s+(?P<message>\S.*want\s+\S[^\n]*)")


def _scan_go_project_test_evidence(root_full: Path, relative_paths: list[str]) -> _FileArtifactQualityEvidence:
    """Run real ``go test ./...`` after compile is clean.

    Compile-only ``-count=0`` is for existing-scope compile gates. Assertion
    failures must still block a test-owning task from completing with
    ``director_no_materialized_changes`` (live L1-10: BucketIntensity(0.34)
    want low vs authored ``< 0.33`` mid). Only emit rows whose path is in
    the caller scan set so TASK-1/2 do not inherit TASK-3 assertion residuals.
    """

    owned_tests = {str(Path(path).as_posix()).lstrip("./") for path in relative_paths if str(path).endswith("_test.go")}
    if not owned_tests:
        return _FileArtifactQualityEvidence()
    if os.environ.get(_GO_PROJECT_CHECK_FLAG, "1").strip().lower() in {"0", "false", "no", "off"}:
        return _FileArtifactQualityEvidence()
    if not (root_full / "go.mod").is_file() or shutil.which("go") is None:
        return _FileArtifactQualityEvidence()
    try:
        proc = subprocess.run(
            ["go", "test", "./..."],
            cwd=str(root_full),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return _FileArtifactQualityEvidence()
    if proc.returncode == 0:
        return _FileArtifactQualityEvidence()
    output = f"{proc.stdout}\n{proc.stderr}"
    errors: list[str] = []
    issues: list[Any] = []
    seen: set[str] = set()
    for match in _GO_TEST_FAIL_LINE_RE.finditer(output):
        relative_path = str(match.group("path") or "").replace("\\", "/").lstrip("./")
        message = str(match.group("message") or "").strip()
        if relative_path not in owned_tests or not message:
            continue
        raw = f"{relative_path}:{match.group('line')}: {message}"
        if raw in seen:
            continue
        seen.add(raw)
        errors.append(raw)
        issues.append(
            _file_artifact_quality_issue(
                raw,
                relative_path,
                code="go_test_assertion_error",
                source="go_project_test_scanner",
                metadata={
                    "language": "go",
                    "line": str(match.group("line") or ""),
                    "diagnostic_kind": "go_test_assertion",
                },
            )
        )
        if len(errors) >= 20:
            break
    return _FileArtifactQualityEvidence(errors=tuple(errors), issues=tuple(issues))
