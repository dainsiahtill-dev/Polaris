"""Immutable Snapshot for Context Projection.

Provides immutable snapshot functionality for ContextOS projection consistency
measurement and audit/replay capabilities per Blueprint v2.1.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ConcurrentModificationError(Exception):
    """Raised when a snapshot is modified concurrently (optimistic lock failure)."""

    pass


@dataclass
class ImmutableSnapshot:
    """Context projection immutable snapshot.

    Attributes:
        version: Snapshot schema version (2.1.0).
        timestamp: ISO format timestamp when snapshot was created.
        input_hash: SHA256 hash prefix (16 chars) of input messages.
        output_hash: SHA256 hash prefix (16 chars) of output projection summary.
        projection_summary: Summary of projection output (not full data).
        content_hash: SHA256 hash of the full snapshot content for idempotency checks.
        version_number: Monotonic version number for optimistic locking.
    """

    version: str = "2.1.0"
    timestamp: str = ""
    input_hash: str = ""
    output_hash: str = ""
    projection_summary: dict | None = None
    content_hash: str = field(default="", compare=False)
    version_number: int = field(default=0, compare=False)

    def __post_init__(self) -> None:
        if self.timestamp == "":
            self.timestamp = datetime.now(timezone.utc).isoformat()
        if self.projection_summary is None:
            self.projection_summary = {}
        # Compute content hash if not already set
        if not self.content_hash:
            self.content_hash = self._compute_content_hash()

    def to_dict(self) -> dict:
        """Convert snapshot to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ImmutableSnapshot:
        """Create snapshot from dictionary."""
        return cls(**data)

    def _compute_content_hash(self) -> str:
        """Compute SHA256 hash of snapshot content (excluding content_hash field).

        Returns:
            Hexadecimal hash string (32 characters).
        """
        # Create a copy without content_hash for hashing
        data = {
            "version": self.version,
            "timestamp": self.timestamp,
            "input_hash": self.input_hash,
            "output_hash": self.output_hash,
            "projection_summary": self.projection_summary,
            "version_number": self.version_number,
        }
        content = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:32]

    def is_content_equivalent(self, other: ImmutableSnapshot) -> bool:
        """Check if this snapshot has the same content as another.

        Args:
            other: Another ImmutableSnapshot to compare with.

        Returns:
            True if content hashes match, False otherwise.
        """
        return self.content_hash == other.content_hash


class SnapshotStore:
    """Immutable snapshot storage.

    Each snapshot is written once and never modified.
    SHA256 checksums are generated for integrity verification.

    Storage location: resolved via the 3-layer architecture (Workspace persistent layer).
    Callers must provide an explicit ``base_path`` resolved through
    ``resolve_workspace_persistent_path(workspace, "workspace/meta/context_snapshots")``.
    """

    def __init__(self, base_path: Path) -> None:
        """Initialize snapshot store.

        Args:
            base_path: Absolute base directory for storing snapshots.
                       Must be pre-resolved through the storage layout layer
                       (e.g. ``resolve_workspace_persistent_path``).
        """
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
        # Track content hashes for idempotency checks
        self._content_hashes: set[str] = set()
        # Track version numbers for optimistic locking
        self._version_counter: int = 0
        # Async lock for concurrent write protection
        self._async_lock = asyncio.Lock()

    def _check_idempotent(self, snapshot: ImmutableSnapshot) -> bool:
        """Check if a snapshot with the same content already exists.

        Args:
            snapshot: The snapshot to check.

        Returns:
            True if content hash already exists, False otherwise.
        """
        return snapshot.content_hash in self._content_hashes

    def save(self, snapshot: ImmutableSnapshot) -> Path:
        """Save snapshot to disk.

        Each snapshot is written exactly once to prevent overwrites.
        A SHA256 checksum file is generated alongside the snapshot.

        Args:
            snapshot: The immutable snapshot to save.

        Returns:
            Path to the saved snapshot file.

        Raises:
            FileExistsError: If snapshot with same timestamp already exists.
        """
        timestamp = snapshot.timestamp.replace(":", "-").replace(".", "-")
        filename = f"{timestamp}.json"
        filepath = self.base_path / filename

        if filepath.exists():
            raise FileExistsError(f"Snapshot already exists: {filepath}")

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(snapshot.to_dict(), f, indent=2, ensure_ascii=False)

        checksum_path = filepath.with_suffix(".json.sha256")
        content = filepath.read_bytes()
        checksum = hashlib.sha256(content).hexdigest()
        checksum_path.write_text(checksum, encoding="utf-8")

        # Track content hash for idempotency
        self._content_hashes.add(snapshot.content_hash)

        return filepath

    async def save_async(self, snapshot: ImmutableSnapshot) -> Path:
        """Save snapshot to disk asynchronously with idempotency check.

        Each snapshot is written exactly once to prevent overwrites.
        A SHA256 checksum file is generated alongside the snapshot.

        Args:
            snapshot: The immutable snapshot to save.

        Returns:
            Path to the saved snapshot file.

        Raises:
            FileExistsError: If snapshot with same timestamp already exists.
            ConcurrentModificationError: If content hash already exists (idempotent write).
        """
        async with self._async_lock:
            # Idempotency check
            if self._check_idempotent(snapshot):
                raise ConcurrentModificationError(
                    f"Snapshot with content hash {snapshot.content_hash} already exists. "
                    "This is an idempotent write - content has already been saved."
                )

            timestamp = snapshot.timestamp.replace(":", "-").replace(".", "-")
            filename = f"{timestamp}.json"
            filepath = self.base_path / filename

            if filepath.exists():
                raise FileExistsError(f"Snapshot already exists: {filepath}")

            # Assign version number for optimistic locking
            self._version_counter += 1
            snapshot.version_number = self._version_counter

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(snapshot.to_dict(), f, indent=2, ensure_ascii=False)

            checksum_path = filepath.with_suffix(".json.sha256")
            content = filepath.read_bytes()
            checksum = hashlib.sha256(content).hexdigest()
            checksum_path.write_text(checksum, encoding="utf-8")

            # Track content hash for idempotency
            self._content_hashes.add(snapshot.content_hash)

            return filepath

    def load(self, timestamp: str) -> ImmutableSnapshot:
        """Load snapshot by timestamp.

        Args:
            timestamp: ISO format timestamp of the snapshot.

        Returns:
            The loaded immutable snapshot.

        Raises:
            FileNotFoundError: If snapshot file does not exist.
        """
        normalized_ts = timestamp.replace(":", "-").replace(".", "-")
        filepath = self.base_path / f"{normalized_ts}.json"
        with open(filepath, encoding="utf-8") as f:
            return ImmutableSnapshot.from_dict(json.load(f))

    def verify(self, timestamp: str) -> bool:
        """Verify snapshot integrity using SHA256 checksum.

        Args:
            timestamp: ISO format timestamp of the snapshot.

        Returns:
            True if snapshot exists and checksum matches, False otherwise.
        """
        normalized_ts = timestamp.replace(":", "-").replace(".", "-")
        filepath = self.base_path / f"{normalized_ts}.json"
        checksum_path = filepath.with_suffix(".json.sha256")

        if not filepath.exists() or not checksum_path.exists():
            return False

        content = filepath.read_bytes()
        checksum = hashlib.sha256(content).hexdigest()
        expected = checksum_path.read_text(encoding="utf-8")

        return checksum == expected

    def list_snapshots(self) -> list[str]:
        """List all available snapshot timestamps.

        Returns:
            List of ISO format timestamps for available snapshots.
        """
        timestamps: list[str] = []
        for filepath in self.base_path.glob("*.json"):
            if filepath.suffix == ".sha256":
                continue
            ts = filepath.stem.replace("-", ":").replace(".json", "")
            timestamps.append(ts)
        return sorted(timestamps)


def compute_hash(data: list[dict] | dict | str, prefix_len: int = 16) -> str:
    """Compute SHA256 hash of data.

    Args:
        data: Data to hash (dict, list, or string).
        prefix_len: Length of hash prefix to return.

    Returns:
        Hexadecimal hash string (prefix_len characters).
    """
    content = json.dumps(data, sort_keys=True, default=str) if isinstance(data, (dict, list)) else str(data)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:prefix_len]


async def verify_context_projection_consistency(
    gateway: Any,
    test_input: list[dict],
    num_runs: int = 10,
) -> tuple[bool, float]:
    """Verify Context projection consistency.

    Runs projection multiple times with identical input and checks
    that output hashes remain consistent.

    Args:
        gateway: RoleContextGateway instance.
        test_input: Input messages for projection.
        num_runs: Number of projection runs to perform.

    Returns:
        Tuple of (is_consistent, consistency_rate).
        consistency_rate >= 0.995 (99.5%) is considered passing.
    """
    snapshots: list[ImmutableSnapshot] = []

    for _ in range(num_runs):
        result = await gateway.build_context(test_input)
        input_hash = compute_hash(test_input)
        output_summary = {
            "head_anchor": result.head_anchor[:100] if result.head_anchor else "",
            "tail_anchor": result.tail_anchor[:100] if result.tail_anchor else "",
            "num_events": len(result.active_window) if result.active_window else 0,
            "num_artifacts": len(result.artifact_stubs) if result.artifact_stubs else 0,
            "num_episodes": len(result.episode_cards) if result.episode_cards else 0,
        }
        output_hash = compute_hash(output_summary)

        snapshot = ImmutableSnapshot(
            version="2.1.0",
            timestamp=datetime.now(timezone.utc).isoformat(),
            input_hash=input_hash,
            output_hash=output_hash,
            projection_summary=output_summary,
        )
        snapshots.append(snapshot)

    unique_output_hashes = {s.output_hash for s in snapshots}
    consistency_rate = 1.0 - (len(unique_output_hashes) / num_runs)

    return consistency_rate >= 0.995, consistency_rate
