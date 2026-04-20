"""Architecture guard CLI for external Cell plugins.

This module provides a fail-closed guard runner for plugin admission checks.
It is intentionally strict on import fences, effect/token alignment, and
manifest-level governance requirements.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)

try:
    import yaml
except (RuntimeError, ValueError):  # pragma: no cover - surfaced as report issue
    yaml = None  # type: ignore[assignment]


_MODE_AUDIT_ONLY = "audit-only"
_MODE_FAIL_ON_NEW = "fail-on-new"
_MODE_HARD_FAIL = "hard-fail"
_SUPPORTED_MODES = (_MODE_AUDIT_ONLY, _MODE_FAIL_ON_NEW, _MODE_HARD_FAIL)

_SEVERITY_BLOCKER = "blocker"
_SEVERITY_HIGH = "high"
_SEVERITY_MEDIUM = "medium"
_SEVERITY_LOW = "low"

_SEMVER_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][a-zA-Z0-9_.-]+)?$")
_CELL_ID_PATTERN = re.compile(r"^[a-z0-9_]+(?:\.[a-z0-9_]+)+$")
_PLUGIN_ID_PATTERN = _CELL_ID_PATTERN
_UTF8_OPEN_MODE_PATTERN = re.compile(r"[waxr+]")
_FORBIDDEN_IMPORT_PREFIXES = (
    "polaris.application.",
    "polaris.infrastructure.",
    "polaris.delivery.",
    "polaris.bootstrap.",
)


@dataclass(frozen=True)
class GuardIssue:
    """One architecture guard violation."""

    check_id: str
    severity: str
    message: str
    path: str = ""
    line: int = 0

    def fingerprint(self) -> str:
        """Generate a stable issue fingerprint for baseline comparison."""
        key = f"{self.check_id}|{self.severity}|{self.path}|{self.line}|{self.message}"
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "severity": self.severity,
            "message": self.message,
            "path": self.path,
            "line": self.line,
            "fingerprint": self.fingerprint(),
        }


@dataclass(frozen=True)
class GuardReport:
    """Structured guard report."""

    plugin_root: str
    mode: str
    exit_code: int
    issue_count: int
    blocker_count: int
    high_count: int
    new_issue_count: int
    issues: tuple[GuardIssue, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "plugin_root": self.plugin_root,
            "mode": self.mode,
            "exit_code": self.exit_code,
            "issue_count": self.issue_count,
            "blocker_count": self.blocker_count,
            "high_count": self.high_count,
            "new_issue_count": self.new_issue_count,
            "issues": [issue.to_dict() for issue in self.issues],
            "issue_fingerprints": [issue.fingerprint() for issue in self.issues],
        }


class ExternalPluginArchitectureGuard:
    """Guard runner for one external plugin package root."""

    def __init__(
        self,
        plugin_root: str | Path,
        *,
        mode: str = _MODE_HARD_FAIL,
        baseline_path: str | Path | None = None,
    ) -> None:
        normalized_root = Path(plugin_root).resolve()
        if not normalized_root.exists():
            raise ValueError(f"plugin_root does not exist: {normalized_root}")
        if mode not in _SUPPORTED_MODES:
            raise ValueError(f"Unsupported mode: {mode}")

        self._plugin_root = normalized_root
        self._mode = mode
        self._baseline_path = Path(baseline_path).resolve() if baseline_path else None
        self._issues: list[GuardIssue] = []

    def run(self) -> GuardReport:
        """Run all checks and return a structured report."""
        plugin_manifest = self._load_yaml(
            self._plugin_root / "plugin.yaml",
            check_id="manifest.plugin_yaml.readable",
        )
        cell_manifest = self._load_yaml(
            self._plugin_root / "cell.yaml",
            check_id="manifest.cell_yaml.readable",
        )
        self._check_plugin_manifest(plugin_manifest)
        self._check_cell_manifest(cell_manifest)
        self._check_manifest_consistency(plugin_manifest, cell_manifest)
        self._check_public_contract_modules(cell_manifest)
        self._check_verification_artifacts(plugin_manifest)
        self._check_owned_paths(cell_manifest)
        self._check_effect_token_alignment(plugin_manifest, cell_manifest)
        self._parsed_files = self._parse_python_files()
        self._check_import_fence()
        self._check_utf8_open_calls()

        issues = tuple(self._issues)
        blocker_count = sum(1 for item in issues if item.severity == _SEVERITY_BLOCKER)
        high_count = sum(1 for item in issues if item.severity == _SEVERITY_HIGH)
        new_issue_count = self._count_new_issues(issues)
        exit_code = self._resolve_exit_code(
            blocker_count=blocker_count,
            high_count=high_count,
            new_issue_count=new_issue_count,
        )
        return GuardReport(
            plugin_root=str(self._plugin_root),
            mode=self._mode,
            exit_code=exit_code,
            issue_count=len(issues),
            blocker_count=blocker_count,
            high_count=high_count,
            new_issue_count=new_issue_count,
            issues=issues,
        )

    def write_baseline(self, path: str | Path, report: GuardReport) -> None:
        """Write current report fingerprints as baseline file."""
        target = Path(path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "plugin_root": report.plugin_root,
            "issue_fingerprints": [issue.fingerprint() for issue in report.issues],
        }
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _add_issue(
        self,
        *,
        check_id: str,
        severity: str,
        message: str,
        path: str = "",
        line: int = 0,
    ) -> None:
        self._issues.append(
            GuardIssue(
                check_id=check_id,
                severity=severity,
                message=message,
                path=path,
                line=line,
            )
        )

    def _load_yaml(self, path: Path, *, check_id: str) -> dict[str, Any]:
        if yaml is None:
            self._add_issue(
                check_id="dependency.yaml.unavailable",
                severity=_SEVERITY_BLOCKER,
                message="PyYAML is unavailable; cannot evaluate plugin governance manifests",
            )
            return {}
        if not path.exists():
            self._add_issue(
                check_id=check_id,
                severity=_SEVERITY_BLOCKER,
                message=f"Required manifest file is missing: {path.name}",
                path=str(path),
            )
            return {}
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (RuntimeError, ValueError) as exc:
            self._add_issue(
                check_id=check_id,
                severity=_SEVERITY_BLOCKER,
                message=f"Failed to parse YAML: {exc}",
                path=str(path),
            )
            return {}
        if not isinstance(data, dict):
            self._add_issue(
                check_id=check_id,
                severity=_SEVERITY_BLOCKER,
                message=f"YAML root must be an object: {path.name}",
                path=str(path),
            )
            return {}
        return data

    def _check_plugin_manifest(self, manifest: dict[str, Any]) -> None:
        required = (
            "manifest_version",
            "plugin_id",
            "display_name",
            "publisher",
            "plugin_version",
            "cell_id",
            "cell_manifest",
            "sdk",
            "runtime",
            "capabilities",
            "verification",
            "distribution",
        )
        for key in required:
            if key not in manifest:
                self._add_issue(
                    check_id="manifest.plugin_yaml.required_fields",
                    severity=_SEVERITY_BLOCKER,
                    message=f"plugin.yaml is missing required field: {key}",
                    path=str(self._plugin_root / "plugin.yaml"),
                )

        plugin_id = str(manifest.get("plugin_id") or "").strip()
        if plugin_id and not _PLUGIN_ID_PATTERN.fullmatch(plugin_id):
            self._add_issue(
                check_id="manifest.plugin_yaml.plugin_id_format",
                severity=_SEVERITY_BLOCKER,
                message=f"plugin_id format is invalid: {plugin_id}",
                path=str(self._plugin_root / "plugin.yaml"),
            )

        plugin_version = str(manifest.get("plugin_version") or "").strip()
        if plugin_version and not _SEMVER_PATTERN.fullmatch(plugin_version):
            self._add_issue(
                check_id="manifest.plugin_yaml.plugin_version_format",
                severity=_SEVERITY_HIGH,
                message=f"plugin_version is not semver-like: {plugin_version}",
                path=str(self._plugin_root / "plugin.yaml"),
            )

        runtime = manifest.get("runtime")
        if isinstance(runtime, dict):
            process_model = str(runtime.get("process_model") or "").strip()
            if process_model != "isolated_process":
                self._add_issue(
                    check_id="runtime.process_model.isolated",
                    severity=_SEVERITY_BLOCKER,
                    message="runtime.process_model must be isolated_process",
                    path=str(self._plugin_root / "plugin.yaml"),
                )
            default_enabled = runtime.get("default_enabled")
            if default_enabled is not False:
                self._add_issue(
                    check_id="runtime.default_enabled.false",
                    severity=_SEVERITY_BLOCKER,
                    message="runtime.default_enabled must be false",
                    path=str(self._plugin_root / "plugin.yaml"),
                )
        else:
            self._add_issue(
                check_id="runtime.object.required",
                severity=_SEVERITY_BLOCKER,
                message="runtime must be an object",
                path=str(self._plugin_root / "plugin.yaml"),
            )

    def _check_cell_manifest(self, manifest: dict[str, Any]) -> None:
        required = (
            "id",
            "owned_paths",
            "public_contracts",
            "depends_on",
            "state_owners",
            "effects_allowed",
            "verification",
        )
        for key in required:
            if key not in manifest:
                self._add_issue(
                    check_id="manifest.cell_yaml.required_fields",
                    severity=_SEVERITY_BLOCKER,
                    message=f"cell.yaml is missing required field: {key}",
                    path=str(self._plugin_root / "cell.yaml"),
                )

        cell_id = str(manifest.get("id") or "").strip()
        if cell_id and not _CELL_ID_PATTERN.fullmatch(cell_id):
            self._add_issue(
                check_id="manifest.cell_yaml.cell_id_format",
                severity=_SEVERITY_BLOCKER,
                message=f"cell.yaml id format is invalid: {cell_id}",
                path=str(self._plugin_root / "cell.yaml"),
            )

    def _check_manifest_consistency(
        self,
        plugin_manifest: dict[str, Any],
        cell_manifest: dict[str, Any],
    ) -> None:
        plugin_cell_id = str(plugin_manifest.get("cell_id") or "").strip()
        manifest_cell_id = str(cell_manifest.get("id") or "").strip()
        if plugin_cell_id and manifest_cell_id and plugin_cell_id != manifest_cell_id:
            self._add_issue(
                check_id="manifest.plugin_cell_id.matches_cell_yaml",
                severity=_SEVERITY_BLOCKER,
                message=(
                    f"plugin.yaml cell_id must match cell.yaml id (plugin={plugin_cell_id}, cell={manifest_cell_id})"
                ),
                path=str(self._plugin_root / "plugin.yaml"),
            )

        manifest_pointer = str(plugin_manifest.get("cell_manifest") or "").strip()
        if manifest_pointer and manifest_pointer != "cell.yaml":
            target = (self._plugin_root / manifest_pointer).resolve()
            if not target.exists():
                self._add_issue(
                    check_id="manifest.plugin_yaml.cell_manifest.path",
                    severity=_SEVERITY_BLOCKER,
                    message=f"cell_manifest target does not exist: {manifest_pointer}",
                    path=str(self._plugin_root / "plugin.yaml"),
                )

    def _check_public_contract_modules(self, cell_manifest: dict[str, Any]) -> None:
        contracts = cell_manifest.get("public_contracts")
        if not isinstance(contracts, dict):
            return
        modules = contracts.get("modules")
        if not isinstance(modules, list):
            return
        for module in modules:
            module_name = str(module or "").strip()
            if not module_name:
                continue
            relative_path = module_name.replace(".", "/") + ".py"
            target = self._plugin_root / relative_path
            if not target.exists():
                self._add_issue(
                    check_id="contracts.public_module.exists",
                    severity=_SEVERITY_BLOCKER,
                    message=f"public contract module file is missing: {module_name}",
                    path=str(target),
                )

    def _check_verification_artifacts(self, plugin_manifest: dict[str, Any]) -> None:
        verification = plugin_manifest.get("verification")
        if not isinstance(verification, dict):
            return

        verify_pack = str(verification.get("verify_pack") or "").strip()
        if verify_pack:
            verify_pack_path = (self._plugin_root / verify_pack).resolve()
            if not verify_pack_path.exists():
                self._add_issue(
                    check_id="verification.verify_pack.exists",
                    severity=_SEVERITY_BLOCKER,
                    message=f"verify pack is missing: {verify_pack}",
                    path=str(verify_pack_path),
                )

        tests = verification.get("tests")
        if isinstance(tests, list):
            for test_path in tests:
                normalized = str(test_path or "").strip()
                if not normalized:
                    continue
                target = (self._plugin_root / normalized).resolve()
                if not target.exists():
                    self._add_issue(
                        check_id="verification.tests.exist",
                        severity=_SEVERITY_HIGH,
                        message=f"verification test file is missing: {normalized}",
                        path=str(target),
                    )

    def _check_owned_paths(self, cell_manifest: dict[str, Any]) -> None:
        raw_paths = cell_manifest.get("owned_paths")
        if not isinstance(raw_paths, list):
            return

        for raw in raw_paths:
            normalized = str(raw or "").strip().replace("\\", "/")
            if not normalized:
                continue
            if normalized.startswith("/") or re.match(r"^[a-zA-Z]:", normalized):
                self._add_issue(
                    check_id="owned_paths.relative_only",
                    severity=_SEVERITY_BLOCKER,
                    message=f"owned_paths entry must be relative: {normalized}",
                    path=str(self._plugin_root / "cell.yaml"),
                )
                continue
            if ".." in normalized.split("/"):
                self._add_issue(
                    check_id="owned_paths.no_parent_traversal",
                    severity=_SEVERITY_BLOCKER,
                    message=f"owned_paths entry cannot use parent traversal: {normalized}",
                    path=str(self._plugin_root / "cell.yaml"),
                )

    def _check_effect_token_alignment(
        self,
        plugin_manifest: dict[str, Any],
        cell_manifest: dict[str, Any],
    ) -> None:
        capabilities = plugin_manifest.get("capabilities")
        capability_tokens: set[str] = set()
        if isinstance(capabilities, dict):
            tokens = capabilities.get("tokens")
            if isinstance(tokens, list):
                capability_tokens = {str(token).strip() for token in tokens if str(token).strip()}

        effects = cell_manifest.get("effects_allowed")
        if not isinstance(effects, list):
            return
        for effect in effects:
            normalized = str(effect or "").strip()
            if not normalized:
                continue
            if normalized not in capability_tokens:
                self._add_issue(
                    check_id="effects.allowed_aligned_with_tokens",
                    severity=_SEVERITY_HIGH,
                    message=f"effects_allowed entry is not covered by capability token: {normalized}",
                    path=str(self._plugin_root / "cell.yaml"),
                )

    def _iter_python_files(self) -> Iterable[Path]:
        plugin_dir = self._plugin_root / "plugin"
        if not plugin_dir.exists():
            return ()
        return tuple(path for path in plugin_dir.rglob("*.py") if path.is_file())

    def _parse_python_files(self) -> dict[Path, Any]:
        """Parse all plugin Python files once and cache the AST.

        BUG-009 fix: _check_import_fence and _check_utf8_open_calls previously
        each read and parsed every file independently, causing double I/O and
        double AST construction for every plugin file.  This method is called
        once per run() and the result is shared between both checks.

        Returns:
            Mapping of Path -> ast.Module (or None if unparseable).
            Files that could not be read or parsed have their issues recorded
            here so the individual check methods can skip them cleanly.
        """
        result: dict[Path, Any] = {}
        for file_path in self._iter_python_files():
            try:
                content = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                self._add_issue(
                    check_id="utf8.decode.plugin_source",
                    severity=_SEVERITY_BLOCKER,
                    message=f"Python source is not UTF-8 decodable: {exc}",
                    path=str(file_path),
                )
                result[file_path] = None
                continue
            except (RuntimeError, ValueError) as exc:
                self._add_issue(
                    check_id="python_source.read",
                    severity=_SEVERITY_HIGH,
                    message=f"Failed reading source file: {exc}",
                    path=str(file_path),
                )
                result[file_path] = None
                continue
            try:
                result[file_path] = ast.parse(content, filename=str(file_path))
            except SyntaxError as exc:
                self._add_issue(
                    check_id="python_source.syntax",
                    severity=_SEVERITY_BLOCKER,
                    message=f"Syntax error: {exc.msg}",
                    path=str(file_path),
                    line=int(getattr(exc, "lineno", 0) or 0),
                )
                result[file_path] = None
        return result

    def _check_import_fence(self) -> None:
        for file_path, tree in self._parsed_files.items():
            if tree is None:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self._check_import_name(
                            import_name=str(alias.name or "").strip(),
                            file_path=file_path,
                            line=int(getattr(node, "lineno", 0) or 0),
                        )
                elif isinstance(node, ast.ImportFrom):
                    base = str(node.module or "").strip()
                    if not base:
                        continue
                    self._check_import_name(
                        import_name=base,
                        file_path=file_path,
                        line=int(getattr(node, "lineno", 0) or 0),
                    )

    def _check_import_name(self, *, import_name: str, file_path: Path, line: int) -> None:
        if not import_name:
            return
        if import_name.startswith("polaris.cells.") and ".internal." in import_name:
            self._add_issue(
                check_id="import_fence.forbidden_internal_cell_import",
                severity=_SEVERITY_BLOCKER,
                message=f"Forbidden internal Cell import: {import_name}",
                path=str(file_path),
                line=line,
            )
            return
        for prefix in _FORBIDDEN_IMPORT_PREFIXES:
            if import_name.startswith(prefix):
                self._add_issue(
                    check_id="import_fence.forbidden_platform_layer_import",
                    severity=_SEVERITY_BLOCKER,
                    message=f"Forbidden platform layer import: {import_name}",
                    path=str(file_path),
                    line=line,
                )
                return
        if import_name.startswith("polaris.kernelone.") and not import_name.startswith("polaris.kernelone.sdk."):
            self._add_issue(
                check_id="import_fence.kernelone_sdk_only",
                severity=_SEVERITY_BLOCKER,
                message=f"KernelOne import must use sdk namespace only: {import_name}",
                path=str(file_path),
                line=line,
            )

    def _check_utf8_open_calls(self) -> None:
        for file_path, tree in self._parsed_files.items():
            if tree is None:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if not isinstance(node.func, ast.Name) or node.func.id != "open":
                    continue
                mode_value = "r"
                encoding_found = False
                for keyword in node.keywords:
                    if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
                        mode_value = str(keyword.value.value or "")
                    if keyword.arg == "encoding":
                        encoding_found = True
                if "b" in mode_value:
                    continue
                if not _UTF8_OPEN_MODE_PATTERN.search(mode_value):
                    continue
                if not encoding_found:
                    self._add_issue(
                        check_id="utf8.open_explicit_encoding_required",
                        severity=_SEVERITY_HIGH,
                        message="Text open() call must explicitly set encoding='utf-8'",
                        path=str(file_path),
                        line=int(getattr(node, "lineno", 0) or 0),
                    )

    def _count_new_issues(self, issues: tuple[GuardIssue, ...]) -> int:
        if self._mode != _MODE_FAIL_ON_NEW:
            return 0
        if self._baseline_path is None or not self._baseline_path.exists():
            return len(issues)
        try:
            payload = json.loads(self._baseline_path.read_text(encoding="utf-8"))
        except (RuntimeError, ValueError) as exc:
            logger.debug("Failed to parse baseline JSON: %s", exc)
            return len(issues)
        previous = payload.get("issue_fingerprints")
        if not isinstance(previous, list):
            return len(issues)
        baseline_set = {str(item).strip() for item in previous if str(item).strip()}
        current_set = {issue.fingerprint() for issue in issues}
        return len(current_set - baseline_set)

    def _resolve_exit_code(
        self,
        *,
        blocker_count: int,
        high_count: int,
        new_issue_count: int,
    ) -> int:
        if self._mode == _MODE_AUDIT_ONLY:
            return 0
        if self._mode == _MODE_FAIL_ON_NEW:
            return 1 if new_issue_count > 0 else 0
        return 1 if blocker_count > 0 or high_count > 0 else 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run external Cell plugin architecture guard checks.",
    )
    parser.add_argument("command", choices=("check_external_plugin",), help="Guard command name")
    parser.add_argument("--plugin-root", required=True, help="External plugin package root directory")
    parser.add_argument(
        "--mode",
        default=_MODE_HARD_FAIL,
        choices=_SUPPORTED_MODES,
        help="Guard mode: audit-only, fail-on-new, hard-fail",
    )
    parser.add_argument("--baseline", help="Baseline JSON file for fail-on-new mode")
    parser.add_argument("--output", help="Output JSON report path")
    parser.add_argument(
        "--write-baseline",
        help="Write current issue fingerprints to this baseline JSON path",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    guard = ExternalPluginArchitectureGuard(
        plugin_root=str(args.plugin_root),
        mode=str(args.mode),
        baseline_path=str(args.baseline) if args.baseline else None,
    )
    report = guard.run()
    payload = report.to_dict()

    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    print(serialized)

    if args.output:
        output_path = Path(str(args.output)).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(serialized, encoding="utf-8")

    if args.write_baseline:
        guard.write_baseline(str(args.write_baseline), report)

    return int(report.exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
