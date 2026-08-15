"""Static ESM named-import vs export scans for JavaScript artifacts."""

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

_JS_NAMED_IMPORT_RE = re.compile(r"\bimport\s*\{\s*(?P<symbols>[^}]+)\s*\}\s*from\s*['\"](?P<specifier>\.[^'\"]+)['\"]")
_JS_IDENTIFIER_RE = re.compile(r"^[A-Za-z_$][\w$]*$")
_JS_SUFFIXES = (".js", ".mjs", ".cjs")


def _scan_javascript_named_export_evidence(
    root_full: Path,
    relative_paths: list[str],
) -> _FileArtifactQualityEvidence:
    """Catch ESM named-import holes that ``node --check`` cannot see.

    ``node --check`` is parse-only. Live L2-11 failed QA on
    ``does not provide an export named 'computeVerdict'`` after Director
    settle reported zero diagnostics. Static importer/exporter matching
    keeps this on the Director repair path without executing CLI entrypoints.
    """

    importers = [
        str(Path(path).as_posix()).lstrip("./")
        for path in relative_paths
        if Path(str(path)).suffix.lower() in _JS_SUFFIXES
    ]
    if not importers:
        return _FileArtifactQualityEvidence()

    errors: list[str] = []
    issues: list[Any] = []
    seen: set[str] = set()
    for importer in importers:
        importer_path = root_full / importer
        try:
            importer_text = importer_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        for match in _JS_NAMED_IMPORT_RE.finditer(importer_text):
            exporter = _resolve_relative_js_module(root_full, importer, str(match.group("specifier") or ""))
            if exporter is None:
                continue
            try:
                exporter_text = (root_full / exporter).read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            for raw_symbol in str(match.group("symbols") or "").split(","):
                symbol = raw_symbol.split(" as ", 1)[0].strip()
                if not _JS_IDENTIFIER_RE.fullmatch(symbol):
                    continue
                if _javascript_module_exports_symbol(exporter_text, symbol):
                    continue
                specifier = str(match.group("specifier") or "")
                raw = (
                    f"{importer}: SyntaxError: The requested module '{specifier}' "
                    f"does not provide an export named '{symbol}'"
                )
                if raw in seen:
                    continue
                seen.add(raw)
                errors.append(raw)
                issues.append(
                    _file_artifact_quality_issue(
                        raw,
                        importer,
                        code="javascript_missing_named_export",
                        source="javascript_named_export_scanner",
                        metadata={
                            "language": "javascript",
                            "diagnostic_kind": "missing_named_export",
                            "symbol": symbol,
                            "module": specifier,
                            "exporter": exporter,
                        },
                    )
                )
                if len(errors) >= 20:
                    return _FileArtifactQualityEvidence(errors=tuple(errors), issues=tuple(issues))
    return _FileArtifactQualityEvidence(errors=tuple(errors), issues=tuple(issues))


def _javascript_module_exports_symbol(text: str, symbol: str) -> bool:
    escaped = re.escape(symbol)
    patterns = (
        rf"(?m)^\s*export\s+(?:async\s+)?(?:class|function)\s+{escaped}\b",
        rf"(?m)^\s*export\s+(?:const|let|var)\s+{escaped}\b",
        rf"(?m)^\s*export\s*\{{[^}}]*\b{escaped}\b",
    )
    return any(re.search(pattern, text) for pattern in patterns)


def _resolve_relative_js_module(root_full: Path, importer: str, specifier: str) -> str | None:
    token = str(specifier or "").strip()
    if not token.startswith("."):
        return None
    base_dir = Path(importer).parent
    raw = (base_dir / token).as_posix()
    while raw.startswith("./"):
        raw = raw[2:]
    candidates = [raw]
    if not Path(raw).suffix:
        candidates.extend([f"{raw}{suffix}" for suffix in _JS_SUFFIXES])
        candidates.extend([f"{raw}/index{suffix}" for suffix in _JS_SUFFIXES])
    for candidate in candidates:
        normalized = str(Path(candidate).as_posix()).lstrip("./")
        if (root_full / normalized).is_file():
            return normalized
    return None
