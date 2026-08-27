"""Transactional snapshot guard for one Director quality-repair candidate.

This is not a second repair writer.  It preserves the exact authorized files
before a Director tool transaction, then either accepts the verifier-proven
effect or restores the pre-candidate bytes when Factory rejects that effect.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _hash_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _safe_target(workspace: Path, relative_path: str) -> tuple[str, Path]:
    normalized = str(relative_path or "").strip().replace("\\", "/")
    if not normalized or Path(normalized).is_absolute():
        raise ValueError("candidate snapshot target must be a non-empty relative path")
    root = workspace.resolve()
    target = (root / normalized).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"candidate snapshot target escapes workspace: {normalized}")
    return normalized, target


def _read_optional_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def _restore_optional_text(path: Path, content: str | None) -> None:
    if content is None:
        if path.exists():
            if not path.is_file():
                raise OSError(f"rollback target is not a file: {path}")
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@dataclass(frozen=True, slots=True)
class _CandidateFileSnapshot:
    relative_path: str
    path: Path
    before_content: str | None
    before_sha256: str | None


class DirectorQualityRepairCandidateGuard:
    """CAS-guarded pre-candidate snapshots for Director-owned repair paths."""

    def __init__(
        self,
        *,
        candidate_id: str,
        snapshots: tuple[_CandidateFileSnapshot, ...],
    ) -> None:
        self._candidate_id = str(candidate_id or "").strip()
        self._snapshots = snapshots
        self._after_sha256: dict[str, str | None] | None = None
        self._closed = False

    @classmethod
    async def capture(
        cls,
        *,
        workspace: str | Path,
        candidate_id: str,
        target_files: list[str] | tuple[str, ...],
    ) -> DirectorQualityRepairCandidateGuard:
        root = Path(workspace).resolve()
        seen: set[str] = set()
        snapshots: list[_CandidateFileSnapshot] = []
        for raw_path in target_files:
            relative_path, target = _safe_target(root, str(raw_path or ""))
            if relative_path in seen:
                continue
            seen.add(relative_path)
            if target.exists() and not target.is_file():
                raise ValueError(f"candidate snapshot target is not a file: {relative_path}")
            content = await asyncio.to_thread(_read_optional_text, target)
            snapshots.append(
                _CandidateFileSnapshot(
                    relative_path=relative_path,
                    path=target,
                    before_content=content,
                    before_sha256=_hash_text(content) if content is not None else None,
                )
            )
        if not snapshots:
            raise ValueError("candidate snapshot requires at least one authorized target")
        return cls(candidate_id=candidate_id, snapshots=tuple(snapshots))

    async def seal_effect(self) -> dict[str, Any]:
        if self._closed:
            return self._receipt(status="closed", reason="candidate_guard_already_closed")
        after: dict[str, str | None] = {}
        for snapshot in self._snapshots:
            content = await asyncio.to_thread(_read_optional_text, snapshot.path)
            after[snapshot.relative_path] = _hash_text(content) if content is not None else None
        self._after_sha256 = after
        return self._receipt(status="sealed", reason="candidate_effect_hashes_captured")

    def accept(self, *, reason: str) -> dict[str, Any]:
        if self._closed:
            return self._receipt(status="closed", reason="candidate_guard_already_closed")
        self._closed = True
        return self._receipt(status="accepted", reason=reason)

    async def rollback(self, *, reason: str) -> dict[str, Any]:
        if self._closed:
            return self._receipt(status="closed", reason="candidate_guard_already_closed")
        if self._after_sha256 is None:
            self._closed = True
            return self._receipt(status="aborted_unsealed", reason="candidate_effect_not_sealed")

        affected = [
            snapshot
            for snapshot in self._snapshots
            if self._after_sha256.get(snapshot.relative_path) != snapshot.before_sha256
        ]
        drifted: list[str] = []
        for snapshot in affected:
            content = await asyncio.to_thread(_read_optional_text, snapshot.path)
            current_hash = _hash_text(content) if content is not None else None
            if current_hash != self._after_sha256.get(snapshot.relative_path):
                drifted.append(snapshot.relative_path)
        if drifted:
            self._closed = True
            return self._receipt(
                status="aborted_state_drift",
                reason=reason,
                drifted_files=drifted,
            )

        restored: list[str] = []
        failed: list[str] = []
        for snapshot in affected:
            try:
                await asyncio.to_thread(
                    _restore_optional_text,
                    snapshot.path,
                    snapshot.before_content,
                )
                restored_content = await asyncio.to_thread(_read_optional_text, snapshot.path)
                restored_hash = _hash_text(restored_content) if restored_content is not None else None
                if restored_hash != snapshot.before_sha256:
                    failed.append(snapshot.relative_path)
                else:
                    restored.append(snapshot.relative_path)
            except (OSError, UnicodeError, ValueError):
                failed.append(snapshot.relative_path)
        self._closed = True
        return self._receipt(
            status="restored" if not failed else "partial",
            reason=reason,
            restored_files=restored,
            failed_files=failed,
        )

    def _receipt(
        self,
        *,
        status: str,
        reason: str,
        restored_files: list[str] | None = None,
        failed_files: list[str] | None = None,
        drifted_files: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "schema_version": "director.quality_repair_candidate_guard.v1",
            "candidate_id": self._candidate_id,
            "status": status,
            "reason": str(reason or "").strip(),
            "target_files": [snapshot.relative_path for snapshot in self._snapshots],
            "before_sha256": {
                snapshot.relative_path: snapshot.before_sha256 for snapshot in self._snapshots
            },
            "after_sha256": dict(self._after_sha256 or {}),
            "affected_files": [
                snapshot.relative_path
                for snapshot in self._snapshots
                if self._after_sha256 is not None
                and self._after_sha256.get(snapshot.relative_path) != snapshot.before_sha256
            ],
            "restored_files": list(restored_files or []),
            "failed_files": list(failed_files or []),
            "drifted_files": list(drifted_files or []),
        }


__all__ = ["DirectorQualityRepairCandidateGuard"]
