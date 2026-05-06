#!/usr/bin/env python3
"""
Fitness Rule Checker Framework.

Provides unified interface for checking governance fitness rules.
Each rule implements a check_* method returning FitnessCheckResult.

用法:
    python docs/governance/ci/scripts/fitness_rule_checker.py --rule context_pack_is_primary_ai_entry
    python docs/governance/ci/scripts/fitness_rule_checker.py --all
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"

SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_ROOT = SCRIPT_DIR.parent.parent.parent.parent


@dataclass
class FitnessCheckResult:
    """Result of a fitness rule check."""

    rule_id: str
    passed: bool
    evidence: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    timestamp: str = ""
    duration_ms: float = 0.0

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def format(self) -> str:
        """Format result for console output."""
        status = f"{GREEN}PASS{RESET}" if self.passed else f"{RED}FAIL{RESET}"
        lines = [
            f"[{self.rule_id}] {status}",
            f"  Duration: {self.duration_ms:.2f}ms",
        ]
        if self.evidence:
            lines.append("  Evidence:")
            for e in self.evidence[:5]:
                lines.append(f"    - {e}")
        if self.violations:
            lines.append("  Violations:")
            for v in self.violations:
                lines.append(f"    - {v}")
        if self.warnings:
            lines.append("  Warnings:")
            for w in self.warnings:
                lines.append(f"    - {w}")
        return "\n".join(lines)


class FitnessRuleChecker:
    """Base class for fitness rule checkers."""

    def __init__(self, workspace: Path | None = None) -> None:
        self.workspace = workspace or REPO_ROOT
        self.start_time = time.time()

    def _elapsed_ms(self) -> float:
        return (time.time() - self.start_time) * 1000

    def check_context_pack_freshness(self) -> FitnessCheckResult:
        """Check that each Cell has a context pack with fresh timestamp."""
        result = FitnessCheckResult(rule_id="context_pack_is_primary_ai_entry", passed=True)
        cells_yaml_path = self.workspace / "docs" / "graph" / "catalog" / "cells.yaml"
        if not cells_yaml_path.exists():
            result.passed = False
            result.violations.append(f"cells.yaml not found at {cells_yaml_path}")
            return result
        try:
            import yaml

            with open(cells_yaml_path, encoding="utf-8") as f:
                catalog_data = yaml.safe_load(f)
        except (OSError, ImportError) as e:
            result.passed = False
            result.violations.append(f"Failed to parse cells.yaml: {e}")
            return result
        cells = catalog_data.get("cells", [])
        if not cells:
            result.warnings.append("No cells found in cells.yaml")
            return result
        total_cells = len(cells)
        cells_with_pack = 0
        fresh_packs = 0
        missing_packs: list[str] = []
        stale_packs: list[str] = []
        invalid_packs: list[str] = []
        current_time = time.time()
        freshness_cutoff = current_time - 7 * 24 * 60 * 60
        for cell in cells:
            cell_id = cell.get("id")
            if not cell_id:
                continue
            cell_path = self.workspace / "polaris" / "cells" / cell_id.replace(".", "/")
            pack_path = cell_path / "generated" / "context.pack.json"
            if not pack_path.exists():
                pack_path = cell_path / "context.pack.json"
            if not pack_path.exists():
                missing_packs.append(cell_id)
                continue
            cells_with_pack += 1
            try:
                with open(pack_path, encoding="utf-8") as f:
                    pack_data = json.load(f)
                if "cell_id" not in pack_data and "id" not in pack_data:
                    invalid_packs.append(f"{cell_id}: Missing 'cell_id' or 'id' field")
                    continue
            except json.JSONDecodeError as e:
                invalid_packs.append(f"{cell_id}: Invalid JSON: {e}")
                continue
            except OSError as e:
                invalid_packs.append(f"{cell_id}: Cannot read: {e}")
                continue
            try:
                mtime = pack_path.stat().st_mtime
            except OSError:
                invalid_packs.append(f"{cell_id}: cannot read modification time")
                continue
            if mtime >= freshness_cutoff:
                fresh_packs += 1
            else:
                stale_packs.append(f"{cell_id}: context.pack.json is stale")
        result.evidence.append(
            f"Summary: {fresh_packs}/{cells_with_pack} packs fresh, "
            f"{len(stale_packs)} stale, {len(missing_packs)} missing out of {total_cells} cells"
        )
        if missing_packs:
            result.violations.extend(f"Missing context.pack.json: {cell_id}" for cell_id in missing_packs)
            result.passed = False
        if stale_packs:
            result.violations.extend(stale_packs)
            result.passed = False
        if invalid_packs:
            result.violations.extend(invalid_packs)
            result.passed = False
        return result

    def check_semantic_retrieval_boundary(self) -> FitnessCheckResult:
        """Check that semantic retrieval respects graph boundaries."""
        result = FitnessCheckResult(rule_id="graph_constrained_semantic_retrieval", passed=True)
        graph_constrained_entrypoints = {
            "polaris/cells/context/engine/internal/search_gateway.py",
            "polaris/cells/context/catalog/service.py",
        }
        known_unconstrained = {
            "polaris/kernelone/akashic/semantic_memory.py",
            "polaris/kernelone/akashic/hybrid_memory.py",
            "polaris/kernelone/akashic/memory_manager.py",
            "polaris/kernelone/memory/memory_store.py",
            "polaris/infrastructure/db/repositories/lancedb_code_search.py",
        }
        patterns = [
            "polaris/cells/**/search*.py",
            "polaris/cells/**/*semantic*.py",
            "polaris/cells/**/*descriptor*.py",
            "polaris/kernelone/**/search*.py",
            "polaris/kernelone/**/semantic*.py",
            "polaris/kernelone/**/*memory*.py",
            "polaris/infrastructure/**/search*.py",
        ]
        constrained_patterns = [
            "ContextCatalogService",
            "SearchService",
            "cells.yaml",
            "_load_from_catalog",
            "_filter_by_cell",
            "graph_constrained",
        ]
        unconstrained_patterns = [
            "AkashicSemanticMemory",
            "LanceDB",
            "vector_search",
            "embedding_search",
            "workspace_search",
        ]
        non_compliant: list[str] = []
        compliant: list[str] = []
        for pattern in patterns:
            for file_path in self.workspace.glob(pattern):
                if "test" in file_path.parts or file_path.name.startswith("test_"):
                    continue
                if file_path.suffix != ".py":
                    continue
                rel = str(file_path.relative_to(self.workspace)).replace("\\", "/")
                if rel in graph_constrained_entrypoints:
                    compliant.append(rel)
                    continue
                if rel in known_unconstrained:
                    compliant.append(f"{rel} (workspace-level acceptable)")
                    continue
                try:
                    content = file_path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                has_constraint = any(p in content for p in constrained_patterns)
                has_unconstraint = any(p in content for p in unconstrained_patterns)
                if has_constraint:
                    compliant.append(rel)
                elif has_unconstraint:
                    non_compliant.append(rel)
                else:
                    compliant.append(f"{rel} (undetermined)")
        result.evidence.append(f"Compliant sites: {len(compliant)}")
        if non_compliant:
            result.passed = False
            for site in non_compliant:
                result.violations.append(f"Semantic search bypasses graph boundaries: {site}")
        return result

    def check_contract_change_review(self) -> FitnessCheckResult:
        """Check that public contract changes trigger review."""
        result = FitnessCheckResult(rule_id="contract_change_requires_review", passed=True)
        contract_patterns = ["public/contracts.py", "public/contract.py", "contracts.py", "contract.py"]
        adr_pattern = re.compile(r"\badr-\d+[-\w]*\b", re.IGNORECASE)
        vc_pattern = re.compile(r"\bvc-\d{8}[-\w]*\b", re.IGNORECASE)
        review_patterns = [
            re.compile(r"\breview(?:\s|[:\-])", re.IGNORECASE),
            re.compile(r"\bapproved?\b", re.IGNORECASE),
            re.compile(r"\bchecked?\b", re.IGNORECASE),
            re.compile(r"\bverified?\b", re.IGNORECASE),
            re.compile(r"\b LGTM \b", re.IGNORECASE),
            re.compile(r"\blooks?\s+good\b", re.IGNORECASE),
            re.compile(r"\bgovernance\b", re.IGNORECASE),
        ]
        cells_dir = self.workspace / "polaris" / "cells"
        contract_files: list[Path] = []
        if cells_dir.exists():
            for cell_dir in cells_dir.iterdir():
                if not cell_dir.is_dir():
                    continue
                for pattern in contract_patterns:
                    for contract_path in cell_dir.rglob(pattern):
                        if "/internal/" in str(contract_path) or "/test" in str(contract_path):
                            continue
                        contract_files.append(contract_path)
        result.evidence.append(f"Found {len(contract_files)} public contract file(s)")
        if not contract_files:
            result.warnings.append("No public contract files found")
            return result
        files_with_changes = 0
        files_without_review: list[str] = []
        for cf in contract_files:
            command = [
                "git",
                "log",
                "--format=%H|%ad|%s",
                "--date=iso",
                "--since=30.days",
                "--",
                str(cf),
            ]
            try:
                proc = subprocess.run(
                    command,
                    cwd=str(self.workspace),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                continue
            stdout = proc.stdout.strip()
            if not stdout:
                continue
            files_with_changes += 1
            lines = stdout.split("\n")
            if lines and lines[0]:
                parts = lines[0].split("|", 2)
                if len(parts) >= 3:
                    commit_message = parts[2]
                    has_review = bool(adr_pattern.search(commit_message) or vc_pattern.search(commit_message))
                    if not has_review:
                        for pat in review_patterns:
                            if pat.search(commit_message.lower()):
                                has_review = True
                                break
                    if not has_review:
                        files_without_review.append(f"{cf.relative_to(self.workspace)}: {commit_message[:60]}...")
        result.evidence.append(f"Checked {files_with_changes} file(s) with recent changes (last 30 days)")
        if files_with_changes == 0:
            result.evidence.append("No contract changes in the last 30 days - rule not applicable")
        elif not files_without_review:
            result.evidence.append("All contract changes have review evidence")
        else:
            result.passed = False
            result.violations.append(f"{len(files_without_review)} contract change(s) lack review evidence")
            for v in files_without_review:
                result.violations.append(v)
        return result

    def check_no_conflicting_coverage(self) -> FitnessCheckResult:
        """Check that migration units don't claim conflicting full coverage."""
        result = FitnessCheckResult(rule_id="migration_units_do_not_conflict", passed=True)
        ledger_path = self.workspace / "docs" / "migration" / "ledger.yaml"
        if not ledger_path.exists():
            result.passed = False
            result.violations.append("docs/migration/ledger.yaml not found")
            return result
        try:
            import yaml

            with open(ledger_path, encoding="utf-8") as f:
                ledger = yaml.safe_load(f)
        except (OSError, ImportError) as e:
            result.passed = False
            result.violations.append(f"Failed to parse ledger.yaml: {e}")
            return result
        units = ledger.get("units", [])
        if not units:
            result.evidence.append("No migration units found in ledger")
            return result
        completed = {"verified", "retired"}
        path_owners: dict[str, list[str]] = defaultdict(list)
        target_owners: dict[str, list[str]] = defaultdict(list)
        root_owners: dict[str, list[str]] = defaultdict(list)
        for unit in units:
            status = str(unit.get("status", ""))
            if status in completed:
                continue
            unit_id = str(unit.get("id", "unknown"))
            for sr in unit.get("source_refs", []):
                if str(sr.get("coverage", "")) == "full":
                    path = str(sr.get("path", "")).replace("\\", "/").strip()
                    if path:
                        path_owners[path].append(unit_id)
            target = unit.get("target", {})
            for tp in target.get("target_paths", []):
                p = str(tp).replace("\\", "/").strip()
                if p:
                    target_owners[p].append(unit_id)
            for rd in target.get("root_dirs", []):
                p = str(rd).replace("\\", "/").strip()
                if p:
                    root_owners[p].append(unit_id)
        for path, owners in path_owners.items():
            if len(owners) > 1:
                result.passed = False
                result.violations.append(
                    f"Source path '{path}' claimed with full coverage by multiple active units: {owners}"
                )
        for path, owners in target_owners.items():
            if len(owners) > 1:
                result.passed = False
                result.violations.append(f"Target path '{path}' claimed by multiple active units: {owners}")
        for path, owners in root_owners.items():
            if len(owners) > 1:
                result.passed = False
                result.violations.append(f"Target root_dir '{path}' claimed by multiple active units: {owners}")
        result.evidence.append(f"Checked {len(units)} migration units")
        return result

    def check_catalog_presence(self) -> FitnessCheckResult:
        """Check that target Cells are present in catalog."""
        result = FitnessCheckResult(rule_id="catalog_missing_units_cannot_advance", passed=True)
        cells_yaml_path = self.workspace / "docs" / "graph" / "catalog" / "cells.yaml"
        ledger_yaml_path = self.workspace / "docs" / "migration" / "ledger.yaml"
        cell_ids: set[str] = set()
        if cells_yaml_path.exists():
            try:
                import yaml

                with open(cells_yaml_path, encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if data and "cells" in data:
                    for cell in data["cells"]:
                        if "id" in cell:
                            cell_ids.add(cell["id"])
            except (OSError, ImportError) as e:
                result.passed = False
                result.violations.append(f"Failed to load cells.yaml: {e}")
                return result
        result.evidence.append(f"Catalog contains {len(cell_ids)} declared cells")
        units: list[dict] = []
        if ledger_yaml_path.exists():
            try:
                import yaml

                with open(ledger_yaml_path, encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if data and "units" in data:
                    units = data["units"]
            except (OSError, ImportError) as e:
                result.passed = False
                result.violations.append(f"Failed to load ledger.yaml: {e}")
                return result
        result.evidence.append(f"Found {len(units)} migration units in ledger")
        non_advanceable = {"verified", "retired"}
        for unit in units:
            target = unit.get("target", {})
            catalog_status = target.get("catalog_status", "unknown")
            current_status = unit.get("status", "")
            unit_id = unit.get("id", "unknown")
            target_cell = target.get("cell", "")
            if catalog_status == "missing" and current_status in non_advanceable:
                result.passed = False
                result.violations.append(
                    f"Unit '{unit_id}' advanced to {current_status} but target cell '{target_cell}' is missing from catalog"
                )
            if target_cell and target_cell not in cell_ids and catalog_status == "actual":
                result.warnings.append(f"Target cell '{target_cell}' declared as 'actual' but not found in catalog")
        return result

    def check_shim_markers(self) -> FitnessCheckResult:
        """Check that shim_only files have migration markers."""
        result = FitnessCheckResult(rule_id="shim_only_units_require_markers", passed=True)
        ledger_path = self.workspace / "docs" / "migration" / "ledger.yaml"
        if not ledger_path.exists():
            result.passed = False
            result.violations.append("docs/migration/ledger.yaml not found")
            return result
        try:
            import yaml

            with open(ledger_path, encoding="utf-8") as f:
                ledger = yaml.safe_load(f)
        except (OSError, ImportError) as e:
            result.passed = False
            result.violations.append(f"Failed to parse ledger.yaml: {e}")
            return result
        units = ledger.get("units", [])
        shim_units = [u for u in units if str(u.get("status", "")) == "shim_only"]
        if not shim_units:
            result.evidence.append("No shim_only migration units found - check passes vacuously")
            return result
        result.evidence.append(f"Found {len(shim_units)} shim_only migration unit(s)")
        marker_patterns = [
            re.compile(r"#\s*DEPRECATED", re.IGNORECASE),
            re.compile(r"\.\.\s*deprecated::", re.IGNORECASE),
            re.compile(r"warnings\.warn\([^)]*DeprecationWarning", re.IGNORECASE),
            re.compile(r"#\s*TODO[:]\s+migrate", re.IGNORECASE),
            re.compile(r"#\s*MIGRATED", re.IGNORECASE),
            re.compile(r"#\s*LEGACY", re.IGNORECASE),
            re.compile(r"#\s*SHIM", re.IGNORECASE),
            re.compile(r"#\s*COMPATIBILITY", re.IGNORECASE),
            re.compile(r"#\s*BACKWARD\s*COMPAT", re.IGNORECASE),
            re.compile(r"#\s*MOVED\s*TO", re.IGNORECASE),
            re.compile(r"#\s*\d{4}-\d{2}-\d{2}.*migration", re.IGNORECASE),
            re.compile(r"migrated?\s+(?:on|from|to)\s+\d{4}-\d{2}-\d{2}", re.IGNORECASE),
            re.compile(r"deprecated.*\d{4}-\d{2}-\d{2}", re.IGNORECASE),
        ]
        files_without_markers: list[str] = []
        total_checked = 0
        for unit in shim_units:
            unit_id = unit.get("id", "unknown")
            for sr in unit.get("source_refs", []):
                path_str = str(sr.get("path", "")).replace("\\", "/").strip()
                kind = str(sr.get("kind", "file"))
                if not path_str:
                    continue
                possible_paths = [
                    self.workspace / path_str,
                    self.workspace / "src" / "backend" / path_str,
                    self.workspace / "polaris" / path_str,
                ]
                if kind == "directory":
                    dir_path = None
                    for candidate in possible_paths:
                        if candidate.exists() and candidate.is_dir():
                            dir_path = candidate
                            break
                    if dir_path is None:
                        result.warnings.append(f"Directory not found: {path_str} (unit: {unit_id})")
                        continue
                    for py_file in dir_path.rglob("*.py"):
                        total_checked += 1
                        try:
                            content = py_file.read_text(encoding="utf-8")
                        except (OSError, UnicodeDecodeError):
                            continue
                        has_marker = any(p.search(content) for p in marker_patterns)
                        if not has_marker:
                            files_without_markers.append(f"{py_file.relative_to(self.workspace)} (unit: {unit_id})")
                else:
                    total_checked += 1
                    file_path = None
                    for candidate in possible_paths:
                        if candidate.exists() and candidate.is_file():
                            file_path = candidate
                            break
                    if file_path is None:
                        result.warnings.append(f"Source file not found: {path_str} (unit: {unit_id})")
                        continue
                    try:
                        content = file_path.read_text(encoding="utf-8")
                    except (OSError, UnicodeDecodeError):
                        continue
                    has_marker = any(p.search(content) for p in marker_patterns)
                    if not has_marker:
                        files_without_markers.append(f"{file_path.relative_to(self.workspace)} (unit: {unit_id})")
        result.evidence.append(f"Files checked: {total_checked}, without markers: {len(files_without_markers)}")
        if files_without_markers:
            result.passed = False
            for fwm in files_without_markers:
                result.violations.append(f"No migration markers in {fwm}")
        return result

    def check_legacy_coverage(self) -> FitnessCheckResult:
        """Check that legacy path coverage is audited at file granularity."""
        result = FitnessCheckResult(rule_id="legacy_file_coverage_audit", passed=True)
        ledger_path = self.workspace / "docs" / "migration" / "ledger.yaml"
        if not ledger_path.exists():
            result.passed = False
            result.violations.append("docs/migration/ledger.yaml not found")
            return result
        try:
            import yaml

            with open(ledger_path, encoding="utf-8") as f:
                ledger = yaml.safe_load(f)
        except (OSError, ImportError) as e:
            result.passed = False
            result.violations.append(f"Failed to parse ledger.yaml: {e}")
            return result
        units = ledger.get("units", [])
        if not units:
            result.evidence.append("No migration units found in ledger")
            return result
        vague_patterns = [
            re.compile(r"entire\s+(legacy\s+)?(directory|directory\s+replaced)", re.IGNORECASE),
            re.compile(r"whole\s+directory", re.IGNORECASE),
            re.compile(r"all\s+files?\s+in\s+directory", re.IGNORECASE),
            re.compile(r"directory\s+fully\s+(covered|migrated|replaced)", re.IGNORECASE),
            re.compile(r"the\s+entire\s+", re.IGNORECASE),
            re.compile(r"all\s+\*\.py\s+files?", re.IGNORECASE),
            re.compile(r"\*\.py\s+files?", re.IGNORECASE),
            re.compile(r"\.\.\.", re.IGNORECASE),
        ]
        explicit_patterns = [
            re.compile(r"\d+\s+files?:\s*\w+", re.IGNORECASE),
            re.compile(r"(file|module)s?:\s*\w+", re.IGNORECASE),
            re.compile(r"\[[\w\s,]+\]", re.IGNORECASE),
            re.compile(r"(\w+\.py\s*,\s*){2,}", re.IGNORECASE),
            re.compile(r"(service|storage|models|runtime|engine|config)\.py", re.IGNORECASE),
        ]
        file_exts = {".py", ".yaml", ".yml", ".json", ".txt", ".md", ".rst"}
        violations: list[str] = []
        dir_kinds = {"directory", "file_family"}
        for unit in units:
            unit_id = unit.get("id", "unknown")
            for sr in unit.get("source_refs", []):
                kind = str(sr.get("kind", ""))
                note = str(sr.get("note", ""))
                path = str(sr.get("path", ""))
                if kind not in dir_kinds:
                    continue
                has_explicit = False
                for pat in explicit_patterns:
                    if pat.search(note):
                        has_explicit = True
                        break
                if not has_explicit:
                    for ext in file_exts:
                        if re.search(rf"\w+{re.escape(ext)}\b", note, re.IGNORECASE):
                            has_explicit = True
                            break
                if not has_explicit:
                    module_patterns = [r"\b(service|storage|models|runtime|engine|config|loader|manager|handler)\b"]
                    explicit_module_count = sum(1 for p in module_patterns if re.search(p, note, re.IGNORECASE))
                    if explicit_module_count >= 2:
                        has_explicit = True
                if not has_explicit:
                    is_vague = False
                    if not note:
                        is_vague = True
                    else:
                        for pat in vague_patterns:
                            if pat.search(note):
                                is_vague = True
                                break
                    if is_vague:
                        note_snippet = note[:80] + "..." if len(note) > 80 else note
                        violations.append(
                            f"Unit '{unit_id}': Directory '{path}' lacks explicit file list. Note: \"{note_snippet}\""
                        )
        if violations:
            result.passed = False
            for v in violations:
                result.violations.append(v)
        result.evidence.append(f"Checked {len(units)} migration units")
        return result

    def check_verified_evidence(self) -> FitnessCheckResult:
        """Check that verified/retired units have evidence."""
        result = FitnessCheckResult(rule_id="verified_or_retired_units_require_evidence", passed=True)
        ledger_path = self.workspace / "docs" / "migration" / "ledger.yaml"
        if not ledger_path.exists():
            result.passed = False
            result.violations.append(f"Ledger not found: {ledger_path}")
            return result
        try:
            import yaml

            with open(ledger_path, encoding="utf-8") as f:
                ledger = yaml.safe_load(f)
        except (OSError, ImportError) as e:
            result.passed = False
            result.violations.append(f"Failed to parse ledger.yaml: {e}")
            return result
        units = ledger.get("units", [])
        if not units:
            result.warnings.append("No migration units found in ledger")
            return result
        checked: list[str] = []
        without_evidence: list[str] = []
        for unit in units:
            status = unit.get("status", "")
            unit_id = unit.get("id", "unknown")
            if status not in ("verified", "retired"):
                continue
            checked.append(unit_id)
            verification = unit.get("verification", {})
            evidence_fields = [
                verification.get("checks", []),
                verification.get("required_tests", []),
                verification.get("docs_updates", []),
                verification.get("graph_updates", []),
            ]
            has_evidence = any(isinstance(f, list) and len(f) > 0 for f in evidence_fields) or bool(
                unit.get("evidence_notes")
            )
            if has_evidence:
                result.evidence.append(f"{unit_id}: has verification evidence")
            else:
                without_evidence.append(unit_id)
                result.violations.append(f"{unit_id}: status={status} but missing verification evidence")
        if checked:
            result.evidence.append(f"Checked {len(checked)} verified/retired units")
        else:
            result.warnings.append("No verified/retired units found to check")
        result.passed = len(without_evidence) == 0
        return result

    def check_command_pattern_source(self) -> FitnessCheckResult:
        """Check that dangerous command patterns have single source."""
        result = FitnessCheckResult(rule_id="canonical_dangerous_patterns", passed=True)
        canonical_path = self.workspace / "polaris" / "kernelone" / "security" / "dangerous_patterns.py"
        if not canonical_path.exists():
            result.passed = False
            result.violations.append("Canonical dangerous_patterns not found in kernelone/security/")
            return result
        try:
            with open(canonical_path, encoding="utf-8") as f:
                content = f.read()
            if "_DANGEROUS_PATTERNS" not in content and "DANGEROUS_PATTERNS" not in content:
                result.passed = False
                result.violations.append("Canonical dangerous_patterns file lacks pattern definitions")
                return result
        except OSError:
            result.passed = False
            result.violations.append("Cannot read canonical dangerous_patterns file")
            return result
        result.evidence.append("Canonical dangerous_patterns verified")
        cells_dir = self.workspace / "polaris" / "cells"
        dangerous_patterns = [
            re.compile(r"_DANGEROUS_PATTERNS\s*=\s*\["),
            re.compile(r"DANGEROUS_PATTERNS\s*=\s*\["),
            re.compile(r"DANGEROUS_PATTERNS\s*:\s*list"),
            re.compile(r"_DANGEROUS_PATTERNS\s*:\s*list"),
        ]
        violations: list[str] = []
        if cells_dir.exists():
            for py_file in cells_dir.rglob("*.py"):
                if py_file == canonical_path:
                    continue
                if "test" in py_file.parts or "_fixture" in py_file.name:
                    continue
                try:
                    with open(py_file, encoding="utf-8") as f:
                        file_content = f.read()
                    for pat in dangerous_patterns:
                        for match in pat.finditer(file_content):
                            line_num = file_content[: match.start()].count("\n") + 1
                            violations.append(
                                f"Local pattern definition at {py_file.relative_to(self.workspace)}:{line_num}"
                            )
                except OSError:
                    continue
        if violations:
            result.passed = False
            for v in violations:
                result.violations.append(v)
        else:
            result.evidence.append("No local dangerous pattern definitions found in cells/")
        return result

    def check_event_usage(self) -> FitnessCheckResult:
        """Check that events use kernelone.events."""
        result = FitnessCheckResult(rule_id="CELL_KERNELONE_05", passed=True)
        canonical_events = [
            self.workspace / "polaris" / "kernelone" / "events" / "fact_events.py",
            self.workspace / "polaris" / "kernelone" / "events" / "session_events.py",
            self.workspace / "polaris" / "kernelone" / "events" / "__init__.py",
        ]
        canonical_found = False
        for path in canonical_events:
            if path.exists():
                try:
                    with open(path, encoding="utf-8") as f:
                        content = f.read()
                    if "emit_fact_event" in content or "emit_session_event" in content:
                        canonical_found = True
                        break
                except OSError:
                    continue
        if not canonical_found:
            result.passed = False
            result.violations.append("Canonical events not found in kernelone/events/")
            return result
        result.evidence.append("Canonical events verified in kernelone/events/")
        cells_dir = self.workspace / "polaris" / "cells"
        event_patterns = [
            re.compile(r"def _emit_event\s*\("),
            re.compile(r"def emit_event\s*\("),
            re.compile(r"async def _emit_event\s*\("),
            re.compile(r"async def emit_event\s*\("),
        ]
        violations: list[str] = []
        if cells_dir.exists():
            for py_file in cells_dir.rglob("*.py"):
                if py_file in canonical_events:
                    continue
                if "test" in py_file.parts or "_fixture" in py_file.name:
                    continue
                try:
                    with open(py_file, encoding="utf-8") as f:
                        file_content = f.read()
                    for pat in event_patterns:
                        for match in pat.finditer(file_content):
                            line_num = file_content[: match.start()].count("\n") + 1
                            violations.append(
                                f"Local event emitter at {py_file.relative_to(self.workspace)}:{line_num}"
                            )
                except OSError:
                    continue
        if violations:
            result.passed = False
            for v in violations:
                result.violations.append(v)
        else:
            result.evidence.append("No local event emitter definitions found in cells/")
        return result

    def check_tool_compression(self) -> FitnessCheckResult:
        """Check that tool compression uses kernelone.tool."""
        result = FitnessCheckResult(rule_id="CELL_KERNELONE_07", passed=True)
        canonical_paths = [
            self.workspace / "polaris" / "kernelone" / "tool" / "compaction.py",
            self.workspace / "polaris" / "kernelone" / "tool" / "safety.py",
            self.workspace / "polaris" / "kernelone" / "tool" / "transcript.py",
        ]
        canonical_found = False
        for path in canonical_paths:
            if path.exists():
                canonical_found = True
                break
        if not canonical_found:
            result.warnings.append("Canonical kernelone/tool/ modules not found")
        else:
            result.evidence.append("Canonical kernelone/tool/ modules exist")
        cells_dir = self.workspace / "polaris" / "cells"
        local_patterns = [
            re.compile(r"def compact_result_payload\s*\("),
            re.compile(r"class ToolLoopSafetyPolicy\b"),
            re.compile(r"class ToolCompaction\b"),
            re.compile(r"def compress_tool_result\s*\("),
        ]
        violations: list[str] = []
        if cells_dir.exists():
            for py_file in cells_dir.rglob("*.py"):
                if "test" in py_file.parts or "_fixture" in py_file.name:
                    continue
                try:
                    with open(py_file, encoding="utf-8") as f:
                        file_content = f.read()
                    for pat in local_patterns:
                        for match in pat.finditer(file_content):
                            line_num = file_content[: match.start()].count("\n") + 1
                            violations.append(
                                f"Local tool compression at {py_file.relative_to(self.workspace)}:{line_num}"
                            )
                except OSError:
                    continue
        if violations:
            result.passed = False
            for v in violations:
                result.violations.append(v)
        else:
            result.evidence.append("No local tool compression implementations found in cells/")
        return result

    def check_llm_import(self) -> FitnessCheckResult:
        """Check that LLM calls use kernelone.llm."""
        result = FitnessCheckResult(rule_id="CELL_KERNELONE_08", passed=True)
        kernelone_llm_dir = self.workspace / "polaris" / "kernelone" / "llm"
        if not kernelone_llm_dir.exists():
            result.passed = False
            result.violations.append("kernelone/llm/ directory not found")
            return result
        result.evidence.append("kernelone/llm/ directory exists")
        cells_dir = self.workspace / "polaris" / "cells"
        local_patterns = [
            re.compile(r"def _call_role_llm\s*\("),
            re.compile(r"def role_llm_invoke\s*\("),
            re.compile(r"class RoleLLMInvoker\b"),
        ]
        violations: list[str] = []
        if cells_dir.exists():
            for py_file in cells_dir.rglob("*.py"):
                if "test" in py_file.parts or "_fixture" in py_file.name:
                    continue
                try:
                    with open(py_file, encoding="utf-8") as f:
                        file_content = f.read()
                    for pat in local_patterns:
                        for match in pat.finditer(file_content):
                            line_num = file_content[: match.start()].count("\n") + 1
                            violations.append(f"Local LLM caller at {py_file.relative_to(self.workspace)}:{line_num}")
                except OSError:
                    continue
        if violations:
            result.passed = False
            for v in violations:
                result.violations.append(v)
        else:
            result.evidence.append("No local _call_role_llm implementations found in cells/")
        return result

    def check_role_call_hierarchy(self) -> FitnessCheckResult:
        """Check that roles don't directly call同级 peers."""
        result = FitnessCheckResult(rule_id="no_direct_role_call", passed=True)
        cells_dir = self.workspace / "polaris" / "cells"
        role_call_pattern = re.compile(
            r"(DirectorService|ChiefEngineerService|PmService|QaService|"
            r"RoleService|RoleAgent|"
            r"execute_role|invoke_role|run_role|call_role)"
        )
        allowed_patterns = [
            re.compile(r"from polaris\.cells\.runtime\.task_market"),
            re.compile(r"from polaris\.cells\.roles\.runtime"),
            re.compile(r"\.task_market"),
            re.compile(r"TaskMarket"),
            re.compile(r"WorkItem"),
        ]
        orchestrator_dirs = [
            cells_dir / "director" / "execution",
            cells_dir / "pm" / "workflow",
            cells_dir / "roles" / "runtime",
        ]
        violations: list[str] = []
        for dir_path in orchestrator_dirs:
            if not dir_path.exists():
                continue
            for py_file in dir_path.rglob("*.py"):
                if "test" in py_file.parts:
                    continue
                try:
                    with open(py_file, encoding="utf-8") as f:
                        content = f.read()
                    tree = ast.parse(content, filename=str(py_file))
                    for node in ast.walk(tree):
                        if isinstance(node, ast.ImportFrom):
                            module = node.module or ""
                            if "polaris.cells.roles" in module or "polaris.cells.director" in module:
                                for alias in node.names:
                                    name = alias.name
                                    if name.endswith(("Service", "Agent", "Adapter")) and any(
                                        role in module for role in ["pm", "director", "chief_engineer", "qa"]
                                    ):
                                        violations.append(
                                            f"Direct peer role import at {py_file.relative_to(self.workspace)}:{node.lineno}: {module}.{name}"
                                        )
                    for match in role_call_pattern.finditer(content):
                        line_num = content[: match.start()].count("\n") + 1
                        line = content.split("\n")[line_num - 1] if line_num <= len(content.split("\n")) else ""
                        if not any(p.search(line) for p in allowed_patterns):
                            violations.append(
                                f"Suspicious role call at {py_file.relative_to(self.workspace)}:{line_num}: {match.group()}"
                            )
                except OSError:
                    pass
                except SyntaxError:
                    pass
        if violations:
            result.passed = False
            for v in violations:
                result.violations.append(v)
        else:
            result.evidence.append("No direct peer role calls found in mainline orchestration")
        return result

    def check_task_broker(self) -> FitnessCheckResult:
        """Check that task_market is the only business broker."""
        result = FitnessCheckResult(rule_id="task_market_is_single_business_broker", passed=True)
        task_market_dir = self.workspace / "polaris" / "cells" / "runtime" / "task_market"
        if not task_market_dir.exists() or not (task_market_dir / "cell.yaml").exists():
            result.passed = False
            result.violations.append("runtime.task_market cell not found or incomplete")
            return result
        result.evidence.append("runtime.task_market cell exists")
        execution_broker_dir = self.workspace / "polaris" / "cells" / "runtime" / "execution_broker"
        forbidden_patterns = [
            re.compile(r"ExecutionBroker\.publish\("),
            re.compile(r"execution_broker\.claim\("),
            re.compile(r"execution_broker\.acquire\("),
            re.compile(r"from.*execution_broker.*import.*publish", re.DOTALL),
            re.compile(r"from.*execution_broker.*import.*claim", re.DOTALL),
            re.compile(r"from.*execution_broker.*import.*acquire", re.DOTALL),
            re.compile(r"ExecutionBroker\("),
        ]
        broker_violations: list[str] = []
        if execution_broker_dir.exists():
            for py_file in execution_broker_dir.rglob("*.py"):
                if "test" in py_file.parts:
                    continue
                try:
                    with open(py_file, encoding="utf-8") as f:
                        content = f.read()
                    for pat in forbidden_patterns:
                        for match in pat.finditer(content):
                            line_num = content[: match.start()].count("\n") + 1
                            broker_violations.append(
                                f"Execution broker task routing at {py_file.relative_to(self.workspace)}:{line_num}"
                            )
                except OSError:
                    continue
        if broker_violations:
            result.passed = False
            for v in broker_violations:
                result.violations.append(v)
        else:
            result.evidence.append("execution_broker does not have business task routing")
        cells_yaml_path = self.workspace / "docs" / "graph" / "catalog" / "cells.yaml"
        if cells_yaml_path.exists():
            try:
                import yaml

                with open(cells_yaml_path, encoding="utf-8") as f:
                    catalog_data = yaml.safe_load(f)
                cells = catalog_data.get("cells", [])
                peer_role_ids = [
                    "pm",
                    "chief_engineer",
                    "director",
                    "qa",
                    "roles.pm",
                    "roles.chief_engineer",
                    "roles.director",
                    "roles.qa",
                ]
                for cell in cells:
                    cell_id = cell.get("id", "")
                    if any(peer in cell_id for peer in peer_role_ids):
                        depends_on = cell.get("depends_on", [])
                        if "runtime.task_market" not in depends_on and "task_market" not in depends_on:
                            result.warnings.append(f"Cell '{cell_id}' missing runtime.task_market in depends_on")
            except (OSError, ImportError) as e:
                result.warnings.append(f"Error parsing cells.yaml for graph relations: {e}")
        return result


def get_checker() -> FitnessRuleChecker:
    """Get the default fitness rule checker instance."""
    return FitnessRuleChecker(REPO_ROOT)


def run_rule(rule_id: str) -> FitnessCheckResult:
    """Run a specific rule and return result."""
    checker = get_checker()
    method_name = f"check_{rule_id}"
    if not hasattr(checker, method_name):
        return FitnessCheckResult(
            rule_id=rule_id,
            passed=False,
            violations=[f"Unknown rule: {rule_id}"],
            duration_ms=checker._elapsed_ms(),
        )
    method = getattr(checker, method_name)
    result = method()
    result.duration_ms = checker._elapsed_ms()
    return result


def run_all() -> list[FitnessCheckResult]:
    """Run all rules and return results."""
    rules = [
        "context_pack_freshness",
        "semantic_retrieval_boundary",
        "contract_change_review",
        "no_conflicting_coverage",
        "catalog_presence",
        "shim_markers",
        "legacy_coverage",
        "verified_evidence",
        "command_pattern_source",
        "event_usage",
        "tool_compression",
        "llm_import",
        "role_call_hierarchy",
        "task_broker",
    ]
    results = []
    for rule in rules:
        result = run_rule(rule)
        results.append(result)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Fitness Rule Checker")
    parser.add_argument("--rule", help="Specific rule ID to check")
    parser.add_argument("--all", action="store_true", help="Run all rules")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    if args.rule:
        result = run_rule(args.rule)
        if args.json:
            import json

            print(
                json.dumps(
                    {
                        "rule_id": result.rule_id,
                        "passed": result.passed,
                        "evidence": result.evidence,
                        "violations": result.violations,
                        "warnings": result.warnings,
                        "timestamp": result.timestamp,
                        "duration_ms": result.duration_ms,
                    }
                )
            )
        else:
            print(result.format())
        return 0 if result.passed else 1

    if args.all:
        results = run_all()
        passed = sum(1 for r in results if r.passed)
        total = len(results)
        print(f"\n{'=' * 60}")
        print(f"Fitness Rule Check: {passed}/{total} passed")
        print(f"{'=' * 60}\n")
        for result in results:
            print(result.format())
            print()
        return 0 if passed == total else 1

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
