"""Migration script: JSONL Semantic Memory to LanceDB.

This script migrates existing semantic memory data from JSONL storage
to LanceDB vector storage for improved search performance.

Usage::

    # Migrate all semantic memory to LanceDB
    python -m polaris.kernelone.akashic.knowledge_pipeline.migrate_to_lancedb \\
        --workspace /path/to/workspace

    # Dry run (show what would be migrated)
    python -m polaris.kernelone.akashic.knowledge_pipeline.migrate_to_lancedb \\
        --workspace /path/to/workspace --dry-run

    # Verify migration
    python -m polaris.kernelone.akashic.knowledge_pipeline.migrate_to_lancedb \\
        --workspace /path/to/workspace --verify
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from polaris.kernelone.storage import resolve_runtime_path

logger = logging.getLogger(__name__)


@dataclass
class MigrationStats:
    """Statistics from a migration run."""

    total_items: int = 0
    migrated_items: int = 0
    skipped_items: int = 0
    failed_items: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_items": self.total_items,
            "migrated_items": self.migrated_items,
            "skipped_items": self.skipped_items,
            "failed_items": self.failed_items,
            "errors": self.errors,
        }


class SemanticMemoryToLanceDBMigrator:
    """Migrates semantic memory from JSONL to LanceDB.

    This class reads existing semantic memory items from JSONL files
    and upserts them into a LanceDB vector store.

    Usage::

        migrator = SemanticMemoryToLanceDBMigrator(workspace="/path/to/workspace")
        stats = await migrator.run()
        print(f"Migrated {stats.migrated_items} items")
    """

    def __init__(
        self,
        workspace: str,
        *,
        memory_file: str | None = None,
        dry_run: bool = False,
    ) -> None:
        """Initialize migrator.

        Args:
            workspace: Workspace root path
            memory_file: Optional explicit path to memory JSONL file
            dry_run: If True, only count items without migrating
        """
        self._workspace = Path(workspace)
        self._dry_run = dry_run

        # Default memory file location
        if memory_file:
            self._memory_file = Path(memory_file)
        else:
            self._memory_file = Path(resolve_runtime_path(str(self._workspace), "runtime/semantic/memory.jsonl"))

        # LanceDB adapter (lazy initialized)
        self._lancedb: Any = None

    def _get_lancedb(self) -> Any:
        """Get or create LanceDB adapter."""
        if self._lancedb is None:
            from polaris.kernelone.akashic.knowledge_pipeline.lancedb_adapter import (
                KnowledgeLanceDB,
            )

            self._lancedb = KnowledgeLanceDB(
                workspace=str(self._workspace),
                table_name="semantic_memory",
            )
        return self._lancedb

    def _load_jsonl_items(self) -> list[dict[str, Any]]:
        """Load items from semantic memory JSONL file.

        Returns:
            List of memory item dictionaries
        """
        items: list[dict[str, Any]] = []

        if not self._memory_file.exists():
            logger.warning("Memory file not found: %s", self._memory_file)
            return items

        try:
            with open(self._memory_file, encoding="utf-8") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        items.append(data)
                    except json.JSONDecodeError as exc:
                        logger.warning(
                            "Failed to parse line %d in %s: %s",
                            line_num,
                            self._memory_file,
                            exc,
                        )
        except OSError as exc:
            logger.error("Failed to read memory file: %s", exc)

        return items

    async def _migrate_item(
        self,
        item: dict[str, Any],
        lancedb: Any,
    ) -> tuple[bool, str]:
        """Migrate a single item to LanceDB.

        Args:
            item: Memory item dictionary
            lancedb: LanceDB adapter instance

        Returns:
            Tuple of (success, status_message)
        """
        try:
            memory_id = item.get("memory_id", "")
            text = item.get("text", "")
            importance = item.get("importance", 5)
            embedding_list = item.get("embedding")

            if not text:
                return False, "Empty text, skipped"

            # Use memory_id as chunk_id if available
            chunk_id = memory_id if memory_id else f"migrated_{hash(text):x}"[:32]

            # Convert embedding to list if present
            embedding: list[float]
            if embedding_list:
                embedding = list(embedding_list)
            else:
                # Generate a dummy embedding for migrated items
                # In practice, these should be recomputed
                logger.debug("No embedding for item %s, will use zero vector", chunk_id[:12])
                embedding = [0.0] * 384

            await lancedb.add(
                chunk_id=chunk_id,
                text=text,
                embedding=embedding,
                importance=importance,
                source_file=item.get("metadata", {}).get("source_file"),
                line_start=item.get("metadata", {}).get("line_start"),
                line_end=item.get("metadata", {}).get("line_end"),
            )

            return True, "Migrated"

        except (RuntimeError, ValueError) as exc:
            return False, f"Error: {exc}"

    async def run(self) -> MigrationStats:
        """Run the migration.

        Returns:
            MigrationStats with migration results
        """
        stats = MigrationStats()

        # Load existing items
        items = self._load_jsonl_items()
        stats.total_items = len(items)

        if stats.total_items == 0:
            logger.info("No items to migrate")
            return stats

        logger.info(
            "Found %d items in semantic memory JSONL",
            stats.total_items,
        )

        if self._dry_run:
            logger.info("Dry run mode - showing items without migrating")
            for item in items:
                logger.info(
                    "  Would migrate: %s (importance=%d)",
                    item.get("text", "")[:50],
                    item.get("importance", 5),
                )
            stats.skipped_items = stats.total_items
            return stats

        # Get LanceDB adapter
        lancedb = self._get_lancedb()

        # Migrate items
        logger.info("Starting migration to LanceDB...")
        for i, item in enumerate(items, 1):
            success, message = await self._migrate_item(item, lancedb)

            if success:
                stats.migrated_items += 1
                logger.debug("Migrated %d/%d: %s", i, stats.total_items, message)
            else:
                stats.failed_items += 1
                stats.errors.append(message)
                logger.warning("Failed %d/%d: %s", i, stats.total_items, message)

        logger.info(
            "Migration complete: %d migrated, %d failed, %d total",
            stats.migrated_items,
            stats.failed_items,
            stats.total_items,
        )

        return stats

    async def verify(self) -> dict[str, Any]:
        """Verify migration by comparing counts.

        Returns:
            Dictionary with verification results
        """
        # Count JSONL items
        jsonl_items = self._load_jsonl_items()
        jsonl_count = len(jsonl_items)

        # Count LanceDB items
        lancedb = self._get_lancedb()
        lancedb_stats = await lancedb.get_stats()
        lancedb_count = lancedb_stats.get("total_records", 0)

        return {
            "jsonl_item_count": jsonl_count,
            "lancedb_record_count": lancedb_count,
            "match": jsonl_count == lancedb_count,
            "jsonl_file": str(self._memory_file),
            "lancedb_stats": lancedb_stats,
        }


async def main() -> int:
    """Main entry point for the migration script."""
    parser = argparse.ArgumentParser(
        description="Migrate semantic memory from JSONL to LanceDB",
    )
    parser.add_argument(
        "--workspace",
        required=True,
        help="Workspace root path",
    )
    parser.add_argument(
        "--memory-file",
        help="Optional explicit path to memory JSONL file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be migrated without actually migrating",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify migration by comparing counts",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    # Setup logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Run migrator
    migrator = SemanticMemoryToLanceDBMigrator(
        workspace=args.workspace,
        memory_file=args.memory_file,
        dry_run=args.dry_run,
    )

    if args.verify:
        logger.info("Verifying migration...")
        result = await migrator.verify()
        logger.info("Verification result: %s", result)
        if result["match"]:
            logger.info("Verification PASSED: counts match")
            return 0
        else:
            logger.error(
                "Verification FAILED: JSONL=%d, LanceDB=%d",
                result["jsonl_item_count"],
                result["lancedb_record_count"],
            )
            return 1

    stats = await migrator.run()

    if stats.failed_items > 0:
        logger.error("Migration completed with %d failures", stats.failed_items)
        for error in stats.errors[:10]:
            logger.error("  - %s", error)
        return 1

    logger.info(
        "Migration successful: %d/%d items migrated",
        stats.migrated_items,
        stats.total_items,
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
