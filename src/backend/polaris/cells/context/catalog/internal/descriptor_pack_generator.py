"""Descriptor Pack Generator for ACGA 2.0.

Analyzes Cell source code and generates machine-readable functional descriptors.
This is a key component of the Polaris governance system.

PR-09 Enhancements:
- Schema validation against semantic-descriptor.schema.yaml
- Semantic versioning (2.1.0)
- Source hash (content fingerprint)
- Index write receipt
- Recall@10 verification (>= 92%)
"""

import ast
import asyncio
import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from polaris.infrastructure.storage import LocalFileSystemAdapter
from polaris.kernelone.fs import KernelFileSystem
from polaris.kernelone.utils.time_utils import utc_now_str

logger = logging.getLogger(__name__)


@dataclass
class DescriptorPack:
    """Descriptor pack with versioning and content fingerprint."""

    version: str = "2.1.0"  # Semantic versioning
    generated_at: str = ""
    source_hash: str = ""  # Content fingerprint
    cell_id: str = ""
    workspace: str = ""
    capabilities: list[dict[str, Any]] = field(default_factory=list)
    embedding_runtime_fingerprint: str = ""
    evolution: dict[str, Any] | None = None

    def _compute_source_hash(self, source_files: list[Path]) -> str:
        """Compute content fingerprint from source files."""
        content = b""
        for f in sorted(source_files):
            try:
                content += f.read_bytes()
            except (OSError, PermissionError) as e:
                logger.debug("Failed to read source file %s for hashing: %s", f, e)
        return hashlib.sha256(content).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result: dict[str, Any] = {
            "version": self.version,
            "generated_at": self.generated_at,
            "source_hash": self.source_hash,
            "cell_id": self.cell_id,
            "workspace": self.workspace,
            "capabilities": self.capabilities,
            "embedding_runtime_fingerprint": self.embedding_runtime_fingerprint,
        }
        if self.evolution:
            result["evolution"] = self.evolution
        return result


@dataclass
class IndexWriteReceipt:
    """Receipt for index write operations, used for audit and rollback."""

    timestamp: str
    descriptor_count: int
    index_path: Path
    verification_hash: str  # Post-write verification hash
    cell_id: str = ""
    source_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "timestamp": self.timestamp,
            "descriptor_count": self.descriptor_count,
            "index_path": str(self.index_path),
            "verification_hash": self.verification_hash,
            "cell_id": self.cell_id,
            "source_hash": self.source_hash,
        }


def verify_recall_at_10(
    candidate_set: list[str],
    ground_truth: list[str],
) -> float:
    """
    Verify retrieval recall.

    Prevents threshold too tight from "finding nothing".
    Target: >= 92%
    """
    if not ground_truth:
        return 0.0
    hits = len(set(candidate_set[:10]) & set(ground_truth))
    recall = hits / len(ground_truth)
    assert recall >= 0.92, f"Recall@10 {recall:.2%} < 92%"
    return recall


def validate_schema(descriptor: dict[str, Any], schema_path: Path) -> bool:
    """Validate descriptor pack against YAML schema.

    Args:
        descriptor: The descriptor pack dictionary to validate.
        schema_path: Path to the semantic-descriptor.schema.yaml file.

    Returns:
        True if validation passes, False otherwise.
    """
    try:
        schema_data = _load_yaml_simple(schema_path)
    except OSError as e:
        logger.warning("Failed to load schema from %s: %s", schema_path, e)
        return False

    # Extract required top-level fields from schema
    required_fields = schema_data.get("required", [])
    for field_name in required_fields:
        if field_name not in descriptor:
            logger.error("Schema validation failed: missing required field '%s'", field_name)
            return False

    # Validate descriptors array if present
    descriptors = descriptor.get("descriptors", [])
    if descriptors:
        descriptor_schema = schema_data.get("$defs", {}).get("descriptor", {})
        descriptor_required = descriptor_schema.get("required", [])

        for i, desc in enumerate(descriptors):
            for req_field in descriptor_required:
                if req_field not in desc:
                    logger.error(
                        "Schema validation failed: descriptor[%d] missing required field '%s'",
                        i,
                        req_field,
                    )
                    return False

    logger.info("Schema validation passed for descriptor pack")
    return True


def _load_yaml_simple(path: Path) -> dict[str, Any]:
    """Load YAML file with fallback to simple parser."""
    try:
        import yaml

        if not path.exists():
            return {}
        with open(path, encoding="utf-8") as f:
            data_yaml: dict[str, Any] = yaml.safe_load(f)  # renamed to avoid redef
            return data_yaml if isinstance(data_yaml, dict) else {}
    except (OSError, ImportError):
        # Fallback for basic cell.yaml structure
        data: dict[str, Any] = {}
        if not path.exists():
            return data
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()

        current_key = None
        for line in lines:
            line = line.split("#")[0].strip()
            if not line:
                continue
            if ":" in line and not line.startswith("-"):
                parts = line.split(":", 1)
                current_key = parts[0].strip()
                val = parts[1].strip()
                if val:
                    data[current_key] = val
                else:
                    data[current_key] = []
            elif line.startswith("-") and current_key:
                val = line[1:].strip()
                if isinstance(data.get(current_key), list):
                    data[current_key].append(val)
        return data


class FunctionalAnalyzer(ast.NodeVisitor):
    """AST visitor to extract classes and functions."""

    def __init__(self) -> None:
        self.classes: list[dict[str, Any]] = []
        self.functions: list[dict[str, Any]] = []
        self._current_class: dict[str, Any] | None = None

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        doc = ast.get_docstring(node) or ""
        cls_info: dict[str, Any] = {
            "name": node.name,
            "doc": doc.split("\n")[0] if doc else "",
            "methods": [],
        }

        old_class = self._current_class
        self._current_class = cls_info
        self.generic_visit(node)
        self.classes.append(cls_info)
        self._current_class = old_class

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        doc = ast.get_docstring(node) or ""
        func_name = node.name

        # Skip dunder methods except __init__
        if func_name.startswith("__") and func_name.endswith("__") and func_name != "__init__":
            return

        if self._current_class:
            self._current_class["methods"].append({"name": func_name, "doc": doc.split("\n")[0] if doc else ""})
        else:
            # Top-level function
            self.functions.append({"name": func_name, "doc": doc.split("\n")[0] if doc else ""})
        self.generic_visit(node)


def analyze_file(path: Path) -> dict[str, Any]:
    """Analyze a single Python file."""
    try:
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())

        visitor = FunctionalAnalyzer()
        visitor.visit(tree)
        return {"classes": visitor.classes, "functions": visitor.functions}
    except (OSError, SyntaxError, RuntimeError, ValueError) as e:
        logger.debug("Failed to analyze Python file %s: %s", path, e)
        return {"error": str(e)}


def _write_receipt(receipt: IndexWriteReceipt, repo_root: Path) -> None:
    """Write index write receipt to workspace."""
    from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

    receipt_path = repo_root / get_workspace_metadata_dir_name() / "runtime" / "index_receipts"
    receipt_path.mkdir(parents=True, exist_ok=True)

    receipt_file = receipt_path / f"{receipt.cell_id}_receipt.json"
    payload = json.dumps(receipt.to_dict(), indent=2, ensure_ascii=False) + "\n"
    try:
        with open(receipt_file, "w", encoding="utf-8") as f:
            f.write(payload)
        logger.debug("Written index receipt to %s", receipt_file)
    except OSError as e:
        logger.warning("Failed to write index receipt to %s: %s", receipt_file, e)


def _compute_verification_hash(content: str) -> str:
    """Compute verification hash for written content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


async def generate_pack(
    cell_dir: Path,
    repo_root: Path,
    evolve: bool = False,
    skip_schema_validation: bool = False,
) -> Path | None:
    """Generate descriptor pack for a cell.

    Args:
        cell_dir: Path to the cell directory.
        repo_root: Repository root path.
        evolve: Whether to enable role-driven evolution insights.
        skip_schema_validation: Skip schema validation (useful for testing).

    Returns:
        Path to the generated descriptor pack, or None if generation failed.
    """
    cell_yaml_path = cell_dir / "cell.yaml"
    if not cell_yaml_path.exists():
        return None

    cell_data = _load_yaml_simple(cell_yaml_path)
    cell_id = cell_data.get("id")
    if not cell_id:
        return None

    owned_paths = cell_data.get("owned_paths", [])
    if not isinstance(owned_paths, list):
        owned_paths = [owned_paths]

    capabilities: list[dict[str, Any]] = []
    py_files: set[Path] = set()

    for pattern in owned_paths:
        pattern = str(pattern).strip()
        if "**" in pattern:
            # Handle glob patterns like polaris/cells/audit/**
            base_part = pattern.split("**")[0].rstrip("/")
            search_dir = repo_root / base_part
            if search_dir.exists() and search_dir.is_dir():
                py_files.update(search_dir.rglob("*.py"))
        else:
            # Direct path or simple glob
            for match in repo_root.glob(pattern):
                if match.is_file() and match.suffix == ".py":
                    py_files.add(match)
                elif match.is_dir():
                    py_files.update(match.rglob("*.py"))

    source_files_for_hash: list[Path] = []

    for py_file in sorted(py_files):
        # Avoid analyzing generated files themselves or 3rd party
        if "generated/" in py_file.as_posix() or "venv" in py_file.as_posix():
            continue

        source_files_for_hash.append(py_file)
        rel_path = py_file.relative_to(repo_root).as_posix()
        analysis = analyze_file(py_file)
        if "error" in analysis:
            continue

        for cls in analysis["classes"]:
            capabilities.append(
                {
                    "type": "class",
                    "name": cls["name"],
                    "description": cls["doc"],
                    "defined_in": rel_path,
                    "methods": cls["methods"],
                }
            )
        for func in analysis["functions"]:
            capabilities.append(
                {"type": "function", "name": func["name"], "description": func["doc"], "defined_in": rel_path}
            )

    # Build DescriptorPack with PR-09 enhancements
    pack = DescriptorPack(
        version="2.1.0",
        generated_at=utc_now_str(),
        cell_id=cell_id,
        workspace=str(repo_root),
        capabilities=capabilities,
        embedding_runtime_fingerprint="local-v1",
    )

    # Compute source hash from analyzed files
    pack.source_hash = pack._compute_source_hash(source_files_for_hash)

    # Role-Driven Evolution (Optional)
    if evolve:
        try:
            from polaris.cells.context.catalog.internal.evolution_engine import EvolutionEngine

            engine = EvolutionEngine(str(repo_root))
            insights = await engine.get_evolution_insights(cell_id, pack.to_dict())
            if insights:
                pack.evolution = insights
        except ImportError:
            logger.debug("Evolution engine not found, skipping evolution insights for %s.", cell_id)
        except (RuntimeError, ValueError) as e:
            logger.warning("Failed to get evolution insights for %s: %s", cell_id, e)

    output_path = cell_dir / "generated" / "descriptor.pack.json"
    kfs = _build_kernel_fs(repo_root)
    relative_output_path = output_path.relative_to(repo_root).as_posix()

    descriptor_dict = pack.to_dict()
    payload = json.dumps(descriptor_dict, indent=2, ensure_ascii=False) + "\n"

    # Validate against schema if not skipped
    if not skip_schema_validation:
        schema_path = repo_root / "docs" / "governance" / "schemas" / "semantic-descriptor.schema.yaml"
        if schema_path.exists():
            if not validate_schema(descriptor_dict, schema_path):
                logger.warning("Schema validation failed for %s, but proceeding with write", cell_id)
        else:
            logger.debug("Schema file not found at %s, skipping validation", schema_path)

    kfs.workspace_write_text(relative_output_path, payload, encoding="utf-8")

    # Compute verification hash after write
    verification_hash = _compute_verification_hash(payload)

    # Write index receipt
    receipt = IndexWriteReceipt(
        timestamp=utc_now_str(),
        descriptor_count=len(capabilities),
        index_path=output_path,
        verification_hash=verification_hash,
        cell_id=cell_id,
        source_hash=pack.source_hash,
    )
    _write_receipt(receipt, repo_root)

    # Verify Recall@10 if ground truth is available
    # This is a placeholder - in production, this would use actual ground truth data
    _verify_recall_if_applicable(capabilities, cell_id)

    return output_path


def _verify_recall_if_applicable(capabilities: list[dict[str, Any]], cell_id: str) -> None:
    """Verify Recall@10 if ground truth data is available.

    This is a no-op implementation that logs the intent.
    In production, this would compare against stored ground truth.
    """
    # Placeholder for Recall@10 verification
    # In a full implementation, this would:
    # 1. Load ground truth for the cell from workspace/meta/context_catalog/
    # 2. Extract candidate keys from capabilities
    # 3. Call verify_recall_at_10(candidates, ground_truth)
    logger.debug(
        "Recall@10 verification for %s: %d capabilities analyzed",
        cell_id,
        len(capabilities),
    )


async def run_all(
    repo_root: Path | None = None,
    evolve: bool = False,
    max_concurrency: int = 8,
    skip_schema_validation: bool = False,
) -> None:
    """Run generation for all cells with bounded concurrency.

    Args:
        repo_root: Repository root path. Auto-detected if not provided.
        evolve: Whether to enable role-driven evolution insights.
        max_concurrency: Maximum number of concurrent pack generations.
            Defaults to 8 to prevent resource exhaustion.
        skip_schema_validation: Skip schema validation during generation.
    """
    if repo_root is None:
        # Assume we are in src/backend/polaris/application/governance
        # or root. Let's find polaris dir.
        current = Path(os.getcwd())
        if (current / "polaris").exists():
            repo_root = current
        elif (current.parent / "polaris").exists():
            repo_root = current.parent
        else:
            # Fallback to standard layout
            repo_root = Path(__file__).resolve().parent.parent.parent.parent

    cells_root = repo_root / "polaris" / "cells"
    if not cells_root.exists():
        logger.error("Error: Cells root not found at %s", cells_root)
        return

    # Collect all cell directories first
    cell_dirs = [cell_yaml.parent for cell_yaml in cells_root.rglob("cell.yaml")]

    if not cell_dirs:
        logger.info("No cells found to process.")
        return

    # Use semaphore to limit concurrent execution
    semaphore = asyncio.Semaphore(max(max_concurrency, 1))

    async def _generate_with_limit(cell_dir: Path) -> Path | None:
        """Generate pack with concurrency limit."""
        async with semaphore:
            return await generate_pack(
                cell_dir, repo_root, evolve=evolve, skip_schema_validation=skip_schema_validation
            )

    # Create tasks with bounded concurrency
    tasks = [_generate_with_limit(cell_dir) for cell_dir in cell_dirs]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Count successful results, log errors
    success_count = 0
    error_count = 0
    for result in results:
        if isinstance(result, Exception):
            logger.error("Error generating pack: %s", result)
            error_count += 1
        elif result is not None:
            success_count += 1

    logger.info(
        "Successfully generated %s descriptor packs (%s errors, Evolve: %s).",
        success_count,
        error_count,
        evolve,
    )


def _build_kernel_fs(repo_root: Path) -> KernelFileSystem:
    """Build a local KernelFileSystem rooted at repository root."""
    return KernelFileSystem(str(repo_root), LocalFileSystemAdapter())


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Descriptor Pack Generator")
    parser.add_argument("--evolve", action="store_true", help="Enable role-driven evolution insights")
    parser.add_argument(
        "--skip-schema-validation",
        action="store_true",
        help="Skip schema validation during generation",
    )
    args = parser.parse_args()

    asyncio.run(run_all(evolve=args.evolve, skip_schema_validation=args.skip_schema_validation))
