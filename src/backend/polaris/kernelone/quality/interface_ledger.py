"""Cross-file interface ledger (组合律 / assume-guarantee across parents).

When a multi-task plan has tasks that legitimately share files (a base task
plus an enhancement task — the common incremental-development shape), each PM
task is fissioned by the CE in isolation. With no shared contract, every parent
invents its own interface identifiers for the same artifact: live I3-r14 had one
parent name a canvas ``id="game"`` and a sibling parent name the same canvas
``id="gameCanvas"``. The enhancement step then clobbered the base step's
markers, the base step's QA verify failed, and the product shipped non-running
(``main.js`` called ``getElementById('game')`` while ``index.html`` exposed
``id="gameCanvas"``).

Local per-file ``grep`` verify is structurally blind to that drift — every step
passes its own clauses while the composition is broken. This ledger establishes
ONE interface contract per file: the first parent to declare a file's public
identifiers freezes them, and later parents reuse the exact names.

Language-agnostic by construction: the ledger only ever stores the CE's own
declared ``interface_names``/``signatures`` strings, never any HTML/JS-specific
parsing — honoring the "no business code in Polaris" rule. It is best-effort
prevention (raises coherence probability for the weak cloud CE); QA and the
claim-time punch list remain the backstop.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import stat
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from polaris.kernelone.fs.jsonl.locking import file_lock
from polaris.kernelone.fs.text_ops import write_json_atomic
from polaris.kernelone.quality.file_ownership_ledger import normalize_file_ownership_target
from polaris.kernelone.storage.io_paths import resolve_artifact_path

if TYPE_CHECKING:
    from polaris.kernelone.quality.cross_artifact_interfaces import SymbolIndexSnapshot

logger = logging.getLogger(__name__)

_LEDGER_REL_PATH = "runtime/contracts/interface_ledger.json"
_SCHEMA_VERSION = "interface-ledger/1"

# In-process serialization for the load-modify-write below. ``write_json_atomic``
# makes the FILE write atomic but NOT the read-modify-write, so without a guard two
# concurrent CEConsumer fission threads (KERNELONE_TASK_MARKET_ROLE_POOLS includes
# chief_engineer + concurrency>1) both load the same baseline and the later write
# clobbers the earlier's first-writer-wins entries (lost write). Mirrors the guard
# in ``file_ownership_ledger`` — see that module for the cross-process rationale.
_PROCESS_LOCK = threading.Lock()


@contextmanager
def _ledger_write_lock(ledger_path: str) -> Iterator[None]:
    """Serialize the whole load-modify-write of the ledger keyed by its path.

    The thread lock serializes concurrent fission threads in this process; the
    cross-process ``file_lock`` serializes other processes. It uses a DISTINCT
    ``.rmw.lock`` sidecar — NOT the ``{path}.lock`` that ``write_json_atomic``
    acquires internally — so the inner atomic write can still take its own lock
    without self-deadlocking. Best-effort: a failed cross-process acquisition still
    runs the body (degrading to the prior behaviour) rather than aborting fission.
    """
    with _PROCESS_LOCK, file_lock(f"{ledger_path}.rmw.lock") as acquired:
        if not acquired:
            logger.warning("interface ledger rmw lock not acquired (non-fatal); proceeding")
        yield


def _normalize_target(raw: Any) -> str:
    """Use the same lexical key as the file-ownership ledger."""

    return cast("str", (normalize_file_ownership_target(raw)))


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    rows: list[str] = []
    seen: set[str] = set()
    for item in value:
        token = str(item or "").strip()
        if token and token not in seen:
            seen.add(token)
            rows.append(token)
    return rows


@dataclass(frozen=True, slots=True)
class DeclaredInterfaceValidationIssue:
    """Typed validation issue for CE-declared interface ledger facts."""

    code: str
    message: str
    target: str
    identifier: str = ""
    signature: str = ""

    def to_error_message(self) -> str:
        """Render the legacy artifact-quality error string."""

        return f"Artifact quality scan failed: {self.message}"

    def to_artifact_quality_issue(self) -> dict[str, Any]:
        """Project this ledger fact into ArtifactQualityIssue-compatible payload."""

        metadata: dict[str, Any] = {
            "raw": self.to_error_message(),
            "target_file": self.target,
        }
        if self.identifier:
            metadata["identifier"] = self.identifier
        if self.signature:
            metadata["signature"] = self.signature
        return {
            "code": self.code,
            "message": self.message,
            "path": self.target,
            "severity": "error",
            "source": "declared_interface_ledger",
            "metadata": metadata,
        }


def _ledger_path(workspace: str, cache_root: str) -> str:
    return cast("str", (resolve_artifact_path(workspace, cache_root, _LEDGER_REL_PATH)))


@dataclass(frozen=True)
class WorkspaceTextReadSnapshot:
    """One actual UTF-8 read, with confinement and drift evidence."""

    path: str
    status: str
    detail: str = ""
    text: str = ""
    content_sha256: str = ""


class _ConfinedReadPath(Path):
    """Preserve the Path text-read protocol while binding its actual open."""

    _opener: Callable[[str, int], int]

    def open(
        self,
        mode: str = "r",
        buffering: int = -1,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> Any:
        if mode not in {"r", "rt"}:
            raise ValueError("confined snapshot paths are read-only")
        return open(
            str(self),
            mode=mode,
            buffering=buffering,
            encoding=encoding,
            errors=errors,
            newline=newline,
            opener=self._opener,
        )


class WorkspaceReadSnapshot:
    """Per-query read-only payloads; never persisted or shared between scans.

    Links are rejected, including lexical ancestors before normalizing '..'.
    Actual opens are descriptor-relative and no-follow at every component.
    Cached payloads, rather than later best-effort reads, drive consumers.
    """

    def __init__(self, workspace: str | Path) -> None:
        self.root = Path(workspace).resolve()
        self._reads: dict[str, WorkspaceTextReadSnapshot] = {}
        self._identities: dict[str, tuple[int, ...]] = {}
        self._root_chain: dict[Path, tuple[int, ...]] = {}
        self._directories: dict[Path, tuple[int, ...]] = {}
        self._binding_error = ""
        try:
            directory = self._open_workspace_root(capture=True)
            os.close(directory)
        except (OSError, RuntimeError, ValueError) as exc:
            self._binding_error = f"{type(exc).__name__}:{exc}"

    @staticmethod
    def _directory_identity(value: os.stat_result) -> tuple[int, ...]:
        # Entries may be created during a query; directory mtime/size are not
        # identity. Device/inode/mode bind the original namespace instead.
        return (value.st_dev, value.st_ino, value.st_mode)

    def _open_workspace_root(self, *, capture: bool = False) -> int:
        """Return one owned descriptor, checking every absolute ancestor."""
        if not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd:
            raise OSError("confined_read_unavailable")
        if self._binding_error:
            raise OSError(f"workspace_identity_drift:{self._binding_error}")
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        current = Path(self.root.anchor)
        directory = os.open(current, flags)
        try:
            for component in (None, *self.root.parts[1:]):
                if component is not None:
                    child: int | None = None
                    try:
                        child = os.open(component, flags, dir_fd=directory)
                        # Swap owners together; the inner finally closes the
                        # pending child on failure or the parent on transfer.
                        directory, child = child, directory
                    finally:
                        if child is not None:
                            os.close(child)
                    current /= component
                identity = self._directory_identity(os.fstat(directory))
                if capture:
                    self._root_chain[current] = identity
                elif self._root_chain.get(current) != identity:
                    raise OSError(f"workspace_identity_drift:{current}")
            return directory
        except BaseException:
            os.close(directory)
            raise

    def _check_workspace_binding(self) -> None:
        """Metadata check never substitutes for descriptor checks at open."""
        if self._binding_error:
            raise OSError(f"workspace_identity_drift:{self._binding_error}")
        for directory, identity in self._root_chain.items():
            try:
                current = directory.lstat()
            except OSError as exc:
                raise OSError(f"workspace_identity_drift:{directory}:{exc}") from exc
            if self._directory_identity(current) != identity:
                raise OSError(f"workspace_identity_drift:{directory}")

    def confined_path(self, path: Path) -> tuple[Path, os.stat_result | None]:
        self._check_workspace_binding()
        requested = path if path.is_absolute() else self.root / path
        try:
            parts = requested.relative_to(self.root).parts
        except ValueError as exc:
            raise ValueError(f"outside_workspace:{requested}") from exc
        current = self.root
        missing = False
        witness: os.stat_result | None = None
        for part in parts:
            if part == "..":
                current = current.parent
                if not current.is_relative_to(self.root):
                    raise ValueError(f"outside_workspace:{requested}")
                continue
            current /= part
            if missing:
                continue
            try:
                witness = current.lstat()
            except FileNotFoundError as exc:
                if current in self._directories:
                    raise OSError(f"directory_identity_drift:{current}:disappeared") from exc
                missing, witness = True, None
                continue
            if stat.S_ISLNK(witness.st_mode):
                raise ValueError(f"symlink_unvisited:{current}")
            if stat.S_ISDIR(witness.st_mode):
                identity = self._directory_identity(witness)
                if self._directories.setdefault(current, identity) != identity:
                    raise OSError(f"directory_identity_drift:{current}")
        return current, witness

    @staticmethod
    def _identity(value: os.stat_result) -> tuple[int, ...]:
        return (value.st_dev, value.st_ino, value.st_mode, value.st_size, value.st_mtime_ns, value.st_ctime_ns)

    def read(self, path: Path) -> WorkspaceTextReadSnapshot:
        key = str(path)
        if key in self._reads:
            return self._reads[key]
        witness: os.stat_result | None = None
        read_fd: int | None = None
        try:
            full, witness = self.confined_path(path)
            if witness is not None and not stat.S_ISREG(witness.st_mode):
                raise OSError(f"not_regular_file:{full}")
            if not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd:
                raise OSError("confined_read_unavailable")

            def opener(_name: str, flags: int) -> int:
                nonlocal read_fd
                directory = self._open_workspace_root()
                try:
                    components = full.relative_to(self.root).parts
                    current_directory = self.root
                    for component in components[:-1]:
                        child: int | None = None
                        try:
                            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                            current_directory /= component
                            if self._directories.get(current_directory) != self._directory_identity(os.fstat(child)):
                                raise OSError(f"directory_identity_drift:{current_directory}")
                            directory, child = child, directory
                        finally:
                            if child is not None:
                                os.close(child)
                    result_fd = os.open(components[-1], flags | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                    try:
                        opened = os.fstat(result_fd)
                        _, current = self.confined_path(full)
                        if (
                            witness is None
                            or current is None
                            or not stat.S_ISREG(opened.st_mode)
                            or self._identity(opened) != self._identity(witness)
                            or self._identity(opened) != self._identity(current)
                        ):
                            raise OSError(f"read_identity_drift:{full}")
                        read_fd = os.dup(result_fd)
                    except BaseException:
                        os.close(result_fd)
                        raise
                    return result_fd
                finally:
                    os.close(directory)

            read_path = _ConfinedReadPath(full)
            read_path._opener = opener
            content = read_path.read_text(encoding="utf-8", errors="strict")
            if read_fd is None:
                raise OSError(f"read_witness_unavailable:{full}")
            after = os.fstat(read_fd)
            _, current = self.confined_path(full)
            if (
                current is None
                or witness is None
                or self._identity(witness) != self._identity(after)
                or self._identity(after) != self._identity(current)
            ):
                raise OSError(f"read_identity_drift:{full}")
            result = WorkspaceTextReadSnapshot(
                str(full), "readable", text=content, content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest()
            )
            self._identities[key] = self._identity(after)
        except FileNotFoundError as exc:
            result = WorkspaceTextReadSnapshot(
                key, "missing" if witness is None else "unknown", "" if witness is None else f"read_disappeared:{exc}"
            )
        except (OSError, RuntimeError, ValueError) as exc:
            result = WorkspaceTextReadSnapshot(key, "unknown", f"{type(exc).__name__}:{exc}")
        finally:
            if read_fd is not None:
                os.close(read_fd)
        self._reads[key] = result
        return result

    def drift_reasons(self) -> tuple[str, ...]:
        """Check cached read witnesses without reopening any source content."""
        reasons: list[str] = []
        for key, identity in self._identities.items():
            try:
                _, current = self.confined_path(Path(key))
                if current is None or self._identity(current) != identity:
                    reasons.append(f"source_snapshot_drift:{key}")
            except (OSError, RuntimeError, ValueError) as exc:
                reasons.append(f"source_snapshot_drift:{key}:{type(exc).__name__}:{exc}")
        return tuple(reasons)


@dataclass(frozen=True)
class DeclaredInterfaceLedgerSnapshot:
    path: str
    status: str
    detail: str
    payload: dict[str, Any]
    content_sha256: str = ""


def _ledger_payload_failure(data: Any) -> str:
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
        return "malformed_ledger_structure"
    if data.get("schema_version", _SCHEMA_VERSION) != _SCHEMA_VERSION:
        return "unsupported_ledger_schema"
    for target, entry in data["files"].items():
        if not isinstance(entry, dict):
            return f"malformed_ledger_entry:{target}"
        for field in ("identifiers", "public_symbols", "signatures"):
            if field in entry and (
                not isinstance(entry[field], list) or any(not isinstance(item, str) for item in entry[field])
            ):
                return f"malformed_ledger_field:{target}:{field}"
    return ""


def read_declared_interface_ledger_snapshot(
    workspace: str, cache_root: str, *, reader: WorkspaceReadSnapshot | None = None
) -> DeclaredInterfaceLedgerSnapshot:
    """Return validated payload and status from one confined actual read."""
    if not cache_root:
        raise ValueError("read-only interface ledger status requires an explicit root")
    if reader is not None and reader.root != Path(workspace).resolve():
        raise ValueError("read-only snapshot reader workspace binding mismatch")
    reader = reader or WorkspaceReadSnapshot(workspace)
    # With a nonempty root this preserves the legacy logical path mapping,
    # without invoking storage initialization or consulting external caches.
    path = Path(_ledger_path(workspace, cache_root))
    read = reader.read(path)
    empty: dict[str, Any] = {"schema_version": _SCHEMA_VERSION, "files": {}}
    if read.status != "readable":
        return DeclaredInterfaceLedgerSnapshot(read.path, read.status, read.detail, empty)
    try:
        data = json.loads(read.text)
        failure = _ledger_payload_failure(data)
    except (ValueError, RuntimeError) as exc:
        failure = f"{type(exc).__name__}:{exc}"
        data = empty
    return DeclaredInterfaceLedgerSnapshot(
        read.path, "unknown" if failure else "readable", failure, empty if failure else data, read.content_sha256
    )


def read_declared_interface_ledger_status(
    workspace: str, cache_root: str, *, snapshot: DeclaredInterfaceLedgerSnapshot | None = None
) -> tuple[str, str]:
    """Project the same actual read witness; absence is optional, failure is not."""
    if not cache_root:
        raise ValueError("read-only interface ledger status requires an explicit root")
    snapshot = snapshot or read_declared_interface_ledger_snapshot(workspace, cache_root)
    return snapshot.status, snapshot.detail


def _load(workspace: str, cache_root: str) -> dict[str, Any]:
    path = _ledger_path(workspace, cache_root)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {"schema_version": _SCHEMA_VERSION, "files": {}}
    if not isinstance(data, dict):
        return {"schema_version": _SCHEMA_VERSION, "files": {}}
    files = data.get("files")
    if not isinstance(files, dict):
        data["files"] = {}
    return data


def _merge_names(existing: list[str], incoming: list[str]) -> list[str]:
    """First-writer-wins union: keep existing order, append genuinely new names."""
    seen = set(existing)
    merged = list(existing)
    for name in incoming:
        if name not in seen:
            seen.add(name)
            merged.append(name)
    return merged


def record_declared_interfaces(
    workspace: str,
    cache_root: str,
    steps: list[dict[str, Any]],
) -> None:
    """Accumulate each step's declared interface into the per-file ledger.

    Best-effort: a ledger write failure must never abort the fission path, so
    OSError is swallowed (logged). Called after the CE step gate passes, before
    the steps are published to the market.
    """
    if not steps:
        return
    ledger_path = _ledger_path(workspace, cache_root)
    # Re-read UNDER the lock and merge at write time: the entire load-modify-write
    # must be serialized, otherwise two concurrent fissions both load the same
    # baseline and the later write clobbers the earlier's first-writer-wins entries
    # (lost write) — mirrors the guard in ``file_ownership_ledger.record_file_owners``.
    with _ledger_write_lock(ledger_path):
        ledger = _load(workspace, cache_root)
        files: dict[str, Any] = ledger["files"]
        changed = False
        for step in steps:
            target = _normalize_target(step.get("target_file"))
            if not target:
                continue
            identifiers = _string_list(step.get("interface_names"))
            public_symbols = _string_list(step.get("public_symbols"))
            signatures = _string_list(step.get("signatures"))
            if not identifiers and not public_symbols and not signatures:
                continue
            entry = files.get(target)
            if not isinstance(entry, dict):
                entry = {"identifiers": [], "public_symbols": [], "signatures": [], "declared_by": []}
            entry["identifiers"] = _merge_names(_string_list(entry.get("identifiers")), identifiers)
            entry["public_symbols"] = _merge_names(_string_list(entry.get("public_symbols")), public_symbols)
            entry["signatures"] = _merge_names(_string_list(entry.get("signatures")), signatures)
            step_id = str(step.get("step_id") or "").strip()
            if step_id:
                entry["declared_by"] = _merge_names(_string_list(entry.get("declared_by")), [step_id])
            files[target] = entry
            changed = True
        if not changed:
            return
        ledger["schema_version"] = _SCHEMA_VERSION
        try:
            write_json_atomic(ledger_path, ledger)
        except OSError as exc:
            logger.warning("interface ledger write failed (non-fatal): %s", exc)


def read_declared_interfaces(
    workspace: str,
    cache_root: str,
    target_files: list[str],
    *,
    snapshot: DeclaredInterfaceLedgerSnapshot | None = None,
) -> dict[str, dict[str, Any]]:
    """Return ledger entries for the given target files (only those present)."""
    wanted = {_normalize_target(tf) for tf in target_files if _normalize_target(tf)}
    if not wanted:
        return {}
    ledger = snapshot.payload if snapshot is not None else _load(workspace, cache_root)
    files: dict[str, Any] = ledger["files"]
    declared: dict[str, dict[str, Any]] = {}
    for target in wanted:
        entry = files.get(target)
        if not isinstance(entry, dict):
            continue
        identifiers = _string_list(entry.get("identifiers"))
        public_symbols = _string_list(entry.get("public_symbols"))
        signatures = _string_list(entry.get("signatures"))
        if not identifiers and not public_symbols and not signatures:
            continue
        declared_entry: dict[str, Any] = {"identifiers": identifiers, "signatures": signatures}
        if public_symbols:
            declared_entry["public_symbols"] = public_symbols
        declared[target] = declared_entry
    return declared


def read_all_declared_interfaces(
    workspace: str,
    cache_root: str,
    *,
    exclude_target: str = "",
    snapshot: DeclaredInterfaceLedgerSnapshot | None = None,
) -> dict[str, dict[str, Any]]:
    """Return every ledger entry that declares identifiers/signatures.

    Used to surface the cross-file interface contract to the Director at exec time
    (I3-r28): the weak model must REUSE the names other files already froze instead
    of inventing mismatched ones (live: ``main.js`` calling ``getElementById('game')``
    while ``index.html`` froze ``gameCanvas``). ``exclude_target`` drops the file the
    current step owns, leaving only what it must consume.
    """
    excluded = _normalize_target(exclude_target)
    ledger = snapshot.payload if snapshot is not None else _load(workspace, cache_root)
    files: dict[str, Any] = ledger["files"]
    declared: dict[str, dict[str, Any]] = {}
    for raw_target, entry in files.items():
        target = _normalize_target(raw_target)
        if not target or target == excluded or not isinstance(entry, dict):
            continue
        identifiers = _string_list(entry.get("identifiers"))
        public_symbols = _string_list(entry.get("public_symbols"))
        signatures = _string_list(entry.get("signatures"))
        if identifiers or public_symbols or signatures:
            declared_entry = {"identifiers": identifiers, "signatures": signatures}
            if public_symbols:
                declared_entry["public_symbols"] = public_symbols
            declared[target] = declared_entry
    return declared


def validate_declared_interface_issues_against_snapshot(
    workspace: str,
    cache_root: str,
    target_files: list[str] | None = None,
    *,
    ledger_snapshot: DeclaredInterfaceLedgerSnapshot | None = None,
    source_snapshot: SymbolIndexSnapshot | None = None,
) -> tuple[DeclaredInterfaceValidationIssue, ...]:
    """Return typed issues for CE-declared interfaces missing from source snapshots.

    The ledger is intentionally language-agnostic and may contain HTML ids,
    asset keys, or other domains the source-code scanner does not yet parse.
    Unsupported files fail open: only files present in the symbol snapshot are
    checked.
    """

    from polaris.kernelone.quality.cross_artifact_interfaces import build_symbol_index_snapshot

    declared = (
        read_declared_interfaces(workspace, cache_root, target_files, snapshot=ledger_snapshot)
        if target_files is not None
        else read_all_declared_interfaces(workspace, cache_root, snapshot=ledger_snapshot)
    )
    if not declared:
        return ()

    snapshot = source_snapshot or build_symbol_index_snapshot(workspace)
    if snapshot.workspace != Path(workspace).resolve().as_posix():
        raise ValueError("source snapshot workspace binding mismatch")
    issues: list[DeclaredInterfaceValidationIssue] = []
    for target, entry in sorted(declared.items()):
        normalized_target = _normalize_target(target)
        if normalized_target not in snapshot.files:
            continue
        if source_snapshot is not None and normalized_target in snapshot.unknown_export_paths:
            continue
        symbols = snapshot.namespace_exports.get(normalized_target, ())
        actual_names = {symbol.name for symbol in symbols}
        actual_signatures = {symbol.signature for symbol in symbols if symbol.signature}
        symbols_to_check = _string_list(entry.get("public_symbols")) or _string_list(entry.get("identifiers"))
        for identifier in symbols_to_check:
            if identifier not in actual_names:
                issues.append(
                    DeclaredInterfaceValidationIssue(
                        code="declared_interface_missing",
                        message=f"declared interface {identifier!r} missing from {normalized_target}",
                        target=normalized_target,
                        identifier=identifier,
                    )
                )
        for signature in _string_list(entry.get("signatures")):
            if signature not in actual_signatures:
                issues.append(
                    DeclaredInterfaceValidationIssue(
                        code="declared_interface_signature_missing",
                        message=f"declared interface signature {signature!r} missing from {normalized_target}",
                        target=normalized_target,
                        signature=signature,
                    )
                )
    return tuple(issues)


def validate_declared_interfaces_against_snapshot(
    workspace: str,
    cache_root: str,
    target_files: list[str] | None = None,
) -> list[str]:
    """Validate CE-declared interfaces and return legacy error strings."""

    return [
        issue.to_error_message()
        for issue in validate_declared_interface_issues_against_snapshot(
            workspace,
            cache_root,
            target_files,
        )
    ]


def render_assume_contract(declared: dict[str, dict[str, Any]]) -> str:
    """Render the frozen cross-file interface contract for the fission prompt.

    Returns "" when nothing is declared, so the caller appends nothing.
    """
    if not declared:
        return ""
    lines = [
        "\n## 跨文件接口契约(前序任务已定名,必须复用)",
        "以下文件已由前序任务声明这些公开标识符。你的步骤必须复用完全相同的名字;"
        "新增元素可用新名字,但严禁重命名或删除既有标识符——否则会破坏其它文件对它们的引用,"
        "导致产物虽通过单文件检查却整体无法运行。",
    ]
    for target in sorted(declared):
        entry = declared[target]
        identifiers = entry.get("identifiers") or []
        public_symbols = entry.get("public_symbols") or []
        signatures = entry.get("signatures") or []
        if identifiers:
            lines.append(f"- {target} 已公开标识符: {', '.join(identifiers)}")
        if public_symbols:
            lines.append(f"- {target} 已公开代码符号: {', '.join(public_symbols)}")
        if signatures:
            lines.append(f"  {target} 既有签名: {'; '.join(signatures)}")
    return "\n".join(lines)


__all__ = [
    "DeclaredInterfaceLedgerSnapshot",
    "WorkspaceReadSnapshot",
    "WorkspaceTextReadSnapshot",
    "read_all_declared_interfaces",
    "read_declared_interface_ledger_snapshot",
    "read_declared_interface_ledger_status",
    "read_declared_interfaces",
    "record_declared_interfaces",
    "render_assume_contract",
    "validate_declared_interface_issues_against_snapshot",
    "validate_declared_interfaces_against_snapshot",
]
