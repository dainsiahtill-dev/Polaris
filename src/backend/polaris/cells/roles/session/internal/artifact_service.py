"""Role Session Artifact Service - Artifact management for role sessions."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from polaris.cells.roles.session.internal.storage_paths import (
    resolve_preferred_logical_prefix,
)
from polaris.infrastructure.storage import LocalFileSystemAdapter
from polaris.kernelone.fs import KernelFileSystem

logger = logging.getLogger(__name__)


@dataclass
class Artifact:
    """An artifact in a role session."""

    id: str
    type: str
    content: str
    metadata: dict[str, Any]
    created_at: str
    session_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Artifact:
        return cls(**data)


class RoleSessionArtifactService:
    """Service for managing role session artifacts."""

    ARTIFACT_TYPES = {
        "code",  # Generated code
        "document",  # Documents (markdown, etc.)
        "diagram",  # Diagrams and visuals
        "plan",  # Implementation plans
        "review",  # Code review comments
        "test",  # Test cases
        "config",  # Configuration files
        "log",  # Session logs
    }

    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace).resolve()
        self._kernel_fs = KernelFileSystem(str(self.workspace), LocalFileSystemAdapter())
        self._base_rel_dir = resolve_preferred_logical_prefix(
            self._kernel_fs,
            runtime_prefix="runtime/role_sessions",
            workspace_fallback_prefix="workspace/runtime/role_sessions",
        )
        self.base_dir = self._kernel_fs.resolve_path(self._base_rel_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _artifact_rel_path(self, session_id: str, artifact_id: str) -> str:
        return f"{self._base_rel_dir}/{session_id}/artifacts/{artifact_id}.json"

    def _artifacts_dir_path(self, session_id: str) -> Path:
        return self._kernel_fs.resolve_path(f"{self._base_rel_dir}/{session_id}/artifacts")

    def get_session_dir(self, session_id: str) -> Path:
        """Get directory for a session."""
        return self._kernel_fs.resolve_path(f"{self._base_rel_dir}/{session_id}")

    def write_artifact(
        self,
        session_id: str,
        artifact_type: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> Artifact:
        """Write an artifact to a session."""
        if artifact_type not in self.ARTIFACT_TYPES:
            logger.warning("Unknown artifact type: %s", artifact_type)

        artifact_id = f"{artifact_type}_{uuid.uuid4().hex[:8]}"
        artifact = Artifact(
            id=artifact_id,
            type=artifact_type,
            content=content,
            metadata=metadata or {},
            created_at=datetime.now(timezone.utc).isoformat(),
            session_id=session_id,
        )

        artifact_rel_path = self._artifact_rel_path(session_id, artifact_id)
        self._kernel_fs.write_text(
            artifact_rel_path,
            json.dumps(artifact.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        logger.info("Created artifact %s in session %s", artifact_id, session_id)
        return artifact

    def read_artifact(self, session_id: str, artifact_id: str) -> Artifact | None:
        """Read a specific artifact."""
        artifact_rel_path = self._artifact_rel_path(session_id, artifact_id)
        if not self._kernel_fs.exists(artifact_rel_path):
            return None

        try:
            data = json.loads(
                self._kernel_fs.read_text(
                    artifact_rel_path,
                    encoding="utf-8",
                )
            )
            if not isinstance(data, dict):
                logger.warning(
                    "Invalid artifact payload type for %s in session %s: %s",
                    artifact_id,
                    session_id,
                    type(data).__name__,
                )
                return None
            artifact = Artifact.from_dict(data)
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            logger.warning(
                "Failed to read artifact %s in session %s: %s",
                artifact_id,
                session_id,
                exc,
            )
            return None
        except TypeError as exc:
            logger.warning(
                "Artifact payload schema mismatch for %s in session %s: %s",
                artifact_id,
                session_id,
                exc,
            )
            return None
        return artifact

    def list_artifacts(
        self,
        session_id: str,
        artifact_type: str | None = None,
    ) -> list[Artifact]:
        """List artifacts in a session."""
        artifacts_dir = self._artifacts_dir_path(session_id)
        if not artifacts_dir.is_dir():
            return []

        artifacts: list[Artifact] = []
        for f in artifacts_dir.glob("*.json"):
            try:
                rel = self._kernel_fs.to_logical_path(str(f))
                data = json.loads(self._kernel_fs.read_text(rel, encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("artifact payload must be JSON object")
                artifact = Artifact.from_dict(data)

                # Filter by type if specified
                if artifact_type and artifact.type != artifact_type:
                    continue

                artifacts.append(artifact)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError, OSError) as exc:
                logger.warning("Failed to load artifact %s: %s", f, exc)

        # Sort by creation time
        artifacts.sort(key=lambda a: a.created_at, reverse=True)
        return artifacts

    def delete_artifact(self, session_id: str, artifact_id: str) -> bool:
        """Delete an artifact."""
        artifact_rel_path = self._artifact_rel_path(session_id, artifact_id)

        if not self._kernel_fs.exists(artifact_rel_path):
            return False
        removed = self._kernel_fs.remove(artifact_rel_path, missing_ok=True)
        if removed:
            logger.info("Deleted artifact %s from session %s", artifact_id, session_id)
        return removed

    def export_artifacts(
        self,
        session_id: str,
        target_dir: Path,
        artifact_type: str | None = None,
    ) -> list[Path]:
        """Export artifacts to a workspace-relative directory."""
        artifacts = self.list_artifacts(session_id, artifact_type)
        target_rel = self._kernel_fs.to_workspace_relative_path(str(Path(target_dir).resolve()))

        exported: list[Path] = []
        for artifact in artifacts:
            # Determine file extension based on type
            ext = self._get_file_extension(artifact.type)
            filename = f"{artifact.id}{ext}"
            export_rel = f"{target_rel}/{filename}" if target_rel not in {"", "."} else filename
            self._kernel_fs.workspace_write_text(export_rel, artifact.content, encoding="utf-8")
            exported.append((self.workspace / export_rel).resolve())

        return exported

    def _get_file_extension(self, artifact_type: str) -> str:
        """Get file extension for artifact type."""
        extensions = {
            "code": ".py",
            "document": ".md",
            "diagram": ".mmd",
            "plan": ".md",
            "review": ".md",
            "test": ".py",
            "config": ".json",
            "log": ".log",
        }
        return extensions.get(artifact_type, ".txt")
