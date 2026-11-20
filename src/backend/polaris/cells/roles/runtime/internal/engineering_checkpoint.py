"""Bounded, content-addressed copies of the granted workspace portion of a verified candidate."""

from __future__ import annotations

import hashlib
import io
import os
import re
import stat
import tarfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from polaris.kernelone.fs import (
    GuardedRegularFileSnapshotError,
    guarded_compare_and_create_regular_file,
    guarded_create_directory,
    guarded_remove_tree,
    read_guarded_regular_file_snapshot,
)
from polaris.kernelone.fs.guarded_metadata import read_guarded_metadata

_MAX_BYTES = 64 * 1024 * 1024
_MAX_ENTRIES = 4096
_OMITTED = frozenset(
    {
        ".git",
        ".polaris",
        ".cache",
        ".npm",
        ".pnpm-store",
        ".gradle",
        ".venv",
        ".pytest_cache",
        "__pycache__",
        "node_modules",
    }
)


class CheckpointUnavailableError(ValueError):
    """A verified check did not yield a complete checkpoint of its declared file scope."""


def _checkpoint_entries(
    workspace: Path,
    result_manifest: Mapping[str, object],
    granted_roots: Iterable[Path],
    excluded_roots: Iterable[Path],
) -> dict[str, dict[str, Any]]:
    roots = tuple(root for root in granted_roots if root.is_relative_to(workspace))
    excluded = (workspace / "runtime", *excluded_roots)
    selected = {}
    for name, item in sorted(result_manifest.items()):
        path = Path(name)
        if path == workspace or not any(path.is_relative_to(root) for root in roots):
            continue
        relative = path.relative_to(workspace)
        if _OMITTED.intersection(relative.parts) or any(path.is_relative_to(root) for root in excluded):
            continue
        if ".." in relative.parts or not isinstance(item, dict) or not isinstance(item.get("metadata"), list):
            raise CheckpointUnavailableError("unsupported_manifest")
        selected[relative.as_posix()] = item
        if len(selected) > _MAX_ENTRIES:
            raise CheckpointUnavailableError("entry_budget")
    return selected


def save_checkpoint(
    workspace: Path,
    control_root: Path,
    result_manifest: Mapping[str, object],
    granted_roots: Iterable[Path],
    excluded_roots: Iterable[Path],
) -> dict[str, Any]:
    selected = _checkpoint_entries(workspace, result_manifest, granted_roots, excluded_roots)
    files = 0
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for relative, item in selected.items():
            metadata = item["metadata"]
            member = tarfile.TarInfo(relative)
            member.mode = metadata[2] & 0o777
            if stat.S_ISDIR(metadata[2]):
                member.type = tarfile.DIRTYPE
                archive.addfile(member)
                continue
            if not stat.S_ISREG(metadata[2]) or metadata[3] != 1:
                raise CheckpointUnavailableError("unsupported_file_type_or_hardlink")
            if buffer.tell() + metadata[4] + tarfile.BLOCKSIZE > _MAX_BYTES:
                raise CheckpointUnavailableError("byte_budget")
            observed = read_guarded_regular_file_snapshot(workspace, relative, max_bytes=_MAX_BYTES)
            if [observed.device, observed.inode, observed.size, observed.mtime_ns, observed.ctime_ns] != [
                metadata[0],
                metadata[1],
                *metadata[4:7],
            ] or hashlib.sha256(observed.content).hexdigest() != item.get("sha256"):
                raise CheckpointUnavailableError("candidate_changed")
            member.size = observed.size
            archive.addfile(member, io.BytesIO(observed.content))
            files += 1
    if not files:
        raise CheckpointUnavailableError("empty_file_scope")
    content = buffer.getvalue()
    if len(content) > _MAX_BYTES:
        raise CheckpointUnavailableError("byte_budget")
    digest = hashlib.sha256(content).hexdigest()
    slot = f"checkpoints/{digest}.tar"
    try:
        guarded_compare_and_create_regular_file(control_root, slot, content, max_bytes=_MAX_BYTES)
    except GuardedRegularFileSnapshotError as exc:
        if exc.code != "guarded_create_expected_mismatch":
            raise
        stored = read_guarded_regular_file_snapshot(control_root, slot, max_bytes=_MAX_BYTES)
        if stored.content != content:
            raise CheckpointUnavailableError("existing_blob_corrupt") from exc
    return {
        "status": "available",
        "format": "workspace_files_tar_v1",
        "sha256": digest,
        "archive_bytes": len(content),
        "files": files,
        "entries": len(selected),
    }


def _restore(archive: tarfile.TarFile, destination: Path, selected: dict[str, dict[str, Any]]) -> None:
    remaining = selected.copy()
    for member in archive:
        expected = remaining.pop(member.name, None)
        if expected is None or member.mode != expected["metadata"][2] & 0o777:
            raise ValueError("checkpoint member is outside the verified file scope")
        relative = Path(member.name)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not relative.parts
            or _OMITTED.intersection(relative.parts)
            or relative.as_posix() != member.name
        ):
            raise ValueError("invalid checkpoint member path")
        if member.isdir() and stat.S_ISDIR(expected["metadata"][2]):
            guarded_create_directory(str(destination), member.name, parents=True, exist_ok=True)
        elif member.isfile() and stat.S_ISREG(expected["metadata"][2]):
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError("checkpoint member bytes missing")
            with stream:
                data = stream.read()
            if hashlib.sha256(data).hexdigest() != expected.get("sha256"):
                raise ValueError("checkpoint member disagrees with verified manifest")
            saved = guarded_compare_and_create_regular_file(destination, member.name, data, max_bytes=_MAX_BYTES)
            if member.mode & 0o100:
                fd = os.open(destination / member.name, os.O_RDONLY | os.O_NOFOLLOW)
                try:
                    info = os.fstat(fd)
                    if (info.st_dev, info.st_ino, info.st_nlink) != (saved.device, saved.inode, 1):
                        raise ValueError("exported executable identity changed")
                    os.fchmod(fd, 0o700)
                finally:
                    os.close(fd)
        else:
            raise ValueError("unsupported checkpoint member")
    if remaining:
        raise ValueError("checkpoint lacks verified members")


def export_checkpoint(
    workspace: Path,
    control_root: Path,
    ref: Mapping[str, Any],
    destination: Path,
    result_manifest: Mapping[str, object],
    granted_roots: Iterable[Path],
    excluded_roots: Iterable[Path],
) -> Path:
    if (
        not destination.is_absolute()
        or destination.resolve(strict=False) != destination
        or destination.is_relative_to(workspace)
    ):
        raise ValueError("export requires a fresh canonical directory outside the workspace")
    digest = ref.get("sha256")
    if (
        ref.get("format") != "workspace_files_tar_v1"
        or not isinstance(digest, str)
        or not re.fullmatch("[a-f0-9]{64}", digest)
    ):
        raise ValueError("invalid checkpoint identity")
    saved = read_guarded_regular_file_snapshot(control_root, f"checkpoints/{digest}.tar", max_bytes=_MAX_BYTES)
    if hashlib.sha256(saved.content).hexdigest() != digest:
        raise ValueError("checkpoint archive digest mismatch")
    selected = _checkpoint_entries(workspace, result_manifest, granted_roots, excluded_roots)
    creation = guarded_create_directory(str(destination.parent), destination.name)
    owned = read_guarded_metadata(str(destination.parent), destination.name)
    if creation.target_identity != (owned.device, owned.inode):
        raise ValueError("export directory identity changed")
    try:
        with tarfile.open(fileobj=io.BytesIO(saved.content), mode="r:") as archive:
            _restore(archive, destination, selected)
    except BaseException:
        guarded_remove_tree(str(destination.parent), destination.name, expected=owned)
        raise
    return destination
