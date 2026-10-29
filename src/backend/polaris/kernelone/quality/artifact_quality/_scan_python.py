"""Python acceptance-test scans for artifact quality evidence."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

from polaris.kernelone.quality.artifact_quality._issues import (
    _file_artifact_quality_issue,
)
from polaris.kernelone.quality.artifact_quality._models import (
    _FileArtifactQualityEvidence,
)
from polaris.kernelone.quality.interface_ledger import WorkspaceReadSnapshot

_REQUIRED_TERMS_LOOP_RE = re.compile(r"for\s+term\s+in\s+REQUIRED_TERMS\b")
_ASSERT_NODE_IN_SCRIPT_RE = re.compile(
    r"""assertIn\(\s*(['"])node\1""",
    flags=re.IGNORECASE,
)
_NPM_RUN_ALIAS_RE = re.compile(r"^\s*npm\s+run\s+([A-Za-z0-9:_-]+)\b")


def _python_specialist_text(
    root_full: Path,
    relative: str,
    reader: WorkspaceReadSnapshot | None,
    incomplete_reasons: list[str],
    *,
    optional: bool = False,
) -> str | None:
    """Consume the scan's existing source witness; legacy reads stay best-effort."""
    if reader is not None:
        if reader.root != root_full.absolute():
            raise ValueError("specialist reader workspace binding mismatch")
        read = reader.read(root_full / relative)
        if read.status == "readable":
            return cast("str | None", (read.text))
        if not (optional and read.status == "missing"):
            incomplete_reasons.append(f"python_specialist_source_unknown:{relative}:{read.status}:{read.detail}")
        return None
    try:
        return (root_full / relative).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def _scan_python_acceptance_term_pair_evidence(
    root_full: Path,
    relative_paths: list[str],
    *,
    reader: WorkspaceReadSnapshot | None = None,
) -> _FileArtifactQualityEvidence:
    """Flag unused plural-term tables left beside REQUIRED_TERMS assert loops.

    Live L2-11: tests defined REQUIRED_TERM_PAIRS (galaxy/galaxies) but still
    looped REQUIRED_TERMS, so assertIn('galaxy', '{"galaxies": 0}') failed.
    """

    errors: list[str] = []
    issues: list[Any] = []
    incomplete_reasons: list[str] = []
    if reader is not None and reader.root != root_full.absolute():
        raise ValueError("specialist reader workspace binding mismatch")
    for raw_path in relative_paths:
        relative = str(Path(raw_path).as_posix()).lstrip("./")
        if not relative.endswith(".py"):
            continue
        text = _python_specialist_text(root_full, relative, reader, incomplete_reasons)
        if text is None:
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
    return _FileArtifactQualityEvidence(
        errors=tuple(errors), issues=tuple(issues), incomplete_reasons=tuple(incomplete_reasons)
    )


def _load_package_scripts(
    root_full: Path,
    *,
    reader: WorkspaceReadSnapshot | None = None,
    incomplete_reasons: list[str] | None = None,
) -> dict[str, str]:
    reasons = incomplete_reasons if incomplete_reasons is not None else []
    text = _python_specialist_text(root_full, "package.json", reader, reasons, optional=True)
    if text is None:
        return {}
    try:
        raw = json.loads(text)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        if reader is not None:
            reasons.append(f"python_specialist_package_unknown:package.json:invalid_json:{type(exc).__name__}:{exc}")
        return {}
    if not isinstance(raw, dict):
        if reader is not None:
            reasons.append("python_specialist_package_unknown:package.json:package_not_object")
        return {}
    if "scripts" not in raw:
        return {}
    scripts = raw.get("scripts")
    if not isinstance(scripts, dict):
        if reader is not None:
            reasons.append("python_specialist_package_unknown:package.json:scripts_not_object")
        return {}
    resolved: dict[str, str] = {}
    for key, value in scripts.items():
        name = str(key or "").strip()
        if not name or not isinstance(value, str):
            if reader is not None:
                reasons.append(f"python_specialist_package_unknown:package.json:invalid_script:{key}")
            continue
        resolved[name] = value
    return resolved


def _npm_run_alias_target(scripts: dict[str, str], command: str) -> str:
    match = _NPM_RUN_ALIAS_RE.match(str(command or ""))
    if match is None:
        return ""
    return str(scripts.get(match.group(1)) or "").strip()


def _scan_python_acceptance_npm_node_alias_evidence(
    root_full: Path,
    relative_paths: list[str],
    *,
    reader: WorkspaceReadSnapshot | None = None,
) -> _FileArtifactQualityEvidence:
    """Flag assertIn('node', scripts.test) when test is a real npm-run alias.

    Live L2-11: scripts.test is ``npm run test:js`` and test:js is
    ``node --test ...``. assertIn('node', 'npm run test:js') fails even though
    the resolved command already invokes Node.
    """

    incomplete_reasons: list[str] = []
    scripts = _load_package_scripts(root_full, reader=reader, incomplete_reasons=incomplete_reasons)
    test_command = str(scripts.get("test") or "").strip()
    alias_target = _npm_run_alias_target(scripts, test_command)
    if not test_command or not alias_target or "node" not in alias_target.lower():
        return _FileArtifactQualityEvidence(incomplete_reasons=tuple(incomplete_reasons))
    if "node" in test_command.lower():
        return _FileArtifactQualityEvidence(incomplete_reasons=tuple(incomplete_reasons))

    errors: list[str] = []
    issues: list[Any] = []
    for raw_path in relative_paths:
        relative = str(Path(raw_path).as_posix()).lstrip("./")
        if not relative.endswith(".py"):
            continue
        text = _python_specialist_text(root_full, relative, reader, incomplete_reasons)
        if text is None:
            continue
        if _ASSERT_NODE_IN_SCRIPT_RE.search(text) is None:
            continue
        if "scripts.test" not in text and "test_script" not in text:
            continue
        raw = (
            f"{relative}: assertIn('node', scripts.test) fails when scripts.test "
            f"is the npm-run alias {test_command!r} whose target already invokes Node "
            f"({alias_target!r}). Accept the alias or assert against the resolved script."
        )
        errors.append(raw)
        issues.append(
            _file_artifact_quality_issue(
                raw,
                relative,
                code="python_npm_node_alias_assert",
                source="python_acceptance_npm_alias_scanner",
                metadata={
                    "language": "python",
                    "diagnostic_kind": "npm_run_node_alias_assert",
                    "scripts_test": test_command,
                    "resolved_test": alias_target,
                },
            )
        )
        if len(errors) >= 20:
            break
    return _FileArtifactQualityEvidence(
        errors=tuple(errors), issues=tuple(issues), incomplete_reasons=tuple(incomplete_reasons)
    )
