"""Content-addressed research material. The producer's engineering journal owns its provenance."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from graphlib import CycleError, TopologicalSorter
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope
from polaris.kernelone.fs import (
    GuardedRegularFileSnapshotError,
    guarded_compare_and_create_regular_file,
    guarded_create_directory,
    read_guarded_regular_file_snapshot,
)
from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

FILE_LIMIT = 96 * 1024 * 1024
PUBLICATION_LIMIT = 256 * 1024 * 1024
DESCRIPTOR_LIMIT = 1024 * 1024
_SHA = re.compile(r"[0-9a-f]{64}")
_BLOBS = "research-publications/blobs"
_SCHEMA = "engineering.research-publication.v1"


def _wire(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def publication_hash(descriptor: Mapping[str, Any]) -> str:
    return hashlib.sha256(_wire(descriptor)).hexdigest()


def valid_digest(value: object) -> bool:
    return isinstance(value, str) and _SHA.fullmatch(value) is not None


def eligible_path(workspace: Path, name: str, scope: PermissionScope, *, write: bool) -> bool:
    """Apply normalized host tool grants, the real path and its authorized root."""
    tool = "write_file" if write else "read_file"
    if tool not in {ToolSpecRegistry.get_canonical(key) for key in scope.enabled_tools}:
        return False
    target = (workspace / name).resolve()
    roots = scope.write_roots if write else scope.read_roots
    return (
        target.is_relative_to(workspace)
        and not target.is_relative_to(workspace / ".polaris")
        and any(target.is_relative_to(root.resolve()) for root in roots)
    )


def source_paths(paths: Sequence[str]) -> list[str]:
    if isinstance(paths, str) or not 1 <= len(paths) <= 256:
        raise ValueError("select between 1 and 256 publication files")
    for name in paths:
        if (
            not isinstance(name, str)
            or not name
            or "\x00" in name
            or "\\" in name
            or len(name) > 4096
            or PureWindowsPath(name).drive
            or PurePosixPath(name).as_posix() != name
            or name.startswith("/")
            or any(p in {"", ".", "..", ".polaris", ".git"} for p in name.split("/"))
        ):
            raise ValueError("publication requires a canonical relative source path")
    if len(set(paths)) != len(paths):
        raise ValueError("duplicate publication source path")
    return sorted(paths)


def _stored(root: Path, path: str, limit: int) -> bytes | None:
    try:
        content = read_guarded_regular_file_snapshot(root, path, max(1, limit)).content
        if not isinstance(content, bytes):
            raise ValueError("invalid research publication snapshot")
        return content
    except GuardedRegularFileSnapshotError as error:
        if error.code != "guarded_snapshot_missing":
            raise
        return None


def _create_once(root: Path, path: str, data: bytes) -> None:
    existing = _stored(root, path, len(data))
    if existing is None:
        guarded_compare_and_create_regular_file(root, path, data, max_bytes=max(1, len(data)))
    elif existing != data:
        raise ValueError(f"publication integrity conflict: {path}")


def seal_verified_files(
    workspace: Path,
    control: Path,
    paths: Sequence[str],
    verified: Mapping[str, Any],
    read_allowed: Callable[[str], bool],
) -> list[dict[str, Any]]:
    """Save only bytes matching the selected, observed verifier's result manifest."""
    result = []
    total = 0
    for name in source_paths(paths):
        if not read_allowed(name):
            raise PermissionError("publication source is outside current read grants")
        entry = verified.get(str(workspace / name))
        if not isinstance(entry, Mapping) or not valid_digest(entry.get("sha256")):
            raise ValueError(f"no verified regular-file digest for {name}")
        data = read_guarded_regular_file_snapshot(workspace, name, FILE_LIMIT).content
        digest = hashlib.sha256(data).hexdigest()
        if digest != entry["sha256"]:
            raise ValueError(f"verified source changed: {name}")
        total += len(data)
        if total > PUBLICATION_LIMIT:
            raise ValueError("selected research publication exceeds the transfer budget")
        guarded_create_directory(str(control), _BLOBS, parents=True, exist_ok=True)
        _create_once(control, f"{_BLOBS}/{digest}", data)
        result.append({"path": name, "sha256": digest, "bytes": len(data)})
    return result


def read_publication_blob(control: Path, item: Mapping[str, Any]) -> bytes:
    digest = item.get("sha256")
    size = item.get("bytes")
    if not valid_digest(digest) or type(size) is not int or not 0 <= size <= FILE_LIMIT:
        raise ValueError("invalid publication artifact binding")
    data = _stored(control, f"{_BLOBS}/{digest}", size)
    if data is None or len(data) != size or hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("producer publication artifact integrity failure")
    return data


def validate_descriptor(descriptor: dict[str, Any], digest: str) -> None:
    if not isinstance(descriptor, dict) or not valid_digest(digest) or descriptor.get("schema") != _SCHEMA:
        raise ValueError("publication descriptor integrity failure")
    raw = _wire(descriptor)
    if len(raw) > DESCRIPTOR_LIMIT or hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("publication descriptor integrity failure")
    for key in ("project", "lab", "experiment", "scope", "run_id", "producer_workspace"):
        if not isinstance(descriptor.get(key), str) or not descriptor[key]:
            raise ValueError(f"missing publication identity: {key}")
    if (
        not valid_digest(descriptor.get("plan_sha256"))
        or type(descriptor.get("verification_seq")) is not int
        or descriptor["verification_seq"] < 1
    ):
        raise ValueError("invalid publication verifier identity")
    if type(descriptor.get("workspace_revision")) is not int or descriptor["workspace_revision"] < 0:
        raise ValueError("invalid publication workspace revision")
    deps = descriptor.get("depends_on", [])
    if not isinstance(deps, list) or any(
        not isinstance(dep, dict)
        or not all(isinstance(dep.get(key), str) and dep[key] for key in ("lab", "id"))
        or (dep.get("sha256") is not None and not valid_digest(dep["sha256"]))
        for dep in deps
    ):
        raise ValueError("invalid published dependency binding")
    files = descriptor.get("files")
    if not isinstance(files, list) or not all(isinstance(row, dict) for row in files):
        raise ValueError("invalid publication files")
    paths = [row.get("path") for row in files]
    if source_paths(paths) != paths:
        raise ValueError("publication files are out of order")
    total = 0
    sizes: dict[str, int] = {}
    for item in files:
        size = item.get("bytes")
        if not valid_digest(item.get("sha256")) or type(size) is not int or not 0 <= size <= FILE_LIMIT:
            raise ValueError("invalid publication file size or digest")
        if sizes.setdefault(item["sha256"], size) != size:
            raise ValueError("one publication digest must identify one file size")
        total += size
    if total > PUBLICATION_LIMIT:
        raise ValueError("publication byte budget exceeded")


def _local_material(digest: str, descriptor: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any]]:
    folder = f"research-inputs/{digest}"
    manifest_path = f"{folder}/manifest.json"
    files = descriptor["files"]
    rows = [
        {
            "source_path": f["path"],
            "path": f"{folder}/objects/{f['sha256']}",
            "sha256": f["sha256"],
            "bytes": f["bytes"],
        }
        for f in files
    ]
    local = {
        "schema": "engineering.imported-research-evidence.v1",
        "publication_sha256": digest,
        "producer": {
            k: descriptor[k]
            for k in ("producer_workspace", "run_id", "project", "lab", "experiment", "plan_sha256", "verification_seq")
        },
        "scope": descriptor["scope"],
        "producer_dependencies": descriptor.get("depends_on", []),
        "artifacts": rows,
    }
    raw = _wire(local)
    report = {
        "manifest": manifest_path,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "references": [
            {"path_pointer": f"/artifacts/{i}/path", "digest_pointer": f"/artifacts/{i}/sha256"}
            for i in range(len(rows))
        ],
    }
    material = {
        "publication_sha256": digest,
        "producer": local["producer"],
        "report": report,
        "experiment": {
            "lab": descriptor["lab"],
            "id": descriptor["experiment"],
            "scope": descriptor["scope"],
            "report": report,
        },
    }
    return folder, local, material


def install_publication(
    workspace: Path,
    digest: str,
    descriptor: dict[str, Any],
    fetch: Callable[[int], bytes],
    write_allowed: Callable[[str], bool],
) -> dict[str, Any]:
    """Install through a locked consumer owner. A verified local manifest is the commit point."""
    validate_descriptor(descriptor, digest)
    folder, local, material = _local_material(digest, descriptor)
    manifest_path = material["report"]["manifest"]
    rows = local["artifacts"]
    raw = _wire(local)
    if not all(write_allowed(name) for name in (manifest_path, f"{folder}/intent.json", *(r["path"] for r in rows))):
        raise PermissionError("research import requires host write grants on its version directory")
    existing = _stored(workspace, manifest_path, DESCRIPTOR_LIMIT)
    if existing is not None and existing != raw:
        raise ValueError("committed research manifest integrity failure")
    intent_path = f"{folder}/intent.json"
    intent = _wire(descriptor)
    if existing is None:
        guarded_create_directory(str(workspace), f"{folder}/objects", parents=True, exist_ok=True)
        _create_once(workspace, intent_path, intent)
    elif _stored(workspace, intent_path, DESCRIPTOR_LIMIT) != intent:
        raise ValueError("committed research intent integrity failure")
    seen: set[str] = set()
    for index, row in enumerate(rows):
        if row["sha256"] in seen:
            continue
        seen.add(row["sha256"])
        content = _stored(workspace, row["path"], FILE_LIMIT)
        if content is None and existing is None:
            content = fetch(index)
            if len(content) == row["bytes"] and hashlib.sha256(content).hexdigest() == row["sha256"]:
                _create_once(workspace, row["path"], content)
        if content is None or len(content) != row["bytes"] or hashlib.sha256(content).hexdigest() != row["sha256"]:
            raise ValueError(f"research import artifact integrity failure: {row['source_path']}")
    _create_once(workspace, manifest_path, raw)
    return material


def adopt_research_publications(
    workspace: Path,
    plan: dict[str, Any],
    digests: Sequence[str],
    consumers: Sequence[str],
    *,
    read_allowed: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    """Explicitly bind local imports and selected edges in a new plan; the prior plan stays immutable."""
    from polaris.cells.roles.runtime.internal.engineering_research_labs import _catalog, freeze_research_plan
    from polaris.cells.roles.runtime.internal.engineering_research_references import (
        decode_research_json,
        verify_references,
    )

    revised, _ = freeze_research_plan(plan)
    _, index, edges = _catalog(revised)
    try:
        tuple(TopologicalSorter(edges).static_order())
    except CycleError as exc:
        raise ValueError("research dependency cycle in the adoption plan") from exc
    if (
        isinstance(digests, str)
        or not 1 <= len(digests) <= len(index)
        or not all(valid_digest(sha) for sha in digests)
        or len(set(digests)) != len(digests)
    ):
        raise ValueError("select distinct publication SHA-256 identities")
    if (
        isinstance(consumers, str)
        or len(consumers) > len(index)
        or any(not isinstance(key, str) or key not in index for key in consumers)
        or len(set(consumers)) != len(consumers)
    ):
        raise ValueError("invalid experiment identity in adoption")
    adopted: dict[str, str] = {}
    for digest in digests:
        path = f"research-inputs/{digest}/intent.json"
        if read_allowed is not None and not read_allowed(path):
            raise PermissionError("research import is outside current read grants")
        intent = _stored(workspace, path, DESCRIPTOR_LIMIT)
        if intent is None:
            raise ValueError("publication has not been installed in this workspace")
        desc = decode_research_json(intent)
        if not isinstance(desc, dict):
            raise ValueError("invalid local publication intent")
        validate_descriptor(desc, digest)
        if desc["project"] != revised.get("project"):
            raise ValueError("publication belongs to a different research project")
        key = f"{desc['lab']}/{desc['experiment']}"
        if key not in index or key in adopted:
            raise ValueError("adoption requires exactly one selected version per producer experiment")
        _, _, material = _local_material(digest, desc)
        report = material["report"]
        proof = verify_references(workspace, [report], max_bytes=FILE_LIMIT, read_allowed=read_allowed)
        if proof["status"] != "matched":
            raise ValueError(f"local research publication is not intact: {key}")
        index[key]["scope"] = desc["scope"]
        index[key]["report"] = report
        index[key].pop("recheck", None)
        adopted[key] = report["sha256"]
    for key in consumers:
        updated = 0
        changed = []
        for edge in index[key].get("depends_on", []):
            producer = f"{edge['lab']}/{edge['id']}"
            if producer in adopted:
                if edge.get("sha256") != adopted[producer]:
                    changed.append(producer)
                edge["sha256"] = adopted[producer]
                updated += 1
        previous = index[key].get("report")
        if changed and isinstance(previous, dict):
            index[key]["recheck"] = {"previous_report_sha256": previous["sha256"], "changed_dependencies": changed}
            index[key].pop("report")
        if not updated:
            raise ValueError(f"no declared dependency on the selected publications: {key}")
    freeze_research_plan(revised)
    return revised
