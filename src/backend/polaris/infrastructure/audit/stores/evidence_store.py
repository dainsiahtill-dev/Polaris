r"""Evidence file persistence for Director v2.

Stores evidence packages to files for audit trail and cross-agent sharing.
Maintains compatibility with original Director evidence format.

CRITICAL: All evidence is stored OUTSIDE the workspace to avoid pollution.
Storage locations (in priority order):
1. Ramdisk (X:\) if available and KERNELONE_STATE_TO_RAMDISK is enabled
2. System cache directory (%LOCALAPPDATA%\Polaris\cache or ~/.cache/polaris)
3. Explicit KERNELONE_RUNTIME_ROOT

Path structure: {runtime_base}/<metadata_dir>/projects/{workspace_key}/runtime/evidence/{task_id}/
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# No Domain entity imports: this Infrastructure layer accepts Dict[str, Any].
# The caller (task_lifecycle_service) serializes EvidencePackage.to_dict()
# before calling save_evidence(), breaking the Infrastructure->Domain dependency.
# Architectural note: This follows ACGA 2.0 rule that Infrastructure may not
# import Domain entities. The Dict[str, Any] payload is the cross-layer contract.


class EvidenceNotFoundError(Exception):
    """Raised when evidence is not found."""

    pass


class EvidenceStore:
    """File-based evidence storage for Director v2.

    Evidence files are stored OUTSIDE workspace to avoid pollution.
    Compatible with original Director evidence format for cross-version access.
    """

    def __init__(self, runtime_root: str | Path) -> None:
        """Initialize evidence store with external runtime root.

        Args:
            runtime_root: External runtime directory (from StorageLayout.runtime_root)
                         NEVER inside the workspace
        """
        self.runtime_root = Path(runtime_root).resolve()
        self.evidence_dir = self.runtime_root / "evidence"

    def _get_task_evidence_dir(self, task_id: str) -> Path:
        """Get evidence directory for a task.

        Raises:
            ValueError: If task_id contains path traversal patterns.
        """
        # Reject obvious path traversal patterns up front
        if ".." in task_id or "/" in task_id or "\\" in task_id:
            raise ValueError(f"Invalid task_id: {task_id!r}")

        path = self.evidence_dir / task_id

        # Defense-in-depth: resolve and verify the path stays within evidence_dir.
        # This catches edge cases like "foo/../bar" that the simple check misses.
        try:
            path.resolve().relative_to(self.evidence_dir.resolve())
        except ValueError:
            raise ValueError(f"Invalid task_id: {task_id!r}")

        path.mkdir(parents=True, exist_ok=True)
        return path

    def save_evidence(
        self,
        package: dict[str, Any],
        *,
        run_id: str = "",
        stage: str = "execution",
    ) -> dict[str, Any]:
        """Save evidence package to file.

        Args:
            package: Evidence package as dictionary. Required keys: ``task_id``,
                     ``iteration``. Duck-typed to accept both plain dicts and
                     objects with a ``to_dict()`` method (e.g. EvidencePackage).
            run_id: Optional run identifier
            stage: Stage name (planning, execution, verification)

        Returns:
            Metadata about saved file including path, hash, size
        """
        # Duck-type: accept EvidencePackage or plain dict.
        # ACGA 2.0: Infrastructure accepts Dict[str, Any]; caller serializes.
        to_dict_fn = getattr(package, "to_dict", None)
        if callable(to_dict_fn):
            payload: dict[str, Any] = to_dict_fn()
        else:
            payload = dict(package)  # shallow copy to avoid mutation

        # Extract required fields
        task_id: str = payload["task_id"]
        iteration: int = payload["iteration"]

        task_dir = self._get_task_evidence_dir(task_id)

        # Primary evidence file: evidence_{iteration}.json
        evidence_file = task_dir / f"evidence_{iteration:05d}.json"

        # Also save as CONTEXT file for compatibility with old Director
        nonce = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        run_id = run_id or "run"
        context_file = task_dir / (f"CONTEXT_{task_id}_{run_id}_{iteration:05d}_{stage}_{nonce}.json")

        # Add metadata (on the shallow copy, not the original)
        payload["_metadata"] = {
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "store_version": "2.0",
            "stage": stage,
            "run_id": run_id,
        }

        # Serialize
        serialized = json.dumps(payload, ensure_ascii=False, indent=2, default=str)

        # Write both files
        for file_path in [evidence_file, context_file]:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(serialized)

        return {
            "evidence_path": str(evidence_file),
            "context_path": str(context_file),
            "size": len(serialized),
            "task_id": task_id,
            "iteration": iteration,
        }

    def load_evidence(self, task_id: str, iteration: int = 0) -> dict[str, Any]:
        """Load evidence package from file.

        Args:
            task_id: Task identifier
            iteration: Build iteration number

        Returns:
            Evidence package as dictionary

        Raises:
            EvidenceNotFoundError: If evidence file doesn't exist
        """
        task_dir = self._get_task_evidence_dir(task_id)
        evidence_file = task_dir / f"evidence_{iteration:05d}.json"

        if not evidence_file.exists():
            raise EvidenceNotFoundError(f"Evidence not found for task {task_id} iteration {iteration}")

        with open(evidence_file, encoding="utf-8") as f:
            return json.load(f)

    def load_latest_evidence(self, task_id: str) -> dict[str, Any]:
        """Load latest evidence for a task.

        Args:
            task_id: Task identifier

        Returns:
            Latest evidence package as dictionary
        """
        task_dir = self._get_task_evidence_dir(task_id)
        if not task_dir.exists():
            raise EvidenceNotFoundError(f"No evidence found for task {task_id}")

        # Find all evidence files and sort by iteration
        evidence_files = sorted(task_dir.glob("evidence_*.json"))
        if not evidence_files:
            raise EvidenceNotFoundError(f"No evidence files for task {task_id}")

        latest = evidence_files[-1]
        with open(latest, encoding="utf-8") as f:
            return json.load(f)

    def list_evidence(self, task_id: str) -> list[dict[str, Any]]:
        """List all evidence iterations for a task.

        Args:
            task_id: Task identifier

        Returns:
            List of evidence metadata
        """
        task_dir = self._get_task_evidence_dir(task_id)
        if not task_dir.exists():
            return []

        results = []
        for evidence_file in sorted(task_dir.glob("evidence_*.json")):
            try:
                stat = evidence_file.stat()
                # Extract iteration from filename
                iteration = int(evidence_file.stem.split("_")[1])
                results.append(
                    {
                        "task_id": task_id,
                        "iteration": iteration,
                        "path": str(evidence_file),
                        "size": stat.st_size,
                        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    }
                )
            except (ValueError, IndexError, OSError):
                continue

        return results

    def append_to_evidence_log(
        self,
        task_id: str,
        entry: dict[str, Any],
    ) -> str:
        """Append entry to evidence log (JSONL format).

        Args:
            task_id: Task identifier
            entry: Log entry to append

        Returns:
            Path to log file
        """
        task_dir = self._get_task_evidence_dir(task_id)
        log_file = task_dir / "evidence_log.jsonl"

        line = json.dumps(entry, ensure_ascii=False, default=str)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")

        return str(log_file)

    def read_evidence_log(self, task_id: str) -> list[dict[str, Any]]:
        """Read evidence log for a task.

        Args:
            task_id: Task identifier

        Returns:
            List of log entries
        """
        # Validate task_id without creating directories (read-only method)
        if ".." in task_id or "/" in task_id or "\\" in task_id:
            raise ValueError(f"Invalid task_id: {task_id!r}")
        task_dir = self.evidence_dir / task_id
        # Verify path stays within evidence_dir
        try:
            task_dir.resolve().relative_to(self.evidence_dir.resolve())
        except ValueError:
            raise ValueError(f"Invalid task_id: {task_id!r}")
        log_file = task_dir / "evidence_log.jsonl"

        if not log_file.exists():
            return []

        entries = []
        with open(log_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

        return entries

    def export_for_role_agent(
        self,
        task_id: str,
        role: str,
        output_dir: str | None = None,
    ) -> str:
        """Export evidence package for another role agent.

        Creates a summary file optimized for specific role consumption.

        Args:
            task_id: Task identifier
            role: Target role (qa, pm, director, etc.)
            output_dir: Optional output directory

        Returns:
            Path to exported file
        """
        evidence = self.load_latest_evidence(task_id)

        # Create role-specific summary
        if role == "qa":
            summary = self._create_qa_summary(evidence)
        elif role == "pm":
            summary = self._create_pm_summary(evidence)
        elif role == "director":
            summary = self._create_director_summary(evidence)
        else:
            summary = evidence

        # Determine output path
        out_path = Path(output_dir) if output_dir else self._get_task_evidence_dir(task_id)

        export_file = out_path / f"evidence_for_{role}.json"
        with open(export_file, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2, default=str)

        return str(export_file)

    def _create_qa_summary(self, evidence: dict[str, Any]) -> dict[str, Any]:
        """Create QA-focused evidence summary."""
        return {
            "task_id": evidence.get("task_id"),
            "acceptance": evidence.get("acceptance"),
            "file_changes": evidence.get("file_changes", []),
            "verification_results": evidence.get("verification_results", []),
            "policy_violations": evidence.get("policy_violations", []),
            "summary": evidence.get("summary"),
        }

    def _create_pm_summary(self, evidence: dict[str, Any]) -> dict[str, Any]:
        """Create PM-focused evidence summary."""
        return {
            "task_id": evidence.get("task_id"),
            "iteration": evidence.get("iteration"),
            "file_changes": len(evidence.get("file_changes", [])),
            "tool_outputs": len(evidence.get("tool_outputs", [])),
            "verification_passed": all(vr.get("passed", False) for vr in evidence.get("verification_results", [])),
            "has_critical_issues": evidence.get("has_critical_issues", False),
        }

    def _create_director_summary(self, evidence: dict[str, Any]) -> dict[str, Any]:
        """Create Director-focused evidence summary."""
        return {
            "task_id": evidence.get("task_id"),
            "iteration": evidence.get("iteration"),
            "file_changes": evidence.get("file_changes", []),
            "tool_outputs": evidence.get("tool_outputs", []),
            "llm_interactions": evidence.get("llm_interactions", []),
            "audit_entries": evidence.get("audit_entries", []),
        }
